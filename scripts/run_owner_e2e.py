"""Run real authenticated browsers against explicitly disposable previews only.

Legacy regression and measured-friction fixtures get separate DB lifecycles so
immutable reversed audit history cannot alter another suite's starting state.
"""
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
import httpx
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run_preview(env,suites,log_name,*,agent_browser=False):
    with (ROOT/'artifacts'/log_name).open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','greenhouse_owner_os.preview'],env=env,cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if server.poll() is not None:raise RuntimeError('preview exited; see artifacts/'+log_name)
                try:
                    if httpx.get(env['PREVIEW_URL']+'/health',timeout=1,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('preview readiness timed out')
            for index,suite in enumerate(suites):
                subprocess.run(['node','tests/e2e/'+suite+'.mjs'],env=env,cwd=ROOT,check=True)
                if index==0 and agent_browser:
                    agent=os.environ['AGENT_BROWSER']
                    options=['--executable-path',os.environ['CHROME_PATH']] if os.getenv('CHROME_PATH') else []
                    subprocess.run([agent,*options,'open',env['PREVIEW_URL']],env=env,cwd=ROOT,check=True)
                    subprocess.run([agent,'snapshot','-i'],env=env,cwd=ROOT,check=True)
                    subprocess.run([agent,'screenshot',str(ROOT/'artifacts/agent-browser-check.png')],env=env,cwd=ROOT,check=True)
                    subprocess.run([agent,'close'],env=env,cwd=ROOT,check=True)
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()


def main():
    require_disposable_database(os.environ['TEST_DATABASE_URL'],os.getenv('APP_ENV'))
    from tests.integration.conftest import _reset_and_migrate
    _reset_and_migrate(os.environ['TEST_DATABASE_URL'])
    env={**os.environ,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT),'PREVIEW_SECRET':secrets.token_urlsafe(32),
         'PREVIEW_PORT':'4173','PREVIEW_BIND':'127.0.0.1','PREVIEW_URL':'http://127.0.0.1:4173'}
    r2=bool(os.getenv('R2_E2E_ONLY'))
    if r2:env['R2_REVIEW_FIXTURES']='1'
    (ROOT/'artifacts').mkdir(exist_ok=True)
    suites=['owner-r2-ui-fencing']
    if not r2:
        if not any(os.getenv(flag) for flag in ('HARVEST_E2E_ONLY','INBOX_E2E_ONLY','INVENTORY_E2E_ONLY')):suites+=['owner','business']
        if not any(os.getenv(flag) for flag in ('INBOX_E2E_ONLY','INVENTORY_E2E_ONLY')):suites+=['harvest']
        if not os.getenv('INVENTORY_E2E_ONLY'):suites+=['inbox']
        suites+=['inventory','workers']
    suites+=['owner-r2','owner-modern-ui','owner-iphone-ui']
    run_preview(env,suites,'preview.log',agent_browser=bool(os.getenv('AGENT_BROWSER')) and not r2)
    # Metrics must measure the owner's flow against a fresh known TEST state,
    # not inherit prior suites' reversed settlements, aliases, or task evidence.
    _reset_and_migrate(os.environ['TEST_DATABASE_URL'])
    env={**env,'PREVIEW_SECRET':secrets.token_urlsafe(32)}
    run_preview(env,['owner-friction'],'preview-friction.log')
    # Queue/error fault injection gets its own known TEST state so retained
    # invalid originals and unconfirmed sources cannot alter legacy journeys.
    _reset_and_migrate(os.environ['TEST_DATABASE_URL'])
    env={**env,'PREVIEW_SECRET':secrets.token_urlsafe(32)}
    run_preview(env,['owner-source-queue-errors'],'preview-source-queue-errors.log')


if __name__=='__main__':main()
