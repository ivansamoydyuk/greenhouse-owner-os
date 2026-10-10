"""Stage G browser/API/PostgreSQL proof, local disposable TEST lifecycle only."""
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


def require_free_preview_port(port=4173):
    # A stopped preview can leave accepted connections in TIME_WAIT. Match the
    # server's reuse semantics, but still fail for any live listener.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))


def require_fixture_metadata():
    subprocess.run([sys.executable, '-c',
        'import scripts.owner_recovery_browser_evidence; '
        'from greenhouse_owner_os.infrastructure.models import Base; '
        'assert Base.metadata.sorted_tables'],
        cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT/'src')+os.pathsep+str(ROOT)},
        check=True, timeout=20)


def main():
    require_disposable_database(os.environ['TEST_DATABASE_URL'], os.getenv('APP_ENV'))
    assert not os.getenv('PREVIEW_PUBLIC_ORIGIN'), 'G proof must remain local TEST preview'
    require_free_preview_port()
    require_fixture_metadata()
    from tests.integration.conftest import _reset_and_migrate
    _reset_and_migrate(os.environ['TEST_DATABASE_URL'])
    artifacts = Path(os.environ.get('E2E_ARTIFACTS', ROOT/'artifacts')).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    (ROOT/'artifacts').mkdir(exist_ok=True)
    def snapshot():
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in ('src', 'alembic', 'scripts', 'tests', 'openapi', '.github')
                for p in sorted((ROOT/folder).rglob('*'))
                if p.is_file() and '__pycache__' not in p.parts and not p.name.endswith('.pyc')}
    before = snapshot()
    (artifacts/'g-inputs-before.json').write_text(json.dumps(dict(
        head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        status=subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True), hashes=before), indent=2))
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'src')+os.pathsep+str(ROOT),
           'PREVIEW_SECRET': secrets.token_urlsafe(32), 'PREVIEW_PORT': '4173', 'PREVIEW_BIND': '127.0.0.1',
           'PREVIEW_URL': 'http://127.0.0.1:4173', 'E2E_ARTIFACTS': str(artifacts), 'PYTHON': sys.executable}
    env.pop('R2_REVIEW_FIXTURES', None)
    try:
        run_preview(env, ['owner-expense-recovery'], str(artifacts/'preview-expense-recovery.log'))
    finally:
        after = snapshot()
        (artifacts/'g-inputs-after.json').write_text(json.dumps(dict(hashes=after, unchanged=before==after), indent=2))
        assert before == after, 'Inputs changed during expense recovery browser proof'


if __name__ == '__main__':
    main()
