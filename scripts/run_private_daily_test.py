"""Resume an existing private synthetic TEST database; never initialize/reset it."""
import argparse, json, os, re, subprocess, threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from greenhouse_owner_os.daily_pilot import parse_expiry
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database

def require(condition, message):
    if not condition:
        raise RuntimeError(message)
p = argparse.ArgumentParser()
p.add_argument('--check', action='store_true')
a = p.parse_args()
root = Path(__file__).resolve().parents[1]
sha = os.environ['PILOT_EXPECTED_SHA']
require(re.fullmatch('[0-9a-f]{40}', sha), 'Private TEST preflight requirement failed')
require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == sha, 'Use the exact verified build')
require(not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root, text=True), 'Tracked source is modified')
require(os.environ.get('APP_ENV') == 'test' and os.environ.get('R3_CONNECTED_PILOT') == '1' and (os.environ.get('R3_DAILY_PILOT') == '1'), 'Private TEST preflight requirement failed')
url = os.environ['TEST_DATABASE_URL']
require_disposable_database(url, 'test')
parsed = make_url(url)
require(parsed.host in ('127.0.0.1', 'localhost') and parsed.database == 'greenhouse_owner_os_test' and (not parsed.query), 'Private TEST preflight requirement failed')
bind = os.getenv('PREVIEW_BIND', '127.0.0.1')
require(bind in ('127.0.0.1', 'localhost', '::1'), 'Use existing private TLS gateway; no public app listener')
origin = os.getenv('PREVIEW_PUBLIC_ORIGIN')
local = os.getenv('PILOT_LOCAL_TEST') == '1'
require(origin or local, 'Approved private HTTPS origin required')
if origin:
    o = urlsplit(origin)
    require(o.scheme == 'https' and o.hostname and (not o.username) and (not o.query) and (not o.fragment) and (o.path in ('', '/')), 'Private TEST preflight requirement failed')
expiry = parse_expiry(os.environ['PILOT_EXPIRES_AT'])
seconds = (expiry - datetime.now(timezone.utc)).total_seconds()
require(0 < seconds <= 7 * 86400, 'Private TEST preflight requirement failed')
engine = create_engine(url)
with engine.connect() as c:
    require(c.scalar(text('SELECT version_num FROM alembic_version')) == '20261011_0032', 'Private TEST preflight requirement failed')
    require(c.scalar(text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")) == 'r3-connected-pilot:synthetic-only:v1', 'Private TEST preflight requirement failed')
    role = c.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
    require(not any(role), 'Private TEST preflight requirement failed')
    require(not c.scalar(text("SELECT has_database_privilege(current_user,current_database(),'CREATE') OR has_database_privilege(current_user,current_database(),'TEMP')")), 'Private TEST preflight requirement failed')
    rows = c.execute(text('SELECT expires_at FROM core.pilot_deployments')).all()
    require(len(rows) == 1 and rows[0][0] == expiry, 'Pre-provisioned fixed expiry required; launcher never resets or extends it')
engine.dispose()
os.environ['OWNER_PREVIEW_FEATURE_SHA'] = sha
print(json.dumps({'mode': 'PRIVATE_DAILY_TEST', 'sha': sha, 'migration': '20261011_0032', 'expires_at': expiry.isoformat(), 'synthetic_only': True, 'preflight': 'PASS'}))
if not a.check:
    import uvicorn
    from greenhouse_owner_os.preview import build_preview
    app = build_preview(url, os.environ['PREVIEW_SECRET'], environment='test', public_origin=origin)
    server = uvicorn.Server(uvicorn.Config(app, host=bind, port=int(os.getenv('PREVIEW_PORT', '4173')), proxy_headers=False))
    timer = threading.Timer(seconds, lambda: setattr(server, 'should_exit', True))
    timer.daemon = True
    timer.start()
    try:
        server.run()
    finally:
        timer.cancel()
