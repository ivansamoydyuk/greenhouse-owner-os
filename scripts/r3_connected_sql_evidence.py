"""Attest the complete two-browser pilot in one guarded, read-only PG snapshot.

Expected values below come from synthetic_pilot.py and r3-connected.mjs inputs,
not from the observed database. This intentionally fails on partial browser runs,
extra effects, or a changed journey; it never repairs or resets the database.
"""
import hashlib
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url

from greenhouse_owner_os.domain.references import canonical_key
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.inventory import InventoryService
from greenhouse_owner_os.services.reconciliation import reconcile
from greenhouse_owner_os.services.references import reference_id
from greenhouse_owner_os.synthetic_pilot import OWNER_REF, SOURCE

ENGINES = ('chromium', 'webkit')
PRICES = (Decimal('10.00'), Decimal('123456789.12'))
TOTALS = (Decimal('600.00'), Decimal('7407407347.20'))
COUNTS = {
    'finance.postings': 11, 'finance.obligations': 4, 'finance.payments': 4,
    'finance.direct_expenses': 2, 'finance.advances': 0,
    'finance.owner_fundings': 1, 'finance.opening_payables': 0,
    'finance.advance_applications': 0, 'finance.direct_settlements': 0,
    'finance.own_account_transfers': 0,
    'inventory.events': 4, 'inventory.movements': 4, 'inventory.products': 3,
    'inventory.locations': 2, 'harvest.worker_reports': 2, 'harvest.batches': 2,
    'harvest.depot_settlements': 2, 'harvest.evidence': 4,
    'core.owner_tasks': 5, 'core.owner_task_actions': 17,
    'core.confirmation_effects': 8, 'core.invoice_receipt_links': 0,
    'core.inbox_items': 10, 'workers.tasks': 1, 'workers.assignments': 1,
    'workers.actions': 1, 'workers.reports': 0, 'workers.links': 0,
}
JOURNEYS = {
    'two-tab stale confirmation rejected before any effect',
    'A source revision trusted expense lost response hard reload',
    'E all tasks DONE reload completed restore',
    'two-tab old DONE replay preserves later restore and reads current state',
    'D inline product receipt exact physical movement UNKNOWN opening',
    'AUD011 stored adversarial and ordinary legacy modal title',
    'C field original file caption explicit depot link mismatch',
    'late A detail cannot target visible B action',
    'F cold and warm secondary outage keeps Home authenticated and explicit retry recovers',
    'offline persisted original reload session expiry recovery',
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def uid(value):
    return UUID(str(value))


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else bytes(value)).hexdigest()


def validate_browser(result, expected_sha):
    require(re.fullmatch('[0-9a-f]{40}', expected_sha or ''), 'Exact feature SHA required')
    require(result.get('status') == 'PASS' and result.get('mode') == 'CONNECTED_TEST'
            and result.get('sha') == expected_sha, 'Complete same-SHA connected browser PASS required')
    require(not result.get('errors') and not result.get('applicationFailures'), 'Browser failures remain')
    journeys = result['journeys']
    require({j['engine'] for j in journeys} == set(ENGINES), 'Both browser engines required')
    require(all(j.get('pass') is True for j in journeys), 'Every journey must pass')
    require(len({(j['engine'], j['name']) for j in journeys}) == len(journeys), 'Duplicate journey')
    for engine, direction in zip(ENGINES, ('payable', 'receivable')):
        expected = JOURNEYS | {f'B {direction} partial and full allocated settlement'}
        require(expected <= {j['name'] for j in journeys if j['engine'] == engine}, f'Incomplete {engine} journeys')
    records = result['records']
    for key, count, id_key in (
        ('expenses', 2, 'proposal_id'), ('payments', 4, None), ('receipts', 4, 'id'),
        ('reports', 2, None), ('settlements', 2, None), ('settlementEvidence', 2, 'id'),
        ('products', 2, None), ('tasks', 2, None), ('workerTasks', 2, None),
    ):
        values = records[key]
        require(len(values) == count, f'Expected {count} browser {key}')
        ids = [uid(v[id_key] if id_key else v) for v in values]
        # The same seeded task is deliberately reopened in both engines.
        require(len(set(ids)) == (1 if key in ('tasks', 'workerTasks') else count), f'Unexpected duplicate {key} IDs')
    require(len({uid(x['source_id']) for x in records['expenses']}) == 2, 'Expense sources must be distinct')
    require(len({uid(x['effect']['id']) for x in records['expenses']}) == 2, 'Expense effects must be distinct')
    for index, engine in enumerate(ENGINES):
        item = records['settlementEvidence'][index]
        require(uid(item['id']) == uid(records['settlements'][index])
                and uid(item['report_id']) == uid(records['reports'][index]), 'Evidence IDs do not match browser records')
        require(re.fullmatch('[0-9a-f]{64}', item['original_sha256'])
                and isinstance(item['original_bytes'], int) and item['original_bytes'] > 0, 'Independent upload hash and length required')
        require(item['caption'] == f'TEST {engine} separate owner caption'
                and item['field_original'] == f'TEST {engine} field original seven cuts', 'Unexpected original text input')
        require(Decimal(item['unit_price']) == PRICES[index], 'Unexpected independently supplied receipt price')
    return records


def attest(uow, result, proof=None):
    proof = proof if proof is not None else {}

    def phase(name):
        prior = proof.get('phase')
        if prior:
            proof.setdefault('completed_phases', []).append(prior)
        proof['phase'] = name
        print(json.dumps({'phase': name, 'status': 'RUNNING'}), flush=True)

    phase('guard_and_snapshot')
    session = uow.session
    # Must precede the runtime guard and all other queries.
    session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
    _require_connected_runtime(session)
    entity = reference_id('TEST_PREVIEW', 'entity', canonical_key('TEST R3 Synthetic Farm'))
    owner = reference_id('TEST_PREVIEW', 'actor', OWNER_REF)
    records = result['records']

    def rows(sql, **params):
        return [dict(r) for r in session.execute(text(sql), dict(e=entity, a=owner, **params)).mappings()]

    def one(sql, **params):
        values = rows(sql, **params)
        require(len(values) == 1, 'Expected exactly one scoped row: ' + sql.split('FROM', 1)[-1][:130])
        return values[0]

    def count(table):
        # Only internal static table names are passed here.
        return one(f'SELECT count(*) n FROM {table} WHERE entity_id=:e')['n']

    def exact_ids(table, expected):
        actual = {r['id'] for r in rows(f'SELECT id FROM {table} WHERE entity_id=:e')}
        require(actual == {uid(v) for v in expected}, 'Exact entity ID coverage failed: ' + table)

    def original(source_id, expected):
        row = one('SELECT id,raw_text,file_bytes,fingerprint,created_by FROM core.inbox_items WHERE entity_id=:e AND id=:id', id=uid(source_id))
        require(row['raw_text'] == expected and row['file_bytes'] is None and row['created_by'] == owner
                and row['fingerprint'] == digest(expected), 'Immutable inbox original mismatch')
        return dict(id=row['id'], sha256=row['fingerprint'], bytes=len(expected.encode()), exact_text_match=True)

    def replay_identity(operation, resource, expected=1):
        values = rows('SELECT id,operation,request_hash,resource_id,response_json FROM audit.idempotency_keys WHERE actor_id=:a AND operation=:op AND resource_id=:id ORDER BY id', op=operation, id=uid(resource))
        require(len(values) == expected, f'{operation}: expected {expected} durable request identities')
        for value in values:
            require(re.fullmatch('[0-9a-f]{64}', value['request_hash']) and uid(value['response_json']['id']) == uid(resource), 'Invalid replay identity/response')
        audit_action, audit_type = {
            'create_payment': ('create', 'payment'), 'post_payment': ('post', 'payment'),
            'inventory_post': ('inventory_post', 'inventory'), 'task_assign': ('task_assign', 'workers'),
        }.get(operation, (operation, 'owner_capture'))
        audits = rows('SELECT id,action,entity_type,entity_id,occurred_at FROM audit.events WHERE actor_id=:a AND action=:op AND entity_type=:kind AND entity_id=:id ORDER BY id', op=audit_action, kind=audit_type, id=uid(resource))
        require(len(audits) == expected, f'{operation}: durable audit count differs from request count')
        return dict(requests=[{k: v for k, v in value.items() if k != 'response_json'} for value in values], audit=audits)

    identity = one('''SELECT e.id,e.name,e.reference_class,e.finance_opening_policy,r.source,r.created_by,
        a.external_ref,a.actor_type,a.active,core.r2_owner(e.id,a.id) owner_bound
        FROM core.entities e JOIN core.reference_records r ON r.id=e.id AND r.entity_id=e.id AND r.kind='entity'
        JOIN core.actors a ON a.id=r.created_by WHERE e.id=:e AND a.id=:a''')
    require(identity['name'] == 'TEST R3 Synthetic Farm' and identity['reference_class'] == 'TEST_PREVIEW'
            and identity['source'] == SOURCE and identity['external_ref'] == OWNER_REF
            and identity['actor_type'] == 'owner' and identity['active'] and identity['owner_bound']
            and identity['finance_opening_policy'] == 'ZERO', 'Synthetic owner/entity identity is not proven')
    proof.update(feature_sha=result['sha'], entity_id=entity, owner_id=owner, identity=identity,
                 browser_journeys=result['journeys'], expenses=[], payments=[], receipts=[], reports=[], settlements=[], tasks=[])
    proof['transaction'] = one("SELECT current_setting('transaction_isolation') isolation,current_setting('transaction_read_only') read_only,pg_current_snapshot()::text snapshot")
    require(proof['transaction']['isolation'] == 'repeatable read' and proof['transaction']['read_only'] == 'on', 'Read-only repeatable snapshot required')
    proof['role'] = one('SELECT current_user role,session_user session_role,current_database() database,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')
    phase('exact_entity_counts')
    proof['counts'] = {table: count(table) for table in COUNTS}
    require(proof['counts'] == COUNTS, 'Exact pilot effect counts differ: ' + json.dumps({t: {'expected': v, 'actual': proof['counts'][t]} for t, v in COUNTS.items() if proof['counts'][t] != v}))

    phase('cash_and_seed_obligations')
    accounts = rows('''SELECT m.id,m.account_type,m.currency,m.name,m.ledger_account_id,b.balance
        FROM finance.money_accounts m JOIN finance.money_account_balances b ON b.money_account_id=m.id
        AND b.entity_id=m.entity_id AND b.currency=m.currency WHERE m.entity_id=:e ORDER BY m.account_type''')
    require(len(accounts) == 2 and {(r['account_type'], r['currency'], r['balance']) for r in accounts}
            == {('cash', 'TRY', Decimal('4348.00')), ('bank', 'TRY', Decimal('0.00'))}, 'Cash must equal 5000 - 252 - 1200 + 800 = 4348 TRY; bank zero')
    cash = next(r for r in accounts if r['account_type'] == 'cash')
    require(cash['name'] == 'TEST Касса TRY', 'Unexpected synthetic cash account')
    proof['accounts'] = accounts
    proof['cash_equation'] = '5000.00 - 2*126.00 - (100.00+1100.00) + (100.00+700.00) = 4348.00 TRY'
    parties = {r['counterparty_type']: r for r in rows('SELECT id,name,counterparty_type FROM core.counterparties WHERE entity_id=:e')}
    require(set(parties) == {'supplier', 'customer', 'depot'} and len(rows('SELECT id FROM core.counterparties WHERE entity_id=:e')) == 3, 'Unexpected counterparties')
    for role, name in (('supplier', 'TEST Поставщик А'), ('customer', 'TEST Клиент А'), ('depot', 'TEST Депо А')):
        require(parties[role]['name'] == name, 'Synthetic counterparty identity mismatch')

    obligations = rows('''SELECT o.*,b.remaining_amount FROM finance.obligations o
        JOIN finance.obligation_balances b ON b.obligation_id=o.id WHERE o.entity_id=:e ORDER BY o.id''')
    seed = {r['direction']: r for r in obligations if r['economic_source'] != 'harvest_depot'}
    require(len(seed) == 2, 'Exactly two original fixture obligations required')
    for direction, amount, role in (('payable', '1200.00', 'supplier'), ('receivable', '800.00', 'customer')):
        ob = seed[direction]
        require(ob['original_amount'] == Decimal(amount) and ob['remaining_amount'] == 0
                and ob['counterparty_id'] == parties[role]['id'] and ob['currency'] == 'TRY'
                and ob['status'] == 'posted' and ob['created_by'] == owner, 'Original fixture obligation mismatch')
    proof['settled_obligations'] = [dict(id=r['id'], direction=r['direction'], original_amount=r['original_amount'], remaining_amount=r['remaining_amount']) for r in seed.values()]

    phase('expenses_originals_effects_and_replay')
    exact_ids('finance.direct_expenses', [r['effect']['id'] for r in records['expenses']])
    for engine, item in zip(ENGINES, records['expenses']):
        expected = f'TEST {engine}: купил фильтр за 125 TRY наличными 10.10.2026'
        require(item['original'] == expected and Decimal(item['amount']) == Decimal('126.00'), 'Expense input changed')
        source = original(item['source_id'], expected)
        row = one('''SELECT d.*,p.inbox_id,p.action_type,p.state proposal_state,c.version confirmed_version,c.preview_digest,
            c.actor_id confirmation_actor,c.response,v.payload,v.preview_digest revision_digest
            FROM finance.direct_expenses d JOIN core.proposed_actions p ON p.id=d.proposal_id AND p.entity_id=d.entity_id
            JOIN core.confirmation_effects c ON c.proposal_id=p.id AND c.entity_id=p.entity_id AND c.direct_expense_id=d.id
            JOIN core.proposal_revisions v ON v.proposal_id=c.proposal_id AND v.entity_id=c.entity_id AND v.version=c.version
            WHERE d.entity_id=:e AND d.id=:id AND p.id=:p AND p.inbox_id=:s''', id=uid(item['effect']['id']), p=uid(item['proposal_id']), s=uid(item['source_id']))
        require(row['amount'] == Decimal('126.00') and row['currency'] == 'TRY' and row['status'] == 'posted'
                and row['action_type'] == 'DIRECT_EXPENSE' and row['proposal_state'] == 'POSTED'
                and row['money_account_id'] == cash['id'] and row['counterparty_id'] is None
                and row['created_by'] == row['confirmation_actor'] == owner
                and str(row['event_date']) == '2026-10-10' and row['reversal_posting_id'] is None
                and row['description'] == f'TEST {engine} фильтр после уточнения'
                and row['preview_digest'] == row['revision_digest']
                and Decimal(row['payload']['amount']) == Decimal('126.00')
                and uid(row['response']['effect']['id']) == row['id']
                and uid(item['effect']['posting_id']) == row['posting_id'], 'Expense source/proposal/effect/posting linkage mismatch')
        require(one('SELECT count(*) n FROM core.confirmation_effects WHERE entity_id=:e AND direct_expense_id=:id', id=row['id'])['n'] == 1, 'Expense duplicate effect')
        confirmations = rows("SELECT id,version,actor_id FROM core.proposal_actions WHERE entity_id=:e AND proposal_id=:id AND action='CONFIRM'", id=row['proposal_id'])
        require(len(confirmations) == 1 and confirmations[0]['version'] == row['confirmed_version'] and confirmations[0]['actor_id'] == owner, 'Expense confirmation audit mismatch')
        recovery = rows('SELECT * FROM core.owner_recovery_links WHERE entity_id=:e AND recovery_proposal_id=:p', p=row['proposal_id'])
        require(len(recovery) <= 1, 'Multiple recovery origins')
        for link in recovery:
            require(link['inbox_id'] == row['inbox_id'] and link['created_by'] == owner, 'Recovery source mismatch')
            if link['origin_proposal_id'] is not None:
                origin = one('SELECT inbox_id FROM core.proposed_actions WHERE entity_id=:e AND id=:id', id=link['origin_proposal_id'])
                require(origin['inbox_id'] == row['inbox_id'], 'Foreign recovery origin')
            else:
                job = one('SELECT inbox_id FROM core.processing_jobs WHERE entity_id=:e AND id=:id', id=link['failed_job_id'])
                require(job['inbox_id'] == row['inbox_id'], 'Foreign failed-job recovery')
        proof['expenses'].append(dict(id=row['id'], proposal_id=row['proposal_id'], source=source, amount=row['amount'], currency=row['currency'], posting_id=row['posting_id'], confirmed_version=row['confirmed_version'], recovery=recovery,
            confirmation_actions=confirmations, replay=replay_identity('owner_confirm', row['proposal_id'])))
        replay_identity('owner_capture', row['inbox_id'])

    phase('payments_allocations_and_replay')
    exact_ids('finance.payments', records['payments'])
    for payment, (direction, amount, debt) in zip(records['payments'], (
        ('out', '100.00', seed['payable']), ('out', '1100.00', seed['payable']),
        ('in', '100.00', seed['receivable']), ('in', '700.00', seed['receivable']),
    )):
        row = one('SELECT * FROM finance.payments WHERE entity_id=:e AND id=:id', id=uid(payment))
        require(row['status'] == 'posted' and row['direction'] == direction and row['amount'] == Decimal(amount)
                and row['currency'] == debt['currency'] == 'TRY' and row['counterparty_id'] == debt['counterparty_id']
                and row['money_account_id'] == cash['id'] and row['created_by'] == owner
                and row['reversal_posting_id'] is None, 'Payment party/account/direction/amount mismatch')
        allocations = rows('''SELECT pa.id,pa.payment_id,pa.obligation_id,pa.amount FROM finance.payment_allocations pa
            JOIN finance.payments p ON p.id=pa.payment_id JOIN finance.obligations o ON o.id=pa.obligation_id
            AND o.entity_id=p.entity_id WHERE p.entity_id=:e AND p.id=:id''', id=row['id'])
        require(len(allocations) == 1 and allocations[0]['obligation_id'] == debt['id']
                and allocations[0]['amount'] == row['amount'], 'Payment allocation must fully settle only its original debt')
        proof['payments'].append(dict(id=row['id'], amount=row['amount'], direction=direction, currency=row['currency'], money_account_id=row['money_account_id'], counterparty_id=row['counterparty_id'], posting_id=row['posting_id'], allocations=allocations,
            create_replay=replay_identity('create_payment', row['id']), post_replay=replay_identity('post_payment', row['id'])))
    require(one('SELECT count(*) n FROM finance.payment_allocations pa JOIN finance.payments p ON p.id=pa.payment_id WHERE p.entity_id=:e')['n'] == 4, 'Unexpected payment allocations')

    phase('inventory_movements_unknown_opening')
    locations = {r['name']: r for r in rows('SELECT id,name,work_area_id FROM inventory.locations WHERE entity_id=:e')}
    require(set(locations) == {'TEST Склад Север', 'TEST Склад Юг'} and all(r['work_area_id'] is None for r in locations.values()), 'Warehouses must be independently configured')
    north, south = locations['TEST Склад Север']['id'], locations['TEST Склад Юг']['id']
    fixture_product = one("SELECT id FROM inventory.products WHERE entity_id=:e AND name='TEST KNO3' AND base_unit='kg' AND source=:source", source=SOURCE)['id']
    exact_ids('inventory.products', [fixture_product, *records['products']])
    exact_ids('inventory.events', [r['id'] for r in records['receipts']])
    expected_stock = {}
    for i, engine in enumerate(ENGINES):
        product = one('SELECT id,name,base_unit,created_by FROM inventory.products WHERE entity_id=:e AND id=:id', id=uid(records['products'][i]))
        require(product['name'] == f'TEST {engine} New Product <img src=x onerror=alert(1)>' and product['base_unit'] == 'kg' and product['created_by'] == owner, 'New product identity mismatch')
        for j, (pid, loc, qty, raw) in enumerate(((product['id'], north, Decimal('12.5'), f'TEST {engine} original receipt 12.5 kg'),
                (fixture_product, south, Decimal('3'), f'TEST {engine} existing product other location'))):
            rec = records['receipts'][i*2+j]
            require((uid(rec['product_id']), uid(rec['location_id']), Decimal(rec['quantity'])) == (pid, loc, qty), 'Browser stock input mismatch')
            row = one('SELECT * FROM inventory.events WHERE entity_id=:e AND id=:id', id=uid(rec['id']))
            require(row['kind'] == 'receipt' and row['status'] == 'posted' and row['created_by'] == row['posted_by'] == owner
                    and row['evidence']['raw_text'] == raw and row['replacement_of_id'] is None, 'Receipt original/status mismatch')
            require(row['duplicate_reviewed'] is (i == 1 and j == 1), 'Intentional second South delivery must be explicitly duplicate-reviewed')
            require(len(row['payload']['lines']) == 1 and row['payload']['lines'][0]['unit_cost'] is None, 'Receipt must not invent an invoice or unit cost')
            movements = rows('SELECT * FROM inventory.movements WHERE entity_id=:e AND event_id=:id', id=row['id'])
            require(len(movements) == 1, 'Each receipt must produce one movement')
            m = movements[0]
            require((m['product_id'], m['location_id'], m['quantity'], m['uncosted_quantity'], m['known_cost'], m['role'])
                    == (pid, loc, qty, qty, Decimal(0), 'receipt') and m['greenhouse_id'] is None
                    and m['work_area_id'] is None and m['reversal_of_seq'] is None, 'Receipt movement/scope/cost mismatch')
            expected_stock[(pid, loc)] = expected_stock.get((pid, loc), Decimal(0)) + qty
            proof['receipts'].append(dict(id=row['id'], original_sha256=digest(raw), movements=movements, replay=replay_identity('inventory_post', row['id'])))
    stock = rows('SELECT * FROM inventory.stock WHERE entity_id=:e')
    require({(r['product_id'], r['location_id']): r['quantity'] for r in stock} == expected_stock
            and all(r['uncosted_quantity'] == r['quantity'] and r['known_cost'] == 0 for r in stock)
            and sum(r['quantity'] for r in stock) == Decimal('31'), 'Exact known stock subtotal must be 31 uncosted kg')
    inventory = InventoryService(None).read(uow, entity)
    require(inventory['opening_state'] == 'UNINITIALIZED_VERIFY' and inventory['invariants']['ok'], 'Unknown stock opening/invariants not preserved')
    proof['inventory'] = dict(stock=stock, opening_state=inventory['opening_state'], invariants=inventory['invariants'], known_movement_subtotal_kg='31', total_physical_stock='UNKNOWN', known_cost_component='0; full valuation remains unknown')

    phase('harvest_independent_originals_and_authoritative_links')
    exact_ids('harvest.worker_reports', records['reports'])
    exact_ids('harvest.depot_settlements', records['settlements'])
    batch_ids = []
    evidence_ids = []
    depot_obligations = []
    for i, engine in enumerate(ENGINES):
        item = records['settlementEvidence'][i]
        report = one('''SELECT r.*,e.raw_text,e.fingerprint,e.file_bytes FROM harvest.worker_reports r
            JOIN harvest.evidence e ON e.id=r.evidence_id AND e.entity_id=r.entity_id WHERE r.entity_id=:e AND r.id=:id''', id=uid(item['report_id']))
        require(report['cut_count'] == 7 and report['field_weight_kg'] == 70 and report['raw_text'] == item['field_original']
                and report['fingerprint'] == digest(item['field_original']) and report['file_bytes'] is None
                and report['created_by'] == owner, 'Field original/count/weight mismatch')
        settlement = one('SELECT * FROM harvest.depot_settlements WHERE entity_id=:e AND id=:id', id=uid(item['id']))
        ev = one('SELECT * FROM harvest.evidence WHERE entity_id=:e AND id=:id', id=settlement['evidence_id'])
        expected_mime = 'image/png' if i == 0 else 'application/pdf'
        expected_filename = f'TEST-{engine}-receipt.' + ('png' if i == 0 else 'pdf')
        require(ev['file_bytes'] is not None and digest(ev['file_bytes']) == ev['fingerprint'] == item['original_sha256']
                and len(ev['file_bytes']) == item['original_bytes'] and ev['raw_text'] == item['caption']
                and ev['filename'] == item['filename'] == expected_filename and ev['mime_type'] == item['mime_type'] == expected_mime
                and ev['created_by'] == owner, 'Original upload bytes/hash/caption/metadata mismatch')
        require(settlement['status'] == 'posted' and settlement['total_amount'] == TOTALS[i]
                and settlement['currency'] == 'TRY' and settlement['depot_id'] == parties['depot']['id']
                and settlement['created_by'] == owner and settlement['payment_id'] is None
                and settlement['application_ids'] == [] and settlement['reversed_at'] is None
                and settlement['link_evidence'] == f'TEST {engine} explicitly the selected cut'
                and {'count_discrepancy', 'weight_discrepancy', 'unallocated'} <= set(settlement['reviewed_issues'])
                and settlement['review_reason'] == 'TEST: sources remain distinct; count and weight mismatch reviewed; unallocated geography remains unknown.', 'Depot review/financial authority mismatch')
        links = rows('''SELECT b.id batch_id,r.id report_id,b.link_evidence FROM harvest.settlement_batches sb
            JOIN harvest.depot_settlements s ON s.id=sb.settlement_id JOIN harvest.batches b ON b.id=sb.batch_id AND b.entity_id=s.entity_id
            JOIN harvest.batch_reports br ON br.batch_id=b.id JOIN harvest.worker_reports r ON r.id=br.report_id AND r.entity_id=b.entity_id
            WHERE s.entity_id=:e AND s.id=:id''', id=settlement['id'])
        require(len(links) == 1 and links[0]['report_id'] == report['id']
                and links[0]['link_evidence'] == settlement['link_evidence'], 'Exact report/batch/settlement evidence link required')
        batch_ids.append(links[0]['batch_id'])
        lines = rows('''SELECT l.* FROM harvest.settlement_lines l JOIN harvest.depot_settlements s ON s.id=l.settlement_id
            WHERE s.entity_id=:e AND s.id=:id''', id=settlement['id'])
        require(len(lines) == 1, 'Exactly one depot line expected')
        line = lines[0]
        require(line['count'] == 6 and line['net_weight_kg'] == 60 and line['unit_price'] == PRICES[i]
                and line['amount'] == line['calculated_amount'] == TOTALS[i]
                and line['source_line'] == f"Grand 60 kg x {item['unit_price']} TRY, ADET 6", 'Depot quantities/price/source line mismatch')
        require(one('SELECT count(*) n FROM harvest.line_allocations WHERE line_id=:id', id=line['id'])['n'] == 0, 'Unallocated geography must remain unknown')
        ob = next(r for r in obligations if r['id'] == settlement['obligation_id'])
        require(ob['direction'] == 'receivable' and ob['counterparty_id'] == settlement['depot_id']
                and ob['currency'] == 'TRY' and ob['original_amount'] == ob['remaining_amount'] == TOTALS[i]
                and ob['economic_source'] == 'harvest_depot' and ob['status'] == 'posted', 'Each receipt must create exactly its unpaid receivable')
        depot_obligations.append(ob['id'])
        evidence_ids.extend((report['evidence_id'], ev['id']))
        proof['reports'].append(dict(id=report['id'], evidence_id=report['evidence_id'], original_sha256=report['fingerprint'], count=7, kg='70'))
        proof['settlements'].append(dict(id=settlement['id'], evidence_id=ev['id'], original_sha256=ev['fingerprint'], independent_expected_sha256=item['original_sha256'], original_bytes=len(ev['file_bytes']), caption_sha256=digest(ev['raw_text']), filename=ev['filename'], mime_type=ev['mime_type'], links=links, line={k: line[k] for k in ('id', 'count', 'net_weight_kg', 'unit_price', 'amount')}, obligation_id=ob['id'], remaining=ob['remaining_amount'], payment_id=None, reviewed_issues=settlement['reviewed_issues']))
    exact_ids('harvest.batches', batch_ids)
    exact_ids('harvest.evidence', evidence_ids)
    exact_ids('finance.obligations', [r['id'] for r in seed.values()] + depot_obligations)
    require(sum(r['remaining_amount'] for r in obligations if r['economic_source'] == 'harvest_depot') == Decimal('7407407947.20'), 'Unpaid depot AR total mismatch')
    proof['unpaid_depot_receivables_try'] = '7407407947.20'

    phase('personal_task_history_and_replay')
    tasks = rows('SELECT * FROM core.owner_tasks WHERE entity_id=:e ORDER BY planned_date NULLS LAST,created_at,id')
    require({t['title'] for t in tasks} == {f'TEST личная задача {n}' for n in range(1, 6)}, 'Unexpected personal task set')
    require(uid(records['tasks'][0]) == tasks[-1]['id'], 'Main browser task is not the last seeded task')
    # Same ordered immutable seed list in each engine: last task has 4 transitions
    # per engine; the navigation-race second task has 2 transitions per engine.
    expected_versions = {t['id']: 1 + (8 if index == 4 else 4 if index == 1 else 0) for index, t in enumerate(tasks)}
    for task in tasks:
        require(task['state'] == 'TO_DO' and task['version'] == expected_versions[task['id']] and task['owner_id'] == owner, 'Task replay changed restored state/version')
        n = int(task['title'].rsplit(' ', 1)[1])
        expected = f'Сегодня проверить давление в TEST Теплица А, TEST задача {n}.'
        lineage = one('''SELECT p.inbox_id,c.version,c.actor_id,v.payload FROM core.proposed_actions p
            JOIN core.confirmation_effects c ON c.proposal_id=p.id AND c.entity_id=p.entity_id
            JOIN core.proposal_revisions v ON v.proposal_id=c.proposal_id AND v.entity_id=c.entity_id AND v.version=c.version
            WHERE p.entity_id=:e AND p.id=:p AND c.owner_task_id=:id''', p=task['proposal_id'], id=task['id'])
        require(lineage['actor_id'] == owner and lineage['payload']['original_instruction'] == expected, 'Personal task original lineage mismatch')
        source = original(lineage['inbox_id'], expected)
        actions = rows('SELECT id,version,actor_id,state FROM core.owner_task_actions WHERE entity_id=:e AND task_id=:id ORDER BY version', id=task['id'])
        require([r['version'] for r in actions] == list(range(1, task['version']+1))
                and all(r['actor_id'] == owner and r['state'] == ('TO_DO' if r['version'] % 2 else 'DONE') for r in actions), 'Personal task action lineage/replay mismatch')
        replay = replay_identity('owner_task_state', task['id'], task['version']-1)
        proof['tasks'].append(dict(id=task['id'], state=task['state'], version=task['version'], source=source, actions=actions, replay=replay))

    phase('worker_original_review_and_internal_assignment')
    exact_ids('workers.tasks', [records['workerTasks'][0]])
    worker_task = one('SELECT * FROM workers.tasks WHERE entity_id=:e AND id=:id', id=uid(records['workerTasks'][0]))
    expected_original = 'TEST Работник А, проверьте трубу в TEST Теплица А 2026-10-10.'
    expected_rendering = 'TEST کارگر الف، لوله را در گلخانهٔ آزمایشی الف در تاریخ ۲۰۲۶-۱۰-۱۰ بررسی کنید.'
    require(worker_task['status'] == 'assigned' and worker_task['original_instruction'] == expected_original, 'Worker task original/state mismatch')
    assignment = one('SELECT * FROM workers.assignments WHERE entity_id=:e AND task_id=:id', id=worker_task['id'])
    require(assignment['assigned_by'] == owner and assignment['rendering_reviewed'] is True
            and assignment['language'] == 'fa' and assignment['rendered_text'] == expected_rendering
            and assignment['rendered_text'] != worker_task['original_instruction'], 'Reviewed worker rendering mismatch')
    worker = one('SELECT id,display_name,contact,telegram_ref FROM workers.people WHERE entity_id=:e AND id=:id', id=assignment['worker_id'])
    require(worker['display_name'] == 'TEST Работник А' and worker['contact'] is None and worker['telegram_ref'] is None, 'Real worker contact must be absent')
    action = one('SELECT id,kind,assignment_id,actor_id FROM workers.actions WHERE entity_id=:e AND task_id=:id', id=worker_task['id'])
    require(action['kind'] == 'assign' and action['assignment_id'] == assignment['id'] and action['actor_id'] == owner, 'Worker assignment action mismatch')
    lineage = one('''SELECT p.inbox_id,c.actor_id,v.payload FROM core.proposed_actions p
        JOIN core.confirmation_effects c ON c.proposal_id=p.id AND c.entity_id=p.entity_id
        JOIN core.proposal_revisions v ON v.proposal_id=c.proposal_id AND v.entity_id=c.entity_id AND v.version=c.version
        WHERE p.entity_id=:e AND c.worker_task_id=:id''', id=worker_task['id'])
    require(lineage['actor_id'] == owner and lineage['payload']['original_instruction'] == expected_original
            and lineage['payload']['rendered_text'] == expected_rendering, 'Worker source/review linkage mismatch')
    proof['worker'] = dict(task_id=worker_task['id'], assignment_id=assignment['id'], action=action, source=original(lineage['inbox_id'], expected_original), rendering_sha256=digest(expected_rendering), rendering_reviewed=True, contact=None, telegram_ref=None, replay=replay_identity('task_assign', assignment['id']), external_dispatch='No external channel exists in this bounded pilot; no contact or Telegram is configured')
    phase('offline_original_recovery')
    proof['offline_sources'] = []
    for engine in ENGINES:
        expected = f'TEST {engine} offline source remains original'
        source = one('SELECT id FROM core.inbox_items WHERE entity_id=:e AND raw_text=:raw', raw=expected)
        proof['offline_sources'].append(original(source['id'], expected))
        replay_identity('owner_capture', source['id'])
        require(one('''SELECT count(*) n FROM core.confirmation_effects c JOIN core.proposed_actions p
            ON p.id=c.proposal_id AND p.entity_id=c.entity_id WHERE p.entity_id=:e AND p.inbox_id=:id''', id=source['id'])['n'] == 0, 'Offline original unexpectedly confirmed')

    phase('exact_journal_and_scoped_reconciliation')
    funding = one('SELECT * FROM finance.owner_fundings WHERE entity_id=:e')
    require(funding['amount'] == Decimal('5000') and funding['currency'] == 'TRY' and funding['status'] == 'posted'
            and funding['money_account_id'] == cash['id'] and funding['source'] == SOURCE, 'Synthetic funding mismatch')
    expected_postings = {('obligation', r['id']): r['posting_id'] for r in obligations}
    expected_postings.update({('payment', r['id']): r['posting_id'] for r in proof['payments']})
    expected_postings.update({('direct_expense', r['id']): r['posting_id'] for r in proof['expenses']})
    expected_postings[('owner_funding', funding['id'])] = funding['posting_id']
    postings = rows('SELECT id,source_type,source_id,currency,created_by,reversal_of_id FROM finance.postings WHERE entity_id=:e')
    require({(r['source_type'], r['source_id']): r['id'] for r in postings} == expected_postings
            and all(r['currency'] == 'TRY' and r['created_by'] == owner and r['reversal_of_id'] is None for r in postings), 'Journal must contain exactly the 11 expected effect postings')
    journal = rows('''SELECT p.id posting_id,count(*) entries,sum(CASE WHEN pe.side='debit' THEN pe.amount ELSE -pe.amount END) delta,
        bool_and(l.entity_id=p.entity_id AND l.currency=p.currency) scoped
        FROM finance.postings p JOIN finance.posting_entries pe ON pe.posting_id=p.id
        JOIN finance.ledger_accounts l ON l.id=pe.ledger_account_id WHERE p.entity_id=:e GROUP BY p.id''')
    require(len(journal) == 11 and all(r['entries'] == 2 and r['delta'] == 0 and r['scoped'] for r in journal), 'Every expected journal must have two balanced scoped entries')
    proof['postings'] = postings
    proof['journal'] = journal
    proof['reconciliation'] = reconcile(session, include_classification=True, snapshot_established=True, entity_id=entity)
    require(proof['reconciliation']['ok'], 'Entity-scoped reconciliation failed')
    proof.setdefault('completed_phases', []).append(proof['phase'])
    proof['phase'] = 'complete'
    proof['status'] = 'PASS'
    return proof


def main():
    output = Path(sys.argv[2])
    proof = dict(status='RUNNING', phase='browser_manifest', completed_phases=[])
    try:
        result = json.loads(Path(sys.argv[1]).read_text())
        proof['feature_sha'] = result.get('sha')
        validate_browser(result, os.environ['OWNER_PREVIEW_FEATURE_SHA'])
        require(os.environ.get('R3_CONNECTED_PILOT') == '1', 'Explicit connected pilot mode required')
        url = make_url(os.environ['TEST_DATABASE_URL'])
        require_disposable_database(url, os.environ.get('APP_ENV'))
        require(not url.query, 'Connection URL overrides are not permitted')
        factory = make_session_factory(url)
        with SqlAlchemyUnitOfWork(factory) as uow:
            attest(uow, result, proof)
    except Exception as exc:
        # Preserve partial checked facts and phase. Never stringify DB exceptions:
        # they may contain SQL parameters, originals or connection information.
        proof['status'] = 'FAIL'
        proof['failure_type'] = type(exc).__name__
        proof['failure'] = str(exc) if isinstance(exc, AssertionError) else 'Execution error; see sanitized phase and exception type'
        output.write_text(json.dumps(proof, ensure_ascii=False, indent=2, default=str))
        print(json.dumps({k: proof[k] for k in ('status', 'phase', 'failure_type', 'failure')}), flush=True)
        raise SystemExit(1) from None
    output.write_text(json.dumps(proof, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(dict(status='PASS', feature_sha=result['sha'], cash_try='4348.00', counts=proof['counts'])))


if __name__ == '__main__':
    main()
