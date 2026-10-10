"""Exact read-only PostgreSQL attestation of browser-created Harvest journeys."""
import hashlib,json,os,sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.reconciliation import reconcile
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test');sf=make_session_factory(url)
UNCHANGED=('finance.payments','finance.direct_expenses','finance.advances','finance.owner_fundings','finance.advance_applications','finance.direct_settlements','finance.own_account_transfers','inventory.events','inventory.movements','inventory.stock','inventory.products')
def snapshot(db,e):
 def normalized(rows):return sorted([json.loads(json.dumps(dict(x),default=str)) for x in rows],key=lambda x:json.dumps(x,sort_keys=True))
 tables={name:normalized(db.execute(text('SELECT * FROM '+name+' WHERE entity_id=:e'),{'e':e}).mappings()) for name in UNCHANGED}
 tables['cash']=normalized(db.execute(text('SELECT b.* FROM finance.money_account_balances b JOIN finance.money_accounts m ON m.id=b.money_account_id WHERE m.entity_id=:e'),{'e':e}).mappings())
 return dict(sha=os.environ['OWNER_PREVIEW_FEATURE_SHA'],unchanged=tables,postings=normalized(db.execute(text('SELECT * FROM finance.postings WHERE entity_id=:e'),{'e':e}).mappings()),obligations=normalized(db.execute(text('SELECT * FROM finance.obligations WHERE entity_id=:e'),{'e':e}).mappings()))
if sys.argv[1]=='--before':
 with sf() as db:
  db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
  entities=db.execute(text('SELECT id FROM core.entities')).scalars().all();assert len(entities)==1
  Path(sys.argv[2]).parent.mkdir(parents=True,exist_ok=True);Path(sys.argv[2]).write_text(json.dumps(snapshot(db,entities[0]),indent=2));print('HARVEST_BEFORE_SNAPSHOT_SAVED')
 sys.exit(0)
result=json.loads(Path(sys.argv[1]).read_text());assert result['status']=='PASS' and result['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA'];assert len(result['records'])==2 and not result['errors']
before=json.loads(Path(sys.argv[3]).read_text());assert before['sha']==result['sha']
proof={'sha':result['sha'],'cases':[]}
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
 entities=db.execute(text('SELECT id FROM core.entities')).scalars().all();assert len(entities)==1;e=entities[0]
 def one(sql,**kw):return dict(db.execute(text(sql),dict(e=e,**kw)).mappings().one())
 def rows(sql,**kw):return [dict(x) for x in db.execute(text(sql),dict(e=e,**kw)).mappings()]
 for record in result['records']:
  sid=UUID(record['settlement_id']);pid=UUID(record['proposal_id']);source=one('SELECT * FROM core.inbox_items WHERE id=:id AND entity_id=:e',id=UUID(record['source_id']))
  assert source['metadata']['capture_context']=='depot_receipt' and hashlib.sha256(source['file_bytes']).hexdigest()==record['source_sha256'];assert source['raw_text']=='TEST separate caption '+record['engine']
  report=one('SELECT * FROM harvest.worker_reports WHERE id=:id AND entity_id=:e',id=UUID(record['report_id']));assert report['cut_count']==131 and report['field_weight_kg'] is None
  proposal=one('SELECT * FROM core.proposed_actions WHERE id=:id AND entity_id=:e',id=pid);assert proposal['inbox_id']==source['id'] and proposal['state']=='POSTED'
  effects=rows('SELECT * FROM core.confirmation_effects WHERE proposal_id=:id AND entity_id=:e',id=pid);assert len(effects)==1 and effects[0]['settlement_id']==sid
  actions=rows("SELECT * FROM core.proposal_actions WHERE proposal_id=:id AND entity_id=:e AND action='CONFIRM'",id=pid);assert len(actions)==1
  receipt=one('SELECT * FROM harvest.depot_settlements WHERE id=:id AND entity_id=:e',id=sid);assert receipt['status']=='posted' and receipt['total_amount']==Decimal('45000') and receipt['payment_id'] is None and receipt['application_ids']==[] and receipt['obligation_id']==UUID(record['obligation_id'])
  linked_evidence=one('SELECT * FROM harvest.evidence WHERE id=:id AND entity_id=:e',id=receipt['evidence_id']);assert linked_evidence['fingerprint']==source['fingerprint'] and linked_evidence['source_reference']==str(pid) and linked_evidence['raw_text']==source['raw_text']
  lines=rows('SELECT * FROM harvest.settlement_lines WHERE settlement_id=:id ORDER BY net_weight_kg',id=sid);assert len(lines)==2 and sum(x['count'] for x in lines)==93 and sum(x['net_weight_kg'] for x in lines)==Decimal('900') and sum(x['amount'] for x in lines)==Decimal('45000');assert {x['original_quality_name'] for x in lines}=={'Grand','İkinci'} and {x['normalized_quality'] for x in lines}=={'HIGHEST','SECOND'}
  quality=rows('SELECT * FROM harvest.receipt_quality_reviews WHERE settlement_id=:id',id=sid);assert len(quality)==2 and all(x['proposal_id']==pid for x in quality)
  obligation=one('SELECT * FROM finance.obligations WHERE id=:id AND entity_id=:e',id=receipt['obligation_id']);assert obligation['original_amount']==Decimal('45000') and obligation['direction']=='receivable' and obligation['economic_source']=='harvest_depot'
  postings=rows("SELECT * FROM finance.postings WHERE source_id=:id AND entity_id=:e AND posting_type='obligation_posted'",id=obligation['id']);assert len(postings)==1
  entries=rows('SELECT side,sum(amount) amount FROM finance.posting_entries WHERE posting_id=:id GROUP BY side',id=postings[0]['id']);assert {x['side']:x['amount'] for x in entries}=={'debit':Decimal('45000'),'credit':Decimal('45000')}
  revisions=rows('SELECT * FROM harvest.journey_revisions WHERE settlement_id=:id ORDER BY version',id=sid);assert [x['kind'] for x in revisions]==['ESTIMATE','EVIDENCE','LINKS'];assert len({x['version'] for x in revisions})==3
  estimate=rows('SELECT * FROM harvest.estimate_lines WHERE revision_id=:id',id=UUID(record['estimate_id']));assert len(estimate)==4 and sum(x['net_weight_kg'] for x in estimate)==Decimal('900') and sum(x['amount'] for x in estimate)==Decimal('45000')
  per_area={};
  for x in estimate:
   v=per_area.setdefault(str(x['work_area_id']),[Decimal(0),Decimal(0)]);v[0]+=x['net_weight_kg'];v[1]+=x['amount']
  assert sorted(per_area.values())==[[Decimal('300'),Decimal('15000')],[Decimal('600'),Decimal('30000')]]
  assert not rows('SELECT a.* FROM harvest.line_allocations a JOIN harvest.settlement_lines l ON l.id=a.line_id WHERE l.settlement_id=:id',id=sid)
  assert revisions[0]['link_revision_id'] is None and revisions[-1]['id']!=revisions[0]['link_revision_id']
  extra=one('SELECT * FROM harvest.evidence WHERE id=:id AND entity_id=:e',id=UUID(record['attachment_id']));assert extra['file_bytes'] and extra['raw_text']=='TEST later evidence';assert hashlib.sha256(extra['file_bytes']).hexdigest()==extra['fingerprint'];assert extra['id']!=receipt['evidence_id']
  proof['cases'].append(dict(engine=record['engine'],source_id=str(source['id']),source_sha256=record['source_sha256'],report_id=str(report['id']),field_count=131,depot_adet=93,settlement_id=str(sid),sale='45000.00',currency='TRY',payment=None,quality_reviews=len(quality),estimate_totals={'kg':'900.000','amount':'45000.00'},estimate_stale_after_link=True,append_only_revisions=3,confirmed_effects=1,balanced_postings=1))
 after=snapshot(db,e);assert after['unchanged']==before['unchanged'],'Unrelated cash/payment/expense/stock state changed'
 old_postings={x['id']:x for x in before['postings']};new_postings={x['id']:x for x in after['postings']};assert all(new_postings[k]==v for k,v in old_postings.items())
 added=[v for k,v in new_postings.items() if k not in old_postings];assert len(added)==2 and all(x['posting_type']=='obligation_posted' and x['source_id'] in {r['obligation_id'] for r in result['records']} for x in added)
 old_obligations={x['id']:x for x in before['obligations']};new_obligations={x['id']:x for x in after['obligations']};assert all(new_obligations[k]==v for k,v in old_obligations.items())
 added_ar=[v for k,v in new_obligations.items() if k not in old_obligations];assert {x['id'] for x in added_ar}=={r['obligation_id'] for r in result['records']} and sum(Decimal(x['original_amount']) for x in added_ar)==Decimal('90000')
 proof['unchanged_tables']=list(UNCHANGED)+['cash'];proof['only_added_postings']=added;proof['only_added_ar']=added_ar
 db.rollback()
with sf() as session:
 session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
 reconciliation=reconcile(session,include_classification=True,snapshot_established=True,entity_id=e)
 proof['reconciliation']=reconciliation
 assert reconciliation['ok'],reconciliation
proof['status']='PASS';Path(sys.argv[2]).write_text(json.dumps(proof,indent=2,default=str));print('HARVEST_JOURNEY_SQL_PASS')
