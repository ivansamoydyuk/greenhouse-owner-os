"""Owned synthetic reference setup and read-only evidence for actual browser plans."""
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
   root=uuid5(NAMESPACE_URL,SOURCE+':D:'+engine+':greenhouse');parts=[]
   for index in range(5):
    rid=root if index==0 else uuid5(root,str(index));name='TEST D '+engine+' İmamlı' if index==0 else 'TEST D '+engine+' Part '+str(index);parent=None if index==0 else root;kind='greenhouse' if index==0 else 'work_area'
    old=u.session.get(WorkAreaRow,rid)
    if not old:
     u.session.add(WorkAreaRow(id=rid,entity_id=e,name=name,parent_id=parent,kind=kind,source=SOURCE));u.session.flush();u.audit.append(actor_id=a,action='reference_create',entity_type='work_area',entity_id=rid,before=None,after=dict(id=str(rid),name=name,source=SOURCE,classification='TEST_PREVIEW'))
    else:assert (old.entity_id,old.name,old.parent_id,old.kind,old.source)==(e,name,parent,kind,SOURCE)
    if index:parts.append(dict(id=str(rid),name=name))
   fixtures[engine]=dict(entity_id=str(e),greenhouse_id=str(root),parts=parts)
  u.commit()
 with sf() as db:
  db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db);before=snapshot(db)
 (path/'fixtures.json').write_text(json.dumps(fixtures,indent=2));(path/'before.json').write_text(json.dumps(before,sort_keys=True,default=str));print('FERTILIZER_SYNTHETIC_REFERENCE_FIXTURE_READY');sys.exit(0)
r=json.loads(Path(sys.argv[1]).read_text());assert r['status']=='PASS' and r['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA'] and not r['errors'];assert {x['engine'] for x in r['records']}=={'chromium','webkit'}
proof={'sha':r['sha'],'cases':[]}
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
 assert json.loads(json.dumps(snapshot(db),sort_keys=True,default=str))==json.loads(Path(sys.argv[3]).read_text()),'Fertilizer journey changed Finance/Harvest authoritative rows'
 for record in r['records']:
  p=UUID(record['plan_id']);e=UUID(record['entity_id'])
  revisions=list(db.execute(text('SELECT * FROM inventory.fertilizer_revisions WHERE plan_id=:p ORDER BY revision'),dict(p=p)).mappings());assert len(revisions)==2
  final=revisions[-1];assert final['input']['source']['source_kind']=='OWNER_FORM' and len(final['input']['cards'])==4
  for c in final['input']['cards']:
   assert [x['dose_per_da'] for x in c['components']]==['10','5','2'];assert [x['unit'] for x in c['components']]==['kg','L','kg'];assert c['rendering_reviewed'] is True
  reports=list(db.execute(text('SELECT * FROM inventory.fertilizer_reports WHERE plan_id=:p ORDER BY received_at'),dict(p=p)).mappings());assert len(reports)==record['expected_reports']
  assert reports[0]['response']=='PROBLEM' and reports[0]['source_snapshot']['comment']=='TEST D проблема: часть ещё не обработана.'
  assert any(x['response']=='PARTIAL' and x['source_snapshot']['quantities'][0]['quantity']=='25,00' for x in reports)
  actuals=list(db.execute(text('SELECT * FROM inventory.fertilizer_actuals WHERE plan_id=:p'),dict(p=p)).mappings());assert len(actuals)==1;actual=actuals[0];assert len(actual['input']['lines'])==12 and len(actual['input']['completed_card_ids'])==4
  event=db.execute(text('SELECT * FROM inventory.events WHERE id=:i'),dict(i=actual['application_id'])).mappings().one();assert event['kind']=='application' and event['status']=='posted'
  movements=list(db.execute(text('SELECT * FROM inventory.movements WHERE event_id=:i'),dict(i=event['id'])).mappings());assert len(movements)==12
  totals={}
  for m in movements:totals[str(m['product_id'])]=totals.get(str(m['product_id']),Decimal(0))+m['quantity']
  assert totals=={record['products'][0]['id']:Decimal('-200'),record['products'][1]['id']:Decimal('-100'),record['products'][2]['id']:Decimal('-40')},totals
  assert event['payload'].get('finance_obligation_id') is None
  copied=db.execute(text('SELECT input FROM inventory.fertilizer_revisions WHERE plan_id=:p'),dict(p=UUID(record['copy_id']))).scalar_one();assert copied['copied_from_plan_id']==str(p) and copied['copied_from_revision']==2 and copied['source']['form_id']!=final['input']['source']['form_id']
  assert db.scalar(text('SELECT count(*) FROM inventory.fertilizer_actuals WHERE plan_id=:p'),dict(p=UUID(record['copy_id'])))==0
  assert db.scalar(text('SELECT count(*) FROM inventory.fertilizer_dispatches WHERE plan_id=:p'),dict(p=UUID(record['copy_id'])))==0
  proof['cases'].append(dict(engine=record['engine'],plan_id=str(p),revisions=2,cards=4,components_per_card=3,actual_count=1,movements=12,quantities={k:str(v) for k,v in totals.items()},source_immutable=True,finance_rows_unchanged=True,copy_without_effect=True))
 db.rollback()
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));proof['reconciliation']=reconcile(db,include_classification=True,snapshot_established=True,entity_id=e);assert proof['reconciliation']['ok'],proof['reconciliation']
proof['status']='PASS';Path(sys.argv[2]).write_text(json.dumps(proof,indent=2,default=str));print('OVERNIGHT_FERTILIZER_SQL_PASS')
