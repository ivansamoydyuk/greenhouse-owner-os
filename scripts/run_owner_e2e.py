"""Run a real authenticated browser against the disposable preview only.

Called AFTER Finance tests. Reset is explicit, exact-target guarded and never
available as an HTTP route. E2E reverses successful synthetic postings.
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
sys.path.insert(0, str(ROOT))

def main():
    require_disposable_database(os.environ['TEST_DATABASE_URL'],os.getenv('APP_ENV'))
    # Shared reviewed guard/migration reset used by integration tests.
    from tests.integration.conftest import _reset_and_migrate
    _reset_and_migrate(os.environ['TEST_DATABASE_URL'])
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PREVIEW_SECRET':secrets.token_urlsafe(32),
         'PREVIEW_PORT':'4173','PREVIEW_BIND':'127.0.0.1','PREVIEW_URL':'http://127.0.0.1:4173'}
    (ROOT/'artifacts').mkdir(exist_ok=True)
    with (ROOT/'artifacts/preview.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','greenhouse_owner_os.preview'],env=env,cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if server.poll() is not None:raise RuntimeError('preview exited; see artifacts/preview.log')
                try:
                    if httpx.get(env['PREVIEW_URL']+'/health',timeout=1,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('preview readiness timed out')
            if os.getenv('AGENT_BROWSER'):
                agent=os.environ['AGENT_BROWSER']
                options=['--executable-path',os.environ['CHROME_PATH']] if os.getenv('CHROME_PATH') else []
                subprocess.run([agent,*options,'open',env['PREVIEW_URL']],env=env,cwd=ROOT,check=True)
                subprocess.run([agent,'snapshot','-i'],env=env,cwd=ROOT,check=True)
                subprocess.run([agent,'screenshot',str(ROOT/'artifacts/agent-browser-check.png')],env=env,cwd=ROOT,check=True)
                subprocess.run([agent,'close'],env=env,cwd=ROOT,check=True)
            if not os.getenv('HARVEST_E2E_ONLY') and not os.getenv('INBOX_E2E_ONLY'):
                subprocess.run(['node','tests/e2e/owner.mjs'],env=env,cwd=ROOT,check=True)
            if not os.getenv('HARVEST_E2E_ONLY') and not os.getenv('INBOX_E2E_ONLY'):
                subprocess.run(['node','tests/e2e/business.mjs'],env=env,cwd=ROOT,check=True)
            if not os.getenv('INBOX_E2E_ONLY'):
                subprocess.run(['node','tests/e2e/harvest.mjs'],env=env,cwd=ROOT,check=True)
            subprocess.run(['node','tests/e2e/inbox.mjs'],env=env,cwd=ROOT,check=True)
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()

if __name__=='__main__':main()
