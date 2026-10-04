"""Finance R1 operational acceptance through authenticated HTTP/service boundary."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.domain.errors import ConflictError, ValidationError
from greenhouse_owner_os.domain.references import EntitySetup, CounterpartySetup, SYSTEM_ACCOUNTS
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.models import ActorRow, ReferenceRecordRow, PostingRow, LedgerAccountRow, MoneyAccountRow, EntityRow, CounterpartyRow, AuditEventRow, IdempotencyRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork, SqlAlchemyAuditRepository
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.services.references import ReferenceService, provision_authenticated_owner


@pytest.fixture
def preview(migrated_database):
    sf = make_session_factory(migrated_database)
    identity='test:'+str(uuid4())
    actor = provision_authenticated_owner(sf,external_ref=identity,display_name='TEST PREVIEW Owner',environment='TEST_PREVIEW')
    assert provision_authenticated_owner(sf,external_ref=identity,display_name='TEST PREVIEW Owner',environment='TEST_PREVIEW')==actor
    factory = lambda: SqlAlchemyUnitOfWork(sf)
    refs = ReferenceService(factory,environment='TEST_PREVIEW')
    finance = FinanceService(factory)
    client = TestClient(create_app(finance_service=finance,reference_service=refs,actor_provider=lambda:actor))
    return sf,actor,refs,finance,client


def send(client,path,body=None,key=None,expected=201):
    r=client.post('/v1'+path,json=body,headers={'Idempotency-Key':key or str(uuid4())})
    assert r.status_code==expected,r.text
    return r.json()


def setup(preview):
    sf,actor,refs,finance,c=preview
    entity=send(c,'/entities',{'name':'TEST PREVIEW Entity '+str(actor),'currency':'TRY','source':'test:synthetic'})
    eid=entity['id']; base='/entities/'+eid
    mappings=send(c,base+'/reference-bootstrap',{'currency':'TRY'},expected=200)
    assert send(c,base+'/reference-bootstrap',{'currency':'TRY'},expected=200)==mappings
    money=send(c,base+'/money-accounts',{'name':'TEST PREVIEW Cash','account_type':'cash','currency':'TRY','source':'test:synthetic'})
    bank=send(c,base+'/money-accounts',{'name':'TEST PREVIEW Bank','account_type':'bank','currency':'TRY','source':'test:synthetic'})
    cp=send(c,base+'/counterparties',{'name':'TEST PREVIEW Counterparty','role':'both','source':'test:synthetic'})
    ids={r['system_code']:r['id'] for r in mappings['references']}
    return c,eid,money['id'],bank['id'],cp['id'],ids


def test_complete_operational_api_smoke(preview):
    c,eid,cash,bank,cp,ids=setup(preview)
    base='/v1/entities/'+eid
    zero={'cash':'0.00','payable':'0.00','receivable':'0.00','advance_paid':'0.00','advance_received':'0.00'}
    def balances():
        r=c.get(base+'/balances');assert r.status_code==200,r.text
        return r.json()['balances']['TRY']
    assert balances()==zero
    cutoff=c.get(base+'/cutover').json()
    assert cutoff['cutover_date']=='2026-10-04' and cutoff['opening_policy']=='ZERO'
    assert set(cutoff['opening_balances'].values())=={'0.00'}
    assert c.get(base+'/readiness').json()['ok'] is True, c.get(base+'/readiness').json()
    common=dict(entity_id=eid,counterparty_id=cp,currency='TRY',event_date='2026-10-04')
    def obligation(direction,amount):
        r=send(c,'/obligations',dict(common,direction=direction,original_amount=amount,
            offset_ledger_account_id=ids['GENERAL_EXPENSE' if direction=='payable' else 'GENERAL_REVENUE']))
        assert r['status']=='draft'
        return send(c,f"/obligations/{r['id']}/post?expected_version={r['version']}",expected=200)
    ap=obligation('payable','100.00')
    assert balances()==dict(zero,payable='100.00')
    pay=send(c,'/payments',dict(common,money_account_id=cash,direction='out',amount='40.00',
                              allocations=[{'obligation_id':ap['id'],'amount':'40.00'}]))
    assert balances()['cash']=='0.00'
    pay=send(c,f"/payments/{pay['id']}/post?expected_version=1",expected=200)
    assert balances()==dict(zero,cash='-40.00',payable='60.00')
    assert c.get('/v1/obligations/'+ap['id']).json()['remaining_amount']=='60.00'
    assert c.get('/v1/payments/'+pay['id']).json()['status']=='posted'
    reverse={'expected_version':pay['version'],'event_date':'2026-10-04','reason':'TEST PREVIEW reversal'}
    rev=send(c,f"/payments/{pay['id']}/reverse",reverse,expected=200)
    assert rev['status']=='reversed' and balances()==dict(zero,payable='100.00')
    assert c.get('/v1/payments/'+pay['id']+'/linkage').status_code==200
    audit=c.get(base+'/resources/'+pay['id']+'/audit').json()['events']
    assert [r['action'] for r in audit]==['create','post','reverse']
    # Prove ordinary correction/replacement linkage through the same draft API.
    replacement=send(c,'/payments',dict(common,money_account_id=cash,direction='out',amount='10.00',
        replacement_of_id=pay['id'],allocations=[{'obligation_id':ap['id'],'amount':'10.00'}]))
    assert replacement['replacement_of_id']==pay['id'] and balances()['cash']=='0.00'
    funding=send(c,'/owner-fundings',dict(entity_id=eid,money_account_id=cash,amount='500.00',currency='TRY',event_date='2026-10-04',source='test:synthetic'))
    funding=send(c,f"/owner-fundings/{funding['id']}/post?expected_version=1",expected=200)
    assert balances()['cash']=='500.00'
    transfer=send(c,'/transfers',dict(entity_id=eid,source_account_id=cash,destination_account_id=bank,
        amount='100.00',currency='TRY',event_date='2026-10-04'))
    send(c,f"/transfers/{transfer['id']}/post?expected_version=1",expected=200)
    assert balances()['cash']=='500.00'
    assert c.get('/v1/transfers/'+transfer['id']).json()['status']=='posted'
    advance=send(c,'/supplier-advances',dict(common,money_account_id=cash,amount='30.00',source='test:synthetic'))
    advance=send(c,f"/supplier-advances/{advance['id']}/post?expected_version=1",expected=200)
    assert balances()['advance_paid']=='30.00'
    app=send(c,'/supplier-advance-applications',dict(common,amount='20.00',direction='paid',advance_id=advance['advance_id'],obligation_id=ap['id']))
    assert balances()['cash']=='470.00' and balances()['advance_paid']=='10.00' and balances()['payable']=='80.00'
    send(c,f"/advance-applications/{app['id']}/reverse",dict(reverse,expected_version=app['version']),expected=200)
    assert balances()['advance_paid']=='30.00' and balances()['payable']=='100.00'
    ar=obligation('receivable','25.00')
    settlement=send(c,'/direct-settlements',dict(entity_id=eid,receivable_id=ar['id'],payable_id=ap['id'],amount='25.00',currency='TRY',event_date='2026-10-04'))
    send(c,f"/direct-settlements/{settlement['id']}/post?expected_version=1",expected=200)
    assert balances()==dict(zero,cash='470.00',payable='75.00',advance_paid='30.00')
    position=c.get(base+f'/counterparties/{cp}/financial-position?currency=TRY').json()
    assert position['open_payable']=='75.00' and position['supplier_advance_remaining']=='30.00'
    assert c.get('/v1/finance/reconciliation?entity_id='+eid).json()['ok'] is True
    assert c.get(base+'/readiness').json()['ok'] is True, c.get(base+'/readiness').json()
    sf=preview[0]
    with sf() as s:
        entries=s.execute(text("SELECT p.id,sum(CASE e.side WHEN 'debit' THEN e.amount ELSE -e.amount END) FROM finance.postings p JOIN finance.posting_entries e ON e.posting_id=p.id WHERE p.entity_id=:id GROUP BY p.id"),{'id':UUID(eid)}).all()
        assert entries and all(amount==0 for _,amount in entries)
        # Funding/transfer never create P&L; only the actual payable/receivable do.
        pnl=s.execute(text("SELECT l.system_code,sum(CASE e.side WHEN 'debit' THEN e.amount ELSE -e.amount END) FROM finance.ledger_accounts l LEFT JOIN finance.posting_entries e ON e.ledger_account_id=l.id WHERE l.entity_id=:id AND l.account_class IN ('expense','revenue') GROUP BY l.system_code"),{'id':UUID(eid)}).all()
        assert dict(pnl)=={'GENERAL_EXPENSE':100,'GENERAL_REVENUE':-25,'TRANSFER_FEE':None}
    old=send(c,'/obligations',dict(common,event_date='2026-10-03',direction='payable',original_amount='1',offset_ledger_account_id=ids['GENERAL_EXPENSE']))
    r=c.post(f"/v1/obligations/{old['id']}/post?expected_version=1",headers={'Idempotency-Key':'reject-old'})
    assert r.status_code==422 and balances()['payable']=='75.00'
    assert c.get(base+'/readiness').json()['ok'] is True, c.get(base+'/readiness').json()


def test_bootstrap_setup_idempotency_and_original_name_conflict(preview):
    c,eid,cash,bank,cp,ids=setup(preview)
    base='/entities/'+eid
    response=send(c,base+'/counterparties',{'name':'TEST PREVIEW Exact Name','role':'supplier','source':'owner:verified'},key='exact')
    assert send(c,base+'/counterparties',{'name':'TEST PREVIEW Exact Name','role':'supplier','source':'owner:verified'},key='exact')==response
    assert send(c,base+'/counterparties',{'name':'TEST PREVIEW Exact Name','role':'supplier','source':'different-import'},key='exact-other')==response
    for name,role in [('test preview exact name','supplier'),('TEST PREVIEW Exact Name','customer')]:
        assert c.post('/v1'+base+'/counterparties',json={'name':name,'role':role,'source':'owner:verified'},headers={'Idempotency-Key':str(uuid4())}).status_code==409
    sf=preview[0]
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(PostingRow).where(PostingRow.entity_id==UUID(eid)))==0
        assert s.scalar(select(func.count()).select_from(LedgerAccountRow).where(LedgerAccountRow.entity_id==UUID(eid)))==len(SYSTEM_ACCOUNTS)+2


@pytest.mark.parametrize('kind',['bootstrap','counterparty'])
def test_concurrent_semantic_identity(preview,kind):
    c,eid,*_=setup(preview)
    _,actor,refs,_,_=preview
    barrier=Barrier(2)
    def run(i):
        barrier.wait(timeout=10)
        if kind=='bootstrap':
            return refs.bootstrap(entity_id=UUID(eid),currency='USD',actor_id=actor,idempotency_key='concurrent-'+str(i))
        return refs.setup(kind='counterparty',entity_id=UUID(eid),actor_id=actor,idempotency_key='concurrent-'+str(i),
                          command=CounterpartySetup(name='TEST PREVIEW Race',role='both',source='test:race'))
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(run,range(2)))
    assert result[0]==result[1]


@pytest.mark.parametrize('kind',['entity','bootstrap','counterparty','money_account'])
def test_reference_atomic_rollback(preview,monkeypatch,kind):
    c,eid,*_=setup(preview)
    sf,actor,refs,_,_=preview
    def counts():
        with sf() as s:
            return [s.scalar(select(func.count()).select_from(m)) for m in (EntityRow,CounterpartyRow,MoneyAccountRow,LedgerAccountRow,ReferenceRecordRow,AuditEventRow,IdempotencyRow,PostingRow)]
    def call():
        if kind=='bootstrap':
            return send(c,'/entities/'+eid+'/reference-bootstrap',{'currency':'EUR'},key='fail-atomic',expected=200)
        path='/entities' if kind=='entity' else '/entities/'+eid+'/'+('counterparties' if kind=='counterparty' else 'money-accounts')
        body=dict(name='TEST PREVIEW Atomic '+str(actor),source='test:atomic')
        body.update({'role':'both'} if kind=='counterparty' else {'currency':'TRY'})
        if kind=='money_account':body['account_type']='cash'
        return send(c,path,body,key='fail-atomic')
    before=counts()
    def fail(*a,**kw):raise RuntimeError('injected reference audit failure')
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyAuditRepository,'append',fail)
        with pytest.raises(RuntimeError):call()
    assert counts()==before
    assert call()


def test_environment_separation_and_database_guards(preview):
    c,eid,*_=setup(preview)
    sf,actor,refs,_,_=preview
    owner=ReferenceService(refs.uow_factory,environment='OWNER_VERIFIED')
    with pytest.raises(ValidationError,match='environment'):
        owner.bootstrap(entity_id=UUID(eid),currency='TRY',actor_id=actor,idempotency_key='wrong-env')
    with pytest.raises(DBAPIError):
        with sf.begin() as s:
            s.execute(text("UPDATE core.entities SET reference_class='OWNER_VERIFIED' WHERE id=:id"),{'id':UUID(eid)})
    with pytest.raises(DBAPIError):
        with sf.begin() as s:
            s.execute(text('DELETE FROM core.reference_records WHERE entity_id=:id'),{'id':UUID(eid)})
    # Corrupt detached reference resource through raw DB deletion: validation fails and never repairs.
    with sf.begin() as s:
        cp=s.scalar(select(CounterpartyRow).where(CounterpartyRow.entity_id==UUID(eid)))
        s.delete(cp)
    result=refs.readiness(UUID(eid))
    assert not result['ok'] and any(f['code']=='orphan_reference' for f in result['findings'])
    assert len(refs.read(UUID(eid))['references'])==12
    # Restore deliberately removed fixture resource, outside validation tooling.
    with sf.begin() as s:
        s.add(CounterpartyRow(id=cp.id,entity_id=cp.entity_id,name=cp.name,counterparty_type=cp.counterparty_type))


def test_readiness_fails_before_owner_accounts_and_system_setup(preview):
    sf,actor,refs,_,c=preview
    entity=send(c,'/entities',{'name':'TEST PREVIEW Empty '+str(actor),'currency':'TRY','source':'test:empty'})
    report=refs.readiness(UUID(entity['id']))
    assert not report['ok']
    assert {'money_account_required','counterparty_required'}.issubset({f['code'] for f in report['findings']})


def test_preview_metadata_cannot_claim_owner_verified(preview):
    c,eid,*_=setup(preview)
    sf,actor,refs,_,_=preview
    rid=uuid4()
    with pytest.raises(DBAPIError,match='environment mismatch'):
        with sf.begin() as s:
            s.add(CounterpartyRow(id=rid,entity_id=UUID(eid),name='TEST PREVIEW Forged',counterparty_type='supplier'))
            s.flush()
            s.add(ReferenceRecordRow(id=rid,entity_id=UUID(eid),kind='counterparty',canonical_key='forged',
                reference_class='OWNER_VERIFIED',original_name='TEST PREVIEW Forged',source='test:forged',
                definition={'role':'supplier'},created_by=actor))
    assert refs.readiness(UUID(eid))['ok']


def test_readiness_reports_inactive_mapping_without_repair(preview):
    c,eid,*_=setup(preview)
    sf,actor,refs,_,_=preview
    with sf.begin() as s:
        ledger=s.scalar(select(LedgerAccountRow).where(LedgerAccountRow.entity_id==UUID(eid),LedgerAccountRow.system_code=='AP_CONTROL'))
        ledger.active=False
    report=refs.readiness(UUID(eid))
    assert not report['ok'] and any(f['code']=='system_mapping:TRY/AP_CONTROL' for f in report['findings'])
    with sf() as s:
        assert s.get(LedgerAccountRow,ledger.id).active is False
    # Fixture cleanup is explicit; readiness never repairs.
    with sf.begin() as s:s.get(LedgerAccountRow,ledger.id).active=True
