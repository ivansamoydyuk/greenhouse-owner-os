"""Synthetic task audience setup and independent, read-only PostgreSQL proof.

Only --prepare creates an explicitly synthetic Russian-speaking worker through
an authenticated owner service. Browser controls own every source/task effect.
No authentication rows or credentials are serialized into evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.services.reconciliation import reconcile

CASES = [
    dict(id='EXCERPT_1', text='Задача номер 1 - порезать листья старые деревья И навести порядок.', audience='worker'),
    dict(id='EXCERPT_2', text='Задача номер 2- переставить фитинги для полива также как мы сделали во второй и третей части.', audience='personal'),
    dict(id='EXCERPT_3', text='Задача номер 3 - почистить фитинги для полива.', audience='worker'),
    dict(id='EXPLICIT_PERSONAL', text='Для меня завтра проверить насос в TEST Теплица А', audience=None),
    dict(id='VERSIONED_RECOVERY', text='Моя задача: проверить насос', audience='worker'),
    dict(id='KNOWN_FACTS', text='Завтра проверить насос в TEST Теплица А', audience='worker'),
]
BUSINESS = ('finance.obligations','finance.payments','finance.direct_expenses','finance.postings',
    'finance.posting_entries','finance.payment_allocations','finance.advances','finance.owner_fundings',
    'finance.advance_applications','finance.direct_settlements','finance.own_account_transfers',
    'inventory.events','inventory.movements','inventory.stock','harvest.worker_reports',
    'harvest.depot_settlements','workers.assignments','workers.reports','workers.actions','workers.links',
    'workers.day_requests','workers.day_decisions','workers.day_actuals','workers.day_accruals')
HISTORY = ('core.inbox_items','core.proposal_revisions','core.owner_recovery_links')
TASKS = ('core.owner_tasks','core.owner_task_actions','workers.tasks','core.confirmation_effects')

def canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))

def write(path, value):
    target=Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(canonical(value), ensure_ascii=False, indent=2)+'\n')

def rows(db, query, **params):
    return canonical([dict(r) for r in db.execute(text(query),params).mappings()])

def scoped(db):
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    _require_connected_runtime(db)
    entities=db.execute(text('SELECT id FROM core.entities')).scalars().all()
    assert len(entities)==1, 'Owned dedicated synthetic entity required'
    return entities[0]

def snapshot(db, entity, sha, engine):
    # The dedicated synthetic database has exactly one entity. Child tables
    # without entity_id remain covered, including allocations and ledger rows.
    tables={table:rows(db,'SELECT * FROM '+table+' t ORDER BY to_jsonb(t)::text') for table in BUSINESS+TASKS}
    return dict(sha=sha, engine=engine, entity_id=str(entity), tables=tables)

def retained_history(db):
    return {table:[r['digest'] for r in rows(db,"SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') digest FROM "+table+' t ORDER BY digest')] for table in HISTORY}

def prepare(url, sf, credentials_path, sha, engine):
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.preview import build_preview
    private=Path(credentials_path).resolve()
    assert private.is_file() and 'artifacts' not in private.parts and not private.is_relative_to(Path(__file__).resolve().parents[1])
    owner=json.loads(private.read_text())['owner']
    app=build_preview(url,os.getenv('PREVIEW_SECRET') or secrets.token_urlsafe(32),environment='test')
    entity=app.state.pilot_auth.entity_id
    with TestClient(app) as client:
        headers={'Origin':'http://testserver'}
        response=client.post('/pilot/login',json={k:owner[k] for k in ('username','password')},headers=headers)
        assert response.status_code==200
        headers.update({'X-Pilot-Participant':owner['participant_id'],'Idempotency-Key':'task-audience-fixture:'+engine})
        response=client.post('/v1/entities/'+str(entity)+'/workers',json=dict(display_name='TEST Semantic '+engine+' worker',language='ru',source='test:task-audience-e2e'),headers=headers)
        assert response.status_code in (200,201), response.text
        worker=response.json()
    with sf() as db:
        assert scoped(db)==entity
        assert db.scalar(text('SELECT version_num FROM public.alembic_version'))=='20261012_0035'
        areas=rows(db,"SELECT id,name FROM core.work_areas WHERE entity_id=:e AND kind='greenhouse' ORDER BY name",e=entity)
        area=next(a for a in areas if a['name']=='TEST Теплица А')
    return dict(sha=sha,engine=engine,entity_id=str(entity),owner_id=str(app.state.pilot_auth.owner_id),worker_id=worker['id'],worker_language='ru',greenhouse_id=area['id'],greenhouse_name=area['name'],cases=CASES,synthetic_only=True)

def verify(sf, browser_path, baseline_path, sha, engine):
    path=Path(browser_path); result=json.loads(path.read_text()); before=json.loads(Path(baseline_path).read_text())
    assert result['transport']=='actual_browser_controls'
    assert result['status']=='PASS' and result['sha']==sha and result['engine']==engine and not result['errors']
    assert [c['id'] for c in result['cases']]==[c['id'] for c in CASES]
    assert len(result['layouts'])==64 and {row['width'] for row in result['layouts']}=={320,390,768,1280}
    assert len(result['primary_actions'])==11 and all(row['width']==320 and all(row['hits']) and row['enabled'] for row in result['primary_actions'])
    assert all(row['trusted'] for row in result['operations'] if row['kind']=='click')
    setup=json.loads((path.parent/'setup.json').read_text())
    with sf() as db:
        entity=scoped(db); assert str(entity)==setup['entity_id']==before['entity_id']==result['entity_id']
        after=snapshot(db,entity,sha,engine)
        history=retained_history(db)
        for table,digests in before['retained_history'].items():
            assert all(value in history[table] for value in digests), 'Retained original/history changed: '+table
        for table in BUSINESS: assert after['tables'][table]==before['tables'][table], 'Unexpected non-task effect: '+table
        # Every task/effect present before this engine is byte-for-byte retained.
        for table in TASKS:
            for row in before['tables'][table]: assert row in after['tables'][table], 'Retained task/effect changed: '+table
        source_ids={r['id'] for r in rows(db,"SELECT id FROM core.inbox_items WHERE entity_id=:e AND source='owner:r2'",e=entity)}
        assert source_ids-set(before['source_ids'])=={c['source_id'] for c in result['cases']}, 'Extra or duplicate source created'
        expected={t:set() for t in TASKS}; proof=[]
        for fixture,case in zip(CASES,result['cases'],strict=True):
            assert case['status']=='PASS' and case['text']==fixture['text']
            source=rows(db,'SELECT * FROM core.inbox_items WHERE entity_id=:e AND id=:s',e=entity,s=UUID(case['source_id']))
            assert len(source)==1 and source[0]['raw_text']==fixture['text'] and source[0]['file_bytes'] is None
            assert source[0]['source']=='owner:r2' and source[0]['created_by']==setup['owner_id']
            assert hashlib.sha256(fixture['text'].encode()).hexdigest()==case['original_sha256']
            origin=rows(db,'SELECT * FROM core.proposed_actions WHERE entity_id=:e AND id=:p',e=entity,p=UUID(case['origin_id']))[0]
            first=rows(db,'SELECT * FROM core.proposal_revisions WHERE proposal_id=:p AND version=1',p=UUID(case['origin_id']))[0]
            assert first['payload']==case['initial_payload'] and origin['action_type']==case['initial_type']
            assert origin['inbox_id']==case['source_id'] and origin['created_by']==setup['owner_id']
            if fixture['id'].startswith('EXCERPT') or fixture['id']=='KNOWN_FACTS':
                assert origin['action_type']=='UNRESOLVED' and first['payload']['task_candidates']['original_instruction']==fixture['text']
            else: assert origin['action_type']=='PERSONAL_TASK'
            links=rows(db,'SELECT * FROM core.owner_recovery_links WHERE entity_id=:e AND origin_proposal_id=:p',e=entity,p=UUID(case['origin_id']))
            if fixture['audience']:
                assert len(links)==1 and links[0]['recovery_proposal_id']==case['proposal_id'] and links[0]['inbox_id']==case['source_id']
                assert links[0]['created_by']==setup['owner_id'] and links[0]['origin_version']==case['initial_version']
                assert case['proposal_id']!=case['origin_id']
                assert not rows(db,'SELECT * FROM core.confirmation_effects WHERE proposal_id=:p',p=UUID(case['origin_id']))
            else: assert links==[] and case['proposal_id']==case['origin_id']
            proposals=rows(db,'SELECT id FROM core.proposed_actions WHERE inbox_id=:s',s=UUID(case['source_id']))
            assert {row['id'] for row in proposals}=={case['origin_id'],case['proposal_id']}, 'Unexpected extra proposal in source family'
            proposal=rows(db,'SELECT * FROM core.proposed_actions WHERE id=:p',p=UUID(case['proposal_id']))[0]
            assert proposal['inbox_id']==case['source_id'] and proposal['created_by']==setup['owner_id']
            effect=rows(db,'SELECT * FROM core.confirmation_effects WHERE proposal_id=:p',p=UUID(case['proposal_id']))
            assert len(effect)==1; effect=effect[0]; expected['core.confirmation_effects'].add(case['proposal_id'])
            confirmations=rows(db,"SELECT * FROM core.proposal_actions WHERE proposal_id=:p AND action='CONFIRM'",p=UUID(case['proposal_id']))
            assert len(confirmations)==1
            is_worker=fixture['audience']=='worker'; table='workers.tasks' if is_worker else 'core.owner_tasks'
            assert proposal['action_type']==('WORKER_TASK' if is_worker else 'PERSONAL_TASK')
            assert effect['worker_task_id' if is_worker else 'owner_task_id']==case['task_id']
            task=rows(db,'SELECT * FROM '+table+' WHERE id=:id',id=UUID(case['task_id']))[0]
            assert task['title']==fixture['text'][:200]
            if is_worker:
                assert task['original_instruction']==fixture['text'] and task['description']==fixture['text']
                assert task['status']=='draft' and task['greenhouse_id']==setup['greenhouse_id']
                assert task['source']=='owner:r2-action' and task['locator']==case['proposal_id']
                assert case['effect']['not_sent'] is True
            else:
                assert task['state']=='TO_DO' and task['proposal_id']==case['proposal_id']
                actions=rows(db,'SELECT * FROM core.owner_task_actions WHERE task_id=:id',id=UUID(case['task_id']))
                assert len(actions)==1; expected['core.owner_task_actions'].add(actions[0]['id'])
            expected[table].add(case['task_id'])
            stages={label:json.loads((path.parent/file).read_text()) for label,file in case['stages'].items()}
            assert stages['before']==stages['prepared']==stages['ready'], 'Capture, choice or ordinary review created a task before confirmation'
            assert stages['confirmed']==stages['reloaded'], 'Reload/recovery created duplicate effects'
            if 'lost' in stages: assert stages['lost']==stages['confirmed'] and case['replay_same_command'], 'Same-command confirmation replay created an effect'
            if fixture['id']=='KNOWN_FACTS': assert stages['audience-lost']==stages['before'] and case['audience_lost_reply_reload_same_target']
            proof.append(dict(id=case['id'],source_id=case['source_id'],original_immutable=True,origin_type=origin['action_type'],proposal_id=case['proposal_id'],task_id=case['task_id'],audience=fixture['audience'] or 'explicit_personal',one_effect=True,not_sent=is_worker))
        for table,ids in expected.items():
            key='proposal_id' if table=='core.confirmation_effects' else 'id'
            old={r[key] for r in before['tables'][table]}; new={r[key] for r in after['tables'][table]}
            assert new-old==ids, 'Unaccounted task/effect: '+table
        reconciliation=reconcile(db,include_classification=True,snapshot_established=True)
        assert reconciliation['ok'] and not reconciliation['findings']
        return dict(status='PASS',sha=sha,engine=engine,cases=proof,business_tables_unchanged=list(BUSINESS),retained_tasks_unchanged=True,reconciliation=reconciliation)

def main():
    p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--credentials');p.add_argument('--before');p.add_argument('--probe',action='store_true');p.add_argument('--verify');p.add_argument('--baseline');p.add_argument('--output');a=p.parse_args()
    assert sum(bool(v) for v in (a.prepare,a.before,a.probe,a.verify))==1
    url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test')
    assert os.environ.get('APP_ENV')=='test' and os.environ.get('R3_DAILY_PILOT')=='1' and os.environ.get('PILOT_LOCAL_TEST')=='1'
    sha=os.environ['OWNER_PREVIEW_FEATURE_SHA']; engine=os.environ['TASK_AUDIENCE_ENGINE'];assert engine in ('chromium','webkit')
    sf=make_session_factory(url)
    if a.prepare: value=prepare(url,sf,a.credentials,sha,engine)
    elif a.verify: value=verify(sf,a.verify,a.baseline,sha,engine)
    else:
        with sf() as db:
            value=snapshot(db,scoped(db),sha,engine)
            if a.before:
                value['retained_history']=retained_history(db)
                value['source_ids']=[r['id'] for r in rows(db,"SELECT id FROM core.inbox_items WHERE entity_id=:e AND source='owner:r2' ORDER BY id",e=UUID(value['entity_id']))]
    write(a.before or a.output,value);print('TASK_AUDIENCE_'+('SQL_PASS' if a.verify else 'EVIDENCE_READY'))

if __name__=='__main__': main()
