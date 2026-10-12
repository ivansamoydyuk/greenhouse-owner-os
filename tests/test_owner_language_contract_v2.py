"""A v2 PASS cannot be obtained by blank unresolved output or label laundering."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.owner_language_benchmark import CORPUS, load, prepare_case
from scripts.owner_language_contract_v2 import (
    V2, digest, evaluate_case, validate_lineage, verify_contract, load_baseline,
)
from scripts.run_owner_language_contract_v2 import validate_raw


@pytest.fixture
def evidence():
    contract = json.loads((V2/'contract.json').read_text())
    baseline = load_baseline(contract)
    gold = {c['id']: c for name in ('train', 'holdout') for c in load(CORPUS/f'{name}.jsonl')}
    return contract, baseline, gold


def target(evidence, id_='GOLD-0235', name='holdout'):
    contract, baseline, gold = evidence
    case = gold[id_]
    return case, prepare_case(case), baseline[name][id_], contract['reviewed_expectations'][id_]


@pytest.mark.parametrize('id_', ['GOLD-0235', 'GOLD-0236', 'GOLD-0237'])
def test_retained_original_examples_require_real_audience_clarification(evidence, id_):
    case, slots, old, entry = target(evidence, id_)
    assert entry['expected_kind'] == 'UNRESOLVED'
    result = evaluate_case(case, slots, old, entry)
    assert result['strict_passed'], result
    assert slots[0][1]['task_candidates']['original_instruction'] == case['text']
    assert all(slots[0][1]['task_candidates'][key] is None for key in ('worker_id', 'greenhouse_id', 'planned_date'))
    # Frozen-v1 disagreement is still reported in the actual raw score.
    assert not result['raw_v1']['route_ok']


@pytest.mark.parametrize('mutation', [
    lambda s: s[0][1].pop('task_candidates'),
    lambda s: s[0][1].update(task_candidates={}),
    lambda s: s[0][1]['task_candidates'].update(original_instruction=''),
    lambda s: s[0][1]['task_candidates'].update(title='Уточните'),
    lambda s: s[0][1]['task_candidates'].update(category=None),
    lambda s: s[0][1]['task_candidates'].update(worker_id='00000000-0000-0000-0000-000000000001'),
    lambda s: s[0][1]['task_candidates'].update(planned_date='2026-10-10'),
    lambda s: s[0][1]['task_candidates'].update(greenhouse_id='00000000-0000-0000-0000-000000000001'),
    lambda s: s[0][1]['task_candidates'].update(rendered_text='invented translation'),
    lambda s: s[0][2]['task_candidates']['fields']['original_instruction'].update(category='INFERENCE'),
    lambda s: s[0][2]['task_candidates']['fields']['worker_id'].update(category='SOURCE FACT'),
    lambda s: s.append(deepcopy(s[0])),
])
def test_generic_or_corrupted_unresolved_cannot_receive_v2_credit(evidence, mutation):
    case, actual, old, entry = target(evidence)
    slots = [list(deepcopy(s)) for s in actual]
    mutation(slots)
    assert evaluate_case(case, slots, old, entry)['failures']


def test_candidate_without_real_short_question_fails(evidence, monkeypatch):
    case, slots, old, entry = target(evidence)
    monkeypatch.setattr('scripts.owner_language_contract_v2.audience_question', lambda _: ['Выберите режим'])
    assert 'missing_short_audience_question' in evaluate_case(case, slots, old, entry)['failures']


def test_unreviewed_reroute_cannot_be_waived(evidence):
    case, slots, old, _ = target(evidence)
    result = evaluate_case(case, slots, old)
    assert 'unreviewed_payload_or_route_change' in result['failures']


def test_money_sibling_cannot_change_under_task_review(evidence):
    case, slots, old, entry = target(evidence, 'GOLD-0564', 'train')
    slots = deepcopy(slots)
    money = next(s for s in slots if s[0] == 'PAYABLE')
    money[1]['amount'] = '1501'
    result = evaluate_case(case, slots, old, entry)
    assert 'unrelated_sibling_payload_changed' in result['failures']
    assert 'contradictory_or_filled_unknown_fact' in result['failures']


def test_review_source_and_lineage_are_bound(evidence):
    contract, _, gold = evidence
    case = deepcopy(gold['GOLD-0235'])
    entry = contract['reviewed_expectations'][case['id']]
    validate_lineage(case, case, entry)
    changed = deepcopy(case)
    changed['text'] += ' Умар'
    with pytest.raises(ValueError, match='source bytes'):
        validate_lineage(changed, changed, entry)
    changed = deepcopy(case)
    changed.update(id='UNREVIEWED-1', parent_id='GOLD-0001')
    with pytest.raises(ValueError, match='lineage'):
        validate_lineage(changed, case, entry)


def test_pending_review_fails_closed(evidence):
    contract, baseline, _ = evidence
    pending = deepcopy(contract)
    pending['status'] = 'PENDING_INDEPENDENT_REVIEW'
    with pytest.raises(ValueError, match='independent review'):
        verify_contract(pending, baseline)


def test_every_gold_task_has_independently_reviewed_expectation(evidence):
    contract, baseline, gold = evidence
    task_ids = {id_ for id_, case in gold.items() if any(e['kind'] in ('PERSONAL_TASK', 'WORKER_TASK') for e in case['expected'])}
    assert len(task_ids) == 208
    assert task_ids <= set(contract['reviewed_expectations'])
    assert contract['case_counts'] == dict(train=618, holdout=187, semantic=5200, noise=5200)
    assert sum(len(v) for v in baseline.values()) == 11205
    for id_ in task_ids:
        entry = contract['reviewed_expectations'][id_]
        assert entry['rationale'] and entry['original_text'] == gold[id_]['text']


def test_all_original_evidence_remains_byte_identical(evidence):
    contract, _, _ = evidence
    root = Path(__file__).resolve().parents[1]
    for name, expected in contract['frozen_v1_files'].items():
        assert digest(root/name) == expected, name
    first = json.loads((CORPUS/'first-unseen-report.json').read_text())
    assert first['safety_gate']['passed'] is False
    assert len(first['safety_gate']['case_failures']) == 17
    assert first['coverage_gate']['assertion_failure_cases'] == 65


def test_workflow_runs_actual_v2_wrapper_and_keeps_raw_runner_frozen():
    root = Path(__file__).resolve().parents[1]
    workflow = (root/'.github/workflows/postgresql-verification.yml').read_text()
    assert 'run: python scripts/run_owner_language_contract_v2.py' in workflow
    assert 'run: python scripts/run_owner_language_exposed_ci.py' not in workflow
    wrapper = (root/'scripts/run_owner_language_contract_v2.py').read_text()
    assert "'scripts/owner_language_benchmark.py', '--require-safety'" in wrapper
    assert "'scripts/diagnose_owner_language_audience.py', '--require-safety'" in wrapper
    assert "KNOWN_AUDIENCE_FLAGS" not in wrapper
    assert 'expected = [dict(id=i' not in wrapper


def test_forged_generated_source_cannot_inherit_audience_review(evidence):
    from scripts.generate_owner_language_variants import noise_variants
    contract, _, gold = evidence
    original = gold['GOLD-0001']
    child = noise_variants([original], 1)[0]
    entry = contract['reviewed_expectations'][original['id']]
    validate_lineage(child, original, entry)
    child['text'] = 'Для меня ' + child['text']
    with pytest.raises(ValueError, match='source text/identity'):
        validate_lineage(child, original, entry)


def test_known_worker_candidate_cannot_be_claimed_as_source_identity_fact(evidence):
    case, slots, old, entry = target(evidence, 'GOLD-0003', 'train')
    assert not evaluate_case(case, slots, old, entry)['failures']
    slots = deepcopy(slots)
    slots[0][2]['worker_id']['category'] = 'SOURCE FACT'
    assert 'reference_or_category_claimed_as_source_fact_worker_id' in evaluate_case(case, slots, old, entry)['failures']


def test_non_task_safety_failure_is_never_waived_as_historical(evidence):
    from scripts.owner_language_contract_v2 import payload_hash, score_contract
    from scripts.owner_language_benchmark import score
    _, baseline, gold = evidence
    case = next(c for c in gold.values() if c['expected'][0]['kind'] == 'PAYMENT')
    slots = deepcopy(prepare_case(case))
    slots[0][1]['amount'] = '999999'
    # Even a poisoned baseline cannot whitelist substantive financial safety.
    old = deepcopy(baseline[case['split']][case['id']])
    old.update(payload_sha256=payload_hash(slots), score=score_contract(score(case, slots)))
    assert 'contradictory_or_filled_unknown_fact' in evaluate_case(case, slots, old)['failures']


def test_baseline_shards_are_bounded_and_no_case_is_omitted(evidence):
    contract, baseline, _ = evidence
    manifest = json.loads((V2/'baseline-manifest.json').read_text())
    for name, shards in manifest['corpora'].items():
        assert sum(s['count'] for s in shards) == contract['case_counts'][name]
        for shard in shards:
            assert (V2/shard['file']).stat().st_size <= 60000
    assert sum(len(c) for c in baseline.values()) == 11205


def test_corrupted_baseline_shard_is_rejected(evidence, tmp_path):
    import shutil
    contract, _, _ = evidence
    for path in V2.glob('baseline*.json'):
        shutil.copyfile(path, tmp_path/path.name)
    manifest = json.loads((tmp_path/'baseline-manifest.json').read_text())
    first = tmp_path/manifest['corpora']['train'][0]['file']
    first.write_text(first.read_text() + ' ')
    with pytest.raises(ValueError, match='shard changed'):
        load_baseline(contract, tmp_path)


def test_raw_exit_is_measured_without_requiring_old_three_failures(evidence):
    contract, _, _ = evidence
    path = CORPUS/'holdout.jsonl'
    report = dict(corpus_sha256=digest(path), harness_sha256=contract['frozen_v1_files']['scripts/owner_language_benchmark.py'],
        summary=dict(cases=187), corpus_case_count=187, evaluated_case_count=187,
        partial_evaluation=False, errors=[], parity=dict(cases=187, failures=[], scope='test'),
        implementation=dict(source_changed_during_run=False),
        safety_gate=dict(passed=True, required_checks_complete=True))
    validate_raw(report, 0, path, contract, 'holdout')
    report['safety_gate']['passed'] = False
    validate_raw(report, 1, path, contract, 'holdout')
    with pytest.raises(ValueError, match='exit and safety'):
        validate_raw(report, 0, path, contract, 'holdout')


def test_workflow_retains_full_historical_and_audience_upgrade_chain():
    root = Path(__file__).resolve().parents[1]
    workflow = (root/'.github/workflows/postgresql-verification.yml').read_text()
    assert 'python -m tests.helpers.task_audience_upgrade_0035' in workflow
    helper = (root/'tests/helpers/task_audience_upgrade_0035.py').read_text()
    assert 'day_workers_upgrade_0034' in helper


@pytest.mark.parametrize(('id_', 'corpus'), [
    ('GOLD-0003', 'train'), ('GOLD-0491', 'holdout'), ('GOLD-0235', 'holdout'),
])
@pytest.mark.parametrize(('field', 'forged'), [
    ('assignment_id', '00000000-0000-0000-0000-000000000001'),
    ('status', 'ASSIGNED'), ('owner_confirmed', True), ('worker_id', 7),
    ('title', ['malformed']),
])
def test_every_reviewed_task_rejects_extra_authority_or_malformed_types(evidence, id_, corpus, field, forged):
    case, slots, old, entry = target(evidence, id_, corpus)
    assert not evaluate_case(case, slots, old, entry)['failures']
    slots = deepcopy(slots)
    payload = slots[0][1].get('task_candidates', slots[0][1])
    payload[field] = forged
    assert 'invalid_task_dto_or_unexpected_authority_fields' in evaluate_case(case, slots, old, entry)['failures']


def test_semantic_dependencies_are_exactly_pinned_and_byte_fingerprinted():
    from scripts.run_owner_language_contract_v2 import lexical_dependency_fingerprint, LEXICAL_DEPENDENCIES
    dependencies = lexical_dependency_fingerprint()
    assert set(dependencies) == set(LEXICAL_DEPENDENCIES)
    for row in dependencies.values():
        assert row['installed_version'] == row['declared_pin']
        assert row['package_files_sha256'] and len(row['installed_record_sha256']) == 64
    dictionary = dependencies['pymorphy3-dicts-ru']['package_files_sha256']
    assert any(path.endswith('/meta.json') for path in dictionary)
    assert any(path.endswith('.dawg') for path in dictionary)


def test_semantic_dependency_pin_mismatch_blocks_the_gate(tmp_path):
    from scripts.run_owner_language_contract_v2 import lexical_dependency_fingerprint
    root = Path(__file__).resolve().parents[1]
    project = tmp_path/'pyproject.toml'
    value = (root/'pyproject.toml').read_text()
    import re
    value = re.sub(r'pymorphy3==[^\"]+', 'pymorphy3==0.0.0', value)
    project.write_text(value)
    with pytest.raises(ValueError, match='differs from declared exact pin'):
        lexical_dependency_fingerprint(project)


def test_dictionary_environment_override_cannot_redirect_semantic_evidence(monkeypatch, tmp_path):
    from greenhouse_owner_os.services.task_audience_language import _dictionary
    from scripts.run_owner_language_contract_v2 import lexical_dependency_fingerprint
    from pymorphy3_dicts_ru import get_path
    monkeypatch.setenv('PYMORPHY2_DICT_PATH', str(tmp_path/'untrusted-dictionary'))
    monkeypatch.setenv('PYMORPHY3_DICT_PATH', str(tmp_path/'untrusted-dictionary'))
    _dictionary.cache_clear()
    try:
        row = lexical_dependency_fingerprint()['pymorphy3-dicts-ru']
        assert row['helper_data_path'] == row['installed_data_path'] == str(Path(get_path()).resolve())
    finally:
        _dictionary.cache_clear()


def reviewed_worker_prefill_entry(entry):
    entry = deepcopy(entry)
    entry['reviewed_field_improvement'] = dict(
        status='INDEPENDENTLY_REVIEWED', kind='existing_expected_worker_reference',
        slot_index=0, reviewer='/root/review_day_workers_integrity',
        rationale='The source explicitly identifies the actor; the existing expected worker reference is authoritative. Preserve the remaining historical greenhouse gap.')
    return entry


def test_reviewed_worker_improvement_keeps_every_remaining_gap_explicit(evidence):
    case, slots, old, entry = target(evidence, 'GOLD-0769', 'train')
    unreviewed = deepcopy(entry)
    unreviewed.pop('reviewed_field_improvement', None)
    assert 'new_or_changed_field_or_coverage_failure' in evaluate_case(case, slots, old, unreviewed)['failures']
    result = evaluate_case(case, slots, old, reviewed_worker_prefill_entry(entry))
    assert not result['failures'], result
    assert result['historical_coverage_gap'] and not result['strict_passed']
    assert result['versioned_score']['field_correct'] == 2
    assert result['versioned_score']['field_total'] == 3
    assert slots[0][1]['greenhouse_id'] is None
    assert result['reviewed_field_improvement']['baseline_payload_sha256'] == old['payload_sha256']


@pytest.mark.parametrize(('field', 'value'), [
    ('worker_id', '00000000-0000-0000-0000-000000000001'),
    ('language', 'tr'), ('planned_date', None), ('greenhouse_id', 'invented'),
    ('category', 'general'), ('title', 'changed instruction'),
])
def test_worker_improvement_cannot_offset_or_hide_any_other_payload_change(evidence, field, value):
    from scripts.owner_language_contract_v2 import reviewed_worker_improvement
    case, slots, old, entry = target(evidence, 'GOLD-0769', 'train')
    slots = deepcopy(slots)
    slots[0][1][field] = value
    entry = reviewed_worker_prefill_entry(entry)
    assert reviewed_worker_improvement(case, slots, old, entry) is None
    assert evaluate_case(case, slots, old, entry)['failures']


@pytest.mark.parametrize('context', ['omit_references', 'inactive_references', 'foreign_entity'])
def test_reviewed_worker_improvement_requires_current_same_entity_reference(evidence, context):
    from scripts.owner_language_contract_v2 import reviewed_worker_improvement
    case, slots, old, entry = target(evidence, 'GOLD-0769', 'train')
    case = deepcopy(case)
    if context == 'foreign_entity':
        case['entity_id'] = '00000000-0000-0000-0000-000000000001'
    else:
        case[context] = ['muhammad']
    assert reviewed_worker_improvement(case, slots, old, reviewed_worker_prefill_entry(entry)) is None


@pytest.mark.parametrize('missing', ['status', 'kind', 'reviewer', 'rationale'])
def test_worker_improvement_requires_specific_independent_review(evidence, missing):
    from scripts.owner_language_contract_v2 import reviewed_worker_improvement
    case, slots, old, entry = target(evidence, 'GOLD-0769', 'train')
    entry = reviewed_worker_prefill_entry(entry)
    del entry['reviewed_field_improvement'][missing]
    assert reviewed_worker_improvement(case, slots, old, entry) is None
