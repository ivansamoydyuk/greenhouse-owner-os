"""API and database boundaries preserve reserved control/subledger authority."""
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyFinanceRepository
from tests.integration.test_postgres_readiness import preview, setup, send

CONTROLS = ('AP_CONTROL', 'AR_CONTROL', 'SUPPLIER_ADVANCE', 'CUSTOMER_ADVANCE')


def fingerprint(preview):
    with preview[0]() as db:
        return {table: db.scalar(text("SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]')) FROM " + table + ' t'))
            for table in ('finance.obligations', 'finance.postings', 'finance.posting_entries',
                'finance.advances', 'finance.payments', 'audit.events', 'audit.idempotency_keys')}


def body(entity, counterparty, direction, offset):
    return dict(entity_id=entity, counterparty_id=counterparty, direction=direction,
        original_amount='900', currency='TRY', event_date='2026-10-06', offset_ledger_account_id=offset)


@pytest.mark.parametrize('code', CONTROLS)
@pytest.mark.parametrize('direction', ('payable', 'receivable'))
def test_authenticated_api_denies_control_offset_and_retry_is_atomic(preview, code, direction):
    c, entity, _, _, counterparty, ids = setup(preview)
    before = fingerprint(preview)
    for key in ('reserved-create', 'reserved-create', 'reserved-create-retry'):
        result = send(c, '/obligations', body(entity, counterparty, direction, ids[code]), key=key, expected=422)
        assert 'subledger control' in str(result)
        assert fingerprint(preview) == before
    result = c.get('/v1/finance/reconciliation', params={'entity_id': entity})
    assert result.status_code == 200 and result.json() == {'ok': True, 'findings': []}


@pytest.mark.parametrize('code', CONTROLS)
@pytest.mark.parametrize('direction', ('payable', 'receivable'))
def test_database_denies_control_offset_when_repository_guard_is_bypassed(preview, monkeypatch, code, direction):
    c, entity, _, _, counterparty, ids = setup(preview)
    before = fingerprint(preview)
    # This bounded misuse probe proves the separate additive DB boundary.
    monkeypatch.setattr(SqlAlchemyFinanceRepository, 'assert_regular_offset', lambda *args: None)
    with pytest.raises(DBAPIError) as exc:
        send(c, '/obligations', body(entity, counterparty, direction, ids[code]))
    assert exc.value.orig.sqlstate == '23514'
    assert 'subledger control' in str(exc.value.orig)
    assert fingerprint(preview) == before


@pytest.mark.parametrize('code', CONTROLS)
@pytest.mark.parametrize('direction', ('payable', 'receivable'))
def test_database_denies_draft_offset_switch_without_touching_original(preview, code, direction):
    c, entity, _, _, counterparty, ids = setup(preview)
    normal = ids['GENERAL_EXPENSE' if direction == 'payable' else 'GENERAL_REVENUE']
    draft = send(c, '/obligations', body(entity, counterparty, direction, normal))
    before = fingerprint(preview)
    with pytest.raises(DBAPIError) as exc:
        with preview[0].begin() as db:
            db.execute(text('UPDATE finance.obligations SET offset_ledger_account_id=:offset WHERE id=:id'),
                {'offset': UUID(ids[code]), 'id': UUID(draft['id'])})
    assert exc.value.orig.sqlstate == '23514'
    assert 'subledger control' in str(exc.value.orig)
    assert fingerprint(preview) == before
    posted = send(c, f'/obligations/{draft["id"]}/post?expected_version=1', expected=200)
    assert posted['status'] == 'posted'
    assert c.get('/v1/finance/reconciliation', params={'entity_id': entity}).json() == {'ok': True, 'findings': []}
