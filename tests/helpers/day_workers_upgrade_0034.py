"""Owned synthetic populated 0033 -> 0034 preservation and replay proof."""
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from tests.helpers.capture_daily_upgrade_0033 import validated_database_url
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_owner_capture import source, queue, confirm
from tests.integration.test_postgres_owner_expense_recovery import recover, fill
from tests.integration.test_postgres_readiness import send


def main():
    url = validated_database_url()
    engine = create_engine(url)
    with engine.begin() as db:
        for schema in ('workers', 'inventory', 'harvest', 'audit', 'finance', 'core'):
            db.execute(text('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE'))
        db.execute(text('DROP TABLE IF EXISTS public.alembic_version'))
    config = Config('alembic.ini')
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, '20261012_0033')
    fixture = inv.__wrapped__(preview.__wrapped__(url))
    original = source(fixture, 'TEST retained day-workers upgrade original 777')
    proposal = next(p for p in queue(fixture) if p['source']['id'] == original['id'])
    recovered = fill(fixture, recover(fixture, original, proposal, key='day-upgrade-legacy-handoff'))
    effect = confirm(fixture, recovered, key='day-upgrade-legacy-confirm')
    # Populate the directly affected Workers history as well as retained domains.
    from tests.integration.test_postgres_workers import setup, reported, act
    from tests.integration.test_postgres_workers_r2 import r2, body
    from tests.integration.test_postgres_fertilizer import fertilizer, ready, actual_input, confirm as confirm_fertilizer
    from tests.integration.test_postgres_harvest import receipt, draft, post
    f = SimpleNamespace(session_factory=fixture['preview'][0], entity_id=UUID(fixture['e']), actor_id=fixture['preview'][1], service=fixture['preview'][3])
    workers = setup.__wrapped__(f)
    reported(workers)
    act(workers, workers[2], workers[3], 'complete')
    task, assignment = r2(workers)
    received = body(assignment)
    worker_id, worker_actor = UUID(workers[2]['id']), UUID(workers[2]['actor_id'])
    response = workers[1].respond(f.entity_id, worker_actor, 'day-upgrade-r2-received', UUID(task['id']), worker_id, received)
    fert = fertilizer.__wrapped__(fixture)
    plan = ready(fert)
    fert_result = confirm_fertilizer(fert, plan, actual_input(fert, plan))
    harvest_root = '/entities/' + fixture['e'] + '/harvest'
    report = send(fixture['c'], harvest_root + '/reports', dict(event_date='2026-10-05', original_greenhouse='İmamlı', cut_count=131, reporter='TEST upgrade worker', evidence=dict(source='test:upgrade', source_reference='retained-field-report', raw_text='TEST retained field original 131')))
    batch = send(fixture['c'], harvest_root + '/batches', dict(event_date='2026-10-05', label='TEST retained load', report_ids=[report['id']], link_evidence='TEST explicit owner link'))
    depot = send(fixture['c'], '/entities/' + fixture['e'] + '/counterparties', dict(name='TEST upgrade depot', role='depot', source='test:upgrade'))
    receipt_body = receipt(depot['id'], {'Özvadi': UUID(fixture['g'])}, batch_ids=[batch['id']], link_evidence='TEST explicit retained depot link')
    receipt_body['original_greenhouse'] = 'İmamlı'
    receipt_body['lines'][0]['count'] = 93
    receipt_body['lines'][0]['allocations'][0]['confirmed_count'] = 93
    settlement = post(fixture['c'], fixture['e'], draft(fixture['c'], fixture['e'], receipt_body))
    with engine.connect() as db:
        assert db.scalar(text('SELECT version_num FROM public.alembic_version')) == '20261012_0033'
        tables = db.execute(text("SELECT schemaname || '.' || tablename FROM pg_tables WHERE schemaname IN ('core','finance','harvest','inventory','workers','audit') ORDER BY 1")).scalars().all()

    def snapshot():
        with engine.connect() as db:
            return {table: db.execute(text('SELECT to_jsonb(t) FROM ' + table + ' t ORDER BY to_jsonb(t)::text')).scalars().all() for table in tables}

    before = snapshot()
    for table in ('workers.tasks', 'workers.sources', 'workers.reports', 'harvest.worker_reports', 'harvest.depot_settlements', 'inventory.movements', 'finance.obligations', 'audit.idempotency_keys'):
        assert before[table], 'Missing representative retained data: ' + table
    command.upgrade(config, '20261012_0034')
    after = snapshot()
    assert before == after, 'Previously populated rows, sources or audit changed during additive upgrade'
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    print(json.dumps({'proof': 'populated_0033_to_0034', 'before_sha256': digest(before), 'after_sha256': digest(after), 'tables': len(tables), 'rows': sum(len(r) for r in before.values()), 'table_rows': {table: len(rows) for table, rows in before.items()}}, sort_keys=True))
    assert recover(fixture, original, proposal, key='day-upgrade-legacy-handoff')['id'] == recovered['id']
    assert confirm(fixture, recovered, key='day-upgrade-legacy-confirm') == effect
    assert workers[1].respond(f.entity_id, worker_actor, 'day-upgrade-r2-received', UUID(task['id']), worker_id, received) == response
    assert workers[1].get(f.entity_id, UUID(workers[3]['id']))['status'] == 'completed'
    assert fert['svc'].get(f.entity_id, f.actor_id, UUID(plan['id']))['actual']['application_id'] == fert_result['application_id']
    retained = fixture['c'].get('/v1' + harvest_root + '/settlements/' + settlement['id']).json()
    assert retained['reports'][0]['cut_count'] == 131 and retained['lines'][0]['count'] == 93
    with engine.connect() as db:
        assert db.scalar(text('SELECT version_num FROM public.alembic_version')) == '20261012_0034'
    assert fixture['preview'][3].reconcile(entity_id=UUID(fixture['e']))['ok']
    assert snapshot() == after, 'Cached replay or readback changed retained rows or created another effect'
    print('PASS populated 0033 -> 0034: all old tables preserved exactly; confirmed replay stable; Finance reconciliation clean')
    engine.dispose()


if __name__ == '__main__':
    main()
