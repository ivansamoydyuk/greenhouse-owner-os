"""Durable capture, leased preparation and individually authorized effects."""
import base64
import hashlib
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import select, text

from greenhouse_owner_os.domain.commands import CreateObligationCommand, CreatePaymentCommand
from greenhouse_owner_os.domain.errors import ConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.domain.owner_confirmation import SCHEMAS
from greenhouse_owner_os.infrastructure.inbox_models import InboxRow
from greenhouse_owner_os.infrastructure.models import ActorRow, EntityRow, CounterpartyRow, WorkAreaRow, MoneyAccountRow
from .capture import BorrowedUow
from .finance import FinanceService, _hash_request
from .inbox import serialize, reject_floats
from .inventory import InventoryService, rows, val
from .ocr import LocalOCR, RecognitionError
from .owner_preparation import prepare


TERMINAL = ('POSTED','CONFIRMED_NONPOSTING','REJECTED')


class OwnerConfirmationService:
    def __init__(self, factory, provider=None):
        self.factory = factory
        self.provider = provider or LocalOCR()
        self.finance = FinanceService(factory)

    def _owner(self, u, entity, actor):
        a = u.session.get(ActorRow, actor)
        if not a or not a.active or a.actor_type != 'owner':
            raise ValidationError('Только владелец может подтвердить действие')
        if not u.session.get(EntityRow, entity):
            raise NotFoundError('entity not found')

    def _write(self, e, a, key, operation, payload, fn):
        h = _hash_request(dict(entity_id=str(e), **payload))
        with self.factory() as u:
            self._owner(u,e,a)
            u.idempotency.lock(a,operation,key)
            prior = self.finance._replay_if_exists(u,a,operation,key,h)
            if prior:
                return prior
            result = fn(u)
            resource = UUID(result['id'])
            u.audit.append(actor_id=a,action=operation,entity_type='owner_r2',entity_id=resource,before=None,after=result)
            u.idempotency.put(actor_id=a,operation=operation,key=key,request_hash=h,resource_type='owner_r2',resource_id=resource,response=result)
            u.commit()
            return result

    def capture(self, e, a, key, body):
        binary = None
        if body.file_base64:
            try:binary = base64.b64decode(body.file_base64, validate=True)
            except ValueError:raise ValidationError('Некорректный файл') from None
            signatures={'application/pdf':b'%PDF-','image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8\xff'}
            if not binary or len(binary)>20*1024*1024 or not binary.startswith(signatures[body.mime_type]):
                raise ValidationError('Файл не поддерживается или превышает 20 MiB')
        fingerprint=hashlib.sha256(binary if binary is not None else body.text.encode()).hexdigest()
        content_hash=_hash_request(dict(binary=fingerprint if binary else None,text=body.text))
        metadata=dict(contract='OWNER_R2', client_capture_id=str(body.client_capture_id), captured_at=body.captured_at.isoformat(),
                      timezone=body.timezone, transcript_only=body.input_type=='voice_transcript')
        identity=_hash_request(dict(content=content_hash,filename=body.filename,mime_type=body.mime_type,metadata=metadata))
        def run(u):
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':'inbox:'+str(e)})
            old=u.session.scalar(select(InboxRow).where(InboxRow.entity_id==e,InboxRow.source=='owner:r2',InboxRow.locator==str(body.client_capture_id)))
            if old:
                if old.identity_hash!=identity:raise ConflictError('Содержимое сохранённого источника изменилось')
                return self._source(u,e,old.id)
            duplicate=u.session.scalar(select(InboxRow).where(InboxRow.entity_id==e,InboxRow.fingerprint==fingerprint).order_by(InboxRow.created_at).limit(1))
            row=InboxRow(id=uuid4(),entity_id=e,source_type='text' if binary is None else 'pdf' if body.mime_type=='application/pdf' else 'image',
                source='owner:r2',locator=str(body.client_capture_id),raw_text=body.text,filename=body.filename,mime_type=body.mime_type,file_bytes=binary,
                metadata_json=metadata,received_at=datetime.now(timezone.utc),fingerprint=fingerprint,content_hash=content_hash,identity_hash=identity,
                duplicate_of_id=duplicate.id if duplicate else None,extractor_version='pending-r2',extracted_text=None,
                extraction={},normalized={},validation={},route='UNRESOLVED',confidence='UNAVAILABLE',state='NEEDS_REVIEW',version=1,created_by=a)
            u.session.add(row);u.session.flush()
            u.session.execute(text('INSERT INTO core.owner_source_states(inbox_id,entity_id,client_capture_id,captured_at,timezone,transcript_only) VALUES(:i,:e,:cid,:at,:tz,:voice)'),
                dict(i=row.id,e=e,cid=body.client_capture_id,at=body.captured_at,tz=body.timezone,voice=body.input_type=='voice_transcript'))
            u.session.execute(text("INSERT INTO core.processing_jobs(id,inbox_id,entity_id,stage,engine_version) VALUES(:id,:i,:e,'PREPARE',:v)"),
                dict(id=uuid4(),i=row.id,e=e,v=self.provider.version))
            return self._source(u,e,row.id)
        return self._write(e,a,key,'owner_capture',body.model_dump(mode='json'),run)

    def _source(self,u,e,i):
        original=u.session.scalar(select(InboxRow).where(InboxRow.id==i,InboxRow.entity_id==e))
        if not original:raise NotFoundError('source not found')
        states=rows(u,'SELECT * FROM core.owner_source_states WHERE inbox_id=:i AND entity_id=:e',i=i,e=e)
        if not states:raise NotFoundError('R2 source not found')
        return dict(id=str(i),source_state=states[0]['state'],source=serialize(original),
                    client_capture_id=states[0]['client_capture_id'],captured_at=states[0]['captured_at'],
                    proposals=rows(u,'SELECT id,state,action_type FROM core.proposed_actions WHERE inbox_id=:i ORDER BY priority,first_unresolved_at,id',i=i))

    def source(self,e,a,i):
        with self.factory() as u:
            self._owner(u,e,a)
            return self._source(u,e,i)

    def process_one(self, source_id=None):
        """No actor/token or domain adapters in preparation. Claim commits first."""
        token=uuid4()
        with self.factory() as u:
            job=rows(u,"""SELECT * FROM core.processing_jobs WHERE attempts<3
                AND (:i IS NULL OR inbox_id=CAST(:i AS uuid))
                AND (state='QUEUED' OR state='FAILED' AND retry_at<=now() OR state='RUNNING' AND lease_until<now())
                ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1""",i=str(source_id) if source_id else None)
            if not job:return False
            job=job[0]
            u.session.execute(text("UPDATE core.processing_jobs SET state='RUNNING',attempts=attempts+1,lease_token=:t,lease_until=now()+interval '10 minutes' WHERE id=:id"),dict(t=token,id=job['id']))
            u.session.execute(text("UPDATE core.owner_source_states SET state='PROCESSING' WHERE inbox_id=:i"),dict(i=job['inbox_id']))
            original=u.session.get(InboxRow,UUID(job['inbox_id']))
            data=dict(text=original.raw_text,binary=original.file_bytes,mime=original.mime_type,fingerprint=original.fingerprint,
                      received=original.received_at,duplicate=bool(original.duplicate_of_id))
            refs=dict(counterparties=list(u.session.scalars(select(CounterpartyRow).where(CounterpartyRow.entity_id==original.entity_id))),
                      areas=list(u.session.scalars(select(WorkAreaRow).where(WorkAreaRow.entity_id==original.entity_id))),
                      accounts=list(u.session.scalars(select(MoneyAccountRow).where(MoneyAccountRow.entity_id==original.entity_id))),
                      products=rows(u,'SELECT * FROM inventory.products WHERE entity_id=:e',e=original.entity_id),
                      locations=rows(u,'SELECT * FROM inventory.locations WHERE entity_id=:e',e=original.entity_id),
                      workers=rows(u,'SELECT * FROM workers.people WHERE entity_id=:e',e=original.entity_id))
            # ORM data remains usable after the borrowed session closes.
            for group in ('counterparties','areas','accounts'):
                for obj in refs[group]:u.session.expunge(obj)
            u.commit()
        try:
            recognition=self.provider.recognize(data['binary'],data['mime']) if data['binary'] else dict(provider='original_text',version='text-r2-v1',text=data['text'],pages=[],warnings=[])
            candidates=prepare(recognition['text'],data['received'].astimezone(ZoneInfo('Europe/Istanbul')).date(),refs,recognition)
            error=None
        except RecognitionError as exc:
            recognition=dict(provider='local',version=self.provider.version,text='',pages=[],warnings=[str(exc)])
            candidates=[('unresolved:0','UNRESOLVED',dict(text=data['text'] or ''),{},['recognition_failed'])]
            error=str(exc)
        except Exception:
            recognition=dict(provider='local',version=self.provider.version,text='',pages=[],warnings=['PREPARATION_FAILED'])
            candidates=[('unresolved:0','UNRESOLVED',dict(text=data['text'] or ''),{},['preparation_failed'])]
            error='PREPARATION_FAILED'
        with self.factory() as u:
            lease=rows(u,'SELECT * FROM core.processing_jobs WHERE id=:id FOR UPDATE',id=job['id'])[0]
            if lease['lease_token']!=str(token) or lease['state']!='RUNNING':return False
            run_id=uuid4();attempt=lease['attempts']
            u.session.execute(text('INSERT INTO core.extraction_runs(id,entity_id,inbox_id,job_id,attempt,provider,engine_version,input_hash,raw_output,normalized,warnings) VALUES(:id,:e,:i,:j,:n,:p,:v,:h,CAST(:raw AS jsonb),CAST(:normal AS jsonb),CAST(:warnings AS jsonb))'),
                dict(id=run_id,e=job['entity_id'],i=job['inbox_id'],j=job['id'],n=attempt,p=recognition['provider'],v=recognition['version'],h=data['fingerprint'],raw=json.dumps(recognition),normal=json.dumps(candidates),warnings=json.dumps(recognition['warnings'])))
            for slot,kind,payload,provenance,warnings in candidates:
                prior=rows(u,'SELECT * FROM core.proposed_actions WHERE inbox_id=:i AND slot=:slot',i=job['inbox_id'],slot=slot)
                if prior:continue  # Neither approved nor rejected slots are regenerated.
                validated=SCHEMAS[kind].model_validate(payload).model_dump(mode='json')
                pid=uuid4();priority=1 if kind.startswith(('PAY','DIRECT','INVENTORY','INVOICE')) else 2 if kind=='DEPOT_SETTLEMENT' else 3
                u.session.execute(text("INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,priority) VALUES(:id,:e,:i,:slot,:kind,'PREPARING',:p)"),dict(id=pid,e=job['entity_id'],i=job['inbox_id'],slot=slot,kind=kind,p=priority))
                preview=self._validate(u,UUID(job['entity_id']),kind,validated,data['duplicate'])
                all_warnings=warnings+recognition['warnings']
                if all_warnings:preview['missing'].append('Проверьте распознавание оригинала')
                self._revision(u,pid,1,validated,provenance,preview,run_id=run_id)
                state='NEEDS_INPUT' if preview['missing'] else 'READY'
                # Initial state is published with revision one in the same transaction.
                u.session.execute(text('UPDATE core.proposed_actions SET state=:state,version=version+1 WHERE id=:id'),dict(state=state,id=pid))
                self._revision(u,pid,2,validated,provenance,preview,run_id=run_id)
            u.session.execute(text("UPDATE core.processing_jobs SET state=:state,error_code=:error,retry_at=now()+interval '1 minute',lease_until=NULL WHERE id=:id"),dict(state='FAILED' if error else 'SUCCEEDED',error=error,id=job['id']))
            u.session.execute(text("UPDATE core.owner_source_states SET state=:state WHERE inbox_id=:i"),dict(state='FAILED' if error else 'NEEDS_REVIEW',i=job['inbox_id']))
            u.commit()
        return True

    def _validate(self,u,e,kind,payload,duplicate=False):
        missing=[];basis={}
        if duplicate and not payload.get('duplicate_reviewed'):missing.append('Проверьте дубликат источника')
        def reference(table,identifier):
            if not identifier:return None
            rs=rows(u,'SELECT * FROM '+table+' WHERE id=:id AND entity_id=:e',id=identifier,e=e)
            if not rs:raise ValidationError('Связанная запись относится к другой компании')
            result=rs[0];basis[table+':'+str(identifier)]=result
            if result.get('active') is False:missing.append('Связанная запись больше не активна')
            return result
        if kind in ('PAYABLE','PAYMENT','DIRECT_EXPENSE'):
            if not payload.get('amount') or Decimal(payload['amount'])<=0:missing.append('Укажите сумму')
            if not payload.get('event_date'):missing.append('Укажите дату')
            if not payload.get('description'):missing.append('Укажите назначение')
            if kind!='DIRECT_EXPENSE' and not payload.get('counterparty_id'):missing.append('Выберите контрагента')
            reference('core.counterparties',payload.get('counterparty_id'))
            if kind=='PAYMENT':
                account=reference('finance.money_accounts',payload.get('money_account_id'))
                if not account:missing.append('Выберите Наличные / Банк')
                elif account['currency']!=payload['currency']:missing.append('Валюта счёта не совпадает')
                debt=reference('finance.obligations',payload.get('obligation_id'))
                if not debt:missing.append('Выберите долг; аванс не создаётся автоматически')
                else:
                    remaining=u.finance.obligation_remaining(UUID(debt['id']))
                    basis['remaining']=str(remaining)
                    if debt['status']!='posted' or debt['counterparty_id']!=payload.get('counterparty_id') or debt['currency']!=payload['currency'] or debt['direction']!=('payable' if payload.get('direction')=='out' else 'receivable'):
                        missing.append('Выберите подходящий действующий долг')
                    if payload.get('amount') and remaining<Decimal(payload['amount']):missing.append('Сумма превышает остаток долга')
        elif kind in ('INVENTORY_RECEIPT','INVENTORY_APPLICATION'):
            if not payload.get('event_date'):missing.append('Укажите дату')
            if not payload.get('lines'):missing.append('Укажите товар и количество')
            for ln in payload.get('lines',[]):
                p=reference('inventory.products',ln.get('product_id'))
                loc=reference('inventory.locations',ln.get('location_id'))
                if not p:missing.append('Выберите товар')
                if not loc:missing.append('Выберите склад')
                if not ln.get('quantity') or not ln.get('unit'):missing.append('Укажите количество и единицу')
                if p and ln.get('unit')!=p['base_unit']:
                    conversion=rows(u,'SELECT * FROM inventory.conversions WHERE product_id=:p AND unit=:unit',p=p['id'],unit=ln.get('unit'))
                    basis['conversion:'+p['id']]=conversion
                    if not conversion:missing.append('Нет проверенного пересчёта упаковки')
                if p and loc:
                    basis['stock:'+p['id']+':'+loc['id']]=rows(u,'SELECT * FROM inventory.stock WHERE entity_id=:e AND product_id=:p AND location_id=:l',e=e,p=p['id'],l=loc['id'])
                    basis['movements:'+p['id']+':'+loc['id']]=rows(u,'SELECT max(seq) AS seq FROM inventory.movements WHERE entity_id=:e AND product_id=:p AND location_id=:l',e=e,p=p['id'],l=loc['id'])
                reference('core.work_areas',ln.get('greenhouse_id'))
                if kind=='INVENTORY_APPLICATION' and not ln.get('greenhouse_id'):missing.append('Выберите теплицу')
        elif kind=='PERSONAL_TASK':reference('core.work_areas',payload.get('greenhouse_id'))
        elif kind=='WORKER_TASK':
            missing.append('Подготовлено; отправка доступна в существующем ручном режиме')
            for worker in payload.get('worker_ids',[]):reference('workers.people',worker)
        elif kind=='DEPOT_SETTLEMENT':
            reference('core.counterparties',payload.get('depot_id'))
            if not payload.get('depot_id'):missing.append('Выберите депо')
            if not payload.get('event_date'):missing.append('Укажите дату чека')
            if not payload.get('lines'):missing.append('Проверьте все строки чека')
            if len(payload.get('qualities',[]))!=len(payload.get('lines',[])) or 'UNKNOWN' in payload.get('qualities',[]):missing.append('Подтвердите качество каждой строки')
            missing.append('Требуется адаптер текущих качеств депо')
        else:missing.append('Выберите ручной режим для этого источника')
        return dict(missing=list(dict.fromkeys(missing)),basis=basis)

    def _revision(self,u,pid,n,payload,provenance,preview,actor=None,run_id=None):
        digest=_hash_request(dict(proposal_id=str(pid),revision=n,payload=payload,basis=preview['basis']))
        u.session.execute(text('INSERT INTO core.proposal_revisions(proposal_id,revision,schema_version,extraction_run_id,payload,provenance,validation,basis,digest,actor_id) VALUES(:id,:n,1,:run,CAST(:payload AS jsonb),CAST(:provenance AS jsonb),CAST(:validation AS jsonb),CAST(:basis AS jsonb),:digest,:a)'),
            dict(id=pid,n=n,run=run_id,payload=json.dumps(payload),provenance=json.dumps(provenance),validation=json.dumps(preview['missing']),basis=json.dumps(preview['basis']),digest=digest,a=actor))

    def _proposal(self,u,e,i,lock=False):
        r=rows(u,'SELECT * FROM core.proposed_actions WHERE id=:id AND entity_id=:e'+(' FOR UPDATE' if lock else ''),id=i,e=e)
        if not r:raise NotFoundError('proposal not found')
        r=r[0]
        revision=rows(u,'SELECT * FROM core.proposal_revisions WHERE proposal_id=:id AND revision=:v',id=i,v=r['version'])
        if not revision:raise ConflictError('Подготовка ещё не завершена')
        return dict(r,**{k:v for k,v in revision[0].items() if k not in ('created_at','proposal_id')})

    def preview(self,e,a,i):
        with self.factory() as u:
            self._owner(u,e,a)
            return self._proposal(u,e,i)

    def queue(self,e,a,offset=0):
        with self.factory() as u:
            self._owner(u,e,a)
            candidates=rows(u,"SELECT id FROM core.proposed_actions WHERE entity_id=:e AND state NOT IN ('POSTED','CONFIRMED_NONPOSTING','REJECTED') ORDER BY priority,first_unresolved_at,id LIMIT 50 OFFSET :offset",e=e,offset=offset)
            return [self._proposal(u,e,UUID(r['id'])) for r in candidates]

    def revise(self,e,a,key,i,body):
        def run(u):
            old=self._proposal(u,e,i,True)
            if old['version']!=body.expected_version or old['state'] in TERMINAL:raise ConflictError('Карточка уже изменилась')
            reject_floats(body.payload)
            payload=SCHEMAS[old['action_type']].model_validate(body.payload).model_dump(mode='json')
            original=u.session.get(InboxRow,UUID(old['inbox_id']))
            preview=self._validate(u,e,old['action_type'],payload,bool(original.duplicate_of_id))
            n=old['version']+1
            provenance={k:dict(category='OWNER_INPUT',actor_id=str(a)) for k in payload}
            self._revision(u,i,n,payload,provenance,preview,actor=a)
            u.session.execute(text('UPDATE core.proposed_actions SET version=:n,state=:state WHERE id=:id'),dict(n=n,state='NEEDS_INPUT' if preview['missing'] else 'READY',id=i))
            self._audit(u,i,n,a,'EDIT',dict(payload=payload))
            return self._proposal(u,e,i)
        return self._write(e,a,key,'owner_revision',dict(id=str(i),**body.model_dump(mode='json')),run)

    def _audit(self,u,i,n,a,action,snapshot,reason=None):
        u.session.execute(text('INSERT INTO core.proposal_actions(id,proposal_id,revision,actor_id,action,snapshot,reason) VALUES(:id,:i,:n,:a,:action,CAST(:snapshot AS jsonb),:reason)'),dict(id=uuid4(),i=i,n=n,a=a,action=action,snapshot=json.dumps(snapshot),reason=reason))

    def decide(self,e,a,key,i,body,action):
        def run(u):
            old=self._proposal(u,e,i,True)
            if old['version']!=body.expected_version or old['state'] in TERMINAL:raise ConflictError('Карточка уже изменилась')
            n=old['version']+1
            self._revision(u,i,n,old['payload'],old['provenance'],dict(missing=old['validation'],basis=old['basis']),actor=a)
            u.session.execute(text('UPDATE core.proposed_actions SET version=:n,state=:state,deferred_at=CASE WHEN :state=\'DEFERRED\' THEN now() ELSE deferred_at END WHERE id=:id'),dict(n=n,state='DEFERRED' if action=='DEFER' else 'REJECTED',id=i))
            self._audit(u,i,n,a,action,dict(digest=old['digest']),body.reason)
            return self._proposal(u,e,i)
        return self._write(e,a,key,'owner_'+action.lower(),dict(id=str(i),**body.model_dump(mode='json')),run)

    def confirm(self,e,a,key,i,body):
        def run(u):
            old=self._proposal(u,e,i,True)
            if old['state'] in ('POSTED','CONFIRMED_NONPOSTING'):
                return dict(old,effect=rows(u,'SELECT * FROM core.proposal_effect_links WHERE proposal_id=:id',id=i)[0])
            if old['version']!=body.expected_version or old['digest']!=body.preview_digest:raise ConflictError('Карточка изменилась; проверьте новую версию')
            if old['state']=='REJECTED':raise ConflictError('Карточка отклонена')
            # Shared locks cover payment remaining and inventory movement state.
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),dict(key='inventory:'+str(e)))
            for allocation_id in [old['payload'].get('obligation_id')]:
                if allocation_id:u.finance.get_obligation(UUID(allocation_id),for_update=True)
            original=u.session.get(InboxRow,UUID(old['inbox_id']))
            current=self._validate(u,e,old['action_type'],old['payload'],bool(original.duplicate_of_id))
            if current['basis']!=old['basis']:
                n=old['version']+1
                self._revision(u,i,n,old['payload'],old['provenance'],current,actor=a)
                u.session.execute(text("UPDATE core.proposed_actions SET state='STALE',version=:n WHERE id=:id"),dict(n=n,id=i))
                self._audit(u,i,n,a,'STALE',dict(previous_digest=old['digest']))
                return self._proposal(u,e,i)
            if current['missing'] or old['validation']:raise ValidationError(' · '.join(current['missing'] or old['validation']))
            self._audit(u,i,old['version'],a,'CONFIRM',dict(payload=old['payload'],digest=old['digest']),body.reason)
            effect=self._apply(u,e,a,i,old,original)
            u.session.execute(text('INSERT INTO core.proposal_effect_links(proposal_id,entity_id,'+effect['field']+') VALUES(:id,:e,:target)'),dict(id=i,e=e,target=effect['id']))
            u.session.execute(text("UPDATE core.proposed_actions SET state='POSTED',version=version+1 WHERE id=:id"),dict(id=i))
            self._revision(u,i,old['version']+1,old['payload'],old['provenance'],current,actor=a)
            return dict(self._proposal(u,e,i),effect=effect)
        return self._write(e,a,key,'owner_confirm',dict(id=str(i),**body.model_dump(mode='json')),run)

    def _apply(self,u,e,a,i,proposal,original):
        kind=proposal['action_type'];p=proposal['payload'];factory=lambda:BorrowedUow(u)
        identity='owner-action:'+str(i)
        if kind=='PAYABLE':
            service=FinanceService(factory)
            offset=u.finance.get_system_ledger_account(e,p['currency'],'GENERAL_EXPENSE' if p['direction']=='payable' else 'GENERAL_REVENUE')
            created=service.create_obligation(actor_id=a,idempotency_key=identity,command=CreateObligationCommand(entity_id=e,counterparty_id=p['counterparty_id'],direction=p['direction'],original_amount=p['amount'],currency=p['currency'],event_date=p['event_date'],due_date=p['due_date'],description=p['description'],attributions=p['attributions'],offset_ledger_account_id=offset))
            posted=service.post_obligation(actor_id=a,idempotency_key=identity+':post',obligation_id=UUID(created['id']),expected_version=created['version'])
            return dict(field='obligation_id',id=posted['id'])
        if kind=='PAYMENT':
            service=FinanceService(factory)
            created=service.create_payment(actor_id=a,idempotency_key=identity,command=CreatePaymentCommand(entity_id=e,counterparty_id=p['counterparty_id'],money_account_id=p['money_account_id'],direction=p['direction'],amount=p['amount'],currency=p['currency'],event_date=p['event_date'],reason=p['description'],source=identity,allocations=[dict(obligation_id=p['obligation_id'],amount=p['amount'])]))
            posted=service.post_payment(actor_id=a,idempotency_key=identity+':post',payment_id=UUID(created['id']),expected_version=created['version'])
            return dict(field='payment_id',id=posted['id'])
        if kind in ('INVENTORY_RECEIPT','INVENTORY_APPLICATION'):
            from greenhouse_owner_os.domain.inventory import InventoryInput, InventoryPost
            service=InventoryService(factory)
            draft=service.create(e,a,identity,InventoryInput(kind='receipt' if kind=='INVENTORY_RECEIPT' else 'application',event_date=p['event_date'],lines=p['lines'],notes=p['notes'],supplier_id=p['supplier_id'],evidence=dict(source='owner:r2:action',locator=str(i),source_document=str(original.id),raw_text=original.raw_text,verification='VERIFIED')))
            posted=service.post(e,a,identity+':post',UUID(draft['id']),InventoryPost(confirmed=True,expected_version=draft['version'],duplicate_reviewed=p['duplicate_reviewed']))
            return dict(field='inventory_id',id=posted['id'])
        if kind=='PERSONAL_TASK':
            task=uuid4()
            u.session.execute(text('INSERT INTO core.owner_personal_tasks(id,entity_id,owner_id,title,greenhouse_id,planned_date,note,inbox_id) VALUES(:id,:e,:a,:title,:g,:day,:note,:source)'),dict(id=task,e=e,a=a,title=p['title'],g=p['greenhouse_id'],day=p['planned_date'],note=p['original_instruction'],source=original.id))
            u.session.execute(text("INSERT INTO core.owner_personal_task_actions(id,task_id,actor_id,revision,action) VALUES(:id,:task,:a,1,'CREATE')"),dict(id=uuid4(),task=task,a=a))
            return dict(field='personal_task_id',id=str(task))
        raise ValidationError('Для этого действия нужен ручной режим')

    def counterparty(self,e,a,key,body):
        from greenhouse_owner_os.domain.references import canonical_key
        def run(u):
            name_key=canonical_key(body.name)
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),dict(k='counterparty:'+str(e)+':'+name_key))
            candidates=list(u.session.scalars(select(CounterpartyRow).where(CounterpartyRow.entity_id==e)))
            prior=[r for r in candidates if canonical_key(r.name)==name_key]
            if prior:return dict(id=str(prior[0].id),name=prior[0].name)
            row=CounterpartyRow(id=uuid4(),entity_id=e,name=body.name,counterparty_type='unclassified')
            u.session.add(row);u.session.flush()
            return dict(id=str(row.id),name=row.name)
        return self._write(e,a,key,'owner_counterparty',body.model_dump(mode='json'),run)

    def home(self,e,a):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            self._owner(u,e,a)
            today=datetime.now(ZoneInfo('Europe/Istanbul')).date()
            from datetime import date
            year=today.year if today.month>=10 else today.year-1
            start,end=date(year,10,1),date(year+1,10,1)
            balances=rows(u,"SELECT direction,currency,sum(remaining_amount) AS amount FROM finance.obligation_balances WHERE entity_id=:e AND settlement_status IN ('open','partial') GROUP BY direction,currency",e=e)
            season=rows(u,"""SELECT s.currency,sum(l.net_weight_kg) AS kg,sum(l.amount) AS revenue,
                sum(l.net_weight_kg) FILTER(WHERE l.count IS NOT NULL) AS covered_kg,
                sum(l.count) FILTER(WHERE l.count IS NOT NULL) AS covered_count
                FROM harvest.depot_settlements s JOIN harvest.settlement_lines l ON l.settlement_id=s.id
                WHERE s.entity_id=:e AND s.status='posted' AND s.event_date>=:start AND s.event_date<:end GROUP BY s.currency""",e=e,start=start,end=end)
            for b in season:
                kg=Decimal(b['kg']);covered=Decimal(b['covered_kg']) if b['covered_kg'] else Decimal(0)
                b.update(average_price=str(Decimal(b['revenue'])/kg) if kg else None,
                         average_bunch_weight=str(covered/Decimal(b['covered_count'])) if b['covered_count'] else None,
                         coverage_complete=covered==kg)
            tasks=rows(u,"SELECT * FROM core.owner_personal_tasks WHERE entity_id=:e AND owner_id=:a AND state='TO_DO' ORDER BY planned_date NULLS LAST,created_at,id",e=e,a=a)
            queue=rows(u,"""SELECT count(*) AS count,count(*) FILTER(WHERE first_unresolved_at<now()-interval '36 hours') AS overdue
                FROM core.proposed_actions WHERE entity_id=:e AND state NOT IN ('POSTED','CONFIRMED_NONPOSTING','REJECTED')""",e=e)[0]
            workers=rows(u,"SELECT count(*) AS active FROM workers.people WHERE entity_id=:e AND active",e=e)[0]
            preparing=rows(u,"SELECT count(*) AS count FROM core.processing_jobs WHERE entity_id=:e AND state IN ('QUEUED','RUNNING')",e=e)[0]
            return dict(entity_id=str(e),as_of=datetime.now(timezone.utc).isoformat(),balances=balances,season=dict(start=str(start),end=str(end),currencies=season),
                        personal_tasks=dict(count=len(tasks),nearest=tasks[:3]),workers=workers,confirmation_queue=queue,preparing=preparing['count'])

    def reminder_eligible(self,e,a,now=None):
        now=now or datetime.now(timezone.utc)
        with self.factory() as u:
            self._owner(u,e,a)
            return rows(u,"""SELECT p.id FROM core.proposed_actions p WHERE p.entity_id=:e
                AND p.state NOT IN ('POSTED','CONFIRMED_NONPOSTING','REJECTED')
                AND p.first_unresolved_at<:at-interval '36 hours'
                AND NOT EXISTS(SELECT 1 FROM core.owner_reminder_claims r WHERE r.proposal_id=p.id AND r.day=:day)
                ORDER BY p.priority,p.first_unresolved_at,p.id""",e=e,at=now,day=now.astimezone(ZoneInfo('Europe/Istanbul')).date())
