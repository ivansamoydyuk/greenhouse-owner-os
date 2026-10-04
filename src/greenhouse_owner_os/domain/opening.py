"""Explicit outstanding AP recognition, not an import/replay permission."""
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator
from .commands import StrictModel


class OpeningPayableCommand(StrictModel):
    counterparty_id: UUID
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    currency: Literal['TRY', 'USD', 'EUR', 'UAH']
    original_event_date: date
    description: str = Field(min_length=1, max_length=2000)
    source: str = Field(min_length=1, max_length=2000)
    approval_reference: str = Field(min_length=1, max_length=500)

    @field_validator('amount', mode='before')
    @classmethod
    def exact_amount(cls, value):
        if isinstance(value, float):
            raise ValueError('opening amount requires exact decimal, never float')
        return value

    @field_validator('amount')
    @classmethod
    def cents(cls, value):
        return value.quantize(Decimal('.01'))

    @field_validator('description', 'source', 'approval_reference')
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError('exact nonblank opening provenance required')
        return value
