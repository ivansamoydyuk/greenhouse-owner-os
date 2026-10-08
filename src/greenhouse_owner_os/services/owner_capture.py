"""R2 source and review envelope. Providers prepare; only owner confirms.

The source commit precedes recognition. Trusted adapters borrow one UoW so
effect, authority snapshot, result link and request replay commit together.
"""
import base64
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import text, select
from greenhouse_owner_os.domain.errors import ValidationError, NotFoundError, ConflictError
from greenhouse_owner_os.domain.owner_capture import DTO
from greenhouse_owner_os.infrastructure.inbox_models import InboxRow
from greenhouse_owner_os.infrastructure.models import EntityRow, CounterpartyRow, WorkAreaRow, MoneyAccountRow, LedgerAccountRow
from .finance import FinanceService, _hash_request
from .capture import BorrowedUow, extract
from .inventory import rows
from .ocr import LocalOCR

TERMINAL=('POSTED','CONFIRMED_NONPOSTING','REJECTED')
ENGINE='owner-preparation-v1'
def js(v):return json.dumps(v,ensure_ascii=False,default=str)
def now():return datetime.now(timezone.utc)

class OwnerCaptureService:
    def __init__(self,factory,recognizer=None):
        self.factory=factory;self.finance=FinanceService(factory);self.recognizer=recognizer or LocalOCR()

    def owner(self,u,e,a):
        if not u.session.scalar(text('SELECT core.r2_owner(:e,:a)'),dict(e=e,a=a)):
            raise ValidationError('Доступ владельца к этой компании не подтверждён')

    def _write(self,e,a,key,op,payload,fn):
        if not key or len(key)>200:raise ValidationError('Stable request identity required')
        h=_hash_request(dict(entity_id=str(e),**payload))
        with self.factory() as u:
            self.owner(u,e,a);u.idempotency.lock(a,op,key)
            replay=self.finance._replay_if_exists(u,a,op,key,h)
            if replay:return replay
            result=fn(u)
            rid=UUID(result['id'])
            u.audit.append(actor_id=a,action=op,entity_type='owner_capture',entity_id=rid,before=None,after=result)
            u.idempotency.put(actor_id=a,operation=op,key=key,request_hash=h,resource_type='owner_capture',resource_id=rid,response=result)
            u.commit();return result

    def capture(self,e,a,key,b):
        raw=None
        if b.file_base64:
            try:raw=base64.b64decode(b.file_base64,validate=True)
            except ValueError:raise ValidationError('Файл повреждён')
            signatures={'application/pdf':b'%PDF-','image/jpeg':b'\xff\xd8\xff','image/png':b'\x89PNG\r\n\x1a\n'}
            if not raw or len(raw)>20*1024*1024 or not raw.startswith(signatures[b.mime_type]):raise ValidationError('Поддерживаются JPEG/PNG/PDF до 20 MiB. HEIC сохраните как JPEG.')
        fingerprint=hashlib.sha256(raw if raw is not None else b.text.encode()).hexdigest()
        h=_hash_request(b.model_dump(mode='json'))
        def run(u):
            locator=str(b.client_capture_id)
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':f'owner-source:{e}:{locator}'})
            prior=u.session.scalar(select(InboxRow).where(InboxRow.entity_id==e,InboxRow.source=='owner:r2',InboxRow.locator==locator))
            if prior:
                if prior.identity_hash!=h:raise ConflictError('Содержание источника изменилось; оригинал сохранён')
                return self._capture_view(u,prior)
            duplicate=u.session.scalar(select(InboxRow.id).where(InboxRow.entity_id==e,InboxRow.fingerprint==fingerprint).limit(1))
            row=InboxRow(id=uuid4(),entity_id=e,source_type='pdf' if b.mime_type=='application/pdf' else 'image' if raw else 'text',
                source='owner:r2',locator=locator,filename=b.filename,mime_type=b.mime_type,file_bytes=raw,raw_text=b.text,
                metadata_json=dict(mode=b.mode,timezone=b.timezone,client_capture_id=locator),received_at=b.captured_at,
                fingerprint=fingerprint,content_hash=_hash_request(dict(text=b.text,binary=fingerprint if raw else None)),identity_hash=h,
                duplicate_of_id=duplicate,extractor_version=ENGINE,extracted_text=None,extraction={},normalized={},
                validation=dict(review_required=True),route='UNRESOLVED',confidence='UNAVAILABLE',state='NEEDS_REVIEW',version=1,created_by=a)
            u.session.add(row);u.session.flush()
            rows(u,"INSERT INTO core.processing_jobs(id,entity_id,inbox_id,engine_version) VALUES(:id,:e,:s,:v) RETURNING id",id=uuid4(),e=e,s=row.id,v=ENGINE)
            return self._capture_view(u,row)
        return self._write(e,a,key,'owner_capture',b.model_dump(mode='json'),run)

    def _capture_view(self,u,s):
        job=rows(u,'SELECT id,state,attempts,error_code FROM core.processing_jobs WHERE inbox_id=:s ORDER BY created_at DESC LIMIT 1',s=s.id)[0]
        actions=rows(u,'SELECT id,state FROM core.proposed_actions WHERE inbox_id=:s ORDER BY priority,id',s=s.id)
        state='SERVER_SAVED' if job['state']=='QUEUED' else 'PROCESSING' if job['state']=='RUNNING' else 'FAILED' if job['state']=='FAILED' else 'NEEDS_REVIEW'
        if actions and all(p['state'] in TERMINAL for p in actions):state='CONFIRMED' if any(p['state']!='REJECTED' for p in actions) else 'REJECTED'
        elif actions and all(p['state']=='READY' for p in actions):state='READY'
        return dict(id=str(s.id),client_capture_id=s.locator,state=state,server_saved=True,job=job,proposals=actions,
                    captured_at=s.received_at.isoformat(),original_name=s.filename,text=s.raw_text,duplicate_of_id=str(s.duplicate_of_id) if s.duplicate_of_id else None)

    def source(self,e,a,i):
        with self.factory() as u:
            self.owner(u,e,a);s=u.session.get(InboxRow,i)
            if not s or s.entity_id!=e or s.source!='owner:r2':raise NotFoundError('Источник не найден')
            return self._capture_view(u,s)

    def original(self,e,a,i):
        with self.factory() as u:
            self.owner(u,e,a);s=u.session.get(InboxRow,i)
            if not s or s.entity_id!=e or s.source!='owner:r2':raise NotFoundError('Источник не найден')
            return s.file_bytes or (s.raw_text or '').encode(),s.mime_type or 'text/plain; charset=utf-8'

    def process(self,i):
        """Lease commit, external recognition, fenced result commit. No domain effect."""
        token=uuid4()
        with self.factory() as u:
            job=rows(u,"SELECT * FROM core.processing_jobs WHERE inbox_id=:i AND engine_version=:v AND attempts<3 AND (state='QUEUED' OR state='FAILED' AND retry_at<=clock_timestamp() OR state='RUNNING' AND lease_until<clock_timestamp()) FOR UPDATE SKIP LOCKED",i=i,v=ENGINE)
            if not job:return
            j=job[0];rows(u,"UPDATE core.processing_jobs SET state='RUNNING',attempts=attempts+1,lease_token=:t,lease_until=clock_timestamp()+interval '15 minutes',error_code=NULL WHERE id=:id RETURNING id",t=token,id=j['id'])
            s=u.session.get(InboxRow,i);source=dict(id=s.id,e=s.entity_id,a=s.created_by,raw=s.raw_text,binary=s.file_bytes,mime=s.mime_type,fp=s.fingerprint,day=s.received_at.astimezone(ZoneInfo('Europe/Istanbul')).date(),duplicate=bool(s.duplicate_of_id))
            u.commit()
        try:
            if source['binary']:
                output=self.recognizer.recognize(source['binary'],source['mime'])
            else:output=dict(provider='local',engine='source-text',version=ENGINE,text=source['raw'],pages=[],warnings=[])
            with self.factory() as u:
                j=rows(u,'SELECT * FROM core.processing_jobs WHERE id=:id FOR UPDATE',id=j['id'])[0]
                if j['state']!='RUNNING' or j['lease_token']!=str(token):return
                specs=self.prepare(u,source,output)
                run_id=uuid4()
                rows(u,'INSERT INTO core.extraction_runs(id,entity_id,inbox_id,job_id,attempt,provider,engine_version,input_hash,raw_output,normalized,warnings) VALUES(:id,:e,:s,:j,:n,:p,:v,:h,CAST(:raw AS jsonb),CAST(:norm AS jsonb),CAST(:w AS jsonb)) RETURNING id',id=run_id,e=source['e'],s=i,j=j['id'],n=j['attempts'],p=output['provider'],v=output['version'],h=source['fp'],raw=js(output),norm=js(specs),w=js(output['warnings']))
                for idx,(kind,payload,provenance) in enumerate(specs):
                    slot=f'{kind}:{idx}'
                    # Never regenerate a reviewed slot or change its effect kind.
                    if rows(u,'SELECT id FROM core.proposed_actions WHERE inbox_id=:s AND slot=:slot',s=i,slot=slot):continue
                    pid=uuid4();priority=0 if kind in ('PAYABLE','PAYMENT','DIRECT_EXPENSE','STOCK_RECEIPT','STOCK_APPLICATION','INVOICE_MATCH') else 1 if kind=='DEPOT_RECEIPT' else 2
                    rows(u,"INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by) VALUES(:id,:e,:s,:slot,:k,'PREPARING',1,:p,:a) RETURNING id",id=pid,e=source['e'],s=i,slot=slot,k=kind,p=priority,a=source['a'])
                    p=self._proposal(u,source['e'],pid)
                    warnings=list(output['warnings'])
                    if source['duplicate']:warnings.append('Возможный повтор источника: проверьте оригинал')
                    self._revision(u,p,payload,provenance,source['a'],'PREPARE',run_id,warnings)
                rows(u,"UPDATE core.processing_jobs SET state='SUCCEEDED',lease_token=NULL,lease_until=NULL WHERE id=:id RETURNING id",id=j['id']);u.commit()
        except Exception:
            # No raw provider exception/original/credentials in API or logs.
            with self.factory() as u:
                rows(u,"UPDATE core.processing_jobs SET state='FAILED',error_code='PREPARATION_FAILED',retry_at=clock_timestamp()+interval '1 minute',lease_token=NULL,lease_until=NULL WHERE id=:id AND lease_token=:t RETURNING id",id=j['id'],t=token);u.commit()

    def prepare(self,u,s,out):
        raw=out['text'] or '';refs=lambda m:list(u.session.scalars(select(m).where(m.entity_id==s['e'])))
        refs_kw=dict(counterparties=refs(CounterpartyRow),areas=refs(WorkAreaRow),accounts=refs(MoneyAccountRow),
                     products=rows(u,'SELECT * FROM inventory.products WHERE entity_id=:e',e=s['e']),
                     locations=rows(u,'SELECT * FROM inventory.locations WHERE entity_id=:e',e=s['e']),workers=rows(u,'SELECT * FROM workers.people WHERE entity_id=:e',e=s['e']))
        route,f,_=extract(raw,today=s['day'],**refs_kw)
        provenance={}
        if route=='TASKS' or re.search(r'(?i)\b(починит|полить|закрыть окна|открыть окна)\b',raw):
            from .tasks_capture import extract_task
            if route!='TASKS':
                task=extract_task(re.sub('(?i)починит','починить',raw),areas=refs_kw['areas'],workers=refs_kw['workers'],today=s['day']);f=task[1] if task else {}
            workers=f.get('suggested_worker_ids',[])
            payload=dict(title=raw[:200],original_instruction=raw,greenhouse_id=f.get('greenhouse_id'),planned_date=f.get('planned_date'),worker_id=workers[0] if len(workers)==1 else None)
            for k in payload:provenance[k]={'category':'SOURCE FACT' if k in ('title','original_instruction') else 'INFERENCE','source_text':raw}
            # An unknown named worker stays a worker proposal, never becomes finance
            # or silently becomes a personal task assigned to the owner.
            kind='WORKER_TASK' if workers or re.search(r'(?i)(починит|работник|асыф|инаят|умар|мухам)',raw) else 'PERSONAL_TASK'
            return [(kind,payload,provenance)]
        if route=='INVENTORY':
            kind='STOCK_RECEIPT' if f.get('kind')=='receipt' else 'STOCK_APPLICATION' if f.get('kind')=='application' else 'UNRESOLVED'
            if kind=='UNRESOLVED':return [(kind,dict(note=raw[:2000]),{})]
            return [(kind,dict(event_date=f.get('event_date') or s['day'].isoformat(),lines=f['lines'],notes=raw[:2000]),{'event_date':{'category':'INFERENCE','basis':'capture_date'}})]
        depot=self._depot_text(raw,refs_kw['counterparties'])
        if depot:
            return [('DEPOT_RECEIPT',depot,{'lines':{'category':'SOURCE FACT','pages':out.get('pages',[])},'qualities':{'category':'INFERENCE','basis':'depot_scoped_unverified_candidate'}})]
        if route=='FINANCE' and f.get('kind'):
            amount=f.get('amount')
            if not amount:
                n=re.findall(r'(?<![\w.+-])(\d+(?:[ \u00a0]\d{3})*(?:[,.]\d{1,2})?)(?!\w)',raw)
                if len(n)==1:amount=n[0].replace(' ','').replace('\u00a0','').replace(',','.')
            payload=dict(amount=amount,currency=f.get('currency','TRY'),event_date=f.get('event_date') or s['day'].isoformat(),
                         counterparty_id=f.get('counterparty_id'),description=raw[:2000],due_date=f.get('due_date'),money_account_id=f.get('money_account_id'))
            for k,v in payload.items():provenance[k]={'category':'UNKNOWN' if v is None else 'INFERENCE' if k in ('event_date','currency','counterparty_id','money_account_id') else 'SOURCE FACT','source_text':raw}
            return [('PAYMENT' if f['kind']=='payment' else 'PAYABLE',payload,provenance)]
        if re.search(r'(?i)(fatura|invoice|счёт поставщика)',raw):
            # Invoice is not evidence that goods arrived or cash was paid.
            return [('PAYABLE',dict(description=raw[:2000],event_date=s['day'].isoformat()),{}),('INVOICE_MATCH',dict(note=raw[:2000]),{})]
        return [('UNRESOLVED',dict(note=raw[:2000] or 'Текст не распознан. Оригинал сохранён.'),{})]

    def _depot_text(self,raw,parties):
        if not re.search(r'(?i)(grand|birinci|ikinci|i̇kinci|alacak)',raw):return None
        lines=[];qualities=[]
        number=lambda v:v.replace('.','').replace(',','.') if ',' in v else v
        for line in raw.splitlines():
            label=re.search(r'(?i)\b(Grand|Birinci|İkinci|Ikinci|Sera|Yerli)\b',line)
            if not label:continue
            nums=re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)',line[label.end():])
            # Bounded labelled format [count optional] kg, price, amount.
            # Other/partial layouts retain source and require owner input.
            if len(nums) not in (3,4):continue
            try:
                kg,price,amount=[number(v) for v in nums[-3:]]
                if any(Decimal(v)<=0 for v in (kg,price,amount)):continue
                ln=dict(original_quality_name=label.group(),net_weight_kg=kg,unit_price=price,stated_amount=amount,source_line=line[:1000])
                if len(nums)==4 and nums[0].isdigit():ln['count']=int(nums[0])
                lines.append(ln)
                qualities.append({'grand':'HIGHEST','birinci':'FIRST','ikinci':'SECOND','i̇kinci':'SECOND'}.get(label.group().casefold()))
            except Exception:continue
        match=[p for p in parties if p.counterparty_type in ('depot','customer','both') and p.name.casefold() in raw.casefold()]
        dates=re.findall(r'(?<!\d)(\d{2})[./](\d{2})[./](20\d{2})(?!\d)',raw)
        event=None
        if len(set(dates))==1:
            try:d,m,y=dates[0];event=date(int(y),int(m),int(d)).isoformat()
            except ValueError:pass
        totals=re.findall(r'(?im)(?:alacak|toplam)\s*[:=]?\s*(\d+(?:[.,]\d+)*)',raw)
        alacak=re.findall(r'(?im)alacak\s*[:=]?\s*(\d+(?:[.,]\d+)*)',raw)
        # Görgün candidates are only available when that depot is identified;
        # other depots require receipt-specific owner classification.
        if not match or 'görgün' not in match[0].name.casefold():qualities=[None for _ in lines]
        return dict(depot_id=str(match[0].id) if len(match)==1 else None,event_date=event,lines=lines,qualities=qualities,
                    stated_total=number(totals[0]) if len(set(totals))==1 else None,alacak_value=number(alacak[0]) if len(alacak)==1 else None)

    def _proposal(self,u,e,i,lock=False):
        p=rows(u,'SELECT * FROM core.proposed_actions WHERE id=:i AND entity_id=:e'+(' FOR UPDATE' if lock else ''),i=i,e=e)
        if not p:raise NotFoundError('Предложение не найдено')
        return p[0]

    def _validate(self,u,p,payload):
        from pydantic import ValidationError as InputError
        try:fields=DTO[p['action_type']].model_validate(payload).model_dump(mode='json')
        except InputError as ex:raise ValidationError('Проверьте значения карточки') from ex
        kind=p['action_type'];missing=[];basis={};e=p['entity_id']
        def required(key,label):
            if fields.get(key) is None or fields.get(key)=='':missing.append(label)
        def scoped(table,id):
            if not id:return None
            found=rows(u,'SELECT * FROM '+table+' WHERE id=:id AND entity_id=:e',id=id,e=e)
            if not found or found[0].get('active') is False:raise ValidationError('Справочник недоступен в этой компании')
            basis[table+':'+str(id)]=found[0];return found[0]
        if kind in ('PAYABLE','PAYMENT','DIRECT_EXPENSE'):
            required('amount','Сумма');required('event_date','Дата')
            if kind!='DIRECT_EXPENSE':required('counterparty_id','Кому / от кого')
            scoped('core.counterparties',fields.get('counterparty_id'))
            account=scoped('finance.money_accounts',fields.get('money_account_id'))
            if account and account['currency']!=fields['currency']:raise ValidationError('Валюта счёта не совпадает')
            if kind!='PAYABLE':required('money_account_id','Наличные / Банк')
            if kind=='PAYMENT':
                required('payment_purpose','Назначение: долг / аванс / расход')
                if fields.get('payment_purpose')!='debt':missing.append('Аванс / новый расход: откройте точный ручной режим')
                required('obligation_id','Какой долг погасить')
                oid=fields.get('obligation_id')
                if oid:
                    debt=scoped('finance.obligations',oid)
                    remaining=rows(u,'SELECT * FROM finance.obligation_balances WHERE obligation_id=:id',id=oid)[0]
                    basis['debt_remaining']=remaining
                    if debt['status']!='posted' or debt['direction']!='payable' or debt['counterparty_id']!=fields['counterparty_id'] or debt['currency']!=fields['currency']:raise ValidationError('Выберите действующий долг этому контрагенту')
                    if fields.get('amount') and Decimal(fields['amount'])>Decimal(remaining['remaining_amount']):missing.append('Сумма больше остатка долга; уточните назначение')
        elif kind in ('STOCK_RECEIPT','STOCK_APPLICATION'):
            required('event_date','Дата')
            if not fields['lines']:missing.append('Товар и количество')
            for ln in fields['lines']:
                for key,label in [('product_id','Товар'),('location_id','Склад'),('quantity','Количество'),('unit','Единица')]:
                    if ln.get(key) is None:missing.append(label)
                product=scoped('inventory.products',ln.get('product_id'));scoped('inventory.locations',ln.get('location_id'))
                if ln.get('greenhouse_id'):scoped('core.work_areas',ln['greenhouse_id'])
                if product and ln.get('unit') and ln['unit']!=product['base_unit']:
                    conv=rows(u,'SELECT * FROM inventory.conversions WHERE product_id=:p AND unit=:unit',p=product['id'],unit=ln['unit']);basis['conversion:'+product['id']+ln['unit']]=conv
                    if not conv:missing.append('Проверенный размер упаковки')
                if ln.get('product_id') and ln.get('location_id'):
                    basis['stock:'+ln['product_id']+ln['location_id']]=rows(u,'SELECT * FROM inventory.stock WHERE entity_id=:e AND product_id=:p AND location_id=:l',e=e,p=ln['product_id'],l=ln['location_id'])
                    basis['movement_sequence']=rows(u,'SELECT coalesce(max(seq),0) seq FROM inventory.movements WHERE entity_id=:e',e=e)
        elif kind in ('PERSONAL_TASK','WORKER_TASK'):
            required('title','Задача');scoped('core.work_areas',fields.get('greenhouse_id'))
            if kind=='WORKER_TASK':
                required('worker_id','Работник');required('planned_date','Дата');required('greenhouse_id','Теплица')
                worker=scoped('workers.people',fields.get('worker_id'))
                if worker and worker['language']!='ru' and not fields.get('rendered_text'):missing.append('Проверенный перевод: задача пока не отправляется')
        elif kind=='DEPOT_RECEIPT':
            required('depot_id','Депо');required('event_date','Дата');scoped('core.counterparties',fields.get('depot_id'))
            if not fields['lines']:missing.append('Нечитаемые строки: внесите по оригиналу')
            if len(fields['qualities'])!=len(fields['lines']) or any(q is None for q in fields['qualities']) or not fields['quality_reviewed']:missing.append('Подтвердите качества именно этого депо')
            if not fields['arithmetic_reviewed']:missing.append('Проверьте строки и сумму; Alacak не является оплатой')
            basis['existing_receipts']=rows(u,'SELECT id,version,status FROM harvest.depot_settlements WHERE entity_id=:e AND evidence_id IN (SELECT id FROM harvest.evidence WHERE fingerprint=(SELECT fingerprint FROM core.inbox_items WHERE id=:s))',e=e,s=p['inbox_id'])
        elif kind=='INVOICE_MATCH':
            required('receipt_id','Ранее подтверждённый приход')
            receipt=scoped('inventory.events',fields.get('receipt_id'))
            if receipt and (receipt['kind']!='receipt' or receipt['status']!='posted'):raise ValidationError('Выберите проведённый физический приход')
            scoped('finance.obligations',fields.get('finance_obligation_id'))
        else:missing.append('Выберите точный ручной режим по сохранённому оригиналу')
        return fields,list(dict.fromkeys(missing)),basis

    def _revision(self,u,p,payload,provenance,a,action,run_id=None,warnings=()):
        fields,missing,basis=self._validate(u,p,payload)
        missing+=list(warnings)
        v=p['version'] if action=='PREPARE' else p['version']+1
        digest=_hash_request(dict(id=p['id'],version=v,type=p['action_type'],payload=fields,basis=basis,missing=missing))
        rows(u,'INSERT INTO core.proposal_revisions(proposal_id,entity_id,version,schema_version,extraction_run_id,payload,provenance,missing,basis,preview_digest,actor_id,reason) VALUES(:i,:e,:v,1,:run,CAST(:f AS jsonb),CAST(:p AS jsonb),CAST(:m AS jsonb),CAST(:b AS jsonb),:d,:a,:reason) RETURNING version',i=p['id'],e=p['entity_id'],v=v,run=run_id,f=js(fields),p=js(provenance),m=js(missing),b=js(basis),d=digest,a=a,reason=action)
        rows(u,"UPDATE core.proposed_actions SET version=:v,state=:s,updated_at=clock_timestamp() WHERE id=:i RETURNING id",v=v,s='NEEDS_INPUT' if missing else 'READY',i=p['id'])
        self._audit(u,p,a,action,v,dict(payload=fields,preview_digest=digest))
        return self._view(u,self._proposal(u,p['entity_id'],p['id']))

    def _audit(self,u,p,a,action,v,snapshot):
        rows(u,'INSERT INTO core.proposal_actions(id,entity_id,proposal_id,version,actor_id,action,snapshot) VALUES(:id,:e,:p,:v,:a,:action,CAST(:s AS jsonb)) RETURNING id',id=uuid4(),e=p['entity_id'],p=p['id'],v=v,a=a,action=action,s=js(snapshot))

    def _view(self,u,p):
        rev=rows(u,'SELECT * FROM core.proposal_revisions WHERE proposal_id=:i AND version=:v',i=p['id'],v=p['version'])[0]
        src=u.session.get(InboxRow,UUID(p['inbox_id']))
        overdue=now()-datetime.fromisoformat(p['first_unresolved_at'])>timedelta(hours=36)
        return dict(p,revision=rev,source=dict(id=str(src.id),text=src.raw_text,filename=src.filename,has_file=src.file_bytes is not None),reminder_eligible=overdue and p['state'] not in TERMINAL)

    def queue(self,e,a,offset=0,limit=50):
        with self.factory() as u:
            self.owner(u,e,a)
            ps=rows(u,"SELECT * FROM core.proposed_actions WHERE entity_id=:e AND state NOT IN ('POSTED','CONFIRMED_NONPOSTING','REJECTED') ORDER BY priority,first_unresolved_at,id OFFSET :o LIMIT :l",e=e,o=offset,l=limit+1)
            return dict(items=[self._view(u,p) for p in ps[:limit]],next_offset=offset+limit if len(ps)>limit else None)

    def preview(self,e,a,i):
        with self.factory() as u:self.owner(u,e,a);return self._view(u,self._proposal(u,e,i))

    def review(self,e,a,i,key,b,action):
        def run(u):
            p=self._proposal(u,e,i,True)
            if p['version']!=b.expected_version or p['state'] in ('POSTED','CONFIRMED_NONPOSTING'):raise ConflictError('Карточка изменилась; обновите её')
            if action=='EDIT' or action=='REOPEN':
                payload=b.payload if action=='EDIT' else self._view(u,p)['revision']['payload']
                return self._revision(u,p,payload,{k:{'category':'OWNER INPUT'} for k in payload},a,action)
            if p['state']=='REJECTED':raise ConflictError('Предложение отклонено; восстановите новой ревизией')
            state='DEFERRED' if action=='DEFER' else 'REJECTED'
            rows(u,'UPDATE core.proposed_actions SET state=:s,deferred_at=CASE WHEN :s=\'DEFERRED\' THEN clock_timestamp() ELSE deferred_at END,updated_at=clock_timestamp() WHERE id=:i RETURNING id',s=state,i=i)
            self._audit(u,p,a,action,p['version'],dict(reason=b.reason))
            return self._view(u,self._proposal(u,e,i))
        return self._write(e,a,key,'owner_'+action.lower(),dict(id=str(i),**b.model_dump(mode='json')),run)

    def confirm(self,e,a,i,key,b):
        def run(u):
            p=self._proposal(u,e,i,True)
            linked=rows(u,'SELECT response FROM core.confirmation_effects WHERE proposal_id=:i',i=i)
            rev=self._view(u,p)['revision']
            if p['version']!=b.expected_version or rev['preview_digest']!=b.preview_digest:raise ConflictError('Карточка изменилась; подтвердите актуальную версию')
            if linked:return linked[0]['response']
            if p['state'] in ('REJECTED','FAILED','PREPARING'):raise ConflictError('Предложение нельзя подтвердить в этом состоянии')
            # Serialize all referenced domain writes using the core lock order.
            kind=p['action_type'];payload=rev['payload']
            if kind in ('STOCK_RECEIPT','STOCK_APPLICATION','INVOICE_MATCH'):
                u.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('inventory:'||CAST(:e AS text),0))"),{'e':str(e)})
            if payload.get('obligation_id'):u.finance.get_obligation(UUID(payload['obligation_id']),for_update=True)
            if payload.get('money_account_id'):u.finance.lock_money_accounts([UUID(payload['money_account_id'])])
            # Lock mutable references before taking the fresh preview basis.
            for basis_key in sorted(rev['basis']):
                if ':' in basis_key and basis_key.split(':',1)[0] in ('core.counterparties','finance.money_accounts','core.work_areas','inventory.products','inventory.locations','workers.people'):
                    table,rid=basis_key.split(':',1);u.session.execute(text('SELECT id FROM '+table+' WHERE id=:id FOR UPDATE'),dict(id=rid))
            fields,missing,basis=self._validate(u,p,payload)
            if basis!=rev['basis']:
                result=self._revision(u,p,payload,rev['provenance'],a,'STALE')
                rows(u,"UPDATE core.proposed_actions SET state='STALE' WHERE id=:i RETURNING id",i=i)
                return dict(result,state='STALE',stale=True)
            if rev['missing'] or missing:raise ValidationError('Уточните: '+', '.join(rev['missing'] or missing))
            result,link,nonposting=self._apply(u,e,a,p,fields)
            state='CONFIRMED_NONPOSTING' if nonposting else 'POSTED'
            response=dict(id=str(i),state=state,effect=result,version=p['version'])
            self._audit(u,p,a,'CONFIRM',p['version'],dict(preview_digest=b.preview_digest,payload=fields,effect=result))
            rows(u,'UPDATE core.proposed_actions SET state=:s,updated_at=clock_timestamp() WHERE id=:i RETURNING id',s=state,i=i)
            cols={'obligation_id':None,'payment_id':None,'inventory_id':None,'settlement_id':None,'owner_task_id':None,'worker_task_id':None,'direct_expense_id':None,'invoice_match_id':None};cols.update(link)
            rows(u,'INSERT INTO core.confirmation_effects(proposal_id,entity_id,version,actor_id,preview_digest,obligation_id,payment_id,inventory_id,settlement_id,owner_task_id,worker_task_id,direct_expense_id,invoice_match_id,response) VALUES(:p,:e,:v,:a,:d,:obligation_id,:payment_id,:inventory_id,:settlement_id,:owner_task_id,:worker_task_id,:direct_expense_id,:invoice_match_id,CAST(:r AS jsonb)) RETURNING proposal_id',p=i,e=e,v=p['version'],a=a,d=b.preview_digest,r=js(response),**cols)
            return response
        return self._write(e,a,key,'owner_confirm',dict(id=str(i),**b.model_dump(mode='json')),run)

    def _apply(self,u,e,a,p,f):
        kind=p['action_type'];factory=lambda:BorrowedUow(u);key='owner-effect:'+p['id'];s=u.session.get(InboxRow,UUID(p['inbox_id']))
        if kind=='PAYABLE':
            from greenhouse_owner_os.domain.commands import CreateObligationCommand
            finance=FinanceService(factory)
            offset=u.finance.get_system_ledger_account(e,f['currency'],'GENERAL_EXPENSE')
            d=finance.create_obligation(actor_id=a,idempotency_key=key,command=CreateObligationCommand(entity_id=e,counterparty_id=f['counterparty_id'],direction='payable',original_amount=f['amount'],currency=f['currency'],event_date=f['event_date'],due_date=f['due_date'],description=f['description'],offset_ledger_account_id=offset))
            r=finance.post_obligation(actor_id=a,idempotency_key=key+':post',obligation_id=UUID(d['id']),expected_version=d['version']);return r,dict(obligation_id=r['id']),False
        if kind=='PAYMENT':
            from greenhouse_owner_os.domain.commands import CreatePaymentCommand, AllocationInput
            finance=FinanceService(factory)
            d=finance.create_payment(actor_id=a,idempotency_key=key,command=CreatePaymentCommand(entity_id=e,counterparty_id=f['counterparty_id'],money_account_id=f['money_account_id'],direction='out',amount=f['amount'],currency=f['currency'],event_date=f['event_date'],source=f'proposal:{p["id"]}',reason=f['description'],allocations=(AllocationInput(obligation_id=f['obligation_id'],amount=f['amount']),)))
            r=finance.post_payment(actor_id=a,idempotency_key=key+':post',payment_id=UUID(d['id']),expected_version=d['version']);return r,dict(payment_id=r['id']),False
        if kind=='DIRECT_EXPENSE':
            from greenhouse_owner_os.domain.finance import FinancialPosting,PostingLine,PostingSide
            rid,pid=uuid4(),uuid4()
            cash=u.finance.get_money_account_ledger(UUID(f['money_account_id']),e,f['currency'])
            expense=u.finance.get_system_ledger_account(e,f['currency'],'GENERAL_EXPENSE')
            u.finance.add_posting(FinancialPosting(id=pid,entity_id=e,currency=f['currency'],event_date=date.fromisoformat(f['event_date']),posting_type='direct_expense_posted',source_type='direct_expense',source_id=rid,created_by=a,lines=[PostingLine(expense,PostingSide.DEBIT,Decimal(f['amount'])),PostingLine(cash,PostingSide.CREDIT,Decimal(f['amount']))]))
            u.session.flush()
            r=rows(u,"INSERT INTO finance.direct_expenses(id,entity_id,proposal_id,amount,currency,event_date,money_account_id,counterparty_id,description,status,posting_id,created_by) VALUES(:id,:e,:p,:amount,:currency,:event_date,:money_account_id,:counterparty_id,:description,'posted',:posting,:a) RETURNING *",id=rid,e=e,p=p['id'],posting=pid,a=a,**{k:f[k] for k in ('amount','currency','event_date','money_account_id','counterparty_id','description')})[0]
            return r,dict(direct_expense_id=rid),False
        if kind in ('STOCK_RECEIPT','STOCK_APPLICATION'):
            from .inventory import InventoryService
            from greenhouse_owner_os.domain.inventory import InventoryInput,InventoryEvidence,InventoryPost
            svc=InventoryService(factory)
            body=InventoryInput(kind='receipt' if kind=='STOCK_RECEIPT' else 'application',event_date=f['event_date'],lines=f['lines'],notes=f['notes'],evidence=InventoryEvidence(source='owner:r2-action',locator=p['id'],raw_text=s.raw_text or None,source_document=str(s.id),verification='VERIFIED'))
            d=svc.create(e,a,key,body);r=svc.post(e,a,key+':post',UUID(d['id']),InventoryPost(expected_version=d['version'],confirmed=True));return r,dict(inventory_id=r['id']),False
        if kind=='PERSONAL_TASK':
            tid=uuid4()
            r=rows(u,"INSERT INTO core.owner_tasks(id,entity_id,owner_id,proposal_id,title,greenhouse_id,planned_date,note,state) VALUES(:id,:e,:a,:p,:t,:g,:d,:n,'TO_DO') RETURNING *",id=tid,e=e,a=a,p=p['id'],t=f['title'],g=f['greenhouse_id'],d=f['planned_date'],n=f['note'])[0]
            rows(u,"INSERT INTO core.owner_task_actions(id,entity_id,task_id,version,actor_id,state) VALUES(:id,:e,:t,1,:a,'TO_DO') RETURNING id",id=uuid4(),e=e,t=tid,a=a)
            return r,dict(owner_task_id=tid),False
        if kind=='WORKER_TASK':
            from .workers import WorkersService
            from greenhouse_owner_os.domain.workers import TaskInput
            d=WorkersService(factory).create(e,a,key,TaskInput(title=f['title'],original_instruction=f['original_instruction'],description=f['original_instruction'],greenhouse_id=f['greenhouse_id'],planned_date=f['planned_date'],source='owner:r2-action',locator=p['id'],source_timestamp=s.received_at))
            return dict(d,not_sent=True),dict(worker_task_id=d['id']),True
        if kind=='INVOICE_MATCH':
            r=rows(u,'INSERT INTO core.invoice_receipt_links(id,entity_id,inbox_id,proposal_id,receipt_id,finance_obligation_id,reviewed_by,note) VALUES(:id,:e,:s,:p,:r,:f,:a,:n) RETURNING *',id=uuid4(),e=e,s=p['inbox_id'],p=p['id'],r=f['receipt_id'],f=f['finance_obligation_id'],a=a,n=f['note'])[0]
            return r,dict(invoice_match_id=r['id']),True
        if kind=='DEPOT_RECEIPT':
            from .harvest import HarvestService
            from greenhouse_owner_os.domain.harvest import SettlementInput,SettlementPost,EvidenceInput
            svc=HarvestService(factory)
            body=SettlementInput(depot_id=f['depot_id'],event_date=f['event_date'],currency=f['currency'],original_greenhouse=f['original_greenhouse'],stated_total=f['stated_total'],alacak_value=f['alacak_value'],lines=f['lines'],evidence=EvidenceInput(source='owner:r2-action',source_reference=p['id'],raw_text=s.raw_text or None))
            d=svc.settlement(e,a,key,body)
            for idx,q in enumerate(f['qualities']):
                rows(u,'INSERT INTO harvest.receipt_quality_reviews(settlement_id,line_no,entity_id,proposal_id,quality,reviewed_by) VALUES(:s,:n,:e,:p,:q,:a) RETURNING line_no',s=d['id'],n=idx,e=e,p=p['id'],q=q,a=a)
            # R2-specific classification is assigned before posting; legacy quality()
            # remains unchanged for historical/manual R1 contracts.
            ls=list(u.session.scalars(select(__import__('greenhouse_owner_os.infrastructure.harvest_models',fromlist=['LineRow']).LineRow).where(__import__('greenhouse_owner_os.infrastructure.harvest_models',fromlist=['LineRow']).LineRow.settlement_id==UUID(d['id'])).order_by(__import__('greenhouse_owner_os.infrastructure.harvest_models',fromlist=['LineRow']).LineRow.id)))
            # Receipt line reviews bind by original line text, not random row ID order.
            for ln in ls:
                matches=[idx for idx,l in enumerate(f['lines']) if l['source_line']==ln.source_line and l['original_quality_name']==ln.original_quality_name]
                if len(matches)!=1:raise ValidationError('Повторяющиеся строки требуют точного ручного режима')
                ln.normalized_quality=f['qualities'][matches[0]]
            u.session.flush();d=svc.get(e,UUID(d['id']))
            issues=d.get('issues',[])
            r=svc.post(e,a,key+':post',UUID(d['id']),SettlementPost(expected_version=d['version'],confirmed=True,reviewed_issues=tuple(issues),review_reason='Owner reviewed R2 receipt arithmetic and scoped qualities'))
            return r,dict(settlement_id=r['id']),False
        raise ValidationError('Для этого действия пока доступен ручной режим; источник сохранён')

    def task_state(self,e,a,i,key,b):
        def run(u):
            t=rows(u,'SELECT * FROM core.owner_tasks WHERE entity_id=:e AND owner_id=:a AND id=:i FOR UPDATE',e=e,a=a,i=i)
            if not t:raise NotFoundError('Задача не найдена')
            t=t[0]
            if t['version']!=b.expected_version:raise ConflictError('Задача изменилась')
            if t['state']==b.state:return t
            r=rows(u,'UPDATE core.owner_tasks SET state=:s,version=version+1,updated_at=clock_timestamp() WHERE id=:i RETURNING *',s=b.state,i=i)[0]
            rows(u,'INSERT INTO core.owner_task_actions(id,entity_id,task_id,version,actor_id,state) VALUES(:id,:e,:t,:v,:a,:s) RETURNING id',id=uuid4(),e=e,t=i,v=r['version'],a=a,s=b.state);return r
        return self._write(e,a,key,'owner_task_state',dict(id=str(i),**b.model_dump()),run)

    def reminders(self,e,a):
        with self.factory() as u:
            self.owner(u,e,a)
            ps=rows(u,"SELECT id FROM core.proposed_actions WHERE entity_id=:e AND state NOT IN ('POSTED','CONFIRMED_NONPOSTING','REJECTED') AND first_unresolved_at<clock_timestamp()-interval '36 hours' ORDER BY id",e=e)
            day=now().astimezone(ZoneInfo('Europe/Istanbul')).date()
            if ps:rows(u,"INSERT INTO core.owner_reminders(id,entity_id,actor_id,reminder_day,reason,state,proposal_ids) VALUES(:id,:e,:a,:d,'UNCONFIRMED','ELIGIBLE',CAST(:p AS jsonb)) ON CONFLICT DO NOTHING RETURNING id",id=uuid4(),e=e,a=a,d=day,p=js([p['id'] for p in ps]))
            if not ps:rows(u,"UPDATE core.owner_reminders SET state='CANCELLED' WHERE entity_id=:e AND actor_id=:a AND state='ELIGIBLE' RETURNING id",e=e,a=a)
            u.commit();return dict(eligible=len(ps),transport='IN_APP_ONLY',delivered=False)

    def home(self,e,a):
        from .owner_view import owner_view
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));self.owner(u,e,a)
            v=owner_view(lambda:BorrowedUow(u),e)
            today=now().astimezone(ZoneInfo('Europe/Istanbul')).date();year=today.year if today.month>=10 else today.year-1
            season=rows(u,"SELECT s.currency,sum(l.net_weight_kg) kg,sum(l.amount) revenue,sum(l.net_weight_kg) FILTER(WHERE l.count IS NOT NULL) count_covered_kg,sum(l.count) count FROM harvest.depot_settlements s JOIN harvest.settlement_lines l ON l.settlement_id=s.id WHERE s.entity_id=:e AND s.status='posted' AND s.event_date>=:start AND s.event_date<:end GROUP BY s.currency",e=e,start=date(year,10,1),end=date(year+1,10,1))
            for r in season:
                r['average_price']=str(Decimal(r['revenue'])/Decimal(r['kg'])) if Decimal(r['kg']) else None
                r['average_bunch_weight']=str(Decimal(r['count_covered_kg'])/Decimal(r['count'])) if r['count'] else None
                r['count_coverage_complete']=r['count_covered_kg']==r['kg']
            tasks=rows(u,"SELECT * FROM core.owner_tasks WHERE entity_id=:e AND owner_id=:a AND state='TO_DO' ORDER BY planned_date NULLS LAST,created_at LIMIT 3",e=e,a=a)
            count=rows(u,"SELECT count(*) n,count(*) FILTER(WHERE first_unresolved_at<clock_timestamp()-interval '36 hours') overdue FROM core.proposed_actions WHERE entity_id=:e AND state NOT IN ('POSTED','CONFIRMED_NONPOSTING','REJECTED')",e=e)[0]
            task_count=rows(u,"SELECT count(*) n FROM core.owner_tasks WHERE entity_id=:e AND owner_id=:a AND state='TO_DO'",e=e,a=a)[0]['n']
            return dict(id=str(e),finance=v,season_start=str(date(year,10,1)),season_end=str(date(year+1,10,1)),season=season,tasks=tasks,task_count=task_count,confirmations=count,
                        worker_exceptions=rows(u,"SELECT t.id,t.title FROM workers.tasks t WHERE t.entity_id=:e AND EXISTS(SELECT 1 FROM workers.reports r WHERE r.task_id=t.id AND r.claim='issue') LIMIT 3",e=e))

    def counterparty(self,e,a,key,b):
        from .references import ReferenceService
        from greenhouse_owner_os.domain.references import CounterpartySetup
        def run(u):
            # Name-only creates explicitly unclassified reference, not a supplier.
            entity=u.session.get(EntityRow,e)
            r=ReferenceService(lambda:BorrowedUow(u),environment=entity.reference_class).setup(kind='counterparty',entity_id=e,actor_id=a,idempotency_key='owner-party:'+key,command=CounterpartySetup(name=b.name,role='unclassified',source='owner:r2-name-confirmed'))
            return r
        return self._write(e,a,key,'owner_counterparty',b.model_dump(),run)

    def reverse_expense(self,e,a,i,key,b):
        from greenhouse_owner_os.domain.finance import FinancialPosting,PostingLine,PostingSide
        def run(u):
            rs=rows(u,'SELECT * FROM finance.direct_expenses WHERE id=:i AND entity_id=:e FOR UPDATE',i=i,e=e)
            if not rs:raise NotFoundError('Расход не найден')
            r=rs[0]
            if r['version']!=b.expected_version or r['status']!='posted':raise ConflictError('Расход уже изменён')
            if b.event_date<date.fromisoformat(r['event_date']):raise ValidationError('Дата отмены раньше расхода')
            ls=rows(u,'SELECT * FROM finance.posting_entries WHERE posting_id=:p ORDER BY id',p=r['posting_id'])
            pid=uuid4()
            u.finance.add_posting(FinancialPosting(id=pid,entity_id=e,currency=r['currency'],event_date=b.event_date,posting_type='direct_expense_reversed',source_type='direct_expense',source_id=i,created_by=a,reversal_of_id=UUID(r['posting_id']),reason=b.reason,lines=[PostingLine(UUID(l['ledger_account_id']),PostingSide.CREDIT if l['side']=='debit' else PostingSide.DEBIT,Decimal(l['amount'])) for l in ls]))
            u.session.flush()
            return rows(u,"UPDATE finance.direct_expenses SET status='reversed',version=version+1,reversal_posting_id=:p,reversed_by=:a,reversal_reason=:reason WHERE id=:i RETURNING *",p=pid,a=a,reason=b.reason,i=i)[0]
        return self._write(e,a,key,'owner_expense_reverse',dict(id=str(i),**b.model_dump(mode='json')),run)
