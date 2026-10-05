"""INV-001..030: PostgreSQL source, physical/cost, SQL and race acceptance."""
from uuid import UUID,uuid4
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import copy
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.inventory import InventoryInput,InventoryPost,InventoryReverse
from greenhouse_owner_os.domain.errors import ValidationError,ConflictError
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork,SqlAlchemyAuditRepository
from greenhouse_owner_os.services.inventory import InventoryService
from greenhouse_owner_os.services.inbox import InboxService,InboxConfirm
from tests.integration.test_postgres_readiness import preview,send
from tests.integration.test_postgres_business_capture import prepare,overview,ob,post as finance_post

@pytest.fixture
def inv(preview):
 c,e,cash,bank,cp,ids,g=prepare(preview);root=f'/entities/{e}/inventory'
 p=send(c,root+'/products',dict(name='TEST KNO3',aliases=['калийной селитры','KNO3'],base_unit='kg',currency='TRY',source='test:verified'))
 l=send(c,root+'/locations',dict(name='TEST A',source='test:synthetic'))
 b=send(c,root+'/locations',dict(name='TEST B',work_area_id=str(g),source='test:synthetic'))
 return dict(c=c,e=e,root=root,p=p,l=l,b=b,g=str(g),preview=preview,cp=cp,ids=ids,cash=cash)

def line(i,qty='100',**kw):return dict(product_id=i['p']['id'],original_product_name='KNO3',quantity=qty,unit='kg',location_id=i['l']['id'],greenhouse_id=i['g'],**kw)
def event(i,kind='receipt',qty='100',lines=None,**kw):return send(i['c'],i['root']+'/events',dict(kind=kind,event_date='2026-10-05',lines=lines or [line(i,qty)],**kw))
def post(i,r,expected=200,key=None):return send(i['c'],i['root']+f"/events/{r['id']}/post",dict(confirmed=True,expected_version=1),key=key,expected=expected)
def rev(i,r,expected=200):return send(i['c'],i['root']+f"/events/{r['id']}/reverse",dict(event_date='2026-10-05',reason='TEST correction'),expected=expected)
def view(i):return i['c'].get('/v1'+i['root']).json()
def qty(i,loc=None):return sum((Decimal(s['quantity']) for s in view(i)['stock'] if loc is None or s['location_id']==loc),Decimal(0))

def test_receipt_draft_application_plan_and_no_finance(inv):
 i=inv;initial=overview(i['c'],i['e'])['balances'];r=event(i);assert qty(i)==0
 post(i,r);assert qty(i)==100
 a=event(i,'application','15');assert qty(i)==100
 post(i,a);assert qty(i)==85
 plan=event(i,'plan','8');post(i,plan,422);assert qty(i)==85
 applied=view(i)['applied'][0];assert applied['greenhouse_id']==i['g'] and applied['work_area_id'] is None and applied['quantity']=='15'
 assert overview(i['c'],i['e'])['balances']==initial
 assert view(i)['invariants']['ok'] and view(i)['opening_state']=='UNINITIALIZED_VERIFY'

def test_atomic_transfer_independent_locations_and_reversal(inv):
 i=inv;r=post(i,event(i));t=post(i,event(i,'transfer','20',lines=[line(i,'20',destination_id=i['b']['id'])]))
 assert qty(i,i['l']['id'])==80 and qty(i,i['b']['id'])==20 and qty(i)==100
 assert [m['quantity'] for m in t['movements']]==['-20','20']
 rev(i,r,422);rev(i,t);assert qty(i,i['l']['id'])==100 and qty(i,i['b']['id'])==0
 rev(i,r);assert qty(i)==0

def test_application_reversal_immutable_compensation_replacement_retry(inv):
 i=inv;post(i,event(i));a=event(i,'application','15',evidence=dict(source='test:source',locator='apply1',raw_text='15 kg'))
 post(i,a);r=rev(i,a);assert qty(i)==100 and [m['quantity'] for m in r['movements']]==['-15','15']
 assert event(i,'application','15',evidence=dict(source='test:source',locator='apply1',raw_text='15 kg'))['id']==a['id']
 child=event(i,'application','12',replacement_of_id=a['id'],evidence=dict(source='test:source',locator='apply1',raw_text='15 kg'))
 assert event(i,'application','12',replacement_of_id=a['id'],evidence=dict(source='test:source',locator='apply1',raw_text='15 kg'))['id']==child['id']
 post(i,child);assert qty(i)==88
 with pytest.raises(DBAPIError):
  with i['preview'][0].begin() as s:s.execute(text("UPDATE inventory.events SET payload=payload||'{\"notes\":\"changed\"}'::jsonb WHERE id=:i"),{'i':a['id']})

def test_idempotency_source_original_payload_conflicts(inv):
 i=inv;body=dict(kind='receipt',event_date='2026-10-05',lines=[line(i)],evidence=dict(source='mail',locator='r1',raw_text='100 kg'))
 r=send(i['c'],i['root']+'/events',body,key='same');assert send(i['c'],i['root']+'/events',body,key='same')['id']==r['id']
 assert send(i['c'],i['root']+'/events',body)['id']==r['id'];post(i,r);post(i,r);assert qty(i)==100
 changed=copy.deepcopy(body);changed['lines'][0]['quantity']='101'
 send(i['c'],i['root']+'/events',changed,key='same',expected=409);send(i['c'],i['root']+'/events',changed,expected=409)
 changed=copy.deepcopy(body);changed['evidence']['raw_text']='changed';send(i['c'],i['root']+'/events',changed,expected=409)

@pytest.mark.parametrize('unit',['bag','container','L'])
def test_unverified_conversion_draft_only(inv,unit):
 i=inv;r=event(i,lines=[dict(line(i,'5'),unit=unit,original_unit=unit,original_quantity='5 bags')]);assert 'conversion' in r['issues'];post(i,r,422);assert qty(i)==0
 assert r['payload']['lines'][0]['original_quantity']=='5 bags'

def test_explicit_product_package_and_density_conversion(inv):
 i=inv;send(i['c'],i['root']+f"/products/{i['p']['id']}/conversions",dict(unit='bag',factor='25',verification='TEST verified label'))
 post(i,event(i,lines=[dict(line(i,'4'),unit='bag')]));assert qty(i)==100
 send(i['c'],i['root']+f"/products/{i['p']['id']}/conversions",dict(unit='L',factor='1.25',verification='TEST verified density kg/L'))
 post(i,event(i,lines=[dict(line(i,'10'),unit='L')]));assert qty(i)==Decimal('112.5')

@pytest.mark.parametrize('bad',[1.1,'-1','0','1e3','1.0000000000001'])
def test_quantity_exact_no_float_or_rounding(inv,bad):
 i=inv;send(i['c'],i['root']+'/events',dict(kind='receipt',event_date='2026-10-05',lines=[dict(line(i),quantity=bad)]),expected=422)

def test_unknown_product_and_units_remain_review(inv):
 i=inv;r=event(i,lines=[dict(original_product_name='unknown',quantity='5',unit='bag',original_quantity='5 мешков')]);assert {'product','location'}<=set(r['issues']);post(i,r,422);assert qty(i)==0

def test_multi_unit_multi_line_atomic_rollback(inv):
 i=inv;post(i,event(i));p=send(i['c'],i['root']+'/products',dict(name='TEST Multilex',base_unit='L',source='test:synthetic'))
 second=dict(line(i,'10'),product_id=p['id'],original_product_name='Multilex',unit='L')
 r=event(i,'application',lines=[line(i,'15'),second]);post(i,r,422);assert qty(i)==100 and view(i)['movements'][-1]['quantity']=='100'
 post(i,event(i,lines=[second]));a=post(i,event(i,'application',lines=[line(i,'15'),dict(second,quantity='5')]))
 assert {m['product_id'] for m in a['movements']}=={p['id'],i['p']['id']}
 totals={s['base_unit']:s['quantity'] for s in view(i)['totals']};assert totals=={'kg':'85','L':'5'}

def test_weighted_value_transfer_unknown_pool_and_exact_restore(inv):
 i=inv;post(i,event(i,lines=[line(i,'100',unit_cost='10',currency='TRY')]))
 post(i,event(i,lines=[line(i,'100',unit_cost='20',currency='TRY')]))
 a=post(i,event(i,'application','50'));assert a['movements'][0]['known_cost']=='-750.000000000000'
 rev(i,a);assert Decimal(view(i)['stock'][0]['known_cost'])==3000
 t=post(i,event(i,'transfer','20',lines=[line(i,'20',destination_id=i['b']['id'])]));assert sum(Decimal(m['known_cost']) for m in t['movements'])==0;rev(i,t)
 post(i,event(i,'receipt','100'));assert Decimal(view(i)['stock'][0]['uncosted_quantity'])==100
 a=post(i,event(i,'application','30'));m=a['movements'][0];assert Decimal(m['known_cost'])==-300 and Decimal(m['uncosted_quantity'])==-10
 assert Decimal(view(i)['stock'][0]['quantity'])==270;rev(i,a);assert qty(i)==300
 assert any(a['type']=='uncosted_stock' for a in overview(i['c'],i['e'])['attention'])

def test_supplier_invoice_and_advance_have_no_stock_effect(inv):
 i=inv;c=i['c'];o=ob(c,i['e'],i['cp'],i['ids']);finance_post(c,'obligations',o)
 advance=send(c,'/supplier-advances',dict(entity_id=i['e'],counterparty_id=i['cp'],money_account_id=i['cash'],currency='TRY',amount='100',event_date='2026-10-05',reason='TEST'))
 assert qty(i)==0
 r=event(i,supplier_id=i['cp'],finance_obligation_id=o['id']);assert qty(i)==0;post(i,r);assert qty(i)==100


def test_consumption_race_and_concurrent_source_retry(inv):
 i=inv;post(i,event(i,qty='10'));r1=event(i,'application','8',notes='TEST distinct physical event 1');r2=event(i,'application','8',notes='TEST distinct physical event 2')
 service=InventoryService(lambda:SqlAlchemyUnitOfWork(i['preview'][0]));barrier=Barrier(2)
 def consume(r):
  barrier.wait()
  try:service.post(UUID(i['e']),i['preview'][1],str(uuid4()),UUID(r['id']),InventoryPost(confirmed=True,expected_version=1));return 'OK'
  except ValidationError:return 'FAIL'
 with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(consume,[r1,r2]))==['FAIL','OK']
 assert qty(i)==2
 barrier=Barrier(4);body=InventoryInput(kind='receipt',event_date='2026-10-05',notes='TEST distinct receipt 2',lines=[line(i,'10')],evidence=dict(source='test:race',locator='r1',raw_text='10 kg'))
 def create(_):barrier.wait();return service.create(UUID(i['e']),i['preview'][1],str(uuid4()),body)['id']
 with ThreadPoolExecutor(4) as pool:results=list(pool.map(create,range(4)))
 assert len(set(results))==1
 barrier=Barrier(4)
 def posting(_):barrier.wait();return service.post(UUID(i['e']),i['preview'][1],str(uuid4()),UUID(results[0]),InventoryPost(confirmed=True,expected_version=1))['id']
 with ThreadPoolExecutor(4) as pool:assert len(set(pool.map(posting,range(4))))==1
 assert qty(i)==12

@pytest.mark.parametrize('direct_first',[True,False])
def test_inbox_and_direct_source_one_draft_nonposting(inv,direct_first):
 i=inv;raw='Сегодня привезли 100 кг KNO3';fields=dict(kind='receipt',event_date='2026-10-05',lines=[line(i)])
 body=dict(fields,evidence=dict(source='mail',locator='delivery:1',raw_text=raw))
 if direct_first:prior=send(i['c'],i['root']+'/events',body)
 item=send(i['c'],f"/entities/{i['e']}/inbox",dict(raw_text=raw,source='mail',locator='delivery:1'))
 assert item['route']=='INVENTORY';r=send(i['c'],f"/entities/{i['e']}/inbox/{item['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=fields,reason='TEST'),expected=200)
 prior=send(i['c'],i['root']+'/events',body);assert r['inventory_id']==prior['id'] and qty(i)==0
 post(i,prior);assert qty(i)==100


def test_inbox_direct_inventory_race(inv):
 i=inv;raw='Сегодня привезли 100 кг KNO3';fields=dict(kind='receipt',event_date='2026-10-05',lines=[line(i)])
 item=send(i['c'],f"/entities/{i['e']}/inbox",dict(raw_text=raw,source='mail',locator='race:1'))
 factory=lambda:SqlAlchemyUnitOfWork(i['preview'][0]);barrier=Barrier(2)
 def run(adapter):
  barrier.wait()
  if adapter=='inbox':return InboxService(factory).confirm(UUID(i['e']),UUID(item['id']),i['preview'][1],str(uuid4()),InboxConfirm(expected_version=1,confirmed=True,reviewed=fields,reason='TEST'))['inventory_id']
  return InventoryService(factory).create(UUID(i['e']),i['preview'][1],str(uuid4()),InventoryInput(**fields,evidence=dict(source='mail',locator='race:1',raw_text=raw)))['id']
 with ThreadPoolExecutor(2) as pool:assert len(set(pool.map(run,['inbox','direct'])))==1
 assert qty(i)==0 and len(view(i)['events'])==1

@pytest.mark.parametrize('source',['text','voice_transcript'])
def test_shared_capture_alias_and_originals_draft_only(inv,source):
 i=inv;raw='Сегодня в Имамлы дали 15 кг калийной селитры и 10 литров из оранжевого бидона.'
 r=send(i['c'],f"/entities/{i['e']}/captures",dict(text=raw,source=source))
 assert r['route']=='INVENTORY';first,second=r['extracted']['lines'];assert first['product_id']==i['p']['id'] and first['unit']=='kg' and second['unit']=='L' and second['product_id'] is None
 fields=dict(kind='application',event_date='2026-10-05',lines=[line(i,'15')])
 r=send(i['c'],f"/entities/{i['e']}/captures/{r['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=fields),expected=200)
 assert r['inventory']['status']=='draft' and qty(i)==0 and r['inventory']['evidence']['raw_text']==raw


def test_cross_entity_links_and_sql_ledger_bypass(inv):
 i=inv;c=i['c'];other=send(c,'/entities',dict(name='TEST Other',currency='TRY',source='test'))['id']
 original=event(i)
 assert c.get(f"/v1/entities/{other}/inventory/events/{original['id']}").status_code==404
 send(c,f'/entities/{other}/inventory/events',dict(kind='receipt',event_date='2026-10-05',lines=[line(i)]),expected=404)
 r=post(i,original)
 for stmt in ["UPDATE inventory.movements SET quantity=999 WHERE event_id=:i", "DELETE FROM inventory.movements WHERE event_id=:i", "INSERT INTO inventory.movements(event_id,entity_id,product_id,location_id,line_no,role,quantity,uncosted_quantity,known_cost) SELECT event_id,entity_id,product_id,location_id,9,'receipt',100,0,0 FROM inventory.movements WHERE event_id=:i"]:
  with pytest.raises(DBAPIError):
   with i['preview'][0].begin() as s:s.execute(text(stmt),dict(i=r['id']))
 a=event(i,'application','101')
 with pytest.raises(DBAPIError):
  with i['preview'][0].begin() as s:s.execute(text("UPDATE inventory.events SET status='posted',version=2,posted_by=:a,posted_at=now() WHERE id=:i"),dict(a=str(i['preview'][1]),i=a['id']))
 assert qty(i)==100


def test_late_failure_rolls_back_effect_and_idempotency(inv,monkeypatch):
 i=inv;r=event(i);orig=SqlAlchemyAuditRepository.append
 def fail(self,**kw):
  if kw['action']=='inventory_post':raise RuntimeError('TEST late failure')
  return orig(self,**kw)
 with monkeypatch.context() as m:
  m.setattr(SqlAlchemyAuditRepository,'append',fail)
  with pytest.raises(RuntimeError):post(i,r)
 assert qty(i)==0;post(i,r);assert qty(i)==100


def test_review_incomplete_draft_append_only_amendment(inv):
 i=inv;raw='Привезли 5 мешков удобрения';evidence=dict(source='mail',locator='unknown:1',raw_text=raw)
 root=event(i,lines=[dict(original_product_name='удобрение',quantity='5',unit='bag')],evidence=evidence)
 child=event(i,'receipt','100',replacement_of_id=root['id'],evidence=evidence)
 post(i,root,422);post(i,child);assert qty(i)==100
 assert i['c'].get('/v1'+i['root']+'/events/'+root['id']).json()['replacement_id']==child['id']


def test_explicit_area_split_partial_unknown_allocation(inv):
 i=inv;part=str(uuid4())
 with i['preview'][0].begin() as s:s.execute(text("INSERT INTO core.work_areas(id,entity_id,name,parent_id,kind,source) VALUES(:id,:e,'TEST part',:g,'work_area','test')"),dict(id=part,e=i['e'],g=i['g']))
 post(i,event(i));a=post(i,event(i,'application',lines=[dict(line(i,'10'),work_area_id=part),line(i,'5')]))
 applied=view(i)['applied'];assert {a['work_area_id']:a['quantity'] for a in applied}=={part:'10',None:'5'}
 assert qty(i)==85


def test_cost_currency_guard_and_conversion_does_not_spread(inv):
 i=inv;p=send(i['c'],i['root']+'/products',dict(name='TEST other kg',base_unit='kg',source='test'))
 send(i['c'],i['root']+f"/products/{i['p']['id']}/conversions",dict(unit='bag',factor='25',verification='TEST label'))
 r=event(i,lines=[dict(line(i,'5'),unit='bag',product_id=p['id'])]);post(i,r,422)
 r=event(i,lines=[line(i,'10',unit_cost='2',currency='USD')]);post(i,r,422)
 assert qty(i)==0


def test_history_nonposting_sql_and_source_identity_sql(inv):
 i=inv;r=event(i,evidence=dict(source='history',locator='row1',raw_text='100 kg',verification='HISTORY'))
 post(i,r,422)
 with pytest.raises(DBAPIError):
  with i['preview'][0].begin() as s:s.execute(text("UPDATE inventory.events SET status='posted',version=2,posted_by=:a,posted_at=now() WHERE id=:i"),dict(a=str(i['preview'][1]),i=r['id']))
 with pytest.raises(DBAPIError):
  with i['preview'][0].begin() as s:s.execute(text("INSERT INTO inventory.events(id,entity_id,kind,event_date,payload,evidence,source,locator,created_by) SELECT CAST(:new AS uuid),entity_id,kind,event_date,payload,evidence||'{\"raw_text\":\"changed\"}'::jsonb,source,locator,created_by FROM inventory.events WHERE id=:i"),dict(new=str(uuid4()),i=r['id']))
 assert qty(i)==0


def test_late_event_rejected_before_revaluing_history(inv):
 i=inv;post(i,event(i))
 late=send(i['c'],i['root']+'/events',dict(kind='receipt',event_date='2026-10-04',lines=[line(i,'10')]))
 r=post(i,late,422);assert 'Поздняя операция' in r['error']['message'] and qty(i)==100


def test_large_exact_quantity_read_totals_and_provenance(inv):
 i=inv;value='999999999999999999.123456789012'
 r=event(i,lines=[line(i,value)],evidence=dict(source='test:import',locator='row1',source_file='TEST.csv',source_row='1',imported_at='2026-10-05T12:00:00Z',confidence='HIGH'))
 post(i,r);v=view(i);assert v['totals'][0]['quantity']==value
 assert v['events'][0]['evidence']['confidence']=='HIGH' and v['events'][0]['evidence']['source_row']=='1'


def test_ambiguous_equal_payload_requires_explicit_separate_event_review(inv):
 i=inv;first=event(i);second=event(i)
 assert first['id']!=second['id'] and 'duplicate' in second['issues']
 post(i,second,422);assert qty(i)==0
 send(i['c'],i['root']+f"/events/{second['id']}/post",dict(confirmed=True,expected_version=1,duplicate_reviewed=True),expected=200)
 assert qty(i)==100
