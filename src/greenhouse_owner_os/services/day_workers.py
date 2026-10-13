"""Source-bound crew facts; only explicit owner review creates Finance accrual."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
import json
from uuid import UUID, uuid4
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.domain.day_workers import plan_fields, positive_integer, actual_people, parse_allocations, exact_rate
from greenhouse_owner_os.domain.commands import CreateObligationCommand, AttributionInput
from .inventory import rows
from .finance import FinanceService, _hash_request
from .owner_capture import OwnerCaptureService
from .capture import BorrowedUow

TABLES={'scope':'greenhouse_scope','request':'day_requests','decision':'day_decisions',
        'clarification':'day_clarifications','actual':'day_actuals','confirmation':'day_confirmations','accrual':'day_accruals'}


def json_dump(value):
    return json.dumps(value,ensure_ascii=False,default=str)


class DayWorkersService:
    def __init__(self,factory):
        self.factory=factory; self.finance=FinanceService(factory); self.owner=OwnerCaptureService(factory)

    def _worker(self,u,e,a,w):
        found=rows(u,'SELECT * FROM workers.people WHERE entity_id=:e AND id=:w AND actor_id=:a AND active',e=e,w=w,a=a)
        if not found: raise NotFoundError('Active scoped worker required')
        return found[0]

    def _scope(self,u,e,w,g):
        if not u.session.scalar(text('SELECT workers.day_scope(:e,:w,:g)'),dict(e=e,w=w,g=g)):
            raise NotFoundError('Worker greenhouse scope is not authorized')

    def _greenhouse(self,u,e,g):
        if not rows(u,"SELECT id FROM core.work_areas WHERE id=:g AND entity_id=:e AND kind='greenhouse' AND parent_id IS NULL",g=g,e=e):
            raise ValidationError('Verified greenhouse is required')

    def _row(self,u,e,table,rid,lock=False):
        data=rows(u,'SELECT * FROM workers.'+table+' WHERE entity_id=:e AND id=:id'+(' FOR UPDATE' if lock else ''),e=e,id=rid)
        if not data: raise NotFoundError('DayWorkers record not found')
        return data[0]

    def _authorize_row(self,u,e,w,row):
        if not w:return
        if row.get('worker_id')!=str(w): raise NotFoundError('DayWorkers record not found')
        g=row.get('greenhouse_id')
        if not g and row.get('request_id'):g=self._row(u,e,'day_requests',row['request_id'])['greenhouse_id']
        if not g:raise NotFoundError('Verified reporting context required')
        self._scope(u,e,w,g)
        if 'event_date' in row:
            for item in rows(u,'SELECT greenhouse_id FROM workers.day_actual_allocations WHERE actual_id=:id',id=row['id']):self._scope(u,e,w,item['greenhouse_id'])

    def _write(self,e,a,key,op,body,fn,w=None,target=None):
        if not key or len(key)>200:raise ValidationError('Stable request identity required')
        command=body.model_dump(mode='json')
        if target:command.update(target)
        snapshot=dict(entity_id=str(e),actor_id=str(a),worker_id=str(w) if w else None,operation=op,command=command)
        h=_hash_request(snapshot); source='day-workers:'+op; locator=str(body.source.form_id)
        try:
            with self.factory() as u:
                if w:self._worker(u,e,a,w)
                else:self.owner.owner(u,e,a)
                u.idempotency.lock(a,'day_'+op,key)
                u.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('day-workers:'||CAST(:e AS text),0))"),dict(e=str(e)))
                # Identity authorization is checked on every retry; a revoked worker cannot replay private records.
                if w:
                    if op in ('request','actual'):
                        g=command.get('greenhouse_id')
                        if not g and command.get('request_id'):g=self._row(u,e,'day_requests',command['request_id'])['greenhouse_id']
                        if not g:raise ValidationError('Verified reporting context required')
                        self._scope(u,e,w,g)
                        if op=='actual':
                            for allocation in parse_allocations(body.source.fields['allocations']):self._scope(u,e,w,allocation.greenhouse_id)
                    elif op=='clarification':self._authorize_row(u,e,w,self._row(u,e,'day_requests',command['request_id']))
                replay=self.finance._replay_if_exists(u,a,'day_'+op,key,h)
                if replay:return replay
                prior=rows(u,'SELECT * FROM workers.sources WHERE entity_id=:e AND source=:s AND locator=:l',e=e,s=source,l=locator)
                if prior:
                    if prior[0]['event_type']!='day_'+op or prior[0]['snapshot']!=snapshot:
                        raise ConflictError('Original form changed; use a separate source and preserve the original')
                    saved=rows(u,'SELECT id FROM workers.'+TABLES[op]+' WHERE entity_id=:e AND source=:s AND locator=:l',e=e,s=source,l=locator)
                    if not saved:raise ConflictError('Original exists without a completed action; review its history')
                    receipts=rows(u,'SELECT response_json FROM audit.idempotency_keys WHERE actor_id=:a AND operation=:op AND resource_id=:id ORDER BY created_at LIMIT 1',a=a,op='day_'+op,id=saved[0]['id'])
                    if not receipts:raise ConflictError('Existing action requires history review')
                    result=receipts[0]['response_json']
                else:
                    u.session.execute(text('INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(:e,:s,:l,:kind,CAST(:snapshot AS jsonb))'),dict(e=e,s=source,l=locator,kind='day_'+op,snapshot=json_dump(snapshot)))
                    result=fn(u,dict(id=body.source.form_id,e=e,a=a,source=source,locator=locator))
                    u.audit.append(actor_id=a,action='day_'+op,entity_type='day_workers',entity_id=UUID(result['id']),before=None,after=result)
                u.idempotency.put(actor_id=a,operation='day_'+op,key=key,request_hash=h,resource_type='day_workers',resource_id=UUID(result['id']),response=result)
                u.commit();return result
        except DBAPIError as exc:
            code=getattr(exc.orig,'sqlstate',None)
            if code=='23505':raise ConflictError('This source, decision or actual has already been recorded') from exc
            if code in ('23514','23503','55000','22P02','22007','22008'):
                raise ValidationError(str(exc.orig).split('\n')[0]) from exc
            raise

    def scope(self,e,a,key,b):
        f=b.source.fields
        def run(u,p):
            self._greenhouse(u,e,f['greenhouse_id'])
            if not rows(u,'SELECT id FROM workers.people WHERE entity_id=:e AND id=:w AND active',e=e,w=f['worker_id']):raise ValidationError('Known active worker required')
            v=u.session.scalar(text('SELECT coalesce(max(version),0)+1 FROM workers.greenhouse_scope WHERE entity_id=:e AND worker_id=:w AND greenhouse_id=:g'),dict(e=e,w=f['worker_id'],g=f['greenhouse_id']))
            return rows(u,'INSERT INTO workers.greenhouse_scope(id,entity_id,worker_id,greenhouse_id,version,action,reason,source,locator,created_by) VALUES(:id,:e,:worker_id,:greenhouse_id,:v,:action,:reason,:source,:locator,:a) RETURNING *',**p,v=v,**f)[0]
        return self._write(e,a,key,'scope',b,run)

    def request(self,e,a,key,b,w=None):
        f=plan_fields(b.source.fields)
        def run(u,p):
            self._greenhouse(u,e,b.greenhouse_id)
            rows(u,'INSERT INTO workers.day_requests(id,entity_id,worker_id,greenhouse_id,people,days,work,start_date,note,source,locator,created_by) VALUES(:id,:e,:w,:g,:people,:days,:work,:start_date,:note,:source,:locator,:a) RETURNING id',**p,w=w,g=b.greenhouse_id,**f)
            return self._request_view(u,e,b.source.form_id,w)
        return self._write(e,a,key,'request',b,run,w)

    def decision(self,e,a,key,rid,b):
        f=b.source.fields
        def run(u,p):
            r=self._row(u,e,'day_requests',rid,True);v=self._version(u,rid)
            if v!=b.expected_version:raise ConflictError('Request changed; review the latest history')
            plan=plan_fields(f) if f['decision'] in ('APPROVE','REVISE_APPROVE') else None
            if f['decision']=='APPROVE' and {k:str(v) if isinstance(v,date) else v for k,v in plan.items()}!={k:r[k] for k in ('people','days','work','start_date','note')}:
                raise ValidationError('Use revised approval to change the original requested plan')
            result=rows(u,'INSERT INTO workers.day_decisions(id,entity_id,request_id,version,decision,plan,reason,source,locator,created_by) VALUES(:id,:e,:r,:v,:decision,CAST(:plan AS jsonb),:reason,:source,:locator,:a) RETURNING *',**p,r=rid,v=v+1,decision=f['decision'],plan=json_dump(plan) if plan else None,reason=f['reason'])[0]
            return dict(result,request=self._request_view(u,e,rid))
        return self._write(e,a,key,'decision',b,run,target={'request_id':str(rid)})

    def clarification(self,e,a,key,rid,b,w):
        def run(u,p):
            r=self._row(u,e,'day_requests',rid,True);self._authorize_row(u,e,w,r);v=self._version(u,rid)
            if v!=b.expected_version:raise ConflictError('Request changed; refresh the clarification')
            if not rows(u,"SELECT id FROM workers.day_decisions WHERE request_id=:r AND version=:v AND decision='CLARIFY'",r=rid,v=v):raise ValidationError('No current owner clarification question')
            result=rows(u,'INSERT INTO workers.day_clarifications(id,entity_id,request_id,version,worker_id,body,source,locator,created_by) VALUES(:id,:e,:r,:v,:w,:body,:source,:locator,:a) RETURNING *',**p,r=rid,v=v+1,w=w,body=b.source.fields['text'])[0]
            return dict(result,request=self._request_view(u,e,rid,w))
        return self._write(e,a,key,'clarification',b,run,w,{'request_id':str(rid)})

    def actual(self,e,a,key,b,w=None):
        f=b.source.fields;allocations=parse_allocations(f['allocations'])
        def run(u,p):
            if b.greenhouse_id:self._greenhouse(u,e,b.greenhouse_id)
            if b.request_id:
                r=self._row(u,e,'day_requests',b.request_id)
                self._authorize_row(u,e,w,r)
            if b.replaces_id:
                old=self._row(u,e,'day_actuals',b.replaces_id,True);self._authorize_row(u,e,w,old)
                if rows(u,'SELECT id FROM workers.day_accruals WHERE actual_id=:id',id=b.replaces_id):
                    raise ValidationError('Accrued corrections need owner financial review; original liability is preserved')
                if rows(u,'SELECT id FROM workers.day_actuals WHERE replaces_id=:id',id=b.replaces_id):raise ConflictError('Actual already has a replacement')
            rows(u,'INSERT INTO workers.day_actuals(id,entity_id,worker_id,greenhouse_id,request_id,replaces_id,event_date,people,work,full_day,note,source,locator,created_by) VALUES(:id,:e,:w,:g,:r,:old,:day,:people,:work,:full_day,:note,:source,:locator,:a) RETURNING id',**p,w=w,g=b.greenhouse_id,r=b.request_id,old=b.replaces_id,day=date.fromisoformat(f['event_date']),people=actual_people(f['people']),work=f['work'],full_day=f['full_day'],note=f['note'])
            for ordinal,item in enumerate(allocations):
                rows(u,'INSERT INTO workers.day_actual_allocations(actual_id,ordinal,greenhouse_id,work_area_id,people) VALUES(:id,:ordinal,:greenhouse_id,:work_area_id,:people) RETURNING actual_id',id=b.source.form_id,ordinal=ordinal,**item.model_dump(mode='json'))
            return self._actual_view(u,e,b.source.form_id,w)
        return self._write(e,a,key,'actual',b,run,w)

    def _current_actual(self,u,e,rid):
        r=self._row(u,e,'day_actuals',rid,True)
        if rows(u,'SELECT id FROM workers.day_actuals WHERE replaces_id=:id',id=rid):raise ConflictError('Actual has been replaced; review the current report')
        return r

    def confirmation(self,e,a,key,rid,b):
        def run(u,p):
            self._current_actual(u,e,rid)
            if rows(u,'SELECT id FROM workers.day_confirmations WHERE actual_id=:id',id=rid):raise ConflictError('Actual is already owner-confirmed')
            result=rows(u,'INSERT INTO workers.day_confirmations(id,entity_id,actual_id,source,locator,created_by) VALUES(:id,:e,:r,:source,:locator,:a) RETURNING *',**p,r=rid)[0]
            return dict(result,actual=self._actual_view(u,e,rid))
        return self._write(e,a,key,'confirmation',b,run,target={'actual_id':str(rid)})

    def accrual(self,e,a,key,rid,b):
        f=b.source.fields;rate=exact_rate(f['daily_rate'])
        def run(u,p):
            actual=self._current_actual(u,e,rid)
            if rows(u,'SELECT id FROM workers.day_accruals WHERE actual_id=:id',id=rid):raise ConflictError('Actual already has a Finance accrual; review its existing debt')
            allocations=rows(u,'SELECT * FROM workers.day_actual_allocations WHERE actual_id=:id ORDER BY ordinal',id=rid)
            if actual['people']==0:raise ValidationError('Explicit zero work has no accrual')
            if actual['full_day']!='true':raise ValidationError('Partial or unknown days require owner policy review; no full-day conversion')
            if sum(x['people'] for x in allocations)!=actual['people']:raise ValidationError('Complete explicit greenhouse allocation required before accrual')
            if not rows(u,"SELECT id FROM finance.ledger_accounts WHERE id=:id AND entity_id=:e AND currency=:currency AND account_class='expense'",id=f['expense_account_id'],e=e,currency=f['currency']):raise ValidationError('Known matching-currency expense account required')
            if not rows(u,'SELECT id FROM core.counterparties WHERE id=:id AND entity_id=:e',id=f['counterparty_id'],e=e):raise ValidationError('Known creditor required; crew lead identity is not inferred')
            if not rows(u,'SELECT id FROM workers.day_confirmations WHERE actual_id=:id',id=rid):
                u.session.execute(text('INSERT INTO workers.day_confirmations(id,entity_id,actual_id,source,locator,created_by) VALUES(:id,:e,:r,:source,:locator,:a)'),dict(p,id=uuid4(),r=rid))
            attribution=defaultdict(lambda:Decimal(0))
            for item in allocations:attribution[item['work_area_id'] or item['greenhouse_id']]+=item['people']*rate
            nested=FinanceService(lambda:BorrowedUow(u))
            obligation=nested.create_obligation(actor_id=a,idempotency_key='day-accrual-create:'+str(rid),command=CreateObligationCommand(entity_id=e,counterparty_id=UUID(f['counterparty_id']),direction='payable',original_amount=actual['people']*rate,currency=f['currency'],event_date=date.fromisoformat(actual['event_date']),due_date=date.fromisoformat(f['due_date']) if f['due_date'] else None,offset_ledger_account_id=UUID(f['expense_account_id']),description=('Дневные рабочие: '+actual['work'])[:2000],attributions=tuple(AttributionInput(work_area_id=UUID(area),amount=value) for area,value in attribution.items())))
            nested.post_obligation(actor_id=a,idempotency_key='day-accrual-post:'+str(rid),obligation_id=UUID(obligation['id']),expected_version=1)
            result=rows(u,'INSERT INTO workers.day_accruals(id,entity_id,actual_id,obligation_id,counterparty_id,daily_rate,currency,expense_account_id,rate_basis,source,locator,created_by) VALUES(:id,:e,:r,:obligation,:counterparty_id,:rate,:currency,:expense_account_id,:rate_basis,:source,:locator,:a) RETURNING *',**p,r=rid,obligation=obligation['id'],rate=rate,**{k:f[k] for k in ('counterparty_id','currency','expense_account_id','rate_basis')})[0]
            return dict(result,actual=self._actual_view(u,e,rid))
        return self._write(e,a,key,'accrual',b,run,target={'actual_id':str(rid)})

    def _original(self,u,row):
        data=rows(u,'SELECT snapshot FROM workers.sources WHERE entity_id=:e AND source=:s AND locator=:l',e=row['entity_id'],s=row['source'],l=row['locator'])
        return data[0]['snapshot']['command']['source']

    def _version(self,u,rid):
        return u.session.scalar(text('SELECT workers.day_request_version(:r)'),dict(r=rid))

    def _actual_view(self,u,e,rid,w=None):
        r=self._row(u,e,'day_actuals',rid);self._authorize_row(u,e,w,r)
        r['original']=self._original(u,r)
        names=rows(u,'SELECT name FROM core.work_areas WHERE id=:id',id=r['greenhouse_id']) if r['greenhouse_id'] else []
        r['greenhouse_name']=names[0]['name'] if names else None
        r['allocations']=rows(u,'SELECT x.*,g.name greenhouse_name,a.name work_area_name FROM workers.day_actual_allocations x JOIN core.work_areas g ON g.id=x.greenhouse_id LEFT JOIN core.work_areas a ON a.id=x.work_area_id WHERE actual_id=:id ORDER BY ordinal',id=rid)
        r['unallocated_people']=r['people']-sum(x['people'] for x in r['allocations'])
        r['people_days']=r['people'] if r['people']==0 or r['full_day']=='true' else None
        replacements=rows(u,'SELECT id FROM workers.day_actuals WHERE replaces_id=:id',id=rid)
        r['replacement_id']=replacements[0]['id'] if replacements else None;r['current']=not replacements
        confirmations=rows(u,'SELECT * FROM workers.day_confirmations WHERE actual_id=:id',id=rid)
        r['confirmation']=confirmations[0] if confirmations else None
        blockers=[]
        if not r['current']:blockers.append('ACTUAL_REPLACED')
        if r['people']==0:blockers.append('ZERO_WORK_NO_ACCRUAL')
        if r['people']>0 and r['full_day']!='true':blockers.append('FULL_DAY_UNRESOLVED')
        if r['unallocated_people']:blockers.append('ALLOCATION_INCOMPLETE')
        r['state']='CONFIRMED' if confirmations else 'REPORTED'
        if not w:
            accruals=rows(u,'''SELECT a.*,o.status obligation_status,o.original_amount,o.replacement_of_id,b.remaining_amount,b.settlement_status
              FROM workers.day_accruals a JOIN finance.obligations o ON o.id=a.obligation_id
              JOIN finance.obligation_balances b ON b.obligation_id=o.id WHERE a.actual_id=:id''',id=rid)
            r['accrual']=accruals[0] if accruals else None
            if accruals:
                r['accrual']['original']=self._original(u,accruals[0])
                names=rows(u,'SELECT name FROM core.counterparties WHERE id=:id',id=accruals[0]['counterparty_id'])
                r['accrual']['counterparty_name']=names[0]['name'] if names else None
                r['accrual']['correction_required']=accruals[0]['obligation_status']!='posted'
                r['accrual']['document_url']=f'/v1/entities/{e}/finance/analytics/documents/{accruals[0]["obligation_id"]}'
                r['state']='ACCRUED' if accruals[0]['obligation_status']=='posted' else 'FINANCIAL_REVIEW_REQUIRED'
            else:blockers.extend(['EXPLICIT_CREDITOR_RATE_REVIEW_REQUIRED'])
            r['accrual_blockers']=blockers
        else:
            # Confirmation is operational truth; its source may contain owner-only financial input.
            r['confirmation']=dict(id=confirmations[0]['id'],created_at=confirmations[0]['created_at']) if confirmations else None
            r.pop('created_by',None);r.pop('source',None);r.pop('locator',None)
        return r

    def _request_view(self,u,e,rid,w=None):
        r=self._row(u,e,'day_requests',rid);self._authorize_row(u,e,w,r)
        r['original']=self._original(u,r);r['version']=self._version(u,rid)
        r['original_plan']={k:r[k] for k in ('people','days','work','start_date','note')}
        r['decisions']=rows(u,'SELECT * FROM workers.day_decisions WHERE request_id=:id ORDER BY version',id=rid)
        r['clarifications']=rows(u,'SELECT * FROM workers.day_clarifications WHERE request_id=:id ORDER BY version',id=rid)
        for item in r['decisions']+r['clarifications']:item['original']=self._original(u,item)
        latest=r['decisions'][-1] if r['decisions'] else None
        r['state']={'APPROVE':'APPROVED','REVISE_APPROVE':'APPROVED','REJECT':'REJECTED','CLARIFY':'CLARIFICATION_REQUIRED'}.get(latest['decision'] if latest else None,'PENDING')
        if r['clarifications'] and (not latest or r['clarifications'][-1]['version']>latest['version']):r['state']='PENDING'
        approved=next((x for x in reversed(r['decisions']) if x['plan']),None)
        r['current_plan']=approved['plan'] if approved else r['original_plan']
        r['planned_people_days']=r['current_plan']['people']*r['current_plan']['days'] if approved and r['state']=='APPROVED' else None
        r['requested_people_days']=r['people']*r['days']
        actuals=rows(u,'SELECT id FROM workers.day_actuals WHERE request_id=:id ORDER BY event_date,created_at',id=rid)
        r['actuals']=[]
        for item in actuals:
            if w:
                try:self._authorize_row(u,e,w,self._row(u,e,'day_actuals',item['id']))
                except NotFoundError:continue
            r['actuals'].append(self._actual_view(u,e,item['id'],w))
        r['actual_people_days']=sum(x['people_days'] or 0 for x in r['actuals'] if x['current'])
        r['unresolved_day_actual_ids']=[x['id'] for x in r['actuals'] if x['current'] and x['people_days'] is None]
        r['greenhouse_name']=rows(u,'SELECT name FROM core.work_areas WHERE id=:id',id=r['greenhouse_id'])[0]['name']
        people=rows(u,'SELECT display_name FROM workers.people WHERE id=:id',id=r['worker_id']) if r['worker_id'] else []
        r['worker_name']=people[0]['display_name'] if people else None
        return r

    def get_request(self,e,a,rid,w=None):
        with self.factory() as u:
            if w:self._worker(u,e,a,w)
            else:self.owner.owner(u,e,a)
            return self._request_view(u,e,rid,w)

    def get_actual(self,e,a,rid,w=None):
        with self.factory() as u:
            if w:self._worker(u,e,a,w)
            else:self.owner.owner(u,e,a)
            return self._actual_view(u,e,rid,w)

    def overview(self,e,a,w=None):
        with self.factory() as u:
            if w:self._worker(u,e,a,w)
            else:self.owner.owner(u,e,a)
            greenhouses=rows(u,"SELECT id,name FROM core.work_areas WHERE entity_id=:e AND kind='greenhouse' AND parent_id IS NULL ORDER BY name",e=e)
            if w:greenhouses=[g for g in greenhouses if u.session.scalar(text('SELECT workers.day_scope(:e,:w,:g)'),dict(e=e,w=w,g=g['id']))]
            allowed={g['id'] for g in greenhouses}
            requests=rows(u,'SELECT * FROM workers.day_requests WHERE entity_id=:e ORDER BY created_at DESC',e=e)
            requests=[r for r in requests if not w or r['worker_id']==str(w) and r['greenhouse_id'] in allowed]
            actuals=rows(u,'SELECT * FROM workers.day_actuals WHERE entity_id=:e ORDER BY event_date DESC,created_at DESC',e=e)
            request_context={r['id']:r['greenhouse_id'] for r in requests}
            selected=[]
            for r in actuals:
                if w:
                    if r['worker_id']!=str(w) or (r['greenhouse_id'] or request_context.get(r['request_id'])) not in allowed:continue
                    if any(x['greenhouse_id'] not in allowed for x in rows(u,'SELECT greenhouse_id FROM workers.day_actual_allocations WHERE actual_id=:id',id=r['id'])):continue
                selected.append(r)
            result=dict(greenhouses=greenhouses,requests=[self._request_view(u,e,r['id'],w) for r in requests],actuals=[self._actual_view(u,e,r['id'],w) for r in selected])
            if not w:
                result['references']=dict(workers=rows(u,'SELECT id,display_name,language,active FROM workers.people WHERE entity_id=:e ORDER BY display_name',e=e),counterparties=rows(u,'SELECT id,name FROM core.counterparties WHERE entity_id=:e ORDER BY name',e=e),expense_accounts=rows(u,"SELECT id,name,currency FROM finance.ledger_accounts WHERE entity_id=:e AND account_class='expense' ORDER BY name",e=e),scopes=rows(u,'SELECT * FROM workers.greenhouse_scope WHERE entity_id=:e ORDER BY created_at',e=e),work_areas=rows(u,'SELECT id,name,parent_id,kind FROM core.work_areas WHERE entity_id=:e ORDER BY name',e=e))
            return result

    def summary(self,e,a,date_from,date_to,greenhouse_id=None):
        if date_from>date_to:raise ValidationError('Invalid period')
        view=self.overview(e,a)
        if greenhouse_id and str(greenhouse_id) not in {g['id'] for g in view['greenhouses']}:raise ValidationError('Verified greenhouse required')
        buckets={g['id']:dict(greenhouse_id=g['id'],request_count=0,requested_people=0,requested_people_days=0,planned_people_days=0,reported_people_days=0,confirmed_people_days=0,request_ids=[],actual_ids=[],obligation_ids=[],current_obligation_ids=[],undated_request_ids=[],undated_attention_request_ids=[],undated_requested_people_days=0,undated_planned_people_days=0) for g in view['greenhouses']}
        unknown_plans=[];unknown_actuals=[];partial_days=[]
        for r in view['requests']:
            p=r['current_plan'];start=p['start_date']
            if not start:
                unknown_plans.append(r['id']);bucket=buckets[r['greenhouse_id']];bucket['undated_request_ids'].append(r['id']);bucket['undated_requested_people_days']+=r['requested_people_days'];bucket['undated_planned_people_days']+=r['planned_people_days'] or 0
                if r['state'] in ('PENDING','CLARIFICATION_REQUIRED','APPROVED'):bucket['undated_attention_request_ids'].append(r['id'])
                continue
            if not date_from<=date.fromisoformat(start)<=date_to:continue
            bucket=buckets[r['greenhouse_id']];bucket['request_count']+=1;bucket['request_ids'].append(r['id']);bucket['requested_people']+=r['people'];bucket['requested_people_days']+=r['requested_people_days'];bucket['planned_people_days']+=r['planned_people_days'] or 0
        for r in view['actuals']:
            if not r['current']:continue
            in_period=date_from<=date.fromisoformat(r['event_date'])<=date_to
            accrued=r.get('accrual');oid=accrued['obligation_id'] if accrued else None
            for allocation in r['allocations']:
                bucket=buckets[allocation['greenhouse_id']]
                if oid and oid not in bucket['current_obligation_ids']:bucket['current_obligation_ids'].append(oid)
                if not in_period:continue
                if r['id'] not in bucket['actual_ids']:bucket['actual_ids'].append(r['id'])
                if r['full_day']=='true':
                    bucket['reported_people_days']+=allocation['people']
                    if r['confirmation']:bucket['confirmed_people_days']+=allocation['people']
                if oid and oid not in bucket['obligation_ids']:bucket['obligation_ids'].append(oid)
            if in_period:
                if r['unallocated_people']:unknown_actuals.append(r['id'])
                if r['people']>0 and r['full_day']!='true':partial_days.append(r['id'])
        return dict(date_from=str(date_from),date_to=str(date_to),greenhouse_id=str(greenhouse_id) if greenhouse_id else None,
            greenhouses=[v for k,v in buckets.items() if not greenhouse_id or k==str(greenhouse_id)],basis='DATED_CURRENT_ACTUALS_FULL_DAY_COUNTS',
            coverage_scope='ENTITY_ALL_GREENHOUSES',unknown_plan_date_ids=unknown_plans,unallocated_actual_ids=unknown_actuals,unresolved_day_actual_ids=partial_days,
            warnings=[name for condition,name in [(unknown_plans,'PLAN_DATE_UNKNOWN'),(unknown_actuals,'ACTUAL_ALLOCATION_INCOMPLETE'),(partial_days,'FULL_DAY_UNRESOLVED')] if condition])
