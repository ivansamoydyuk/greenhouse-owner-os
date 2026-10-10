"""Read actual browser-created Stage B rows without adding business effects."""
import base64,hashlib,json,os,sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.services.reconciliation import reconcile
r=json.loads(Path(sys.argv[1]).read_text());assert r['status']=='PASS' and r['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA'] and not r['errors']
assert {x['engine'] for x in r['records']}=={'chromium','webkit'}
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test');sf=make_session_factory(url);proof={'sha':r['sha'],'cases':[]}
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
 for x in r['records']:
  e=UUID(x['entity_id']);params={k:UUID(v) for k,v in x.items() if k.endswith('_id')}
  params['e']=e
  def rows(sql,**kw):return list(db.execute(text(sql),{**params,**kw}).mappings())
  events=rows('SELECT * FROM inventory.events WHERE entity_id=:e AND id IN (:receipt_id,:transfer_id)')
  assert len(events)==2 and all(v['status']=='posted' for v in events)
  arrival=next(v for v in events if v['kind']=='receipt');transfer=next(v for v in events if v['kind']=='transfer')
  assert arrival['payload']['lines'][0]['unit_cost'] is None and arrival['payload'].get('finance_obligation_id') is None
  assert hashlib.sha256(base64.b64decode(arrival['evidence']['file_base64'])).hexdigest()==x['receipt_sha256']
  assert transfer['evidence']['structured_original']['fields']['quantity']=='20,00'
  assert transfer['evidence']['structured_original']['fields']['destination_id']==x['destination_id']
  moves=rows('SELECT * FROM inventory.movements WHERE entity_id=:e AND product_id=:product_id')
  assert len(moves)==3
  a=[v for v in moves if v['event_id']==UUID(x['receipt_id'])];t=[v for v in moves if v['event_id']==UUID(x['transfer_id'])]
  assert len(a)==1 and a[0]['quantity']==Decimal('100') and len(t)==2 and sum(v['quantity'] for v in t)==0
  assert sum(v['quantity'] for v in moves)==Decimal('100') and all(v['known_cost']==0 and v['uncosted_quantity']==v['quantity'] for v in moves)
  stock=rows('SELECT * FROM inventory.stock WHERE entity_id=:e AND product_id=:product_id')
  assert {str(v['location_id']):v['quantity'] for v in stock}=={x['origin_id']:Decimal('80'),x['destination_id']:Decimal('20')}
  for v in stock:
   assert v['quantity']==sum(m['quantity'] for m in moves if m['location_id']==v['location_id'])
   assert v['uncosted_quantity']==v['quantity'] and v['known_cost']==0
  links=rows('SELECT * FROM core.invoice_receipt_links WHERE entity_id=:e AND receipt_id=:receipt_id')
  assert len(links)==2 and {str(v['inbox_id']) for v in links}=={x['source_id'],x['existing_source_id']}
  assert all(v['finance_obligation_id']==UUID(x['obligation_id']) for v in links)
  originals=rows('SELECT * FROM core.inbox_items WHERE entity_id=:e AND id IN (:source_id,:existing_source_id)')
  first=next(v for v in originals if str(v['id'])==x['source_id'])
  assert hashlib.sha256(first['file_bytes']).hexdigest()==x['invoice_sha256']
  assert first['metadata']['invoice_receipt_id']==x['receipt_id']
  proposals=rows('SELECT * FROM core.proposed_actions WHERE entity_id=:e AND inbox_id=:existing_source_id')
  assert len(proposals)==1 and proposals[0]['action_type']=='INVOICE_MATCH' and proposals[0]['state']=='CONFIRMED_NONPOSTING'
  obligations=rows('SELECT * FROM finance.obligations WHERE entity_id=:e AND counterparty_id=:supplier_id')
  assert len(obligations)==1 and str(obligations[0]['id'])==x['obligation_id'] and obligations[0]['original_amount']==Decimal('500')
  posting_id=obligations[0]['posting_id']
  entries=rows('SELECT pe.side,pe.amount,l.account_class FROM finance.posting_entries pe JOIN finance.ledger_accounts l ON l.id=pe.ledger_account_id WHERE pe.posting_id=:posting',posting=posting_id)
  assert sum(v['amount'] if v['side']=='debit' else -v['amount'] for v in entries if v['account_class']=='expense')==Decimal('500')
  assert len(rows("SELECT * FROM finance.postings WHERE entity_id=:e AND source_id=:obligation_id"))==1
  balance=rows('SELECT * FROM finance.obligation_balances WHERE entity_id=:e AND obligation_id=:obligation_id')[0]
  assert balance['remaining_amount']==Decimal('300')
  payments=rows('SELECT * FROM finance.payments WHERE entity_id=:e AND money_account_id=:account_id')
  assert len(payments)==1 and str(payments[0]['id'])==x['payment_id'] and payments[0]['amount']==Decimal('200') and payments[0]['status']=='posted'
  assert rows('SELECT * FROM finance.money_account_balances WHERE entity_id=:e AND money_account_id=:account_id')[0]['balance']==Decimal('-200')
  effects=rows('SELECT * FROM core.confirmation_effects WHERE entity_id=:e AND proposal_id IN (:payable_proposal_id,:match_proposal_id)')
  assert len(effects)==2
  proof['cases'].append(dict(engine=x['engine'],receipt_id=x['receipt_id'],physical_moves=3,physical_total='100',source_position='80',destination_position='20',invoice_links=2,payables=1,remaining_payable='300',payments=1,cash='-200',cost='UNKNOWN',original_hashes_verified=True))
 proof['finance_reconciliation']=reconcile(db,include_classification=True,snapshot_established=True,entity_id=e)
 assert proof['finance_reconciliation']['ok'],proof['finance_reconciliation']
 db.rollback()
proof['status']='PASS';Path(sys.argv[2]).write_text(json.dumps(proof,indent=2,default=str));print('OVERNIGHT_PURCHASES_SQL_PASS')
