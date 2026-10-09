"""Separate physical-owner regression. Not part of the 805-case language GOLD."""
from uuid import UUID, uuid4
import pytest
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.models import WorkAreaRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_owner_capture import source, queue, revise, confirm

OWNER_PHRASE = 'Нужно переделать полив на Озвади на следующей неделе'
VARIANTS = [
    OWNER_PHRASE,
    'на следующей неделе надо переделать полив на озвади',
    'озвади на следующей неделе поменять полив',
    'нужно изменить полив в Özvadi на следующей неделе',
    'следующая неделя озвади переделать полив',
    'НУЖНО\nПЕРЕДЕЛАТЬ ПОЛИВ В Ozvadi НА СЛЕДУЮЩЕЙ НЕДЕЛЕ',
]


def prepare_area(inv):
    area = uuid4()
    with inv['preview'][0].begin() as db:
        db.add(WorkAreaRow(id=area, entity_id=UUID(inv['e']), name='Özvadi', kind='greenhouse', source='test:physical-owner-regression'))
    return str(area)


def get_proposal(inv, saved):
    found = [p for p in queue(inv) if p['inbox_id'] == saved['id']]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize('raw', VARIANTS)
def test_relative_irrigation_text_voice_source_parity_and_no_effect(inv, raw):
    area = prepare_area(inv)
    sf, actor, *_ = inv['preview']
    svc = OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf))
    payloads = []
    for mode in ('text', 'voice_transcript'):
        saved = source(inv, raw, mode=mode)
        p = get_proposal(inv, saved)
        assert p['action_type'] == 'PERSONAL_TASK'
        assert p['state'] == ('READY' if mode == 'text' else 'NEEDS_INPUT')
        assert p['revision']['missing'] == ([] if mode == 'text' else ['Возможный повтор источника: проверьте оригинал'])
        f = p['revision']['payload']
        assert f['category'] == 'irrigation'
        assert f['relative_time_context'] == 'следующая неделя'
        assert f['greenhouse_id'] == area
        assert f['planned_date'] is None and f['worker_id'] is None
        assert f['original_instruction'] == raw
        assert p['source']['text'] == raw
        assert svc.original(UUID(inv['e']), actor, UUID(saved['id']))[0] == raw.encode('utf-8')
        assert p['revision']['provenance']['category']['category'] == 'INFERENCE'
        assert p['revision']['provenance']['planned_date']['category'] == 'UNKNOWN'
        assert p['revision']['provenance']['worker_id']['category'] == 'UNKNOWN'
        assert p['revision']['provenance']['relative_time_context']['source_text'] == raw
        assert p['revision']['extraction_run_id']
        payloads.append(f)
        svc.process(UUID(saved['id']))
        assert get_proposal(inv, saved)['id'] == p['id']
    assert payloads[0] == payloads[1]
    with sf() as db:
        for table in ('core.confirmation_effects', 'core.owner_tasks', 'workers.tasks', 'workers.assignments', 'finance.postings', 'inventory.movements'):
            assert db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'), dict(e=inv['e'])) == 0


def test_relative_irrigation_revision_confirmation_home_and_retry_preserve_context(inv):
    area = prepare_area(inv)
    saved = source(inv, OWNER_PHRASE)
    p = get_proposal(inv, saved)
    original_revision = p['revision']
    p = revise(inv, p, dict(p['revision']['payload'], note='TEST owner note remains separate'))
    assert p['revision']['payload']['relative_time_context'] == 'следующая неделя'
    result = confirm(inv, p, key='physical-owner-confirm')
    assert result['state'] == 'POSTED'
    assert result['effect']['planned_date'] is None
    assert result['effect']['note'] == 'TEST owner note remains separate'
    assert confirm(inv, p, key='physical-owner-confirm') == result
    assert confirm(inv, p, key='physical-owner-replay') == result
    home = inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()
    task = next(t for t in home['tasks'] if t['id'] == result['effect']['id'])
    assert task['category'] == 'irrigation'
    assert task['relative_time_context'] == 'следующая неделя'
    assert task['original_instruction'] == OWNER_PHRASE
    assert task['planned_date'] is None and task['greenhouse_id'] == area
    sf, actor, *_ = inv['preview']
    with sf() as db:
        stored = db.execute(text('SELECT payload,preview_digest FROM core.proposal_revisions WHERE proposal_id=:p AND version=:v'), dict(p=p['id'], v=original_revision['version'])).mappings().one()
        assert stored['payload'] == original_revision['payload']
        assert stored['preview_digest'] == original_revision['preview_digest']
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE proposal_id=:p'), dict(p=p['id'])) == 1
        assert db.scalar(text('SELECT count(*) FROM core.owner_tasks WHERE proposal_id=:p'), dict(p=p['id'])) == 1
    assert OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf)).original(UUID(inv['e']), actor, UUID(saved['id']))[0] == OWNER_PHRASE.encode('utf-8')


def test_legacy_task_revision_without_additive_keys_preview_confirm_and_replay(inv):
    saved = source(inv, 'Сегодня проверить давление в Имамлы')
    p = get_proposal(inv, saved)
    legacy = {k: v for k, v in p['revision']['payload'].items() if k not in ('category', 'relative_time_context')}
    p = revise(inv, p, legacy)
    assert 'category' not in p['revision']['payload']
    assert 'relative_time_context' not in p['revision']['payload']
    snapshot = p['revision']
    root = f"/v1/entities/{inv['e']}/owner/confirmations/{p['id']}/preview"
    assert inv['c'].get(root).json()['revision'] == snapshot
    result = confirm(inv, p, key='legacy-confirm')
    assert confirm(inv, p, key='legacy-replay') == result
    assert inv['c'].get(root).json()['revision'] == snapshot
    home = inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()
    task = next(t for t in home['tasks'] if t['id'] == result['effect']['id'])
    assert task.get('category') is None and task.get('relative_time_context') is None


def test_named_worker_relative_irrigation_requires_only_missing_exact_day(inv):
    area = prepare_area(inv)
    worker = send(inv['c'], f"/entities/{inv['e']}/workers", dict(display_name='Умар', language='ru', source='test:physical-owner-regression'))
    raw = 'Умар на следующей неделе переделать полив на Озвади'
    saved = source(inv, raw)
    p = get_proposal(inv, saved)
    assert p['action_type'] == 'WORKER_TASK'
    assert p['revision']['payload']['greenhouse_id'] == area
    assert p['revision']['payload']['worker_id'] == worker['id']
    assert p['revision']['missing'] == ['На какой день следующей недели поставить?']
    confirm(inv, p, expected=422)
    f = dict(p['revision']['payload'], planned_date='2026-10-15')
    p = revise(inv, p, f)
    assert p['revision']['payload']['relative_time_context'] == 'следующая неделя'
    assert p['revision']['missing'] == []
    result = confirm(inv, p)
    assert result['state'] == 'CONFIRMED_NONPOSTING'
    assert result['effect']['not_sent'] is True
    assert result['effect']['original_instruction'] == raw
    assert result['effect']['planned_date'] == '2026-10-15'
    task = inv['c'].get(f"/v1/entities/{inv['e']}/tasks/{result['effect']['id']}").json()
    assert task['assignments'] == []
    assert task['relative_time_context'] == 'следующая неделя'
    assert task['category'] == 'general'  # Reviewed proposal metadata does not rewrite the R1 category.
    assert task['prepared_category'] == 'irrigation'


@pytest.mark.parametrize('places', ['Озвдаи и Имамлы', 'Имамлы и Озвдаи', 'Озвадия и Имамлы', 'Имамлы или Озвдаи'])
def test_relative_irrigation_unknown_coordinated_greenhouse_scope_is_not_narrowed(inv, places):
    prepare_area(inv)
    raw = f'Нужно переделать полив на {places} на следующей неделе'
    for mode in ('text', 'voice_transcript'):
        saved = source(inv, raw, mode=mode)
        p = get_proposal(inv, saved)
        f = p['revision']['payload']
        assert p['action_type'] == 'PERSONAL_TASK'
        assert f['greenhouse_id'] is None and f['worker_id'] is None
        assert f['planned_date'] is None
        assert f['category'] == 'irrigation' and f['relative_time_context'] == 'следующая неделя'
        assert f['original_instruction'] == raw and p['source']['text'] == raw
        assert p['revision']['provenance']['greenhouse_id']['category'] == 'UNKNOWN'


def test_relative_irrigation_explicitly_replaced_unknown_place_keeps_corrected_known_place(inv):
    prepare_area(inv)
    raw = 'Нужно переделать полив не на Озвдаи, а на Имамлы на следующей неделе'
    saved = source(inv, raw)
    p = get_proposal(inv, saved)
    assert p['revision']['payload']['greenhouse_id'] == inv['g']
    assert p['revision']['payload']['original_instruction'] == raw


def test_relative_irrigation_negated_coordinated_locations_do_not_leak_second_place(inv):
    prepare_area(inv)
    raw = 'Нужно переделать полив не на Озвдаи и Имамлы на следующей неделе'
    saved = source(inv, raw)
    p = get_proposal(inv, saved)
    f = p['revision']['payload']
    assert p['action_type'] == 'PERSONAL_TASK'
    assert f['greenhouse_id'] is None and f['worker_id'] is None and f['planned_date'] is None
    assert f['category'] == 'irrigation' and f['relative_time_context'] == 'следующая неделя'
    assert f['original_instruction'] == raw
