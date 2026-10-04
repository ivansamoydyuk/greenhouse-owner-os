from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from greenhouse_owner_os.domain.commands import AllocationInput, CreateObligationCommand, CreatePaymentCommand
from greenhouse_owner_os.domain.errors import IdempotencyConflictError, ValidationError
from greenhouse_owner_os.domain.finance import ObligationDirection, PaymentDirection, RecordStatus


def create_posted_obligation(fx, amount="10000.00", counterparty_id=None, currency="TRY"):
    cp = counterparty_id or fx.counterparty_id
    created = fx.service.create_obligation(
        actor_id=fx.actor_id,
        idempotency_key=f"create-ob-{uuid4()}",
        command=CreateObligationCommand(
            entity_id=fx.entity_id,
            counterparty_id=cp,
            direction=ObligationDirection.PAYABLE,
            original_amount=Decimal(amount),
            currency=currency,
            event_date=date(2026, 10, 4),
            due_date=date(2026, 10, 10),
            offset_ledger_account_id=fx.offset_account_id,
        ),
    )
    return fx.service.post_obligation(
        actor_id=fx.actor_id,
        idempotency_key=f"post-ob-{uuid4()}",
        obligation_id=UUID(created["id"]),
        expected_version=created["version"],
    )


def create_post_payment(fx, allocations, amount, counterparty_id=None, key_suffix=None):
    cp = counterparty_id or fx.counterparty_id
    created = fx.service.create_payment(
        actor_id=fx.actor_id,
        idempotency_key=f"create-pay-{key_suffix or uuid4()}",
        command=CreatePaymentCommand(
            entity_id=fx.entity_id,
            money_account_id=fx.money_account_id,
            counterparty_id=cp,
            direction=PaymentDirection.OUT,
            amount=Decimal(amount),
            currency="TRY",
            event_date=date(2026, 10, 4),
            allocations=tuple(AllocationInput(obligation_id=UUID(ob_id), amount=Decimal(value)) for ob_id, value in allocations),
        ),
    )
    return fx.service.post_payment(
        actor_id=fx.actor_id,
        idempotency_key=f"post-pay-{key_suffix or uuid4()}",
        payment_id=UUID(created["id"]),
        expected_version=created["version"],
    )


def test_fin_001_posted_obligation_has_full_remaining_and_no_cash_effect(fx):
    ob = create_posted_obligation(fx, "10000.00")
    assert ob["remaining_amount"] == "10000.00"
    assert len(fx.store.postings) == 1
    posting = next(iter(fx.store.postings.values()))
    posting.validate_balanced()
    assert sum(1 for _ in fx.store.payments) == 0


def test_fin_014_draft_has_no_financial_effect(fx):
    draft = fx.service.create_obligation(
        actor_id=fx.actor_id,
        idempotency_key="draft-no-effect",
        command=CreateObligationCommand(
            entity_id=fx.entity_id,
            counterparty_id=fx.counterparty_id,
            direction=ObligationDirection.PAYABLE,
            original_amount=Decimal("10000"),
            currency="TRY",
            event_date=date(2026, 10, 4),
            offset_ledger_account_id=fx.offset_account_id,
        ),
    )
    assert draft["status"] == "draft"
    assert len(fx.store.postings) == 0


def test_fin_002_partial_payment_remaining_6000(fx):
    ob = create_posted_obligation(fx, "10000.00")
    pay = create_post_payment(fx, [(ob["id"], "4000.00")], "4000.00")
    assert pay["obligation_balances"][ob["id"]] == "6000.00"
    assert fx.service.get_obligation(UUID(ob["id"]))["remaining_amount"] == "6000.00"


def test_fin_003_multiple_payments_settle_obligation(fx):
    ob = create_posted_obligation(fx, "10000.00")
    create_post_payment(fx, [(ob["id"], "4000.00")], "4000.00")
    create_post_payment(fx, [(ob["id"], "6000.00")], "6000.00")
    assert fx.service.get_obligation(UUID(ob["id"]))["remaining_amount"] == "0.00"


def test_fin_004_one_payment_covers_multiple_obligations(fx):
    a = create_posted_obligation(fx, "3000.00")
    b = create_posted_obligation(fx, "7000.00")
    pay = create_post_payment(fx, [(a["id"], "3000.00"), (b["id"], "7000.00")], "10000.00")
    assert pay["unallocated_amount"] == "0.00"
    assert pay["obligation_balances"][a["id"]] == "0.00"
    assert pay["obligation_balances"][b["id"]] == "0.00"


def test_fin_005_overpayment_creates_explicit_advance(fx):
    ob = create_posted_obligation(fx, "10000.00")
    pay = create_post_payment(fx, [(ob["id"], "10000.00")], "12000.00")
    assert pay["obligation_balances"][ob["id"]] == "0.00"
    assert pay["unallocated_amount"] == "2000.00"
    assert pay["advance_id"] is not None
    advance = fx.store.advances[UUID(pay["advance_id"])]
    assert advance.original_amount == Decimal("2000.00")


def test_fin_006_allocation_cannot_exceed_payment_and_is_atomic(fx):
    ob = create_posted_obligation(fx, "10000.00")
    before = (len(fx.store.payments), len(fx.store.postings), len(fx.store.advances))
    with pytest.raises(ValidationError):
        fx.service.create_payment(
            actor_id=fx.actor_id,
            idempotency_key="too-much-allocation",
            command=CreatePaymentCommand(
                entity_id=fx.entity_id,
                money_account_id=fx.money_account_id,
                counterparty_id=fx.counterparty_id,
                direction=PaymentDirection.OUT,
                amount=Decimal("5000.00"),
                currency="TRY",
                event_date=date(2026,10,4),
                allocations=(AllocationInput(obligation_id=UUID(ob["id"]), amount=Decimal("5000.01")),),
            ),
        )
    assert (len(fx.store.payments), len(fx.store.postings), len(fx.store.advances)) == before


def test_fin_007_allocation_cannot_exceed_obligation_remaining_and_rolls_back(fx):
    ob = create_posted_obligation(fx, "2000.00")
    created = fx.service.create_payment(
        actor_id=fx.actor_id,
        idempotency_key="draft-over-obligation",
        command=CreatePaymentCommand(
            entity_id=fx.entity_id,
            money_account_id=fx.money_account_id,
            counterparty_id=fx.counterparty_id,
            direction=PaymentDirection.OUT,
            amount=Decimal("2500.00"),
            currency="TRY",
            event_date=date(2026,10,4),
            allocations=(AllocationInput(obligation_id=UUID(ob["id"]), amount=Decimal("2500.00")),),
        ),
    )
    posting_count = len(fx.store.postings)
    with pytest.raises(ValidationError):
        fx.service.post_payment(
            actor_id=fx.actor_id,
            idempotency_key="post-over-obligation",
            payment_id=UUID(created["id"]),
            expected_version=created["version"],
        )
    assert len(fx.store.postings) == posting_count
    assert fx.store.payments[UUID(created["id"])].status == RecordStatus.DRAFT
    assert fx.service.get_obligation(UUID(ob["id"]))["remaining_amount"] == "2000.00"


def test_payment_counterparty_must_match_obligation(fx):
    ob = create_posted_obligation(fx, "1000.00")
    created = fx.service.create_payment(
        actor_id=fx.actor_id,
        idempotency_key="create-wrong-cp",
        command=CreatePaymentCommand(
            entity_id=fx.entity_id,
            money_account_id=fx.money_account_id,
            counterparty_id=fx.other_counterparty_id,
            direction=PaymentDirection.OUT,
            amount=Decimal("1000.00"),
            currency="TRY",
            event_date=date(2026,10,4),
            allocations=(AllocationInput(obligation_id=UUID(ob["id"]), amount=Decimal("1000.00")),),
        ),
    )
    with pytest.raises(ValidationError, match="counterparty mismatch"):
        fx.service.post_payment(
            actor_id=fx.actor_id,
            idempotency_key="post-wrong-cp",
            payment_id=UUID(created["id"]),
            expected_version=created["version"],
        )


def test_sys_001_idempotent_retry_same_request_returns_same_resource(fx):
    command = CreateObligationCommand(
        entity_id=fx.entity_id,
        counterparty_id=fx.counterparty_id,
        direction=ObligationDirection.PAYABLE,
        original_amount=Decimal("10000"),
        currency="TRY",
        event_date=date(2026,10,4),
        offset_ledger_account_id=fx.offset_account_id,
    )
    first = fx.service.create_obligation(actor_id=fx.actor_id, idempotency_key="same-key", command=command)
    second = fx.service.create_obligation(actor_id=fx.actor_id, idempotency_key="same-key", command=command)
    assert first == second
    assert len(fx.store.obligations) == 1


def test_idempotency_same_key_different_request_conflicts(fx):
    base = dict(
        entity_id=fx.entity_id,
        counterparty_id=fx.counterparty_id,
        direction=ObligationDirection.PAYABLE,
        currency="TRY",
        event_date=date(2026,10,4),
        offset_ledger_account_id=fx.offset_account_id,
    )
    fx.service.create_obligation(actor_id=fx.actor_id, idempotency_key="collision", command=CreateObligationCommand(original_amount=Decimal("100"), **base))
    with pytest.raises(IdempotencyConflictError):
        fx.service.create_obligation(actor_id=fx.actor_id, idempotency_key="collision", command=CreateObligationCommand(original_amount=Decimal("101"), **base))


def test_posting_is_balanced_for_partial_payment(fx):
    ob = create_posted_obligation(fx, "10000.00")
    create_post_payment(fx, [(ob["id"], "4000.00")], "4000.00")
    for posting in fx.store.postings.values():
        posting.validate_balanced()
