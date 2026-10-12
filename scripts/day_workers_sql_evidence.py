"""Isolated DayWorkers fixture and independent read-only PostgreSQL evidence.

Only --prepare changes state, using authenticated TEST owner services and an
owner-checked synthetic work-area adapter. Browser journeys perform every tested
request, decision, actual, accrual and payment. No credentials enter evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.services.reconciliation import reconcile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'test:day-workers-e2e:v1'
BUSINESS = (
    'finance.obligations', 'finance.payments', 'finance.direct_expenses',
    'finance.postings', 'finance.advances', 'finance.owner_fundings',
    'finance.advance_applications', 'finance.direct_settlements',
    'finance.own_account_transfers', 'inventory.events', 'inventory.movements',
    'inventory.stock', 'harvest.worker_reports', 'harvest.depot_settlements',
)
DAY = ('workers.day_requests', 'workers.day_decisions', 'workers.day_clarifications',
       'workers.day_actuals', 'workers.day_confirmations',
       'workers.day_accruals', 'workers.greenhouse_scope')
UNCHANGED_FULL = tuple(t for t in BUSINESS if t not in ('finance.obligations', 'finance.payments', 'finance.postings'))


def canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def write(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(canonical(value), ensure_ascii=False, indent=2) + '\n')


def rows(db, sql, **params):
    return canonical([dict(row) for row in db.execute(text(sql), params).mappings()])


def read_scope(db):
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    _require_connected_runtime(db)
    entities = db.execute(text('SELECT id FROM core.entities')).scalars().all()
    assert len(entities) == 1, 'Dedicated synthetic entity required'
    return entities[0]


def snapshot(db, entity_id, *, sha, engine, phase):
    tables = {t: rows(db, 'SELECT * FROM ' + t + ' WHERE entity_id=:e ORDER BY to_jsonb(' + t.split('.')[1] + ')::text', e=entity_id) for t in BUSINESS + DAY}
    tables['finance.posting_entries'] = rows(db, 'SELECT en.* FROM finance.posting_entries en JOIN finance.postings p ON p.id=en.posting_id WHERE p.entity_id=:e ORDER BY to_jsonb(en)::text', e=entity_id)
    tables['finance.payment_allocations'] = rows(db, 'SELECT a.* FROM finance.payment_allocations a JOIN finance.payments p ON p.id=a.payment_id WHERE p.entity_id=:e ORDER BY to_jsonb(a)::text', e=entity_id)
    tables['finance.obligation_attributions'] = rows(db, 'SELECT a.* FROM finance.obligation_attributions a JOIN finance.obligations o ON o.id=a.obligation_id WHERE o.entity_id=:e ORDER BY to_jsonb(a)::text', e=entity_id)
    tables['finance.obligation_balances'] = rows(db, 'SELECT * FROM finance.obligation_balances WHERE entity_id=:e ORDER BY obligation_id', e=entity_id)
    tables['cash'] = rows(db, 'SELECT b.* FROM finance.money_account_balances b JOIN finance.money_accounts a ON a.id=b.money_account_id WHERE a.entity_id=:e ORDER BY b.money_account_id', e=entity_id)
    # Originals are synthetic typed form snapshots. Authentication tables are
    # deliberately absent from every saved projection.
    tables['workers.sources'] = rows(db, "SELECT * FROM workers.sources WHERE entity_id=:e AND source LIKE 'day-workers:%' ORDER BY source,locator", e=entity_id)
    tables['workers.day_actual_allocations'] = rows(db, 'SELECT d.* FROM workers.day_actual_allocations d JOIN workers.day_actuals a ON a.id=d.actual_id WHERE a.entity_id=:e ORDER BY d.actual_id,d.ordinal', e=entity_id)
    return dict(sha=sha, engine=engine, phase=phase, entity_id=str(entity_id), tables=tables,
                digests={t: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest() for t, v in tables.items()})


def form(kind, fields, identity):
    return dict(schema_version=1, form_kind=kind,
                form_id=str(uuid5(NAMESPACE_URL, SOURCE + '/' + identity)),
                captured_at='2026-10-12T09:00:00+03:00', timezone='Europe/Istanbul',
                fields={k: str(v) for k, v in fields.items()})


def prepare(url, sf, credentials_path, engine, phase, sha):
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.infrastructure.models import WorkAreaRow
    from greenhouse_owner_os.preview import build_preview
    from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork

    private = Path(credentials_path).resolve()
    assert private.is_file() and 'artifacts' not in private.parts and not private.is_relative_to(ROOT)
    credentials = json.loads(private.read_text())
    owner, worker = credentials['owner'], credentials[engine]['worker']
    app = build_preview(url, os.getenv('PREVIEW_SECRET') or secrets.token_urlsafe(32), environment='test')
    entity_id, actor_id = app.state.pilot_auth.entity_id, app.state.pilot_auth.owner_id
    areas = []
    # The application has no public work-area creation endpoint. Match the
    # existing trusted synthetic_pilot adapter with new identities and audit.
    with SqlAlchemyUnitOfWork(sf) as u:
        _require_connected_runtime(u.session)
        assert u.session.scalar(text('SELECT core.r2_owner(:e,:a)'), dict(e=entity_id, a=actor_id))
        for label in ('permitted', 'unpermitted'):
            identity = SOURCE + '/' + engine + '/greenhouse/' + label
            area_id = uuid5(NAMESPACE_URL, identity)
            name = 'TEST DayWorkers ' + engine + ' ' + label
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,73))'), dict(key=str(area_id)))
            old = u.session.get(WorkAreaRow, area_id)
            if old:
                assert (old.entity_id, old.name, old.kind, old.source, old.parent_id) == (entity_id, name, 'greenhouse', SOURCE, None)
            else:
                u.session.add(WorkAreaRow(id=area_id, entity_id=entity_id, name=name, kind='greenhouse', source=SOURCE))
                u.session.flush()
                u.audit.append(actor_id=actor_id, action='reference_create', entity_type='work_area', entity_id=area_id,
                               before=None, after=dict(id=str(area_id), name=name, source=SOURCE, classification='TEST_PREVIEW'))
            areas.append(dict(id=str(area_id), name=name))
        u.commit()
    root = '/v1/entities/' + str(entity_id)
    with TestClient(app) as client:
        headers = {'Origin': 'http://testserver'}
        response = client.post('/pilot/login', json={k: owner[k] for k in ('username', 'password')}, headers=headers)
        assert response.status_code == 200, 'Synthetic owner authentication failed'
        headers['X-Pilot-Participant'] = owner['participant_id']
        def post(path, body, identity):
            response = client.post(path, json=body, headers=headers | {'Idempotency-Key': SOURCE + ':' + engine + ':' + identity})
            assert response.status_code in (200, 201), (path, response.status_code, response.text)
            return response.json()
        scope = post(root + '/day-workers/scopes', dict(source=form('day_scope', dict(
            worker_id=worker['worker_id'], greenhouse_id=areas[0]['id'], action='GRANT',
            reason='TEST owner explicitly verified this worker for this synthetic greenhouse'), engine + '/scope')), 'scope')
        refs = post(root + '/reference-bootstrap', dict(currency='TRY'), 'references')
        ledger = next(r['id'] for r in refs['references'] if r.get('system_code') == 'GENERAL_EXPENSE')
        creditor = post(root + '/counterparties', dict(name='TEST DayWorkers ' + engine + ' known creditor', role='supplier', source=SOURCE), 'creditor')
        account = post(root + '/money-accounts', dict(name='TEST DayWorkers ' + engine + ' payment account', account_type='cash', currency='TRY', source=SOURCE), 'account')
        area_reference = post(root + '/fertilizer/references', dict(
            client_reference_id=str(uuid5(NAMESPACE_URL, SOURCE + '/' + engine + '/area-reference')),
            captured_at='2026-10-12T09:00:00+03:00', work_area_id=areas[0]['id'], area_da='2',
            verification='TEST owner verified synthetic full greenhouse area, not canonical relative weights',
            effective_from='2026-10-01', confirmed=True), 'area-reference')
    return dict(sha=sha, engine=engine, phase=phase, entity_id=str(entity_id), greenhouse=areas[0],
                unpermitted_greenhouse=areas[1], worker_id=worker['worker_id'],
                other_worker_id=credentials[engine]['other_worker']['worker_id'], scope=scope,
                counterparty_id=creditor['id'], account_id=account['id'], expense_account_id=ledger,
                area_reference_id=area_reference['id'], rate='100.00', payment='200.00', currency='TRY',
                dates=['2026-10-11', '2026-10-12'], work='TEST ' + engine + ' подвязка растений',
                fixture_only=True, scenario_writes='Actual browser controls only')


def diff_rows(before, after, table):
    old = {r.get('id', json.dumps(r, sort_keys=True)): r for r in before['tables'][table]}
    new = {r.get('id', json.dumps(r, sort_keys=True)): r for r in after['tables'][table]}
    assert all(new.get(key) == value for key, value in old.items()), 'Pre-existing rows changed: ' + table
    return [row for key, row in new.items() if key not in old]


def verify(report, before, final, setup, db):
    assert report['status'] == 'PASS' and not report['errors']
    for value in (before, final, setup):
        assert all(value[k] == report[k] for k in ('sha', 'engine', 'phase', 'entity_id'))
    assert report['phase'] in ('request', 'full')
    assert report['request']['planned_people_days'] == 10
    assert report['request']['actual_people_days'] == 7
    assert len(report['actual_ids']) == 2 and len(set(report['actual_ids'])) == 2
    assert set(report['stage_files']) >= {'request', 'decision', 'actual-1', 'actual-2'}
    effect_tables = tuple(BUSINESS) + ('finance.posting_entries', 'finance.payment_allocations', 'finance.obligation_attributions', 'finance.obligation_balances', 'cash')
    for name in ('request', 'decision', 'actual-1', 'actual-2'):
        stage = json.loads(Path(report['stage_files'][name]).read_text())
        assert all(stage[k] == report[k] for k in ('sha', 'engine', 'phase', 'entity_id'))
        for table in effect_tables:
            assert stage['tables'][table] == before['tables'][table], f'{name} created an unauthorized effect in {table}'
    new_requests = diff_rows(before, final, 'workers.day_requests')
    assert len(new_requests) == (2 if report['phase'] == 'full' else 1)
    main_request = next(r for r in new_requests if r['id'] == report['request']['id'])
    assert all(r['greenhouse_id'] == setup['greenhouse']['id'] and r['worker_id'] == setup['worker_id'] for r in new_requests)
    assert main_request['people'] == 5 and main_request['days'] == 2
    assert main_request['work'] == setup['work'] and main_request['start_date'] is None
    new_decisions = diff_rows(before, final, 'workers.day_decisions')
    main_decisions = sorted((r for r in new_decisions if r['request_id'] == report['request']['id']), key=lambda r: r['version'])
    assert len(main_decisions) == (2 if report['phase'] == 'full' else 1)
    assert main_decisions[0]['decision'] == 'APPROVE' and main_decisions[0]['version'] == 2
    assert main_decisions[0]['plan'] == dict(people=5, days=2, work=setup['work'], start_date=None, note='')
    new_clarifications = diff_rows(before, final, 'workers.day_clarifications')
    if report['phase'] == 'full':
        reviewed = report['stale_review']
        assert reviewed['rejected_status'] == 409 and reviewed['explicit_current_review'] is True
        assert main_decisions[1]['id'] == reviewed['revised_decision_id']
        assert main_decisions[1]['decision'] == 'REVISE_APPROVE' and main_decisions[1]['version'] == 3
        assert main_decisions[1]['plan'] == dict(people=5, days=2, work=reviewed['work'], start_date=None, note='')
        assert report['worker_privacy_after_finance'] == dict(overview=True, request_detail=True, actual_details=2, rendered_detail=True)
        assert len(report['retained_routes']) == 2
        assert all(r['first_calls'] == 1 and r['cleaned_up'] and r['lifecycle'] == 'retained_until_explicit_cleanup' for r in report['retained_routes'])
        held = next(r for r in report['retained_routes'] if r['label'] == 'held-day-workers-read')
        assert held['terminal']['status'] == 200 and held['terminal']['terminal'] in ('requestfinished', 'requestfailed')
        clarification = report['clarification']
        extra_request = next(r for r in new_requests if r['id'] == clarification['request_id'])
        assert (extra_request['people'], extra_request['days'], extra_request['work']) == (2, 1, clarification['work'])
        questions = sorted((r for r in new_decisions if r['request_id'] == extra_request['id']), key=lambda r: r['version'])
        assert len(new_decisions) == 4 and len(questions) == 2
        assert [r['id'] for r in questions] == clarification['question_ids']
        assert [r['reason'] for r in questions] == clarification['questions']
        assert [r['version'] for r in questions] == [2, 3]
        assert all(r['decision'] == 'CLARIFY' and r['plan'] is None for r in questions)
        assert len(new_clarifications) == 1
        assert new_clarifications[0]['id'] == clarification['answer_id']
        assert new_clarifications[0]['request_id'] == extra_request['id']
        assert new_clarifications[0]['version'] == 4 and new_clarifications[0]['body'] == clarification['answer']
        assert clarification['rejected_status'] == 409 and clarification['explicit_current_review'] is True
        clarification_before = json.loads(Path(report['stage_files']['clarification-before']).read_text())
        clarification_after = json.loads(Path(report['stage_files']['clarification-after']).read_text())
        for table in effect_tables:
            assert clarification_before['tables'][table] == clarification_after['tables'][table], 'Clarification changed business effects: ' + table
    else:
        assert len(new_decisions) == 1 and not new_clarifications
    assert not diff_rows(before, final, 'workers.greenhouse_scope')
    new_actuals = diff_rows(before, final, 'workers.day_actuals')
    assert {r['id'] for r in new_actuals} == set(report['actual_ids'])
    assert sorted(int(r['people']) for r in new_actuals) == [3, 4]
    assert {r['event_date'] for r in new_actuals} == set(setup['dates'])
    assert all(r['request_id'] == report['request']['id'] and r['greenhouse_id'] == setup['greenhouse']['id'] for r in new_actuals)
    source_ids = {(r['source'], r['locator']) for r in new_requests + new_decisions + new_actuals + new_clarifications}
    sources = {(r['source'], r['locator']): r for r in final['tables']['workers.sources']}
    assert source_ids <= sources.keys(), 'Typed immutable originals missing'
    for row in new_requests + new_decisions + new_actuals + new_clarifications:
        saved = sources[(row['source'], row['locator'])]['snapshot']
        original = saved['command']['source']
        assert original['form_id'] == row['locator'] and saved['entity_id'] == row['entity_id']
        assert saved['actor_id'] == row['created_by'] and original['timezone'] == 'Europe/Istanbul'
    original_request = sources[(main_request['source'], main_request['locator'])]['snapshot']['command']['source']
    assert original_request['fields'] == dict(people='5', days='2', work=setup['work'], start_date='', note='')
    for actual in new_actuals:
        allocation = [r for r in final['tables']['workers.day_actual_allocations'] if r['actual_id'] == actual['id']]
        assert len(allocation) == 1 and allocation[0]['greenhouse_id'] == setup['greenhouse']['id']
        assert allocation[0]['people'] == actual['people'] and allocation[0]['work_area_id'] is None
    diff_rows(before, final, 'workers.sources')
    if report['phase'] == 'request':
        for table in effect_tables:
            assert final['tables'][table] == before['tables'][table], 'Request-only phase changed ' + table
        assert not diff_rows(before, final, 'workers.day_accruals')
        assert not diff_rows(before, final, 'workers.day_confirmations')
    else:
        for table in UNCHANGED_FULL:
            assert final['tables'][table] == before['tables'][table], 'Unexpected effect: ' + table
        obligations = diff_rows(before, final, 'finance.obligations')
        assert {r['id'] for r in obligations} == set(report['obligation_ids']) and len(obligations) == 2
        assert all(r['status'] == 'posted' and r['direction'] == 'payable' and r['obligation_class'] == 'OPERATIONAL'
                   and r['counterparty_id'] == setup['counterparty_id'] and r['currency'] == 'TRY' for r in obligations)
        assert sorted(Decimal(r['original_amount']) for r in obligations) == [Decimal('300'), Decimal('400')]
        accruals = diff_rows(before, final, 'workers.day_accruals')
        assert len(accruals) == 2 and {r['actual_id'] for r in accruals} == set(report['actual_ids'])
        assert all(Decimal(r['daily_rate']) == Decimal('100') and r['rate_basis'].startswith('TEST verified ') for r in accruals)
        confirmations = diff_rows(before, final, 'workers.day_confirmations')
        assert len(confirmations) == 2 and {r['actual_id'] for r in confirmations} == set(report['actual_ids'])
        actual_by_id = {r['id']: r for r in new_actuals}
        obligation_by_id = {r['id']: r for r in obligations}
        for accrual in accruals:
            actual, obligation = actual_by_id[accrual['actual_id']], obligation_by_id[accrual['obligation_id']]
            assert obligation['event_date'] == actual['event_date']
            assert Decimal(obligation['original_amount']) == actual['people'] * Decimal(accrual['daily_rate'])
        payments = diff_rows(before, final, 'finance.payments')
        assert len(payments) == 1 and payments[0]['id'] == report['payment_id']
        assert payments[0]['status'] == 'posted' and payments[0]['direction'] == 'out' and Decimal(payments[0]['amount']) == Decimal('200')
        allocations = [r for r in final['tables']['finance.payment_allocations'] if r['payment_id'] == report['payment_id']]
        assert sum(Decimal(r['amount']) for r in allocations) == Decimal('200')
        assert {r['obligation_id'] for r in allocations} <= set(report['obligation_ids'])
        attributions = [r for r in final['tables']['finance.obligation_attributions'] if r['obligation_id'] in report['obligation_ids']]
        assert len(attributions) == 2 and all(r['work_area_id'] == setup['greenhouse']['id'] for r in attributions)
        assert sum(Decimal(r['amount']) for r in attributions) == Decimal('700')
        balances = [r for r in final['tables']['finance.obligation_balances'] if r['obligation_id'] in report['obligation_ids']]
        assert sum(Decimal(r['remaining_amount']) for r in balances) == Decimal('500')
        before_cash = {r['money_account_id']: Decimal(r['balance']) for r in before['tables']['cash']}
        after_cash = {r['money_account_id']: Decimal(r['balance']) for r in final['tables']['cash']}
        assert after_cash[setup['account_id']] - before_cash[setup['account_id']] == Decimal('-200')
        assert all(after_cash[k] == v for k, v in before_cash.items() if k != setup['account_id'])
        postings = diff_rows(before, final, 'finance.postings')
        assert len(postings) == 3
        for posting in postings:
            entries = [r for r in final['tables']['finance.posting_entries'] if r['posting_id'] == posting['id']]
            assert sum(Decimal(r['amount']) * (1 if r['side'] == 'debit' else -1) for r in entries) == 0
    reconciliation = reconcile(db, include_classification=True, snapshot_established=True)
    assert reconciliation['ok'] and not reconciliation['findings']
    return dict(status='PASS', phase=report['phase'], sha=report['sha'], engine=report['engine'],
                entity_id=report['entity_id'], plan_people_days=10, actual_people_days=7,
                request_id=report['request']['id'], actual_ids=report['actual_ids'],
                typed_originals_verified=len(source_ids), no_automatic_finance=True,
                exact_payment_verified=report['phase'] == 'full', reconciliation=reconciliation,
                coverage='Focused ' + report['phase'] + ' phase; not a full regression gate')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--prepare', action='store_true'); p.add_argument('--credentials'); p.add_argument('--output')
    p.add_argument('--before'); p.add_argument('--probe', action='store_true')
    p.add_argument('--verify'); p.add_argument('--baseline')
    args = p.parse_args()
    assert sum(bool(x) for x in (args.prepare, args.before, args.probe, args.verify)) == 1
    engine, phase, sha = (os.environ[k] for k in ('DAY_WORKERS_ENGINE', 'DAY_WORKERS_PHASE', 'OWNER_PREVIEW_FEATURE_SHA'))
    assert engine in ('chromium', 'webkit') and phase in ('request', 'full')
    assert len(sha) == 40 and all(c in '0123456789abcdef' for c in sha)
    assert os.environ.get('APP_ENV') == 'test' and os.environ.get('R3_DAILY_PILOT') == '1' and os.environ.get('PILOT_LOCAL_TEST') == '1'
    url = os.environ['TEST_DATABASE_URL']; require_disposable_database(url, 'test')
    sf = make_session_factory(url)
    if args.prepare:
        write(args.output, prepare(url, sf, args.credentials, engine, phase, sha))
        print('DAY_WORKERS_SYNTHETIC_FIXTURE_READY'); return
    with sf() as db:
        entity_id = read_scope(db)
        current = snapshot(db, entity_id, sha=sha, engine=engine, phase=phase)
        if args.before or args.probe:
            write(args.before or args.output, current)
            print('DAY_WORKERS_READ_ONLY_SNAPSHOT_READY'); return
        report = json.loads(Path(args.verify).read_text())
        before = json.loads(Path(args.baseline).read_text())
        setup = json.loads((Path(args.verify).parent / 'setup.json').read_text())
        write(args.output, verify(report, before, current, setup, db))
        print('DAY_WORKERS_' + phase.upper() + '_SQL_PASS')


if __name__ == '__main__':
    main()
