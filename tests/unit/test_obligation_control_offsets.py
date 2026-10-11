"""Ordinary obligations cannot invent balances in another authoritative subledger."""
import copy
from uuid import UUID

import pytest

from greenhouse_owner_os.domain.commands import CreateObligationCommand
from greenhouse_owner_os.domain.errors import ValidationError
from greenhouse_owner_os.domain.finance import RecordStatus

CONTROLS = ('AP_CONTROL', 'AR_CONTROL', 'SUPPLIER_ADVANCE', 'CUSTOMER_ADVANCE')


def command(fx, direction, offset):
    return CreateObligationCommand(entity_id=fx.entity_id, counterparty_id=fx.counterparty_id,
        direction=direction, original_amount='900', currency='TRY', event_date='2026-10-06',
        offset_ledger_account_id=offset)


@pytest.mark.parametrize('code', CONTROLS)
@pytest.mark.parametrize('direction', ('payable', 'receivable'))
def test_ordinary_create_rejects_reserved_control_without_effects(fx, code, direction):
    offset = fx.store.system_accounts[fx.entity_id, 'TRY', code]
    before = copy.deepcopy(fx.store.__dict__)
    for key in ('rejected', 'rejected', 'another-rejected'):
        with pytest.raises(ValidationError, match='subledger control'):
            fx.service.create_obligation(actor_id=fx.actor_id, idempotency_key=key,
                command=command(fx, direction, offset))
        assert fx.store.__dict__ == before


@pytest.mark.parametrize('code', CONTROLS)
@pytest.mark.parametrize('direction', ('payable', 'receivable'))
@pytest.mark.parametrize('status', (RecordStatus.DRAFT, RecordStatus.REVIEW))
def test_old_reserved_control_draft_cannot_post_or_leave_partial_effects(fx, code, direction, status):
    draft = fx.service.create_obligation(actor_id=fx.actor_id, idempotency_key='legacy-create',
        command=command(fx, direction, fx.offset_account_id))
    # Memory fixture represents a persisted draft from the pre-guard version.
    fx.store.obligations[UUID(draft['id'])].offset_ledger_account_id = fx.store.system_accounts[fx.entity_id, 'TRY', code]
    fx.store.obligations[UUID(draft['id'])].status = status
    before = copy.deepcopy(fx.store.__dict__)
    for key in ('rejected-post', 'rejected-post', 'another-post'):
        with pytest.raises(ValidationError, match='subledger control'):
            fx.service.post_obligation(actor_id=fx.actor_id, idempotency_key=key,
                obligation_id=UUID(draft['id']), expected_version=1)
        assert fx.store.__dict__ == before
