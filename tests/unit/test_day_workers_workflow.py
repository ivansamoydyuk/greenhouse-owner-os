"""The new owner/worker proof must execute against one exact private TEST build."""
from pathlib import Path
import yaml


def test_day_workers_browser_checkpoint_is_real_scoped_and_guarded():
    path = Path('.github/workflows/day-workers-checkpoint.yml')
    raw = path.read_text()
    workflow = yaml.safe_load(raw)
    triggers = workflow.get('on', workflow.get(True))
    assert {'16-greenhouse-daily-day-workers', '01-architecture-database'} <= set(triggers['push']['branches'])
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['day-workers-browser-sql']
    assert job['env']['PILOT_LOCAL_TEST'] == '1'
    assert job['env']['PREVIEW_BIND'] == '127.0.0.1'
    assert job['env']['PREVIEW_URL'] == 'http://127.0.0.1:4173'
    assert job['env']['DAY_WORKERS_PHASE'] in ('request', 'full')
    assert 'test "$(git rev-parse HEAD)"' in raw
    assert 'test -z "$(git status --porcelain)"' in raw
    assert 'for engine in chromium webkit' in raw
    assert 'node tests/e2e/day-workers.mjs' in raw
    assert 'day_workers_sql_evidence.py --before' in raw
    assert 'day_workers_sql_evidence.py --verify' in raw
    assert 'PILOT_E2E_CREDENTIALS=$RUNNER_TEMP/' in raw
    assert 'timedelta(hours=4)' in raw
    steps = job['steps']
    names = [s.get('name', s.get('uses')) for s in steps]
    assert names.index('Guard private evidence') < names.index('actions/upload-artifact@v4')
    upload = steps[names.index('actions/upload-artifact@v4')]
    assert "steps.guard.outcome == 'success'" in upload['if']
    assert upload['with']['if-no-files-found'] == 'error'
    assert all(term not in raw for term in ('cloudflared', 'trycloudflare', 'id-token: write', 'contents: write', 'environment: production'))
    assert steps[-1]['if'] == 'always()'
    assert 'R3_PILOT_CONTAINER' in steps[-1]['run']
