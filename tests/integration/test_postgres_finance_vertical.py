from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from greenhouse_owner_os.domain.commands import AllocationInput, CreateObligationCommand, CreatePaymentCommand
from greenhouse_owner_os.domain.errors import ValidationError
from greenhouse_owner_os.domain.finance import ObligationDirection, PaymentDirection
from greenhouse_owner_os.infrastructure.models import AdvanceRow, PaymentAllocationRow, PostingEntryRow, PostingRow


def create_posted_obligation(pgfx, amount: str = "10000.00") -> dict:
    created = pgfx.service.create_obligation(
        actor_id=pgfx.actor_id,
        idempotency_key=f"create-ob-{uuid4()}",
        command=CreateObligationCommand(
            entity_id=pgfx.entity_id,
            counterparty_id=pgfx.counterparty_id,
            direction=ObligationDirection.PAYABLE,
            original_amount=Decimal(amount),
            currency="TRY",
            event_date=date(2026, 10, 4),
            due_date=date(2026, 10, 10),
            offset_ledger_account_id=pgfx.offset_account_id,
        ),
    )
    return pgfx.service.post_obligation(
        actor_id=pgfx.actor_id,
        idempotency_key=f"post-ob-{uuid4()}",
        obligation_id=UUID(created["id"]),
        expected_version=created["version"],
    )


def create_payment_draft(pgfx, obligation_id: str, amount: str) -> dict:
    return pgfx.service.create_payment(
        actor_id=pgfx.actor_id,
        idempotency_key=f"create-pay-{uuid4()}",
        command=CreatePaymentCommand(
            entity_id=pgfx.entity_id,
            money_account_id=pgfx.money_account_id,
            counterparty_id=pgfx.counterparty_id,
            direction=PaymentDirection.OUT,
            amount=Decimal(amount),
            currency="TRY",
            event_date=date(2026, 10, 4),
            allocations=(AllocationInput(obligation_id=UUID(obligation_id), amount=Decimal(amount)),),
        ),
    )


def test_postgresql_migrations_and_partial_payment_flow(pgfx):
    obligation = create_posted_obligation(pgfx, "10000.00")
    payment = create_payment_draft(pgfx, obligation["id"], "4000.00")
    posted = pgfx.service.post_payment(
        actor_id=pgfx.actor_id,
        idempotency_key="pg-post-partial-payment",
        payment_id=UUID(payment["id"]),
        expected_version=payment["version"],
    )
    assert posted["obligation_balances"][obligation["id"]] == "6000.00"
    assert pgfx.service.get_obligation(UUID(obligation["id"]))["remaining_amount"] == "6000.00"


def test_database_rejects_unbalanced_posting_at_commit(pgfx):
    with pgfx.session_factory() as session:
        posting = PostingRow(
            entity_id=pgfx.entity_id,
            currency="TRY",
            event_date=date(2026, 10, 4),
            posting_type="invalid_test",
            source_type="test",
            source_id=uuid4(),
            created_by=pgfx.actor_id,
        )
        session.add(posting)
        session.flush()
        session.add(PostingEntryRow(posting_id=posting.id, ledger_account_id=pgfx.offset_account_id, side="debit", amount=Decimal("1.00")))
        with pytest.raises(DBAPIError):
            session.commit()
        session.rollback()


def test_postgresql_overpayment_creates_explicit_advance(pgfx):
    obligation = create_posted_obligation(pgfx, "10000.00")
    payment = pgfx.service.create_payment(
        actor_id=pgfx.actor_id,
        idempotency_key=f"create-overpayment-{uuid4()}",
        command=CreatePaymentCommand(
            entity_id=pgfx.entity_id,
            money_account_id=pgfx.money_account_id,
            counterparty_id=pgfx.counterparty_id,
            direction=PaymentDirection.OUT,
            amount=Decimal("12000.00"),
            currency="TRY",
            event_date=date(2026, 10, 4),
            allocations=(AllocationInput(obligation_id=UUID(obligation["id"]), amount=Decimal("10000.00")),),
        ),
    )
    posted = pgfx.service.post_payment(
        actor_id=pgfx.actor_id,
        idempotency_key=f"post-overpayment-{uuid4()}",
        payment_id=UUID(payment["id"]),
        expected_version=payment["version"],
    )
    assert posted["obligation_balances"][obligation["id"]] == "0.00"
    assert posted["unallocated_amount"] == "2000.00"
    assert posted["advance_id"] is not None
    assert pgfx.service.get_obligation(UUID(obligation["id"]))["remaining_amount"] == "0.00"
    with pgfx.session_factory() as session:
        advances = session.scalars(select(AdvanceRow).where(AdvanceRow.source_payment_id == UUID(payment["id"]))).all()
        assert len(advances) == 1
        assert str(advances[0].id) == posted["advance_id"]
        assert advances[0].original_amount == Decimal("2000.00")
        assert advances[0].currency == "TRY"
        assert advances[0].counterparty_id == pgfx.counterparty_id
        entries = session.scalars(select(PostingEntryRow).where(PostingEntryRow.posting_id == UUID(posted["posting_id"]))).all()
        assert len(entries) == 3
        assert sum(row.amount for row in entries if row.side == "debit") == Decimal("12000.00")
        assert sum(row.amount for row in entries if row.side == "credit") == Decimal("12000.00")


def test_concurrent_allocations_cannot_make_obligation_negative(pgfx):
    obligation = create_posted_obligation(pgfx, "5000.00")
    p1 = create_payment_draft(pgfx, obligation["id"], "4000.00")
    p2 = create_payment_draft(pgfx, obligation["id"], "4000.00")

    def post(payment: dict, key: str):
        try:
            return ("ok", pgfx.service.post_payment(
                actor_id=pgfx.actor_id,
                idempotency_key=key,
                payment_id=UUID(payment["id"]),
                expected_version=payment["version"],
            ))
        except ValidationError as exc:
            return ("validation", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: post(*args), [(p1, "race-post-1"), (p2, "race-post-2")]))

    assert sorted(item[0] for item in results) == ["ok", "validation"]
    assert pgfx.service.get_obligation(UUID(obligation["id"]))["remaining_amount"] == "1000.00"


def test_concurrent_same_idempotency_key_creates_one_obligation(pgfx):
    command = CreateObligationCommand(
        entity_id=pgfx.entity_id,
        counterparty_id=pgfx.counterparty_id,
        direction=ObligationDirection.PAYABLE,
        original_amount=Decimal("1234.00"),
        currency="TRY",
        event_date=date(2026, 10, 4),
        due_date=None,
        offset_ledger_account_id=pgfx.offset_account_id,
    )

    def create():
        return pgfx.service.create_obligation(
            actor_id=pgfx.actor_id,
            idempotency_key="same-concurrent-create",
            command=command,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: create(), range(2)))
    assert a == b
    assert a["id"] == b["id"]


def test_database_rejects_allocation_insert_after_payment_posted(pgfx):
    first = create_posted_obligation(pgfx, "1000.00")
    second = create_posted_obligation(pgfx, "1000.00")
    payment = create_payment_draft(pgfx, first["id"], "1000.00")
    posted = pgfx.service.post_payment(
        actor_id=pgfx.actor_id,
        idempotency_key=f"post-immutable-{uuid4()}",
        payment_id=UUID(payment["id"]),
        expected_version=payment["version"],
    )

    with pgfx.session_factory() as session:
        session.add(PaymentAllocationRow(
            payment_id=UUID(posted["id"]),
            obligation_id=UUID(second["id"]),
            amount=Decimal("1.00"),
        ))
        with pytest.raises(DBAPIError):
            session.commit()
        session.rollback()
