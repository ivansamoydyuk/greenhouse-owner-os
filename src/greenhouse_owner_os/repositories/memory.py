from __future__ import annotations

import copy
from dataclasses import asdict
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from greenhouse_owner_os.domain.errors import NotFoundError, ValidationError
from greenhouse_owner_os.domain.finance import Advance, FinancialPosting, Obligation, Payment, RecordStatus, money


class MemoryStore:
    def __init__(self) -> None:
        self.obligations: dict[UUID, Obligation] = {}
        self.payments: dict[UUID, Payment] = {}
        self.advances: dict[UUID, Advance] = {}
        self.postings: dict[UUID, FinancialPosting] = {}
        self.system_accounts: dict[tuple[UUID, str, str], UUID] = {}
        self.account_scopes: dict[UUID, tuple[UUID, str]] = {}
        self.money_account_ledgers: dict[UUID, tuple[UUID, UUID, str]] = {}
        self.idempotency: dict[tuple[UUID, str, str], dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []

    def seed_ledger_account(self, *, entity_id: UUID, currency: str, system_code: str | None = None) -> UUID:
        account_id = uuid4()
        self.account_scopes[account_id] = (entity_id, currency)
        if system_code:
            self.system_accounts[(entity_id, currency, system_code)] = account_id
        return account_id

    def seed_money_account(self, *, entity_id: UUID, currency: str) -> tuple[UUID, UUID]:
        money_account_id = uuid4()
        ledger_id = self.seed_ledger_account(entity_id=entity_id, currency=currency)
        self.money_account_ledgers[money_account_id] = (ledger_id, entity_id, currency)
        return money_account_id, ledger_id


class MemoryFinanceRepository:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def add_obligation(self, obligation: Obligation, actor_id: UUID) -> None:
        self.store.obligations[obligation.id] = copy.deepcopy(obligation)

    def get_obligation(self, obligation_id: UUID, *, for_update: bool = False) -> Obligation | None:
        item = self.store.obligations.get(obligation_id)
        return copy.deepcopy(item) if item else None

    def save_obligation(self, obligation: Obligation) -> None:
        self.store.obligations[obligation.id] = copy.deepcopy(obligation)

    def obligation_remaining(self, obligation_id: UUID) -> Decimal:
        obligation = self.store.obligations.get(obligation_id)
        if not obligation:
            raise NotFoundError("obligation not found")
        allocated = Decimal("0.00")
        for payment in self.store.payments.values():
            if payment.status != RecordStatus.POSTED:
                continue
            allocated += sum(
                (row.amount for row in payment.allocations if row.obligation_id == obligation_id),
                Decimal("0.00"),
            )
        return money(obligation.original_amount - allocated)

    def add_payment(self, payment: Payment, actor_id: UUID) -> None:
        self.store.payments[payment.id] = copy.deepcopy(payment)

    def get_payment(self, payment_id: UUID, *, for_update: bool = False) -> Payment | None:
        item = self.store.payments.get(payment_id)
        return copy.deepcopy(item) if item else None

    def save_payment(self, payment: Payment) -> None:
        self.store.payments[payment.id] = copy.deepcopy(payment)

    def add_advance(self, advance: Advance) -> None:
        self.store.advances[advance.id] = copy.deepcopy(advance)

    def get_advance_by_payment(self, payment_id: UUID) -> Advance | None:
        for item in self.store.advances.values():
            if item.source_payment_id == payment_id:
                return copy.deepcopy(item)
        return None

    def add_posting(self, posting: FinancialPosting) -> None:
        posting.validate_balanced()
        self.store.postings[posting.id] = copy.deepcopy(posting)

    def get_system_ledger_account(self, entity_id: UUID, currency: str, system_code: str) -> UUID:
        try:
            return self.store.system_accounts[(entity_id, currency, system_code)]
        except KeyError as exc:
            raise NotFoundError(f"system ledger account {system_code} not found") from exc

    def get_money_account_ledger(self, money_account_id: UUID, entity_id: UUID, currency: str) -> UUID:
        try:
            ledger_id, actual_entity, actual_currency = self.store.money_account_ledgers[money_account_id]
        except KeyError as exc:
            raise NotFoundError("money account not found") from exc
        if actual_entity != entity_id or actual_currency != currency:
            raise ValidationError("money account entity/currency mismatch")
        return ledger_id

    def assert_ledger_account_scope(self, account_id: UUID, entity_id: UUID, currency: str) -> None:
        try:
            actual = self.store.account_scopes[account_id]
        except KeyError as exc:
            raise NotFoundError("ledger account not found") from exc
        if actual != (entity_id, currency):
            raise ValidationError("ledger account entity/currency mismatch")


class MemoryIdempotencyRepository:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def lock(self, actor_id: UUID, operation: str, key: str) -> None:
        return None

    def get(self, actor_id: UUID, operation: str, key: str) -> dict[str, Any] | None:
        item = self.store.idempotency.get((actor_id, operation, key))
        return copy.deepcopy(item) if item else None

    def put(self, *, actor_id: UUID, operation: str, key: str, request_hash: str, resource_type: str, resource_id: UUID, response: dict[str, Any]) -> None:
        self.store.idempotency[(actor_id, operation, key)] = {
            "request_hash": request_hash,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "response": copy.deepcopy(response),
        }


class MemoryAuditRepository:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def append(self, *, actor_id: UUID, action: str, entity_type: str, entity_id: UUID, before: dict[str, Any] | None, after: dict[str, Any] | None, reason: str | None = None) -> None:
        self.store.audit_events.append({
            "actor_id": actor_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before": copy.deepcopy(before),
            "after": copy.deepcopy(after),
            "reason": reason,
        })


class MemoryUnitOfWork:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self._snapshot: MemoryStore | None = None
        self.finance = MemoryFinanceRepository(store)
        self.idempotency = MemoryIdempotencyRepository(store)
        self.audit = MemoryAuditRepository(store)

    def __enter__(self) -> "MemoryUnitOfWork":
        self._snapshot = copy.deepcopy(self.store)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type:
            self.rollback()

    def commit(self) -> None:
        self._snapshot = None

    def rollback(self) -> None:
        if self._snapshot is None:
            return
        self.store.__dict__.clear()
        self.store.__dict__.update(copy.deepcopy(self._snapshot.__dict__))
        self._snapshot = None
