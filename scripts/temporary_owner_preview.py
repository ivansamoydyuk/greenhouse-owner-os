"""One manually approved, synthetic owner session on an ephemeral hosted runner.

No legacy launcher is weakened. No credentials, response bodies or runtime logs
are published. The repository secret is NOT isolated from repository writers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'ivansamoydyuk/greenhouse-owner-os'
OWNER = 'ivansamoydyuk'
REF = 'refs/heads/01-architecture-database'
DURATION = 35 * 60
DATABASE = 'postgresql+psycopg://postgres@127.0.0.1:5432/greenhouse_owner_os_test'
# Official release digest and the official image digest verified on 2026-10-13.
POSTGRES_IMAGE = 'postgres:17.11@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3'
CLOUDFLARED_URL = 'https://github.com/cloudflare/cloudflared/releases/download/2026.10.0/cloudflared-linux-amd64'
CLOUDFLARED_SHA256 = 'd33ff2d14475178d2012c2c56beba87389ac5ded27649519f198a7d3134a99db'


def require(condition, message='Temporary TEST boundary rejected'):
    if not condition:
        raise RuntimeError(message)


def validate_context(env, head, dirty):
    require(env.get('GITHUB_REPOSITORY') == REPOSITORY)
    require(env.get('GITHUB_ACTOR') == OWNER and env.get('GITHUB_TRIGGERING_ACTOR') == OWNER)
    require(env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch')
    require(env.get('GITHUB_REF') == REF and env.get('GITHUB_RUN_ATTEMPT') == '1')
    require(env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_ENVIRONMENT') == 'github-hosted')
    sha = env.get('EXPECTED_SHA', '')
    require(bool(re.fullmatch('[0-9a-f]{40}', sha)))
    require(sha == env.get('GITHUB_SHA') == head and not dirty, 'Use the exact reviewed clean default-branch build')
    require(bool(re.fullmatch('[0-9]+', env.get('GITHUB_RUN_ID', ''))))
    require(env.get('APP_ENV') == 'test')
    return sha


def validate_password(password):
    # Unique synthetic-only secret must be supplied by the owner, never generated
    # for external access by this launcher or published as a workflow input.
    require(isinstance(password, str) and 20 <= len(password) <= 200, 'Owner TEST secret must contain 20–200 characters')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in password), 'Owner TEST secret contains unsupported characters')


def validate_origin(origin):
    require(bool(re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com', origin)))
    return origin


def runtime_path(name):
    root = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(root.is_dir())
    return root / name


def atomic_json(path, value):
    require(not path.is_symlink() and not path.exists(), 'Refuse existing readiness file')
    temporary = path.with_name(path.name + '.pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and cannot replace an existing symlink.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate readiness field')
            value[key] = item
        return value
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_size <= 1024)
        return json.load(handle, object_pairs_hook=unique)


def validate_metadata(value, sha, now=None):
    require(type(value) is dict and set(value) == {'url', 'username', 'sha', 'expires_at'}, 'Invalid readiness schema')
    require(all(type(v) is str for v in value.values()))
    validate_origin(value['url'])
    require(value['username'] == 'owner')
    require(bool(re.fullmatch('[0-9a-f]{40}', sha)) and value['sha'] == sha)
    from greenhouse_owner_os.daily_pilot import parse_expiry
    expires = parse_expiry(value['expires_at'])
    require(value['expires_at'] == expires.isoformat(), 'Noncanonical expiry')
    remaining = (expires - (now or datetime.now(timezone.utc))).total_seconds()
    require(0 < remaining <= DURATION, 'Readiness expired or extended')
    return expires


def process_identity(pid):
    require(type(pid) is int and pid > 1)
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return fields[19] if os.getpgid(pid) == pid else None
    except (FileNotFoundError, ProcessLookupError):
        return None


def owned_process():
    value = read_json(runtime_path('owner-preview-process.json'))
    require(type(value) is dict and set(value) == {'pid', 'started'})
    require(type(value['started']) is str and value['started'].isdigit())
    return value if process_identity(value['pid']) == value['started'] else None


def start():
    # The child alone retains the secret; no process state or readiness file does.
    validate_password(os.environ.get('TEST_PREVIEW_OWNER_PASSWORD'))
    require(not runtime_path('owner-preview-ready.json').exists())
    require(not runtime_path('owner-preview-process.json').exists())
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    os.environ.pop('TEST_PREVIEW_OWNER_PASSWORD', None)
    try:
        identity = process_identity(child.pid)
        require(identity is not None, 'TEST process failed to start')
        atomic_json(runtime_path('owner-preview-process.json'), dict(pid=child.pid, started=identity))
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            require(child.poll() is None, 'TEST startup failed')
            if runtime_path('owner-preview-ready.json').exists():
                check_ready()
                return
            time.sleep(1)
        raise RuntimeError('TEST readiness deadline reached')
    except BaseException:
        child.terminate()
        raise


def check_ready():
    metadata = read_json(runtime_path('owner-preview-ready.json'))
    expires = validate_metadata(metadata, os.environ['EXPECTED_SHA'])
    require(owned_process() is not None, 'TEST process is unavailable')
    require((expires - datetime.now(timezone.utc)).total_seconds() > 60, 'Insufficient live review time')
    return metadata


def hold():
    metadata = check_ready()
    expires = validate_metadata(metadata, os.environ['EXPECTED_SHA'])
    while datetime.now(timezone.utc) < expires:
        if owned_process() is None:
            # The child can finish normally between the clock and identity reads.
            require(datetime.now(timezone.utc) >= expires, 'TEST process ended early')
            return
        time.sleep(1)


def cleanup():
    path = runtime_path('owner-preview-process.json')
    if path.exists() or path.is_symlink():
        process = owned_process()
        if process:
            os.killpg(process['pid'], signal.SIGTERM)
            for _ in range(15):
                if process_identity(process['pid']) != process['started']:
                    break
                time.sleep(1)
            else:
                os.killpg(process['pid'], signal.SIGKILL)
    # Workflow's independent always-step also removes only the labelled database.


def provision_owner(app, password):
    """Only one owner, using the existing PilotAuth, without credential files."""
    from sqlalchemy import text
    validate_password(password)
    with app.state.pilot_auth.factory() as u:
        require(u.session.scalar(text('SELECT count(*) FROM core.pilot_participants')) == 0)
    app.state.pilot_auth.provision('owner', password)
    with app.state.pilot_auth.factory() as u:
        rows = u.session.execute(text('SELECT username, role, worker_id FROM core.pilot_participants')).all()
        require(rows == [('owner', 'owner', None)])


def verify_denial(client):
    for path in ('/preview/config', '/pilot/session', '/pilot/participants', '/pilot/draft-key', '/v1/owner/summary', '/r3/connected.js'):
        require(client.get(path).status_code == 401, 'Unauthenticated access was not denied')
    require(client.get('/preview/login?token=invalid').status_code == 404)
    require(client.get('/r3/connected.html').status_code == 303)
    require(client.post('/preview/login', headers={'Origin': str(client.base_url).rstrip('/'), 'Content-Type': 'application/json'}, json={}).status_code == 404)
    require(client.get('/pilot/').status_code == 200)


def quiet(command, **kwargs):
    return subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)


def main():
    sha = validate_context(os.environ,
        subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    require(re.fullmatch(r'postgres:17\.11@sha256:[a-f0-9]{64}', POSTGRES_IMAGE), 'PostgreSQL immutable image has not been verified')
    require(re.fullmatch(r'https://github.com/cloudflare/cloudflared/releases/download/[0-9.]+/cloudflared-linux-amd64', CLOUDFLARED_URL), 'Tunnel release has not been verified')
    require(re.fullmatch('[a-f0-9]{64}', CLOUDFLARED_SHA256), 'Tunnel checksum has not been verified')
    password = os.environ.pop('TEST_PREVIEW_OWNER_PASSWORD', '')
    validate_password(password)
    os.environ.update(APP_ENV='test', R3_CONNECTED_PILOT='1', R3_DAILY_PILOT='1',
        PREVIEW_BIND='127.0.0.1', PREVIEW_PORT='4173', OWNER_PREVIEW_FEATURE_SHA=sha,
        TEST_DATABASE_URL=DATABASE)
    os.environ.pop('PILOT_LOCAL_TEST', None)
    container = 'owner-temporary-' + os.environ['GITHUB_RUN_ID'] + '-1'
    owned = False
    tunnel = None
    server = None
    server_thread = None
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    # Runner teardown also destroys these process-local resources on hard cancel.
    with tempfile.TemporaryDirectory(prefix='owner-temporary-', dir=os.environ['RUNNER_TEMP']) as temp:
        try:
            import httpx
            binary = Path(temp) / 'cloudflared'
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                response = client.get(CLOUDFLARED_URL)
                response.raise_for_status()
                require(hashlib.sha256(response.content).hexdigest() == CLOUDFLARED_SHA256, 'Tunnel checksum mismatch')
                binary.write_bytes(response.content)
            binary.chmod(0o700)
            # Never replace or stop an existing container of the same name.
            require(subprocess.run(['docker', 'container', 'inspect', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0)
            quiet(['docker', 'run', '--detach', '--name', container, '--label', 'greenhouse.temporary-owner=' + os.environ['GITHUB_RUN_ID'],
                '--network', 'host', '-e', 'POSTGRES_USER=r3_pilot_bootstrap', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
                '-e', 'POSTGRES_DB=greenhouse_owner_os_test', POSTGRES_IMAGE, '-c', 'listen_addresses=127.0.0.1'])
            owned = True
            for _ in range(60):
                if subprocess.run(['docker', 'exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'r3_pilot_bootstrap', '-d', 'greenhouse_owner_os_test'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    break
                require(not stop.wait(1), 'Cancelled')
            else:
                raise RuntimeError('Owned TEST database did not become ready')
            env = dict(os.environ, DATABASE_URL='postgresql+psycopg://r3_pilot_bootstrap@127.0.0.1:5432/greenhouse_owner_os_test')
            env['R3_BOOTSTRAP_DATABASE_URL'] = env['DATABASE_URL']
            quiet(['alembic', 'upgrade', 'head'], cwd=ROOT, env=env)
            quiet([sys.executable, 'scripts/r3_connected_runtime.py'], cwd=ROOT, env=env)
            # The app is not listening while the noncredential tunnel URL is found.
            with (Path(temp) / 'tunnel.log').open('w+') as log:
                tunnel = subprocess.Popen([str(binary), 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', 'http://127.0.0.1:4173'], stdout=log, stderr=log)
                origin = None
                for _ in range(60):
                    require(tunnel.poll() is None, 'Tunnel failed to start')
                    log.flush()
                    log.seek(0)
                    found = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', log.read())
                    if found:
                        origin = validate_origin(found.group(0))
                        break
                    require(not stop.wait(1), 'Cancelled')
                require(origin, 'Tunnel URL was not established')
                expires = datetime.now(timezone.utc) + timedelta(seconds=DURATION)
                os.environ['PILOT_EXPIRES_AT'] = expires.isoformat()
                from greenhouse_owner_os.preview import build_preview
                app = build_preview(DATABASE, secrets.token_urlsafe(32), environment='test', public_origin=origin)
                provision_owner(app, password)
                del password
                from fastapi.testclient import TestClient
                with TestClient(app, base_url=origin, follow_redirects=False) as client:
                    verify_denial(client)
                import uvicorn
                server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=4173, proxy_headers=False, access_log=False, log_level='critical'))
                server_thread = threading.Thread(target=server.run, daemon=True)
                server_thread.start()
                smoke_deadline = time.monotonic() + 120
                while time.monotonic() < smoke_deadline:
                    require(server_thread.is_alive() and tunnel.poll() is None, 'TEST runtime stopped')
                    try:
                        with httpx.Client(base_url=origin, timeout=5, follow_redirects=False) as client:
                            verify_denial(client)
                        break
                    except (httpx.HTTPError, RuntimeError):
                        require(not stop.wait(1), 'Cancelled')
                else:
                    raise RuntimeError('HTTPS authentication-denial smoke did not pass')
                require(datetime.now(timezone.utc) + timedelta(minutes=1) < expires, 'Preview expired before publication')
                summary = f'Temporary synthetic TEST\n\nURL: {origin}/pilot/\nUsername: owner\nBuild: {sha}\nExpires: {expires.isoformat()}\n'
                with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as handle:
                    handle.write(summary)
                metadata = dict(url=origin, username='owner', sha=sha, expires_at=expires.isoformat())
                validate_metadata(metadata, sha)
                atomic_json(runtime_path('owner-preview-ready.json'), metadata)
                while datetime.now(timezone.utc) < expires and not stop.wait(1):
                    require(server_thread.is_alive() and tunnel.poll() is None, 'TEST runtime stopped')
        finally:
            if server is not None:
                server.should_exit = True
            if tunnel is not None:
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
                    tunnel.wait(timeout=10)
            if server_thread is not None:
                server_thread.join(timeout=10)
            if owned:
                quiet(['docker', 'rm', '--force', '--volumes', container])


if __name__ == '__main__':
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in ('--serve', '--start', '--check-ready', '--hold', '--cleanup'))
        {'--serve': main, '--start': start, '--check-ready': check_ready, '--hold': hold, '--cleanup': cleanup}[sys.argv[1]]()
    except Exception:
        # Exception strings can carry SQL parameters, HTTP headers or credentials.
        # Fail closed with a fixed diagnostic instead of exposing runtime internals.
        print('Temporary TEST failed closed; no credentials or runtime logs are published.', file=sys.stderr)
        raise SystemExit(1)
