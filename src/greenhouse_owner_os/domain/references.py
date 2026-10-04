"""Minimal owner reference commands. Classification is trusted environment config."""
import unicodedata
from typing import Literal
from pydantic import Field, field_validator
from .commands import StrictModel

SUPPORTED_CURRENCIES = ('TRY', 'USD', 'EUR', 'UAH')
SYSTEM_ACCOUNTS = {
    'AP_CONTROL': ('payable', 'credit'),
    'AR_CONTROL': ('receivable', 'debit'),
    'SUPPLIER_ADVANCE': ('advance', 'debit'),
    'CUSTOMER_ADVANCE': ('advance', 'credit'),
    'OWNER_FUNDING': ('owner_funding', 'credit'),
    'TRANSFER_FEE': ('expense', 'debit'),
    'GENERAL_EXPENSE': ('expense', 'debit'),
    'GENERAL_REVENUE': ('revenue', 'credit'),
    'OPENING_PAYABLE_OFFSET': ('opening_equity', 'debit'),
}


def canonical_key(name):
    return unicodedata.normalize('NFC', name).casefold()


class ReferenceCommand(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=500)

    @field_validator('name', 'source')
    @classmethod
    def exact_text(cls, value):
        if value != value.strip() or not value.strip():
            raise ValueError('supply exact nonblank text without surrounding whitespace')
        return value


class CurrencyReference(ReferenceCommand):
    currency: Literal['TRY', 'USD', 'EUR', 'UAH']


class EntitySetup(CurrencyReference):
    pass


class MoneyAccountSetup(CurrencyReference):
    account_type: Literal['bank', 'cash']


class CounterpartySetup(ReferenceCommand):
    role: Literal['supplier', 'customer', 'depot', 'both']


class BootstrapCommand(StrictModel):
    currency: Literal['TRY', 'USD', 'EUR', 'UAH']
