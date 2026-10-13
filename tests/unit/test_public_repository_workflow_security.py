"""Public CI stays unprivileged, checks every default PR, and never opens access."""
from itertools import product
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / '.github/workflows'
CORE = (
    'postgresql-verification.yml',
    'r3-connected-pilot.yml',
    'capture-daily-checkpoint.yml',
    'day-workers-checkpoint.yml',
)
HISTORICAL = ('owner-finance-preview.yml', 'owner-r3-prototype.yml')
DEFAULT = '01-architecture-database'
REPOSITORY = 'ivansamoydyuk/greenhouse-owner-os'
# Verified against official actions/* Git refs on 2026-10-13, preserving existing
# major lines. A dependency update must independently verify a new immutable ref.
ACTION_PINS = {
    'actions/checkout': '11d5960a326750d5838078e36cf38b85af677262',
    'actions/setup-python': 'a26af69be951a213d495a4c3e4e4022e16d87065',
    'actions/setup-node': '49933ea5288caeca8642d1e84afbd3f7d6820020',
    'actions/upload-artifact': 'ea165f8d65b6e75b540449e92b4886f43607fa02',
}
REQUIRED_CONTEXTS = {
    'verify', 'owner-r2-browser', 'connected-browser', 'private-daily-browser',
    'corpus-browser-sql (candidate, chromium)',
    'corpus-browser-sql (candidate, webkit)', 'day-workers-browser-sql',
}


def load(name):
    return yaml.load((DIRECTORY / name).read_text(), Loader=yaml.BaseLoader)


@pytest.mark.parametrize('name', CORE)
def test_every_default_pr_and_push_emits_current_checks_without_path_filters(name):
    workflow = load(name)
    assert set(workflow['on']) == {'pull_request', 'push', 'workflow_dispatch'}
    assert workflow['on']['pull_request'] == {'branches': [DEFAULT], 'types': ['opened', 'synchronize', 'reopened', 'edited']}
    assert workflow['on']['push'] == {'branches': [DEFAULT]}
    assert workflow['concurrency'] == {
        'group': '${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}',
        'cancel-in-progress': 'true',
    }
    for job in workflow['jobs'].values():
        assert 'if' not in job and 'needs' not in job
        assert job.get('continue-on-error', 'false') == 'false'
        assert all(step.get('continue-on-error', 'false') == 'false' for step in job['steps'])
        for step in job['steps']:
            # The optional frozen historical measurement is not a current gate.
            if step.get('name') != 'Before evidence on immutable delivered baseline':
                assert 'github.ref_name' not in step.get('if', '')


def test_seven_required_contexts_remain_stable_and_are_not_synthetic_successes():
    contexts = set()
    for name in CORE:
        for key, job in load(name)['jobs'].items():
            assert 'name' not in job
            matrix = job.get('strategy', {}).get('matrix')
            if matrix:
                contexts.update(key + ' (' + ', '.join(values) + ')' for values in product(*matrix.values()))
            else:
                contexts.add(key)
            assert any('pytest' in step.get('run', '') or 'node tests/e2e/' in step.get('run', '') or 'run_owner_e2e.py' in step.get('run', '') for step in job['steps'])
    assert contexts == REQUIRED_CONTEXTS


@pytest.mark.parametrize('name', CORE + HISTORICAL)
def test_actions_are_pinned_read_only_hosted_and_do_not_persist_git_credentials(name):
    workflow = load(name)
    assert workflow['permissions'] == {'contents': 'read'}
    for job in workflow['jobs'].values():
        assert job['runs-on'] == 'ubuntu-latest'
        assert 0 < int(job['timeout-minutes']) <= 50
        assert 'permissions' not in job and 'environment' not in job and 'uses' not in job
        for step in job['steps']:
            if 'uses' in step:
                action, sha = step['uses'].split('@')
                assert sha == ACTION_PINS[action]
                assert re.fullmatch(r'[0-9a-f]{40}', sha)
                if action == 'actions/checkout':
                    assert step['with']['persist-credentials'] == 'false'
                    assert step['with']['ref'] == '${{ github.sha }}'
                if action == 'actions/upload-artifact':
                    assert 0 < int(step['with']['retention-days']) <= 14
            assert 'cache' not in step.get('with', {})
    raw = (DIRECTORY / name).read_text()
    assert not re.search(r'\$\{\{[^}]*\bsecrets\.', raw)
    for forbidden in ('pull_request_target:', 'workflow_run:', 'repository_dispatch:', 'issue_comment:', 'workflow_call:', 'self-hosted', 'id-token:', 'secrets: inherit'):
        assert forbidden not in raw
    for job in workflow['jobs'].values():
        for step in job['steps']:
            # Event data must be passed as data through env, never shell source.
            assert '${{' not in step.get('run', '')


@pytest.mark.parametrize('name', CORE)
def test_current_test_workflows_never_publish_a_tunnel_or_execute_downloaded_artifacts(name):
    raw = (DIRECTORY / name).read_text()
    for forbidden in ('cloudflared', 'trycloudflare', 'actions/download-artifact', 'private-access.txt', 'PREVIEW_PUBLIC_ORIGIN'):
        assert forbidden not in raw


def evaluate_private_launch(condition, context):
    """Evaluate the small allowlisted conjunction grammar, never arbitrary code."""
    terms = condition.split(' && ')
    expected = {
        'github.repository': REPOSITORY,
        'github.event.repository.private': True,
        'github.event_name': 'workflow_dispatch',
        'github.ref': 'refs/heads/' + DEFAULT,
        'inputs.launch_private_test': True,
    }
    parsed = {}
    for term in terms:
        key, literal = term.split(' == ')
        assert key in expected and key not in parsed
        value = True if literal == 'true' else literal.removeprefix("'").removesuffix("'")
        assert value == expected[key]
        parsed[key] = value
    assert parsed == expected
    return all(context[key] == value for key, value in parsed.items())


@pytest.mark.parametrize('name', HISTORICAL)
def test_historical_preview_quarantine_has_no_automatic_or_rerun_authority(name):
    workflow = load(name)
    assert set(workflow['on']) == {'workflow_dispatch'}
    request = workflow['on']['workflow_dispatch']['inputs']['launch_private_test']
    assert request['type'] == 'boolean' and request['default'] == 'false'
    guard = workflow['jobs']['preview']['if']
    for repository, private, event, ref, requested in product(
        [REPOSITORY, 'contributor/greenhouse-owner-os'], [False, True],
        ['push', 'pull_request', 'workflow_dispatch'],
        ['refs/heads/' + DEFAULT, 'refs/heads/spoofed', 'refs/pull/1/merge'], [False, True],
    ):
        context = dict(zip(
            ['github.repository', 'github.event.repository.private', 'github.event_name', 'github.ref', 'inputs.launch_private_test'],
            [repository, private, event, ref, requested],
        ))
        allowed = evaluate_private_launch(guard, context)
        assert allowed == (repository == REPOSITORY and private and event == 'workflow_dispatch' and ref == 'refs/heads/' + DEFAULT and requested)
        if not private:
            assert not allowed
    raw = (DIRECTORY / name).read_text()
    for forbidden in ('github.run_attempt', 'github.head_ref', 'actions/download-artifact', 'preview-access.txt', 'owner-r3-private-access', 'owner-workers-review-access'):
        assert forbidden not in raw


def test_workflow_inventory_requires_explicit_review_for_new_automation():
    assert {path.name for path in DIRECTORY.glob('*.yml')} == set(CORE + HISTORICAL + ('temporary-owner-test.yml',))
    assert not list(DIRECTORY.glob('*.yaml'))


@pytest.mark.parametrize('name', CORE + HISTORICAL)
def test_every_artifact_upload_is_gated_by_a_successful_prior_guard(name):
    for job in load(name)['jobs'].values():
        guards = {}
        for step in job['steps']:
            command = step.get('run', '')
            if 'scripts/private_daily_pilot_artifact_guard.py artifacts' in command:
                assert step['if'] == 'always()'
                assert '--generic' in command or '--require-inventory "$PILOT_E2E_CREDENTIALS"' in command
                guards[step['id']] = step
            if step.get('uses', '').startswith('actions/upload-artifact@'):
                assert guards, 'No artifact upload may precede the disclosure guard'
                assert any("steps." + guard + ".outcome == 'success'" in step.get('if', '') for guard in guards)
                paths = step['with']['path']
                assert 'RUNNER_TEMP' not in paths and 'PILOT_E2E_CREDENTIALS' not in paths
