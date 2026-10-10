"""Read-only attestation of the three actual owner browser cases after pilot gate."""
import hashlib,json,os,sys
from pathlib import Path
from decimal import Decimal
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.domain.owner_form_source import decode_original

result=json.loads(Path(sys.argv[1]).read_text())
assert result['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA'] and not result['errors']
assert len(result['cases'])==6
assert {(x['engine'],x['case']) for x in result['cases']}=={(e,c) for e in ('chromium','webkit') for c in ('A','B','C')}
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test')
sf=make_session_factory(url);proof={'sha':result['sha'],'cases':[]}
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
 _require_connected_runtime(db)
 entities=db.execute(text('SELECT id FROM core.entities')).scalars().all();assert len(entities)==1;e=entities[0]
 def one(sql,**kw):return dict(db.execute(text(sql),dict(e=e,**kw)).mappings().one())
 for row in result['cases']:
  if row['case']=='A':
   exp=one('SELECT * FROM finance.direct_expenses WHERE entity_id=:e AND proposal_id=:p',p=UUID(row['proposal_id']))
   source=one('SELECT * FROM core.inbox_items WHERE entity_id=:e AND id=:s',s=UUID(row['source_id']))
   assert exp['amount']==Decimal('8500') and exp['currency']=='TRY' and exp['description']=='Ремонт насоса' and exp['status']=='posted'
   proposal=one('SELECT * FROM core.proposed_actions WHERE entity_id=:e AND id=:i',i=UUID(row['proposal_id']))
   effect=one('SELECT * FROM core.confirmation_effects WHERE entity_id=:e AND proposal_id=:i',i=proposal['id'])
   assert proposal['inbox_id']==source['id'] and effect['direct_expense_id']==exp['id']
   assert source['raw_text']=='8500 насос ремонт' and source['metadata']['capture_context']=='quick_expense'
   entries=db.execute(text('SELECT side,sum(amount) amount FROM finance.posting_entries WHERE posting_id=:p GROUP BY side'),dict(p=exp['posting_id'])).all()
   assert dict(entries)=={'debit':Decimal('8500'),'credit':Decimal('8500')}
   proof['cases'].append(dict(engine=row['engine'],case='A',expense_id=str(exp['id']),posting_id=str(exp['posting_id']),amount='8500',source_sha256=hashlib.sha256(source['raw_text'].encode()).hexdigest()))
  elif row['case']=='B':
   report=one('SELECT * FROM harvest.worker_reports WHERE entity_id=:e AND id=:i',i=UUID(row['report_id']))
   original=one('SELECT * FROM harvest.evidence WHERE entity_id=:e AND id=:i',i=UUID(row['evidence_id']))
   assert report['cut_count']==135 and report['field_weight_kg'] is None and report['original_greenhouse']=='TEST İmamlı'
   assert original['source_reference']==row['original']['form_id']
   assert report['evidence_id']==original['id'] and decode_original(original['source'],original['raw_text'])==row['original']
   assert original['fingerprint']==hashlib.sha256(original['raw_text'].encode()).hexdigest()
   assert db.scalar(text('SELECT count(*) FROM harvest.batch_reports WHERE report_id=:i'),dict(i=report['id']))==0
   proof['cases'].append(dict(engine=row['engine'],case='B',report_id=str(report['id']),source_sha256=original['fingerprint'],field_weight='UNKNOWN',depot_link=False))
  else:
   event=one('SELECT * FROM inventory.events WHERE entity_id=:e AND id=:i',i=UUID(row['event_id']))
   assert event['locator']==row['original']['form_id']
   assert event['status']=='posted' and event['kind']=='receipt' and event['evidence']['structured_original']==row['original']
   assert event['payload']['lines'][0]['unit_cost'] is None and event['payload']['lines'][0]['unit']=='kg'
   moves=db.execute(text('SELECT * FROM inventory.movements WHERE entity_id=:e AND event_id=:i'),dict(e=e,i=event['id'])).mappings().all()
   assert len(moves)==1 and moves[0]['product_id']==UUID(row['product_id']) and moves[0]['location_id']==UUID(row['location_id'])
   assert moves[0]['quantity']==Decimal('120') and moves[0]['uncosted_quantity']==Decimal('120') and moves[0]['known_cost']==0
   proof['cases'].append(dict(engine=row['engine'],case='C',event_id=str(event['id']),quantity='120',unit='kg',movement_count=1,cost='UNKNOWN'))
 # Prior full pilot SQL proof established these counts before the owner cases.
 expected={'finance.postings':13,'finance.direct_expenses':4,'finance.payments':4,'finance.obligations':4,'finance.advances':0,'inventory.movements':6,'harvest.worker_reports':4,'harvest.depot_settlements':2,'core.invoice_receipt_links':0}
 counts={t:db.scalar(text('SELECT count(*) FROM '+t+' WHERE entity_id=:e'),dict(e=e)) for t in expected}
 assert counts==expected,(counts,expected)
 proof['exact_counts']=counts
 db.rollback()
Path(sys.argv[2]).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
print('Three owner browser cases: exact PostgreSQL effects, immutable originals and separated money/stock/depot counts PASS.')
