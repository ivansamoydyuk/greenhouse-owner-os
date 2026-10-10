"""Read-only PostgreSQL effects for actual overnight Stage A browser payments."""
import json,os,sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.services.reconciliation import reconcile
result=json.loads(Path(sys.argv[1]).read_text())
assert result['status']=='PASS' and result['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA'] and not result['errors']
assert {x['engine'] for x in result['engines']}=={'chromium','webkit'}
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test');sf=make_session_factory(url)
proof={'sha':result['sha'],'cases':[]}
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
 for record in result['engines']:
  e=UUID(record['entity_id']);aid=UUID(record['account_id'])
  payments=list(db.execute(text('SELECT * FROM finance.payments WHERE entity_id=:e AND money_account_id=:a'),dict(e=e,a=aid)).mappings())
  assert {str(p['id']) for p in payments}==set(record['payments']) and len(payments)==2
  assert all(p['status']=='posted' and p['reversal_posting_id'] is None for p in payments)
  balance=db.execute(text('SELECT balance FROM finance.money_account_balances WHERE entity_id=:e AND money_account_id=:a'),dict(e=e,a=aid)).scalar_one()
  assert balance==Decimal('200')
  documents=[]
  for direction,remaining,allocated in [('receivable','600','400'),('payable','300','200')]:
   oid=UUID(record['documents'][direction]['id'])
   row=db.execute(text('SELECT * FROM finance.obligation_balances WHERE entity_id=:e AND obligation_id=:id'),dict(e=e,id=oid)).mappings().one()
   assert row['remaining_amount']==Decimal(remaining)
   allocation_rows=list(db.execute(text('SELECT a.* FROM finance.payment_allocations a JOIN finance.payments p ON p.id=a.payment_id WHERE a.obligation_id=:o AND p.entity_id=:e'),dict(o=oid,e=e)).mappings())
   assert len(allocation_rows)==1 and allocation_rows[0]['amount']==Decimal(allocated)
   posting=db.execute(text('SELECT posting_id FROM finance.obligations WHERE id=:o AND entity_id=:e'),dict(o=oid,e=e)).scalar_one()
   entries=dict(db.execute(text('SELECT side,sum(amount) FROM finance.posting_entries WHERE posting_id=:p GROUP BY side'),dict(p=posting)).all())
   expected=Decimal('1000') if direction=='receivable' else Decimal('500')
   assert entries=={'debit':expected,'credit':expected}
   documents.append(dict(id=str(oid),direction=direction,remaining=remaining,allocated=allocated,posting_id=str(posting)))
  proof['cases'].append(dict(engine=record['engine'],account_id=str(aid),balance=str(balance),payment_count=2,documents=documents))
 db.rollback()
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
 proof['reconciliation']=reconcile(db,include_classification=True,snapshot_established=True,entity_id=e)
 assert proof['reconciliation']['ok'],proof['reconciliation']
proof['status']='PASS';Path(sys.argv[2]).write_text(json.dumps(proof,indent=2,default=str));print('OVERNIGHT_FINANCE_SQL_PASS')
