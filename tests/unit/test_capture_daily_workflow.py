"""The bounded OCR checkpoint tests immutable app code, not a mixed baseline."""
from pathlib import Path
import yaml

WORKFLOW=Path('.github/workflows/capture-daily-checkpoint.yml')


def test_capture_checkpoint_has_independent_browser_database_jobs():
    workflow=yaml.safe_load(WORKFLOW.read_text())
    assert workflow['permissions']=={'contents':'read'}
    job=workflow['jobs']['corpus-browser-sql']
    assert job['strategy']['matrix']['engine']==['chromium','webkit']
    assert 'candidate' in job['strategy']['matrix']['mode']
    assert job['env']['PILOT_LOCAL_TEST']=='1'
    assert job['env']['PREVIEW_BIND']=='127.0.0.1'
    assert job['env']['PREVIEW_URL']=='http://127.0.0.1:4173'
    text=WORKFLOW.read_text()
    assert 'git checkout --detach 9a981766f217fe33364f32b601bfc2a4468b4f59' in text
    assert 'application-sha.txt' in text and 'harness-sha.txt' in text
    assert 'node tests/e2e/capture-daily-checkpoint.mjs' in text
    assert 'capture_daily_sql_evidence.py --verify' in text
    assert 'private_daily_pilot_artifact_guard.py artifacts' in text
    assert "steps.guard.outcome == 'success'" in text
    assert 'PILOT_E2E_CREDENTIALS=$RUNNER_TEMP/' in text
    assert 'cloudflared' not in text and 'trycloudflare' not in text
    assert not any(x in text for x in ('contents: write','id-token: write','environment: production'))


def test_baseline_only_copies_measurement_files():
    text=WORKFLOW.read_text()
    selection=text.split('name: Select immutable application')[1].split('- uses: actions/setup-python')[0]
    assert 'cp --parents tests/e2e/capture-daily-checkpoint.mjs scripts/capture_daily_sql_evidence.py tests/capture_daily_corpus.py tests/fixtures/capture_daily_benchmark.json' in selection
    assert 'cp -R tests/fixtures/capture_daily_assets' in selection
    assert 'prototype/' not in selection
    assert 'cp src/' not in selection
