"""The focused source proof does not expand the retained legacy runner."""
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from scripts import run_source_queue_e2e


@pytest.mark.parametrize('r2', [False, True])
def test_source_queue_runner_uses_one_fresh_isolated_preview(monkeypatch, tmp_path, r2):
    events = []
    fixtures = ModuleType('tests.integration.conftest')
    fixtures._reset_and_migrate = lambda url: events.append(('reset', url))
    monkeypatch.setitem(sys.modules, 'tests.integration.conftest', fixtures)
    url = 'postgresql+psycopg://postgres:postgres@localhost:5432/greenhouse_owner_os_test'
    monkeypatch.setenv('APP_ENV', 'test')
    monkeypatch.setenv('TEST_DATABASE_URL', url)
    monkeypatch.setenv('E2E_ARTIFACTS', str(tmp_path / 'source-evidence'))
    monkeypatch.setenv('PREVIEW_SECRET', 'not-reused-by-the-focused-runner')
    monkeypatch.setenv('R2_REVIEW_FIXTURES', '1')
    monkeypatch.delenv('R2_E2E_ONLY', raising=False)
    if r2:
        monkeypatch.setenv('R2_E2E_ONLY', '1')
    monkeypatch.setattr(run_source_queue_e2e, 'run_preview',
                        lambda env, suites, log: events.append(('run', dict(env), suites, log)))

    run_source_queue_e2e.main()

    assert [event[0] for event in events] == ['reset', 'run']
    assert events[0][1] == url
    _, env, suites, log = events[1]
    assert suites == ['owner-source-queue-errors']
    assert env['PREVIEW_SECRET'] != 'not-reused-by-the-focused-runner'
    assert len(env['PREVIEW_SECRET']) >= 24
    assert env['PREVIEW_BIND'] == '127.0.0.1' and env['PREVIEW_PORT'] == '4173'
    assert env['PREVIEW_URL'] == 'http://127.0.0.1:4173'
    assert 'R2_REVIEW_FIXTURES' not in env
    assert str(run_source_queue_e2e.ROOT / 'src') in env['PYTHONPATH']
    assert Path(env['E2E_ARTIFACTS']).is_dir()
    assert Path(log) == Path(env['E2E_ARTIFACTS']) / 'preview-source-queue-errors.log'


@pytest.mark.parametrize('environment,url', [
    ('production', 'postgresql+psycopg://postgres@localhost:5432/greenhouse_owner_os_test'),
    ('test', 'postgresql+psycopg://postgres@localhost:5432/production'),
    ('test', 'postgresql+psycopg://postgres@example.com:5432/greenhouse_owner_os_test'),
])
def test_source_queue_runner_rejects_unsafe_target_before_reset(monkeypatch, environment, url):
    events = []
    fixtures = ModuleType('tests.integration.conftest')
    fixtures._reset_and_migrate = lambda url: events.append('reset')
    monkeypatch.setitem(sys.modules, 'tests.integration.conftest', fixtures)
    monkeypatch.setenv('APP_ENV', environment)
    monkeypatch.setenv('TEST_DATABASE_URL', url)
    monkeypatch.setattr(run_source_queue_e2e, 'run_preview', lambda *args: events.append('run'))
    with pytest.raises(ValueError):
        run_source_queue_e2e.main()
    assert events == []


def test_focused_gate_runs_once_without_replacing_legacy_browser_gates():
    workflow = yaml.safe_load((run_source_queue_e2e.ROOT / '.github/workflows/postgresql-verification.yml').read_text())
    commands = [step.get('run', '') for job in workflow['jobs'].values() for step in job['steps']]
    assert commands.count('python scripts/run_source_queue_e2e.py') == 1
    assert commands.count('python scripts/run_owner_e2e.py') == 2
