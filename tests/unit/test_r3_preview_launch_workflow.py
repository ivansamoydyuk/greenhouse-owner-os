"""This delivery branch verifies isolated TEST journeys without public access."""
from pathlib import Path
import yaml


def test_program_workflow_has_no_public_preview_or_access_artifact():
    raw = Path('.github/workflows/r3-connected-pilot.yml').read_text()
    workflow = yaml.safe_load(raw)
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['jobs']) == {'connected-browser'}
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
