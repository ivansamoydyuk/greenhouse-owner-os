from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.infrastructure.models import WorkAreaRow, AttributionRow, CaptureRow, ObligationRow, PostingRow, PostingEntryRow, LedgerAccountRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyAuditRepository
from greenhouse_owner_os.services.capture import CaptureService, CaptureInput, CaptureConfirm
from greenhouse_owner_os.domain.errors import ConflictError
from tests.integration.test_postgres_readiness import preview, setup, send

def prepare(preview):
    c,eid,cash,bank,cp,ids=setup(preview)
    area=uuid4()
    with preview[0].begin() as s:s.add(WorkAreaRow(id=area,entity_id=UUID(eid),name='İmamlı',kind='greenhouse',source='test:synthetic'))
    return c,eid,cash,bank,cp,ids,area

def ob(c,eid,cp,ids,amount='8000',cls='OPERATIONAL',direction='payable',**kwargs):
    return send(c,'/obligations',dict(entity_id=eid,counterparty_id=cp,direction=direction,original_amount=amount,currency='TRY',event_date='2026-10-04',obligation_class=cls,offset_ledger_account_id=ids['STRATEGIC_PRINCIPAL' if cls=='STRATEGIC' else 'GENERAL_EXPENSE' if direction=='payable' else 'GENERAL_REVENUE'],**kwargs))

def post(c,kind,r):return send(c,f"/{kind}/{r['id']}/post?expected_version=1",expected=200)
def reverse(c,kind,r):return send(c,f"/{kind}/{r['id']}/reverse",dict(expected_version=2,event_date='2026-10-05',reason='TEST reversal'),expected=200)
def overview(c,eid):return c.get('/v1/entities/'+eid+'/finance-overview').json()

def test_operational_attribution_payment_reversal_correction_and_economics(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    r=ob(c,eid,cp,ids,attributions=[dict(work_area_id=str(area),amount='8000')],description='TEST ремонт')
    assert overview(c,eid)['economics']==[]
    r=post(c,'obligations',r)
    view=overview(c,eid);o=view['obligations'][0]
    assert o['obligation_class']=='OPERATIONAL' and o['unknown_attribution_amount']=='0.00'
    assert view['business_balances']['TRY']['operational_payable']=='8000.00'
    assert view['economics'][0]['amount']=='8000.00'
    p=send(c,'/payments',dict(entity_id=eid,counterparty_id=cp,direction='out',amount='1000',currency='TRY',event_date='2026-10-05',money_account_id=cash,allocations=[dict(obligation_id=r['id'],amount='1000')]))
    p=post(c,'payments',p)
    assert overview(c,eid)['obligations'][0]['remaining_amount']=='7000.00'
    assert overview(c,eid)['economics']==view['economics']
    assert overview(c,eid)['obligations'][0]['attributions']==o['attributions']
    reverse(c,'payments',p);reverse(c,'obligations',r)
    assert overview(c,eid)['economics']==[]
    replacement=ob(c,eid,cp,ids,amount='9000',replacement_of_id=r['id'],attributions=[dict(work_area_id=str(area),amount='9000')])
    post(c,'obligations',replacement)
    assert overview(c,eid)['economics'][0]['amount']=='9000.00'
    assert preview[3].reconcile(entity_id=UUID(eid))['ok']

def test_split_unknown_remainder_and_database_scope_immutability(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    r=ob(c,eid,cp,ids,attributions=[dict(work_area_id=str(area),amount='3000')])
    post(c,'obligations',r)
    assert overview(c,eid)['obligations'][0]['unknown_attribution_amount']=='5000.00'
    for sql in ('UPDATE finance.obligation_attributions SET amount=1 WHERE obligation_id=:id',
                'DELETE FROM finance.obligation_attributions WHERE obligation_id=:id',
                "UPDATE finance.obligations SET obligation_class='STRATEGIC' WHERE id=:id"):
        with pytest.raises(DBAPIError),preview[0].begin() as s:s.execute(text(sql),{'id':r['id']})
    assert overview(c,eid)['obligations'][0]['attributions'][0]['amount']=='3000.00'

def test_strategic_principal_receipt_and_repayment_never_expense(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    with preview[0]() as s:ledger=s.scalar(select(__import__('greenhouse_owner_os.infrastructure.models',fromlist=['MoneyAccountRow']).MoneyAccountRow.ledger_account_id).where(__import__('greenhouse_owner_os.infrastructure.models',fromlist=['MoneyAccountRow']).MoneyAccountRow.id==UUID(cash)))
    r=send(c,'/obligations',dict(entity_id=eid,counterparty_id=cp,direction='payable',original_amount='300000',currency='TRY',event_date='2026-10-04',obligation_class='STRATEGIC',offset_ledger_account_id=str(ledger)))
    r=post(c,'obligations',r);v=overview(c,eid)
    assert v['balances']['TRY']['cash']=='300000.00'
    assert v['business_balances']['TRY']['strategic_payable']=='300000.00' and v['business_balances']['TRY']['operational_payable']=='0.00'
    p=send(c,'/payments',dict(entity_id=eid,counterparty_id=cp,direction='out',amount='20000',currency='TRY',event_date='2026-10-05',money_account_id=cash,allocations=[dict(obligation_id=r['id'],amount='20000')]))
    post(c,'payments',p)
    assert overview(c,eid)['business_balances']['TRY']['strategic_payable']=='280000.00'
    with preview[0]() as s:
        n=s.scalar(select(func.count()).select_from(PostingEntryRow).join(PostingRow,PostingRow.id==PostingEntryRow.posting_id).join(LedgerAccountRow,LedgerAccountRow.id==PostingEntryRow.ledger_account_id).where(PostingRow.entity_id==UUID(eid),LedgerAccountRow.account_class.in_(['expense','revenue'])))
        assert n==0
    bad=send(c,'/obligations',dict(entity_id=eid,counterparty_id=cp,direction='payable',original_amount='1',currency='TRY',event_date='2026-10-04',obligation_class='STRATEGIC',offset_ledger_account_id=ids['GENERAL_EXPENSE']),expected=422)
    assert bad['error']

def test_receivable_due_dates_overdue_advances_and_no_cross_currency_total(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    # Non-banana service receivable: authoritative banana settlement is deferred.
    r=ob(c,eid,cp,ids,direction='receivable',amount='65000',due_date='2026-10-04',description='TEST other service')
    post(c,'obligations',r);v=overview(c,eid)
    assert v['obligations'][0]['event_date']=='2026-10-04' and v['obligations'][0]['due_date']=='2026-10-04'
    assert v['obligations'][0]['overdue'] and v['business_balances']['TRY']['overdue_receivable']=='65000.00'
    p=send(c,'/payments',dict(entity_id=eid,counterparty_id=cp,direction='in',amount='100000',currency='TRY',event_date='2026-10-05',money_account_id=cash))
    post(c,'payments',p)
    assert overview(c,eid)['balances']['TRY']['advance_received']=='100000.00'
    assert 'total' not in overview(c,eid)['balances']

def test_harvest_depot_boundary_rejects_manual_sale(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    send(c,'/obligations',dict(entity_id=eid,counterparty_id=cp,direction='receivable',original_amount='65000',currency='TRY',event_date='2026-10-04',offset_ledger_account_id=ids['GENERAL_REVENUE'],economic_source='harvest_depot'),expected=422)
    r=send(c,f'/entities/{eid}/captures',dict(text='Горгун депо должен мне 65000 лир, обещали заплатить 15 октября'))
    assert r['route']=='HARVEST_DEPOT'
    send(c,f"/entities/{eid}/captures/{r['id']}/confirm",dict(expected_version=1,reviewed={},confirmed=True),expected=422)
    assert overview(c,eid)['balances']['TRY']['receivable']=='0.00'

def test_capture_review_confirmation_atomic_retry_no_posting_and_evidence(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    r=send(c,f'/entities/{eid}/captures',dict(text='Серкану должен 8000 лир за ремонт насоса в Имамлы',source='voice_transcript'))
    assert r['state']=='REVIEW' and r['source']=='voice_transcript'
    assert overview(c,eid)['balances']['TRY']['payable']=='0.00'
    body=dict(expected_version=1,confirmed=True,reviewed=dict(kind='obligation',direction='payable',amount='8000',currency='TRY',counterparty_id=cp,event_date='2026-10-05',obligation_class='OPERATIONAL',attributions=[dict(work_area_id=str(area),amount='8000')]))
    send(c,f"/entities/{eid}/captures/{r['id']}/confirm",dict(body,reviewed=dict(body['reviewed'],event_date='')),expected=422)
    send(c,f"/entities/{eid}/captures/{r['id']}/confirm",dict(body,reviewed=dict(body['reviewed'],principal_received='false')),expected=422)
    send(c,f"/entities/{eid}/captures/{r['id']}/confirm",dict(body,reviewed=dict(body['reviewed'],obligation_class='STRATEGIC',principal_received=True,money_account_id='invalid')),expected=422)
    with preview[0]() as s:assert s.get(CaptureRow,UUID(r['id'])).state=='REVIEW'
    a=send(c,f"/entities/{eid}/captures/{r['id']}/confirm",body,key='confirmed',expected=200)
    assert send(c,f"/entities/{eid}/captures/{r['id']}/confirm",body,key='confirmed',expected=200)==a
    assert a['posted'] is False and a['finance']['status']=='draft'
    assert overview(c,eid)['balances']['TRY']['payable']=='0.00'
    assert c.get(f"/v1/entities/{eid}/captures/{r['id']}").json()['original_text']==r['original_text']
    for sql in ("UPDATE core.capture_drafts SET original_text='changed' WHERE id=:id",'DELETE FROM core.capture_drafts WHERE id=:id'):
        with pytest.raises(DBAPIError),preview[0].begin() as s:s.execute(text(sql),dict(id=r['id']))

@pytest.mark.parametrize('same_key',[True,False])
def test_capture_concurrent_confirmation_creates_only_one_finance_draft(preview,same_key):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    service=CaptureService(preview[3].uow_factory)
    row=service.create(UUID(eid),preview[1],'capture',CaptureInput(text='Серкану должен 8000 лир'))
    body=CaptureConfirm(expected_version=1,confirmed=True,reviewed=dict(kind='obligation',direction='payable',amount='8000',currency='TRY',counterparty_id=cp,event_date='2026-10-05'))
    def call(i):
        try:return service.confirm(UUID(eid),UUID(row['id']),preview[1],'same' if same_key else str(i),body)
        except ConflictError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(call,range(2)))
    if same_key:assert results[0]==results[1]
    else:assert results.count('conflict')==1
    with preview[0]() as s:assert s.scalar(select(func.count()).select_from(ObligationRow).where(ObligationRow.entity_id==UUID(eid)))==1

def test_capture_late_failure_rolls_back_link_draft_and_audit(preview,monkeypatch):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    service=CaptureService(preview[3].uow_factory)
    r=service.create(UUID(eid),preview[1],'create',CaptureInput(text='Серкану должен 8000 лир'))
    body=CaptureConfirm(expected_version=1,confirmed=True,reviewed=dict(kind='obligation',direction='payable',amount='8000',currency='TRY',counterparty_id=cp,event_date='2026-10-05'))
    original=SqlAlchemyAuditRepository.append
    def fail(self,**kw):
        if kw['action']=='capture_confirm':raise RuntimeError('Injected link audit failure')
        return original(self,**kw)
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyAuditRepository,'append',fail)
        with pytest.raises(RuntimeError):service.confirm(UUID(eid),UUID(r['id']),preview[1],'confirm',body)
    with preview[0]() as s:
        assert s.get(CaptureRow,UUID(r['id'])).state=='REVIEW'
        assert s.scalar(select(func.count()).select_from(ObligationRow).where(ObligationRow.entity_id==UUID(eid)))==0
    assert service.confirm(UUID(eid),UUID(r['id']),preview[1],'confirm',body)['finance']['status']=='draft'

def test_upcoming_due_today_and_undated_are_separate(preview):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    today=datetime.now(ZoneInfo('Europe/Istanbul')).date()
    for amount,due in [('1',str(today)),('2',str(today+timedelta(days=1))),('3',None)]:
        r=ob(c,eid,cp,ids,amount=amount,direction='receivable',due_date=due)
        post(c,'obligations',r)
    v=overview(c,eid)
    assert all(not o['overdue'] for o in v['obligations'])
    assert v['business_balances']['TRY']['upcoming_receivable']=='3.00'
    assert v['business_balances']['TRY']['undated_receivable']=='3.00'

def test_work_area_hierarchy_and_two_explicit_splits(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    child=uuid4();other=uuid4()
    with preview[0].begin() as s:
        s.add(WorkAreaRow(id=child,entity_id=UUID(eid),name='TEST участок 1',parent_id=area,kind='work_area',source='test:synthetic'))
        s.add(WorkAreaRow(id=other,entity_id=UUID(eid),name='Özvadi',kind='greenhouse',source='test:synthetic'))
    r=ob(c,eid,cp,ids,attributions=[dict(work_area_id=str(child),amount='3000'),dict(work_area_id=str(other),amount='5000')]);post(c,'obligations',r)
    assert len(overview(c,eid)['obligations'][0]['attributions'])==2
    assert overview(c,eid)['obligations'][0]['unknown_attribution_amount']=='0.00'
    with pytest.raises(DBAPIError),preview[0].begin() as s:
        s.add(WorkAreaRow(id=uuid4(),entity_id=UUID(eid),name='TEST bad nested child',parent_id=child,kind='work_area',source='test:synthetic'))

def test_capture_duplicate_requires_explicit_review(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    path=f'/entities/{eid}/captures';text_value='Серкану должен 8000 лир'
    send(c,path,dict(text=text_value));r=send(c,path,dict(text=text_value))
    assert any('Похожее' in x for x in r['gaps'])
    body=dict(expected_version=1,confirmed=True,reviewed=dict(kind='obligation',direction='payable',amount='8000',currency='TRY',counterparty_id=cp,event_date='2026-10-05'))
    send(c,path+'/'+r['id']+'/confirm',body,expected=422)
    send(c,path+'/'+r['id']+'/confirm',dict(body,reviewed=dict(body['reviewed'],duplicate_reviewed=True)),expected=200)

def test_manual_depot_receivable_blocked_even_without_source_label(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    depot=send(c,f'/entities/{eid}/counterparties',dict(name='TEST Depot',role='depot',source='test:synthetic'))
    send(c,'/obligations',dict(entity_id=eid,counterparty_id=depot['id'],direction='receivable',original_amount='10',currency='TRY',event_date='2026-10-05',offset_ledger_account_id=ids['GENERAL_REVENUE']),expected=422)
    with pytest.raises(DBAPIError),preview[0].begin() as s:
        s.add(ObligationRow(id=uuid4(),entity_id=UUID(eid),counterparty_id=UUID(depot['id']),direction='receivable',original_amount=10,currency='TRY',event_date=date(2026,10,5),offset_ledger_account_id=UUID(ids['GENERAL_REVENUE']),status='draft',created_by=preview[1]))

def test_concurrent_attribution_cannot_exceed_original_economic_amount(preview):
    c,eid,cash,bank,cp,ids,area=prepare(preview)
    other=uuid4()
    with preview[0].begin() as s:s.add(WorkAreaRow(id=other,entity_id=UUID(eid),name='Özvadi',kind='greenhouse',source='test:synthetic'))
    r=ob(c,eid,cp,ids)
    def insert(a):
        try:
            with preview[0].begin() as s:s.add(AttributionRow(obligation_id=UUID(r['id']),work_area_id=a,amount=6000,source='test:explicit',created_by=preview[1]))
            return 'ok'
        except DBAPIError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(insert,[area,other]))
    assert sorted(results)==['ok','rejected']
    assert overview(c,eid)['obligations'][0]['unknown_attribution_amount']=='2000.00'
