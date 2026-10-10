"""AUD-001/002: retained source and owner-approved physical-effect boundary."""
from copy import deepcopy
from decimal import Decimal
import json
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from tests.integration.test_postgres_inventory import inv, event, line, post, qty
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_owner_capture import source, queue, revise, confirm, confirm_body


INVOICE = 'Получил счёт поставщика на уже принятые 100 кг Atocrop на склад Имамлы. Нового прихода не было.'
RATE = 'Внесли 0,5 кг Atocrop на каждый донюм, всего площадь 10 донюм Имамлы'
TABLES = ('inventory.events', 'inventory.movements', 'core.confirmation_effects',
          'core.invoice_receipt_links', 'finance.postings')


@pytest.fixture
def semantic_inventory(inv):
    """Only synthetic, verified references and a trusted historical receipt."""
    i = dict(inv)
    i['p'] = send(i['c'], i['root'] + '/products', dict(
        name='Atocrop', base_unit='kg', currency='TRY', source='test:aud001-002'))
    i['l'] = send(i['c'], i['root'] + '/locations', dict(
        name='Склад Имамлы', source='test:aud001-002'))
    receipt = post(i, event(i, lines=[line(i, '100')], evidence=dict(
        source='test:aud001-002:historical-receipt', raw_text='TEST: ранее принятые 100 кг Atocrop')))
    assert qty(i) == Decimal('100')
    return i, receipt


def counts(i):
    with i['preview'][0]() as db:
        return {table: db.scalar(text('SELECT count(*) FROM ' + table + ' WHERE entity_id=:e'),
                                 dict(e=i['e'])) for table in TABLES}


def actual_preview(i, p):
    r = i['c'].get(f"/v1/entities/{i['e']}/owner/confirmations/{p['id']}/preview")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view['revision'] == p['revision']
    assert view['version'] == p['version']
    return view


def assert_retained(i, saved, original, initial, reviewed, result):
    sf, actor, *_ = i['preview']
    svc = OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf))
    assert svc.original(UUID(i['e']), actor, UUID(saved['id']))[0] == original.encode('utf-8')
    with sf() as db:
        history = db.execute(text('SELECT version,payload,provenance,preview_digest,reason '
                                  'FROM core.proposal_revisions WHERE proposal_id=:p ORDER BY version'),
                             dict(p=initial['id'])).mappings().all()
        assert len(history) == 2
        assert dict(history[0]) == {k: initial['revision'][k] for k in history[0]}
        assert dict(history[1]) == {k: reviewed['revision'][k] for k in history[1]}
        actions = db.execute(text('SELECT action,version,actor_id,snapshot FROM core.proposal_actions '
                                  'WHERE proposal_id=:p ORDER BY version,created_at'),
                             dict(p=initial['id'])).mappings().all()
        assert [(a['action'], a['version']) for a in actions] == [
            ('PREPARE', initial['version']), ('EDIT', reviewed['version']), ('CONFIRM', reviewed['version'])]
        approval = actions[-1]
        assert str(approval['actor_id']) == str(actor)
        assert approval['snapshot']['payload'] == reviewed['revision']['payload']
        assert approval['snapshot']['preview_digest'] == reviewed['revision']['preview_digest']
        effect = db.execute(text('SELECT version,actor_id,preview_digest,response FROM core.confirmation_effects '
                                 'WHERE proposal_id=:p'), dict(p=initial['id'])).mappings().one()
        assert effect['version'] == reviewed['version']
        assert str(effect['actor_id']) == str(actor)
        assert effect['preview_digest'] == reviewed['revision']['preview_digest']
        assert effect['response'] == result
        assert db.scalar(text('SELECT raw_text FROM core.inbox_items WHERE id=:s'), dict(s=saved['id'])) == original
    # Database enforcement, rather than a read-only equality assertion alone.
    for statement, values in [
        ('UPDATE core.inbox_items SET raw_text=:changed WHERE id=:s', dict(s=saved['id'], changed='TEST forbidden rewrite')),
        ("UPDATE core.proposal_revisions SET payload='{}'::jsonb WHERE proposal_id=:p", dict(p=initial['id'])),
        ("UPDATE core.proposal_actions SET snapshot='{}'::jsonb WHERE proposal_id=:p", dict(p=initial['id'])),
    ]:
        with pytest.raises(DBAPIError):
            with sf.begin() as db:
                db.execute(text(statement), values)
    assert svc.original(UUID(i['e']), actor, UUID(saved['id']))[0] == original.encode('utf-8')
    print(json.dumps(dict(source=saved, first_revision=initial, reviewed_preview=reviewed,
                         confirmation=result, row_counts=counts(i)), ensure_ascii=False, default=str))


@pytest.mark.parametrize('mode', ['text', 'voice_transcript'])
def test_late_invoice_links_old_receipt_without_new_physical_stock(semantic_inventory, mode):
    i, receipt = semantic_inventory
    before = counts(i)
    saved = source(i, INVOICE, mode=mode)
    proposals = [p for p in queue(i) if p['inbox_id'] == saved['id']]
    print(json.dumps(dict(phase='prepared', mode=mode, source=saved, proposals=proposals,
                          row_counts=counts(i)), ensure_ascii=False, default=str))
    assert not any(p['action_type'] in ('STOCK_RECEIPT', 'STOCK_APPLICATION') for p in proposals)
    matches = [p for p in proposals if p['action_type'] == 'INVOICE_MATCH']
    assert len(matches) == 1
    initial = matches[0]
    assert initial['state'] == 'NEEDS_INPUT'
    assert initial['revision']['payload']['receipt_id'] is None
    evidence = initial['revision']['provenance']['note']['nonposting_inventory_evidence']
    assert any(f['product_id'] == i['p']['id'] and f['quantity'] == '100' and
               f['unit'] == 'kg' and f['location_id'] == i['l']['id'] for f in evidence)
    actual_preview(i, initial)
    assert counts(i) == before and qty(i) == Decimal('100')
    confirm(i, initial, expected=422)
    assert counts(i) == before
    reviewed = revise(i, initial, dict(initial['revision']['payload'], receipt_id=receipt['id']))
    assert reviewed['state'] == 'READY'
    assert reviewed['revision']['provenance']['receipt_id']['category'] == 'OWNER INPUT'
    assert reviewed['revision']['preview_digest'] != initial['revision']['preview_digest']
    actual_preview(i, reviewed)
    confirm(i, initial, expected=409)
    assert counts(i) == before
    result = confirm(i, reviewed, key='aud001-reviewed-confirm')
    assert result['state'] == 'CONFIRMED_NONPOSTING'
    assert result['effect']['receipt_id'] == receipt['id']
    assert result['effect']['valuation_state'] == 'UNKNOWN_UNCHANGED'
    assert confirm(i, reviewed, key='aud001-reviewed-confirm') == result
    assert confirm(i, reviewed, key='aud001-response-loss-replay') == result
    after = counts(i)
    assert after == dict(before, **{'core.confirmation_effects': before['core.confirmation_effects'] + 1,
                                    'core.invoice_receipt_links': before['core.invoice_receipt_links'] + 1})
    assert qty(i) == Decimal('100')
    assert_retained(i, saved, INVOICE, initial, reviewed, result)


@pytest.mark.parametrize('mode', ['text', 'voice_transcript'])
def test_rate_needs_owner_total_and_consumes_exactly_reviewed_five(semantic_inventory, mode):
    i, _ = semantic_inventory
    before = counts(i)
    saved = source(i, RATE, mode=mode)
    proposals = [p for p in queue(i) if p['inbox_id'] == saved['id']]
    print(json.dumps(dict(phase='prepared', mode=mode, source=saved, proposals=proposals,
                          row_counts=counts(i)), ensure_ascii=False, default=str))
    assert len(proposals) == 1 and proposals[0]['action_type'] == 'STOCK_APPLICATION'
    initial = proposals[0]
    assert initial['state'] == 'NEEDS_INPUT'
    fields = initial['revision']['payload']
    assert len(fields['lines']) == 1
    original_line = fields['lines'][0]
    assert original_line['quantity'] is None
    assert original_line['product_id'] == i['p']['id'] and original_line['unit'] == 'kg'
    assert '0,5' in original_line['original_quantity'] and 'донюм' in original_line['original_quantity']
    evidence = initial['revision']['provenance']['lines']['quantity_evidence']
    rates = [item for item in evidence if item['role'] == 'RATE']
    assert len(rates) == 1 and rates[0]['quantity'] == '0.5'
    assert rates[0]['area']['quantity'] == '10'
    assert any('общее количество' in issue and 'доза' in issue for issue in initial['revision']['missing'])
    actual_preview(i, initial)
    assert counts(i) == before and qty(i) == Decimal('100')
    confirm(i, initial, expected=422)
    assert counts(i) == before
    corrected = deepcopy(fields)
    corrected['lines'][0].update(quantity='5', location_id=i['l']['id'], greenhouse_id=i['g'])
    reviewed = revise(i, initial, corrected)
    assert reviewed['state'] == 'READY' and reviewed['revision']['missing'] == []
    assert reviewed['revision']['provenance']['lines']['category'] == 'OWNER INPUT'
    assert reviewed['revision']['payload']['lines'][0]['original_quantity'] == original_line['original_quantity']
    assert reviewed['revision']['preview_digest'] != initial['revision']['preview_digest']
    actual_preview(i, reviewed)
    confirm(i, initial, expected=409)
    send(i['c'], f"/entities/{i['e']}/owner/confirmations/{reviewed['id']}/confirm",
         dict(confirm_body(reviewed), preview_digest=initial['revision']['preview_digest']), expected=409)
    assert counts(i) == before and qty(i) == Decimal('100')
    result = confirm(i, reviewed, key='aud002-reviewed-five')
    assert result['state'] == 'POSTED' and result['effect']['kind'] == 'application'
    assert result['effect']['payload']['lines'][0]['quantity'] == '5'
    assert confirm(i, reviewed, key='aud002-reviewed-five') == result
    assert confirm(i, reviewed, key='aud002-response-loss-replay') == result
    assert qty(i) == Decimal('95')
    after = counts(i)
    assert after == dict(before, **{'inventory.events': before['inventory.events'] + 1,
                                    'inventory.movements': before['inventory.movements'] + 1,
                                    'core.confirmation_effects': before['core.confirmation_effects'] + 1})
    with i['preview'][0]() as db:
        movements = db.execute(text('SELECT quantity,role FROM inventory.movements WHERE event_id=:id'),
                               dict(id=result['effect']['id'])).all()
        assert movements == [(Decimal('-5'), 'application')]
    assert_retained(i, saved, RATE, initial, reviewed, result)
