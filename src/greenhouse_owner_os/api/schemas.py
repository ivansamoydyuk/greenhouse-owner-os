from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from greenhouse_owner_os.domain.finance import ObligationDirection, PaymentDirection


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AllocationBody(ApiModel):
    obligation_id: UUID
    amount: Decimal = Field(gt=0)


class ObligationCreateBody(ApiModel):
    entity_id: UUID
    counterparty_id: UUID
    direction: ObligationDirection
    original_amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    event_date: date
    due_date: date | None = None
    replacement_of_id: UUID | None = None
    offset_ledger_account_id: UUID


class PaymentCreateBody(ApiModel):
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
    allocations: list[AllocationBody] = Field(default_factory=list)
