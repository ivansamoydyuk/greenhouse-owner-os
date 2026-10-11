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
        self.cutovers = {}
        self.import_batches = {}
        self.historical_candidates = {}
        self.obligations: dict[UUID, Obligation] = {}
        self.cash_records = {k: {} for k in ("transfer", "owner_funding", "direct_settlement", "advance_application")}
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


from .history import MemoryHistoryRepositoryMixin


class MemoryFinanceRepository(MemoryHistoryRepositoryMixin):
    def assert_manual_receivable_boundary(self, counterparty_id):
        # This unit adapter has no reference directory; PostgreSQL owns roles.
        pass

    def assert_strategic_basis(self, obligation):
        cash_ledgers={a[0] for a in self.store.money_account_ledgers.values()}
        principal=self.store.system_accounts.get((obligation.entity_id,obligation.currency,'STRATEGIC_PRINCIPAL'))
        if obligation.direction!='payable' or obligation.offset_ledger_account_id not in cash_ledgers|{principal}:
            raise ValidationError('strategic principal requires cash or financing principal account; never expense')

    def add_attributions(self, obligation, actor_id):
        # Unit adapter retains typed basis in its draft, but is not FK verification.
        pass

    def get_opening_payable(self, obligation_id):
        return None

    def get_opening_payables(self, entity_id):
        return []

    def assert_regular_offset(self, account_id):
        if any(code == 'OPENING_PAYABLE_OFFSET' and aid == account_id
               for (_, _, code), aid in self.store.system_accounts.items()):
            raise ValidationError('opening offset reserved for explicit owner approval')
        if any(code in ('AP_CONTROL', 'AR_CONTROL', 'SUPPLIER_ADVANCE', 'CUSTOMER_ADVANCE') and aid == account_id
               for (_, _, code), aid in self.store.system_accounts.items()):
            raise ValidationError('subledger control account cannot be an ordinary obligation offset')

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
        if obligation.status == RecordStatus.REVERSED:
            return Decimal("0.00")
        allocated = Decimal("0.00")
        for payment in self.store.payments.values():
            if payment.status != RecordStatus.POSTED:
                continue
            allocated += sum(
                (row.amount for row in payment.allocations if row.obligation_id == obligation_id),
                Decimal("0.00"),
            )
        allocated += sum((r.amount for r in self.store.cash_records['direct_settlement'].values()
            if r.status == RecordStatus.POSTED and obligation_id in (r.receivable_id, r.payable_id)), Decimal('0.00'))
        return money(obligation.original_amount - allocated - self.application_total(obligation_id=obligation_id))

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

    def get_posting(self, posting_id: UUID) -> FinancialPosting | None:
        return copy.deepcopy(self.store.postings.get(posting_id))

    def get_advance(self, advance_id, *, for_update=False):
        return copy.deepcopy(self.store.advances.get(advance_id))

    def application_total(self, *, advance_id=None, obligation_id=None):
        return sum((r.amount for r in self.store.cash_records['advance_application'].values()
                    if r.status == RecordStatus.POSTED and (r.advance_id == advance_id if advance_id else r.obligation_id == obligation_id)), Decimal('0.00'))

    def advance_application_links(self, advance_id):
        from greenhouse_owner_os.domain.cash import serialize_cash
        return [serialize_cash(r) for r in sorted(self.store.cash_records['advance_application'].values(),key=lambda r:str(r.id)) if r.advance_id==advance_id]

    def advance_remaining(self, advance_id):
        a = self.get_advance(advance_id)
        if not a: raise NotFoundError('advance not found')
        return Decimal('0.00') if a.status == 'reversed' else a.original_amount - self.application_total(advance_id=advance_id)

    def counterparty_position(self, entity_id, counterparty_id, currency):
        result = dict.fromkeys(['open_payable','open_receivable','supplier_advance_remaining','customer_advance_remaining'], Decimal('0.00'))
        for ob in self.store.obligations.values():
            if (ob.entity_id,ob.counterparty_id,ob.currency,ob.status) == (entity_id,counterparty_id,currency,RecordStatus.POSTED):
                result['open_'+ob.direction.value] += self.obligation_remaining(ob.id)
        for a in self.store.advances.values():
            if (a.entity_id,a.counterparty_id,a.currency) == (entity_id,counterparty_id,currency):
                result[('supplier' if a.direction.value == 'paid' else 'customer')+'_advance_remaining'] += self.advance_remaining(a.id)
        return result

    def reverse_advance(self, payment_id: UUID) -> None:
        for advance in self.store.advances.values():
            if advance.source_payment_id == payment_id:
                if self.application_total(advance_id=advance.id):
                    raise ValidationError("reverse active advance applications first")
                if advance.status != "open":
                    raise ValidationError("only untouched open advance can be reversed")
                advance.status = "reversed"

    def replacement_id(self, resource_type: str, original_id: UUID) -> UUID | None:
        items = self.store.cash_records[resource_type] if resource_type in self.store.cash_records else (self.store.payments if resource_type == "payment" else self.store.obligations)
        return next((i.id for i in items.values() if i.replacement_of_id == original_id), None)

    def currency_balances(self, entity_id: UUID) -> dict[str, dict[str, Decimal]]:
        result = {}
        def add(currency, category, amount):
            row = result.setdefault(currency, dict.fromkeys(["cash", "payable", "receivable", "advance_paid", "advance_received"], Decimal("0.00")))
            row[category] += amount
        cash_ids = {v[0] for v in self.store.money_account_ledgers.values() if v[1] == entity_id}
        for posting in self.store.postings.values():
            if posting.entity_id == entity_id:
                for line in posting.lines:
                    if line.ledger_account_id in cash_ids:
                        add(posting.currency, "cash", line.amount if line.side.value == "debit" else -line.amount)
        for ob in self.store.obligations.values():
            if ob.entity_id == entity_id and ob.status == RecordStatus.POSTED:
                add(ob.currency, ob.direction.value, self.obligation_remaining(ob.id))
        for adv in self.store.advances.values():
            if adv.entity_id == entity_id and adv.status == "open":
                add(adv.currency, "advance_" + adv.direction.value, self.advance_remaining(adv.id))
        return result

    def add_posting(self, posting: FinancialPosting) -> None:
        self.assert_live_date(posting.entity_id, posting.event_date)
        if str(posting.source_id) in self.store.historical_candidates:
            raise ValidationError("import candidates are non-posting evidence")
        posting.validate_balanced()
        for line in posting.lines:
            self.assert_ledger_account_scope(line.ledger_account_id, posting.entity_id, posting.currency)
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

    def add_cash_record(self, kind, record, actor_id):
        self.store.cash_records[kind][record.id] = copy.deepcopy(record)

    def get_cash_record(self, kind, resource_id, *, for_update=False):
        return copy.deepcopy(self.store.cash_records[kind].get(resource_id))

    def save_cash_record(self, kind, record):
        self.store.cash_records[kind][record.id] = copy.deepcopy(record)

    def lock_money_accounts(self, account_ids):
        pass  # Sequential test double; PostgreSQL tests verify actual locking.

    def assert_counterparty_scope(self, counterparty_id, entity_id):
        pass  # References are provisioned by fixtures; production validates in PostgreSQL.

    def assert_account_class(self, account_id, entity_id, currency, account_class):
        self.assert_ledger_account_scope(account_id, entity_id, currency)


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
