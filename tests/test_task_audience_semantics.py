"""Owner's explicit 2026-10-12 audience rule; separate from frozen benchmark."""
from datetime import date
from uuid import uuid4
import pytest
from greenhouse_owner_os.domain.owner_capture import DTO, TaskProposal
from greenhouse_owner_os.services.owner_preparation import prepare_owner_source

DAY = date(2026, 10, 12)

@pytest.fixture
def refs():
    return {'areas': [dict(id=str(uuid4()), name='İmamlı', kind='greenhouse', active=True)],
            'workers': [dict(id=str(uuid4()), display_name=name, language='ru', active=True) for name in ('Умар', 'Инаят')]}

def proposal(raw, refs):
    result = prepare_owner_source(raw, today=DAY, refs=refs)
    assert len(result) == 1
    kind, fields, evidence = result[0]
    DTO[kind].model_validate(fields)
    return kind, fields, evidence

@pytest.mark.parametrize('raw', [
    'Задача номер 1 - порезать листья старые деревья И навести порядок.',
    'Задача номер 2- переставить фитинги для полива также как мы сделали во второй и третей части.',
    'Задача номер 3 - почистить фитинги для полива.',
    'Нужно переделать полив на Имамлы на следующей неделе',
    'Сегодня полить Имамлы',
    'Проверить насос завтра',
    'Задача номер 81: осмотреть сетку',
], ids=lambda raw: raw[:12])
def test_unknown_audience_preserves_action_and_asks_instead_of_selecting_personal(raw, refs):
    kind, fields, evidence = proposal(raw, refs)
    assert kind == 'UNRESOLVED'
    task = fields['task_candidates']
    assert task['original_instruction'] == raw and task['worker_id'] is None
    assert evidence['task_candidates']['basis'] == 'task_audience_requires_owner_choice'
    assert evidence['task_candidates']['fields']['original_instruction']['category'] == 'SOURCE FACT'

@pytest.mark.parametrize('raw', ['Умар завтра полить Имамлы', 'Для Умара завтра полить Имамлы'], ids=lambda raw: raw[:12])
def test_explicit_current_worker_is_a_candidate_not_assignment(raw, refs):
    kind, fields, evidence = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][0]['id']
    assert fields['greenhouse_id'] == refs['areas'][0]['id'] and fields['planned_date'] == '2026-10-13'
    assert fields['original_instruction'] == raw and evidence['worker_id']['category'] == 'INFERENCE'

@pytest.mark.parametrize('raw', ['Работнику завтра полить Имамлы', 'Рахим завтра полить Имамлы', 'Рахим, полить Имамлы завтра'], ids=lambda raw: raw[:12])
def test_worker_intent_without_known_identity_preserves_known_facts(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None
    assert fields['greenhouse_id'] == refs['areas'][0]['id'] and fields['planned_date'] == '2026-10-13'

@pytest.mark.parametrize('raw', ['Я сам завтра полить Имамлы', 'Для меня завтра проверить насос', 'Напомни мне позвонить Умару завтра', 'Моя задача: проверить насос'], ids=lambda raw: raw[:12])
def test_explicit_personal_intention_is_never_converted_by_worker_mentions(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'PERSONAL_TASK' and fields['worker_id'] is None and fields.get('language') is None

@pytest.mark.parametrize('raw', ['Пока не поливать Имамлы', 'Не надо полить Имамлы', 'Не поручай Умару полить Имамлы'], ids=lambda raw: raw[:12])
def test_negated_instruction_has_no_positive_task_candidate(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED' and not fields.get('task_candidates')

@pytest.mark.parametrize('raw', ['Умар и Инаят завтра полить Имамлы', 'Умар или Инаят завтра полить Имамлы', 'Умар и Рахим завтра полить Имамлы'], ids=lambda raw: raw[:12])
def test_multiple_possible_workers_are_not_narrowed_to_one(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None


def test_inactive_worker_and_foreign_greenhouse_are_not_resolved(refs):
    refs['workers'][0]['active'] = False
    kind, fields, _ = proposal('Умар завтра полить ЧужаяТеплица', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None and fields['greenhouse_id'] is None


def test_permission_and_fertilizer_context_never_become_task_assignment(refs):
    refs['scopes'] = [dict(worker_id=refs['workers'][0]['id'], greenhouse_id=refs['areas'][0]['id'], action='GRANT')]
    refs['bindings'] = [dict(default_worker_id=refs['workers'][0]['id'], work_area_id=refs['areas'][0]['id'], effective_from='2020-01-01', verification='OWNER_CONFIRMED')]
    kind, fields, _ = proposal('Работнику завтра полить Имамлы', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None


def test_additive_dto_does_not_change_legacy_task_serialization():
    old = TaskProposal(title='Legacy personal').model_dump(mode='json')
    assert 'task_candidates' not in old and 'category' not in old and 'execution_contract' not in old

@pytest.mark.parametrize('raw', [
    'Я сегодня поручаю Умару полить Имамлы',
    'Я завтра поручаю Инаяту проверить насос в Имамлы',
    'Мне нужно, чтобы Умар завтра полил Имамлы',
    'Мне надо чтобы Рахим завтра проверил насос в Имамлы',
], ids=['owner-delegates', 'owner-future-delegates', 'desired-worker-action', 'desired-unknown-worker'])
def test_owner_first_person_framing_does_not_override_worker_instruction(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK'
    assert fields['original_instruction'] == raw
    assert fields['worker_id'] == (None if 'Рахим' in raw else refs['workers'][1]['id'] if 'Инаят' in raw else refs['workers'][0]['id'])

@pytest.mark.parametrize('raw', [
    'Поставь мне на завтра проверку давления в Имамлы',
    'Добавь в мой список: завтра проверить насос',
    'Мне на завтра нужна задача проверить насос',
    'Запиши мое дело: завтра проверить насос',
], ids=['personal-schedule', 'personal-list', 'personal-needed-task', 'personal-matter'])
def test_explicit_personal_task_frames_are_preserved(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'PERSONAL_TASK' and fields['worker_id'] is None


def test_leading_object_noun_does_not_establish_worker_name(refs):
    kind, fields, _ = proposal('поезд закрой все окна на Имамлы', refs)
    assert kind == 'UNRESOLVED' and fields['task_candidates']['worker_id'] is None

@pytest.mark.parametrize('raw', ['Я не куплю удобрение', 'Я не распишу план', 'Умар, не закрывай окна', 'Умар завтра не полить Имамлы'], ids=['negated-purchase-intent', 'negated-plan-intent', 'negated-worker-imperative', 'negated-worker-infinitive'])
def test_negation_does_not_become_positive_personal_or_worker_action(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED' and not fields.get('task_candidates')


def test_first_person_call_is_not_assigned_to_its_recipient(refs):
    kind, fields, _ = proposal('Я позвоню Умару', refs)
    assert kind == 'PERSONAL_TASK' and fields['worker_id'] is None

@pytest.mark.parametrize('raw', [
    'Заедьте завтра возьмите веревку на мое имя',
    'Ты пиши мне перед тем как заказывать работника',
], ids=['other-person-owner-order', 'other-person-report-to-owner'])
def test_other_person_and_owner_contrast_establishes_worker_audience(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None

@pytest.mark.parametrize('raw', ['Сегодня полить İmamlı.', 'Сегодня проверить давление в Имамлы TEST 0', 'Сегодня проверить давление в Имамлы'], ids=['old-shared-personal-fixture', 'old-personal-list-fixture', 'old-legacy-shape-fixture'])
def test_preserved_old_personal_fixture_text_now_requires_audience_choice(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED' and fields['task_candidates']['original_instruction'] == raw


def test_preserved_mixed_recovery_fixture_keeps_financial_sibling_independent(refs):
    raw = 'TEST неопределённое событие.\nКупил бензин за 321 TRY.\nЗавтра полить İmamlı.'
    result = prepare_owner_source(raw, today=DAY, refs=refs)
    assert [p[0] for p in result] == ['UNRESOLVED', 'DIRECT_EXPENSE', 'UNRESOLVED']
    assert result[1][1]['amount'] == '321'
    assert result[2][1]['task_candidates']['original_instruction'] == 'Завтра полить İmamlı.'

@pytest.mark.parametrize('name', ['Рахим', 'Закир', 'Рустам', 'рустам'])
def test_dictionary_known_unambiguous_name_preserves_worker_intent(name, refs):
    kind, fields, _ = proposal(name + ' полить Имамлы завтра', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None


def test_negated_personal_audience_does_not_clear_known_worker(refs):
    kind, fields, _ = proposal('Не для меня: Умар завтра полить Имамлы', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][0]['id']


def test_negative_operating_constraint_does_not_cancel_positive_instruction(refs):
    raw = 'Воду на поливе завтра не экономить. Пока чините полив и фитинги - все это время поливать'
    result = prepare_owner_source(raw, today=DAY, refs=refs)
    assert len(result) == 1
    kind, payload, _ = result[0]
    fields = payload.get('task_candidates', payload)
    assert kind == 'UNRESOLVED' and fields['original_instruction'] == raw
    assert fields['planned_date'] == '2026-10-13' and fields['category'] == 'irrigation'


def test_explicit_worker_instruction_preserves_negative_operating_constraint(refs):
    raw = 'Умар, воду на поливе завтра не экономить. Пока чините полив и фитинги - все это время поливать'
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][0]['id']
    assert fields['original_instruction'] == raw and fields['planned_date'] == '2026-10-13'


@pytest.mark.parametrize('raw', [
    'Дверь починить завтра', 'Фитинги проверить завтра', 'Крышу проверить завтра',
    'Ворота проверить завтра', 'Насос завтра проверить', 'Дверь закроет окна завтра',
    'Дверь, проверь трубу завтра', 'Фитинги, проверь завтра', 'Фитингг, проверь завтра',
    'Зорпак завтра проверить насос', 'Зорпак, проверить насос завтра',
    'Хасан полить Имамлы завтра', 'Роман проверить завтра', 'Вера проверить завтра',
    'Любовь проверить завтра', 'Надежда проверить завтра',
], ids=lambda raw: raw[:30])
def test_objects_homonyms_and_unknown_tokens_keep_audience_open(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED'
    assert fields['task_candidates']['original_instruction'] == raw
    assert fields['task_candidates']['worker_id'] is None


@pytest.mark.parametrize('raw', [
    'Завтра позвонить Умару', 'Завтра помочь Умару полить Имамлы',
    'Письмо Умару написать завтра', 'Завтра позвонить брату', 'Найти работника завтра',
    'Мне нужно, чтобы насос работал, проверить завтра', 'Пусть насос работает, завтра проверить',
], ids=lambda raw: raw[:30])
def test_object_or_inanimate_subject_does_not_become_executor(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED'
    assert fields['task_candidates']['original_instruction'] == raw
    assert fields['task_candidates']['worker_id'] is None


def test_addressed_person_movement_is_distinct_from_object_topic(refs):
    raw = 'Шербек, в 4:30 заедь на Озвади и закрой, пожалуйста, вентиляцию.'
    kind, fields, evidence = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None
    basis = evidence['original_instruction']['audience_evidence']
    assert basis['category'] == 'INFERENCE' and basis['rule'] == 'vocative_person_movement_v1'


def test_current_reference_resolves_name_homonym_but_lexicon_never_supplies_id(refs):
    refs['workers'].append(dict(id=str(uuid4()), display_name='Хасан', active=True, language='ru'))
    kind, fields, _ = proposal('Хасан завтра полить Имамлы', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][-1]['id']


def test_explicit_role_resolves_lexical_ambiguity_without_identity(refs):
    kind, fields, _ = proposal('Работнику Хасану завтра полить Имамлы', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None


def test_worker_actor_does_not_get_replaced_by_contact_object(refs):
    kind, fields, _ = proposal('Умар завтра позвонить Инаяту', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][0]['id']


def test_omitted_and_inactive_name_evidence_does_not_restore_identity(refs):
    for people in ([], [dict(refs['workers'][0], active=False)]):
        kind, fields, evidence = proposal('Умар завтра проверить Имамлы', dict(refs, workers=people))
        assert kind == 'WORKER_TASK' and fields['worker_id'] is None
        assert evidence['original_instruction']['audience_evidence']['source'] == 'OpenCorpora'


@pytest.mark.parametrize('raw', [
    'Пускай доделает окна из за которых мы его пригласили',
    'просто пускай выключает насос через телефон .',
])
def test_implicit_actor_in_intentional_directive_remains_worker(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None
    assert fields['original_instruction'] == raw


@pytest.mark.parametrize('raw', ['Напомни Умару полить завтра', 'Напомни Рахиму полить завтра'])
def test_worker_reminder_has_priority_over_generic_personal_reminder(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK'
    assert fields['worker_id'] == (refs['workers'][0]['id'] if 'Умару' in raw else None)


@pytest.mark.parametrize('raw', ['Рахим позвонить Умару', 'Рахим завтра позвонить Умару'])
def test_removing_contact_object_preserves_the_explicit_unknown_actor(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None
    assert fields['original_instruction'] == raw


@pytest.mark.parametrize('name', ['Рустам', 'Умар'])
@pytest.mark.parametrize('status', ['занят', 'болен', 'устал'])
def test_person_status_clause_does_not_supply_actor_of_later_instruction(name, status, refs):
    raw = f'{name} {status}, завтра проверить насос'
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED' and fields['task_candidates']['worker_id'] is None
    assert fields['task_candidates']['original_instruction'] == raw


@pytest.mark.parametrize('raw', ['Умару завтра позвонить', 'Умару помочь полить завтра', 'Брату завтра позвонить'])
def test_fronted_dative_contact_object_does_not_become_caller(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED' and fields['task_candidates']['worker_id'] is None
    assert fields['task_candidates']['original_instruction'] == raw


@pytest.mark.parametrize('raw', ['Для Умара завтра позвонить Инаяту', 'Умар Инаяту завтра позвонить'])
def test_dative_contact_veto_preserves_explicit_actor_and_task_recipient(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][0]['id']


@pytest.mark.parametrize('raw', [
    'Отправить фото Умару завтра', 'Фото Умару отправить завтра',
    'Написать Умару завтра', 'Проверить список работников завтра',
])
def test_message_recipient_and_genitive_role_object_do_not_supply_executor(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED' and fields['task_candidates']['worker_id'] is None
    assert fields['task_candidates']['original_instruction'] == raw


@pytest.mark.parametrize('raw', [
    'Умар завтра отправить фото Инаяту', 'Умар Инаяту отправить фото завтра',
    'Для Умара завтра отправить фото Инаяту',
])
def test_message_object_veto_retains_distinct_explicit_actor(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][0]['id']


def test_explicit_for_worker_group_remains_worker_without_inferred_identity(refs):
    kind, fields, _ = proposal('Для работников завтра проверить насос', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None


@pytest.mark.parametrize('raw', [
    'Проверить работу Умара завтра', 'Осмотреть насос у Умара завтра',
    'Сфотографировать теплицу Умара завтра', 'Насос Умара завтра проверить',
])
def test_possession_or_location_reference_is_not_an_actor(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED' and fields['task_candidates']['worker_id'] is None
    assert fields['task_candidates']['original_instruction'] == raw


def test_explicit_actor_is_not_dropped_for_a_possessive_second_worker(refs):
    kind, fields, _ = proposal('Умар завтра проверить работу Инаята', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][0]['id']


@pytest.mark.parametrize('raw', [
    'Но завтра на всех теплицах будет резать брат Хиддаят.',
    'пускай мастер просто закроет окно в маленькой теплице и все.',
])
def test_explicit_role_survives_future_and_modal_actor_frames(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None


@pytest.mark.parametrize('raw', [
    'Проверить Имамлы завтра поручаю не Умару, а Инаяту',
    'В Имамлы на послезавтра назначаю Инаята: убрать срезанные листья.',
    'По Имамлы есть работа для Инаята на завтра: ремонт крепления пленки.',
    'Запиши Инаяту поручение, дата 2026-10-12, теплица Имамлы: полив каждой части по 25 минут.',
], ids=['postposed-corrected-delegation', 'appointment-frame', 'work-for-frame', 'task-registration-frame'])
def test_explicit_delegation_task_registration_and_for_frames_keep_current_recipient(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][1]['id']
    assert fields['original_instruction'] == raw


def test_explicit_executor_header_is_retained_with_following_delegation(refs):
    raw = 'Исполнитель Инаят. На завтра поручаю проверку подвязки в Имамлы.'
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][1]['id']
    assert fields['original_instruction'] == raw


@pytest.mark.parametrize('raw', [
    'Не назначаю Умара завтра полить', 'Не назначай Умара завтра полить',
    'Не запиши Умару поручение полить завтра', 'Не записывай Умару поручение полить завтра',
], ids=['negated-appointment', 'negated-appoint-command', 'negated-register', 'negated-register-ongoing'])
def test_negated_appointment_or_registration_does_not_create_positive_task(raw, refs):
    kind, fields, _ = proposal(raw, refs)
    assert kind == 'UNRESOLVED' and not fields.get('task_candidates')


def test_corrected_appointment_retains_the_positive_recipient(refs):
    kind, fields, _ = proposal('Назначаю не Умара, а Инаята завтра полить Имамлы', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] == refs['workers'][1]['id']


def test_appointment_requires_human_recipient_evidence(refs):
    kind, fields, _ = proposal('Назначаю проверку насоса на завтра', refs)
    assert kind == 'UNRESOLVED' and fields['note'] == 'Назначаю проверку насоса на завтра'
    kind, fields, _ = proposal('Назначаю на завтра задачу проверить насос', refs)
    assert kind == 'UNRESOLVED' and fields['task_candidates']['worker_id'] is None
    kind, fields, _ = proposal('Назначаю Рахима завтра полить Имамлы', refs)
    assert kind == 'WORKER_TASK' and fields['worker_id'] is None
