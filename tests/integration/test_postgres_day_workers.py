"""Daily crew facts: real PostgreSQL authority, source, scope and Finance effects."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import copy,json
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.domain.day_workers import RequestInput,DecisionInput,ActualInput,AccrualInput,ConfirmationInput,ScopeInput,ClarificationInput
from greenhouse_owner_os.domain.workers import WorkerInput
from greenhouse_owner_os.domain.errors import ConflictError,ValidationError,NotFoundError
from greenhouse_owner_os.domain.commands import CreatePaymentCommand,AllocationInput,ReverseCommand
from greenhouse_owner_os.services.day_workers import DayWorkersService
from greenhouse_owner_os.services.workers import WorkersService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_business_capture import prepare


def source(kind,**fields):
    return dict(schema_version=1,form_kind='day_'+kind,form_id=str(uuid4()),captured_at='2026-10-12T09:00:00+03:00',timezone='Europe/Istanbul',fields=fields)


@pytest.fixture
def day(preview):
    c,e,cash,bank,cp,ids,g=prepare(preview);sf,a,_,finance,_=preview
    factory=lambda:SqlAlchemyUnitOfWork(sf)
    svc=DayWorkersService(factory);worker=WorkersService(factory).worker(UUID(e),a,str(uuid4()),WorkerInput(display_name='TEST day worker',language='fa',source='test:day-worker'))
    w=UUID(worker['id']);wa=UUID(worker['actor_id']);g2=uuid4();part=uuid4()
    with sf.begin() as db:
        db.execute(text("INSERT INTO core.work_areas(id,entity_id,name,kind,source) VALUES(:id,:e,'TEST second greenhouse','greenhouse','test:verified')"),dict(id=g2,e=e))
        db.execute(text("INSERT INTO core.work_areas(id,entity_id,name,kind,parent_id,source) VALUES(:id,:e,'TEST known part','work_area',:g,'test:verified')"),dict(id=part,e=e,g=g))
    i=dict(svc=svc,e=UUID(e),a=a,w=w,wa=wa,g=UUID(str(g)),g2=g2,part=part,sf=sf,finance=finance,cash=UUID(cash),cp=UUID(cp),expense=UUID(ids['GENERAL_EXPENSE']),c=c)
    svc.scope(i['e'],a,str(uuid4()),ScopeInput(source=source('scope',worker_id=str(w),greenhouse_id=str(g),action='GRANT',reason='TEST explicitly verified assignment')))
    i['wc']=TestClient(create_app(finance_service=finance,actor_provider=lambda:a,worker_provider=lambda:(i['e'],w,wa)))
    return i


def request(i,**changes):
    f=dict(people='5',days='2',work='TEST remove leaves',start_date='',note='');f.update(changes)
    body=RequestInput(greenhouse_id=i['g'],source=source('request',**f))
    return i['svc'].request(i['e'],i['wa'],str(uuid4()),body,i['w']),body


def decide(i,r,kind='APPROVE',**changes):
    f={k:str(v) if v is not None else '' for k,v in r['original_plan'].items()};f.update(decision=kind,reason='TEST owner review');f.update(changes)
    b=DecisionInput(expected_version=r['version'],source=source('decision',**f))
    return i['svc'].decision(i['e'],i['a'],str(uuid4()),UUID(r['id']),b)['request']


def actual(i,people='4',day='2026-10-12',request_id=None,full_day='true',allocations=None,owner=False,**changes):
    if allocations is None:allocations=[dict(greenhouse_id=str(i['g']),work_area_id=None,people=int(people))]
    b=ActualInput(greenhouse_id=i['g'],request_id=request_id,source=source('actual',event_date=day,people=people,work='TEST remove leaves',full_day=full_day,note='',allocations=json.dumps(allocations)),**changes)
    return i['svc'].actual(i['e'],i['a'] if owner else i['wa'],str(uuid4()),b,None if owner else i['w']),b


def accrual_body(i,**changes):
    f=dict(counterparty_id=str(i['cp']),daily_rate='100',currency='TRY',expense_account_id=str(i['expense']),rate_basis='Owner confirmed rate for actual date',due_date='');f.update(changes)
    return AccrualInput(confirmed=True,source=source('accrual',**f))


def accrue(i,r,b=None):
    return i['svc'].accrual(i['e'],i['a'],str(uuid4()),UUID(r['id']),b or accrual_body(i))


def physical(i):
    with i['sf']() as db:
        return [db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),dict(e=i['e'])) for table in ('finance.obligations','finance.payments','finance.postings','workers.day_accruals')]


def test_request_plan_and_actual_4_plus_3_are_distinct_from_5_times_2(day):
    i=day;r,b=request(i);assert r['requested_people_days']==10 and r['planned_people_days'] is None
    approved=decide(i,r);assert approved['planned_people_days']==10
    x,_=actual(i,'4',request_id=r['id']);y,_=actual(i,'3','2026-10-13',request_id=r['id'])
    got=i['svc'].get_request(i['e'],i['a'],UUID(r['id']))
    assert got['actual_people_days']==7 and got['planned_people_days']==10
    assert got['original']['fields']['people']=='5' and physical(i)==[0,0,0,0]
    first=accrue(i,x);second=accrue(i,y)
    assert first['actual']['accrual']['original_amount']=='400.00' and second['actual']['accrual']['original_amount']=='300.00'
    assert physical(i)==[2,0,2,2]
    payment=i['finance'].create_payment(actor_id=i['a'],idempotency_key=str(uuid4()),command=CreatePaymentCommand(entity_id=i['e'],counterparty_id=i['cp'],money_account_id=i['cash'],direction='out',amount='200',currency='TRY',event_date=date(2026,10,13),allocations=(AllocationInput(obligation_id=UUID(first['obligation_id']),amount='200'),)))
    i['finance'].post_payment(actor_id=i['a'],idempotency_key=str(uuid4()),payment_id=UUID(payment['id']),expected_version=1)
    assert i['svc'].get_actual(i['e'],i['a'],UUID(x['id']))['accrual']['remaining_amount']=='200.00'
    assert physical(i)==[2,1,3,2]


def test_clarification_revised_approval_and_stale_version(day):
    i=day;r,b=request(i);r=decide(i,r,'CLARIFY');assert r['state']=='CLARIFICATION_REQUIRED'
    response=ClarificationInput(expected_version=r['version'],source=source('clarification',text='TEST need three days instead'))
    r=i['svc'].clarification(i['e'],i['wa'],'clarify',UUID(r['id']),response,i['w'])['request'];assert r['state']=='PENDING'
    assert r['decisions'][0]['original']['fields']['decision']=='CLARIFY'
    stale=DecisionInput(expected_version=1,source=source('decision',decision='REJECT',people='',days='',work='',start_date='',note='',reason='TEST stale'))
    with pytest.raises(ConflictError):i['svc'].decision(i['e'],i['a'],'stale',UUID(r['id']),stale)
    r=decide(i,r,'REVISE_APPROVE',people='4',days='3');assert r['planned_people_days']==12 and r['original_plan']['people']==5 and r['original_plan']['days']==2
    assert r['clarifications'][0]['original']['fields']['text']=='TEST need three days instead' and physical(i)==[0,0,0,0]


def test_unknown_rate_preserves_confirmed_actual_no_fake_plan(day):
    i=day;r,_=actual(i,owner=True)
    assert r['request_id'] is None
    confirmed=i['svc'].confirmation(i['e'],i['a'],'confirm',UUID(r['id']),ConfirmationInput(confirmed=True,source=source('confirmation',note='TEST verified work')))
    assert confirmed['actual']['state']=='CONFIRMED' and physical(i)==[0,0,0,0]
    body=accrual_body(i).model_dump(mode='json');body['source']['fields']['daily_rate']=''
    endpoint=f"/v1/entities/{i['e']}/day-workers/actuals/{r['id']}/accruals"
    assert i['c'].post(endpoint,json=body,headers={'Idempotency-Key':'missing-rate'}).status_code==422
    body['source']['fields']['daily_rate']='100';body['source']['fields']['counterparty_id']=''
    assert i['c'].post(endpoint,json=body,headers={'Idempotency-Key':'missing-creditor'}).status_code==422
    assert physical(i)==[0,0,0,0]
    assert accrue(i,r)['actual']['state']=='ACCRUED'


def test_multi_greenhouse_whole_crew_one_cost_and_known_parts(day):
    i=day;i['svc'].scope(i['e'],i['a'],'second-scope',ScopeInput(source=source('scope',worker_id=str(i['w']),greenhouse_id=str(i['g2']),action='GRANT',reason='TEST second explicit greenhouse')))
    r,_=actual(i,allocations=[dict(greenhouse_id=str(i['g']),work_area_id=str(i['part']),people=2),dict(greenhouse_id=str(i['g2']),work_area_id=None,people=2)])
    assert r['people_days']==4 and r['unallocated_people']==0
    result=accrue(i,r);assert result['actual']['accrual']['original_amount']=='400.00' and physical(i)==[1,0,1,1]
    with i['sf']() as db:
        vals=list(db.execute(text('SELECT amount FROM finance.obligation_attributions WHERE obligation_id=:id'),dict(id=result['obligation_id'])).scalars())
    assert len(vals)==2 and sum(vals)==400
    summary=i['svc'].summary(i['e'],i['a'],date(2026,10,12),date(2026,10,12))
    assert sum(g['confirmed_people_days'] for g in summary['greenhouses'])==4


@pytest.mark.parametrize('full_day',['false','unknown'])
def test_partial_unknown_days_never_convert_to_full_day_metrics_or_money(day,full_day):
    i=day;r,_=actual(i,full_day=full_day)
    assert r['people_days'] is None
    with pytest.raises(ValidationError):accrue(i,r)
    summary=i['svc'].summary(i['e'],i['a'],date(2026,10,12),date(2026,10,12))
    assert sum(g['reported_people_days'] for g in summary['greenhouses'])==0 and summary['unresolved_day_actual_ids']==[r['id']]
    assert physical(i)==[0,0,0,0]


def test_unknown_allocation_remains_unallocated_without_requester_default(day):
    i=day;r,_=actual(i,allocations=[])
    assert r['unallocated_people']==4 and r['allocations']==[]
    i['svc'].confirmation(i['e'],i['a'],'confirm-unknown',UUID(r['id']),ConfirmationInput(confirmed=True,source=source('confirmation',note='Allocation still unknown')))
    with pytest.raises(ValidationError):accrue(i,r)
    summary=i['svc'].summary(i['e'],i['a'],date(2026,10,12),date(2026,10,12))
    assert sum(g['confirmed_people_days'] for g in summary['greenhouses'])==0 and summary['unallocated_actual_ids']==[r['id']]
    assert physical(i)==[0,0,0,0]


def test_scope_all_rows_foreign_part_worker_owner_denials_and_revocation(day):
    i=day
    with pytest.raises(NotFoundError):actual(i,allocations=[dict(greenhouse_id=str(i['g2']),work_area_id=None,people=4)])
    with pytest.raises(ValidationError):actual(i,owner=True,allocations=[dict(greenhouse_id=str(i['g2']),work_area_id=str(i['part']),people=4)])
    r,b=request(i)
    i['svc'].scope(i['e'],i['a'],'revoke',ScopeInput(source=source('scope',worker_id=str(i['w']),greenhouse_id=str(i['g']),action='REVOKE',reason='TEST revoked')))
    assert i['svc'].overview(i['e'],i['wa'],i['w'])['greenhouses']==[]
    with pytest.raises(NotFoundError):i['svc'].request(i['e'],i['wa'],'retry-after-revoke',b,i['w'])
    with pytest.raises(NotFoundError):i['svc'].get_request(i['e'],i['wa'],UUID(r['id']),i['w'])
    with pytest.raises(ValidationError):i['svc'].overview(i['e'],i['wa'])
    assert physical(i)==[0,0,0,0]


def test_logical_replay_changed_source_and_concurrent_accrual(day):
    i=day;r,b=request(i)
    assert i['svc'].request(i['e'],i['wa'],'second-http-key',b,i['w'])==r
    body=b.model_dump(mode='json');body['source']['fields']['people']='6'
    with pytest.raises(ConflictError):i['svc'].request(i['e'],i['wa'],'changed',RequestInput(**body),i['w'])
    x,_=actual(i);ab=accrual_body(i)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda n:i['svc'].accrual(i['e'],i['a'],'race-'+str(n),UUID(x['id']),ab),range(2)))
    assert results[0]==results[1] and physical(i)==[1,0,1,1]
    with pytest.raises(ConflictError):accrue(i,x)
    other,_=actual(i,'3')
    with pytest.raises(ConflictError):i['svc'].accrual(i['e'],i['a'],'rebind',UUID(other['id']),ab)
    assert physical(i)==[1,0,1,1]


def test_replacements_preserve_current_totals_and_accrued_boundary(day):
    i=day;r,b=request(i);x,_=actual(i,request_id=r['id']);y,_=actual(i,'3',request_id=r['id'],replaces_id=x['id'])
    assert i['svc'].get_request(i['e'],i['a'],UUID(r['id']))['actual_people_days']==3
    with pytest.raises(ConflictError):accrue(i,x)
    accrue(i,y)
    with pytest.raises(ValidationError):actual(i,'2',request_id=r['id'],replaces_id=y['id'])
    assert physical(i)==[1,0,1,1]


def test_owner_linked_actual_does_not_hide_workers_own_request(day):
    i=day;r,_=request(i);x,_=actual(i,request_id=r['id'],owner=True)
    got=i['svc'].get_request(i['e'],i['wa'],UUID(r['id']),i['w'])
    assert got['id']==r['id'] and got['actuals']==[]
    assert i['svc'].overview(i['e'],i['wa'],i['w'])['requests'][0]['id']==r['id']


def test_reversed_finance_visible_and_cannot_reaccrue(day):
    i=day;x,_=actual(i);r=accrue(i,x)
    i['finance'].reverse_obligation(actor_id=i['a'],idempotency_key='reverse-accrual',obligation_id=UUID(r['obligation_id']),command=ReverseCommand(expected_version=2,event_date=date(2026,10,12),reason='TEST ordinary Finance reversal'))
    got=i['svc'].get_actual(i['e'],i['a'],UUID(x['id']))
    assert got['state']=='FINANCIAL_REVIEW_REQUIRED' and got['accrual']['correction_required']
    with pytest.raises(ConflictError):accrue(i,x)


def test_sources_and_rows_are_immutable_and_incomplete_source_cannot_commit(day):
    i=day;r,_=request(i);x,_=actual(i)
    for query in ("UPDATE workers.day_requests SET people=99 WHERE id=:id","DELETE FROM workers.day_requests WHERE id=:id","UPDATE workers.sources SET snapshot='{}' WHERE entity_id=:e AND source='day-workers:request' AND locator=:id"):
        with pytest.raises(DBAPIError):
            with i['sf'].begin() as db:db.execute(text(query),dict(id=r['id'],e=i['e']))
    with pytest.raises(DBAPIError):
        with i['sf'].begin() as db:db.execute(text("INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(:e,'day-workers:confirmation',:id,'day_confirmation','{}')"),dict(e=i['e'],id=str(uuid4())))
    body=ConfirmationInput(confirmed=True,source=source('confirmation',note='TEST')).model_dump(mode='json');body['actual_id']=x['id'];body.pop('confirmed')
    snapshot=dict(entity_id=str(i['e']),actor_id=str(i['a']),worker_id=None,operation='confirmation',command=body)
    with pytest.raises(DBAPIError):
        with i['sf'].begin() as db:
            db.execute(text("INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(:e,'day-workers:confirmation',:id,'day_confirmation',CAST(:s AS jsonb))"),dict(e=i['e'],id=body['source']['form_id'],s=json.dumps(snapshot)))
            db.execute(text("INSERT INTO workers.day_confirmations(id,entity_id,actual_id,source,locator,created_by) VALUES(:id,:e,:actual,'day-workers:confirmation',:loc,:a)"),dict(id=uuid4(),e=i['e'],actual=x['id'],loc=body['source']['form_id'],a=i['a']))
    assert physical(i)==[0,0,0,0]


def test_api_scoped_worker_and_owner_finance_visibility(day):
    i=day;r,_=request(i);x,_=actual(i,request_id=r['id']);accrue(i,x)
    got=i['wc'].get('/v1/worker/day-workers').json()
    assert 'references' not in got and 'accrual' not in got['actuals'][0]
    assert 'daily_rate' not in json.dumps(got) and 'counterparty_id' not in json.dumps(got)
    endpoint=f"/v1/worker/day-workers/actuals/{x['id']}/accruals"
    assert i['wc'].post(endpoint,json=accrual_body(i).model_dump(mode='json'),headers={'Idempotency-Key':'worker-post'}).status_code==404
    refs=i['svc'].overview(i['e'],i['a'])['references'];assert str(i['expense']) in {r['id'] for r in refs['expense_accounts']}


def evidence_counts(i):
    """Financial and command evidence must roll back in the same transaction."""
    with i['sf']() as db:
        result={t:db.scalar(text('SELECT count(*) FROM '+t+' WHERE entity_id=:e'),dict(e=i['e'])) for t in (
            'workers.sources','workers.day_confirmations','workers.day_accruals','finance.obligations','finance.payments','finance.postings')}
        for table in ('audit.events','audit.idempotency_keys'):
            result[table]=db.scalar(text('SELECT count(*) FROM '+table+' WHERE actor_id=:a'),dict(a=i['a']))
        for table,col,parent in (('finance.posting_entries','posting_id','finance.postings'),('finance.obligation_attributions','obligation_id','finance.obligations')):
            result[table]=db.scalar(text('SELECT count(*) FROM '+table+' c JOIN '+parent+' p ON p.id=c.'+col+' WHERE p.entity_id=:e'),dict(e=i['e']))
        return result


@pytest.mark.parametrize('phase',['after_draft','after_post'])
def test_accrual_injected_failure_rolls_back_source_confirmation_and_entire_finance(day,monkeypatch,phase):
    i=day;x,_=actual(i);before=evidence_counts(i);body=accrual_body(i)
    import greenhouse_owner_os.services.day_workers as module
    original=module.FinanceService.post_obligation
    observed=[]
    def failing_post(self,**kw):
        with self.uow_factory() as borrowed:
            observed.append(borrowed.session.scalar(text('SELECT count(*) FROM finance.obligations WHERE entity_id=:e'),dict(e=i['e'])))
        if phase=='after_post':
            original(self,**kw)
            with self.uow_factory() as borrowed:
                assert borrowed.session.scalar(text('SELECT count(*) FROM finance.postings WHERE entity_id=:e'),dict(e=i['e']))==1
        raise RuntimeError('TEST injected '+phase)
    with monkeypatch.context() as patch:
        patch.setattr(module.FinanceService,'post_obligation',failing_post)
        with pytest.raises(RuntimeError,match='TEST injected'):
            i['svc'].accrual(i['e'],i['a'],'same-recoverable-key',UUID(x['id']),body)
    assert observed==[1] and evidence_counts(i)==before
    # Safe same-identity retry after the ordinary technical failure posts exactly once.
    result=i['svc'].accrual(i['e'],i['a'],'same-recoverable-key',UUID(x['id']),body)
    assert result['actual']['state']=='ACCRUED' and physical(i)==[1,0,1,1]


def test_replacement_and_accrual_race_have_one_authoritative_winner(day):
    from threading import Barrier
    i=day;x,b=actual(i);ready=Barrier(2);replacement=b.model_dump(mode='json')
    replacement['source']['form_id']=str(uuid4());replacement['replaces_id']=x['id'];replacement=ActualInput(**replacement)
    def run(kind):
        ready.wait()
        try:
            if kind=='accrual':return kind,accrue(i,x)
            return kind,i['svc'].actual(i['e'],i['wa'],'racing-replacement',replacement,i['w'])
        except (ConflictError,ValidationError) as exc:return 'blocked',str(exc)
    with ThreadPoolExecutor(2) as pool:outcomes=list(pool.map(run,['accrual','replacement']))
    winners=[o for o in outcomes if o[0]!='blocked'];assert len(winners)==1
    read=i['svc'].get_actual(i['e'],i['a'],UUID(x['id']))
    if winners[0][0]=='accrual':assert read['current'] and read['accrual'] and physical(i)==[1,0,1,1]
    else:assert not read['current'] and read['accrual'] is None and physical(i)==[0,0,0,0]


def test_exact_explicit_25000_accrued_10000_paid_15000_remaining(day):
    i=day;x,_=actual(i,'10',owner=True)
    # 2500 is an explicit fixture rate on this source, never a default or inferred tariff.
    accrued=accrue(i,x,accrual_body(i,daily_rate='2500',rate_basis='TEST owner explicitly confirmed2500 for2026-10-12'))
    assert accrued['actual']['accrual']['original_amount']=='25000.00' and physical(i)==[1,0,1,1]
    command=CreatePaymentCommand(entity_id=i['e'],counterparty_id=i['cp'],money_account_id=i['cash'],direction='out',amount='10000',currency='TRY',event_date=date(2026,10,12),allocations=(AllocationInput(obligation_id=UUID(accrued['obligation_id']),amount='10000'),))
    payment=i['finance'].create_payment(actor_id=i['a'],idempotency_key='explicit-payment',command=command)
    i['finance'].post_payment(actor_id=i['a'],idempotency_key='explicit-payment-post',payment_id=UUID(payment['id']),expected_version=1)
    current=i['svc'].get_actual(i['e'],i['a'],UUID(x['id']))['accrual']
    assert current['original_amount']=='25000.00' and current['remaining_amount']=='15000.00'
    assert physical(i)==[1,1,2,1] and i['finance'].reconcile(entity_id=i['e'])['ok']


@pytest.mark.parametrize('full_day',['true','false','unknown'])
def test_explicit_zero_is_known_no_work_without_fake_day_or_money(day,full_day):
    i=day;r,_=request(i);x,_=actual(i,'0',request_id=r['id'],full_day=full_day,allocations=[])
    assert x['people']==0 and x['people_days']==0 and x['original']['fields']['full_day']==full_day
    c=i['svc'].confirmation(i['e'],i['a'],'zero-confirm',UUID(x['id']),ConfirmationInput(confirmed=True,source=source('confirmation',note='Nobody attended')))
    assert c['actual']['state']=='CONFIRMED' and 'FULL_DAY_UNRESOLVED' not in c['actual']['accrual_blockers']
    with pytest.raises(ValidationError,match='zero work'):accrue(i,x)
    report=i['svc'].get_request(i['e'],i['a'],UUID(r['id']));assert report['actual_people_days']==0 and report['unresolved_day_actual_ids']==[]
    summary=i['svc'].summary(i['e'],i['a'],date(2026,10,12),date(2026,10,12));assert summary['unresolved_day_actual_ids']==[]
    assert physical(i)==[0,0,0,0]


@pytest.mark.parametrize('decision',['REJECT','CLARIFY'])
def test_historical_approval_not_active_after_reject_or_clarify(day,decision):
    i=day;r,_=request(i);r=decide(i,r);assert r['planned_people_days']==10
    r=decide(i,r,decision);assert r['planned_people_days'] is None and r['decisions'][0]['plan']['people']==5
    summary=i['svc'].summary(i['e'],i['a'],date(2026,10,12),date(2026,10,12));bucket=next(g for g in summary['greenhouses'] if g['greenhouse_id']==str(i['g']))
    assert bucket['undated_planned_people_days']==0 and r['id'] in bucket['undated_request_ids']
    assert (r['id'] in bucket['undated_attention_request_ids'])==(decision=='CLARIFY')


def test_actual_logical_replay_and_changed_target_cannot_duplicate_crew(day):
    i=day;r,_=request(i);other,_=request(i,work='Independent TEST request');x,b=actual(i,request_id=r['id'])
    assert i['svc'].actual(i['e'],i['wa'],'fresh-http-identity',b,i['w'])==x
    with pytest.raises(ConflictError):i['svc'].actual(i['e'],i['wa'],'changed-link',b.model_copy(update={'request_id':UUID(other['id'])}),i['w'])
    data=b.model_dump(mode='json');data['source']['fields']['allocations']=json.dumps([])
    with pytest.raises(ConflictError):i['svc'].actual(i['e'],i['wa'],'changed-allocation',ActualInput(**data),i['w'])
    with i['sf']() as db:assert db.scalar(text('SELECT count(*) FROM workers.day_actuals WHERE entity_id=:e'),dict(e=i['e']))==1
    assert i['svc'].get_request(i['e'],i['a'],UUID(r['id']))['actual_people_days']==4


def raw_source(db,i,operation,command,worker=True):
    snapshot=dict(entity_id=str(i['e']),actor_id=str(i['wa'] if worker else i['a']),worker_id=str(i['w']) if worker else None,operation=operation,command=command)
    db.execute(text('INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(:e,:s,:loc,:event,CAST(:snapshot AS jsonb))'),dict(e=i['e'],s='day-workers:'+operation,loc=command['source']['form_id'],event='day_'+operation,snapshot=json.dumps(snapshot)))


def raw_actual(db,i,command):
    raw_source(db,i,'actual',command);f=command['source']['fields'];rid=command['source']['form_id']
    db.execute(text("INSERT INTO workers.day_actuals(id,entity_id,worker_id,greenhouse_id,request_id,replaces_id,event_date,people,work,full_day,note,source,locator,created_by) VALUES(:id,:e,:w,:g,:r,:old,:day,:people,:work,:full_day,:note,'day-workers:actual',:locator,:a)"),dict(id=rid,locator=rid,e=i['e'],w=i['w'],g=command['greenhouse_id'],r=command['request_id'],old=command['replaces_id'],day=f['event_date'],people=int(f['people']),work=f['work'],full_day=f['full_day'],note=f['note'],a=i['wa']))
    for ordinal,row in enumerate(json.loads(f['allocations'])):
        db.execute(text('INSERT INTO workers.day_actual_allocations(actual_id,ordinal,greenhouse_id,work_area_id,people) VALUES(:id,:n,:g,:part,:people)'),dict(id=rid,n=ordinal,g=row['greenhouse_id'],part=row.get('work_area_id'),people=row['people']))


@pytest.mark.parametrize('revoked_basis',['reporting_context','allocation'])
def test_sql_replacement_cannot_launder_revoked_original_scope(day,revoked_basis):
    i=day;i['svc'].scope(i['e'],i['a'],'allow-second',ScopeInput(source=source('scope',worker_id=str(i['w']),greenhouse_id=str(i['g2']),action='GRANT',reason='TEST verified second greenhouse')))
    body=ActualInput(greenhouse_id=i['g'] if revoked_basis=='reporting_context' else i['g2'],source=source('actual',event_date='2026-10-12',people='4',work='TEST old work',full_day='true',note='',allocations=json.dumps([dict(greenhouse_id=str(i['g']),work_area_id=None,people=4)])))
    old=i['svc'].actual(i['e'],i['wa'],'old-scope',body,i['w'])
    i['svc'].scope(i['e'],i['a'],'revoke-old',ScopeInput(source=source('scope',worker_id=str(i['w']),greenhouse_id=str(i['g']),action='REVOKE',reason='TEST former assignment ended')))
    replacement=body.model_dump(mode='json');replacement.update(greenhouse_id=str(i['g2']),replaces_id=old['id']);replacement['source']['form_id']=str(uuid4());replacement['source']['fields']['allocations']=json.dumps([dict(greenhouse_id=str(i['g2']),work_area_id=None,people=4)])
    with pytest.raises(NotFoundError):i['svc'].actual(i['e'],i['wa'],'service-revoked-old',ActualInput(**replacement),i['w'])
    with pytest.raises(DBAPIError,match='Original actual greenhouse scope is no longer authorized'):
        with i['sf'].begin() as db:raw_actual(db,i,replacement)
    assert i['svc'].get_actual(i['e'],i['a'],UUID(old['id']))['current'] and physical(i)==[0,0,0,0]


@pytest.mark.parametrize('lexical',['today','infinity','-infinity','20261012','2026-W42-1','2026-2-3'])
def test_sql_actual_requires_explicit_iso_calendar_evidence(day,lexical):
    i=day;command=dict(greenhouse_id=str(i['g']),request_id=None,replaces_id=None,source=source('actual',event_date=lexical,people='4',work='TEST original',full_day='true',note='',allocations=json.dumps([dict(greenhouse_id=str(i['g']),work_area_id=None,people=4)])))
    # An otherwise complete original and matching child exercise the lexical guard,
    # not merely failure of deferred source/child finality.
    with pytest.raises(DBAPIError,match='Explicit ISO calendar date YYYY-MM-DD required'):
        with i['sf'].begin() as db:raw_actual(db,i,command)
    assert physical(i)==[0,0,0,0]


@pytest.mark.parametrize('operation,date_field',[('request','start_date'),('decision','start_date'),('accrual','due_date')])
def test_sql_optional_date_sources_reject_relative_calendar_evidence(day,operation,date_field):
    i=day
    if operation=='request':command=RequestInput(greenhouse_id=i['g'],source=source('request',people='5',days='2',work='TEST',start_date='',note='')).model_dump(mode='json')
    elif operation=='decision':command=dict(expected_version=1,request_id=str(uuid4()),source=source('decision',decision='REJECT',people='',days='',work='',start_date='',note='',reason='TEST'))
    else:command=dict(accrual_body(i).model_dump(mode='json'),actual_id=str(uuid4()))
    command['source']['fields'][date_field]='today'
    with pytest.raises(DBAPIError,match='Explicit ISO calendar date YYYY-MM-DD required'):
        with i['sf'].begin() as db:raw_source(db,i,operation,command,worker=operation=='request')
