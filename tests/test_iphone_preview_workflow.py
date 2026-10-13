"""Historical owner review can never open a public-repository access window."""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/owner-finance-preview.yml'


def test_ui_preview_launch_requires_explicit_private_repository_manual_start():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    triggers = workflow.get('on', workflow.get(True))
    assert set(triggers) == {'workflow_dispatch'}
    request = triggers['workflow_dispatch']['inputs']['launch_private_test']
    assert request['type'] == 'boolean' and request['default'] is False
    guard = workflow['jobs']['preview']['if']
    for boundary in (
        "github.repository == 'ivansamoydyuk/greenhouse-owner-os'",
        'github.event.repository.private == true',
        "github.event_name == 'workflow_dispatch'",
        "github.ref == 'refs/heads/01-architecture-database'",
        'inputs.launch_private_test == true',
    ):
        assert boundary in guard.split(' && ')
    assert '||' not in guard and 'run_attempt' not in guard and 'head_ref' not in guard


def test_historical_preview_has_no_access_key_artifact_and_keeps_exact_identity():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    raw = WORKFLOW.read_text()
    job = workflow['jobs']['preview']
    assert job['env']['OWNER_PREVIEW_FEATURE_SHA'] == '${{ github.sha }}'
    assert "assert config['feature_sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA']" in raw
    assert 'preview-access.txt' not in raw
    assert 'owner-workers-review-access' not in raw
    assert all(not step.get('uses', '').startswith('actions/upload-artifact@') for step in job['steps'])
    stop = job['steps'][-1]
    assert stop['name'] == 'Stop preview' and stop['if'] == 'always()'
    assert 'if [ -f "$process_file" ]' in stop['run']
