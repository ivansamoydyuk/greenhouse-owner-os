from dataclasses import dataclass, asdict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from .commands import StrictModel
from .finance import RecordStatus, validate_currency


class CashCommand(StrictModel):
    entity_id: UUID
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    event_date: date
    source: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator('amount', mode='before')
    @classmethod
    def exact_amount(cls, value):
        if isinstance(value, float):
            raise ValueError('use an exact decimal string, not float')
        return value

    @field_validator('amount')
    @classmethod
    def canonical_amount(cls, value):
        return value.quantize(Decimal('0.01'))

    @field_validator('currency')
    @classmethod
    def currency_code(cls, value):
        return validate_currency(value)


class TransferCommand(CashCommand):
    source_account_id: UUID
    destination_account_id: UUID
    fee: Decimal = Field(default=Decimal('0.00'), ge=0, max_digits=20, decimal_places=2)
    fee_account_id: UUID | None = None

    @field_validator('fee', mode='before')
    @classmethod
    def exact_fee(cls, value):
        return cls.exact_amount(value)

    @field_validator('fee')
    @classmethod
    def canonical_fee(cls, value):
        return value.quantize(Decimal('0.01'))

    @model_validator(mode='after')
    def valid_transfer(self):
        if self.source_account_id == self.destination_account_id:
            raise ValueError('transfer accounts must differ')
        if bool(self.fee) != bool(self.fee_account_id):
            raise ValueError('positive fee requires explicit expense account; zero fee has no fee account')
        return self


class FundingCommand(CashCommand):
    money_account_id: UUID


class SupplierAdvanceCommand(CashCommand):
    money_account_id: UUID
    counterparty_id: UUID


class SettlementCommand(CashCommand):
    receivable_id: UUID
    payable_id: UUID


@dataclass(kw_only=True)
class CashLifecycle:
    id: UUID
    entity_id: UUID
    amount: Decimal
    currency: str
    event_date: date
    source: str | None = None
    reason: str | None = None
    status: RecordStatus = RecordStatus.DRAFT
    version: int = 1
    posting_id: UUID | None = None
    posted_at: datetime | None = None
    reversal_posting_id: UUID | None = None
    reversed_at: datetime | None = None
    reversed_by: UUID | None = None
    reversal_reason: str | None = None
    replacement_of_id: UUID | None = None


@dataclass(kw_only=True)
class OwnAccountTransfer(CashLifecycle):
    source_account_id: UUID
    destination_account_id: UUID
    fee: Decimal = Decimal('0.00')
    fee_account_id: UUID | None = None


@dataclass(kw_only=True)
class OwnerFunding(CashLifecycle):
    money_account_id: UUID


@dataclass(kw_only=True)
class DirectSettlement(CashLifecycle):
    receivable_id: UUID
    payable_id: UUID


class AdvanceApplicationCommand(CashCommand):
    advance_id: UUID
    obligation_id: UUID
    counterparty_id: UUID
    direction: str = Field(pattern='^(paid|received)$')


@dataclass(kw_only=True)
class AdvanceApplication(CashLifecycle):
    advance_id: UUID
    obligation_id: UUID
    counterparty_id: UUID
    direction: str


CASH_TYPES = {'advance_application': AdvanceApplication, 'transfer': OwnAccountTransfer, 'owner_funding': OwnerFunding, 'direct_settlement': DirectSettlement}


def serialize_cash(record):
    def value(v):
        if isinstance(v, Decimal): return f'{v:.2f}'
        if isinstance(v, UUID): return str(v)
        if isinstance(v, (date, datetime)): return v.isoformat()
        return v
    return {k: value(v) for k, v in asdict(record).items()}
