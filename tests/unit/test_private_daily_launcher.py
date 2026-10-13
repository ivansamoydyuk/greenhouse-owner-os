"""Operational preflight guards must survive optimized Python execution."""
import ast,os,subprocess,sys
from pathlib import Path
import pytest

@pytest.mark.parametrize('flag,optimize',[('-O',None),(None,'1')])
def test_invalid_sha_is_rejected_even_when_assertions_disabled(flag,optimize):
    env=os.environ.copy();env['PILOT_EXPECTED_SHA']='invalid-test-sha'
    if optimize:env['PYTHONOPTIMIZE']=optimize
    args=[sys.executable]+([flag] if flag else [])+['scripts/run_private_daily_test.py','--check']
    result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode!=0
    assert 'RuntimeError: Private TEST preflight requirement failed' in result.stderr
    assert 'preflight' not in result.stdout


def test_persistent_launcher_does_not_use_optimizable_assert_guards():
    tree=ast.parse(Path('scripts/run_private_daily_test.py').read_text())
    assert not any(isinstance(node,ast.Assert) for node in ast.walk(tree))

@pytest.mark.parametrize('invalid',['environment','artifact_path'])
def test_offline_provision_rejects_unsafe_boundary_under_optimization(invalid):
    env=os.environ.copy();env.update(APP_ENV='test',R3_DAILY_PILOT='1',PILOT_LOCAL_TEST='1')
    output='/tmp/private-pilot-never-created-'+invalid+'.json'
    if invalid=='environment':env['APP_ENV']='production'
    else:output=str(Path('artifacts/private-pilot-never-created.json').resolve())
    assert not Path(output).exists()
    result=subprocess.run([sys.executable,'-O','scripts/private_daily_pilot_provision.py','--worker-id','00000000-0000-0000-0000-000000000001','--output',output],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode!=0 and 'RuntimeError:' in result.stderr
    assert not Path(output).exists()


@pytest.fixture
def private_launcher_preflight(monkeypatch):
    """Execute the real read-only preflight with synthetic database responses."""
    import runpy
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock
    import sqlalchemy

    sha = 'a' * 40
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    env = {
        'PILOT_EXPECTED_SHA': sha,
        'APP_ENV': 'test',
        'R3_CONNECTED_PILOT': '1',
        'R3_DAILY_PILOT': '1',
        'TEST_DATABASE_URL': 'postgresql+psycopg://postgres@127.0.0.1:5432/greenhouse_owner_os_test',
        'PREVIEW_BIND': '127.0.0.1',
        'PILOT_LOCAL_TEST': '1',
        'PILOT_EXPIRES_AT': expiry.isoformat(),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('PREVIEW_PUBLIC_ORIGIN', raising=False)
    monkeypatch.setenv('OWNER_PREVIEW_FEATURE_SHA', 'preflight-test')
    monkeypatch.setattr(sys, 'argv', ['scripts/run_private_daily_test.py', '--check'])
    git = MagicMock(side_effect=[sha + '\n', ''])
    monkeypatch.setattr(subprocess, 'check_output', git)
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    role = MagicMock()
    role.one.return_value = (False,) * 5
    deployments = MagicMock()
    deployments.all.return_value = [(expiry,)]
    connection.execute.side_effect = [role, deployments]
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda url: engine)

    def run(revision='20261012_0035', marker='r3-connected-pilot:synthetic-only:v1', privileges=False):
        connection.scalar.side_effect = [revision, marker, privileges]
        runpy.run_path('scripts/run_private_daily_test.py', run_name='__main__')
        return engine

    return run, git, role, deployments


def test_private_launcher_accepts_current_schema_and_reports_it(private_launcher_preflight, capsys):
    import json
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert ScriptDirectory.from_config(Config('alembic.ini')).get_heads() == ['20261012_0035']
    run, _, _, _ = private_launcher_preflight
    engine = run()
    report = json.loads(capsys.readouterr().out)
    assert report['migration'] == '20261012_0035'
    assert report['preflight'] == 'PASS'
    assert report['synthetic_only'] is True
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize('revision', ['20261012_0033', '20261012_0034', 'unknown', None])
def test_private_launcher_rejects_incompatible_schema(private_launcher_preflight, capsys, revision):
    run, _, _, _ = private_launcher_preflight
    with pytest.raises(RuntimeError, match='Private TEST preflight requirement failed'):
        run(revision=revision)
    assert not capsys.readouterr().out


@pytest.mark.parametrize('guard', ['sha', 'dirty', 'environment', 'public_bind', 'marker', 'role', 'privileges', 'expiry'])
def test_current_schema_does_not_bypass_private_launcher_guards(private_launcher_preflight, monkeypatch, capsys, guard):
    run, git, role, deployments = private_launcher_preflight
    kwargs = {}
    if guard == 'sha':
        git.side_effect = ['b' * 40 + '\n', '']
    elif guard == 'dirty':
        git.side_effect = ['a' * 40 + '\n', ' M scripts/run_private_daily_test.py\n']
    elif guard == 'environment':
        monkeypatch.setenv('APP_ENV', 'production')
    elif guard == 'public_bind':
        monkeypatch.setenv('PREVIEW_BIND', '0.0.0.0')
    elif guard == 'marker':
        kwargs['marker'] = None
    elif guard == 'role':
        role.one.return_value = (True, False, False, False, False)
    elif guard == 'privileges':
        kwargs['privileges'] = True
    elif guard == 'expiry':
        deployments.all.return_value = []
    with pytest.raises(RuntimeError):
        run(**kwargs)
    assert not capsys.readouterr().out
