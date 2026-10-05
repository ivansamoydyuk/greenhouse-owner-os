from uuid import UUID,uuid4
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,text,func
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.infrastructure.models import WorkAreaRow,PostingRow,ObligationRow,PaymentRow
from greenhouse_owner_os.infrastructure.harvest_models import SettlementRow,ReportRow,EvidenceRow,LineRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyAuditRepository
from greenhouse_owner_os.services.harvest import HarvestService
from greenhouse_owner_os.domain.harvest import SettlementPost
from greenhouse_owner_os.domain.errors import ConflictError
from tests.integration.test_postgres_readiness import preview,setup,send

def prepare(preview):
    c,e,cash,bank,cp,ids=setup(preview)
    depot=send(c,'/entities/'+e+'/counterparties',dict(name='TEST Depot',role='depot',source='test:synthetic'))['id']
    areas={n:uuid4() for n in ('Özvadi','İmamlı','Yeşilöz')}
    with preview[0].begin() as s:
        for n,aid in areas.items():s.add(WorkAreaRow(id=aid,entity_id=UUID(e),name=n,kind='greenhouse',source='test:synthetic'))
    return c,e,cash,depot,areas

def receipt(depot,areas,**kw):
    ln=dict(original_quality_name='Grand',net_weight_kg='1000',unit_price='30',count=50,source_line='1',allocations=[dict(work_area_id=str(areas['Özvadi']),net_weight_kg='1000',amount='30000',confirmed_count=50)])
    return dict(depot_id=depot,event_date='2026-10-05',currency='TRY',due_date='2026-10-15',original_greenhouse='Poncho',lines=[ln],evidence=dict(source='test:synthetic',raw_text='TEST receipt '+str(uuid4())),**kw)
def draft(c,e,b):return send(c,'/entities/'+e+'/harvest/settlements',b)
def post(c,e,r,**kw):return send(c,'/entities/'+e+'/harvest/settlements/'+r['id']+'/post',dict(expected_version=1,confirmed=True,reviewed_issues=r['issues'],review_reason='TEST owner explicitly reviewed evidence',**kw),expected=200)
def reverse(c,e,r,expected=200):return send(c,'/entities/'+e+'/harvest/settlements/'+r['id']+'/reverse',dict(expected_version=2,event_date='2026-10-05',reason='TEST correction'),expected=expected)
def balances(c,e):return c.get('/v1/entities/'+e+'/balances').json()['balances']['TRY']
def test_credit_and_partial_collection_alacak_exact_and_original(preview):
    c,e,cash,depot,areas=prepare(preview)
    body=receipt(depot,areas,alacak_value='50000');body['lines'][0].update(net_weight_kg='1234.50',unit_price='25.75',allocations=[])
    r=draft(c,e,body)
    assert r['lines'][0]['amount']=='31788.38' and r['lines'][0]['original_quality_name']=='Grand' and r['lines'][0]['normalized_quality']=='FIRST'
    assert r['greenhouse_id']==str(areas['Özvadi']) and r['original_greenhouse']=='Poncho'
    assert balances(c,e)['receivable']=='0.00'
    r=post(c,e,r)
    assert balances(c,e)['cash']=='0.00' and balances(c,e)['receivable']=='31788.38'
    o=c.get('/v1/obligations/'+r['obligation_id']).json();assert o['due_date']=='2026-10-15' and o['economic_source']=='harvest_depot'
    p=send(c,'/payments',dict(entity_id=e,counterparty_id=depot,direction='in',amount='10000',currency='TRY',event_date='2026-10-05',money_account_id=cash,allocations=[dict(obligation_id=r['obligation_id'],amount='10000')]))
    send(c,'/payments/'+p['id']+'/post?expected_version=1',{},expected=200)
    assert balances(c,e)['receivable']=='21788.38' and balances(c,e)['cash']=='10000.00'
    assert reverse(c,e,r,422)['error']
    send(c,'/payments/'+p['id']+'/reverse',dict(expected_version=2,event_date='2026-10-05',reason='TEST'),expected=200)
    reverse(c,e,r)
    assert balances(c,e)['receivable']=='0.00' and balances(c,e)['cash']=='0.00'
    assert preview[3].reconcile(entity_id=UUID(e))['ok']

def test_independent_reports_lines_reconciliation_weighted_average(preview):
    c,e,cash,depot,areas=prepare(preview)
    report=send(c,'/entities/'+e+'/harvest/reports',dict(event_date='2026-10-05',original_greenhouse='Poncho',cut_count=55,field_weight_kg='4100',reporter='TEST worker',evidence=dict(source='worker',raw_text='TEST field '+str(uuid4()))))
    batch=send(c,'/entities/'+e+'/harvest/batches',dict(event_date='2026-10-05',label='TEST load',report_ids=[report['id']],link_evidence='TEST owner confirmed truck 1'))
    body=receipt(depot,areas,batch_ids=[batch['id']],link_evidence='TEST owner confirmed receipt for truck 1')
    body['lines']=[dict(original_quality_name=q,net_weight_kg=w,unit_price='30',count=n,source_line=str(i),allocations=[]) for i,(q,w,n) in enumerate([('Sera','1000',50),('Yerli','3000',100),('İkinci','100',5)])]
    r=draft(c,e,body);assert len(r['lines'])==3 and r['reconciliation']['field_count']==55 and r['reconciliation']['depot_count']==155
    assert 'count_discrepancy' in r['issues'] and r['reports'][0]['cut_count']==55
    assert 'weight_discrepancy' not in r['issues']
    body2=receipt(depot,areas);body2['lines']=body['lines'][:2]
    r2=post(c,e,draft(c,e,body2));assert Decimal(r2['average_weight_kg'])==Decimal(4000)/150
    view=c.get('/v1/entities/'+e+'/harvest').json()
    unknown=next(a for a in view['analytics'] if a['greenhouse_id'] is None)
    assert Decimal(unknown['average_weight_kg'])==Decimal(4000)/150
    assert unknown['sales_amount']=='120000.00'

def test_immediate_payment_dependency_reversal_correction_and_idempotency(preview):
    c,e,cash,depot,areas=prepare(preview);body=receipt(depot,areas);r=draft(c,e,body)
    payload=dict(expected_version=1,confirmed=True,cash_amount='30000',money_account_id=cash,reviewed_issues=r['issues'],review_reason='TEST owner reviewed')
    path='/entities/'+e+'/harvest/settlements/'+r['id']+'/post'
    key=str(uuid4());posted=send(c,path,payload,key=key,expected=200)
    assert send(c,path,payload,key=key,expected=200)==posted
    assert balances(c,e)['cash']=='30000.00' and balances(c,e)['receivable']=='0.00'
    assert reverse(c,e,posted,422)['error']
    send(c,'/payments/'+posted['payment_id']+'/reverse',dict(expected_version=2,event_date='2026-10-05',reason='TEST actual cash correction'),expected=200)
    reverse(c,e,posted)
    replacement=draft(c,e,dict(body,replacement_of_id=r['id']))
    assert replacement['replacement_of_id']==r['id']
    post(c,e,replacement)
    assert balances(c,e)['cash']=='0.00' and balances(c,e)['receivable']=='30000.00'
    assert preview[3].reconcile(entity_id=UUID(e))['ok']

def advance(c,e,cash,depot,amount):
    p=send(c,'/payments',dict(entity_id=e,counterparty_id=depot,direction='in',amount=amount,currency='TRY',event_date='2026-10-04',money_account_id=cash))
    return send(c,'/payments/'+p['id']+'/post?expected_version=1',{},expected=200)['advance_id']

def test_mixed_advance_cash_receivable_reverse(preview):
    c,e,cash,depot,areas=prepare(preview);aid=advance(c,e,cash,depot,'10000');r=draft(c,e,receipt(depot,areas))
    p=post(c,e,r,cash_amount='5000',money_account_id=cash,advances=[dict(advance_id=aid,amount='10000')])
    assert balances(c,e)['advance_received']=='0.00' and balances(c,e)['receivable']=='15000.00' and balances(c,e)['cash']=='15000.00'
    assert len(p['application_ids'])==1
    send(c,'/payments/'+p['payment_id']+'/reverse',dict(expected_version=2,event_date='2026-10-05',reason='TEST'),expected=200)
    reverse(c,e,p)
    assert balances(c,e)['advance_received']=='10000.00' and balances(c,e)['receivable']=='0.00' and balances(c,e)['cash']=='10000.00'
    assert preview[3].reconcile(entity_id=UUID(e))['ok']

def test_mixed_unknown_discrepancy_no_fabrication_duplicate(preview):
    c,e,cash,depot,areas=prepare(preview);body=receipt(depot,areas,mixed=True,stated_total='31000');body['lines'][0].update(original_quality_name='Kava',stated_amount='31000',allocations=[])
    r=draft(c,e,body)
    assert {'mixed_receipt','unallocated','amount_discrepancy','unknown_quality'}.issubset(r['issues'])
    assert send(c,'/entities/'+e+'/harvest/settlements/'+r['id']+'/post',dict(expected_version=1,confirmed=True),expected=422)['error']
    assert draft if send(c,'/entities/'+e+'/harvest/settlements',body,expected=409)['error'] else False
    post(c,e,r)
    view=c.get('/v1/entities/'+e+'/harvest').json()
    assert len(view['analytics'])==1 and view['analytics'][0]['greenhouse_id'] is None
    assert view['analytics'][0]['sales_amount']=='30000.00'
    assert c.get('/v1/entities/'+e+'/finance-overview').json()['economics']==[]

def test_atomic_failure_retry_and_concurrency(preview,monkeypatch):
    c,e,cash,depot,areas=prepare(preview);r=draft(c,e,receipt(depot,areas));s=HarvestService(preview[3].uow_factory)
    payload=SettlementPost(expected_version=1,confirmed=True,cash_amount='30000',money_account_id=cash,reviewed_issues=r['issues'],review_reason='TEST')
    old=SqlAlchemyAuditRepository.append
    def fail(self,**kw):
        if kw['action']=='depot_post':raise RuntimeError('late audit failure')
        return old(self,**kw)
    with monkeypatch.context() as m:
        m.setattr(SqlAlchemyAuditRepository,'append',fail)
        with pytest.raises(RuntimeError):s.post(UUID(e),preview[1],'atomic',UUID(r['id']),payload)
    assert balances(c,e)['cash']=='0.00' and s.get(UUID(e),UUID(r['id']))['status']=='draft'
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:s.post(UUID(e),preview[1],'atomic',UUID(r['id']),payload),range(2)))
    assert results[0]==results[1]
    with preview[0]() as db:assert db.scalar(select(func.count()).select_from(ObligationRow).where(ObligationRow.entity_id==UUID(e)))==1
    assert balances(c,e)['cash']=='30000.00'

@pytest.mark.parametrize('target',["UPDATE harvest.depot_settlements SET total_amount=1 WHERE id=:id","DELETE FROM harvest.depot_settlements WHERE id=:id","UPDATE harvest.settlement_lines SET net_weight_kg=1 WHERE settlement_id=:id","UPDATE finance.obligations SET status='reversed' WHERE id=(SELECT obligation_id FROM harvest.depot_settlements WHERE id=:id)"])
def test_posted_sql_immutability_and_reciprocal_ownership(preview,target):
    c,e,cash,depot,areas=prepare(preview);r=post(c,e,draft(c,e,receipt(depot,areas)))
    with pytest.raises(DBAPIError),preview[0].begin() as s:s.execute(text(target),{'id':r['id']})
    assert preview[3].reconcile(entity_id=UUID(e))['ok']

def test_different_key_concurrent_retry_has_one_effect(preview):
    c,e,cash,depot,areas=prepare(preview);r=draft(c,e,receipt(depot,areas));service=HarvestService(preview[3].uow_factory)
    body=SettlementPost(expected_version=1,confirmed=True,reviewed_issues=r['issues'],review_reason='TEST')
    def run(_):
        try:return service.post(UUID(e),preview[1],str(uuid4()),UUID(r['id']),body)
        except ConflictError:return 'conflict'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,range(2)))
    assert sum(isinstance(r,dict) for r in results)==1 and results.count('conflict')==1
    assert balances(c,e)['receivable']=='30000.00'

@pytest.mark.parametrize('alias,normal',[('Poncho','Özvadi'),('Arabik','Yeşilöz'),('Green House','İmamlı'),('GreenHouse','İmamlı'),('UNKNOWN',None)])
def test_aliases_original_and_verify(preview,alias,normal):
    c,e,cash,depot,areas=prepare(preview)
    r=send(c,'/entities/'+e+'/harvest/reports',dict(event_date='2026-10-05',original_greenhouse=alias,reporter='TEST',evidence=dict(source='test',raw_text=str(uuid4()))))
    assert r['original_greenhouse']==alias and r['greenhouse_id']==(str(areas[normal]) if normal else None)
    assert ('unknown_greenhouse' in r['issues'])==(normal is None)

def test_receipt_evidence_file_roundtrip_and_exact_duplicate(preview):
    import base64
    c,e,cash,depot,areas=prepare(preview);body=receipt(depot,areas)
    raw=b'%PDF-1.4\nTEST synthetic receipt\n%%EOF';body['evidence']=dict(source='test:upload',filename='TEST.pdf',mime_type='application/pdf',file_base64=base64.b64encode(raw).decode(),raw_text='TEST extracted text')
    r=draft(c,e,body)
    res=c.get('/v1/entities/'+e+'/harvest/evidence/'+r['evidence']['id'])
    assert res.status_code==200 and res.content==raw and r['evidence']['raw_text']=='TEST extracted text'
    import hashlib
    assert r['evidence']['fingerprint']==hashlib.sha256(raw).hexdigest()
    body['evidence']['source']='test:another-upload';body['evidence']['filename']='another.pdf'
    assert send(c,'/entities/'+e+'/harvest/settlements',body,expected=409)['error']
    assert c.get('/v1/entities/'+str(uuid4())+'/harvest/evidence/'+r['evidence']['id']).status_code==404

def test_capture_harvest_report_settlement_drafts_no_finance(preview):
    c,e,cash,depot,areas=prepare(preview)
    cap=send(c,'/entities/'+e+'/captures',dict(text='Сегодня в Озвади срезали 55 деревьев.',source='voice_transcript'))
    assert cap['route']=='HARVEST_DEPOT' and cap['extracted']['cut_count']==55
    b=dict(kind='report',event_date='2026-10-05',original_greenhouse='Озвади',cut_count=55,reporter='TEST worker')
    key=str(uuid4());r=send(c,'/entities/'+e+'/captures/'+cap['id']+'/confirm',dict(expected_version=1,confirmed=True,reviewed=b),key=key,expected=200)
    assert r['posted'] is False and r['capture']['harvest_kind']=='report' and r['harvest']['cut_count']==55
    assert balances(c,e)['receivable']=='0.00' and balances(c,e)['cash']=='0.00'
    cap2=send(c,'/entities/'+e+'/captures',dict(text='TEST Depot принял 1250 кг Sera по 32 лиры сегодня.',source='text'))
    assert cap2['extracted']['depot_id']==depot and cap2['extracted']['lines'][0]['unit_price']=='32'
    b=receipt(depot,areas);b.pop('evidence');b['lines']=cap2['extracted']['lines']
    r2=send(c,'/entities/'+e+'/captures/'+cap2['id']+'/confirm',dict(expected_version=1,confirmed=True,reviewed=dict(kind='settlement',**b)),expected=200)
    assert r2['harvest']['total_amount']=='40000.00' and r2['posted'] is False
    assert balances(c,e)['receivable']=='0.00'
    cap3=send(c,'/entities/'+e+'/captures',dict(text='По этому чеку депо должен нам 40000 лир до 15 октября.',source='text'))
    assert 'due_date' not in cap3['extracted'] and any('год' in g for g in cap3['gaps'])
    assert balances(c,e)['cash']=='0.00'

@pytest.mark.parametrize('change',[{'net_weight_kg':1234.5},{'unit_price':25.75},{'net_weight_kg':'0'},{'net_weight_kg':'1.0001'},{'unit_price':'30.001'}])
def test_float_invalid_decimal_and_precision_rejected(preview,change):
    c,e,cash,depot,areas=prepare(preview);body=receipt(depot,areas);body['lines'][0].update(change)
    r=c.post('/v1/entities/'+e+'/harvest/settlements',json=body,headers={'Idempotency-Key':str(uuid4())});assert r.status_code==422
    assert balances(c,e)['receivable']=='0.00'

def test_failed_cash_advance_rolls_back_and_scope_isolation(preview):
    c,e,cash,depot,areas=prepare(preview);r=draft(c,e,receipt(depot,areas))
    bad=post(c,e,r) # first credit sale to prove generic source reverse blocked
    assert send(c,'/obligations/'+bad['obligation_id']+'/reverse',dict(expected_version=2,event_date='2026-10-05',reason='TEST'),expected=422)['error']
    r2=draft(c,e,receipt(depot,areas))
    send(c,'/entities/'+e+'/harvest/settlements/'+r2['id']+'/post',dict(expected_version=1,confirmed=True,reviewed_issues=r2['issues'],review_reason='TEST',cash_amount='30000',money_account_id=str(uuid4())),expected=404)
    assert c.get('/v1/entities/'+e+'/harvest/settlements/'+r2['id']).json()['status']=='draft'
    assert balances(c,e)['receivable']=='30000.00' and balances(c,e)['cash']=='0.00'
    assert c.get('/v1/entities/'+str(uuid4())+'/harvest/settlements/'+r2['id']).status_code==404

def test_date_range_attribution_and_mixed_debt_no_arbitrary_split(preview):
    c,e,cash,depot,areas=prepare(preview);body=receipt(depot,areas,mixed=True)
    body['lines'][0]['allocations']=[dict(work_area_id=str(areas[n]),net_weight_kg=kg,amount=amt,confirmed_count=ct) for n,kg,amt,ct in [('Özvadi','400','12000',20),('İmamlı','600','18000',30)]]
    r=post(c,e,draft(c,e,body))
    view=c.get('/v1/entities/'+e+'/harvest').json();a={x['name']:x for x in view['analytics']}
    assert a['Özvadi']['sales_amount']=='12000.00' and a['İmamlı']['sales_amount']=='18000.00'
    assert a['Нераспределено']['receivable_outstanding']=='30000.00'
    assert a['Özvadi']['receivable_outstanding']=='0.00'
    assert c.get('/v1/entities/'+e+'/harvest?date_to=2026-10-04').json()['analytics']==[]
    assert len(c.get('/v1/entities/'+e+'/finance-overview').json()['economics'])==2

def test_depot_supplier_direct_settlement_reuses_finance(preview):
    c,e,cash,depot,areas=prepare(preview);sale=post(c,e,draft(c,e,receipt(depot,areas)))
    refs=c.get('/v1/entities/'+e+'/references').json()['references'];expense=next(r['id'] for r in refs if r.get('system_code')=='GENERAL_EXPENSE')
    supplier=send(c,'/entities/'+e+'/counterparties',dict(name='TEST supplier paid by depot',role='supplier',source='test:synthetic'))['id']
    ob=send(c,'/obligations',dict(entity_id=e,counterparty_id=supplier,direction='payable',original_amount='5000',currency='TRY',event_date='2026-10-05',offset_ledger_account_id=expense))
    send(c,'/obligations/'+ob['id']+'/post?expected_version=1',{},expected=200)
    st=send(c,'/direct-settlements',dict(entity_id=e,receivable_id=sale['obligation_id'],payable_id=ob['id'],amount='5000',currency='TRY',event_date='2026-10-05'))
    send(c,'/direct-settlements/'+st['id']+'/post?expected_version=1',{},expected=200)
    assert balances(c,e)['cash']=='0.00' and balances(c,e)['receivable']=='25000.00' and balances(c,e)['payable']=='0.00'
    reverse(c,e,sale,422)
    send(c,'/direct-settlements/'+st['id']+'/reverse',dict(expected_version=2,event_date='2026-10-05',reason='TEST'),expected=200)
    reverse(c,e,sale)
    assert preview[3].reconcile(entity_id=UUID(e))['ok']

def test_cutover_and_cross_currency_failed_post_is_atomic(preview):
    c,e,cash,depot,areas=prepare(preview);body=receipt(depot,areas);body['event_date']='2026-10-03'
    r=draft(c,e,body)
    send(c,'/entities/'+e+'/harvest/settlements/'+r['id']+'/post',dict(expected_version=1,confirmed=True,reviewed_issues=r['issues'],review_reason='TEST'),expected=422)
    assert balances(c,e)['receivable']=='0.00'
    send(c,'/entities/'+e+'/reference-bootstrap',{'currency':'USD'},expected=200)
    usd=send(c,'/entities/'+e+'/money-accounts',dict(name='TEST USD Cash',account_type='cash',currency='USD',source='test:synthetic'))['id']
    aid=advance(c,e,cash,depot,'10000');r=draft(c,e,receipt(depot,areas))
    send(c,'/entities/'+e+'/harvest/settlements/'+r['id']+'/post',dict(expected_version=1,confirmed=True,cash_amount='20000',money_account_id=usd,advances=[dict(advance_id=aid,amount='10000')],reviewed_issues=r['issues'],review_reason='TEST'),expected=422)
    assert balances(c,e)['receivable']=='0.00' and balances(c,e)['advance_received']=='10000.00'
    assert c.get('/v1/entities/'+e+'/harvest/settlements/'+r['id']).json()['obligation_id'] is None
    assert preview[3].reconcile(entity_id=UUID(e))['ok']
