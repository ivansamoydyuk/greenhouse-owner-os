"""R2 adversarial source-first and owner authority tests on real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
from datetime import datetime,timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.integration.test_postgres_readiness import preview,send,setup
from tests.integration.test_postgres_inventory import inv,line
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.domain.owner_capture import CaptureSource,ConfirmAction,ReviseAction

def source(i,phrase='Сегодня полить İmamlı.',**kw):
    return send(i['c'],f"/entities/{i['e']}/owner/captures",dict(client_capture_id=str(uuid4()),captured_at='2026-10-08T12:00:00+03:00',text=phrase,**kw))
def queue(i):return i['c'].get(f"/v1/entities/{i['e']}/owner/confirmations").json()['items']
def revise(i,p,payload):return send(i['c'],f"/entities/{i['e']}/owner/confirmations/{p['id']}/revise",dict(expected_version=p['version'],payload=payload),expected=200)
def confirm_body(p):return dict(expected_version=p['version'],preview_digest=p['revision']['preview_digest'],confirmed=True)
def confirm(i,p,key=None,expected=200):return send(i['c'],f"/entities/{i['e']}/owner/confirmations/{p['id']}/confirm",confirm_body(p),key=key,expected=expected)

def test_source_is_committed_before_preparation(inv):
    sf,a,_,_,_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    s=svc.capture(UUID(inv['e']),a,'save',CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),text='Заплатил Серкану 5 000'))
    assert s['state']=='SERVER_SAVED' and s['proposals']==[]
    assert svc.source(UUID(inv['e']),a,UUID(s['id']))['server_saved']
    svc.process(UUID(s['id']))
    assert svc.queue(UUID(inv['e']),a)['items'][0]['state']=='NEEDS_INPUT'
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0

def test_neutral_and_worker_future_route_do_not_become_finance(inv):
    source(inv,'Непонятное сообщение');p=queue(inv)[0];assert p['action_type']=='UNRESOLVED'
    source(inv,'Завтра Асыф Газипаша починит плёнку в Özvadi.');ps=queue(inv)
    assert any(p['action_type']=='WORKER_TASK' for p in ps)
    assert not any(p['action_type'] in ('PAYMENT','PAYABLE') for p in ps)

def test_personal_task_single_effect_response_loss_and_two_keys(inv):
    source(inv);p=queue(inv)[0];assert p['action_type']=='PERSONAL_TASK'
    if p['state']!='READY':p=revise(inv,p,dict(title='Полить İmamlı',greenhouse_id=inv['g'],planned_date='2026-10-08'))
    r=confirm(inv,p,key='confirm-first');assert r['state']=='POSTED'
    assert confirm(inv,p,key='confirm-first')==r
    assert confirm(inv,p,key='confirm-second')==r
    sf=inv['preview'][0]
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.owner_tasks WHERE entity_id=:e'),dict(e=inv['e']))==1
    task=r['effect'];done=send(inv['c'],f"/entities/{inv['e']}/owner/tasks/{task['id']}/state",dict(expected_version=1,state='DONE'),expected=200)
    assert done['state']=='DONE'

def test_concurrent_confirm_exactly_one(inv):
    source(inv);p=queue(inv)[0]
    if p['state']!='READY':p=revise(inv,p,dict(title='Полить',planned_date='2026-10-08'))
    sf,a,_,_,_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    def run(k):return svc.confirm(UUID(inv['e']),a,UUID(p['id']),k,ConfirmAction(**confirm_body(p)))
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,['race-1','race-2']))
    assert results[0]==results[1]
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE proposal_id=:p'),dict(p=p['id']))==1

def test_defer_reject_edit_revision_and_age_preserved(inv):
    source(inv);p=queue(inv)[0];start=p['first_unresolved_at'];root=f"/entities/{inv['e']}/owner/confirmations/{p['id']}"
    d=send(inv['c'],root+'/defer',dict(expected_version=p['version']),expected=200)
    assert d['state']=='DEFERRED' and d['first_unresolved_at']==start
    r=send(inv['c'],root+'/reject',dict(expected_version=p['version']),expected=200)
    assert r['state']=='REJECTED';confirm(inv,p,expected=409)
    opened=send(inv['c'],root+'/reopen',dict(expected_version=p['version']),expected=200)
    assert opened['version']==p['version']+1 and opened['first_unresolved_at']==start
    with inv['preview'][0]() as db:assert db.scalar(text('SELECT count(*) FROM core.proposal_revisions WHERE proposal_id=:p'),dict(p=p['id']))==2

def test_source_uuid_retry_and_changed_original_conflict(inv):
    body=dict(client_capture_id=str(uuid4()),captured_at='2026-10-08T12:00:00+03:00',text='Просто текст')
    path=f"/entities/{inv['e']}/owner/captures"
    a=send(inv['c'],path,body,key='source-a');b=send(inv['c'],path,body,key='source-b');assert a['id']==b['id']
    send(inv['c'],path,dict(body,text='changed'),key='source-c',expected=409)

def test_stock_receipt_unknown_cost_and_invoice_match_no_second_quantity(inv):
    source(inv,'Привезли 100 кг KNO3 в İmamlı.');p=queue(inv)[0];assert p['action_type']=='STOCK_RECEIPT'
    p=revise(inv,p,dict(event_date='2026-10-08',lines=[line(inv)]));r=confirm(inv,p)
    assert r['effect']['status']=='posted' and r['effect']['payload']['lines'][0]['unit_cost'] is None
    source(inv,'Fatura KNO3 100 kg');ps=queue(inv)
    match=next(p for p in ps if p['action_type']=='INVOICE_MATCH')
    match=revise(inv,match,dict(receipt_id=r['effect']['id']));linked=confirm(inv,match)
    assert linked['effect']['valuation_state']=='UNKNOWN_UNCHANGED'
    stock=inv['c'].get('/v1'+inv['root']).json()['stock'];assert sum(__import__('decimal').Decimal(s['quantity']) for s in stock)==100
    assert any(p['action_type']=='PAYABLE' for p in queue(inv))

def test_stock_changed_before_confirmation_stale(inv):
    source(inv,'Привезли 100 кг KNO3.');p=revise(inv,queue(inv)[0],dict(event_date='2026-10-08',lines=[line(inv)]))
    from tests.integration.test_postgres_inventory import event,post
    post(inv,event(inv,qty='20'))
    r=confirm(inv,p);assert r['state']=='STALE'
    with inv['preview'][0]() as db:assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE proposal_id=:p'),dict(p=p['id']))==0

def test_general_payable_no_optional_fields(inv):
    source(inv,'Должен 7000 за ремонт');p=queue(inv)[0]
    p=revise(inv,p,dict(amount='7000',currency='TRY',counterparty_id=inv['cp'],event_date='2026-10-08'))
    assert p['state']=='READY';r=confirm(inv,p)
    assert r['effect']['status']=='posted' and r['effect']['attributions']==[] and r['effect']['due_date'] is None

def test_payment_without_debt_never_becomes_advance(inv):
    source(inv,'Заплатил 5000');p=queue(inv)[0]
    p=revise(inv,p,dict(amount='5000',counterparty_id=inv['cp'],event_date='2026-10-08',money_account_id=inv['cash']))
    assert p['state']=='NEEDS_INPUT';confirm(inv,p,expected=422)
    with inv['preview'][0]() as db:assert db.scalar(text('SELECT count(*) FROM finance.advances WHERE entity_id=:e'),dict(e=inv['e']))==0

def test_new_counterparty_name_only_duplicate_and_unclassified(inv):
    path=f"/entities/{inv['e']}/owner/counterparties";body=dict(name='Новый Серкан',confirmed=True)
    a=send(inv['c'],path,body);b=send(inv['c'],path,body);assert a['id']==b['id'] and a['role']=='unclassified'

def test_unauthorized_owner_entity_and_worker_denied(inv):
    sf,a,_,_,_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    from greenhouse_owner_os.domain.errors import ValidationError
    with pytest.raises(ValidationError):svc.queue(UUID(inv['e']),uuid4())
    with pytest.raises(ValidationError):svc.queue(uuid4(),a)
    from greenhouse_owner_os.infrastructure.models import ActorRow
    worker=uuid4()
    with sf.begin() as db:db.add(ActorRow(id=worker,actor_type='worker',display_name='TEST worker'))
    with pytest.raises(ValidationError):svc.queue(UUID(inv['e']),worker)

def test_extraction_provider_failure_source_survives_and_worker_claim_once(inv):
    sf,a,_,_,_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    b=CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),text='неизвестно')
    s=svc.capture(UUID(inv['e']),a,'initial',b)
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:svc.process(UUID(s['id'])),range(2)))
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.extraction_runs WHERE inbox_id=:i'),dict(i=s['id']))==1
    assert svc.source(UUID(inv['e']),a,UUID(s['id']))['server_saved']

def test_sql_revision_and_source_append_only(inv):
    source(inv);p=queue(inv)[0]
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:db.execute(text("UPDATE core.proposal_revisions SET payload='{}' WHERE proposal_id=:p"),dict(p=p['id']))
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:db.execute(text('DELETE FROM core.proposed_actions WHERE id=:p'),dict(p=p['id']))
