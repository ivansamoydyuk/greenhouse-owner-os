from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Iterable
from uuid import UUID, uuid4

from .errors import ValidationError

MONEY_QUANTUM = Decimal("0.01")


def money(value: Decimal | str | int) -> Decimal:
    result = Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if not result.is_finite():
        raise ValidationError("money amount must be finite")
    return result


def positive_money(value: Decimal | str | int, field_name: str = "amount") -> Decimal:
    result = money(value)
    if result <= Decimal("0.00"):
        raise ValidationError(f"{field_name} must be greater than zero")
    return result


class RecordStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    POSTED = "posted"
    REVERSED = "reversed"


class ObligationDirection(StrEnum):
    PAYABLE = "payable"
    RECEIVABLE = "receivable"


class PaymentDirection(StrEnum):
    OUT = "out"
    IN = "in"


class AdvanceDirection(StrEnum):
    PAID = "paid"
    RECEIVED = "received"


class PostingSide(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    obligation_id: UUID
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", positive_money(self.amount))


@dataclass(slots=True)
class Obligation:
    id: UUID
    entity_id: UUID
    counterparty_id: UUID
    direction: ObligationDirection
    original_amount: Decimal
    currency: str
    event_date: date
    due_date: date | None
    offset_ledger_account_id: UUID
    status: RecordStatus = RecordStatus.DRAFT
    version: int = 1
    posting_id: UUID | None = None
    posted_at: datetime | None = None
    reversal_posting_id: UUID | None = None
    reversed_at: datetime | None = None
    reversed_by: UUID | None = None
    reversal_reason: str | None = None
    replacement_of_id: UUID | None = None

    @classmethod
    def draft(
        cls,
        *,
        entity_id: UUID,
        counterparty_id: UUID,
        direction: ObligationDirection,
        original_amount: Decimal,
        currency: str,
        event_date: date,
        due_date: date | None,
        offset_ledger_account_id: UUID,
    ) -> "Obligation":
        return cls(
            id=uuid4(),
            entity_id=entity_id,
            counterparty_id=counterparty_id,
            direction=direction,
            original_amount=positive_money(original_amount, "original_amount"),
            currency=validate_currency(currency),
            event_date=event_date,
            due_date=due_date,
            offset_ledger_account_id=offset_ledger_account_id,
        )

    def post(self, posting_id: UUID) -> None:
        if self.status not in {RecordStatus.DRAFT, RecordStatus.REVIEW}:
            raise ValidationError(f"obligation cannot be posted from status {self.status}")
        self.status = RecordStatus.POSTED
        self.posting_id = posting_id
        self.posted_at = datetime.now(timezone.utc)
        self.version += 1


@dataclass(slots=True)
class PaymentAllocation:
    id: UUID
    payment_id: UUID
    obligation_id: UUID
    amount: Decimal


@dataclass(slots=True)
class Payment:
    id: UUID
    entity_id: UUID
    money_account_id: UUID
    counterparty_id: UUID
    direction: PaymentDirection
    amount: Decimal
    currency: str
    event_date: date
    status: RecordStatus = RecordStatus.DRAFT
    version: int = 1
    posting_id: UUID | None = None
    posted_at: datetime | None = None
    reversal_posting_id: UUID | None = None
    reversed_at: datetime | None = None
    reversed_by: UUID | None = None
    reversal_reason: str | None = None
    replacement_of_id: UUID | None = None
    allocations: list[PaymentAllocation] = field(default_factory=list)

    @classmethod
    def draft(
        cls,
        *,
        entity_id: UUID,
        money_account_id: UUID,
        counterparty_id: UUID,
        direction: PaymentDirection,
        amount: Decimal,
        currency: str,
        event_date: date,
        allocations: Iterable[AllocationRequest],
    ) -> "Payment":
        payment_id = uuid4()
        rows = [
            PaymentAllocation(
                id=uuid4(),
                payment_id=payment_id,
                obligation_id=item.obligation_id,
                amount=positive_money(item.amount),
            )
            for item in allocations
        ]
        total = sum((row.amount for row in rows), Decimal("0.00"))
        amount_decimal = positive_money(amount)
        if total > amount_decimal:
            raise ValidationError("allocation total cannot exceed payment amount")
        return cls(
            id=payment_id,
            entity_id=entity_id,
            money_account_id=money_account_id,
            counterparty_id=counterparty_id,
            direction=direction,
            amount=amount_decimal,
            currency=validate_currency(currency),
            event_date=event_date,
            allocations=rows,
        )

    @property
    def allocation_total(self) -> Decimal:
        return money(sum((row.amount for row in self.allocations), Decimal("0.00")))

    @property
    def unallocated_amount(self) -> Decimal:
        return money(self.amount - self.allocation_total)

    def post(self, posting_id: UUID) -> None:
        if self.status not in {RecordStatus.DRAFT, RecordStatus.REVIEW}:
            raise ValidationError(f"payment cannot be posted from status {self.status}")
        self.status = RecordStatus.POSTED
        self.posting_id = posting_id
        self.posted_at = datetime.now(timezone.utc)
        self.version += 1


@dataclass(slots=True)
class Advance:
    id: UUID
    entity_id: UUID
    counterparty_id: UUID
    source_payment_id: UUID
    direction: AdvanceDirection
    original_amount: Decimal
    currency: str
    event_date: date
    status: str = "open"


@dataclass(frozen=True, slots=True)
class PostingLine:
    ledger_account_id: UUID
    side: PostingSide
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", positive_money(self.amount))


@dataclass(slots=True)
class FinancialPosting:
    id: UUID
    entity_id: UUID
    currency: str
    event_date: date
    posting_type: str
    source_type: str
    source_id: UUID
    created_by: UUID
    lines: list[PostingLine]
    reversal_of_id: UUID | None = None
    reason: str | None = None

    def validate_balanced(self) -> None:
        debit = sum((line.amount for line in self.lines if line.side == PostingSide.DEBIT), Decimal("0.00"))
        credit = sum((line.amount for line in self.lines if line.side == PostingSide.CREDIT), Decimal("0.00"))
        if len(self.lines) < 2 or money(debit) != money(credit):
            raise ValidationError(f"posting is not balanced: debit={money(debit)} credit={money(credit)}")


def validate_currency(currency: str) -> str:
    value = currency.upper().strip()
    if len(value) != 3 or not value.isalpha():
        raise ValidationError("currency must be a three-letter ISO 4217 code")
    return value
