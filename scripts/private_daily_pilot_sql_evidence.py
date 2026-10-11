"""Synthetic offline fixture and read-only proof for private daily TEST browser journeys."""
import argparse,json,os,secrets,hashlib
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from fastapi.testclient import TestClient
from greenhouse_owner_os.preview import build_preview,_require_connected_runtime
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.services.reconciliation import reconcile
p=argparse.ArgumentParser();p.add_argument('--before');p.add_argument('--prepare');p.add_argument('--credentials');p.add_argument('--verify');p.add_argument('--output');p.add_argument('--baseline');a=p.parse_args()
url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test');sf=make_session_factory(url)
TABLES=('finance.obligations','finance.payments','finance.direct_expenses','finance.postings','finance.posting_entries','inventory.events','inventory.movements','harvest.worker_reports','harvest.depot_settlements')
def snapshot(db):return {table:[dict(r) for r in db.execute(text('SELECT * FROM '+table+' r ORDER BY to_jsonb(r)::text')).mappings()] for table in TABLES}
def canonical(x):return json.loads(json.dumps(x,sort_keys=True,default=str))
if a.before:
 with sf() as db:_require_connected_runtime(db);before=canonical(snapshot(db))
 Path(a.before).write_text(json.dumps(before,indent=2));print('PRIVATE_DAILY_BEFORE_READY');raise SystemExit
if a.prepare:
 assert os.environ.get('R3_DAILY_PILOT')=='1' and os.environ.get('PILOT_LOCAL_TEST')=='1'
 out=Path(a.prepare).resolve();out.mkdir(parents=True,exist_ok=True);secret=Path(a.credentials).resolve();assert not secret.exists() and 'artifacts' not in secret.parts and not secret.is_relative_to(Path.cwd().resolve())
 app=build_preview(url,os.environ.get('PREVIEW_SECRET') or secrets.token_urlsafe(32),environment='test');auth=app.state.pilot_auth
 owner={'username':'pilot-owner','password':secrets.token_urlsafe(24)};owner['participant_id']=auth.provision(owner['username'],owner['password']);credentials={'owner':owner};fixtures={'sha':os.environ['OWNER_PREVIEW_FEATURE_SHA'],'entity_id':str(auth.entity_id),'deployment_id':str(auth.deployment_id),'expires_at':auth.expires_at.isoformat(),'synthetic_only':True}
 with TestClient(app) as c:
  h={'Origin':'http://testserver'};r=c.post('/pilot/login',json={'username':owner['username'],'password':owner['password']},headers=h);assert r.status_code==200,r.text
  h['X-Pilot-Participant']=owner['participant_id']
  for engine in ('chromium','webkit'):
   credentials[engine]={};fixtures[engine]={}
   for label in ('worker','other_worker'):
    payload={'display_name':'TEST Daily '+engine+' '+label,'language':'fa','source':'test:private-daily-pilot'}
    r=c.post('/v1/entities/'+str(auth.entity_id)+'/workers',json=payload,headers=h|{'Idempotency-Key':'pilot-fixture-'+engine+'-'+label});assert r.status_code in (200,201),r.text
    worker=r.json();credential={'username':engine+'-'+label.replace('_','-'),'password':secrets.token_urlsafe(24),'worker_id':worker['id']};credential['participant_id']=auth.provision(credential['username'],credential['password'],worker_id=worker['id']);credentials[engine][label]=credential;fixtures[engine][label]={k:v for k,v in credential.items() if k!='password'}
 secret.parent.mkdir(parents=True,exist_ok=True);fd=os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(credentials,f)
 (out/'fixtures.json').write_text(json.dumps(fixtures,indent=2))
 with sf() as db:_require_connected_runtime(db);before=canonical(snapshot(db))
 (out/'before.json').write_text(json.dumps(before,indent=2));print('PRIVATE_DAILY_PILOT_SYNTHETIC_FIXTURE_READY');raise SystemExit
if a.verify:
 r=json.loads(Path(a.verify).read_text());assert r['status']=='PASS' and r['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA'] and not r.get('errors');assert {x['engine'] for x in r['records']}=={'chromium','webkit'}
 proof={'sha':r['sha'],'cases':[]}
 with sf() as db:
  db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
  assert canonical(snapshot(db))==json.loads(Path(a.baseline).read_text()),'Dedicated auth/worker journey changed money,stock,harvest effects'
  for x in r['records']:
   p={'e':UUID(x['entity_id']),'source':UUID(x['source_id']),'task':UUID(x['task_id']),'worker':UUID(x['worker_id']),'participant':UUID(x['participant_id'])}
   original=db.execute(text('SELECT * FROM core.inbox_items WHERE id=:source AND entity_id=:e'),p).mappings().one()
   assert hashlib.sha256((original['raw_text'] or '').encode()).hexdigest()==x['source_hash'],'Original source text changed'
   participant=db.execute(text('SELECT * FROM core.pilot_participants WHERE id=:participant AND entity_id=:e AND worker_id=:worker'),p).mappings().one();assert not participant['enabled'] and participant['version']>=2
   assert participant['password_hash'].startswith('scrypt$') and participant['role']=='worker'
   reports=list(db.execute(text('SELECT * FROM workers.reports WHERE task_id=:task AND entity_id=:e AND worker_id=:worker'),p).mappings());assert any(str(z['id'])==x['report_id'] for z in reports)
   assert not db.scalar(text('SELECT count(*) FROM workers.links WHERE task_id=:task AND entity_id=:e'),p)
   assert not db.scalar(text('SELECT count(*) FROM core.pilot_sessions WHERE participant_id=:participant AND revoked_at IS NULL'),p)
   proof['cases'].append({'engine':x['engine'],'source_id':str(p['source']),'worker_id':str(p['worker']),'revoked':True,'reports':len(reports),'no_worker_accounting_effect':True})
  proof['effects_unchanged']=True;proof['reconciliation']=reconcile(db,include_classification=True,snapshot_established=True);assert proof['reconciliation']['ok'] and not proof['reconciliation']['findings']
 proof['status']='PASS';Path(a.output).write_text(json.dumps(proof,indent=2,default=str));print('PRIVATE_DAILY_PILOT_SQL_PASS')
