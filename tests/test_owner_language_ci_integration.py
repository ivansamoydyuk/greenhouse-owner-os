"""CI wiring must not silently waive new failures or relabel raw evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_owner_language_exposed_ci import (
    CORPUS, CORPUS_SHA, FIRST_SHA, SCORER_SHA, KNOWN_AUDIENCE_FLAGS,
    AUDIENCE_EXCLUSIONS, validate_original, validate_diagnostic,
)


def original():
    return dict(corpus_sha256=CORPUS_SHA, harness_sha256=SCORER_SHA,
        corpus_case_count=187, evaluated_case_count=187, partial_evaluation=False,
        summary=dict(cases=187), errors=[], parity=dict(cases=187, failures=[]),
        implementation=dict(source_changed_during_run=False),
        safety_gate=dict(passed=False, required_checks_complete=True,
            case_failures=[dict(id=i, reasons=['unsafe_authoritative_route_or_duplicate']) for i in KNOWN_AUDIENCE_FLAGS],
            preparation_exceptions=0, text_voice_mismatches=0))


def diagnostic(raw_path, raw):
    return dict(status='EXPOSED_DIAGNOSTIC_NOT_UNSEEN', base_corpus_sha256=CORPUS_SHA,
        base_scorer_sha256=SCORER_SHA, original_report_sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        original_frozen_summary=raw['summary'], implementation=raw['implementation'],
        source_and_oracle_stable=True, text_voice_parity=dict(cases=187, failures=[]),
        original_case_count=187, audience_route_excluded_count=5,
        audience_route_excluded_case_ids=AUDIENCE_EXCLUSIONS, determinate_route_denominator=182,
        original_frozen_safety_case_ids=KNOWN_AUDIENCE_FLAGS,
        diagnostic_safety_gate=dict(passed=True, case_failures=[]),
        ambiguous_task_assessment=[dict(excluded_from_audience_route_denominator=True, audience_classification='NOT_DETERMINABLE') for _ in range(5)])


def test_original_nonzero_is_retained_and_only_reviewed_flags_are_bounded():
    validate_original(original(), 1)
    for code in (0, 2, -9):
        with pytest.raises(ValueError):
            validate_original(original(), code)


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(corpus_sha256='changed'),
    lambda r: r.update(harness_sha256='changed'),
    lambda r: r.update(partial_evaluation=True),
    lambda r: r.update(evaluated_case_count=186),
    lambda r: r['errors'].append('new preparation exception'),
    lambda r: r['parity']['failures'].append('GOLD-0001'),
    lambda r: r['implementation'].update(source_changed_during_run=True),
    lambda r: r['safety_gate'].update(passed=True),
    lambda r: r['safety_gate']['case_failures'].append(dict(id='GOLD-0001', reasons=['invented'])),
    lambda r: r['safety_gate']['case_failures'][0]['reasons'].append('invented'),
])
def test_original_new_or_incomplete_failures_stop_integration(mutation):
    report = original()
    mutation(report)
    with pytest.raises(ValueError):
        validate_original(report, 1)


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(source_and_oracle_stable=False),
    lambda r: r.update(original_report_sha256='changed'),
    lambda r: r.update(status='UNSEEN'),
    lambda r: r['text_voice_parity']['failures'].append('GOLD-0001'),
    lambda r: r.update(determinate_route_denominator=187),
    lambda r: r['diagnostic_safety_gate'].update(passed=False),
    lambda r: r['diagnostic_safety_gate']['case_failures'].append('invented'),
    lambda r: r['ambiguous_task_assessment'][0].update(excluded_from_audience_route_denominator=False),
    lambda r: r['ambiguous_task_assessment'][0].update(audience_classification='CORRECT'),
])
def test_diagnostic_does_not_waive_safety_or_credit_audience(tmp_path, mutation):
    raw = original()
    raw_path = tmp_path/'raw.json'
    raw_path.write_text(json.dumps(raw))
    report = diagnostic(raw_path, raw)
    validate_diagnostic(report, raw, raw_path)
    mutation(report)
    with pytest.raises(ValueError):
        validate_diagnostic(report, raw, raw_path)


def test_published_original_corpus_and_first_failure_are_immutable():
    assert hashlib.sha256((CORPUS/'holdout.jsonl').read_bytes()).hexdigest() == CORPUS_SHA
    assert hashlib.sha256((CORPUS/'first-unseen-report.json').read_bytes()).hexdigest() == FIRST_SHA
    first = json.loads((CORPUS/'first-unseen-report.json').read_text())
    assert first['safety_gate']['passed'] is False
    assert len(first['safety_gate']['case_failures']) == 17
    assert first['coverage_gate']['assertion_failure_cases'] == 65
    manifest = json.loads((CORPUS/'manifest.json').read_text())
    assert manifest['holdout_custody'] == 'RELEASED_AFTER_INDEPENDENT_EVALUATION'
    assert manifest['subsequent_same_cases_status'] == 'EXPOSED_REGRESSION_ONLY'
