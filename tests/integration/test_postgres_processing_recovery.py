"""AUD-012 actual owned-process crashes and unchanged CLI on real PostgreSQL.

No replacement database, mocked handler, OCR result or accounting service.
SIGSTOP checkpoints expose actual committed/uncommitted transaction boundaries.
Lease/retry timestamps are accelerated only in this exact disposable TEST DB.
"""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from greenhouse_owner_os.domain.owner_capture import CaptureSource
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_owner_capture import queue, revise, confirm
from tests.integration.test_postgres_readiness import preview, send


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'tests/helpers/processing_recovery_process.py'
CLI = ROOT / 'scripts/process_owner_sources.py'
EFFECT_TABLES = ('core.confirmation_effects', 'finance.postings', 'finance.obligations',
                 'finance.payments', 'finance.direct_expenses', 'inventory.events',
                 'inventory.movements', 'harvest.depot_settlements', 'workers.tasks',
                 'workers.assignments', 'core.owner_tasks', 'core.invoice_receipt_links')


def service(i):
    sf, actor, *_ = i['preview']
    return OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf)), UUID(i['e']), actor


def capture(i, phrase='Сегодня полить İmamlı.', binary=None):
    svc, entity, actor = service(i)
    fields = dict(client_capture_id=uuid4(), captured_at=datetime(2026, 10, 8, 9, tzinfo=timezone.utc))
    if binary is None:
        fields.update(text=phrase)
    else:
        fields.update(mode='photo', filename='TEST-AUD012.png', mime_type='image/png',
                      file_base64=base64.b64encode(binary).decode())
    saved = svc.capture(entity, actor, str(uuid4()), CaptureSource(**fields))
    assert saved['state'] == 'SERVER_SAVED' and saved['proposals'] == []
    return saved


def original(i, saved):
    with i['preview'][0]() as db:
        result = dict(db.execute(text('SELECT * FROM core.inbox_items WHERE id=:s'),
                                 dict(s=saved['id'])).mappings().one())
    blob = result.pop('file_bytes')
    result['file_bytes_sha256'] = hashlib.sha256(blob).hexdigest() if blob is not None else None
    result['file_bytes_length'] = len(blob) if blob is not None else None
    return result


def snapshot(i, saved):
    with i['preview'][0]() as db:
        params = dict(s=saved['id'], e=i['e'])
        queries = {
            'jobs': 'SELECT * FROM core.processing_jobs WHERE inbox_id=:s ORDER BY id',
            'runs': 'SELECT * FROM core.extraction_runs WHERE inbox_id=:s ORDER BY attempt',
            'proposals': 'SELECT * FROM core.proposed_actions WHERE inbox_id=:s ORDER BY slot',
            'revisions': 'SELECT r.* FROM core.proposal_revisions r JOIN core.proposed_actions p ON p.id=r.proposal_id WHERE p.inbox_id=:s ORDER BY r.proposal_id,r.version',
            'actions': 'SELECT a.* FROM core.proposal_actions a JOIN core.proposed_actions p ON p.id=a.proposal_id WHERE p.inbox_id=:s ORDER BY a.created_at,a.id',
            'effects': 'SELECT e.* FROM core.confirmation_effects e JOIN core.proposed_actions p ON p.id=e.proposal_id WHERE p.inbox_id=:s ORDER BY e.proposal_id',
        }
        result = {name: [dict(row) for row in db.execute(text(sql), params).mappings()]
                  for name, sql in queries.items()}
        result['effect_counts'] = {table: db.scalar(text('SELECT count(*) FROM ' + table + ' WHERE entity_id=:e'), params)
                                   for table in EFFECT_TABLES}
    result['original'] = original(i, saved)
    return result


def accelerate(i, saved, column='lease_until'):
    assert column in ('lease_until', 'retry_at')
    with i['preview'][0].begin() as db:
        before = db.execute(text('SELECT ' + column + ' FROM core.processing_jobs WHERE inbox_id=:s'),
                            dict(s=saved['id'])).scalar_one()
        after = db.execute(text('UPDATE core.processing_jobs SET ' + column + "=clock_timestamp()-interval '1 second' WHERE inbox_id=:s RETURNING " + column),
                           dict(s=saved['id'])).scalar_one()
    return dict(test_only_timestamp_acceleration=column, before=before, after=after,
                simulated_delay='15 minutes' if column == 'lease_until' else '1 minute')


class Processes:
    def __init__(self, output):
        self.output = output
        self.children = []
        self.records = []

    def record(self, phase, **data):
        self.records.append(dict(phase=phase, **data))
        print('AUD012_EVIDENCE ' + json.dumps(self.records[-1], default=str), flush=True)

    def start(self, arguments):
        ordinal = len(self.children) + 1
        log = self.output / f'process-{ordinal}.log'
        stream = log.open('w')
        child = subprocess.Popen([sys.executable, *map(str, arguments)], cwd=ROOT,
                                 stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        self.children.append((child, stream, log))
        self.record('process_started', pid=child.pid, arguments=list(map(str, arguments)), log=log.name)
        return child

    def wait(self, child, expected=0, timeout=90):
        result = child.wait(timeout=timeout)
        self.record('process_reaped', pid=child.pid, returncode=result)
        assert result == expected

    def stop_handler(self, saved, checkpoint='after_lease'):
        marker = self.output / f'checkpoint-{len(self.children) + 1}.json'
        child = self.start([HELPER, 'handler', saved['id'], checkpoint, marker])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert child.poll() is None, 'Handler exited before checkpoint; inspect retained child log'
            if marker.exists():
                # Inspect only the exact child we created. Wait for the actual
                # stopped state so SIGCONT cannot race the child's SIGSTOP.
                state = next(line for line in Path(f'/proc/{child.pid}/status').read_text().splitlines()
                             if line.startswith('State:'))
                if state.split()[1] in ('T', 't'):
                    proof = json.loads(marker.read_text())
                    assert proof['pid'] == child.pid and proof['source_id'] == saved['id']
                    self.record('handler_stopped', process_state=state, **proof)
                    return child, proof
            time.sleep(.02)
        raise AssertionError('Actual handler did not reach stopped checkpoint')

    def kill(self, child):
        assert child.poll() is None
        child.kill()  # Only a Popen child created by this fixture.
        self.wait(child, -signal.SIGKILL)

    def cli(self):
        child = self.start([CLI])
        self.wait(child)

    def lifecycle(self):
        child = self.start([HELPER, 'supervise', '--rounds', '3', '--interval', '.1'])
        self.wait(child, timeout=200)  # Three 60-second rounds plus cleanup margin.
        log = next(path for proc, _, path in self.children if proc is child)
        events = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
        assert [e['round'] for e in events if e['event'] == 'after_cli'] == [1, 2, 3]
        assert events[-1] == dict(event='test_lifecycle_complete', rounds=3, child_reaped=True)
        self.record('bounded_test_lifecycle', events=events)

    def close(self):
        for child, stream, _ in self.children:
            if child.poll() is None:
                # Let the owned supervisor reap its own CLI. SIGCONT also lets
                # an intentionally stopped handler receive pending SIGTERM.
                child.terminate()
                child.send_signal(signal.SIGCONT)
                try:
                    child.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    # start_new_session confines this group to our own tree.
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                self.record('failure_cleanup_owned_process', pid=child.pid, returncode=child.returncode)
            stream.close()
        self.record('cleanup', all_owned_children_reaped=all(p.poll() is not None for p, _, _ in self.children))
        (self.output / 'evidence.json').write_text(json.dumps(self.records, default=str, indent=2) + '\n')


@pytest.fixture
def processes(request, tmp_path):
    output = Path(os.environ.get('AUD012_EVIDENCE_DIR', str(tmp_path))) / request.node.name
    output.mkdir(parents=True, exist_ok=True)
    manager = Processes(output)
    try:
        yield manager
    finally:
        manager.close()


def assert_preparation_only(actual):
    assert all(value == 0 for value in actual['effect_counts'].values()), actual['effect_counts']
    assert actual['effects'] == []
    assert all(action['action'] == 'PREPARE' for action in actual['actions'])


@pytest.mark.parametrize('failure', ['outer_timeout', 'child_timeout'])
def test_owned_supervisor_timeout_reaps_its_child(processes, failure):
    """Cleanup probe only; this sleeping child is not a recovery/CLI claim."""
    marker = processes.output / 'owned-sleeping-child.json'
    sleeping = ('import json,os,time; from pathlib import Path; '
                f'Path({str(marker)!r}).write_text(json.dumps({{"pid":os.getpid(),"ppid":os.getppid()}})); '
                'time.sleep(60)')
    child_timeout = 1 if failure == 'child_timeout' else 60
    invoke = (f'import runpy,sys; runpy.run_path({str(HELPER)!r})["supervise"]'
              f'(3,.1,command=[sys.executable,"-c",{sleeping!r}],child_timeout={child_timeout})')
    supervisor = processes.start(['-c', invoke])
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        assert supervisor.poll() is None
        time.sleep(.01)
    proof = json.loads(marker.read_text())
    assert proof['ppid'] == supervisor.pid
    assert os.getpgid(proof['pid']) == supervisor.pid == os.getpgid(supervisor.pid)
    if failure == 'outer_timeout':
        with pytest.raises(subprocess.TimeoutExpired):
            processes.wait(supervisor, timeout=.05)
        processes.close()
        assert supervisor.returncode == 128 + signal.SIGTERM
    else:
        processes.wait(supervisor, expected=1)
    assert not Path(f'/proc/{proof["pid"]}').exists(), 'Owned child was not reaped'
    log = next(path for proc, _, path in processes.children if proc is supervisor)
    events = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
    cleanup = next(event for event in events if event['event'] == 'owned_cli_cleanup')
    assert cleanup['cli_pid'] == proof['pid'] and cleanup['child_reaped']
    assert cleanup['returncode'] == -signal.SIGTERM
    processes.record('deterministic_timeout_cleanup', failure=failure, supervisor_pid=supervisor.pid,
                     owned_child=proof, cleanup=cleanup, child_proc_entry_absent=True)


@pytest.mark.parametrize('checkpoint,retained_runs', [
    ('after_lease', 0), ('after_extraction', 1), ('before_preparation_commit', 1)])
def test_actual_handler_kill_restart_recovers_once(inv, processes, checkpoint, retained_runs):
    saved = capture(inv)
    before = original(inv, saved)
    child, proof = processes.stop_handler(saved, checkpoint)
    running = snapshot(inv, saved)
    assert running['jobs'][0]['state'] == 'RUNNING' and running['jobs'][0]['attempts'] == 1
    assert str(running['jobs'][0]['lease_token']) == proof['lease_token']
    assert running['proposals'] == [] and len(running['runs']) == retained_runs
    processes.record('stopped_before_kill', snapshot=running)
    processes.kill(child)
    # The existing CLI must not steal an unexpired committed lease.
    processes.cli()
    assert snapshot(inv, saved) == running
    processes.record('accelerated_expiry', **accelerate(inv, saved))
    processes.lifecycle()
    actual = snapshot(inv, saved)
    assert actual['jobs'][0]['state'] == 'SUCCEEDED' and actual['jobs'][0]['attempts'] == 2
    assert actual['jobs'][0]['lease_token'] is None and actual['jobs'][0]['lease_until'] is None
    assert len(actual['runs']) == retained_runs + 1
    assert len(actual['proposals']) == len(actual['revisions']) == len(actual['actions']) == 1
    # Restart preserves the same useful, non-authoritative preparation. The
    # untouched old bare phrase does not establish a personal/worker audience.
    assert actual['proposals'][0]['action_type'] == 'UNRESOLVED'
    assert actual['proposals'][0]['state'] == 'NEEDS_INPUT'
    assert actual['proposals'][0]['version'] == actual['revisions'][0]['version'] == 1
    revision = actual['revisions'][0]
    assert revision['missing'] == ['Для себя или работнику?']
    task = revision['payload']['task_candidates']
    assert task['title'] == task['original_instruction'] == before['raw_text'] == 'Сегодня полить İmamlı.'
    assert task['planned_date'] == '2026-10-08' and task['greenhouse_id'] == inv['g']
    assert task['worker_id'] is None and task.get('language') is None
    assert revision['provenance']['task_candidates']['basis'] == 'task_audience_requires_owner_choice'
    assert revision['provenance']['task_candidates']['fields']['original_instruction']['source_text'] == before['raw_text']
    assert actual['revisions'][0]['extraction_run_id'] == actual['runs'][-1]['id']
    assert actual['original'] == before
    assert_preparation_only(actual)
    service(inv)[0].process(UUID(saved['id']))
    processes.cli()
    assert snapshot(inv, saved) == actual
    processes.record('recovered_and_replayed_without_duplicates', snapshot=actual)


@pytest.mark.parametrize('checkpoint', ['after_lease', 'after_extraction'])
def test_resumed_stale_worker_cannot_publish_or_clobber(inv, processes, checkpoint):
    saved = capture(inv)
    old, proof = processes.stop_handler(saved, checkpoint)
    processes.record('old_worker_stopped', snapshot=snapshot(inv, saved))
    processes.record('accelerated_expiry', **accelerate(inv, saved))
    processes.cli()
    winner = snapshot(inv, saved)
    assert winner['jobs'][0]['state'] == 'SUCCEEDED' and winner['jobs'][0]['attempts'] == 2
    assert winner['runs'][-1]['lease_token'] != UUID(proof['lease_token'])
    old.send_signal(signal.SIGCONT)
    processes.wait(old)
    assert snapshot(inv, saved) == winner
    assert len(winner['proposals']) == len(winner['revisions']) == 1
    assert_preparation_only(winner)
    processes.record('old_worker_resumed_and_fenced', snapshot=winner)


def test_three_real_crashes_are_terminal_and_independent_job_proceeds(inv, processes):
    saved = capture(inv)
    before = original(inv, saved)
    for attempt in (1, 2, 3):
        child, _ = processes.stop_handler(saved)
        actual = snapshot(inv, saved)
        assert actual['jobs'][0]['attempts'] == attempt and actual['jobs'][0]['state'] == 'RUNNING'
        processes.kill(child)
        processes.record('attempt_crashed', attempt=attempt, snapshot=actual)
        processes.record('accelerated_expiry', **accelerate(inv, saved))
    independent = capture(inv, 'TEST AUD012 independent source after repeated crash.')
    processes.lifecycle()
    failed = snapshot(inv, saved)
    job = failed['jobs'][0]
    assert job['state'] == 'FAILED' and job['attempts'] == 3 and job['error_code'] == 'RETRY_EXHAUSTED'
    assert job['lease_token'] is None and job['lease_until'] is None
    assert failed['proposals'] == failed['runs'] == [] and failed['original'] == before
    owner_view = inv['c'].get(f"/v1/entities/{inv['e']}/owner/captures/{saved['id']}").json()
    assert owner_view['state'] == 'FAILED' and owner_view['retryable'] is False and owner_view['server_saved']
    assert snapshot(inv, independent)['jobs'][0]['state'] == 'SUCCEEDED'
    assert service(inv)[0].queue(UUID(inv['e']), inv['preview'][1])['preparing'] == 0
    service(inv)[0].process(UUID(saved['id']))
    processes.cli()
    assert snapshot(inv, saved) == failed
    assert_preparation_only(failed)
    processes.record('terminal_and_independent_progress', failed=failed, owner_view=owner_view,
                     independent=snapshot(inv, independent))


def test_real_recognition_failure_retry_bound_and_binary_original_survive(inv, processes):
    content = b'\x89PNG\r\n\x1a\nAUD012 deliberately malformed TEST image'
    saved = capture(inv, binary=content)
    independent = capture(inv, 'TEST AUD012 independent source beside failing recognition.')
    before = original(inv, saved)
    for attempt in (1, 2, 3):
        processes.cli()
        actual = snapshot(inv, saved)
        assert actual['jobs'][0]['state'] == 'FAILED' and actual['jobs'][0]['attempts'] == attempt
        assert actual['jobs'][0]['error_code'] == 'PREPARATION_FAILED'
        assert actual['proposals'] == actual['runs'] == []
        assert actual['original'] == before
        assert snapshot(inv, independent)['jobs'][0]['state'] == 'SUCCEEDED'
        processes.cli()
        assert snapshot(inv, saved) == actual  # Retry delay is enforced.
        processes.record('real_recognition_failure', attempt=attempt, snapshot=actual)
        if attempt < 3:
            processes.record('accelerated_retry', **accelerate(inv, saved, 'retry_at'))
    view = service(inv)[0].source(UUID(inv['e']), inv['preview'][1], UUID(saved['id']))
    assert view['state'] == 'FAILED' and not view['retryable']
    assert service(inv)[0].original(UUID(inv['e']), inv['preview'][1], UUID(saved['id']))[0] == content
    assert_preparation_only(actual)


def test_stale_recognition_failure_cannot_clear_new_workers_lease(inv, processes):
    saved = capture(inv, binary=b'\x89PNG\r\n\x1a\nAUD012 malformed stale recognition')
    old, _ = processes.stop_handler(saved)
    processes.record('accelerated_expiry', **accelerate(inv, saved))
    current, current_proof = processes.stop_handler(saved)
    claimed = snapshot(inv, saved)
    assert claimed['jobs'][0]['attempts'] == 2
    assert str(claimed['jobs'][0]['lease_token']) == current_proof['lease_token']
    old.send_signal(signal.SIGCONT)
    processes.wait(old)
    assert snapshot(inv, saved) == claimed
    processes.record('stale_real_ocr_failure_fenced', snapshot=claimed)
    current.send_signal(signal.SIGCONT)
    processes.wait(current)
    failed = snapshot(inv, saved)
    assert failed['jobs'][0]['state'] == 'FAILED' and failed['jobs'][0]['attempts'] == 2
    processes.record('accelerated_retry', **accelerate(inv, saved, 'retry_at'))
    processes.cli()
    terminal = snapshot(inv, saved)
    assert terminal['jobs'][0]['state'] == 'FAILED' and terminal['jobs'][0]['attempts'] == 3
    assert_preparation_only(terminal)


def test_binary_crash_recovery_uses_actual_local_ocr(inv, processes):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (900, 150), 'white')
    ImageDraw.Draw(image).text((20, 40), 'TEST AUD012 ORIGINAL SOURCE', fill='black', font_size=42)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    content = buffer.getvalue()
    saved = capture(inv, binary=content)
    before = original(inv, saved)
    child, _ = processes.stop_handler(saved)
    processes.kill(child)
    processes.record('accelerated_expiry', **accelerate(inv, saved))
    processes.cli()
    actual = snapshot(inv, saved)
    assert actual['jobs'][0]['state'] == 'SUCCEEDED' and actual['jobs'][0]['attempts'] == 2
    assert len(actual['runs']) == 1 and actual['runs'][0]['provider'] == 'local'
    assert actual['runs'][0]['engine_version'] == 'local-tesseract-tur-eng-v1'
    assert actual['runs'][0]['raw_output']['pages']
    assert actual['original'] == before
    assert service(inv)[0].original(UUID(inv['e']), inv['preview'][1], UUID(saved['id']))[0] == content
    assert_preparation_only(actual)
    processes.cli()
    assert snapshot(inv, saved) == actual
    processes.record('actual_local_ocr_recovered_preserved_binary', snapshot=actual)


def test_concurrent_existing_cli_invocations_prepare_once(inv, processes):
    saved = [capture(inv, f'TEST AUD012 independent concurrent source {n}') for n in range(4)]
    children = [processes.start([CLI]) for _ in range(4)]
    for child in children:
        processes.wait(child)
    for source in saved:
        actual = snapshot(inv, source)
        assert actual['jobs'][0]['state'] == 'SUCCEEDED' and actual['jobs'][0]['attempts'] == 1
        assert len(actual['runs']) == len(actual['proposals']) == len(actual['revisions']) == 1
        assert_preparation_only(actual)
        processes.record('concurrent_cli_single_preparation', snapshot=actual)


@pytest.mark.parametrize('terminal', ['POSTED', 'CONFIRMED_NONPOSTING', 'REJECTED'])
def test_recovery_does_not_resurrect_reviewed_terminal_slots(inv, processes, terminal):
    phrase = ('Должен 7000 TRY за ремонт.' if terminal == 'POSTED' else
              'Завтра работник починит плёнку в İmamlı.' if terminal == 'CONFIRMED_NONPOSTING' else
              'TEST AUD012 source to reject.')
    saved = capture(inv, phrase)
    processes.cli()
    proposal = next(p for p in queue(inv) if p['inbox_id'] == saved['id'])
    assert_preparation_only(snapshot(inv, saved))
    if terminal == 'POSTED':
        proposal = revise(inv, proposal, dict(amount='7000', currency='TRY', counterparty_id=inv['cp'],
                                             event_date='2026-10-08', description='TEST AUD012 reviewed payable'))
        assert confirm(inv, proposal)['state'] == terminal
    elif terminal == 'CONFIRMED_NONPOSTING':
        assert proposal['action_type'] == 'WORKER_TASK'
        worker = send(inv['c'], f"/entities/{inv['e']}/workers",
                      dict(display_name='TEST AUD012 worker', language='ru', source='test:aud012'))
        proposal = revise(inv, proposal, dict(title='TEST repair', original_instruction='TEST reviewed instruction',
                                             greenhouse_id=inv['g'], planned_date='2026-10-09',
                                             worker_id=worker['id'], language='ru'))
        assert confirm(inv, proposal)['state'] == terminal
    else:
        send(inv['c'], f"/entities/{inv['e']}/owner/confirmations/{proposal['id']}/reject",
             dict(expected_version=proposal['version'], reason='TEST AUD012 reject'), expected=200)
    reviewed = snapshot(inv, saved)
    assert reviewed['proposals'][0]['state'] == terminal
    service(inv)[0].process(UUID(saved['id']))
    processes.cli()
    assert snapshot(inv, saved) == reviewed
    # Adversarial TEST scheduling, not a new application transition: retain the
    # identity and attempts while making the existing job eligible once more.
    with inv['preview'][0].begin() as db:
        db.execute(text("UPDATE core.processing_jobs SET state='RUNNING',lease_token=:t,lease_until=clock_timestamp()-interval '1 second' WHERE inbox_id=:s"),
                   dict(t=uuid4(), s=saved['id']))
    processes.record('test_only_forced_expired_scheduling', terminal=terminal, snapshot=snapshot(inv, saved))
    processes.cli()
    after = snapshot(inv, saved)
    assert after['jobs'][0]['state'] == 'SUCCEEDED' and after['jobs'][0]['attempts'] == 2
    assert len(after['runs']) == 2
    for key in ('proposals', 'revisions', 'actions', 'effects', 'effect_counts', 'original'):
        assert after[key] == reviewed[key], key
    assert after['effect_counts']['workers.assignments'] == 0
    assert not any(p['inbox_id'] == saved['id'] for p in queue(inv))
    processes.record('terminal_review_and_effect_preserved', terminal=terminal, snapshot=after)
