"""Measured OCR runtime may extend the full gate, never narrow its coverage."""
from pathlib import Path

import yaml


def workflow():
    root = Path(__file__).resolve().parents[2]
    return yaml.load((root / '.github/workflows/postgresql-verification.yml').read_text(), Loader=yaml.BaseLoader)


def test_full_regression_keeps_all_tests_and_failure_diagnostics_with_bounded_ocr_budget():
    job = workflow()['jobs']['verify']
    step = next(step for step in job['steps'] if step.get('name') == 'Complete regression suite')
    assert job['timeout-minutes'] == '50'
    assert step['run'].splitlines()[0] == (
        'timeout --signal=TERM --kill-after=10s 1200s pytest -v '
        '-o faulthandler_timeout=60 --junitxml="$RUNNER_TEMP/finance.xml"'
    )
    assert 'continue-on-error' not in step and 'if' not in step
    assert "assert len(cases) >= 1247, 'Complete regression suite did not execute'" in step['run']
    assert "assert not any(case.find('skipped') is not None for case in cases), 'Finance tests skipped'" in step['run']


def test_integration_budget_and_all_authority_minimums_remain_unchanged():
    job = workflow()['jobs']['verify']
    step = next(step for step in job['steps'] if step.get('name') == 'PostgreSQL integration and concurrency tests')
    assert step['run'].splitlines()[0] == (
        'timeout --signal=TERM --kill-after=10s 900s pytest tests/integration -v '
        '--durations=20 -o faulthandler_timeout=60 --junitxml="$RUNNER_TEMP/integration.xml"'
    )
    assert 'continue-on-error' not in step and 'if' not in step
    for assertion in (
        "assert len(cases) >= 608, 'PostgreSQL tests did not execute'",
        "assert not any(case.find('skipped') is not None for case in cases), 'PostgreSQL tests skipped'",
        "assert len(workers) >= 125, 'Workers adversarial gate did not execute'",
        "assert sum('test_postgres_owner_expense_recovery' in case.get('classname', '') for case in cases) >= 54",
        "[('test_postgres_owner_language_benchmark', 21), ('test_postgres_owner_language_preparation', 11), ('test_postgres_owner_actor_scope', 3), ('test_postgres_owner_worker_identity', 1), ('test_postgres_owner_friction_evidence', 1), ('test_postgres_owner_relative_irrigation', 15)]",
        "assert sum(module in case.get('classname', '') for case in cases) >= minimum",
    ):
        assert assertion in step['run']


def test_actual_browser_and_versioned_language_gates_remain_after_full_suite():
    jobs = workflow()['jobs']
    assert jobs['owner-r2-browser']['timeout-minutes'] == '15'
    steps = jobs['verify']['steps']
    full_index = next(index for index, step in enumerate(steps) if step.get('name') == 'Complete regression suite')
    for command in ('python scripts/run_owner_language_contract_v2.py',
                    'python scripts/run_owner_e2e.py', 'python scripts/run_harvest_coverage_e2e.py'):
        matches = [(index, step) for index, step in enumerate(steps) if step.get('run') == command]
        assert len(matches) == 1
        index, step = matches[0]
        assert index > full_index and 'if' not in step and 'continue-on-error' not in step
