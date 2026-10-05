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
from pydantic import Field
from sqlalchemy import select
from greenhouse_owner_os.domain.commands import StrictModel, CreateObligationCommand, CreatePaymentCommand
from greenhouse_owner_os.domain.errors import ValidationError, NotFoundError, ConflictError
from greenhouse_owner_os.infrastructure.models import CaptureRow, CounterpartyRow, WorkAreaRow, MoneyAccountRow, LedgerAccountRow, ObligationRow
from greenhouse_owner_os.services.finance import FinanceService, _hash_request
from greenhouse_owner_os.services.owner_view import json_value

class CaptureInput(StrictModel):
    text: str = Field(min_length=1,max_length=10000)
    source: Literal['text','voice_transcript']='text'

class CaptureConfirm(StrictModel):
    expected_version: int = Field(gt=0)
    reviewed: dict
    confirmed: Literal[True]

AREA_ALIASES={'İmamlı':('имамлы','имаммлы','imamlı','imamli'), 'Özvadi':('озвади','özvadi','ozvadi'), 'Yeşilöz':('ешилоз','ешилёз','yeşilöz','yesiloz')}

def extract(text, *, counterparties=(), areas=(), accounts=(), today=None):
    today=today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    t=text.casefold();f={};gaps=[]
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
    amounts=re.findall(r'(?<![\d.])(\d+(?:[ \u00a0]\d{3})*(?:[,.]\d{1,2})?)\s*(лир\w*|доллар\w*|евро|try|usd|eur|₺|\$)',t)
    if len(amounts)==1:
        value,cur=amounts[0];f['amount']=value.replace(' ','').replace('\u00a0','').replace(',','.')
        f['currency']='TRY' if cur.startswith(('лир','try','₺')) else 'USD' if cur.startswith(('доллар','usd','$')) else 'EUR'
    else:gaps.append('Укажите одну точную сумму и валюту.')
    matches=[]
    for cp in counterparties:
        # Only bounded known first-name stems; no approximate unseen-party creation.
        first=cp.name.casefold().split()[0]
        if cp.name.casefold() in t or (len(first)>=4 and re.search(r'(?<!\w)'+re.escape(first)+r'(?:у|а|ом)?(?!\w)',t)):matches.append(cp)
    if len(matches)==1:f['counterparty_id']=str(matches[0].id)
    else:gaps.append('Выберите контрагента из справочника; неизвестное имя нужно добавить вручную.')
    if re.search(r'\bсегодня\b',t):f['event_date']=today.isoformat()
    else:gaps.append('Укажите дату события. Дата ввода и обещанный срок оплаты её не заменяют.')
    due=re.findall(r'(?:заплачу|заплатить|оплаты|оплатят|заплатят)\s+(\d{1,2})\s+(октября|ноября|декабря)\s+(20\d{2})',t)
    if len(due)==1:
        d,m,year=due[0]
        try:f['due_date']=date(int(year),{'октября':10,'ноября':11,'декабря':12}[m],int(d)).isoformat()
        except ValueError:gaps.append('Исправьте срок оплаты.')
    elif 'следующей неделе' in t:gaps.append('Укажите точную обещанную дату оплаты: «на следующей неделе» неоднозначно.')
    elif re.search(r'\d{1,2}\s+(октября|ноября|декабря)',t):gaps.append('Укажите год ожидаемой оплаты; он не подставляется автоматически.')
    mentioned=[]
    for a in areas:
        if any(alias in t for alias in AREA_ALIASES.get(a.name,(a.name.casefold(),))):mentioned.append(a)
    if len(mentioned)==1 and 'amount' in f and f.get('kind')=='obligation':
        f['attributions']=[{'work_area_id':str(mentioned[0].id),'amount':f['amount']}]
    elif len(mentioned)>1:gaps.append('Несколько теплиц: укажите суммы распределения; пропорции не вычисляются автоматически.')
    f['attributions']=f.get('attributions',[])
    account_matches=[a for a in accounts if a.name.casefold() in t]
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
        payload=dict(entity_id=str(entity_id),**body.model_dump());h=_hash_request(payload)
        with self.factory() as uow:
            uow.idempotency.lock(actor_id,'capture_create',key)
            replay=self.finance._replay_if_exists(uow,actor_id,'capture_create',key,h)
            if replay:return replay
            from greenhouse_owner_os.infrastructure.models import EntityRow
            if not uow.session.get(EntityRow,entity_id):raise NotFoundError('entity not found')
            q=lambda model:list(uow.session.scalars(select(model).where(model.entity_id==entity_id)))
            route,fields,gaps=extract(body.text,counterparties=q(CounterpartyRow),areas=q(WorkAreaRow),accounts=q(MoneyAccountRow))
            fingerprint=hashlib.sha256(body.text.strip().encode()).hexdigest()
            if uow.session.scalar(select(CaptureRow.id).where(CaptureRow.entity_id==entity_id,CaptureRow.fingerprint==fingerprint)):
                gaps.append('Похожее сообщение уже сохранено. Проверьте, что это другое событие.')
            row=CaptureRow(id=uuid4(),entity_id=entity_id,original_text=body.text,source=body.source,fingerprint=fingerprint,
                route=route,extracted=fields,gaps=gaps,state='REVIEW' if gaps else 'READY',created_by=actor_id,version=1)
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
            if row.version!=body.expected_version or row.state=='LINKED':raise ConflictError('capture already confirmed or version changed')
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
                cls=f.get('obligation_class','OPERATIONAL')
                if received and cls=='STRATEGIC':
                    try:a=uow.session.get(MoneyAccountRow,UUID(account)) if account else None
                    except (ValueError,TypeError):a=None
                    if not a or (a.entity_id,a.currency)!=(entity_id,f.get('currency')):raise ValidationError('strategic received principal requires matching money account')
                    offset=a.ledger_account_id
                else:
                    code='STRATEGIC_PRINCIPAL' if cls=='STRATEGIC' else ('GENERAL_EXPENSE' if f.get('direction')=='payable' else 'GENERAL_REVENUE')
                    offset=uow.finance.get_system_ledger_account(entity_id,f.get('currency'),code)
                cmd=CreateObligationCommand(entity_id=entity_id,original_amount=amount,offset_ledger_account_id=offset,**f)
                r=finance.create_obligation(actor_id=actor_id,idempotency_key='capture:'+str(cid),command=cmd)
            elif kind=='payment':
                f.pop('description',None);f.pop('attributions',None);f.pop('obligation_class',None)
                cmd=CreatePaymentCommand(entity_id=entity_id,source='capture:'+str(cid),reason=row.original_text,**f)
                r=finance.create_payment(actor_id=actor_id,idempotency_key='capture:'+str(cid),command=cmd)
            else:raise ValidationError('capture intent requires review')
            before=serialize_capture(row);row.finance_kind=kind;row.finance_id=UUID(r['id']);row.state='LINKED';row.version+=1
            uow.session.flush();result=dict(capture=serialize_capture(row),finance=r,posted=False)
            uow.audit.append(actor_id=actor_id,action='capture_confirm',entity_type='capture',entity_id=cid,before=before,after=dict(result,reviewed=body.reviewed))
            uow.idempotency.put(actor_id=actor_id,operation='capture_confirm',key=key,request_hash=h,resource_type='capture',resource_id=cid,response=result)
            uow.commit();return result
