"""This delivery branch verifies isolated TEST journeys without public access."""
from pathlib import Path
import yaml


def test_program_workflow_has_no_public_preview_or_access_artifact():
    raw = Path('.github/workflows/r3-connected-pilot.yml').read_text()
    workflow = yaml.safe_load(raw)
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['jobs']) == {'connected-browser','private-daily-browser'}
    daily=workflow['jobs']['private-daily-browser']
    assert daily['needs']=='connected-browser'
    assert daily['env']['PILOT_LOCAL_TEST']=='1' and daily['env']['PREVIEW_BIND']=='127.0.0.1'
    guard=next(x for x in daily['steps'] if x.get('id')=='private_evidence_guard')
    assert guard['if']=='always()'
    upload=next(x for x in daily['steps'] if x.get('uses')=='actions/upload-artifact@v4')
    assert upload['if']=="always() && steps.private_evidence_guard.outcome == 'success'"
    assert 'RUNNER_TEMP' in '\n'.join(x.get('run','') for x in daily['steps'])
    for forbidden in ('cloudflared', 'trycloudflare', 'connected-preview:', 'private-access.txt', 'r3_connected_preview_access.py open'):
        assert forbidden not in raw


def test_isolated_gate_keeps_inherited_and_all_four_stage_journeys():
    workflow = yaml.safe_load(Path('.github/workflows/r3-connected-pilot.yml').read_text())
    job = workflow['jobs']['connected-browser']
    runs = '\n'.join(step.get('run', '') for step in job['steps'])
    for journey in ('r3-connected.mjs', 'r3-capture-friction.mjs', 'harvest-owner-journey.mjs', 'overnight-finance.mjs', 'overnight-purchases.mjs', 'overnight-workers.mjs', 'overnight-fertilizer.mjs'):
        assert journey in runs
    cleanup = next(step for step in job['steps'] if step.get('name') == 'Preserve diagnostics and stop only owned runtime')
    assert cleanup['if'] == 'always()'
    assert 'docker rm -v "$R3_PILOT_CONTAINER"' in cleanup['run']
    assert all(step.get('env', {}).get('PREVIEW_URL', 'http://127.0.0.1:4173') == 'http://127.0.0.1:4173' for step in job['steps'])
