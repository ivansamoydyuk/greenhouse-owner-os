"""AUD-003 focused browser/API/PostgreSQL proof in an isolated TEST lifecycle."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from scripts.run_owner_e2e import run_preview


def assert_preview_port_available(port=4173):
    with socket.socket() as probe:
        # A preceding isolated browser gate may leave closed sockets in TIME_WAIT.
        # Match the server's reuse behavior without accepting a live listener.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))


def main():
    require_disposable_database(os.environ['TEST_DATABASE_URL'], os.getenv('APP_ENV'))
    assert not os.getenv('PREVIEW_PUBLIC_ORIGIN'), 'Focused proof must stay on local TEST preview'
    assert_preview_port_available()
    from tests.integration.conftest import _reset_and_migrate
    _reset_and_migrate(os.environ['TEST_DATABASE_URL'])
    artifacts = Path(os.environ.get('E2E_ARTIFACTS', ROOT/'artifacts')).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    names = ['src/greenhouse_owner_os/web/'+name for name in ('app.js', 'owner-r2.js', 'navigation.js', 'personal-tasks.js', 'style.css')]
    names += ['src/greenhouse_owner_os/api/owner_capture.py', 'src/greenhouse_owner_os/services/owner_capture.py',
              'tests/e2e/owner-personal-tasks.mjs', 'scripts/run_personal_tasks_e2e.py']
    def snapshot():
        return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}
    before = snapshot()
    (artifacts/'personal-inputs-before.json').write_text(json.dumps(dict(
        head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        status=subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
        hashes=before), indent=2))
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'src')+os.pathsep+str(ROOT),
           'PREVIEW_SECRET': secrets.token_urlsafe(32), 'PREVIEW_PORT': '4173', 'PREVIEW_BIND': '127.0.0.1',
           'PREVIEW_URL': 'http://127.0.0.1:4173', 'E2E_ARTIFACTS': str(artifacts), 'PYTHON': sys.executable}
    env.pop('R2_REVIEW_FIXTURES', None)
    try:
        run_preview(env, ['owner-personal-tasks'], str(artifacts/'preview-personal-tasks.log'))
    finally:
        after = snapshot()
        (artifacts/'personal-inputs-after.json').write_text(json.dumps(dict(hashes=after, unchanged=before==after), indent=2))
        assert before == after, 'Inputs changed during personal-task browser verification'


if __name__ == '__main__':
    main()
