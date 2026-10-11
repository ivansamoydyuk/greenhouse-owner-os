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
