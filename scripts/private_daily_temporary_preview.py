"""Launch-only wrapper; immutable app, fresh synthetic DB, fixed 35-minute life.

No full test gate, real records, new provider/account, production configuration,
external dispatch, or secret-bearing logs/evidence. Access credentials are saved
only to a dedicated private artifact with the Actions minimum one-day retention.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from zoneinfo import ZoneInfo

APP_SHA = '9a981766f217fe33364f32b601bfc2a4468b4f59'
APP_TREE = 'a1d3f68efb3995154868b7d9ec045d50acb974de'
BRANCH = 'refs/heads/13-private-daily-temporary-preview'
DATABASE = 'postgresql+psycopg://postgres@127.0.0.1:5432/greenhouse_owner_os_test'
TABLES = ('finance.obligations', 'finance.payments', 'finance.direct_expenses',
          'finance.postings', 'finance.posting_entries', 'inventory.events',
          'inventory.movements', 'harvest.worker_reports', 'harvest.depot_settlements')


class CheckFailure(RuntimeError):
    """Only these static allowlisted labels may appear in workflow diagnostics."""


def require(condition, code):
    if not condition:
        raise CheckFailure(code)


def write_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    path.chmod(0o600)


def utcnow():
    return datetime.now(timezone.utc)


def origin_from_log(value):
    urls = re.findall(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?=[\s|/]|$)', value)
    require(bool(urls), 'tunnel_origin_missing')
    return urls[0]


def proc_identity(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if stat[0] == 'Z':
            return None
        return stat[19]  # Linux process start time prevents PID-reuse kills.
    except (FileNotFoundError, ProcessLookupError):
        return None


def stop_process(record):
    pid, start = record['pid'], record['start']
    if not start or proc_identity(pid) != start:
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if proc_identity(pid) != start:
            return
        time.sleep(0.1)
    if proc_identity(pid) == start:
        os.kill(pid, signal.SIGKILL)


def command(args, *, cwd, env=None, timeout=240):
    # Existing bootstrap prints a mask directive with a secret. Discard all
    # child output rather than retaining it in a local or uploaded log.
    result = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=timeout)
    require(result.returncode == 0, 'subprocess_failed')


class Runtime:
    def __init__(self):
        self.runner = Path(os.environ['RUNNER_TEMP']).resolve()
        run, attempt = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']
        require(run.isdigit() and attempt.isdigit(), 'invalid_run_identity')
        self.root = self.runner / f'private-daily-temporary-{run}-{attempt}'
        self.container = f'r3-connected-{run}-{attempt}'
        self.state_path = self.root / 'state.json'
        self.stage = 'preflight'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def save(self):
        tmp = self.root / 'state.next'
        tmp.write_text(json.dumps(self.state))
        tmp.chmod(0o600)
        tmp.replace(self.state_path)

    def register(self, name, pid):
        record = {'pid': pid, 'start': proc_identity(pid)}
        require(bool(record['start']), 'process_not_live')
        self.state.setdefault('processes', {})[name] = record
        self.save()
        return record

    def spawn(self, name, args, env, app):
        # No raw application response, cookie, body, or exception is logged.
        # Tunnel output is needed briefly for its generated hostname only.
        output = (self.root / 'tunnel.log').open('wb') if name == 'tunnel' else subprocess.DEVNULL
        try:
            process = subprocess.Popen(args, cwd=app, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            if name == 'tunnel':
                output.close()
        return self.register(name, process.pid)

    def cleanup(self):
        if not self.root.exists():
            return
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        # Bootstrap's reviewed launcher writes its PID before its readiness wait.
        # Recover it if preparation failed before register() could run.
        bootstrap = self.root / 'r3-connected-server.pid'
        if bootstrap.exists() and 'bootstrap' not in self.state.get('processes', {}):
            try:
                pid = int(bootstrap.read_text())
                cmd = Path(f'/proc/{pid}/cmdline').read_bytes()
                cwd = Path(f'/proc/{pid}/cwd').resolve()
                if b'greenhouse_owner_os.preview' in cmd and str(cwd) == self.state.get('app'):
                    self.register('bootstrap', pid)
            except (ValueError, FileNotFoundError, ProcessLookupError):
                pass
        for record in self.state.get('processes', {}).values():
            stop_process(record)
        cid_path = self.root / 'r3-container.id'
        cid = cid_path.read_text().strip() if cid_path.exists() else ''
        if re.fullmatch('[0-9a-f]{64}', cid):
            inspected = subprocess.run(['docker', 'inspect', '--format', '{{.Name}}', cid],
                                       capture_output=True, text=True)
            if inspected.returncode == 0:
                require(inspected.stdout.strip() == '/' + self.container, 'container_ownership_mismatch')
                result = subprocess.run(['docker', 'rm', '--force', '--volumes', cid],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                require(result.returncode == 0, 'container_cleanup_failed')
        # Remove all local credentials after upload/window; remote access artifact
        # remains private for one day, with already-expired TEST credentials.
        for name in ('pilot-private-credentials.json', 'bootstrap.env'):
            (self.root / name).unlink(missing_ok=True)
        for p in (self.root / 'access').glob('*'):
            p.unlink()
        print('OWNED_TEMPORARY_TEST_RUNTIME_STOPPED')

    def app_identity(self, app):
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=app, text=True).strip()
        require(git('rev-parse', 'HEAD') == APP_SHA, 'app_sha_mismatch')
        require(git('rev-parse', 'HEAD^{tree}') == APP_TREE, 'app_tree_mismatch')
        require(not git('status', '--porcelain', '--untracked-files=no'), 'tracked_app_modified')

    def environment(self, app, expiry, secret):
        env = os.environ.copy()
        for key in ('PILOT_LOCAL_TEST', 'PREVIEW_PUBLIC_ORIGIN', 'R2_REVIEW_FIXTURES', 'PYTHONOPTIMIZE'):
            env.pop(key, None)
        env.update(APP_ENV='test', R3_CONNECTED_PILOT='1', R3_DAILY_PILOT='1',
                   OWNER_PREVIEW_FEATURE_SHA=APP_SHA, PILOT_EXPECTED_SHA=APP_SHA,
                   PILOT_EXPIRES_AT=expiry, PREVIEW_SECRET=secret, PREVIEW_BIND='127.0.0.1',
                   PREVIEW_PORT='4173', DATABASE_URL=DATABASE, TEST_DATABASE_URL=DATABASE,
                   PYTHONPATH=str(app / 'src') + ':' + str(app), RUNNER_TEMP=str(self.root),
                   GITHUB_ENV=str(self.root / 'bootstrap.env'))
        return env

    def launch(self):
        require(os.environ.get('GITHUB_EVENT_NAME') == 'push' and os.environ.get('GITHUB_REF') == BRANCH,
                'explicit_preview_branch_required')
        require(os.environ.get('GITHUB_REPOSITORY') == 'ivansamoydyuk/greenhouse-owner-os', 'wrong_repository')
        require(os.environ['GITHUB_RUN_ATTEMPT'] == '1', 'fresh_owner_launch_required')
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        require(event['repository']['private'] is True, 'private_repository_required')
        app = Path(os.environ['APP_ROOT']).resolve()
        self.app_identity(app)
        require(not self.root.exists(), 'refuse_restart_or_expiry_extension')
        # No launch until localhost application and database ports are free.
        import socket
        for port in (4173, 5432):
            with socket.socket() as probe:
                require(probe.connect_ex(('127.0.0.1', port)) != 0, 'runner_port_already_owned')
        self.root.mkdir(mode=0o700)
        expiry = (utcnow() + timedelta(minutes=35)).isoformat()
        self.state = {'app': str(app), 'expires_at': expiry, 'processes': {}}
        self.save()
        import secrets
        secret = secrets.token_urlsafe(32)
        print('::add-mask::' + secret, flush=True)
        env = self.environment(app, expiry, secret)
        offline = env | {'PILOT_LOCAL_TEST': '1'}
        (app / 'artifacts' / 'r3-connected').mkdir(parents=True, exist_ok=True)
        self.stage = 'fresh_loopback_bootstrap'
        command(['bash', 'scripts/start_r3_connected_ci.sh'], cwd=app, env=offline)
        boot = self.register('bootstrap', int((self.root / 'r3-connected-server.pid').read_text()))
        self.stage = 'offline_synthetic_participants'
        credential_path = self.root / 'pilot-private-credentials.json'
        command([sys.executable, 'scripts/private_daily_pilot_sql_evidence.py', '--prepare',
                 str(self.root / 'fixtures'), '--credentials', str(credential_path)], cwd=app, env=offline)
        credentials = json.loads(credential_path.read_text())
        for value in (credentials['owner'], *(v for engine in ('chromium', 'webkit') for v in credentials[engine].values())):
            print('::add-mask::' + value['password'], flush=True)
        stop_process(boot)
        require(proc_identity(boot['pid']) != boot['start'], 'bootstrap_not_stopped')
        # Offline provisioning has finished before any tunnel exists. The public
        # app is constructed separately, with local mode removed from its env.
        sys.path[:0] = [str(app / 'src'), str(app)]
        from sqlalchemy import create_engine, text
        engine = create_engine(DATABASE)
        selected = {'owner': credentials['owner'], 'worker': credentials['chromium']['worker']}
        with engine.begin() as db:
            db.execute(text('UPDATE core.pilot_participants SET enabled=false, version=version+1 '
                            'WHERE id NOT IN (:owner,:worker)'),
                       {'owner': selected['owner']['participant_id'], 'worker': selected['worker']['participant_id']})
            db.execute(text('UPDATE core.pilot_sessions SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL'))
        self.stage = 'https_gateway'
        tunnel_env = os.environ.copy()
        for key in ('PILOT_LOCAL_TEST', 'PREVIEW_SECRET', 'TEST_DATABASE_URL', 'DATABASE_URL'):
            tunnel_env.pop(key, None)
        self.spawn('tunnel', [str(self.runner / 'cloudflared'), 'tunnel', '--no-autoupdate',
                   '--protocol', 'http2', '--metrics', '127.0.0.1:49312', '--url', 'http://127.0.0.1:4173'], tunnel_env, app)
        origin = None
        for _ in range(60):
            try:
                origin = origin_from_log((self.root / 'tunnel.log').read_text())
                break
            except RuntimeError:
                time.sleep(1)
        require(origin is not None, 'tunnel_origin_timeout')
        env['PREVIEW_PUBLIC_ORIGIN'] = origin
        require('PILOT_LOCAL_TEST' not in env, 'local_mode_cannot_reach_network')
        self.stage = 'strict_https_runtime'
        command([sys.executable, 'scripts/run_private_daily_test.py', '--check'], cwd=app, env=env, timeout=30)
        self.spawn('app', [sys.executable, 'scripts/run_private_daily_test.py'], env, app)
        self.stage = 'narrow_https_smoke'
        checks = smoke(origin, selected, expiry, engine)
        self.app_identity(app)
        engine.dispose()
        self.stage = 'secret_free_metadata'
        local_expiry = datetime.fromisoformat(expiry).astimezone(ZoneInfo('Europe/Istanbul')).isoformat()
        metadata = {'status': 'PASS', 'mode': 'PRIVATE_DAILY_TEST', 'url': origin + '/pilot/',
                    'app_sha': APP_SHA, 'app_tree': APP_TREE, 'ops_sha': os.environ['GITHUB_SHA'],
                    'expires_at': expiry, 'expires_at_istanbul': local_expiry, 'timezone': 'Europe/Istanbul',
                    'synthetic_only': True, 'full_gate_rerun': False,
                    'checks': checks, 'persistent_host': False, 'storage': 'owned ephemeral runner'}
        write_json(self.root / 'metadata' / 'access-status.json', metadata)
        access = self.root / 'access'
        access.mkdir(mode=0o700)
        guide = '\n'.join([
            'PRIVATE DAILY TEST — временный доступ', '', origin + '/pilot/',
            'Автоматическое окончание доступа: ' + local_expiry + ' (Стамбул, UTC+3).',
            'То же время в UTC: ' + expiry + '.',
            'Данные только синтетические. Не вводите реальные или конфиденциальные сведения.', '',
            'ВЛАДЕЛЕЦ', 'Логин: ' + selected['owner']['username'], 'Пароль: ' + selected['owner']['password'], '',
            'ТЕСТОВЫЙ РАБОТНИК', 'Логин: ' + selected['worker']['username'], 'Пароль: ' + selected['worker']['password'], '',
            'На iPhone откройте ссылку в Safari и войдите как владелец.',
            'Для проверки работника сначала нажмите «Выйти», затем войдите с его логином.',
            'Не используйте две учётные записи в обычных вкладках одновременно: у них общие cookies.',
            'Здесь новая отдельная TEST-база, предыдущие черновики и данные не перенесены.',
            'В пределах этого окна данные сохраняются при перезапуске приложения без сброса базы.',
            'При остановке runner доступ прекращается и TEST-вводы теряются; постоянное хранение не обещано.',
            'Не отправляйте этот файл или пароли третьим лицам. Файл доступен только через приватный репозиторий.',
            'Минимальный срок хранения файла в Actions — 1 день; пароли истекают вместе с этим окном.',
            'Сборка: ' + APP_SHA, ''])
        (access / 'START-HERE.txt').write_text(guide, encoding='utf-8')
        (access / 'START-HERE.txt').chmod(0o600)
        # Exact secret scan of the public metadata. No raw server/tunnel/bootstrap
        # logs, fixture files, DB exports, browser state, cookies or keys upload.
        public = (self.root / 'metadata' / 'access-status.json').read_text()
        all_secrets = [secret, credentials['owner']['password']]
        all_secrets += [v['password'] for e in ('chromium', 'webkit') for v in credentials[e].values()]
        require(not any(value in public for value in all_secrets), 'secret_in_metadata')
        self.state['url'] = origin
        self.state['ready'] = True
        self.save()
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
            output.write(f'metadata_dir={self.root / "metadata"}\naccess_dir={access}\n')
        print('PRIVATE_DAILY_TEMPORARY_HTTPS_SMOKE_PASS')

    def hold(self):
        require(self.state.get('ready') is True, 'access_not_verified')
        end = datetime.fromisoformat(self.state['expires_at'])
        while utcnow() < end:
            for name in ('app', 'tunnel'):
                record = self.state['processes'][name]
                # Allow the app's exact-expiry timer to close at the last second.
                if (end - utcnow()).total_seconds() > 2:
                    require(proc_identity(record['pid']) == record['start'], name + '_stopped_early')
            time.sleep(min(5, max(0.01, (end - utcnow()).total_seconds())))
        print('FIXED_35_MINUTE_TEST_WINDOW_EXPIRED')


def smoke(origin, credentials, expiry, engine):
    import httpx
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from greenhouse_owner_os.services.reconciliation import reconcile
    checks = []
    def expect(condition, label):
        require(condition, label)
        checks.append(label)
    def get(client, path, status=200):
        response = client.get(path)
        require(response.status_code == status, 'https_get_status')
        return response
    def digest(db):
        data = {table: [dict(row) for row in db.execute(text('SELECT * FROM ' + table + ' r ORDER BY to_jsonb(r)::text')).mappings()] for table in TABLES}
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    client_args = dict(base_url=origin, timeout=15, follow_redirects=False, trust_env=False)
    with httpx.Client(**client_args) as anonymous, httpx.Client(**client_args) as owner, httpx.Client(**client_args) as worker:
        for _ in range(60):
            try:
                if anonymous.get('/health').status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(1)
        expect(get(anonymous, '/health').json() == {'ok': True, 'environment': 'TEST_PREVIEW'}, 'https_health')
        public = get(anonymous, '/pilot/public-config').json()
        expect(public['mode'] == 'PRIVATE_DAILY_TEST' and public['build_sha'] == APP_SHA and public['expires_at'] == expiry, 'exact_build_and_fixed_expiry')
        get(anonymous, '/pilot/'); get(anonymous, '/assets/pilot-login.js')
        for path in ('/preview/config', '/pilot/session', '/pilot/draft-key', '/v1/worker/tasks'):
            get(anonymous, path, 401)
        expect(get(anonymous, '/r3/connected.html', 303).headers.get('location') == '/pilot/', 'anonymous_has_no_business_access')
        expect(anonymous.post('/preview/login', json={'secret': 'disabled'}, headers={'Origin': origin}).status_code == 404, 'legacy_login_disabled')
        expect(anonymous.post('/pilot/login', json={}, headers={'Origin': 'https://invalid.example'}).status_code == 403, 'cross_origin_login_rejected')
        for role, client in (('owner', owner), ('worker', worker)):
            credential = credentials[role]
            result = client.post('/pilot/login', json={key: credential[key] for key in ('username', 'password')}, headers={'Origin': origin})
            require(result.status_code == 200, 'login_failed')
            value = result.json()
            cookie = SimpleCookie(); cookie.load(result.headers.get('set-cookie', ''))
            session = cookie.get('pilot_session')
            expect(bool(session) and bool(session['secure']) and bool(session['httponly']) and session['samesite'].lower() == 'strict' and not session['domain'] and 0 < int(session['max-age']) <= 2100, role + '_secure_cookie')
            expect(value['role'] == role and value['participant_id'] == credential['participant_id'] and value['build_sha'] == APP_SHA and value['pilot_expires_at'] == expiry, role + '_bound_identity')
            client.headers.update({'Origin': origin, 'X-Pilot-Participant': credential['participant_id']})
            get(client, '/pilot/session')
        config = get(owner, '/preview/config').json()
        expect(config['feature_sha'] == APP_SHA and config['daily_pilot'] and config['synthetic_only'] and not config['actual_operations'] and not config['capabilities']['external_integrations'] and not config['capabilities']['real_dispatch'], 'owner_synthetic_configuration')
        for path in ('/r3/connected.html', '/r3/connected.js', '/assets/pilot-storage.js'):
            expect(len(get(owner, path).content) > 100, 'authenticated_asset_' + path.rsplit('/', 1)[-1])
        get(worker, '/worker'); get(worker, '/assets/worker.js')
        root = '/v1/entities/' + config['entity_id']
        get(owner, root + '/owner/home')
        get(worker, '/preview/config', 403); get(worker, root + '/finance-overview', 403)
        get(owner, '/v1/entities/00000000-0000-0000-0000-000000000001/owner/home', 403)
        with engine.connect() as db:
            before = digest(db)
        # Two non-economic synthetic example tasks prove positive and negative
        # assignment scoping without changing business/accounting decisions.
        tasks = []
        for label in ('assigned', 'unassigned'):
            body = {'title': 'TEST HTTPS ' + label, 'original_instruction': 'TEST: реальную работу выполнять не нужно.',
                    'description': 'Synthetic access check only', 'source': 'test:temporary-https', 'locator': label}
            response = owner.post(root + '/tasks', json=body, headers={'Idempotency-Key': 'temporary-https-' + label})
            require(response.status_code == 201, 'synthetic_task_failed')
            tasks.append(response.json()['id'])
        assignment = {'worker_id': credentials['worker']['worker_id'], 'rendered_text': 'TEST: реальную работу выполнять не нужно.',
                      'language': 'ru', 'translation_source': 'original', 'rendering_reviewed': True}
        response = owner.post(root + '/tasks/' + tasks[0] + '/assignments', json=assignment, headers={'Idempotency-Key': 'temporary-https-assignment'})
        require(response.status_code == 201, 'synthetic_assignment_failed')
        expect(get(worker, '/v1/worker/tasks/' + tasks[0]).json()['id'] == tasks[0], 'worker_assigned_task_visible')
        expect(worker.get('/v1/worker/tasks/' + tasks[1]).status_code in (403, 404), 'worker_unassigned_task_denied')
        overview = get(worker, '/v1/worker/tasks').json()
        expect(overview['worker']['id'] == credentials['worker']['worker_id'], 'worker_list_identity')
        for client in (owner, worker):
            expect(client.post('/pilot/logout', json={}).status_code == 200, 'smoke_session_logged_out')
            get(client, '/pilot/session', 401)
        with Session(engine) as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            expect(db.scalar(text('SHOW listen_addresses')) == '127.0.0.1', 'database_loopback_only')
            role = db.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
            expect(not any(role) and not db.scalar(text("SELECT has_database_privilege(current_user,current_database(),'CREATE') OR has_database_privilege(current_user,current_database(),'TEMP')")), 'restricted_runtime_role')
            expect(db.scalar(text("SELECT rolcanlogin FROM pg_roles WHERE rolname='r3_pilot_bootstrap'")) is False, 'bootstrap_login_disabled')
            expect(db.scalar(text('SELECT version_num FROM alembic_version')) == '20261011_0032', 'schema_0032')
            expect(db.scalar(text('SELECT count(*) FROM core.pilot_participants WHERE enabled')) == 2, 'only_owner_and_worker_enabled')
            expect(digest(db) == before, 'economic_effects_unchanged')
            proof = reconcile(db, include_classification=True, snapshot_established=True)
            expect(proof['ok'] and not proof['findings'], 'global_reconciliation')
    return checks


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('launch', 'hold', 'cleanup'))
    action = parser.parse_args().action
    runtime = Runtime()
    try:
        getattr(runtime, action)()
    except BaseException as error:
        # Deliberately avoid traceback or raw response body/cookies/credentials.
        print('TEMPORARY_TEST_FAILED_STAGE_' + runtime.stage, file=sys.stderr)
        if isinstance(error, CheckFailure) and re.fullmatch(r'[a-z0-9_.-]{1,100}', str(error)):
            print('CHECK_' + str(error), file=sys.stderr)
        if action != 'cleanup':
            try:
                runtime.cleanup()
            except BaseException:
                print('OWNED_CLEANUP_REQUIRES_ATTENTION', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
