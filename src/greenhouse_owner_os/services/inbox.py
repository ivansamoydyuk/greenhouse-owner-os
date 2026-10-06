"""Untrusted originals -> deterministic extraction -> explicit non-posting draft.

No paid provider is required. Binary documents remain UNAVAILABLE for OCR;
owner-supplied transcriptions are labelled as such, never presented as OCR.
"""
import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Literal, Protocol
from uuid import UUID, uuid4
from pydantic import Field, model_validator
from sqlalchemy import select, text
from greenhouse_owner_os.domain.commands import StrictModel
from greenhouse_owner_os.domain.errors import ConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.infrastructure.inbox_models import InboxRow
from greenhouse_owner_os.infrastructure.models import EntityRow, CounterpartyRow, WorkAreaRow, MoneyAccountRow, CaptureRow, LedgerAccountRow
from .capture import CaptureService, CaptureInput, CaptureConfirm, BorrowedUow, extract
from .finance import FinanceService, _hash_request
from .owner_view import json_value

class InboxInput(StrictModel):
    source_type: Literal['text','pdf','image','email','document']='text'
    source: str=Field(default='owner_mobile:manual',min_length=1,max_length=200)
    locator: str|None=Field(default=None,min_length=1,max_length=2000)
    raw_text: str|None=Field(default=None,min_length=1,max_length=10000)
    filename: str|None=Field(default=None,min_length=1,max_length=200)
    mime_type: Literal['application/pdf','image/png','image/jpeg','application/octet-stream']|None=None
    file_base64: str|None=Field(default=None,max_length=7000000)
    metadata: dict=Field(default_factory=dict)
    received_at: datetime|None=None
    @model_validator(mode='after')
    def original(self):
        if not self.raw_text and not self.file_base64:raise ValueError('Original text or file required')
        if self.file_base64 and not (self.filename and self.mime_type):raise ValueError('Filename and MIME required')
        if self.filename and (self.filename in ('.','..') or re.search(r'[/\\\x00-\x1f\x7f]',self.filename)):raise ValueError('Unsafe filename')
        if len(json.dumps(self.metadata,ensure_ascii=False))>10000:raise ValueError('Metadata limit is 10000 characters')
        if self.received_at and self.received_at.tzinfo is None:raise ValueError('Received timestamp requires timezone')
        return self

class InboxConfirm(StrictModel):
    expected_version: int=Field(gt=0)
    confirmed: Literal[True]
    reviewed: dict
    duplicate_reviewed: bool=False
    reason: str=Field(min_length=1,max_length=2000)

class InboxReject(StrictModel):
    expected_version: int=Field(gt=0)
    reason: str=Field(min_length=1,max_length=2000)

class Extractor(Protocol):
    version: str
    def extract(self, original: InboxInput, references: dict) -> dict: ...

class DeterministicExtractor:
    version='deterministic-text-r1'
    def extract(self, original, references):
        raw=original.raw_text
        if not raw:
            return dict(text=None,route='UNRESOLVED',fields={},gaps=['Текст не извлечён. Нужна проверенная расшифровка; OCR не подключён.'],confidence='UNAVAILABLE',method='preserved_binary')
        today=(original.received_at or datetime.now(timezone.utc)).astimezone(ZoneInfo('Europe/Istanbul')).date()
        route,fields,gaps=extract(raw,today=today,**references)
        if route=='UNRESOLVED' and re.search(r'(?i)\b(инструкция|полив|вентиляция|ремонт|договор)\b',raw):route='DOCUMENTS'
        confidence='HIGH' if route in ('FINANCE','HARVEST_DEPOT','INVENTORY') and fields.get('kind') else 'LOW'
        return dict(text=raw,route=route,fields=fields,gaps=gaps,confidence=confidence,method='owner_transcription' if original.file_base64 else 'original_text')

def serialize(row):
    result={c.name:json_value(getattr(row,'metadata_json' if c.name=='metadata' else c.name)) for c in InboxRow.__table__.columns if c.name!='file_bytes'}
    result['has_file']=row.file_bytes is not None
    result['summary']=(row.raw_text or row.filename or 'Документ')[:160]
    return result

def reject_floats(value):
    if isinstance(value,float):raise ValidationError('Use exact decimal strings; floating point is not allowed')
    if isinstance(value,dict):
        for x in value.values():reject_floats(x)
    if isinstance(value,list):
        for x in value:reject_floats(x)

class InboxService:
    def __init__(self,factory,extractor=None):
        self.factory=factory;self.finance=FinanceService(factory);self.extractor=extractor or DeterministicExtractor()

    def _write(self,e,a,key,operation,payload,fn):
        h=_hash_request(dict(entity_id=str(e),**payload))
        with self.factory() as u:
            u.idempotency.lock(a,operation,key)
            replay=self.finance._replay_if_exists(u,a,operation,key,h)
            if replay:return replay
            if not u.session.get(EntityRow,e):raise NotFoundError('entity not found')
            r=fn(u)
            u.audit.append(actor_id=a,action=operation,entity_type='inbox',entity_id=UUID(r['id']),before=None,after=r)
            u.idempotency.put(actor_id=a,operation=operation,key=key,request_hash=h,resource_type='inbox',resource_id=UUID(r['id']),response=r)
            u.commit();return r

    def ingest(self,e,a,key,body):
        binary=None
        if body.file_base64:
            try:binary=base64.b64decode(body.file_base64,validate=True)
            except ValueError:raise ValidationError('Invalid file encoding')
            if not binary or len(binary)>5242880:raise ValidationError('File limit is 5 MB')
            signatures={'application/pdf':b'%PDF-','image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8\xff'}
            if body.mime_type in signatures and not binary.startswith(signatures[body.mime_type]):raise ValidationError('File signature does not match MIME')
            if body.source_type=='pdf' and body.mime_type!='application/pdf' or body.source_type=='image' and body.mime_type not in ('image/png','image/jpeg'):raise ValidationError('Source type does not match MIME')
        fingerprint=hashlib.sha256(binary if binary is not None else body.raw_text.encode()).hexdigest()
        content_hash=_hash_request(dict(binary=fingerprint if binary else None,text=body.raw_text))
        identity_hash=_hash_request(dict(content=content_hash,filename=body.filename,mime=body.mime_type,metadata=body.metadata,source_type=body.source_type))
        def run(u):
            # Entity lock serializes source and content relationship discovery,
            # without conflating distinct locators or dropping manual duplicates.
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':'inbox:'+str(e)})
            conflict=None
            if body.locator:
                rows=list(u.session.scalars(select(InboxRow).where(InboxRow.entity_id==e,InboxRow.source==body.source,InboxRow.locator==body.locator)))
                for row in rows:
                    if row.identity_hash==identity_hash:
                        if body.received_at and row.received_at!=body.received_at:raise ConflictError('Source timestamp changed; original retained')
                        return serialize(row)
                    if row.content_hash==content_hash:raise ConflictError('Source metadata changed; original retained')
                conflict=next((r for r in rows if not r.conflict_of_id),None)
            duplicate=u.session.scalar(select(InboxRow).where(InboxRow.entity_id==e,InboxRow.fingerprint==fingerprint).order_by(InboxRow.created_at,InboxRow.id).limit(1))
            q=lambda model:list(u.session.scalars(select(model).where(model.entity_id==e)))
            try:
                from .inventory import rows as inventory_rows
                output=self.extractor.extract(body,dict(counterparties=q(CounterpartyRow),areas=q(WorkAreaRow),accounts=q(MoneyAccountRow),products=inventory_rows(u,"SELECT * FROM inventory.products WHERE entity_id=:e",e=e),locations=inventory_rows(u,"SELECT * FROM inventory.locations WHERE entity_id=:e",e=e)))
                reject_floats(output)
                if output['route'] not in ('FINANCE','HARVEST_DEPOT','DOCUMENTS','UNRESOLVED','INVENTORY') or not isinstance(output['fields'],dict):raise ValueError('Invalid extractor result')
            except Exception:
                # Provider errors must not lose the original or expose private
                # provider exception/credentials. Review remains non-posting.
                output=dict(text=body.raw_text,route='UNRESOLVED',fields={},gaps=['Не удалось разобрать документ. Оригинал сохранён для проверки.'],confidence='UNAVAILABLE',method='extraction_failed')
            gaps=list(output['gaps'])
            if conflict:gaps.append('Источник изменился. Первый оригинал сохранён; нужен отдельный исправленный источник.')
            if self._domain_duplicate(u,e,body.source if body.locator else 'inbox:manual',body.locator,body.raw_text,binary):gaps.append('Похожее событие уже есть в доменных черновиках. Проверьте, что это отдельное событие, а не дубль.')
            if duplicate:gaps.append('Такой контент уже получен. Проверьте, один ли это документ или другое событие.')
            row=InboxRow(id=uuid4(),entity_id=e,source_type=body.source_type,source=body.source,locator=body.locator,
                filename=body.filename,mime_type=body.mime_type,file_bytes=binary,raw_text=body.raw_text,metadata_json=body.metadata,
                received_at=body.received_at or datetime.now(timezone.utc),fingerprint=fingerprint,content_hash=content_hash,identity_hash=identity_hash,
                conflict_of_id=conflict.id if conflict else None,duplicate_of_id=duplicate.id if duplicate else None,
                extractor_version=self.extractor.version,extracted_text=output['text'],extraction=output,normalized=output['fields'],
                validation=dict(missing_fields=gaps,uncertain_fields=[] if output['confidence']=='HIGH' else ['classification'],review_required=True),
                route=output['route'],confidence=output['confidence'],state='CONFLICT' if conflict else 'NEEDS_REVIEW' if gaps else 'READY',version=1,created_by=a)
            u.session.add(row);u.session.flush();return serialize(row)
        return self._write(e,a,key,'inbox_ingest',body.model_dump(mode='json'),run)

    def _domain_duplicate(self,u,e,source,reference,raw,binary):
        from greenhouse_owner_os.infrastructure.harvest_models import EvidenceRow
        if raw:
            fp=hashlib.sha256(raw.strip().encode()).hexdigest()
            captures=u.session.scalars(select(CaptureRow).where(CaptureRow.entity_id==e,CaptureRow.fingerprint==fp))
            if any((c.source_namespace,c.source_reference)!=(source,reference) for c in captures):return True
        fp=hashlib.sha256(binary if binary is not None else (raw or '').encode()).hexdigest()
        evidence=u.session.scalars(select(EvidenceRow).where(EvidenceRow.entity_id==e,EvidenceRow.fingerprint==fp,EvidenceRow.file_bytes.is_not(None) if binary is not None else EvidenceRow.file_bytes.is_(None)))
        return any((r.source,r.source_reference)!=(source,reference) for r in evidence)

    def _row(self,u,e,i,lock=False):
        query=select(InboxRow).where(InboxRow.entity_id==e,InboxRow.id==i)
        row=u.session.scalar(query.with_for_update() if lock else query)
        if not row:raise NotFoundError('Inbox item not found')
        return row

    def get(self,e,i):
        with self.factory() as u:
            row=self._row(u,e,i);result=serialize(row)
            result['duplicate_warning']=self._domain_duplicate(u,e,row.source if row.locator else 'inbox:manual',row.locator or str(row.id),row.raw_text,row.file_bytes)
            return result

    def list(self,e,offset=0,limit=50):
        with self.factory() as u:
            rows=list(u.session.scalars(select(InboxRow).where(InboxRow.entity_id==e).order_by(InboxRow.created_at.desc(),InboxRow.id).offset(offset).limit(limit+1)))
            return dict(items=[serialize(r) for r in rows[:limit]],next_offset=offset+limit if len(rows)>limit else None)

    def original(self,e,i):
        with self.factory() as u:
            row=self._row(u,e,i)
            if row.file_bytes is None:raise NotFoundError('No binary original')
            return row.file_bytes,row.mime_type

    def confirm(self,e,i,a,key,body):
        reject_floats(body.reviewed)
        def run(u):
            row=self._row(u,e,i,True)
            if row.state=='CONFIRMED':
                if row.reviewed!=body.model_dump(mode='json'):raise ConflictError('Confirmed interpretation cannot change; use an explicit correction in the destination workflow')
                return serialize(row)
            if row.version!=body.expected_version or row.state in ('REJECTED','CONFLICT'):raise ConflictError('Item is finalized or source conflicts; original retained')
            if row.duplicate_of_id and not body.duplicate_reviewed:raise ValidationError('Explicit duplicate review required')
            if row.route not in ('FINANCE','HARVEST_DEPOT','INVENTORY'):raise ValidationError('Unknown document requires verified domain classification; no draft created')
            fields=dict(body.reviewed)
            if row.route=='INVENTORY':
                from .inventory import InventoryService
                from greenhouse_owner_os.domain.inventory import InventoryInput
                evidence=dict(source=row.source if row.locator else 'inbox:manual',locator=row.locator or str(i),raw_text=row.raw_text)
                if row.file_bytes:
                    evidence.update(file_base64=base64.b64encode(row.file_bytes).decode(),filename=row.filename,mime_type=row.mime_type)
                fields['evidence']=evidence
                r=InventoryService(lambda:BorrowedUow(u)).create(e,a,'inbox:inventory:'+str(i),InventoryInput(**fields))
                if r['status']!='draft':raise ConflictError('Исходный складской документ уже проведён; используйте коррекцию')
                row.inventory_id=UUID(r['id'])
            elif row.route=='FINANCE':
                # Existing Capture creates only a draft. The owner must select
                # economic basis for obligations, never an inferred expense.
                if fields.get('kind')=='obligation':
                    offset=fields.get('offset_ledger_account_id')
                    try:ledger=u.session.get(LedgerAccountRow,UUID(offset))
                    except (TypeError,ValueError):ledger=None
                    if not ledger or ledger.entity_id!=e or ledger.currency!=fields.get('currency'):raise ValidationError('Choose the explicit economic basis and matching currency')
                child=CaptureService(lambda:BorrowedUow(u))
                capture=child.create(e,a,'inbox:capture:'+str(i),CaptureInput(text=row.extracted_text or row.raw_text,source_namespace=row.source if row.locator else 'inbox:manual',source_reference=row.locator or str(i)))
                if capture['state']!='LINKED' and any('Похожее' in gap for gap in capture['gaps']) and not body.duplicate_reviewed:raise ValidationError('Похожее событие уже есть. Откройте входящее снова и явно проверьте дубль.')
                fields['duplicate_reviewed']=body.duplicate_reviewed
                result=child.confirm(e,UUID(capture['id']),a,'inbox:confirm:'+str(i),CaptureConfirm(expected_version=1,reviewed=fields,confirmed=True))
                row.capture_id=UUID(result['capture']['id'])
            else:
                from greenhouse_owner_os.domain.harvest import SettlementInput
                from greenhouse_owner_os.infrastructure.harvest_models import EvidenceRow
                from .harvest import HarvestService
                if fields.pop('kind',None)!='settlement':raise ValidationError('Inbox depot handoff requires a reviewed receipt')
                fields.pop('greenhouse_id',None);fields.pop('direction',None)
                content_key='harvest-content:'+str(e)+('binary:' if row.file_bytes is not None else 'text:')+row.fingerprint
                u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':content_key})
                if not body.duplicate_reviewed and self._domain_duplicate(u,e,row.source if row.locator else 'inbox:manual',row.locator or str(i),row.raw_text,row.file_bytes):raise ValidationError('Похожее событие уже есть. Откройте входящее снова и явно проверьте дубль.')
                # Preserve upstream identity across both adapters. A byte hash
                # is only duplicate suspicion, never an alternate source locator.
                evidence=dict(source=row.source if row.locator else 'inbox:manual',source_reference=row.locator or str(i),raw_text=row.raw_text)
                if row.file_bytes:
                    if row.mime_type not in ('application/pdf','image/png','image/jpeg'):raise ValidationError('Unsupported Harvest document type')
                    evidence.update(filename=row.filename,mime_type=row.mime_type,file_base64=base64.b64encode(row.file_bytes).decode())
                fields['evidence']=evidence
                r=HarvestService(lambda:BorrowedUow(u)).settlement(e,a,'inbox:harvest:'+str(i),SettlementInput(**fields))
                if r['status']!='draft':raise ConflictError('Existing Harvest receipt is finalized; use its explicit correction workflow')
                row.harvest_id=UUID(r['id'])
            row.state='CONFIRMED';row.version+=1;row.reviewed=body.model_dump(mode='json');row.reviewed_by=a;row.reviewed_at=datetime.now(timezone.utc)
            u.session.flush();return serialize(row)
        return self._write(e,a,key,'inbox_confirm',dict(id=str(i),**body.model_dump(mode='json')),run)

    def reject(self,e,i,a,key,body):
        def run(u):
            row=self._row(u,e,i,True)
            if row.state=='REJECTED' and row.reviewed==body.model_dump(mode='json'):return serialize(row)
            if row.version!=body.expected_version or row.state in ('CONFIRMED','REJECTED'):raise ConflictError('Finalized item cannot change')
            row.state='REJECTED';row.version+=1;row.reviewed=body.model_dump(mode='json');row.reviewed_by=a;row.reviewed_at=datetime.now(timezone.utc)
            u.session.flush();return serialize(row)
        return self._write(e,a,key,'inbox_reject',dict(id=str(i),**body.model_dump(mode='json')),run)
