"""Global capture contract: conservative extraction -> review -> Finance draft.

No LLM/provider, autonomous posting, inferred currency/date/account or fuzzy entity
creation. Unsupported input remains reviewable evidence for a future domain.
"""
import hashlib
import re
from datetime import datetime, date
from decimal import Decimal
from zoneinfo import ZoneInfo
from uuid import UUID, uuid4
from typing import Literal
from pydantic import Field, model_validator
from sqlalchemy import select, text as sql_text
from greenhouse_owner_os.domain.commands import StrictModel, CreateObligationCommand, CreatePaymentCommand
from greenhouse_owner_os.domain.errors import ValidationError, NotFoundError, ConflictError
from greenhouse_owner_os.infrastructure.models import CaptureRow, CounterpartyRow, WorkAreaRow, MoneyAccountRow, LedgerAccountRow, ObligationRow
from greenhouse_owner_os.services.finance import FinanceService, _hash_request
from greenhouse_owner_os.services.owner_view import json_value

class CaptureInput(StrictModel):
    text: str = Field(min_length=1,max_length=10000)
    source: Literal['text','voice_transcript']='text'
    source_namespace: str|None=Field(default=None,min_length=1,max_length=200)
    source_reference: str|None=Field(default=None,min_length=1,max_length=2000)
    @model_validator(mode='after')
    def source_pair(self):
        if (self.source_namespace is None)!=(self.source_reference is None):raise ValueError('Stable source requires namespace and reference')
        return self

class CaptureConfirm(StrictModel):
    expected_version: int = Field(gt=0)
    reviewed: dict
    confirmed: Literal[True]

from .preparation_language import AREA_ALIASES, corrected, date_value, entity_matches, fold, money_amount, source_currency, value

def extract(text, *, counterparties=(), areas=(), accounts=(), products=(), locations=(), workers=(), today=None):
    from .tasks_capture import extract_task
    task=extract_task(text,areas=areas,workers=workers,today=today)
    if task:return task
    from .inventory_capture import extract_inventory
    inventory=extract_inventory(text,products=products,locations=locations,areas=areas,today=today)
    if inventory:return inventory
    from .harvest_capture import extract_harvest
    harvest=extract_harvest(text,counterparties=counterparties,areas=areas,today=today)
    if harvest:return harvest
    today=today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    t=fold(corrected(text,counterparties=counterparties,areas=areas,workers=workers,locations=locations));f={};gaps=[]
    if not text.strip():return 'UNRESOLVED',{},['Напишите событие или продиктуйте его.']
    advance='аванс' in t
    if any(x in t for x in ('банан','урожай','депо')) and (('должен мне' in t or 'должны мне' in t or 'продал' in t or 'сдал' in t) and not advance):
        route='HARVEST_DEPOT';gaps.append('Долг за бананы требует расчёта депо и исходного чека. Сообщение сохранено; Finance не создаёт вторую продажу.')
    else:route='FINANCE'
    strategic='стратегическ' in t or 'на запуск бизнеса' in t
    if strategic:f.update(kind='obligation',direction='payable',obligation_class='STRATEGIC',principal_received=('взял' in t or 'получил' in t))
    elif advance:f.update(kind='payment',direction='in' if ('получил' in t or 'от депо' in t) else 'out',allocations=[])
    elif any(x in t for x in ('заплатил','оплатил','получил деньги','получил оплату')):f.update(kind='payment',direction='in' if 'получил' in t else 'out')
    elif 'должен мне' in t or 'должны мне' in t:f.update(kind='obligation',direction='receivable',obligation_class='OPERATIONAL')
    elif 'должен' in t or 'должны' in t:f.update(kind='obligation',direction='payable',obligation_class='OPERATIONAL')
    else:route='UNRESOLVED';gaps.append('Уточните событие: долг, оплата или аванс.')
    amount=money_amount(t)
    currency=source_currency(t,None)
    if amount and currency:f.update(amount=amount,currency=currency)
    else:gaps.append('Укажите одну точную сумму и валюту.')
    matches=entity_matches(t,counterparties,inflect=True)
    if len(matches)==1:f['counterparty_id']=str(value(matches[0],'id'))
    else:gaps.append('Выберите контрагента из справочника; неизвестное имя нужно добавить вручную.')
    event,_=date_value(t,today)
    if event:f['event_date']=event
    else:gaps.append('Укажите дату события. Дата ввода и обещанный срок оплаты её не заменяют.')
    due=re.findall(r'(?:заплачу|заплатить|оплаты|оплатят|заплатят)\s+(\d{1,2})\s+(октября|ноября|декабря)\s+(20\d{2})',t)
    if len(due)==1:
        d,m,year=due[0]
        try:f['due_date']=date(int(year),{'октября':10,'ноября':11,'декабря':12}[m],int(d)).isoformat()
        except ValueError:gaps.append('Исправьте срок оплаты.')
    elif 'следующей неделе' in t:gaps.append('Укажите точную обещанную дату оплаты: «на следующей неделе» неоднозначно.')
    elif re.search(r'\d{1,2}\s+(октября|ноября|декабря)',t):gaps.append('Укажите год ожидаемой оплаты; он не подставляется автоматически.')
    mentioned=entity_matches(t,areas,'area')
    if len(mentioned)==1 and 'amount' in f and f.get('kind')=='obligation':
        f['attributions']=[{'work_area_id':str(mentioned[0].id),'amount':f['amount']}]
    elif len(mentioned)>1:gaps.append('Несколько теплиц: укажите суммы распределения; пропорции не вычисляются автоматически.')
    f['attributions']=f.get('attributions',[])
    account_matches=entity_matches(t,accounts)
    if len(account_matches)==1:f['money_account_id']=str(account_matches[0].id)
    if f.get('kind')=='payment' or f.get('principal_received'):
        if len(account_matches)!=1:gaps.append('Укажите счёт или кассу и проверьте валюту.')
    if f.get('kind')=='payment' and not advance:gaps.append('Выберите долг и сумму погашения; без распределения оплата станет авансом.')
    f['description']=text
    return route,f,gaps

def serialize_capture(row):
    return {c.name:json_value(getattr(row,c.name)) for c in CaptureRow.__table__.columns}

class BorrowedUow:
    def __init__(self,uow):self.uow=uow
    def __enter__(self):return self
    def __getattr__(self,key):return getattr(self.uow,key)
    def __exit__(self,*args):pass
    def commit(self):self.uow.session.flush()

class CaptureService:
    def __init__(self,factory):self.factory=factory;self.finance=FinanceService(factory)
    def create(self,entity_id,actor_id,key,body):
        if body.source_namespace in ('owner:r2','owner:r2-action'):
            raise ConflictError('R2 source must use its individual owner confirmation cards')
        payload=dict(entity_id=str(entity_id),**body.model_dump());h=_hash_request(payload)
        with self.factory() as uow:
            uow.idempotency.lock(actor_id,'capture_create',key)
            replay=self.finance._replay_if_exists(uow,actor_id,'capture_create',key,h)
            if replay:return replay
            from greenhouse_owner_os.infrastructure.models import EntityRow
            if not uow.session.get(EntityRow,entity_id):raise NotFoundError('entity not found')
            if body.source_reference:
                lock=_hash_request(dict(entity=str(entity_id),source=body.source_namespace,reference=body.source_reference))
                uow.session.execute(sql_text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':'capture-source:'+lock})
                prior=uow.session.scalar(select(CaptureRow).where(CaptureRow.entity_id==entity_id,CaptureRow.source_namespace==body.source_namespace,CaptureRow.source_reference==body.source_reference))
                if prior:
                    if prior.original_text!=body.text or prior.source!=body.source:raise ConflictError('Stable capture original changed; first original retained')
                    result=serialize_capture(prior)
                    uow.idempotency.put(actor_id=actor_id,operation='capture_create',key=key,request_hash=h,resource_type='capture',resource_id=prior.id,response=result)
                    uow.audit.append(actor_id=actor_id,action='capture_source_reuse',entity_type='capture',entity_id=prior.id,before=None,after=result)
                    uow.commit();return result
            content_key='capture-content:'+str(entity_id)+hashlib.sha256(body.text.strip().encode()).hexdigest()
            uow.session.execute(sql_text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':content_key})
            q=lambda model:list(uow.session.scalars(select(model).where(model.entity_id==entity_id)))
            from .inventory import rows
            route,fields,gaps=extract(body.text,workers=rows(uow,"SELECT * FROM workers.people WHERE entity_id=:e",e=entity_id),counterparties=q(CounterpartyRow),areas=q(WorkAreaRow),accounts=q(MoneyAccountRow),products=rows(uow,"SELECT * FROM inventory.products WHERE entity_id=:e",e=entity_id),locations=rows(uow,"SELECT * FROM inventory.locations WHERE entity_id=:e",e=entity_id))
            fingerprint=hashlib.sha256(body.text.strip().encode()).hexdigest()
            if uow.session.scalar(select(CaptureRow.id).where(CaptureRow.entity_id==entity_id,CaptureRow.fingerprint==fingerprint)):
                gaps.append('Похожее сообщение уже сохранено. Проверьте, что это другое событие.')
            row=CaptureRow(id=uuid4(),entity_id=entity_id,original_text=body.text,source=body.source,fingerprint=fingerprint,
                source_namespace=body.source_namespace,source_reference=body.source_reference,route=route,extracted=fields,gaps=gaps,state='REVIEW' if gaps else 'READY',created_by=actor_id,version=1)
            uow.session.add(row);uow.session.flush();r=serialize_capture(row)
            uow.audit.append(actor_id=actor_id,action='capture_create',entity_type='capture',entity_id=row.id,before=None,after=r)
            uow.idempotency.put(actor_id=actor_id,operation='capture_create',key=key,request_hash=h,resource_type='capture',resource_id=row.id,response=r)
            uow.commit();return r
    def get(self,entity_id,cid):
        with self.factory() as uow:
            row=uow.session.get(CaptureRow,cid)
            if not row or row.entity_id!=entity_id:raise NotFoundError('capture not found')
            return serialize_capture(row)
    def confirm(self,entity_id,cid,actor_id,key,body):
        h=_hash_request(dict(entity_id=str(entity_id),id=str(cid),**body.model_dump()))
        with self.factory() as uow:
            uow.idempotency.lock(actor_id,'capture_confirm',key)
            replay=self.finance._replay_if_exists(uow,actor_id,'capture_confirm',key,h)
            if replay:return replay
            row=uow.session.scalar(select(CaptureRow).where(CaptureRow.id==cid).with_for_update())
            if not row or row.entity_id!=entity_id:raise NotFoundError('capture not found')
            if row.state=='LINKED':
                if not row.source_reference or {k:v for k,v in (row.confirmed_review or {}).items() if k!='duplicate_reviewed'}!={k:v for k,v in body.reviewed.items() if k!='duplicate_reviewed'}:raise ConflictError('capture already confirmed with another interpretation')
                result=dict(capture=serialize_capture(row),posted=False)
                uow.idempotency.put(actor_id=actor_id,operation='capture_confirm',key=key,request_hash=h,resource_type='capture',resource_id=cid,response=result)
                uow.audit.append(actor_id=actor_id,action='capture_confirm_reuse',entity_type='capture',entity_id=cid,before=None,after=result)
                uow.commit();return result
            if row.version!=body.expected_version:raise ConflictError('capture version changed')
            if row.route=='TASKS':
                return self._confirm_task(uow,row,entity_id,cid,actor_id,key,body,h)
            if row.route=='INVENTORY':
                return self._confirm_inventory(uow,row,entity_id,cid,actor_id,key,body,h)
            if row.route=='HARVEST_DEPOT':
                return self._confirm_harvest(uow,row,entity_id,cid,actor_id,key,body,h)
            if row.route!='FINANCE':raise ValidationError('banana sale requires authoritative harvest/depot settlement')
            f=dict(body.reviewed);kind=f.pop('kind',None);received=f.pop('principal_received',False)
            if not isinstance(received,bool):raise ValidationError('strategic principal receipt requires explicit review')
            # Exact values only, before any Finance write; no float coercion/rounding.
            amount=f.get('amount')
            if not isinstance(amount,str) or not re.fullmatch(r'\d{1,18}(?:\.\d{1,2})?',amount) or Decimal(amount)<=0:
                raise ValidationError('amount requires exact positive money and currency')
            if f.get('currency') not in ('TRY','USD','EUR','UAH'):
                raise ValidationError('currency required')
            try:cp=uow.session.get(CounterpartyRow,UUID(f.get('counterparty_id','')))
            except (ValueError,TypeError):cp=None
            if not cp or cp.entity_id!=entity_id:raise ValidationError('counterparty requires explicit matching owner reference')
            if kind=='payment':
                try:a=uow.session.get(MoneyAccountRow,UUID(f.get('money_account_id','')))
                except (ValueError,TypeError):a=None
                if not a or (a.entity_id,a.currency)!=(entity_id,f.get('currency')):
                    raise ValidationError('currency and money account must match')
            confirmed_duplicate=f.pop('duplicate_reviewed',False)
            if any('Похожее' in g for g in row.gaps) and not confirmed_duplicate:raise ValidationError('duplicate suspicion requires explicit review')
            finance=FinanceService(lambda:BorrowedUow(uow))
            if kind=='obligation':
                amount=f.pop('amount',None);account=f.pop('money_account_id',None)
                explicit_offset=f.pop('offset_ledger_account_id',None)
                cls=f.get('obligation_class','OPERATIONAL')
                if received and cls=='STRATEGIC':
                    try:a=uow.session.get(MoneyAccountRow,UUID(account)) if account else None
                    except (ValueError,TypeError):a=None
                    if not a or (a.entity_id,a.currency)!=(entity_id,f.get('currency')):raise ValidationError('strategic received principal requires matching money account')
                    offset=a.ledger_account_id
                else:
                    code='STRATEGIC_PRINCIPAL' if cls=='STRATEGIC' else ('GENERAL_EXPENSE' if f.get('direction')=='payable' else 'GENERAL_REVENUE')
                    offset=UUID(explicit_offset) if explicit_offset else uow.finance.get_system_ledger_account(entity_id,f.get('currency'),code)
                cmd=CreateObligationCommand(entity_id=entity_id,original_amount=amount,offset_ledger_account_id=offset,**f)
                r=finance.create_obligation(actor_id=actor_id,idempotency_key='capture:'+str(cid),command=cmd)
            elif kind=='payment':
                f.pop('description',None);f.pop('attributions',None);f.pop('obligation_class',None)
                cmd=CreatePaymentCommand(entity_id=entity_id,source='capture:'+str(cid),reason=row.original_text,**f)
                r=finance.create_payment(actor_id=actor_id,idempotency_key='capture:'+str(cid),command=cmd)
            else:raise ValidationError('capture intent requires review')
            before=serialize_capture(row);row.confirmed_review=body.reviewed;row.finance_kind=kind;row.finance_id=UUID(r['id']);row.state='LINKED';row.version+=1
            uow.session.flush();result=dict(capture=serialize_capture(row),finance=r,posted=False)
            uow.audit.append(actor_id=actor_id,action='capture_confirm',entity_type='capture',entity_id=cid,before=before,after=dict(result,reviewed=body.reviewed))
            uow.idempotency.put(actor_id=actor_id,operation='capture_confirm',key=key,request_hash=h,resource_type='capture',resource_id=cid,response=result)
            uow.commit();return result

    def _confirm_harvest(self,uow,row,e,cid,actor,key,body,h):
        from .harvest import HarvestService
        from greenhouse_owner_os.domain.harvest import ReportInput,BatchInput,SettlementInput
        f=dict(body.reviewed);kind=f.pop('kind',None)
        duplicate_reviewed=f.pop('duplicate_reviewed',False)
        if any('Похожее' in g for g in row.gaps) and not duplicate_reviewed:raise ValidationError('duplicate suspicion requires explicit review')
        f.pop('greenhouse_id',None);f.pop('direction',None)
        evidence=dict(source=row.source_namespace or 'capture:'+row.source,source_reference=row.source_reference or str(cid),raw_text=row.original_text)
        service=HarvestService(lambda:BorrowedUow(uow))
        if kind=='report':
            f['evidence']=evidence
            r=service.report(e,actor,'capture:harvest:'+str(cid),ReportInput(**f))
        elif kind=='batch':
            # Capture itself is permanent provenance for an explicitly reviewed load.
            f.setdefault('link_evidence','capture:'+str(cid)+' '+row.original_text)
            r=service.batch(e,actor,'capture:harvest:'+str(cid),BatchInput(**f))
        elif kind=='settlement':
            f['evidence']=evidence
            r=service.settlement(e,actor,'capture:harvest:'+str(cid),SettlementInput(**f))
        else:raise ValidationError('select harvest draft type')
        before=serialize_capture(row);row.confirmed_review=body.reviewed;row.harvest_kind=kind;row.harvest_id=UUID(r['id']);row.state='LINKED';row.version+=1
        uow.session.flush();result=dict(capture=serialize_capture(row),harvest=r,posted=False)
        uow.audit.append(actor_id=actor,action='capture_confirm',entity_type='capture',entity_id=cid,before=before,after=dict(result,reviewed=body.reviewed))
        uow.idempotency.put(actor_id=actor,operation='capture_confirm',key=key,request_hash=h,resource_type='capture',resource_id=cid,response=result)
        uow.commit();return result

    def _confirm_inventory(self,uow,row,e,cid,actor,key,body,h):
        from .inventory import InventoryService
        from greenhouse_owner_os.domain.inventory import InventoryInput
        f=dict(body.reviewed);duplicate=f.pop('duplicate_reviewed',False)
        if any('Похожее' in g for g in row.gaps) and not duplicate:raise ValidationError('Проверьте возможный дубль')
        f['evidence']=dict(source=row.source_namespace or 'capture:'+row.source,locator=row.source_reference or str(cid),raw_text=row.original_text)
        r=InventoryService(lambda:BorrowedUow(uow)).create(e,actor,'capture:inventory:'+str(cid),InventoryInput(**f))
        if r['status']!='draft':raise ConflictError('Исходное событие уже проведено; используйте коррекцию')
        before=serialize_capture(row);row.confirmed_review=body.reviewed;row.inventory_kind=r['kind'];row.inventory_id=UUID(r['id']);row.state='LINKED';row.version+=1
        uow.session.flush();result=dict(capture=serialize_capture(row),inventory=r,posted=False)
        uow.audit.append(actor_id=actor,action='capture_confirm',entity_type='capture',entity_id=cid,before=before,after=result)
        uow.idempotency.put(actor_id=actor,operation='capture_confirm',key=key,request_hash=h,resource_type='capture',resource_id=cid,response=result)
        uow.commit();return result

    def _confirm_task(self,u,row,e,cid,a,key,body,h):
        from .workers import WorkersService
        from greenhouse_owner_os.domain.workers import TaskInput
        f=dict(body.reviewed)
        for k in ('duplicate_reviewed','suggested_worker_ids','recurrence_suggestion','kind'):f.pop(k,None)
        if any('Похожее' in g for g in row.gaps) and not body.reviewed.get('duplicate_reviewed'):raise ValidationError('Explicit duplicate review required')
        f.update(original_instruction=row.original_text,source=row.source_namespace or 'capture:'+row.source,locator=row.source_reference or str(cid))
        r=WorkersService(lambda:BorrowedUow(u)).create(e,a,'capture:tasks:'+str(cid),TaskInput(**f))
        row.task_id=UUID(r['id']);row.confirmed_review=body.reviewed;row.state='LINKED';row.version+=1
        u.session.flush();result=dict(capture=serialize_capture(row),task=r,posted=False)
        u.audit.append(actor_id=a,action='capture_confirm',entity_type='capture',entity_id=cid,before=None,after=result)
        u.idempotency.put(actor_id=a,operation='capture_confirm',key=key,request_hash=h,resource_type='capture',resource_id=cid,response=result)
        u.commit();return result
