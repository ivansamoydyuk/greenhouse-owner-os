"""Separate public manual preview boundary, without weakening historical gates."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('temporary_owner_preview', ROOT / 'scripts/temporary_owner_preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def context():
    return dict(GITHUB_REPOSITORY=preview.REPOSITORY, GITHUB_ACTOR=preview.OWNER,
        GITHUB_TRIGGERING_ACTOR=preview.OWNER, GITHUB_EVENT_NAME='workflow_dispatch',
        GITHUB_REF=preview.REF, GITHUB_RUN_ATTEMPT='1', GITHUB_ACTIONS='true',
        RUNNER_ENVIRONMENT='github-hosted', EXPECTED_SHA='a'*40,
        GITHUB_SHA='a'*40, GITHUB_RUN_ID='123', APP_ENV='test')


def test_exact_owner_context_passes():
    assert preview.validate_context(context(), 'a'*40, '') == 'a'*40


@pytest.mark.parametrize('key,value', [
    ('GITHUB_REPOSITORY','fork/repo'), ('GITHUB_ACTOR','someone'),
    ('GITHUB_TRIGGERING_ACTOR','someone'), ('GITHUB_EVENT_NAME','pull_request'),
    ('GITHUB_REF','refs/heads/feature'), ('GITHUB_RUN_ATTEMPT','2'),
    ('GITHUB_ACTIONS','false'), ('RUNNER_ENVIRONMENT','self-hosted'),
    ('EXPECTED_SHA','b'*40), ('EXPECTED_SHA','x'*40), ('GITHUB_SHA','b'*40),
    ('GITHUB_RUN_ID','123;evil'), ('APP_ENV','production'),
])
def test_context_rejects_every_changed_boundary(key, value):
    env = context(); env[key] = value
    with pytest.raises(RuntimeError):
        preview.validate_context(env, 'a'*40, '')


def test_context_rejects_dirty_or_other_checkout():
    with pytest.raises(RuntimeError): preview.validate_context(context(), 'b'*40, '')
    with pytest.raises(RuntimeError): preview.validate_context(context(), 'a'*40, ' M app.py')


@pytest.mark.parametrize('password', ['', 'x'*19, 'x'*201, 'x'*20+'\n', None])
def test_invalid_password_is_not_echoed(password):
    with pytest.raises(RuntimeError) as error: preview.validate_password(password)
    assert not password or password not in str(error.value)


def test_password_and_canonical_tunnel_origin():
    preview.validate_password('synthetic-only-password-example')
    assert preview.validate_origin('https://one-two.trycloudflare.com')


@pytest.mark.parametrize('origin', ['http://one.trycloudflare.com', 'https://evil.example',
    'https://one.trycloudflare.com.evil.example', 'https://one.trycloudflare.com/?token=x',
    'https://user@one.trycloudflare.com', 'https://one.trycloudflare.com/path'])
def test_origin_rejects_unexpected_gateway(origin):
    with pytest.raises(RuntimeError): preview.validate_origin(origin)


def test_provisioner_only_creates_one_owner():
    app = MagicMock()
    u = app.state.pilot_auth.factory.return_value.__enter__.return_value
    u.session.scalar.return_value = 0
    u.session.execute.return_value.all.return_value = [('owner','owner',None)]
    preview.provision_owner(app, 'synthetic-only-password-example')
    app.state.pilot_auth.provision.assert_called_once_with('owner', 'synthetic-only-password-example')
    u.session.scalar.return_value = 1
    with pytest.raises(RuntimeError): preview.provision_owner(app, 'synthetic-only-password-example')
    assert app.state.pilot_auth.provision.call_count == 1


def test_denial_smoke_fails_closed():
    client = MagicMock()
    client.get.return_value.status_code = 200
    with pytest.raises(RuntimeError): preview.verify_denial(client)


def test_manual_workflow_has_narrow_readonly_secret_scope_and_no_public_evidence():
    raw = (ROOT / '.github/workflows/temporary-owner-test.yml').read_text()
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents':'read'}
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert set(inputs) == {'expected_sha'} and inputs['expected_sha']['required'] == 'true'
    job = workflow['jobs']['temporary-owner']
    for clause in ["github.actor == 'ivansamoydyuk'", "github.triggering_actor == 'ivansamoydyuk'",
        'github.run_attempt == 1', 'inputs.expected_sha == github.sha',
        "github.ref == 'refs/heads/01-architecture-database'", "github.repository == 'ivansamoydyuk/greenhouse-owner-os'"]:
        assert clause in job['if']
    assert job['timeout-minutes'] == '45'
    assert workflow['concurrency']['cancel-in-progress'] == 'false'
    secret_steps = [s for s in job['steps'] if 'TEST_PREVIEW_OWNER_PASSWORD' in s.get('env',{})]
    assert len(secret_steps) == 1
    assert secret_steps[0]['run'] == 'python scripts/temporary_owner_preview.py'
    assert job['steps'][-1]['if'] == 'always()'
    for forbidden in ('upload-artifact', 'download-artifact', 'cache:', 'id-token:', 'contents: write', 'pull_request:', 'environment:', 'PILOT_E2E_CREDENTIALS'):
        assert forbidden not in raw
    for step in job['steps']:
        assert '${{' not in step.get('run','')
        if 'uses' in step:
            assert len(step['uses'].split('@')[1]) == 40
    assert job['steps'][0]['with']['persist-credentials'] == 'false'
    assert job['steps'][0]['with']['ref'] == '${{ github.sha }}'


def test_no_private_fixture_provisioning_or_secret_output():
    raw = (ROOT / 'scripts/temporary_owner_preview.py').read_text()
    assert 'private_daily_pilot_sql_evidence' not in raw
    assert 'PILOT_E2E_CREDENTIALS' not in raw
    assert "os.environ.pop('TEST_PREVIEW_OWNER_PASSWORD'" in raw
    assert 'access_log=False' in raw and "proxy_headers=False" in raw
    assert preview.DURATION == 35*60
    assert 'finally:' in raw and "'--volumes'" in raw
    assert 'hashlib.sha256(response.content).hexdigest() == CLOUDFLARED_SHA256' in raw
