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
    assert secret_steps[0]['run'] == 'python scripts/temporary_owner_preview.py --start'
    assert job['steps'][-1]['if'] == 'always()'
    upload = [step for step in job['steps'] if step.get('uses', '').startswith('actions/upload-artifact@')]
    assert len(upload) == 1
    assert upload[0]['uses'] == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
    assert upload[0]['if'] == "steps.metadata.outcome == 'success'"
    assert upload[0]['with']['path'] == '${{ runner.temp }}/owner-preview-ready.json'
    assert upload[0]['with']['retention-days'] == '1'
    assert any(step.get('run') == 'python scripts/temporary_owner_preview.py --hold' for step in job['steps'])
    assert any(step.get('run') == 'python scripts/temporary_owner_preview.py --cleanup' and step.get('if') == 'always()' for step in job['steps'])
    for forbidden in ('download-artifact', 'cache:', 'id-token:', 'contents: write', 'pull_request:', 'environment:', 'PILOT_E2E_CREDENTIALS'):
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


def metadata():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    return dict(url='https://one-two.trycloudflare.com', username='owner', sha='a'*40,
        expires_at=(now + timedelta(minutes=34)).isoformat()), now


def test_exact_nonsecret_readiness_schema():
    value, now = metadata()
    assert preview.validate_metadata(value, 'a'*40, now)
    assert set(value) == {'url','username','sha','expires_at'}


@pytest.mark.parametrize('key,value', [('password','do-not-publish'), ('token','x'),
    ('cookie','x'), ('pid',123), ('url','https://one.trycloudflare.com/pilot/'),
    ('url','https://one.trycloudflare.com?token=x'), ('url','https://one.trycloudflare.com/#x'),
    ('url','https://one.trycloudflare.com/../../secret'), ('url','file:///tmp/secret'),
    ('url','https://user:password@one.trycloudflare.com'), ('username','worker'),
    ('sha','b'*40), ('expires_at','2020-01-01T00:00:00+00:00'), ('expires_at','9999-01-01T00:00:00+00:00'),
    ('expires_at','2026-10-13T00:00:00'), ('sha',{'password':'x'})])
def test_metadata_rejects_extra_fields_paths_tokens_and_unbounded_expiry(key, value):
    data, now = metadata(); data[key] = value
    with pytest.raises((RuntimeError, ValueError)):
        preview.validate_metadata(data, 'a'*40, now)


def test_metadata_rejects_missing_fields():
    data, now = metadata(); del data['url']
    with pytest.raises(RuntimeError): preview.validate_metadata(data, 'a'*40, now)


def test_readiness_file_atomic_private_and_no_symlink(tmp_path):
    import os
    data, now = metadata(); path=tmp_path/'ready.json'
    preview.atomic_json(path,data)
    assert preview.read_json(path) == data
    assert path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path/'ready.json.pending').exists()
    with pytest.raises(RuntimeError): preview.atomic_json(path,data)
    link=tmp_path/'link.json'; link.symlink_to(path)
    with pytest.raises(OSError): preview.read_json(link)
    with pytest.raises(RuntimeError): preview.atomic_json(link,data)
    missing=tmp_path/'missing.json'; missing.symlink_to(tmp_path/'absent')
    with pytest.raises(RuntimeError): preview.atomic_json(missing,data)


def test_readiness_refuses_oversized_file(tmp_path):
    path=tmp_path/'ready.json'; path.write_text('x'*1025)
    with pytest.raises(RuntimeError): preview.read_json(path)


def test_hold_fails_if_owned_process_ends_early(monkeypatch):
    data, _ = metadata()
    monkeypatch.setenv('EXPECTED_SHA','a'*40)
    monkeypatch.setattr(preview,'check_ready',lambda:data)
    monkeypatch.setattr(preview,'owned_process',lambda:None)
    with pytest.raises(RuntimeError,match='ended early'): preview.hold()


def test_cleanup_never_kills_a_reused_process(tmp_path,monkeypatch):
    monkeypatch.setenv('RUNNER_TEMP',str(tmp_path))
    preview.atomic_json(tmp_path/'owner-preview-process.json',{'pid':123,'started':'111'})
    monkeypatch.setattr(preview,'process_identity',lambda pid:'222')
    kill=MagicMock(); monkeypatch.setattr(preview.os,'killpg',kill)
    preview.cleanup(); kill.assert_not_called()


def test_background_lifetime_keeps_runner_tracking_and_hides_logs():
    raw=(ROOT/'scripts/temporary_owner_preview.py').read_text()
    assert 'start_new_session=True' in raw
    assert 'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL' in raw
    assert 'RUNNER_TRACKING_ID' not in raw
    assert raw.index('verify_denial(client)',raw.index('smoke_deadline')) < raw.index("atomic_json(runtime_path('owner-preview-ready.json')")


def test_readiness_rejects_duplicate_fields(tmp_path):
    path=tmp_path/'ready.json'; path.write_text('{"url":"first","url":"second"}')
    with pytest.raises(RuntimeError, match='Duplicate'): preview.read_json(path)


def test_real_background_handshake_returns_while_owned_child_lives(tmp_path, monkeypatch):
    import json, subprocess, sys
    monkeypatch.setenv('RUNNER_TEMP',str(tmp_path))
    monkeypatch.setenv('EXPECTED_SHA','a'*40)
    fake_password='local-handshake-example-only'
    monkeypatch.setenv('TEST_PREVIEW_OWNER_PASSWORD',fake_password)
    value, _ = metadata()
    code = "import os,json,time; from pathlib import Path; "
    code += "assert os.environ.pop('TEST_PREVIEW_OWNER_PASSWORD'); "
    code += "p=Path(os.environ['RUNNER_TEMP'])/'owner-preview-ready.json'; "
    code += "p.write_text(" + repr(json.dumps(value)) + "); time.sleep(30)"
    real_popen = subprocess.Popen
    children=[]
    def launch(command, **kwargs):
        assert command[-1] == '--serve'
        child=real_popen([sys.executable,'-c',code],**kwargs);children.append(child);return child
    monkeypatch.setattr(preview.subprocess,'Popen',launch)
    try:
        preview.start()
        assert children[0].poll() is None
        assert 'TEST_PREVIEW_OWNER_PASSWORD' not in preview.os.environ
        assert preview.check_ready() == value
        assert set(preview.read_json(tmp_path/'owner-preview-process.json')) == {'pid','started'}
        assert all(fake_password not in p.read_text() for p in tmp_path.iterdir())
        preview.cleanup()
        children[0].wait(timeout=5)
        assert preview.owned_process() is None
    finally:
        for child in children:
            if child.poll() is None: child.kill()
            child.wait(timeout=5)


def test_hold_accepts_child_exit_exactly_at_fixed_expiry(monkeypatch):
    from datetime import datetime, timedelta, timezone
    data, current = metadata()
    expires=datetime.fromisoformat(data['expires_at'])
    class Clock:
        value=current
        @staticmethod
        def now(zone): return Clock.value
    monkeypatch.setattr(preview,'datetime',Clock)
    monkeypatch.setenv('EXPECTED_SHA','a'*40)
    monkeypatch.setattr(preview,'check_ready',lambda:data)
    def finished():
        Clock.value=expires
        return None
    monkeypatch.setattr(preview,'owned_process',finished)
    preview.hold()
