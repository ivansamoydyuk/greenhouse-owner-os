"""AUD-003 personal task reachability/state lifecycle on real PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from greenhouse_owner_os.domain.errors import ConflictError, ValidationError
from greenhouse_owner_os.domain.owner_capture import TaskStateAction
from greenhouse_owner_os.infrastructure.models import ActorRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_owner_capture import source, queue, revise, confirm
from tests.integration.test_postgres_owner_review import other_owner


def record(case, **data):
    """Optional raw evidence export; synthetic fixtures only, never substitute DB."""
    if path := os.environ.get('PERSONAL_TASK_EVIDENCE'):
        with Path(path).open('a') as f:
            f.write(json.dumps(dict(case=case, **data), default=str, ensure_ascii=False)+'\n')


def root(i):
    return f"/v1/entities/{i['e']}/owner/tasks"


def page(i, **params):
    response = i['c'].get(root(i), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def detail(i, item):
    response = i['c'].get(root(i)+'/'+item)
    assert response.status_code == 200, response.text
    return response.json()


def state(i, item, version, value, key=None, expected=200):
    return send(i['c'], root(i)[3:]+'/'+item+'/state', dict(expected_version=version, state=value), key=key, expected=expected)


def make_tasks(i, count=5, undated=2):
    tasks = []
    for n in range(count):
        raw = f'Для меня сегодня проверить давление в Имамлы TEST {n}'
        saved = source(i, raw)
        proposal = next(p for p in queue(i) if p['inbox_id'] == saved['id'] and p['action_type'] == 'PERSONAL_TASK')
        proposal = revise(i, proposal, dict(title=f'TEST личная задача {n+1}', original_instruction=raw,
            greenhouse_id=i['g'], planned_date=None if n>=count-undated else '2026-10-10',
            note=f'TEST отдельная заметка {n+1}', category='irrigation',
            relative_time_context='без установленной даты' if n>=count-undated else 'сегодня'))
        assert proposal['state'] == 'READY'
        tasks.append(confirm(i, proposal)['effect'])
    return tasks


def immutable_snapshot(i):
    with i['preview'][0]() as db:
        out = {}
        for table in ('core.inbox_items', 'core.proposed_actions', 'core.proposal_revisions', 'core.confirmation_effects'):
            out[table] = db.execute(text(f'SELECT to_jsonb(t) FROM {table} t WHERE entity_id=:e ORDER BY to_jsonb(t)::text'), dict(e=i['e'])).scalars().all()
        for table in ('workers.tasks', 'workers.assignments', 'finance.postings', 'inventory.movements', 'harvest.worker_reports', 'harvest.depot_settlements'):
            out[table] = db.scalar(text(f'SELECT count(*) FROM {table} WHERE entity_id=:e'), dict(e=i['e']))
        out['task_facts'] = db.execute(text('SELECT id,entity_id,owner_id,proposal_id,title,greenhouse_id,planned_date,note,created_at FROM core.owner_tasks WHERE entity_id=:e ORDER BY id'), dict(e=i['e'])).all()
        return out


def write_snapshot(i, item):
    with i['preview'][0]() as db:
        return dict(
            task=db.execute(text('SELECT to_jsonb(t) FROM core.owner_tasks t WHERE id=:t'), dict(t=item)).scalar_one(),
            actions=db.execute(text('SELECT to_jsonb(t) FROM core.owner_task_actions t WHERE task_id=:t ORDER BY version'), dict(t=item)).scalars().all(),
            audit=db.execute(text("SELECT to_jsonb(t) FROM audit.events t WHERE entity_id=:t AND action='owner_task_state' ORDER BY occurred_at,id"), dict(t=item)).scalars().all(),
            replay=db.execute(text("SELECT to_jsonb(t) FROM audit.idempotency_keys t WHERE resource_id=:t AND operation='owner_task_state' ORDER BY created_at,id"), dict(t=item)).scalars().all())


def test_personal_task_five_reachable_pagination_context_and_read_no_effect(inv):
    tasks = make_tasks(inv)
    before = immutable_snapshot(inv)
    with inv['preview'][0]() as db:
        read_audit = db.scalar(text('SELECT count(*) FROM audit.events WHERE actor_id=:a'), dict(a=inv['preview'][1]))
    home = inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()
    assert home['task_count'] == 5 and len(home['tasks']) == 3
    first = page(inv, limit=3)
    second = page(inv, offset=first['next_offset'], limit=3)
    assert first['counts'] == {'TO_DO': 5, 'DONE': 0} and first['total'] == 5
    assert first['next_offset'] == 3 and second['next_offset'] is None
    all_tasks = first['items'] + second['items']
    assert [t['id'] for t in all_tasks] == [t['id'] for t in tasks]
    assert len({t['id'] for t in all_tasks}) == 5
    assert all(t['planned_date'] is None for t in second['items'])
    fourth = detail(inv, second['items'][0]['id'])
    assert fourth['id'] == tasks[3]['id'] and fourth['state'] == 'TO_DO'
    assert fourth['category'] == 'irrigation' and fourth['relative_time_context'] == 'без установленной даты'
    assert fourth['original_instruction'] == 'Для меня сегодня проверить давление в Имамлы TEST 3'
    assert fourth['title'] == 'TEST личная задача 4' and fourth['note'] == 'TEST отдельная заметка 4'
    assert [(a['version'], a['state']) for a in fourth['actions']] == [(1, 'TO_DO')]
    assert page(inv, offset=5)['items'] == []
    assert page(inv, offset=10**30)['items'] == []
    assert page(inv, state='DONE')['items'] == []
    assert inv['c'].get(root(inv)+'/'+str(uuid4())).status_code == 404
    assert immutable_snapshot(inv) == before
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM audit.events WHERE actor_id=:a'), dict(a=inv['preview'][1])) == read_audit
    record('five_reachable', first=first, second=second, fourth=fourth, immutable_and_no_write=True)


def test_fourth_task_done_reload_completed_restore_same_identity_audit_replay(inv):
    tasks = make_tasks(inv)
    item = tasks[3]['id']
    before = immutable_snapshot(inv)
    done = state(inv, item, 1, 'DONE', key='fourth-done')
    assert done['state'] == 'DONE' and done['version'] == 2
    committed_done = write_snapshot(inv, item)
    assert state(inv, item, 1, 'DONE', key='fourth-done') == done
    assert write_snapshot(inv, item) == committed_done
    state(inv, item, 1, 'TO_DO', key='stale-restore', expected=409)
    assert write_snapshot(inv, item) == committed_done
    assert item not in {t['id'] for t in page(inv)['items']}
    assert [t['id'] for t in page(inv, state='DONE')['items']] == [item]
    assert detail(inv, item)['version'] == 2
    restored = state(inv, item, 2, 'TO_DO', key='explicit-restore')
    committed_restored = write_snapshot(inv, item)
    assert restored['id'] == item and restored['version'] == 3 and restored['state'] == 'TO_DO'
    assert state(inv, item, 2, 'TO_DO', key='explicit-restore') == restored
    assert write_snapshot(inv, item) == committed_restored
    # A delayed retry of the original DONE request can replay its old response,
    # but can never reapply it after the explicit restore.
    assert state(inv, item, 1, 'DONE', key='fourth-done') == done
    assert write_snapshot(inv, item) == committed_restored
    state(inv, item, 2, 'DONE', key='stale-done', expected=409)
    assert write_snapshot(inv, item) == committed_restored
    read = detail(inv, item)
    assert [(a['version'], a['state']) for a in read['actions']] == [(1, 'TO_DO'), (2, 'DONE'), (3, 'TO_DO')]
    assert len(committed_restored['audit']) == len(committed_restored['replay']) == 2
    assert [a['after_json']['version'] for a in committed_restored['audit']] == [2, 3]
    assert page(inv, state='DONE')['items'] == [] and page(inv)['counts'] == {'TO_DO': 5, 'DONE': 0}
    assert len(inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()['tasks']) == 3
    assert immutable_snapshot(inv) == before
    record('fourth_done_restore_replay', done=committed_done, restored=committed_restored, immutable_preserved=True)


def test_personal_task_all_undated_and_completed_only_are_reachable(inv):
    tasks = make_tasks(inv, count=2, undated=2)
    assert len(page(inv)['items']) == 2 and all(t['planned_date'] is None for t in page(inv)['items'])
    for task in tasks:
        state(inv, task['id'], 1, 'DONE')
    assert page(inv)['items'] == [] and page(inv)['counts'] == {'TO_DO': 0, 'DONE': 2}
    first = page(inv, state='DONE', limit=1)
    last = page(inv, state='DONE', limit=1, offset=first['next_offset'])
    assert {first['items'][0]['id'], last['items'][0]['id']} == {t['id'] for t in tasks}
    assert last['next_offset'] is None
    assert detail(inv, last['items'][0]['id'])['state'] == 'DONE'


def test_personal_task_order_has_id_tiebreaker_for_legacy_equal_timestamps(inv):
    ids = sorted([uuid4(), uuid4(), uuid4()], key=str)
    with inv['preview'][0].begin() as db:
        for item in reversed(ids):
            db.execute(text("INSERT INTO core.owner_tasks(id,entity_id,owner_id,title,state,created_at) VALUES(:i,:e,:a,'TEST legacy undated tie','TO_DO','2026-10-10T00:00:00Z')"), dict(i=item, e=inv['e'], a=inv['preview'][1]))
            db.execute(text("INSERT INTO core.owner_task_actions(id,entity_id,task_id,version,actor_id,state) VALUES(:id,:e,:i,1,:a,'TO_DO')"), dict(id=uuid4(), i=item, e=inv['e'], a=inv['preview'][1]))
    read_ids = [page(inv, offset=n, limit=1)['items'][0]['id'] for n in range(3)]
    assert read_ids == [str(item) for item in ids]
    assert detail(inv, read_ids[0])['original_instruction'] is None


def test_personal_task_context_ignores_newer_unapproved_revision(inv):
    task = make_tasks(inv, count=1, undated=1)[0]
    item = task['id']
    approved = detail(inv, item)
    with inv['preview'][0].begin() as db:
        version = db.scalar(text('SELECT version FROM core.confirmation_effects WHERE owner_task_id=:t'), dict(t=item))
        # Append an inert adversarial revision under the existing SQL guards.
        # Do not update the posted proposal, approved revision, authority action,
        # confirmation effect, or task. An unapproved append is not new meaning.
        db.execute(text("""INSERT INTO core.proposal_revisions
            (proposal_id,entity_id,version,schema_version,extraction_run_id,payload,provenance,missing,basis,preview_digest,actor_id,reason)
            SELECT proposal_id,entity_id,version+1,schema_version,NULL,
                payload || '{"category":"UNAPPROVED","relative_time_context":"UNAPPROVED","original_instruction":"UNAPPROVED"}'::jsonb,
                provenance,missing,basis,repeat('0',64),actor_id,'TEST inert unapproved append'
            FROM core.proposal_revisions WHERE proposal_id=:p AND version=:v"""), dict(p=task['proposal_id'], v=version))
        assert db.scalar(text('SELECT max(version) FROM core.proposal_revisions WHERE proposal_id=:p'), dict(p=task['proposal_id'])) == version+1
        assert db.scalar(text('SELECT version FROM core.confirmation_effects WHERE owner_task_id=:t'), dict(t=item)) == version
    assert detail(inv, item) == approved
    listed = page(inv)['items'][0]
    for field in ('category', 'relative_time_context', 'original_instruction'):
        assert listed[field] == approved[field] and listed[field] != 'UNAPPROVED'
    home = inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()
    assert home['tasks'][0]['original_instruction'] == approved['original_instruction']
    record('approved_revision_binding', approved_version=version, unapproved_version=version+1, detail=approved, listed=listed)


def test_personal_task_other_owner_entity_and_worker_cannot_read_or_write(inv):
    item = make_tasks(inv, count=1, undated=1)[0]['id']
    before = write_snapshot(inv, item)
    actor, client = other_owner(inv)
    for path in (root(inv), root(inv)+'/'+item):
        assert client.get(path).status_code in (403, 422)
    assert client.post(root(inv)+'/'+item+'/state', json=dict(expected_version=1, state='DONE'), headers={'Idempotency-Key': 'forbidden'}).status_code in (403, 422)
    own = send(client, '/entities', dict(name='TEST separate personal owner '+str(uuid4()), currency='TRY', source='test:personal-access'))
    own_root = f"/v1/entities/{own['id']}/owner/tasks"
    assert client.get(own_root).json()['items'] == []
    assert client.get(own_root+'/'+item).status_code == 404
    assert client.post(own_root+'/'+item+'/state', json=dict(expected_version=1, state='DONE'), headers={'Idempotency-Key': 'wrong-entity'}).status_code == 404
    sf, owner, *_ = inv['preview']
    worker = uuid4()
    with sf.begin() as db:
        db.add(ActorRow(id=worker, actor_type='worker', display_name='TEST task scope worker'))
    service = OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf))
    for stranger in (actor, worker):
        with pytest.raises(ValidationError):
            service.tasks(UUID(inv['e']), stranger)
        with pytest.raises(ValidationError):
            service.task(UUID(inv['e']), stranger, UUID(item))
        with pytest.raises(ValidationError):
            service.task_state(UUID(inv['e']), stranger, UUID(item), 'wrong-actor', TaskStateAction(expected_version=1, state='DONE'))
    assert write_snapshot(inv, item) == before
    record('owner_entity_worker_denied', task=item, before=before, after=write_snapshot(inv, item))


def test_personal_task_concurrent_done_has_one_transition_and_stale_restore_is_denied(inv):
    item = make_tasks(inv, count=1, undated=1)[0]['id']
    sf, actor, *_ = inv['preview']
    service = OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf))
    barrier = Barrier(2)

    def run(key):
        barrier.wait()
        try:
            return service.task_state(UUID(inv['e']), actor, UUID(item), key, TaskStateAction(expected_version=1, state='DONE'))
        except ConflictError:
            return 'conflict'

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, ['concurrent-done-a', 'concurrent-done-b']))
    assert results.count('conflict') == 1
    assert next(r for r in results if r != 'conflict')['version'] == 2
    written = write_snapshot(inv, item)
    assert len(written['actions']) == 2 and len(written['audit']) == len(written['replay']) == 1
    state(inv, item, 1, 'TO_DO', expected=409)
    assert write_snapshot(inv, item) == written
    state(inv, item, 2, 'TO_DO')
    assert detail(inv, item)['version'] == 3
    record('concurrent_done', results=results, committed=written, restored=write_snapshot(inv, item))


def test_personal_task_same_state_noop_retains_existing_request_audit_semantics(inv):
    item = make_tasks(inv, count=1, undated=1)[0]['id']
    before = write_snapshot(inv, item)
    result = state(inv, item, 1, 'TO_DO', key='same-state')
    after = write_snapshot(inv, item)
    assert result['version'] == 1 and result['state'] == 'TO_DO'
    assert after['task'] == before['task'] and after['actions'] == before['actions']
    assert len(after['audit']) == len(after['replay']) == 1
    assert state(inv, item, 1, 'TO_DO', key='same-state') == result
    assert write_snapshot(inv, item) == after


@pytest.mark.parametrize('read_kind', ['list', 'detail'])
def test_personal_task_read_uses_one_read_only_repeatable_snapshot(inv, monkeypatch, read_kind):
    import greenhouse_owner_os.services.owner_capture as module
    item = make_tasks(inv, count=1, undated=1)[0]['id']
    sf, actor, *_ = inv['preview']
    service = OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf))
    actual_rows = module.rows
    armed = True
    settings = []

    def rows_with_concurrent_commit(u, sql, **params):
        nonlocal armed
        result = actual_rows(u, sql, **params)
        target = 'count(*) FILTER' in sql if read_kind == 'list' else sql.startswith(module.PERSONAL_TASK_SELECT)
        if armed and target:
            armed = False
            settings.append(u.session.execute(text("SELECT current_setting('transaction_isolation'),current_setting('transaction_read_only')")).one())
            # Real second transaction, committed between the two read queries.
            # No data-store or query-result replacement is used by this hook.
            service.task_state(UUID(inv['e']), actor, UUID(item), 'snapshot-concurrent-done', TaskStateAction(expected_version=1, state='DONE'))
        return result

    monkeypatch.setattr(module, 'rows', rows_with_concurrent_commit)
    if read_kind == 'list':
        response = service.tasks(UUID(inv['e']), actor)
        assert response['counts'] == {'TO_DO': 1, 'DONE': 0} and response['total'] == 1
        assert len(response['items']) == 1 and response['items'][0]['state'] == 'TO_DO'
    else:
        response = service.task(UUID(inv['e']), actor, UUID(item))
        assert response['state'] == 'TO_DO' and response['version'] == 1
        assert [(a['version'], a['state']) for a in response['actions']] == [(1, 'TO_DO')]
    assert settings == [('repeatable read', 'on')]
    assert service.task(UUID(inv['e']), actor, UUID(item))['state'] == 'DONE'
    record('repeatable_snapshot_'+read_kind, snapshot=response, settings=settings)


@pytest.mark.parametrize('mutation', [
    "UPDATE core.owner_tasks SET title='mutated',version=version+1 WHERE id=:t",
    "UPDATE core.owner_task_actions SET state='DONE' WHERE task_id=:t",
    'DELETE FROM core.owner_task_actions WHERE task_id=:t',
])
def test_personal_task_existing_fact_and_action_immutability_remains(inv, mutation):
    item = make_tasks(inv, count=1, undated=1)[0]['id']
    before = write_snapshot(inv, item)
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            db.execute(text(mutation), dict(t=item))
    assert write_snapshot(inv, item) == before
