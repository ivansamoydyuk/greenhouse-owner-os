"""Run AUD-004/005 browser proof in its own disposable preview lifecycle."""
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from scripts.run_owner_e2e import run_preview


def main():
    require_disposable_database(os.environ['TEST_DATABASE_URL'], os.getenv('APP_ENV'))
    from tests.integration.conftest import _reset_and_migrate

    _reset_and_migrate(os.environ['TEST_DATABASE_URL'])
    artifacts = Path(os.environ.get('E2E_ARTIFACTS', ROOT / 'artifacts')).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src') + os.pathsep + str(ROOT),
           'PREVIEW_SECRET': secrets.token_urlsafe(32), 'PREVIEW_PORT': '4173',
           'PREVIEW_BIND': '127.0.0.1', 'PREVIEW_URL': 'http://127.0.0.1:4173',
           'E2E_ARTIFACTS': str(artifacts)}
    # The focused source proof creates its own originals through the browser.
    env.pop('R2_REVIEW_FIXTURES', None)
    run_preview(env, ['owner-source-queue-errors'], str(artifacts / 'preview-source-queue-errors.log'))


if __name__ == '__main__':
    main()
