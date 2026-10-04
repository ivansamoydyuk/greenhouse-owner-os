from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .finance import ObligationDirection, PaymentDirection, money


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AllocationInput(StrictModel):
    obligation_id: UUID
    amount: Decimal = Field(gt=0)

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value: Decimal) -> Decimal:
        return money(value)


class CreateObligationCommand(StrictModel):
    entity_id: UUID
    counterparty_id: UUID
    direction: ObligationDirection
    original_amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    event_date: date
    due_date: date | None = None
    replacement_of_id: UUID | None = None
    offset_ledger_account_id: UUID

    @field_validator("original_amount")
    @classmethod
    def normalize_amount(cls, value: Decimal) -> Decimal:
        return money(value)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class CreatePaymentCommand(StrictModel):
    entity_id: UUID
    source: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)
    replacement_of_id: UUID | None = None
    money_account_id: UUID
    counterparty_id: UUID
    direction: PaymentDirection
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    event_date: date
    allocations: tuple[AllocationInput, ...] = ()

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value: Decimal) -> Decimal:
        return money(value)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class ReverseCommand(StrictModel):
    expected_version: int = Field(gt=0)
    event_date: date
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonempty_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reversal reason cannot be blank")
        return value
