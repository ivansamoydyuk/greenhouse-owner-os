"""The optional owner window stays dry until an explicitly rerun verified job."""
from pathlib import Path
import yaml


def test_connected_preview_has_success_dependency_and_every_live_step_is_fenced():
    workflow = yaml.safe_load(Path('.github/workflows/r3-connected-pilot.yml').read_text())
    assert workflow['permissions'] == {'contents': 'read'}
    preview = workflow['jobs']['connected-preview']
    assert preview['needs'] == 'connected-browser'
    assert preview['timeout-minutes'] == 43
    first, *operations = preview['steps']
    assert first['if'] == 'github.run_attempt == 1'
    assert 'NO PUBLIC PREVIEW STARTED' in first['run']
    assert 'PREVIEW_SECRET' not in first['run']
    for step in operations:
        assert 'github.run_attempt > 1' in step['if']
    checkout = next(step for step in operations if step.get('uses', '').startswith('actions/checkout'))
    assert checkout['with']['ref'] == '${{ github.sha }}'
    cleanup = next(step for step in operations if step.get('name') == 'Stop only owned app, tunnel and database')
    assert cleanup['if'].startswith('always()')
    assert 'docker rm -v "$R3_PILOT_CONTAINER"' in cleanup['run']


def test_private_access_is_separate_short_lived_and_not_in_sanitized_evidence():
    workflow = yaml.safe_load(Path('.github/workflows/r3-connected-pilot.yml').read_text())
    uploads = [step for step in workflow['jobs']['connected-preview']['steps']
               if step.get('uses', '').startswith('actions/upload-artifact')]
    private = next(step for step in uploads if 'private-access' in step['with']['name'])
    assert private['with']['path'] == 'artifacts/r3-connected-preview/private-access.txt'
    assert private['with']['retention-days'] == 1
    final = next(step for step in uploads if 'final-evidence' in step['with']['name'])
    assert set(final['with']['path'].splitlines()) == {
        'artifacts/r3-connected-preview/final-audit.json',
        'artifacts/r3-connected-preview/cleanup.json',
        'artifacts/r3-connected-preview/expiry.json',
    }
