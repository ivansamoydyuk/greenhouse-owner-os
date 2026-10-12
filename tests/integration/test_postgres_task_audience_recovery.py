"""Audience clarification uses the original recovery/authority family on PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import UUID, uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ConflictError, ValidationError
from greenhouse_owner_os.domain.owner_capture import TaskRecoveryAction
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_owner_capture import source, queue, revise, confirm


def svc(i):
    return OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(i['preview'][0]))


def capture(i, raw='Завтра проверить насос в Имамлы'):
    saved = source(i, raw)
    return saved, next(p for p in queue(i) if p['inbox_id'] == saved['id'])


def recover(i, saved, p, audience='worker', key=None, expected=200):
    return send(i['c'], f"/entities/{i['e']}/owner/captures/{saved['id']}/task-recovery",
                dict(origin_proposal_id=p['id'], expected_version=p['version'], audience=audience), key=key, expected=expected)


def effects(i):
    with i['preview'][0]() as db:
        return {table: db.scalar(text(f'SELECT count(*) FROM {table} WHERE entity_id=:e'), dict(e=i['e']))
                for table in ('core.confirmation_effects', 'core.owner_tasks', 'workers.tasks', 'workers.assignments', 'finance.postings', 'inventory.movements')}


def test_ambiguous_capture_persists_instruction_and_only_audience_question(inv):
    raw = 'Задача номер 1 - порезать листья старые деревья И навести порядок.'
    saved, p = capture(inv, raw)
    assert p['action_type'] == 'UNRESOLVED' and p['state'] == 'NEEDS_INPUT'
    assert p['revision']['payload']['task_candidates']['original_instruction'] == raw
    assert p['revision']['missing'] == ['Для себя или работнику?']
    old = deepcopy(p['revision'])
    confirm(inv, p, expected=422)
    svc(inv).process(UUID(saved['id']))
    current = svc(inv).preview(UUID(inv['e']), inv['preview'][1], UUID(p['id']))
    assert current['revision'] == old and current['source']['text'] == raw
    assert not any(effects(inv).values())


def test_known_facts_survive_audience_choice_and_only_missing_worker_is_asked(inv):
    saved, origin = capture(inv)
    old = deepcopy(origin['revision'])
    target = recover(inv, saved, origin, key='audience')
    assert target['action_type'] == 'WORKER_TASK' and target['state'] == 'NEEDS_INPUT'
    assert target['revision']['missing'] == ['Работник']
    assert target['revision']['payload']['greenhouse_id'] == inv['g']
    assert target['revision']['payload']['planned_date'] == '2026-10-09'
    assert target['revision']['payload']['original_instruction'] == saved['text']
    assert target['revision']['provenance']['original_instruction']['category'] == 'SOURCE FACT'
    assert target['revision']['extraction_run_id'] == old['extraction_run_id']
    assert recover(inv, saved, origin, key='audience') == target
    assert recover(inv, saved, origin, key='another-key')['id'] == target['id']
    recover(inv, saved, origin, audience='personal', expected=409)
    confirm(inv, origin, expected=409)
    assert [p['id'] for p in queue(inv)] == [target['id']]
    assert not any(effects(inv).values())
    assert svc(inv).preview(UUID(inv['e']), inv['preview'][1], UUID(origin['id']))['revision'] == old


def test_worker_confirmation_creates_only_unassigned_draft_and_retires_original(inv):
    saved, origin = capture(inv)
    target = recover(inv, saved, origin)
    worker = send(inv['c'], f"/entities/{inv['e']}/workers", dict(display_name='Умар', language='ru', source='test:audience'))
    target = revise(inv, target, dict(target['revision']['payload'], worker_id=worker['id'], language='ru'))
    assert target['revision']['provenance']['worker_id']['category'] == 'OWNER INPUT'
    assert target['revision']['provenance']['original_instruction']['category'] == 'SOURCE FACT'
    done = confirm(inv, target, key='worker-confirm')
    assert done['state'] == 'CONFIRMED_NONPOSTING' and done['effect']['not_sent']
    assert confirm(inv, target, key='worker-confirm-again') == done
    count = effects(inv)
    assert count['workers.tasks'] == count['core.confirmation_effects'] == 1
    assert count['workers.assignments'] == count['core.owner_tasks'] == count['finance.postings'] == count['inventory.movements'] == 0
    retired = svc(inv).preview(UUID(inv['e']), inv['preview'][1], UUID(origin['id']))
    assert retired['state'] == 'REJECTED' and retired['version'] == origin['version'] + 1
    assert retired['revision']['payload'] == origin['revision']['payload']
    assert retired['revision']['provenance'] == origin['revision']['provenance']
    recover(inv, saved, target, audience='personal', expected=409)


def test_personal_choice_requires_own_confirmation_and_existing_task_never_converts(inv):
    saved, origin = capture(inv)
    target = recover(inv, saved, origin, audience='personal')
    assert target['action_type'] == 'PERSONAL_TASK' and target['state'] == 'READY'
    assert not any(effects(inv).values())
    done = confirm(inv, target)
    snapshot = svc(inv).task(UUID(inv['e']), inv['preview'][1], UUID(done['effect']['id']))
    recover(inv, saved, target, audience='worker', expected=409)
    svc(inv).process(UUID(saved['id']))
    assert svc(inv).task(UUID(inv['e']), inv['preview'][1], UUID(done['effect']['id'])) == snapshot
    assert effects(inv)['workers.tasks'] == 0


def test_legacy_unconfirmed_personal_can_explicitly_correct_without_reparsing(inv, monkeypatch):
    import greenhouse_owner_os.services.owner_preparation as preparation
    with monkeypatch.context() as m:
        m.setattr(preparation, 'task_audience', lambda *a, **k: 'PERSONAL_TASK')
        saved, origin = capture(inv)
    assert origin['action_type'] == 'PERSONAL_TASK'
    origin = revise(inv, origin, dict(origin['revision']['payload'], title='Owner edited title', note='Keep this reviewed note'))
    old = deepcopy(origin['revision'])
    svc(inv).process(UUID(saved['id']))
    assert svc(inv).preview(UUID(inv['e']), inv['preview'][1], UUID(origin['id']))['revision'] == old
    target = recover(inv, saved, origin)
    assert target['action_type'] == 'WORKER_TASK'
    assert target['revision']['payload']['title'] == 'Owner edited title'
    assert target['revision']['payload']['note'] == 'Keep this reviewed note'
    assert target['revision']['provenance']['title']['category'] == 'OWNER INPUT'
    assert target['revision']['payload']['original_instruction'] == saved['text']
    assert not any(effects(inv).values())


def test_legacy_confirmed_personal_stays_identical_after_new_interpretation(inv, monkeypatch):
    import greenhouse_owner_os.services.owner_preparation as preparation
    with monkeypatch.context() as m:
        m.setattr(preparation, 'task_audience', lambda *a, **k: 'PERSONAL_TASK')
        saved, origin = capture(inv)
    done = confirm(inv, origin)
    old = svc(inv).task(UUID(inv['e']), inv['preview'][1], UUID(done['effect']['id']))
    recover(inv, saved, origin, expected=409)
    svc(inv).process(UUID(saved['id']))
    assert svc(inv).task(UUID(inv['e']), inv['preview'][1], UUID(done['effect']['id'])) == old
    assert effects(inv)['workers.tasks'] == 0


def test_stale_origin_and_forged_source_cannot_be_recovered(inv):
    saved, origin = capture(inv)
    changed = revise(inv, origin, dict(origin['revision']['payload'], note='Reviewed note'))
    recover(inv, saved, origin, expected=409)
    other, _ = capture(inv, 'Для меня завтра проверить другой насос')
    recover(inv, other, changed, expected=404)
    assert not any(effects(inv).values())


def test_unknown_source_cannot_get_task_authority_from_edited_candidate(inv):
    saved, origin = capture(inv, 'Непонятное сообщение')
    p = revise(inv, origin, dict(note='Непонятное сообщение', task_candidates=dict(title='Forged candidate', original_instruction='Forged candidate')))
    recover(inv, saved, p, expected=409)
    assert not any(effects(inv).values())


def test_two_recovery_keys_converge_under_source_lock(inv):
    saved, origin = capture(inv)
    service = svc(inv)
    body = TaskRecoveryAction(origin_proposal_id=origin['id'], expected_version=origin['version'], audience='personal')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda k: service.recover_task(UUID(inv['e']), inv['preview'][1], UUID(saved['id']), k, body), ['first', 'second']))
    assert results[0]['id'] == results[1]['id']
    assert len(queue(inv)) == 1 and not any(effects(inv).values())


def test_worker_reference_deactivation_invalidates_confirmation_without_dispatch(inv):
    worker = send(inv['c'], f"/entities/{inv['e']}/workers", dict(display_name='Умар', language='ru', source='test:audience'))
    saved, p = capture(inv, 'Умар завтра проверить насос в Имамлы')
    assert p['revision']['missing'] == []
    with inv['preview'][0].begin() as db:
        db.execute(text('UPDATE workers.people SET active=false WHERE id=:id'), dict(id=worker['id']))
    confirm(inv, p, expected=422)
    assert not any(effects(inv).values())


def test_foreign_greenhouse_and_worker_ids_rejected_on_review(inv):
    from tests.integration.test_postgres_owner_review import other_owner
    from tests.integration.test_postgres_business_capture import prepare
    sf, _, refs, finance, _ = inv['preview']
    foreign_actor, client = other_owner(inv)
    _, foreign_entity, _, _, _, _, foreign_greenhouse = prepare((sf, foreign_actor, refs, finance, client))
    foreign_worker = send(client, f'/entities/{foreign_entity}/workers', dict(display_name='Рахим', language='ru', source='test:foreign-audience'))
    saved, origin = capture(inv, 'Работнику завтра проверить насос в ЧужаяТеплица')
    assert origin['action_type'] == 'WORKER_TASK' and origin['revision']['payload']['greenhouse_id'] is None
    for field, value in [('greenhouse_id', str(foreign_greenhouse)), ('worker_id', foreign_worker['id'])]:
        payload = dict(origin['revision']['payload'], **{field: value})
        send(inv['c'], f"/entities/{inv['e']}/owner/confirmations/{origin['id']}/revise", dict(expected_version=origin['version'], payload=payload), expected=422)
    assert not any(effects(inv).values())


def test_sql_unlinked_task_recovery_target_is_rejected(inv):
    saved, origin = capture(inv)
    with pytest.raises(DBAPIError), inv['preview'][0].begin() as db:
        db.execute(text("""INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by,first_unresolved_at)
            VALUES(:id,:e,:s,:slot,'WORKER_TASK','PREPARING',1,2,:a,clock_timestamp())"""),
            dict(id=uuid4(), e=inv['e'], s=saved['id'], slot='manual:task:' + origin['id'], a=inv['preview'][1]))
    assert not any(effects(inv).values())


def raw_target(db, i, saved, origin, kind='WORKER_TASK', slot='manual:task:'):
    target = uuid4()
    db.execute(text("""INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by,first_unresolved_at)
        VALUES(:id,:e,:s,:slot,:kind,'PREPARING',1,2,:a,clock_timestamp())"""),
        dict(id=target, e=i['e'], s=saved['id'], slot=slot + origin['id'], kind=kind, a=i['preview'][1]))
    return target


def raw_link(db, i, saved, origin, target):
    db.execute(text('''INSERT INTO core.owner_recovery_links(entity_id,inbox_id,origin_proposal_id,origin_version,recovery_proposal_id,created_by)
        VALUES(:e,:s,:origin,:version,:target,:a)'''),
        dict(e=i['e'], s=saved['id'], origin=origin['id'], version=origin['version'], target=target, a=i['preview'][1]))


def test_sql_linked_generic_unresolved_cannot_become_task(inv):
    saved, origin = capture(inv, 'Непонятное сообщение')
    before = effects(inv)
    with pytest.raises(DBAPIError, match='Task recovery requires an immutable prepared task'), inv['preview'][0].begin() as db:
        raw_link(db, inv, saved, origin, raw_target(db, inv, saved, origin))
    assert effects(inv) == before and len(queue(inv)) == 1


def test_sql_task_candidate_cannot_recover_as_money(inv):
    saved, origin = capture(inv)
    with pytest.raises(DBAPIError, match='Prepared task cannot recover into an economic action'), inv['preview'][0].begin() as db:
        target = raw_target(db, inv, saved, origin, kind='DIRECT_EXPENSE', slot='manual:direct-expense:')
        raw_link(db, inv, saved, origin, target)
    assert not any(effects(inv).values()) and len(queue(inv)) == 1


def test_sql_confirmed_legacy_worker_draft_cannot_become_personal(inv):
    worker = send(inv['c'], f"/entities/{inv['e']}/workers", dict(display_name='Умар', language='ru', source='test:audience'))
    saved, origin = capture(inv, 'Умар завтра проверить насос в Имамлы')
    assert origin['revision']['payload']['worker_id'] == worker['id']
    confirm(inv, origin)
    before = effects(inv)
    with pytest.raises(DBAPIError, match='Selected origin is not eligible'), inv['preview'][0].begin() as db:
        raw_link(db, inv, saved, origin, raw_target(db, inv, saved, origin, kind='PERSONAL_TASK'))
    assert effects(inv) == before and before['workers.tasks'] == 1 and before['workers.assignments'] == 0


@pytest.mark.parametrize('first', ['recovery', 'confirm'])
def test_recovery_and_legacy_confirmation_serialize_without_two_effects(inv, monkeypatch, first):
    from threading import Event, current_thread
    from greenhouse_owner_os.domain.owner_capture import ConfirmAction
    from tests.integration.test_postgres_owner_capture import confirm_body
    saved, origin = capture(inv, 'Для меня завтра проверить насос в Имамлы')
    service = svc(inv)
    locked, release, second_attempt = Event(), Event(), Event()
    original_lock = service._source_lock
    def controlled_lock(u, e, s):
        is_first = current_thread().name.startswith('first')
        if not is_first: second_attempt.set()
        original_lock(u, e, s)
        if is_first:
            locked.set()
            assert release.wait(10)
    monkeypatch.setattr(service, '_source_lock', controlled_lock)
    e, a = UUID(inv['e']), inv['preview'][1]
    actions = dict(
        recovery=lambda: service.recover_task(e, a, UUID(saved['id']), 'race-recovery', TaskRecoveryAction(origin_proposal_id=origin['id'], expected_version=origin['version'], audience='worker')),
        confirm=lambda: service.confirm(e, a, UUID(origin['id']), 'race-confirm', ConfirmAction(**confirm_body(origin))))
    other = 'confirm' if first == 'recovery' else 'recovery'
    with ThreadPoolExecutor(1, thread_name_prefix='first') as one, ThreadPoolExecutor(1, thread_name_prefix='second') as two:
        leading = one.submit(actions[first]); assert locked.wait(10)
        following = two.submit(actions[other]); assert second_attempt.wait(10)
        release.set(); leading.result(timeout=10)
        with pytest.raises(ConflictError): following.result(timeout=10)
    count = effects(inv)
    assert count['core.owner_tasks'] == count['core.confirmation_effects'] == (first == 'confirm')
    assert count['workers.tasks'] == count['workers.assignments'] == 0


def test_target_insert_then_failure_rolls_back_entire_recovery(inv, monkeypatch):
    saved, origin = capture(inv)
    service = svc(inv)
    def fail(*args, **kwargs): raise RuntimeError('TEST failure after target insertion')
    monkeypatch.setattr(service, '_revision', fail)
    with pytest.raises(RuntimeError, match='TEST failure'):
        service.recover_task(UUID(inv['e']), inv['preview'][1], UUID(saved['id']), 'rollback', TaskRecoveryAction(origin_proposal_id=origin['id'], expected_version=origin['version'], audience='worker'))
    assert len(queue(inv)) == 1 and queue(inv)[0]['id'] == origin['id']
    assert not any(effects(inv).values())
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM core.owner_recovery_links WHERE entity_id=:e'), dict(e=inv['e'])) == 0
        assert db.scalar(text("SELECT count(*) FROM audit.idempotency_keys WHERE actor_id=:a AND operation='owner_task_recovery'"), dict(a=inv['preview'][1])) == 0


def test_other_owner_cannot_choose_audience_or_replay_private_response(inv):
    from tests.integration.test_postgres_owner_review import other_owner
    saved, origin = capture(inv)
    recover(inv, saved, origin, key='private-recovery')
    _, client = other_owner(inv)
    response = client.post(f"/v1/entities/{inv['e']}/owner/captures/{saved['id']}/task-recovery",
        json=dict(origin_proposal_id=origin['id'], expected_version=origin['version'], audience='worker'), headers={'Idempotency-Key': 'private-recovery'})
    assert response.status_code in (403, 422)
    assert not any(effects(inv).values())


def test_original_mixed_fixture_keeps_ambiguous_task_and_financial_siblings(inv):
    raw = 'TEST неопределённое событие.\nКупил бензин за 321 TRY.\nЗавтра полить İmamlı.'
    saved = source(inv, raw)
    ps = [p for p in queue(inv) if p['inbox_id'] == saved['id']]
    task = next(p for p in ps if p['revision']['payload'].get('task_candidates'))
    financial = next(p for p in ps if p['action_type'] == 'DIRECT_EXPENSE')
    untouched = deepcopy(financial['revision'])
    assert financial['revision']['payload']['amount'] == '321'
    assert task['revision']['payload']['task_candidates']['original_instruction'] == 'Завтра полить İmamlı.'
    recover(inv, saved, task, audience='personal')
    assert svc(inv).preview(UUID(inv['e']), inv['preview'][1], UUID(financial['id']))['revision'] == untouched
    assert not any(effects(inv).values())


def test_reviewed_ambiguous_task_retains_unchanged_field_provenance_and_owner_edits(inv):
    saved, origin = capture(inv)
    candidate = dict(origin['revision']['payload']['task_candidates'], title='Reviewed exact title')
    reviewed = revise(inv, origin, dict(origin['revision']['payload'], task_candidates=candidate, note='Owner note'))
    fields = reviewed['revision']['provenance']['task_candidates']['fields']
    assert fields['title']['category'] == 'OWNER INPUT'
    assert fields['original_instruction']['category'] == 'SOURCE FACT'
    assert fields['greenhouse_id']['category'] == 'INFERENCE'
    target = recover(inv, saved, reviewed)
    assert target['revision']['payload']['title'] == 'Reviewed exact title'
    assert target['revision']['provenance']['title']['category'] == 'OWNER INPUT'
    assert target['revision']['provenance']['original_instruction']['category'] == 'SOURCE FACT'
    assert not any(effects(inv).values())
