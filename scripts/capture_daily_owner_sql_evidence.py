"""Capture Daily A/C: isolated TEST fixture + independent read-only SQL evidence.

--prepare creates exact synthetic references and one isolated pre-existing T03
repair payable using private owner APIs, before the business snapshot.
All other commands use a guarded REPEATABLE READ READ ONLY transaction. They do
not execute a browser, alter application state, recognize files or repair data.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import secrets
import traceback
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.services.reconciliation import reconcile

parser=argparse.ArgumentParser()
parser.add_argument('--prepare',action='store_true');parser.add_argument('--credentials')
parser.add_argument('--before');parser.add_argument('--probe',action='store_true')
parser.add_argument('--verify');parser.add_argument('--baseline');parser.add_argument('--output')
a=parser.parse_args();assert sum(bool(x) for x in (a.prepare,a.before,a.probe,a.verify))==1
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test')
assert os.environ.get('APP_ENV')=='test' and os.environ.get('R3_DAILY_PILOT')=='1' and os.environ.get('PILOT_LOCAL_TEST')=='1'
sha=os.environ['OWNER_PREVIEW_FEATURE_SHA'];sf=make_session_factory(url)
corpus_path=Path('tests/fixtures/capture_daily_benchmark.json');corpus=json.loads(corpus_path.read_text())
case=lambda key:next(c for c in corpus['cases'] if c['id']==key)
TABLES=('finance.obligations','finance.payments','finance.direct_expenses','finance.postings','finance.advances','finance.owner_fundings','finance.advance_applications','finance.direct_settlements','finance.own_account_transfers','inventory.events','inventory.movements','inventory.stock','inventory.products','harvest.worker_reports','harvest.depot_settlements','core.invoice_receipt_links','core.confirmation_effects')
UNCHANGED=('finance.advances','finance.owner_fundings','finance.advance_applications','finance.direct_settlements','finance.own_account_transfers','inventory.products','harvest.worker_reports','harvest.depot_settlements')
def normalized(obj):return json.loads(json.dumps(obj,sort_keys=True,default=str))
def write(filename,value):
 p=Path(filename);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(normalized(value),ensure_ascii=False,indent=2)+'\n')
def rows(db,query,**params):return [dict(r) for r in db.execute(text(query),params).mappings()]
def ro(db):
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
 entities=db.execute(text('SELECT id FROM core.entities')).scalars().all();assert len(entities)==1;return entities[0]
def snapshot(db,e):
 def ordered(values):return sorted(normalized(values),key=lambda r:json.dumps(r,sort_keys=True))
 tables={t:ordered(rows(db,'SELECT * FROM '+t+' WHERE entity_id=:e',e=e)) for t in TABLES}
 tables['finance.posting_entries']=ordered(rows(db,'SELECT en.* FROM finance.posting_entries en JOIN finance.postings p ON p.id=en.posting_id WHERE p.entity_id=:e',e=e))
 tables['finance.payment_allocations']=ordered(rows(db,'SELECT a.* FROM finance.payment_allocations a JOIN finance.payments p ON p.id=a.payment_id WHERE p.entity_id=:e',e=e))
 tables['finance.obligation_balances']=ordered(rows(db,'SELECT * FROM finance.obligation_balances WHERE entity_id=:e',e=e))
 tables['cash']=ordered(rows(db,'SELECT b.* FROM finance.money_account_balances b JOIN finance.money_accounts a ON a.id=b.money_account_id WHERE a.entity_id=:e',e=e))
 return dict(sha=sha,entity_id=str(e),tables=tables)

if a.prepare:
 from fastapi.testclient import TestClient
 from greenhouse_owner_os.preview import build_preview
 private=Path(a.credentials).resolve();assert private.is_file() and 'artifacts' not in private.parts and not private.is_relative_to(Path.cwd().resolve())
 owner=json.loads(private.read_text())['owner'];app=build_preview(url,os.environ.get('PREVIEW_SECRET') or secrets.token_urlsafe(32),environment='test')
 with sf() as db:e=ro(db)
 with TestClient(app) as client:
  headers={'Origin':'http://testserver'}
  response=client.post('/pilot/login',json={k:owner[k] for k in ('username','password')},headers=headers);assert response.status_code==200,'TEST owner login failed'
  headers['X-Pilot-Participant']=owner['participant_id']
  refs={}
  for key,name in [('supplier',case('P02')['input']['document']['supplier']),('serkan','Серкан')]:
   response=client.post('/v1/entities/'+str(e)+'/counterparties',json=dict(name=name,role='supplier',source='test:capture-daily-owner:frozen'),headers=headers|{'Idempotency-Key':'test:capture-daily-owner:'+key+':v1'})
   assert response.status_code in (200,201),response.text;refs[key+'_id']=response.json()['id']
  response=client.get('/preview/config');assert response.status_code==200;config=response.json();fixture=config['synthetic_fixture']
  assert config['daily_pilot'] and config['feature_sha']==sha
  cash=next(x for x in fixture['money_accounts'] if x['account_type']=='cash')
  engine=os.environ.get('CAPTURE_DAILY_ENGINE');assert engine in ('chromium','webkit'), 'T03 requires one isolated engine per TEST database'
  def post(endpoint,body,key):
   response=client.post(endpoint,json=body,headers=headers|{'Idempotency-Key':'test:capture-daily-owner:T03:'+engine+':'+key})
   assert response.status_code in (200,201),response.text
   return response.json()
  ledger=post('/v1/entities/'+str(e)+'/reference-bootstrap',dict(currency='TRY'),'references')
  expense=next(r['id'] for r in ledger['references'] if r.get('system_code')=='GENERAL_EXPENSE')
  debt=post('/v1/obligations',dict(entity_id=str(e),counterparty_id=refs['serkan_id'],direction='payable',original_amount='5000.00',currency='TRY',event_date='2026-10-10',offset_ledger_account_id=expense,description='TEST T03 '+engine+' existing repair payable'),'existing-repair')
  # Private owner writes require explicit JSON even when the endpoint has no body fields.
  debt=post('/v1/obligations/'+debt['id']+'/post?expected_version=1',{},'post-existing-repair')
  assert debt['status']=='posted'
  refs['t03_obligation_id']=debt['id']
 with sf() as db:
  ro(db)
  sole=rows(db,"SELECT * FROM finance.obligation_balances WHERE entity_id=:e AND counterparty_id=:p AND direction='payable' AND currency='TRY' AND remaining_amount>0",e=e,p=UUID(refs['serkan_id']))
  assert len(sole)==1 and str(sole[0]['obligation_id'])==debt['id'] and sole[0]['remaining_amount']==Decimal('5000'), 'T03 must begin with one exact pre-existing repair payable'

 write(a.output,dict(sha=sha,entity_id=str(e),cash_id=cash['id'],product_id=fixture['product']['id'],location_id=fixture['location']['id'],synthetic_only=True,financial_fixture='One posted 5000 TRY T03 repair payable; captured before --before, no payment',engine=engine,**refs));print('CAPTURE_DAILY_OWNER_REFERENCES_READY');raise SystemExit
if a.before or a.probe:
 with sf() as db:
  e=ro(db);value=snapshot(db,e)
  if a.before:value['source_ids']=[str(v['id']) for v in rows(db,"SELECT id FROM core.inbox_items WHERE entity_id=:e AND source='owner:r2'",e=e)]
 write(a.before or a.output,value);print('CAPTURE_DAILY_OWNER_READ_ONLY_SNAPSHOT_READY');raise SystemExit

result=json.loads(Path(a.verify).read_text());before=json.loads(Path(a.baseline).read_text())
assert result['sha']==sha==before['sha'];assert result['benchmark_sha256']==hashlib.sha256(corpus_path.read_bytes()).hexdigest()
mode=result['mode'];expected='BASELINE_RECORDED' if mode=='baseline' else 'PASS'
assert result['status']==expected and not result['errors'];assert result['records']
engines=[r['engine'] for r in result['records']];assert len(engines)==len(set(engines))
expected_engine=os.environ.get('CAPTURE_DAILY_ENGINE')
assert set(engines)==({expected_engine} if expected_engine else {'chromium','webkit'}), 'Missing, duplicate or misrouted browser engine evidence'
setup=json.loads((Path(a.verify).parent/'capture-daily-owner-setup.json').read_text());assert setup['sha']==sha
assert len(engines)==1 and engines[0]==setup['engine'], 'T03 fixture belongs to one isolated engine'
proof=dict(sha=sha,mode=mode,status='RUNNING',cases=[])
try:
 with sf() as db:
  e=ro(db);assert str(e)==before['entity_id']==setup['entity_id']
  owned={'finance.payments':set(),'finance.payment_allocations':set(),'finance.direct_expenses':set(),'finance.obligations':set(),'inventory.events':set(),'inventory.movements':set(),'finance.postings':set(),'core.invoice_receipt_links':set(),'core.confirmation_effects':set()}
  expected_sources=set();expected_posting_total=Decimal(0)
  def one(query,**params):
   found=rows(db,query,e=e,**params);assert len(found)==1,(query,len(found));return found[0]
  def source(c):
   src=one('SELECT * FROM core.inbox_items WHERE entity_id=:e AND id=:id',id=UUID(c['source_id']))
   assert src['source']=='owner:r2';expected_sources.add(str(src['id']))
   original=case(c['id'])['input']
   if isinstance(original,str):
    assert src['raw_text']==original and src['file_bytes'] is None,'Source text changed'
    digest=hashlib.sha256(original.encode()).hexdigest()
   else:
    frozen=Path(c['asset_path']).read_bytes();assert src['file_bytes']==frozen and src['raw_text'] is None,'File original or separate caption changed'
    digest=hashlib.sha256(frozen).hexdigest();assert digest==c['source_sha256']
   assert digest==c['original_readback_sha256'],'Original HTTP readback differs'
   return src
  def posted(c,kind,amount):
   p=one('SELECT * FROM core.proposed_actions WHERE entity_id=:e AND id=:id',id=UUID(c['proposal_id']))
   assert p['inbox_id']==UUID(c['source_id']) and p['action_type']==kind and p['state']=='POSTED'
   effect=one('SELECT * FROM core.confirmation_effects WHERE entity_id=:e AND proposal_id=:id',id=p['id'])
   assert db.scalar(text("SELECT count(*) FROM core.proposal_actions WHERE entity_id=:e AND proposal_id=:id AND action='CONFIRM'"),dict(e=e,id=p['id']))==1
   table={'DIRECT_EXPENSE':'finance.direct_expenses','PAYABLE':'finance.obligations','PAYMENT':'finance.payments'}[kind];identifier=c[{'DIRECT_EXPENSE':'direct_expense_id','PAYABLE':'obligation_id','PAYMENT':'payment_id'}[kind]]
   domain=one('SELECT * FROM '+table+' WHERE entity_id=:e AND id=:id',id=UUID(identifier))
   assert domain['status']=='posted' and domain['currency']=='TRY'
   assert domain['original_amount' if kind=='PAYABLE' else 'amount']==Decimal(amount)
   assert effect[{'DIRECT_EXPENSE':'direct_expense_id','PAYABLE':'obligation_id','PAYMENT':'payment_id'}[kind]]==domain['id']
   posting=one('SELECT * FROM finance.postings WHERE entity_id=:e AND id=:id',id=domain['posting_id'])
   entries=rows(db,'SELECT side,sum(amount) AS amount FROM finance.posting_entries WHERE posting_id=:p GROUP BY side',p=posting['id'])
   assert {v['side']:v['amount'] for v in entries}=={'debit':Decimal(amount),'credit':Decimal(amount)}
   owned[table].add(str(domain['id']));owned['finance.postings'].add(str(posting['id']));owned['core.confirmation_effects'].add(str(p['id']))
   return p,domain
  for engine in result['records']:
   assert engine['status']=='COMPLETE' and engine['entity_id']==str(e)
   c={c['id']:c for c in engine['cases']};assert set(c)=={'T01','T03','T04','P01','P02','SECURITY'}
   assert all(v['status']==expected for v in c.values())
   paid=c['T01'];src=source(paid);p,expense=posted(paid,'DIRECT_EXPENSE','650')
   assert expense['money_account_id']==UUID(setup['cash_id']) and expense['counterparty_id'] is None
   assert 'топлив' in expense['description'].lower()
   assert src['locator']==paid['client_capture_id']
   assert db.scalar(text('SELECT count(*) FROM core.inbox_items WHERE entity_id=:e AND locator=:l'),dict(e=e,l=paid['client_capture_id']))==1
   assert paid['lost_source_response'] and paid['receipt_lookups']>0 and paid['draft_reload_preserved'] and paid['lost_confirm_response']
   assert paid['before_capture']==paid['before_confirm']
   assert paid['after_retry']==paid['after_lost_response']
   if mode=='candidate':assert paid['metrics']['repeated_known_facts']==0
   proof['cases'].append(dict(engine=engine['engine'],id='T01',source_id=paid['source_id'],direct_expense_id=str(expense['id']),amount='650.00',one_source=True,one_expense=True,cash_delta='-650.00',metrics=paid['metrics']))
   settlement=c['T03'];original=source(settlement);p,payment=posted(settlement,'PAYMENT','2000')
   assert payment['direction']=='out' and payment['counterparty_id']==UUID(setup['serkan_id']) and payment['money_account_id']==UUID(setup['cash_id'])
   ledger=rows(db,'SELECT en.*,la.system_code FROM finance.posting_entries en JOIN finance.ledger_accounts la ON la.id=en.ledger_account_id WHERE en.posting_id=:p',p=payment['posting_id'])
   cash_ledger=one('SELECT * FROM finance.money_accounts WHERE entity_id=:e AND id=:id',id=UUID(setup['cash_id']))['ledger_account_id']
   assert len(ledger)==2 and {(v['side'],v['amount']) for v in ledger}=={('debit',Decimal('2000')),('credit',Decimal('2000'))}
   assert next(v for v in ledger if v['side']=='debit')['system_code']=='AP_CONTROL'
   assert next(v for v in ledger if v['side']=='credit')['ledger_account_id']==cash_ledger

   assert settlement['obligation_id']==setup['t03_obligation_id'] and settlement['lost_confirm_response']
   assert settlement['before_capture']==settlement['before_confirm'] and settlement['after_retry']==settlement['after_lost_response']
   assert settlement['metrics']['repeated_known_facts']==0 and settlement['metrics']['new_required_facts']==2
   allocation=one('SELECT a.* FROM finance.payment_allocations a JOIN finance.payments p ON p.id=a.payment_id WHERE p.entity_id=:e AND a.payment_id=:id',id=payment['id'])
   assert allocation['obligation_id']==UUID(setup['t03_obligation_id']) and allocation['amount']==Decimal('2000')
   owned['finance.payment_allocations'].add(str(allocation['id']))
   seed=one('SELECT * FROM finance.obligations WHERE entity_id=:e AND id=:id',id=allocation['obligation_id'])
   assert seed['direction']=='payable' and seed['counterparty_id']==UUID(setup['serkan_id']) and seed['original_amount']==Decimal('5000')
   prior=next(v for v in before['tables']['finance.obligation_balances'] if v['obligation_id']==setup['t03_obligation_id'])
   assert Decimal(prior['remaining_amount'])==Decimal('5000') and Decimal(prior['allocated_amount'])==0
   remaining=one('SELECT * FROM finance.obligation_balances WHERE entity_id=:e AND obligation_id=:id',id=allocation['obligation_id'])
   assert remaining['remaining_amount']==Decimal('3000') and remaining['allocated_amount']==Decimal('2000') and remaining['settlement_status']=='partial'
   for table in ('finance.obligations','finance.direct_expenses','finance.advances','finance.advance_applications','inventory.events','inventory.movements','harvest.depot_settlements'):
    assert settlement['before_capture']['tables'][table]==settlement['after_retry']['tables'][table], 'Debt settlement created an unrelated effect: '+table
   proof['cases'].append(dict(engine=engine['engine'],id='T03',source_id=settlement['source_id'],payment_id=str(payment['id']),amount='2000.00',cash_delta='-2000.00',existing_payable=setup['t03_obligation_id'],remaining_payable='3000.00',allocations=1,no_new_expense_or_debt=True,metrics=settlement['metrics']))
   ambiguous=c['T04'];original=source(ambiguous)
   if mode=='candidate':
    p,debt=posted(ambiguous,'PAYABLE','5000');assert debt['direction']=='payable' and debt['counterparty_id']==UUID(setup['serkan_id'])
    link=one('SELECT * FROM core.owner_recovery_links WHERE entity_id=:e AND inbox_id=:s AND recovery_proposal_id=:p',s=original['id'],p=p['id'])
    assert str(link['origin_proposal_id'])==ambiguous['origin_proposal_id']
    origin=one('SELECT * FROM core.proposed_actions WHERE entity_id=:e AND id=:id',id=link['origin_proposal_id'])
    assert origin['action_type']=='UNRESOLVED' and origin['inbox_id']==original['id'] and origin['state']=='REJECTED'
    assert not rows(db,'SELECT * FROM core.confirmation_effects WHERE proposal_id=:p',p=origin['id'])
    assert ambiguous['explicit_intent']=='operating_payable' and ambiguous['before_capture']==ambiguous['before_confirm']
    assert ambiguous['metrics']['repeated_known_facts']==0
    assert debt['id']!=UUID(setup['t03_obligation_id'])
    assert one('SELECT * FROM finance.obligation_balances WHERE entity_id=:e AND obligation_id=:id',id=debt['id'])['remaining_amount']==Decimal('5000'), 'T04 debt was confused with the prior T03 payable'
   else:
    assert ambiguous['barriers'] and ambiguous['before_capture']==ambiguous['no_effect_snapshot']
    assert not rows(db,'SELECT ef.* FROM core.confirmation_effects ef JOIN core.proposed_actions p ON p.id=ef.proposal_id WHERE p.inbox_id=:s',s=original['id'])
   proof['cases'].append(dict(engine=engine['engine'],id='T04',source_id=ambiguous['source_id'],explicit_intent=ambiguous.get('explicit_intent'),one_payable=mode=='candidate',metrics=ambiguous['metrics'],barriers=ambiguous['barriers']))
   standalone=c['P01'];original=source(standalone)
   assert standalone['before_capture']==standalone['no_effect_snapshot']
   assert not rows(db,'SELECT ef.* FROM core.confirmation_effects ef JOIN core.proposed_actions p ON p.id=ef.proposal_id WHERE p.inbox_id=:s',s=original['id'])
   assert not rows(db,'SELECT * FROM core.invoice_receipt_links WHERE inbox_id=:s',s=original['id'])
   extraction=rows(db,'SELECT * FROM core.extraction_runs WHERE inbox_id=:s ORDER BY created_at,attempt',s=original['id']);assert extraction
   assert all(x['provider']=='local' and x['raw_output']['version']=='local-tesseract-tur-eng-v1' for x in extraction)
   assert all(x['raw_output']['pages'][0]['raw_tsv'] for x in extraction)
   if mode=='candidate':assert all(x['raw_output']['pages'][0]['lines'] for x in extraction)
   proof['cases'].append(dict(engine=engine['engine'],id='P01',source_id=standalone['source_id'],original_sha256=standalone['source_sha256'],effects=0,physical_arrival=False,payment=False,metrics=standalone['metrics'],barriers=standalone['barriers']))
   late=c['P02'];original=source(late);p,payable=posted(late,'PAYABLE','500')
   assert payable['direction']=='payable' and payable['counterparty_id']==UUID(setup['supplier_id'])
   assert late['source_sha256']==late['clicked_original_sha256'] and late['before_capture']==late['before_confirm']
   assert late['after_retry']==late['after_lost_response'] and late['lost_confirm_response']
   event=one('SELECT * FROM inventory.events WHERE entity_id=:e AND id=:id',id=UUID(late['receipt_id']))
   assert event['kind']=='receipt' and event['status']=='posted' and event['evidence']['structured_original']==late['receipt_original']
   assert event['payload']['lines'][0]['unit_cost'] is None and event['payload'].get('finance_obligation_id') is None
   movements=rows(db,'SELECT * FROM inventory.movements WHERE entity_id=:e AND event_id=:id',e=e,id=event['id']);assert len(movements)==1
   movement=movements[0];assert movement['quantity']==movement['uncosted_quantity']==Decimal('100') and movement['known_cost']==0
   assert movement['product_id']==UUID(setup['product_id']) and movement['location_id']==UUID(setup['location_id'])
   owned['inventory.events'].add(str(event['id']));owned['inventory.movements'].add(str(movement['id']))
   match=one('SELECT * FROM core.proposed_actions WHERE entity_id=:e AND id=:id',id=UUID(late['match_proposal_id']))
   assert match['action_type']=='INVOICE_MATCH' and match['state']=='CONFIRMED_NONPOSTING' and match['inbox_id']==original['id']
   linked=one('SELECT * FROM core.invoice_receipt_links WHERE entity_id=:e AND receipt_id=:r',r=event['id'])
   assert linked['inbox_id']==original['id'] and linked['proposal_id']==match['id'] and linked['finance_obligation_id']==payable['id']
   assert linked['valuation_state']=='UNKNOWN_UNCHANGED' and str(linked['id'])==late['invoice_link_id']
   owned['core.invoice_receipt_links'].add(str(linked['id']));owned['core.confirmation_effects'].add(str(match['id']))
   assert db.scalar(text("SELECT count(*) FROM core.proposal_actions WHERE proposal_id=:p AND action='CONFIRM'"),dict(p=match['id']))==1
   extraction=rows(db,'SELECT * FROM core.extraction_runs WHERE inbox_id=:s ORDER BY created_at,attempt',s=original['id']);assert extraction
   if mode=='candidate':
    assert original['metadata']['invoice_intent'] is None
    assert all(x['provider']=='local' and x['raw_output']['version']=='local-tesseract-tur-eng-v1' for x in extraction)
    assert all(x['raw_output']['pages'][0]['raw_tsv'] and x['raw_output']['pages'][0]['lines'] for x in extraction)
    final=one('SELECT * FROM core.proposal_revisions WHERE entity_id=:e AND proposal_id=:p AND version=:v',p=p['id'],v=p['version'])
    assert final['payload']['invoice_intent']=='operating_expense' and late['metrics']['repeated_known_facts']==0
   else:
    assert original['metadata']['invoice_intent']=='operating_expense'
    assert all(x['provider']=='owner' and x['raw_output']['engine']=='manual-supplier-invoice-source' for x in extraction)
    assert late['barriers'] and late['metrics']['repeated_known_facts']>0
   balance=one('SELECT * FROM finance.obligation_balances WHERE entity_id=:e AND obligation_id=:id',id=payable['id']);assert balance['remaining_amount']==Decimal('500')
   proof['cases'].append(dict(engine=engine['engine'],id='P02',source_id=late['source_id'],original_sha256=late['source_sha256'],receipt_id=str(event['id']),physical_movements=1,physical_quantity='100.000',stock_cost='UNKNOWN_UNCHANGED',payable_id=str(payable['id']),payable='500.00',invoice_links=1,payments=0,metrics=late['metrics'],barriers=late['barriers']))
   security=c['SECURITY'];assert security['before']==security['after']
   assert security['expiry_preflight']==dict(method='GET',path='/pilot/session',status=401,capture_posts=0), 'Expired owner write was not blocked by the real session preflight'
   assert db.scalar(text('SELECT count(*) FROM core.inbox_items WHERE entity_id=:e AND locator=:l'),dict(e=e,l=security['client_capture_id']))==0, 'Expired authenticated save created a source'
   for flag in ('storage_failure_visible','encrypted_draft','expired_session_retained_draft','late_response_did_not_restore_owner_ui','worker_cannot_read_owner_source','delayed_read_did_not_overwrite_edit'):assert security[flag] is True,flag
   proof['cases'].append(dict(engine=engine['engine'],id='SECURITY',effects=0,checks={flag:security[flag] for flag in ('storage_failure_visible','encrypted_draft','expired_session_retained_draft','late_response_did_not_restore_owner_ui','worker_cannot_read_owner_source','delayed_read_did_not_overwrite_edit')}))
  after=snapshot(db,e)
  current_sources={str(v['id']) for v in rows(db,"SELECT id FROM core.inbox_items WHERE entity_id=:e AND source='owner:r2'",e=e)}
  assert current_sources-set(before['source_ids'])==expected_sources, 'Unexpected source root or duplicate source from retry/security fault'
  for table in UNCHANGED:assert after['tables'][table]==before['tables'][table],f'Unrelated table changed: {table}'
  for table,expected_ids in owned.items():
   key='proposal_id' if table=='core.confirmation_effects' else 'id'
   old={v[key]:v for v in before['tables'][table]};new={v[key]:v for v in after['tables'][table]}
   assert all(new.get(k)==v for k,v in old.items()),f'Prior rows changed: {table}'
   assert set(new)-set(old)==expected_ids,f'Unexpected added effects: {table}'
  old_entries=before['tables']['finance.posting_entries'];new_entries=after['tables']['finance.posting_entries'];assert all(v in new_entries for v in old_entries)
  added=[v for v in new_entries if v not in old_entries];assert len(added)==2*len(owned['finance.postings']) and {v['posting_id'] for v in added}==owned['finance.postings']
  old_cash={v['money_account_id']:v for v in before['tables']['cash']};new_cash={v['money_account_id']:v for v in after['tables']['cash']};assert set(old_cash)==set(new_cash)
  for aid,value in new_cash.items():assert Decimal(value['balance'])-Decimal(old_cash[aid]['balance'])==(-Decimal('2650')*len(result['records']) if aid==setup['cash_id'] else 0)
  old_balances={v['obligation_id']:v for v in before['tables']['finance.obligation_balances']};new_balances={v['obligation_id']:v for v in after['tables']['finance.obligation_balances']}
  assert set(new_balances)-set(old_balances)==owned['finance.obligations']
  assert all(new_balances.get(k)==v for k,v in old_balances.items() if k!=setup['t03_obligation_id']), 'Unrelated pre-existing debt balance changed'
  product=UUID(setup['product_id']);location=UUID(setup['location_id'])
  stock=rows(db,'SELECT * FROM inventory.stock WHERE entity_id=:e AND product_id=:p AND location_id=:l',e=e,p=product,l=location);assert len(stock)==1
  old_stock=[v for v in before['tables']['inventory.stock'] if v['product_id']==str(product) and v['location_id']==str(location)]
  old_q=Decimal(old_stock[0]['quantity']) if old_stock else Decimal(0);old_u=Decimal(old_stock[0]['uncosted_quantity']) if old_stock else Decimal(0);old_cost=Decimal(old_stock[0]['known_cost']) if old_stock else Decimal(0)
  assert stock[0]['quantity']==old_q+Decimal('100')*len(result['records']) and stock[0]['uncosted_quantity']==old_u+Decimal('100')*len(result['records']) and stock[0]['known_cost']==old_cost
  others=lambda ss:[v for v in ss if not(v['product_id']==str(product) and v['location_id']==str(location))]
  assert others(after['tables']['inventory.stock'])==others(before['tables']['inventory.stock'])
  proof['exact_added_rows']={table:len(ids) for table,ids in owned.items()}|{'finance.posting_entries':len(added)}
  proof['reconciliation']=reconcile(db,include_classification=True,snapshot_established=True,entity_id=e);assert proof['reconciliation']['ok'] and not proof['reconciliation']['findings']
 proof['status']=expected
except Exception as error:
 proof['status']='FAIL';proof['failure']={'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()};write(a.output,proof);raise
write(a.output,proof);print('CAPTURE_DAILY_OWNER_SQL_'+proof['status'])
