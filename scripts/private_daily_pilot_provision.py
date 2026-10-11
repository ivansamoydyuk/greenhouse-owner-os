"""Offline synthetic TEST credential provisioning; no HTTP activation endpoint."""
import argparse, json, os, secrets
from pathlib import Path
from uuid import UUID
from greenhouse_owner_os.preview import build_preview
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database

def require(condition, message):
    if not condition:
        raise RuntimeError(message)
p = argparse.ArgumentParser()
p.add_argument('--worker-id')
p.add_argument('--expired-cookie-participant')
p.add_argument('--output')
p.add_argument('--reset-fixture-rate-limits', action='store_true')
a = p.parse_args()
require(os.environ.get('APP_ENV') == 'test' and os.environ.get('R3_DAILY_PILOT') == '1' and (os.environ.get('PILOT_LOCAL_TEST') == '1'), 'Private TEST provisioning requirement failed')
require(sum(map(bool, (a.worker_id, a.expired_cookie_participant, a.reset_fixture_rate_limits))) == 1, 'Private TEST provisioning requirement failed')
if not a.reset_fixture_rate_limits:
    require(a.output, 'Private TEST provisioning requirement failed')
    out = Path(a.output).resolve()
    require('artifacts' not in out.parts and (not out.is_relative_to(Path.cwd().resolve())), 'Credential output must remain outside source and artifacts')
    require(not out.exists(), 'Refuse credential output overwrite')
url = os.environ['TEST_DATABASE_URL']
require_disposable_database(url, 'test')
app = build_preview(url, os.environ.get('PREVIEW_SECRET') or secrets.token_urlsafe(32), environment='test')
if a.reset_fixture_rate_limits:
    from sqlalchemy import text
    with app.state.pilot_auth.factory() as u:
        u.session.execute(text('DELETE FROM core.pilot_login_attempts'))
        u.commit()
    print('ISOLATED_TEST_RATE_BUCKETS_RESET_BETWEEN_INDEPENDENT_FIXTURES')
    raise SystemExit
if a.expired_cookie_participant:
    from sqlalchemy import text
    from datetime import datetime, timedelta, timezone
    from greenhouse_owner_os.daily_pilot import digest, COOKIE
    participant = UUID(a.expired_cookie_participant)
    token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    with app.state.pilot_auth.factory() as u:
        require(u.session.scalar(text('SELECT count(*) FROM core.pilot_participants WHERE id=:p AND deployment_id=:d AND enabled'), {'p': participant, 'd': app.state.pilot_auth.deployment_id}) == 1, 'Private TEST provisioning requirement failed')
        u.session.execute(text('INSERT INTO core.pilot_sessions(token_hash,participant_id,created_at,expires_at) VALUES(:h,:p,:created,:expires)'), {'h': digest(token), 'p': participant, 'created': now - timedelta(hours=2), 'expires': now - timedelta(hours=1)})
        u.commit()
    credential = {'name': COOKIE, 'value': token, 'url': os.environ.get('PREVIEW_URL', 'http://127.0.0.1:4173'), 'httpOnly': True, 'secure': False, 'sameSite': 'Strict'}
else:
    worker = UUID(a.worker_id)
    credential = dict(username='worker-' + worker.hex, password=secrets.token_urlsafe(24))
    pid = app.state.pilot_auth.provision(credential['username'], credential['password'], worker_id=worker)
    credential.update(worker_id=str(worker), participant_id=pid)
out.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
with os.fdopen(fd, 'w') as f:
    json.dump(credential, f)
registry = Path(os.environ['PILOT_E2E_CREDENTIALS'] + '.secrets.json').resolve()
require('artifacts' not in registry.parts and (not registry.is_relative_to(Path.cwd().resolve())), 'Private TEST provisioning requirement failed')
entries = json.loads(registry.read_text()) if registry.exists() else []
entries.append({k: v for k, v in credential.items() if k in ('password', 'value')})
fd = os.open(registry, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 384)
with os.fdopen(fd, 'w') as f:
    json.dump(entries, f)
print('PRIVATE_DAILY_TEST_OFFLINE_FIXTURE_READY')
