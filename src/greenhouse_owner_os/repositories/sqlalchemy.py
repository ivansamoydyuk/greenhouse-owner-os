from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dataclasses import asdict, fields
from greenhouse_owner_os.domain.cash import CASH_TYPES
from greenhouse_owner_os.infrastructure.models import TransferRow, FundingRow, SettlementRow, CounterpartyRow, ApplicationRow

from greenhouse_owner_os.domain.errors import IdempotencyConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.domain.finance import (
    Advance,
    AdvanceDirection,
    FinancialPosting,
    Obligation,
    ObligationDirection,
    Payment,
    PaymentAllocation,
    PaymentDirection,
    PostingSide,
    PostingLine,
    RecordStatus,
    money,
)
from greenhouse_owner_os.infrastructure.models import (
    AdvanceRow,
    AuditEventRow,
    IdempotencyRow,
    LedgerAccountRow,
    MoneyAccountRow,
    ObligationRow,
    PaymentAllocationRow,
    PaymentRow,
    PostingEntryRow,
    PostingRow,
)


from .history import SqlHistoryRepositoryMixin
from .opening import SqlOpeningRepositoryMixin


class SqlAlchemyFinanceRepository(SqlHistoryRepositoryMixin, SqlOpeningRepositoryMixin):
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_obligation(self, obligation: Obligation, actor_id: UUID) -> None:
        self.session.add(ObligationRow(
            id=obligation.id,
            entity_id=obligation.entity_id,
            counterparty_id=obligation.counterparty_id,
            direction=obligation.direction.value,
            original_amount=obligation.original_amount,
            currency=obligation.currency,
            event_date=obligation.event_date,
            due_date=obligation.due_date,
            offset_ledger_account_id=obligation.offset_ledger_account_id,
            status=obligation.status.value,
            version=obligation.version,
            created_by=actor_id,
            replacement_of_id=obligation.replacement_of_id,
        ))

    def _obligation_from_row(self, row: ObligationRow) -> Obligation:
        return Obligation(
            id=row.id,
            entity_id=row.entity_id,
            counterparty_id=row.counterparty_id,
            direction=ObligationDirection(row.direction),
            original_amount=money(row.original_amount),
            currency=row.currency,
            event_date=row.event_date,
            due_date=row.due_date,
            offset_ledger_account_id=row.offset_ledger_account_id,
            status=RecordStatus(row.status),
            version=row.version,
            posting_id=row.posting_id,
            posted_at=row.posted_at,
            reversal_posting_id=row.reversal_posting_id,
            reversed_at=row.reversed_at,
            reversed_by=row.reversed_by,
            reversal_reason=row.reversal_reason,
            replacement_of_id=row.replacement_of_id,
        )

    def get_obligation(self, obligation_id: UUID, *, for_update: bool = False) -> Obligation | None:
        stmt = select(ObligationRow).where(ObligationRow.id == obligation_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt.execution_options(populate_existing=True))
        return self._obligation_from_row(row) if row else None

    def save_obligation(self, obligation: Obligation) -> None:
        row = self.session.get(ObligationRow, obligation.id)
        if not row:
            raise NotFoundError("obligation not found")
        row.status = obligation.status.value
        row.posting_id = obligation.posting_id
        row.posted_at = obligation.posted_at
        row.version = obligation.version
        row.reversal_posting_id = obligation.reversal_posting_id
        row.reversed_at = obligation.reversed_at
        row.reversed_by = obligation.reversed_by
        row.reversal_reason = obligation.reversal_reason

    def obligation_remaining(self, obligation_id: UUID) -> Decimal:
        obligation = self.session.get(ObligationRow, obligation_id)
        if not obligation:
            raise NotFoundError("obligation not found")
        if obligation.status == "reversed":
            return Decimal("0.00")
        allocated_stmt = (
            select(func.coalesce(func.sum(PaymentAllocationRow.amount), 0))
            .join(PaymentRow, PaymentRow.id == PaymentAllocationRow.payment_id)
            .where(
                PaymentAllocationRow.obligation_id == obligation_id,
                PaymentRow.status == "posted",
            )
        )
        allocated = Decimal(self.session.scalar(allocated_stmt) or 0)
        settled = self.session.scalar(select(func.coalesce(func.sum(SettlementRow.amount), 0)).where(
            (SettlementRow.receivable_id == obligation_id) | (SettlementRow.payable_id == obligation_id), SettlementRow.status == 'posted'))
        return money(Decimal(obligation.original_amount) - allocated - Decimal(settled or 0) - self.application_total(obligation_id=obligation_id))

    def add_payment(self, payment: Payment, actor_id: UUID) -> None:
        self.session.add(PaymentRow(
            id=payment.id,
            entity_id=payment.entity_id,
            money_account_id=payment.money_account_id,
            counterparty_id=payment.counterparty_id,
            direction=payment.direction.value,
            source=payment.source,
            reason=payment.reason,
            amount=payment.amount,
            currency=payment.currency,
            event_date=payment.event_date,
            status=payment.status.value,
            version=payment.version,
            created_by=actor_id,
            replacement_of_id=payment.replacement_of_id,
        ))
        # No ORM relationship orders these inserts; persist the FK parent first.
        self.session.flush()
        for allocation in payment.allocations:
            self.session.add(PaymentAllocationRow(
                id=allocation.id,
                payment_id=payment.id,
                obligation_id=allocation.obligation_id,
                amount=allocation.amount,
            ))

    def _payment_from_row(self, row: PaymentRow) -> Payment:
        allocation_rows = self.session.scalars(
            select(PaymentAllocationRow).where(PaymentAllocationRow.payment_id == row.id).order_by(PaymentAllocationRow.id)
        ).all()
        return Payment(
            id=row.id,
            entity_id=row.entity_id,
            money_account_id=row.money_account_id,
            counterparty_id=row.counterparty_id,
            direction=PaymentDirection(row.direction),
            source=row.source,
            reason=row.reason,
            amount=money(row.amount),
            currency=row.currency,
            event_date=row.event_date,
            status=RecordStatus(row.status),
            version=row.version,
            posting_id=row.posting_id,
            posted_at=row.posted_at,
            reversal_posting_id=row.reversal_posting_id,
            reversed_at=row.reversed_at,
            reversed_by=row.reversed_by,
            reversal_reason=row.reversal_reason,
            replacement_of_id=row.replacement_of_id,
            allocations=[PaymentAllocation(id=a.id, payment_id=a.payment_id, obligation_id=a.obligation_id, amount=money(a.amount)) for a in allocation_rows],
        )

    def get_payment(self, payment_id: UUID, *, for_update: bool = False) -> Payment | None:
        stmt = select(PaymentRow).where(PaymentRow.id == payment_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt.execution_options(populate_existing=True))
        return self._payment_from_row(row) if row else None

    def save_payment(self, payment: Payment) -> None:
        row = self.session.get(PaymentRow, payment.id)
        if not row:
            raise NotFoundError("payment not found")
        row.status = payment.status.value
        row.posting_id = payment.posting_id
        row.posted_at = payment.posted_at
        row.version = payment.version
        row.reversal_posting_id = payment.reversal_posting_id
        row.reversed_at = payment.reversed_at
        row.reversed_by = payment.reversed_by
        row.reversal_reason = payment.reversal_reason

    def add_advance(self, advance: Advance) -> None:
        self.session.add(AdvanceRow(
            id=advance.id,
            entity_id=advance.entity_id,
            counterparty_id=advance.counterparty_id,
            source_payment_id=advance.source_payment_id,
            direction=advance.direction.value,
            original_amount=advance.original_amount,
            currency=advance.currency,
            event_date=advance.event_date,
            status=advance.status,
        ))

    def get_advance_by_payment(self, payment_id: UUID) -> Advance | None:
        row = self.session.scalar(select(AdvanceRow).where(AdvanceRow.source_payment_id == payment_id))
        if not row:
            return None
        return Advance(
            id=row.id,
            entity_id=row.entity_id,
            counterparty_id=row.counterparty_id,
            source_payment_id=row.source_payment_id,
            direction=AdvanceDirection(row.direction),
            original_amount=money(row.original_amount),
            currency=row.currency,
            event_date=row.event_date,
            status=row.status,
        )

    def get_posting(self, posting_id: UUID) -> FinancialPosting | None:
        row = self.session.get(PostingRow, posting_id)
        if not row:
            return None
        lines = self.session.scalars(select(PostingEntryRow).where(PostingEntryRow.posting_id == posting_id).order_by(PostingEntryRow.id)).all()
        return FinancialPosting(id=row.id, entity_id=row.entity_id, currency=row.currency,
            event_date=row.event_date, posting_type=row.posting_type, source_type=row.source_type,
            source_id=row.source_id, created_by=row.created_by, reversal_of_id=row.reversal_of_id,
            reason=row.reason,
            lines=[PostingLine(i.ledger_account_id, PostingSide(i.side), i.amount) for i in lines])

    def get_advance(self, advance_id, *, for_update=False):
        stmt = select(AdvanceRow).where(AdvanceRow.id == advance_id)
        if for_update: stmt = stmt.with_for_update()
        row = self.session.scalar(stmt.execution_options(populate_existing=True))
        return self.get_advance_by_payment(row.source_payment_id) if row else None

    def application_total(self, *, advance_id=None, obligation_id=None):
        stmt = select(func.coalesce(func.sum(ApplicationRow.amount), 0)).where(ApplicationRow.status == 'posted')
        stmt = stmt.where(ApplicationRow.advance_id == advance_id) if advance_id else stmt.where(ApplicationRow.obligation_id == obligation_id)
        return money(self.session.scalar(stmt))

    def advance_application_links(self, advance_id):
        from greenhouse_owner_os.domain.cash import serialize_cash
        rows = self.session.scalars(select(ApplicationRow.id).where(ApplicationRow.advance_id==advance_id).order_by(ApplicationRow.id)).all()
        return [serialize_cash(self.get_cash_record('advance_application', rid)) for rid in rows]

    def advance_remaining(self, advance_id):
        a = self.get_advance(advance_id)
        if not a: raise NotFoundError('advance not found')
        return Decimal('0.00') if a.status == 'reversed' else money(a.original_amount - self.application_total(advance_id=advance_id))

    def counterparty_position(self, entity_id, counterparty_id, currency):
        self.assert_counterparty_scope(counterparty_id, entity_id)
        row = self.session.execute(text("""
          SELECT
           coalesce((SELECT sum(remaining_amount) FROM finance.obligation_balances WHERE entity_id=:e AND counterparty_id=:c AND currency=:x AND direction='payable' AND settlement_status IN ('open','partial','settled')),0) AS open_payable,
           coalesce((SELECT sum(remaining_amount) FROM finance.obligation_balances WHERE entity_id=:e AND counterparty_id=:c AND currency=:x AND direction='receivable' AND settlement_status IN ('open','partial','settled')),0) AS open_receivable,
           coalesce((SELECT sum(remaining_amount) FROM finance.advance_balances WHERE entity_id=:e AND counterparty_id=:c AND currency=:x AND direction='paid'),0) AS supplier_advance_remaining,
           coalesce((SELECT sum(remaining_amount) FROM finance.advance_balances WHERE entity_id=:e AND counterparty_id=:c AND currency=:x AND direction='received'),0) AS customer_advance_remaining
        """), {'e':entity_id,'c':counterparty_id,'x':currency}).mappings().one()
        return {k:money(v) for k,v in row.items()}

    def reverse_advance(self, payment_id: UUID) -> None:
        row = self.session.scalar(select(AdvanceRow).where(AdvanceRow.source_payment_id == payment_id).with_for_update())
        if row:
            if self.application_total(advance_id=row.id):
                raise ValidationError("reverse active advance applications first")
            if row.status != "open":
                raise ValidationError("only untouched open advance can be reversed")
            row.status = "reversed"

    def replacement_id(self, resource_type: str, original_id: UUID) -> UUID | None:
        model = self.CASH_MODELS[resource_type] if resource_type in self.CASH_MODELS else (PaymentRow if resource_type == "payment" else ObligationRow)
        return self.session.scalar(select(model.id).where(model.replacement_of_id == original_id))

    def currency_balances(self, entity_id: UUID) -> dict[str, dict[str, Decimal]]:
        # Every branch groups by currency; there is deliberately no combined total.
        rows = self.session.execute(text("""
            SELECT currency, category, sum(amount) AS amount FROM (
                SELECT currency, 'cash' AS category, balance AS amount
                  FROM finance.money_account_balances WHERE entity_id=:entity
                UNION ALL
                SELECT currency, direction, remaining_amount FROM finance.obligation_balances
                  WHERE entity_id=:entity AND settlement_status IN ('open','partial','settled')
                UNION ALL
                SELECT currency, 'advance_' || direction, remaining_amount FROM finance.advance_balances
                  WHERE entity_id=:entity
            ) positions GROUP BY currency, category ORDER BY currency, category
        """), {"entity": entity_id}).all()
        result = {}
        for currency, category, amount in rows:
            result.setdefault(currency, dict.fromkeys(["cash", "payable", "receivable", "advance_paid", "advance_received"], Decimal("0.00")))[category] = money(amount)
        return result

    def add_posting(self, posting: FinancialPosting) -> None:
        self.assert_live_date(posting.entity_id, posting.event_date)
        posting.validate_balanced()
        self.session.add(PostingRow(
            id=posting.id,
            entity_id=posting.entity_id,
            currency=posting.currency,
            event_date=posting.event_date,
            posting_type=posting.posting_type,
            source_type=posting.source_type,
            source_id=posting.source_id,
            reversal_of_id=posting.reversal_of_id,
            created_by=posting.created_by,
            reason=posting.reason,
        ))
        # Balance validation is deferred, so the header may flush before its lines.
        self.session.flush()
        for line in posting.lines:
            self.session.add(PostingEntryRow(
                posting_id=posting.id,
                ledger_account_id=line.ledger_account_id,
                side=line.side.value,
                amount=line.amount,
            ))

    def get_system_ledger_account(self, entity_id: UUID, currency: str, system_code: str) -> UUID:
        account = self.session.scalar(select(LedgerAccountRow).where(
            LedgerAccountRow.entity_id == entity_id,
            LedgerAccountRow.currency == currency,
            LedgerAccountRow.system_code == system_code,
            LedgerAccountRow.active.is_(True),
        ))
        if not account:
            raise NotFoundError(f"system ledger account {system_code} not found")
        return account.id

    def get_money_account_ledger(self, money_account_id: UUID, entity_id: UUID, currency: str) -> UUID:
        account = self.session.scalar(select(MoneyAccountRow).where(MoneyAccountRow.id == money_account_id))
        if not account:
            raise NotFoundError("money account not found")
        if account.entity_id != entity_id or account.currency != currency or not account.active:
            raise ValidationError("money account entity/currency mismatch or inactive")
        self.assert_account_class(account.ledger_account_id, entity_id, currency, 'cash')
        return account.ledger_account_id

    def assert_ledger_account_scope(self, account_id: UUID, entity_id: UUID, currency: str) -> None:
        account = self.session.get(LedgerAccountRow, account_id)
        if not account:
            raise NotFoundError("ledger account not found")
        if account.entity_id != entity_id or account.currency != currency or not account.active:
            raise ValidationError("ledger account entity/currency mismatch or inactive")

    CASH_MODELS = {'advance_application': ApplicationRow, 'transfer': TransferRow, 'owner_funding': FundingRow, 'direct_settlement': SettlementRow}

    def add_cash_record(self, kind, record, actor_id):
        self.session.add(self.CASH_MODELS[kind](**asdict(record), created_by=actor_id))
        self.session.flush()

    def get_cash_record(self, kind, resource_id, *, for_update=False):
        model = self.CASH_MODELS[kind]
        stmt = select(model).where(model.id == resource_id)
        if for_update: stmt = stmt.with_for_update()
        row = self.session.scalar(stmt.execution_options(populate_existing=True))
        if not row: return None
        data = {f.name: getattr(row, f.name) for f in fields(CASH_TYPES[kind])}
        data['status'] = RecordStatus(data['status'])
        return CASH_TYPES[kind](**data)

    def save_cash_record(self, kind, record):
        row = self.session.get(self.CASH_MODELS[kind], record.id)
        for name in ('status','version','posting_id','posted_at','reversal_posting_id','reversed_at','reversed_by','reversal_reason'):
            setattr(row, name, getattr(record, name))

    def lock_money_accounts(self, account_ids):
        for account_id in sorted(set(account_ids), key=str):
            self.session.scalar(select(MoneyAccountRow).where(MoneyAccountRow.id == account_id).with_for_update())

    def assert_counterparty_scope(self, counterparty_id, entity_id):
        row = self.session.get(CounterpartyRow, counterparty_id)
        if not row or row.entity_id != entity_id:
            raise ValidationError('counterparty entity mismatch')

    def assert_account_class(self, account_id, entity_id, currency, account_class):
        self.assert_ledger_account_scope(account_id, entity_id, currency)
        row = self.session.get(LedgerAccountRow, account_id)
        if row.account_class != account_class:
            raise ValidationError('ledger account class mismatch')


class SqlAlchemyIdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock(self, actor_id: UUID, operation: str, key: str) -> None:
        # Transaction-scoped advisory lock serializes concurrent retries for the same intended write.
        lock_material = f"{actor_id}:{operation}:{key}"
        self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:value, 0))"), {"value": lock_material})

    def get(self, actor_id: UUID, operation: str, key: str) -> dict[str, Any] | None:
        row = self.session.scalar(select(IdempotencyRow).where(
            IdempotencyRow.actor_id == actor_id,
            IdempotencyRow.operation == operation,
            IdempotencyRow.idempotency_key == key,
        ))
        if not row:
            return None
        return {
            "request_hash": row.request_hash,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "response": row.response_json,
        }

    def put(self, *, actor_id: UUID, operation: str, key: str, request_hash: str, resource_type: str, resource_id: UUID, response: dict[str, Any]) -> None:
        stmt = pg_insert(IdempotencyRow).values(
            actor_id=actor_id,
            operation=operation,
            idempotency_key=key,
            request_hash=request_hash,
            resource_type=resource_type,
            resource_id=resource_id,
            response_json=response,
        ).on_conflict_do_nothing(constraint="uq_idempotency_actor_operation_key")
        result = self.session.execute(stmt)
        if result.rowcount == 0:
            existing = self.get(actor_id, operation, key)
            if not existing or existing["request_hash"] != request_hash:
                raise IdempotencyConflictError("idempotency key already used with different request")


class SqlAlchemyAuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, *, actor_id: UUID, action: str, entity_type: str, entity_id: UUID, before: dict[str, Any] | None, after: dict[str, Any] | None, reason: str | None = None) -> None:
        self.session.add(AuditEventRow(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_json=before,
            after_json=after,
            reason=reason,
        ))


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self.session_factory()
        self.finance = SqlAlchemyFinanceRepository(self.session)
        self.idempotency = SqlAlchemyIdempotencyRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.session:
            return
        try:
            if exc_type:
                self.session.rollback()
        finally:
            self.session.close()

    def commit(self) -> None:
        assert self.session is not None
        self.session.commit()

    def rollback(self) -> None:
        assert self.session is not None
        self.session.rollback()
