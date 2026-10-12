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
    assert svc.queue(UUID(inv['e']),a)['preparing']==1 and svc.queue(UUID(inv['e']),a)['items']==[]
    svc.process(UUID(s['id']))
    assert svc.queue(UUID(inv['e']),a)['items'][0]['state']=='NEEDS_INPUT'
    assert svc.queue(UUID(inv['e']),a)['preparing']==0
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0

def test_daily_reminder_dedupe_and_exact_boundary(inv,monkeypatch):
    from datetime import timedelta
    import greenhouse_owner_os.services.owner_capture as module
    source(inv);p=queue(inv)[0];start=datetime.fromisoformat(p['first_unresolved_at'])
    sf,a,_,_,_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    monkeypatch.setattr(module,'now',lambda:start+timedelta(hours=36))
    svc.reminders(UUID(inv['e']),a)
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.owner_reminders WHERE entity_id=:e'),dict(e=inv['e']))==0
    monkeypatch.setattr(module,'now',lambda:start+timedelta(hours=36,microseconds=1))
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:svc.reminders(UUID(inv['e']),a),range(2)))
    svc.reminders(UUID(inv['e']),a)
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.owner_reminders WHERE entity_id=:e'),dict(e=inv['e']))==1
    send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{p['id']}/reject",dict(expected_version=p['version']),expected=200)
    svc.reminders(UUID(inv['e']),a)
    with sf() as db:assert db.scalar(text('SELECT state FROM core.owner_reminders WHERE entity_id=:e'),dict(e=inv['e']))=='CANCELLED'

def test_real_gorgun_photo_scoped_quality_and_season_weighting(inv):
    import base64
    from decimal import Decimal
    from tests.ocr_corpus import document
    depot=send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name='Görgün',role='depot',source='test:representative'))
    image,mime=document(0)
    s=source(inv,None,mode='photo',filename='TEST-representative.png',mime_type=mime,file_base64=base64.b64encode(image).decode())
    p=queue(inv)[0];assert p['action_type']=='DEPOT_RECEIPT'
    f=p['revision']['payload'];assert f['qualities']==['HIGHEST','FIRST','SECOND']
    assert [l['original_quality_name'] for l in f['lines']]==['Grand','Birinci','İkinci']
    assert f['alacak_value']=='3000' and p['revision']['missing']
    f['quality_reviewed']=f['arithmetic_reviewed']=True
    f['remember_quality_mapping']=True
    f['lines'][0]['count']=10
    p=revise(inv,p,f);assert p['state']=='READY'
    r=confirm(inv,p);assert r['effect']['status']=='posted'
    assert {l['normalized_quality'] for l in r['effect']['lines']}=={'HIGHEST','FIRST','SECOND'}
    assert confirm(inv,p,key='receipt-again')==r
    home=inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json();season=home['season'][0]
    assert Decimal(season['kg'])==175 and Decimal(season['average_price'])==Decimal(3000)/175
    assert Decimal(season['average_bunch_weight'])==10 and season['count_coverage_complete'] is False
    assert season['count_basis']=='CONFIRMED_DEPOT_LINES' and datetime.fromisoformat(home['as_of']).tzinfo is not None
    assert home['finance']['balances']['TRY']['cash']=='0.00'
    assert home['finance']['balances']['TRY']['receivable']=='3000.00'
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM harvest.depot_quality_mappings WHERE depot_id=:d'),dict(d=depot['id']))==3
    # The legacy manual adapter recognizes the SAME original, not a proposal UUID.
    send(inv['c'],f"/entities/{inv['e']}/harvest/settlements",dict(depot_id=depot['id'],event_date='2026-10-08',currency='TRY',lines=f['lines'],evidence=dict(source='owner_mobile:manual',file_base64=base64.b64encode(image).decode(),mime_type=mime,filename='TEST-manual-copy.png')),expected=409)
    # New capture identity of the same physical original must not create a second sale.
    source(inv,None,mode='photo',filename='TEST-copy.png',mime_type=mime,file_base64=base64.b64encode(image).decode())
    duplicate=queue(inv)[0];f=duplicate['revision']['payload'];f['quality_reviewed']=f['arithmetic_reviewed']=True
    duplicate=revise(inv,duplicate,f);assert duplicate['state']=='NEEDS_INPUT'
    confirm(inv,duplicate,expected=422)

def test_payment_adapter_exact_one_effect_and_changed_debt_stale(inv):
    source(inv,'Должен за ремонт.');p=queue(inv)[0]
    # Deterministic financial route; supply the exact reviewed party and amount.
    if p['action_type']!='PAYABLE':
        source(inv,'Должен Серкану 7 000 за ремонт.');p=next(p for p in queue(inv) if p['action_type']=='PAYABLE')
    p=revise(inv,p,dict(amount='7000',currency='TRY',event_date='2026-10-08',counterparty_id=inv['cp'],description='TEST ремонт'))
    debt=confirm(inv,p)['effect']
    source(inv,'Заплатил Серкану 5 000');p=next(p for p in queue(inv) if p['action_type']=='PAYMENT')
    assert p['state']=='NEEDS_INPUT'
    f=dict(amount='5000',currency='TRY',event_date='2026-10-08',counterparty_id=inv['cp'],money_account_id=inv['cash'],payment_purpose='debt',obligation_id=debt['id'],description='TEST ремонт')
    p=revise(inv,p,f)
    source(inv,'Оплатил Серкану 5 000');second=next(x for x in queue(inv) if x['action_type']=='PAYMENT' and x['id']!=p['id'])
    second=revise(inv,second,f)
    r=confirm(inv,p);assert confirm(inv,p,key='payment-retry')==r
    stale=confirm(inv,second,expected=409);assert stale['state']=='STALE' and stale['stale']
    assert r['effect']['status']=='posted'
    assert inv['preview'][3].reconcile(entity_id=UUID(inv['e']))['ok']

def test_supplier_siblings_remain_independent(inv):
    source(inv,'Fatura KNO3 100 kg');ps=queue(inv);pay=next(p for p in ps if p['action_type']=='PAYABLE');match=next(p for p in ps if p['action_type']=='INVOICE_MATCH')
    pay=revise(inv,pay,dict(amount='7000',counterparty_id=inv['cp'],event_date='2026-10-08',description='TEST invoice',invoice_intent='operating_expense'))
    confirm(inv,pay);assert next(p for p in queue(inv) if p['id']==match['id'])['state']==match['state']
    with inv['preview'][0]() as db:assert db.scalar(text('SELECT count(*) FROM inventory.events WHERE entity_id=:e'),dict(e=inv['e']))==0

def test_same_kind_slots_and_supplier_three_effects_are_independent(inv):
    s=source(inv,'Должен Серкану 7000 за ремонт.\nДолжен Серкану 3000 за перевозку.')
    ps=queue(inv);assert len(ps)==2 and all(p['action_type']=='PAYABLE' for p in ps)
    assert all(set(p['revision']['payload'])==set(p['revision']['provenance']) for p in ps)
    assert len({p['slot'] for p in ps})==2
    first=revise(inv,ps[0],dict(amount='7000',counterparty_id=inv['cp'],event_date='2026-10-08',description='TEST first'))
    confirm(inv,first)
    send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{ps[1]['id']}/reject",dict(expected_version=ps[1]['version']),expected=200)
    sf,a,_,_,_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf));svc.process(UUID(s['id']))
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.proposed_actions WHERE inbox_id=:s'),dict(s=s['id']))==2
    source(inv,'Fatura KNO3 100 kg\nПривезли 100 кг KNO3 в İmamlı.\nЗаплатил Серкану 5000')
    kinds={p['action_type'] for p in queue(inv)}
    assert {'PAYABLE','STOCK_RECEIPT','PAYMENT','INVOICE_MATCH'}<=kinds

def test_provider_failure_original_survives(inv):
    class Failed:
        def recognize(self,*args):raise TimeoutError('TEST provider timeout')
    sf,a,_,_,_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf),recognizer=Failed())
    import base64
    from tests.ocr_corpus import document
    image,mime=document(0);b=CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),mode='photo',filename='TEST.png',mime_type=mime,file_base64=base64.b64encode(image).decode())
    s=svc.capture(UUID(inv['e']),a,'provider-failure',b);svc.process(UUID(s['id']))
    assert svc.source(UUID(inv['e']),a,UUID(s['id']))['state']=='FAILED'
    assert svc.original(UUID(inv['e']),a,UUID(s['id']))[0]==image

def test_anonymous_direct_expense_exact_journal_and_reversal(inv):
    source(inv,'Купил бензин за 500 TRY.');p=queue(inv)[0];assert p['action_type']=='DIRECT_EXPENSE'
    p=revise(inv,p,dict(amount='500',currency='TRY',event_date='2026-10-08',money_account_id=inv['cash'],description='TEST топливо'))
    r=confirm(inv,p);assert confirm(inv,p,key='expense-retry')==r
    assert r['effect']['counterparty_id'] is None
    home=inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()
    assert home['finance']['balances']['TRY']['cash']=='-500.00' and home['finance']['balances']['TRY']['payable']=='0.00'
    assert home['finance']['advances']==[]
    reversed=send(inv['c'],f"/entities/{inv['e']}/owner/direct-expenses/{r['effect']['id']}/reverse",dict(expected_version=1,event_date='2026-10-08',reason='TEST correction'),expected=200)
    assert reversed['status']=='reversed' and inv['preview'][3].reconcile(entity_id=UUID(inv['e']))['ok']
    assert inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()['finance']['balances']['TRY']['cash']=='0.00'

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
    r=send(inv['c'],root+'/reject',dict(expected_version=d['version']),expected=200)
    assert r['state']=='REJECTED';confirm(inv,p,expected=409)
    send(inv['c'],root+'/revise',dict(expected_version=p['version'],payload=p['revision']['payload']),expected=409)
    opened=send(inv['c'],root+'/reopen',dict(expected_version=r['version']),expected=200)
    assert opened['version']==r['version']+1 and opened['first_unresolved_at']==start
    with inv['preview'][0]() as db:assert db.scalar(text('SELECT count(*) FROM core.proposal_revisions WHERE proposal_id=:p'),dict(p=p['id']))==4

def test_source_uuid_retry_and_changed_original_conflict(inv):
    body=dict(client_capture_id=str(uuid4()),captured_at='2026-10-08T12:00:00+03:00',text='Просто текст')
    path=f"/entities/{inv['e']}/owner/captures"
    a=send(inv['c'],path,body,key='source-a');b=send(inv['c'],path,body,key='source-b');assert a['id']==b['id']
    send(inv['c'],path,dict(body,text='changed'),key='source-c',expected=409)

def test_stock_receipt_unknown_cost_and_invoice_match_no_second_quantity(inv):
    saved=source(inv,'Привезли 100 кг KNO3 в İmamlı.');p=queue(inv)[0];assert p['action_type']=='STOCK_RECEIPT'
    p=revise(inv,p,dict(event_date='2026-10-08',lines=[line(inv)]));r=confirm(inv,p)
    assert r['effect']['status']=='posted' and r['effect']['payload']['lines'][0]['unit_cost'] is None
    source(inv,'Fatura KNO3 100 kg');ps=queue(inv)
    match=next(p for p in ps if p['action_type']=='INVOICE_MATCH')
    match=revise(inv,match,dict(receipt_id=r['effect']['id']));linked=confirm(inv,match)
    assert linked['effect']['valuation_state']=='UNKNOWN_UNCHANGED'
    stock=inv['c'].get('/v1'+inv['root']).json()['stock'];assert sum(__import__('decimal').Decimal(s['quantity']) for s in stock)==100
    assert any(p['action_type']=='PAYABLE' for p in queue(inv))
    # Manual path of the SAME retained original cannot receive quantity twice.
    manual=send(inv['c'],inv['root']+'/events',dict(kind='receipt',event_date='2026-10-08',lines=[line(inv)],evidence=dict(source='owner_mobile:inventory',source_document=saved['id'],verification='VERIFIED')))
    send(inv['c'],inv['root']+f"/events/{manual['id']}/post",dict(confirmed=True,expected_version=1,duplicate_reviewed=True),expected=409)
    assert confirm(inv,match,key='invoice-link-retry')==linked

def test_stock_changed_before_confirmation_stale(inv):
    source(inv,'Привезли 100 кг KNO3.');p=revise(inv,queue(inv)[0],dict(event_date='2026-10-08',lines=[line(inv)]))
    from tests.integration.test_postgres_inventory import event,post
    post(inv,event(inv,qty='20'))
    r=confirm(inv,p,expected=409);assert r['state']=='STALE'
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
