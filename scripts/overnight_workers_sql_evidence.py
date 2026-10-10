"""Read only the persisted original responses created by real worker browsers."""
import json,os,sys
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.services.reconciliation import reconcile
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test');sf=make_session_factory(url)
TABLES=('finance.obligations','finance.payments','finance.direct_expenses','finance.postings','finance.posting_entries','inventory.events','inventory.movements','harvest.worker_reports','harvest.depot_settlements')
def effects_snapshot(db):
 return {table:[dict(row) for row in db.execute(text('SELECT * FROM '+table+' ORDER BY id')).mappings()] for table in TABLES}
if sys.argv[1]=='--before':
 with sf() as db:
  db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
  Path(sys.argv[2]).parent.mkdir(parents=True,exist_ok=True)
  Path(sys.argv[2]).write_text(json.dumps(effects_snapshot(db),sort_keys=True,default=str))
 print('OVERNIGHT_WORKERS_BEFORE_SNAPSHOT');sys.exit(0)
r=json.loads(Path(sys.argv[1]).read_text());assert r['status']=='PASS' and r['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA'] and not r['errors']
assert {x['engine'] for x in r['records']}=={'chromium','webkit'}
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test');sf=make_session_factory(url);proof={'sha':r['sha'],'cases':[]}
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
 assert json.loads(json.dumps(effects_snapshot(db),sort_keys=True,default=str))==json.loads(Path(sys.argv[3]).read_text()),'Worker journey changed accounting, stock or harvest rows'
 proof['accounting_inventory_harvest_rows_unchanged']=True
 for x in r['records']:
  p={'e':UUID(x['entity_id']),'t':UUID(x['task_id']),'w':UUID(x['worker_id'])}
  def rows(sql):return list(db.execute(text(sql),p).mappings())
  task=rows('SELECT * FROM workers.tasks WHERE id=:t AND entity_id=:e')[0]
  assert task['execution_contract']=='R2_RECEIVED_REPORT' and task['cycle']==2 and task['status']=='completed'
  reports=rows('SELECT * FROM workers.reports WHERE task_id=:t AND entity_id=:e ORDER BY received_at')
  actions=rows('SELECT * FROM workers.actions WHERE task_id=:t AND entity_id=:e ORDER BY received_at')
  sources=rows("SELECT * FROM workers.sources WHERE entity_id=:e AND source='worker:r2-ui' AND snapshot->>'task_id'=CAST(:t AS text) ORDER BY received_at")
  assert len(sources)==x['expected_responses']==5 and len(reports)==x['expected_reports']==3
  assert [s['snapshot']['response'] for s in sources]==['PROBLEM','RECEIVED','DONE','RECEIVED','DONE']
  assert all(s['event_type']=='r2_ui_response' and s['snapshot']['source_kind']=='UI_ACTION' and s['snapshot']['worker_id']==str(p['w']) for s in sources)
  assert len({s['locator'] for s in sources})==5
  first=sources[0]['snapshot'];assert first['button_label']=='Проблема' and first['comment']=='TEST проблема: клапан закрыт. Не включал насос.'
  done=[s for s in sources if s['locator']==x['done_response_id']];assert len(done)==1 and done[0]['snapshot']['comment']=='TEST сделано: проверено 5 литров, насос не включал.'
  assert not any(a['kind']=='start' for a in actions)
  assert len([a for a in actions if a['kind']=='complete'])==2
  assert len([a for a in actions if a['kind']=='reopen'])==1
  assert len([a for a in actions if a['kind']=='review_report'])==1
  assert len([a for a in actions if a['source']=='worker:r2-ui:action'])==5
  assert all(v['state']=='submitted' and v['worker_id']==p['w'] for v in reports)
  assert not rows('SELECT * FROM workers.links WHERE task_id=:t AND entity_id=:e')
  legacy=db.execute(text('SELECT * FROM workers.tasks WHERE id=:id AND entity_id=:e'),dict(id=UUID(x['legacy_task_id']),e=p['e'])).mappings().one()
  assert legacy['execution_contract']=='R1' and legacy['status']=='reported'
  legacy_starts=db.execute(text("SELECT count(*) FROM workers.actions WHERE task_id=:id AND kind='start'"),dict(id=UUID(x['legacy_task_id']))).scalar_one();assert legacy_starts==1
  dari=db.execute(text("SELECT snapshot FROM workers.sources WHERE source='worker:r2-ui' AND snapshot->>'task_id'=:id AND entity_id=:e"),dict(id=x['dari_task_id'],e=p['e'])).scalar_one()
  assert dari['original_language']=='fa' and dari['button_label']=='مشکل' and dari['comment']=='پمپ خاموش است' and dari['response']=='PROBLEM'
  proof['cases'].append({'engine':x['engine'],'task_id':str(p['t']),'responses':5,'reports':3,'cycles':[1,2],'starts':0,'owner_completions':2,'original_comments_exact':True,'effect_links':0})
 db.rollback()
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));proof['reconciliation']=reconcile(db,include_classification=True,snapshot_established=True,entity_id=p['e']);assert proof['reconciliation']['ok'],proof['reconciliation']
proof['status']='PASS';Path(sys.argv[2]).write_text(json.dumps(proof,indent=2,default=str));print('OVERNIGHT_WORKERS_SQL_PASS')
