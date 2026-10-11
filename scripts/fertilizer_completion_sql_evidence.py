"""Synthetic CI setup + independent read-only SQL proof of browser completion.
All financial/harvest authoritative rows remain unchanged. No public deployment.
"""
import json,os,sys
from pathlib import Path
from decimal import Decimal
from uuid import UUID,uuid5,NAMESPACE_URL
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.models import WorkAreaRow
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.synthetic_pilot import SOURCE,OWNER_REF
from greenhouse_owner_os.services.reconciliation import reconcile
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test');sf=make_session_factory(url)
TABLES=('finance.obligations','finance.payments','finance.direct_expenses','finance.postings','finance.posting_entries','harvest.worker_reports','harvest.depot_settlements')
def snapshot(db):return {t:sorted([dict(r) for r in db.execute(text('SELECT * FROM '+t)).mappings()],key=lambda r:json.dumps(r,sort_keys=True,default=str)) for t in TABLES}
if sys.argv[1]=='--prepare':
 path=Path(sys.argv[2]);path.mkdir(parents=True,exist_ok=True);fixtures={}
 with SqlAlchemyUnitOfWork(sf) as u:
  _require_connected_runtime(u.session);e=u.session.execute(text('SELECT id FROM core.entities')).scalar_one();a=u.session.execute(text('SELECT id FROM core.actors WHERE external_ref=:r'),dict(r=OWNER_REF)).scalar_one();assert u.session.scalar(text('SELECT core.r2_owner(:e,:a)'),dict(e=e,a=a))
  for engine in ('chromium','webkit'):
   root=uuid5(NAMESPACE_URL,SOURCE+':COMPLETION:'+engine+':greenhouse');parts=[]
   for index in range(5):
    rid=root if index==0 else uuid5(root,str(index));name='TEST Completion '+engine+' İmamlı' if index==0 else 'TEST Completion '+engine+' Part '+str(index);parent=None if index==0 else root;kind='greenhouse' if index==0 else 'work_area'
    old=u.session.get(WorkAreaRow,rid)
    if not old:
     u.session.add(WorkAreaRow(id=rid,entity_id=e,name=name,parent_id=parent,kind=kind,source=SOURCE));u.session.flush();u.audit.append(actor_id=a,action='reference_create',entity_type='work_area',entity_id=rid,before=None,after=dict(id=str(rid),name=name,source=SOURCE,classification='TEST_PREVIEW'))
    else:assert (old.entity_id,old.name,old.parent_id,old.kind,old.source)==(e,name,parent,kind,SOURCE)
    if index:parts.append(dict(id=str(rid),name=name))
   fixtures[engine]=dict(entity_id=str(e),greenhouse_id=str(root),parts=parts)
  u.commit()
 with sf() as db:
  db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db);before=snapshot(db)
 (path/'fixtures.json').write_text(json.dumps(fixtures,indent=2));(path/'before.json').write_text(json.dumps(before,sort_keys=True,default=str));print('FERTILIZER_COMPLETION_SYNTHETIC_REFERENCE_FIXTURE_READY');sys.exit(0)
r=json.loads(Path(sys.argv[1]).read_text());assert r['status']=='PASS' and r['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA'] and not r['errors'];assert {x['engine'] for x in r['records']}=={'chromium','webkit'}
proof={'sha':r['sha'],'cases':[]}
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
 assert json.loads(json.dumps(snapshot(db),sort_keys=True,default=str))==json.loads(Path(sys.argv[3]).read_text()),'Fertilizer completion changed Finance/Harvest authoritative rows'
 for record in r['records']:
  p=UUID(record['plan_id']);e=UUID(record['entity_id'])
  revisions=list(db.execute(text('SELECT * FROM inventory.fertilizer_revisions WHERE plan_id=:p ORDER BY revision'),dict(p=p)).mappings());assert revisions
  final=revisions[-1];assert final['input']['source']['source_kind']=='OWNER_FORM'
  cards=final['input']['cards'];assert all('tank_id' not in c for c in cards)
  applications={c['application_id'] for c in cards};assert len(applications)>=2
  assert {c['application_order'] for c in cards}>= {1,2}
  reports=list(db.execute(text('SELECT * FROM inventory.fertilizer_reports WHERE plan_id=:p ORDER BY received_at'),dict(p=p)).mappings());assert reports
  actuals=list(db.execute(text('SELECT * FROM inventory.fertilizer_actuals WHERE plan_id=:p ORDER BY created_at,id'),dict(p=p)).mappings());assert len(actuals)==record['actual_count']==6;assert {str(x['id']) for x in actuals}==set(record['actual_ids'])
  normalized=list(db.execute(text('SELECT * FROM inventory.fertilizer_actual_lines WHERE plan_id=:p'),dict(p=p)).mappings());assert normalized
  keys=[(str(x['physical_execution_id']),str(x['card_id']),str(x['component_id'])) for x in normalized];assert len(keys)==len(set(keys)),'Duplicate logical actual line'
  total={};zero_count=0;movement_count=0
  for actual in actuals:
   inputs=actual['input']['lines'];positive=[x for x in inputs if Decimal(x['quantity'].replace(',','.'))>0]
   assert actual['source_snapshot']['input']==actual['input'] and actual['source_id']==UUID(actual['input']['source']['form_id'])
   linked=[x for x in normalized if x['actual_id']==actual['id']];assert len(linked)==len(inputs)
   for original in inputs:
    normalized_line=next(x for x in linked if str(x['card_id'])==original['card_id'] and str(x['component_id'])==original['component_id'])
    assert normalized_line['quantity']==Decimal(original['quantity'].replace(',','.'))
   if not positive:
    zero_count+=1;assert actual['application_id'] is None;continue
   event=db.execute(text('SELECT * FROM inventory.events WHERE id=:i'),dict(i=actual['application_id'])).mappings().one();assert event['kind']=='application' and event['status']=='posted'
   movements=list(db.execute(text('SELECT * FROM inventory.movements WHERE event_id=:i'),dict(i=event['id'])).mappings());assert len(movements)==len(positive)
   movement_count+=len(movements)
   # Exact input effect is independently reconstructed from that historical revision.
   old=db.execute(text('SELECT preview FROM inventory.fertilizer_revisions WHERE id=:i'),dict(i=actual['revision_id'])).scalar_one()
   expected={}
   for line in positive:
    card=next(c for c in old['cards'] if c['card_id']==line['card_id']);component=next(c for c in card['components'] if c['component_id']==line['component_id']);key=(component['product_id'],component['location_id'],card['work_area_id']);expected[key]=expected.get(key,Decimal(0))-Decimal(line['quantity'].replace(',','.'))
   observed={}
   for movement in movements:
    key=(str(movement['product_id']),str(movement['location_id']),str(movement['work_area_id']));observed[key]=observed.get(key,Decimal(0))+movement['quantity'];total[str(movement['product_id'])]=total.get(str(movement['product_id']),Decimal(0))+movement['quantity']
   assert observed==expected,(observed,expected)
  assert zero_count==2 and {str(x['id']) for x in actuals if x['application_id'] is None}==set(record['zero_actual_ids'])
  assert total=={p['id']:-Decimal(p['expected_quantity']) for p in record['products']}
  if 'expected_product_totals' in record:assert total=={k:Decimal(v) for k,v in record['expected_product_totals'].items()}
  resolutions=list(db.execute(text('SELECT * FROM inventory.fertilizer_resolutions WHERE plan_id=:p'),dict(p=p)).mappings());assert len(resolutions)==2 and {x['kind'] for x in resolutions}=={'REOPEN_SCOPE','EVIDENCE_ONLY'}
  assert {str(x['id']) for x in resolutions}==set(record['resolution_ids'])
  if record.get('copy_id'):
   copied=UUID(record['copy_id']);assert db.scalar(text('SELECT count(*) FROM inventory.fertilizer_actuals WHERE plan_id=:p'),dict(p=copied))==0
  proof['cases'].append(dict(engine=record['engine'],plan_id=str(p),applications=len(applications),actual_count=len(actuals),zero_actual_count=zero_count,normalized_lines=len(normalized),movements=movement_count,quantities={k:str(v) for k,v in total.items()},reports=len(reports),resolutions=len(resolutions),finance_harvest_unchanged=True))
 db.rollback()
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));proof['reconciliation']=reconcile(db,include_classification=True,snapshot_established=True);assert proof['reconciliation']['ok'],proof['reconciliation']
proof['status']='PASS';Path(sys.argv[2]).write_text(json.dumps(proof,indent=2,default=str));print('FERTILIZER_COMPLETION_SQL_PASS')
