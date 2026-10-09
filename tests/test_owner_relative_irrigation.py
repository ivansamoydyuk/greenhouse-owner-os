"""Physical-owner regression; independent of the immutable language benchmark."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from greenhouse_owner_os.domain.owner_capture import DTO, TaskProposal
from greenhouse_owner_os.services.finance import _hash_request
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.services.owner_preparation import prepare_owner_source
from greenhouse_owner_os.services.preparation_language import entity_matches


DAY = date(2026, 10, 9)
SOURCE = 'Нужно переделать полив на Озвади на следующей неделе'
OZVADI = str(UUID(int=11))
IMAMLI = str(UUID(int=12))
WORKER = str(UUID(int=21))


@pytest.fixture
def refs():
    return dict(
        areas=[dict(id=OZVADI, name='Özvadi', active=True, kind='greenhouse'),
               dict(id=IMAMLI, name='İmamlı', active=True, kind='greenhouse')],
        workers=[dict(id=WORKER, display_name='Умар', native_name='Umar', active=True, language='ru')],
    )


def prepare(raw, refs):
    slots = prepare_owner_source(raw, today=DAY, refs=refs)
    for kind, fields, _ in slots:
        DTO[kind].model_validate(fields)
    return slots


@pytest.mark.parametrize('raw', [
    SOURCE,
    'На следующей неделе нужно переделать полив на Озвади',
    'Озвади на следующей неделе поменять полив',
    'нужно изменить полив в Özvadi на следующей неделе',
    'следующая неделя озвади переделать полив',
    'Полив на Озвади нужно переделать на следующей неделе',
    'Переделай полив на Ozvadi на следующей неделе',
    'Нужно перенастроить полив в ÖZVADI на следующей неделе',
    'Необходимо наладить орошение на Özvadi на следующей неделе',
    'Нужно переделат полив на Озвади на следущей неделе',
    'Надо передлать полив на Озвади следующей неделе',
    '  НУЖНО\nПЕРЕДЕЛАТЬ ПОЛИВ В Ozvadi НА СЛЕДУЮЩЕЙ НЕДЕЛЕ  ',
    'НУЖНО\nПЕРЕДЕЛАТЬ\nПОЛИВ НА ОЗВАДИ\nНА СЛЕДУЮЩЕЙ НЕДЕЛЕ',
    'На следующей неделе\nнужно переделать полив на Озвади',
    'Полив на Озвади\nнужно переделать\nна следующей неделе',
])
def test_irrigation_intent_reordering_spelling_and_whitespace_preserve_source(raw, refs):
    slots = prepare(raw, refs)
    assert len(slots) == 1
    kind, fields, provenance = slots[0]
    assert kind == 'PERSONAL_TASK'
    assert fields['category'] == 'irrigation'
    assert fields['relative_time_context'] == 'следующая неделя'
    assert fields['greenhouse_id'] == OZVADI
    assert fields['planned_date'] is None and fields['worker_id'] is None
    assert fields['original_instruction'].encode() == raw.encode()
    assert fields['title'] == raw[:200]
    assert provenance['category']['category'] == 'INFERENCE'
    assert provenance['relative_time_context']['category'] == 'SOURCE FACT'
    assert provenance['planned_date']['category'] == 'UNKNOWN'
    assert provenance['worker_id']['category'] == 'UNKNOWN'
    assert all(item['source_text'] == raw for item in provenance.values())


@pytest.mark.parametrize('raw', [
    'Переделал полив на Озвади на следующей неделе',
    'Переделали полив на Озвади вчера',
    'Не нужно переделывать полив на Озвади на следующей неделе',
    'Нужно не переделывать полив на Озвади на следующей неделе',
    'Не надо менять полив на Озвади',
    'Не переделать полив на Озвади',
    'Нужно было переделать полив на Озвади',
    'Если переделать полив на Озвади на следующей неделе',
    'Нужно ли переделать полив на Озвади на следующей неделе?',
    'Наверное нужно переделать полив на Озвади на следующей неделе',
    'Он сказал: «Нужно переделать полив на Озвади на следующей неделе»',
    'Это пример: нужно переделать полив на Озвади на следующей неделе',
    'Полив на Озвади на следующей неделе',
    'Переделать крышу на Озвади на следующей неделе',
])
def test_completed_negated_conditional_reported_and_noninstruction_stay_unresolved(raw, refs):
    slots = prepare(raw, refs)
    assert [kind for kind, _, _ in slots] == ['UNRESOLVED']
    assert slots[0][1]['note'] == raw


@pytest.mark.parametrize('place', ['Озвдаи', 'Озвадия', 'Озвади 2', 'Озвади №2', 'Озвади-2', 'Озвади и Имамлы', 'Озвади 2 и Имамлы'])
def test_unknown_typos_numbered_or_multiple_greenhouses_never_select_one(place, refs):
    raw = f'Нужно переделать полив на {place} на следующей неделе'
    kind, fields, provenance = prepare(raw, refs)[0]
    assert kind == 'PERSONAL_TASK' and fields['category'] == 'irrigation'
    assert fields['greenhouse_id'] is None
    assert provenance['greenhouse_id']['category'] == 'UNKNOWN'


def test_verified_alias_is_authority_for_a_typo_but_inactive_or_duplicate_is_not(refs):
    raw = 'Нужно переделать полив на Озвдаи на следующей неделе'
    refs['areas'][0]['aliases'] = ['Озвдаи']
    assert prepare(raw, refs)[0][1]['greenhouse_id'] == OZVADI
    refs['areas'][0]['active'] = False
    assert prepare(raw, refs)[0][1]['greenhouse_id'] is None
    refs['areas'][0]['active'] = True
    refs['areas'].append(dict(refs['areas'][0], id=str(UUID(int=13))))
    assert prepare(raw, refs)[0][1]['greenhouse_id'] is None


def test_complete_registered_numbered_site_remains_resolvable(refs):
    second = dict(id=str(UUID(int=13)), name='Özvadi 2', aliases=['Озвади 2'], active=True, kind='greenhouse')
    refs['areas'].append(second)
    assert entity_matches('на Озвади 2', refs['areas'], 'area') == [second]


@pytest.mark.parametrize('scope', [
    'на Озвдаи и Имамлы', 'на Имамлы и Озвдаи',
    'на озвдаи и имамлы', 'в İmamlı и озвдаи',
    'на Озвдаи и на Имамлы', 'на Имамлы и в Озвдаи',
    'на Озвдаи или Имамлы', 'на Имамлы либо Озвдаи',
    'на Озвади и Имамлы и Озвдаи',
])
def test_explicit_mixed_unknown_location_scope_cannot_narrow_to_one_known_place(scope, refs):
    raw = f'Нужно переделать полив {scope} на следующей неделе'
    kind, fields, provenance = prepare(raw, refs)[0]
    assert kind == 'PERSONAL_TASK' and fields['category'] == 'irrigation'
    assert fields['greenhouse_id'] is None and fields['planned_date'] is None
    assert fields['relative_time_context'] == 'следующая неделя'
    assert fields['original_instruction'] == raw
    assert provenance['greenhouse_id']['category'] == 'UNKNOWN'


@pytest.mark.parametrize('scope', [
    'не на Озвдаи, а на Имамлы',
    'на Имамлы и настроить систему',
    'на Имамлы и проверить давление',
    'на Имамлы и закрой окна',
    'на Имамлы и вечером проверить давление',
    'на Имамлы и на следующей неделе проверить давление',
])
def test_replacements_and_coordinated_actions_do_not_invent_a_second_place(scope, refs):
    raw = f'Нужно переделать полив {scope} на следующей неделе'
    fields = prepare(raw, refs)[0][1]
    assert fields['greenhouse_id'] == IMAMLI
    assert fields['original_instruction'] == raw


def test_complex_negated_multiplace_scope_stays_for_review(refs):
    # Do not add a broader scope-rewrite grammar for nested list negation.
    raw = 'Нужно переделать полив не на Озвдаи и Озвади, а на Имамлы на следующей неделе'
    fields = prepare(raw, refs)[0][1]
    assert fields['greenhouse_id'] is None
    assert fields['original_instruction'] == raw


@pytest.mark.parametrize('scope', ['не на Озвдаи и Имамлы', 'не на Имамлы и Озвдаи', 'не на Озвади и Имамлы'])
def test_no_member_of_a_negated_location_list_becomes_a_positive_greenhouse(scope, refs):
    raw = f'Нужно переделать полив {scope} на следующей неделе'
    fields = prepare(raw, refs)[0][1]
    assert fields['greenhouse_id'] is None
    assert fields['relative_time_context'] == 'следующая неделя'
    assert fields['original_instruction'] == raw


def test_shared_extractor_retains_legacy_task_input_compatibility(refs):
    from greenhouse_owner_os.services.tasks_capture import extract_task
    from greenhouse_owner_os.domain.workers import TaskInput
    kind, fields, gaps = extract_task(SOURCE, areas=refs['areas'], workers=refs['workers'], today=DAY)
    assert kind == 'TASKS'
    assert 'relative_time_context' not in fields  # R2-only DTO metadata.
    assert 'На какой день следующей недели поставить?' in gaps
    fields.pop('suggested_worker_ids')
    task = TaskInput(**fields, locator='test:legacy-task-input')
    assert task.category == 'irrigation' and task.planned_date is None
    assert task.original_instruction == SOURCE


@pytest.mark.parametrize('suffix', ['15 кг KNO3', '20 минут', '30 минут', '31 февраля 2027', '12 октября 2026', '2026-10-12', '12.10.2026'])
def test_quantity_duration_or_calendar_after_greenhouse_is_not_a_numbered_site(suffix, refs):
    fields = prepare(f'Умар завтра полить Имамлы {suffix}', refs)[0][1]
    assert fields['greenhouse_id'] == IMAMLI


def test_task_date_correction_never_changes_financial_event_or_due_semantics(refs):
    refs['counterparties'] = [dict(id=str(UUID(int=31)), name='Серкан', active=True)]
    raw = 'Должен Серкану 5000 TRY, срок не 15 октября 2026, а 15 ноября 2026'
    kind, fields, _ = prepare(raw, refs)[0]
    assert kind == 'PAYABLE'
    assert fields['event_date'] is None and fields['due_date'] is None
    assert fields['description'] == raw


@pytest.mark.parametrize(('time', 'planned', 'relative'), [
    ('12.10.2026', '2026-10-12', None),
    ('2026-10-12', '2026-10-12', None),
    ('на следующей неделе', None, 'следующая неделя'),
    ('не на следующей неделе, а 12.10.2026', '2026-10-12', None),
    ('не 12.10.2026, а на следующей неделе', None, 'следующая неделя'),
    ('не завтра, а на следующей неделе', None, 'следующая неделя'),
    ('завтра или на следующей неделе', None, 'следующая неделя'),
])
def test_relative_period_is_not_an_exact_day_and_same_source_corrections_win(time, planned, relative, refs):
    raw = f'Нужно переделать полив на Озвади {time}'
    fields = prepare(raw, refs)[0][1]
    assert fields['planned_date'] == planned
    assert fields.get('relative_time_context') == relative
    assert fields['original_instruction'] == raw
    assert fields['greenhouse_id'] == OZVADI


def test_explicit_corrected_greenhouse_uses_current_references(refs):
    raw = 'Нужно переделать полив не на Озвади, а на Имамлы на следующей неделе'
    fields = prepare(raw, refs)[0][1]
    assert fields['greenhouse_id'] == IMAMLI
    assert fields['original_instruction'] == raw


@pytest.mark.parametrize(('prefix', 'identity'), [('Умар', WORKER), ('Umar', WORKER), ('Закир,', None), ('Хасан', None)])
def test_only_explicit_verified_worker_can_prefill_identity(prefix, identity, refs):
    kind, fields, provenance = prepare(f'{prefix} на следующей неделе переделать полив на Озвади', refs)[0]
    assert kind == 'WORKER_TASK'
    assert fields['worker_id'] == identity and fields['planned_date'] is None
    assert provenance['worker_id']['category'] == ('INFERENCE' if identity else 'UNKNOWN')


@pytest.mark.parametrize('prefix', ['Озвади', 'озвади', 'Özvadi', 'ÖZVADI', 'Ozvadi'])
def test_leading_known_area_with_relative_time_is_not_an_unknown_worker(prefix, refs):
    kind, fields, _ = prepare(f'{prefix} на следующей неделе поменять полив', refs)[0]
    assert kind == 'PERSONAL_TASK'
    assert fields['worker_id'] is None and fields['greenhouse_id'] == OZVADI


def test_wrap_handling_does_not_swallow_independent_siblings(refs):
    raw = SOURCE + '\nУмар завтра проверить Имамлы\nНечитаемый остаток'
    slots = prepare(raw, refs)
    assert [kind for kind, _, _ in slots] == ['PERSONAL_TASK', 'WORKER_TASK', 'UNRESOLVED']
    assert slots[0][1]['original_instruction'] == SOURCE
    assert slots[1][1]['greenhouse_id'] == IMAMLI
    assert slots[2][1]['note'] == 'Нечитаемый остаток'


def test_old_task_snapshot_shape_digest_and_notes_survive_nullable_extension():
    legacy = dict(title='Проверить давление', original_instruction='Проверить давление', greenhouse_id=None,
                  planned_date=None, worker_id=None, language=None, rendered_text=None, note='Существующая заметка')
    serialized = TaskProposal.model_validate(legacy).model_dump(mode='json')
    assert serialized == legacy
    assert _hash_request(serialized) == _hash_request(legacy)
    assert TaskProposal.model_validate(dict(legacy, category=None, relative_time_context=None)).model_dump(mode='json') == legacy
    extended = TaskProposal.model_validate(dict(legacy, category='irrigation', relative_time_context='следующая неделя')).model_dump(mode='json')
    assert extended['note'] == legacy['note']
    assert _hash_request(extended) != _hash_request(legacy)


@pytest.mark.parametrize('extra', [dict(category=''), dict(category='x'*101), dict(category=5),
                                  dict(relative_time_context=''), dict(relative_time_context='x'*251),
                                  dict(relative_time_context={}), dict(unrecognized_field='value')])
def test_additive_metadata_remains_strict_and_bounded(extra):
    with pytest.raises(ValidationError):
        TaskProposal(title='Задача', **extra)


def test_worker_date_question_is_specific_and_personal_date_remains_optional(monkeypatch, refs):
    import greenhouse_owner_os.services.owner_capture as capture
    def reference_rows(_u, _sql, **params):
        return [row for values in refs.values() for row in values if row['id'] == params.get('id')]
    monkeypatch.setattr(capture, 'rows', reference_rows)
    service = OwnerCaptureService(None)
    fields = prepare(SOURCE, refs)[0][1]
    proposal = dict(action_type='PERSONAL_TASK', entity_id=str(UUID(int=1)))
    assert service._validate(None, proposal, fields)[1] == []
    proposal['action_type'] = 'WORKER_TASK'
    fields.update(worker_id=WORKER, language='ru')
    assert service._validate(None, proposal, fields)[1] == ['На какой день следующей недели поставить?']
    fields['planned_date'] = '2026-10-12'
    validated, missing, _ = service._validate(None, proposal, fields)
    assert missing == [] and validated['relative_time_context'] == 'следующая неделя'


def test_proposal_metadata_does_not_change_authoritative_r1_category(monkeypatch, refs):
    """Category extension is display metadata; the existing adapter is unchanged."""
    from greenhouse_owner_os.services.workers import WorkersService
    captured = []
    def create(_self, _e, _a, _key, body):
        captured.append(body)
        return dict(id=str(UUID(int=100)), category=body.category)
    monkeypatch.setattr(WorkersService, 'create', create)
    source = SimpleNamespace(received_at=datetime(2026, 10, 9, tzinfo=timezone.utc))
    uow = SimpleNamespace(session=SimpleNamespace(get=lambda *_: source))
    fields = TaskProposal.model_validate(prepare('Умар '+SOURCE, refs)[0][1]).model_dump(mode='json')
    fields['planned_date'] = '2026-10-12'
    proposal = dict(action_type='WORKER_TASK', id=str(UUID(int=1)), inbox_id=str(UUID(int=2)))
    result, _, nonposting = OwnerCaptureService(None)._apply(uow, UUID(int=3), UUID(int=4), proposal, fields)
    assert nonposting and result['not_sent'] is True
    assert captured[0].category == 'general'
    assert captured[0].original_instruction == 'Умар '+SOURCE
    assert fields['category'] == 'irrigation'
