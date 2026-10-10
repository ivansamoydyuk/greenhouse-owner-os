"""Owned TEST process probes for the unchanged preparation handler and CLI.

This is not a deployment worker. Trace checkpoints only stop this process; they
do not replace recognition, preparation, transactions or SQL. The parent test
owns all signals. Timing acceleration, where needed, is performed and disclosed
by the parent against its disposable PostgreSQL rows.
"""
import argparse
import ast
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import UUID

from sqlalchemy import text

from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import ENGINE, OwnerCaptureService


ROOT = Path(__file__).resolve().parents[2]


def factory():
    url = os.environ['TEST_DATABASE_URL']
    require_disposable_database(url, os.environ.get('APP_ENV'))
    assert url == os.environ['DATABASE_URL']
    return make_session_factory(url)


def status(sf):
    """Read-only observed queue status; no repair or status mutation."""
    with sf() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        return dict(db.execute(text("""
            SELECT count(*) total,
              count(*) FILTER (WHERE state='QUEUED') queued,
              count(*) FILTER (WHERE state='RUNNING') running,
              count(*) FILTER (WHERE state='RUNNING' AND lease_until<clock_timestamp()) expired_running,
              count(*) FILTER (WHERE state='FAILED') failed,
              count(*) FILTER (WHERE state='SUCCEEDED') succeeded,
              count(*) FILTER (WHERE attempts<3 AND
                (state='QUEUED' OR state='FAILED' AND retry_at<=clock_timestamp())
                OR state='RUNNING' AND lease_until<clock_timestamp()) eligible,
              min(created_at) FILTER (WHERE state IN ('QUEUED','RUNNING')) oldest_pending_created_at,
              max(clock_timestamp()-created_at) FILTER (WHERE state IN ('QUEUED','RUNNING')) oldest_pending_age
            FROM core.processing_jobs WHERE engine_version=:v
        """), dict(v=ENGINE)).mappings().one())


def checkpoint_line(checkpoint):
    """Resolve semantic locations from current source, without hard-coded lines."""
    source_file = Path(inspect.getsourcefile(OwnerCaptureService.process))
    module = ast.parse(source_file.read_text())
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'OwnerCaptureService')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'process')
    work = next(n for n in method.body if isinstance(n, ast.Try))
    transaction = next(n for n in work.body if isinstance(n, ast.With))
    if checkpoint == 'after_lease':
        node = work.body[0]
        assert isinstance(node, ast.If)
    elif checkpoint == 'after_extraction':
        commit_index = next(i for i, n in enumerate(transaction.body)
                            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                            and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'commit')
        node = transaction.body[commit_index + 1]
    elif checkpoint == 'before_preparation_commit':
        node = next(n for n in transaction.body if isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Call) and n.value.args
                    and any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                            and "SET state='SUCCEEDED'" in a.value for a in n.value.args))
    else:
        raise ValueError(checkpoint)
    return str(source_file), node.lineno


def handler(source_id, checkpoint, marker):
    sf = factory()
    code_file, line = checkpoint_line(checkpoint)
    hit = False

    def trace(frame, event, arg):
        nonlocal hit
        if (not hit and event == 'line' and frame.f_code.co_filename == code_file
                and frame.f_code.co_name == 'process' and frame.f_lineno == line):
            hit = True
            payload = dict(pid=os.getpid(), source_id=source_id, checkpoint=checkpoint,
                           file=str(Path(code_file).relative_to(ROOT)), line=line,
                           lease_token=str(frame.f_locals['token']),
                           job_id=str(frame.f_locals['j']['id']))
            Path(marker).write_text(json.dumps(payload) + '\n')
            print(json.dumps(dict(event='handler_checkpoint', **payload)), flush=True)
            os.kill(os.getpid(), signal.SIGSTOP)
        return trace

    sys.settrace(trace)
    try:
        OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf)).process(UUID(source_id))
    finally:
        sys.settrace(None)
    assert hit, 'Requested actual-handler checkpoint was not reached'
    print(json.dumps(dict(event='handler_returned', pid=os.getpid(), source_id=source_id)), flush=True)


def supervise(rounds, interval, *, command=None, child_timeout=60):
    """Finite TEST lifecycle: unchanged CLI invocation, observation, clean exit."""
    if not 1 <= rounds <= 10 or not 0 <= interval <= 5 or not .1 <= child_timeout <= 60:
        raise ValueError('TEST supervisor is bounded to ten rounds and five-second intervals')
    # The default always runs the existing CLI. Cleanup-only tests may pass an
    # owned sleeping process to deterministically exercise timeout/termination.
    command = command or [sys.executable, str(ROOT / 'scripts/process_owner_sources.py')]
    sf = factory()
    child = None

    def reap_child():
        if child is not None and child.poll() is None:
            child.terminate()
            child.send_signal(signal.SIGCONT)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            print(json.dumps(dict(event='owned_cli_cleanup', cli_pid=child.pid,
                                  returncode=child.returncode, child_reaped=True)), flush=True)

    def stop(signum, _frame):
        reap_child()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for attempt in range(rounds):
            print(json.dumps(dict(event='before_cli', round=attempt + 1, status=status(sf)), default=str), flush=True)
            child = subprocess.Popen(command, cwd=ROOT)
            print(json.dumps(dict(event='cli_started', round=attempt + 1, cli_pid=child.pid)), flush=True)
            result = child.wait(timeout=child_timeout)
            assert result == 0, f'Existing CLI returned {result}'
            print(json.dumps(dict(event='after_cli', round=attempt + 1, cli_pid=child.pid,
                                  returncode=result, status=status(sf)), default=str), flush=True)
            if attempt + 1 < rounds:
                time.sleep(interval)
    finally:
        reap_child()
    print(json.dumps(dict(event='test_lifecycle_complete', rounds=rounds, child_reaped=True)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    one = sub.add_parser('handler')
    one.add_argument('source_id')
    one.add_argument('checkpoint', choices=['after_lease', 'after_extraction', 'before_preparation_commit'])
    one.add_argument('marker')
    loop = sub.add_parser('supervise')
    loop.add_argument('--rounds', type=int, default=3)
    loop.add_argument('--interval', type=float, default=.1)
    args = parser.parse_args()
    if args.command == 'handler':
        handler(args.source_id, args.checkpoint, args.marker)
    else:
        supervise(args.rounds, args.interval)
