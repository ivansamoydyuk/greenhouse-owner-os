"""Deterministic synthetic-only R3 pilot data, using existing trusted services.

This is not an owner opening profile. Every economic seed is an explicit TEST
operation posted through the ordinary domain APIs and listed in the manifest.
There is no stock opening, physical area mapping, real contact or dispatch.
"""
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from greenhouse_owner_os.infrastructure.models import WorkAreaRow
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database

SOURCE = 'test:r3-connected-pilot:v1'
OWNER_REF = SOURCE + ':owner'
OWNER_NAME = 'TEST R3 Owner'
DATABASE_MARKER = 'r3-connected-pilot:synthetic-only:v1'


def _work_areas(factory, entity_id, actor_id):
    """Trusted reference bootstrap; no public work-area setup endpoint exists.

    Mirrors the existing dimensions adapter with independent synthetic names,
    an explicit owner check, identity lock, conflict check, and audit evidence.
    """
    result = []
    with factory() as u:
        require_disposable_database(u.session.get_bind().url, 'test')
        if not u.session.scalar(text('SELECT core.r2_owner(:e,:a)'), dict(e=entity_id, a=actor_id)):
            raise ValueError('synthetic references require the authenticated entity owner')
        for name in ('TEST Теплица А', 'TEST Теплица Б', 'TEST Теплица В'):
            rid = uuid5(NAMESPACE_URL, f'{SOURCE}/{entity_id}/greenhouse/{name}')
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,73))'), dict(key=str(rid)))
            old = u.session.get(WorkAreaRow, rid)
            if old and (old.entity_id, old.name, old.kind, old.source, old.parent_id) != (entity_id, name, 'greenhouse', SOURCE, None):
                raise ValueError('synthetic work-area identity collision')
            if not old:
                u.session.add(WorkAreaRow(id=rid, entity_id=entity_id, name=name, kind='greenhouse', source=SOURCE))
                u.session.flush()
                u.audit.append(actor_id=actor_id, action='reference_create', entity_type='work_area', entity_id=rid,
                    before=None, after=dict(id=str(rid), name=name, source=SOURCE, classification='TEST_PREVIEW'))
            result.append(dict(id=str(rid), name=name, kind='greenhouse'))
        u.commit()
    return result


def setup_synthetic_profile(client, factory, actor_id):
    """Create/replay the explicit TEST fixture through normal scoped APIs."""
    def post(path, payload, key, expected=(200, 201)):
        response = client.post('/v1' + path, json=payload, headers={'Idempotency-Key': SOURCE + ':' + key})
        if response.status_code not in expected:
            raise RuntimeError(f'R3 synthetic setup {key} failed: {response.status_code}: {response.text}')
        return response.json()

    def get(path):
        response = client.get('/v1' + path)
        if response.status_code != 200:
            raise RuntimeError(f'R3 synthetic reference read failed: {response.status_code}')
        return response.json()

    entity = post('/entities', dict(name='TEST R3 Synthetic Farm', currency='TRY', source=SOURCE), 'entity')
    eid = entity['id']
    root = '/entities/' + eid
    bootstrap = post(root + '/reference-bootstrap', dict(currency='TRY'), 'bootstrap')
    ledgers = {r['system_code']: r['id'] for r in bootstrap['references']}
    accounts = [post(root + '/money-accounts', dict(name=name, account_type=kind, currency='TRY', source=SOURCE), 'account-' + kind)
                for name, kind in (('TEST Касса TRY', 'cash'), ('TEST Банк TRY', 'bank'))]
    parties = {role: post(root + '/counterparties', dict(name=name, role=role, source=SOURCE), 'party-' + role)
               for name, role in (('TEST Поставщик А', 'supplier'), ('TEST Клиент А', 'customer'), ('TEST Депо А', 'depot'))}
    areas = _work_areas(factory, UUID(eid), actor_id)
    product = post(root + '/inventory/products', dict(name='TEST KNO3', aliases=['KNO3'], base_unit='kg', currency='TRY', source=SOURCE), 'product')
    location = post(root + '/inventory/locations', dict(name='TEST Склад Север', source=SOURCE), 'warehouse')
    south = post(root + '/inventory/locations', dict(name='TEST Склад Юг', source=SOURCE), 'warehouse-south')
    worker = post(root + '/workers', dict(display_name='TEST Работник А', native_name='TEST کارگر الف', language='fa',
        notes='Синтетический TEST работник. Контакт и реальная отправка отсутствуют.', source=SOURCE), 'worker')
    effects = []
    for direction, role, amount, ledger in (('payable', 'supplier', '1200.00', 'GENERAL_EXPENSE'),
                                            ('receivable', 'customer', '800.00', 'GENERAL_REVENUE')):
        draft = post('/obligations', dict(entity_id=eid, counterparty_id=parties[role]['id'], direction=direction,
            original_amount=amount, currency='TRY', event_date='2026-10-10', due_date='2026-10-20',
            offset_ledger_account_id=ledgers[ledger], description=('TEST синтетическая аренда инструмента' if direction == 'receivable' else 'TEST синтетический payable; не банановая продажа')), 'obligation-' + direction)
        posted = post('/obligations/' + draft['id'] + '/post?expected_version=1', None, 'post-' + direction)
        effects.append(dict(kind=direction, id=posted['id'], amount=amount, currency='TRY', status='posted', synthetic=True))
    funding = post('/owner-fundings', dict(entity_id=eid, money_account_id=accounts[0]['id'], amount='5000.00', currency='TRY',
        event_date='2026-10-10', source=SOURCE, reason='TEST synthetic cash for isolated settlement journeys'), 'funding')
    post('/owner-fundings/' + funding['id'] + '/post?expected_version=1', None, 'post-funding')
    effects.append(dict(kind='owner_funding', id=funding['id'], amount='5000.00', currency='TRY', status='posted', synthetic=True))

    def task(key, original, action_type, reviewed):
        captured = post(root + '/owner/captures', dict(client_capture_id=str(uuid5(NAMESPACE_URL, f'{SOURCE}/{eid}/{key}')),
            captured_at='2026-10-10T09:00:00+03:00', timezone='Europe/Istanbul', mode='text', text=original), key + '-source')
        saved = get(root + '/owner/captures/' + captured['id'])
        if len(saved['proposals']) != 1:
            raise RuntimeError('TEST task requires one prepared proposal')
        path = root + '/owner/confirmations/' + saved['proposals'][0]['id']
        proposal = get(path + '/preview')
        if proposal['action_type'] != action_type:
            raise RuntimeError('TEST task preparation kind mismatch: ' + proposal['action_type'])
        terminal_state = 'CONFIRMED_NONPOSTING' if action_type == 'WORKER_TASK' else 'POSTED'
        if proposal['state'] != terminal_state:
            proposal = post(path + '/revise', dict(expected_version=proposal['version'], payload={**proposal['revision']['payload'], **reviewed},
                reason='TEST synthetic authored fixture, explicitly reviewed for this pilot'), key + '-review')
        confirmed = post(path + '/confirm', dict(expected_version=proposal['version'], preview_digest=proposal['revision']['preview_digest'],
            confirmed=True, reason='TEST synthetic nonposting task fixture'), key + '-confirm')
        if confirmed['state'] != terminal_state:
            raise RuntimeError('TEST fixture task state mismatch')
        return dict(source_id=captured['id'], proposal_id=proposal['id'], task_id=confirmed['effect']['id'])

    personal = []
    for n in range(5):
        original = f'Сегодня проверить давление в TEST Теплица А, TEST задача {n + 1}.'
        personal.append(task('personal-' + str(n), original, 'PERSONAL_TASK', dict(title=f'TEST личная задача {n + 1}',
            original_instruction=original, greenhouse_id=areas[0]['id'], planned_date='2026-10-10' if n < 3 else None,
            note=f'TEST отдельная заметка {n + 1}', category='irrigation',
            relative_time_context='сегодня' if n < 3 else 'без установленной даты')))
    original = 'TEST Работник А, проверьте трубу в TEST Теплица А 2026-10-10.'
    rendered = 'TEST کارگر الف، لوله را در گلخانهٔ آزمایشی الف در تاریخ ۲۰۲۶-۱۰-۱۰ بررسی کنید.'
    assignment = task('worker-draft', original, 'WORKER_TASK', dict(title='TEST Проверить трубу', original_instruction=original,
        greenhouse_id=areas[0]['id'], planned_date='2026-10-10', worker_id=worker['id'], language='fa', rendered_text=rendered))
    assignment.update(worker_id=worker['id'], translation_scope='ONE_AUTHORED_TEST_EXAMPLE', real_dispatch=False)
    return dict(entity=entity, money_accounts=accounts, counterparties=parties, areas=areas, product=product, location=location,
        locations=[location,south], worker_assignment_fixture=assignment, personal_tasks=personal, seeded_effects=effects,
        source=SOURCE, synthetic_only=True, owner_opening_loaded=False, stock_opening='UNINITIALIZED_VERIFY',
        greenhouse_warehouse_mapping=None, physical_area_mapping=None)
