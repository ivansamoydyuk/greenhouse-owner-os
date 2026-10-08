"""Versioned review DTOs. Missing facts are valid before confirmation only."""
import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from .commands import StrictModel, AttributionInput
from .inventory import InventoryLine
from .harvest import LineInput


class OwnerCapture(StrictModel):
    client_capture_id: UUID
    captured_at: datetime
    timezone: Literal['Europe/Istanbul'] = 'Europe/Istanbul'
    input_type: Literal['text', 'voice_transcript', 'photo', 'file'] = 'text'
    text: str | None = Field(default=None, min_length=1, max_length=10000)
    filename: str | None = Field(default=None, min_length=1, max_length=200)
    mime_type: Literal['application/pdf', 'image/png', 'image/jpeg'] | None = None
    file_base64: str | None = Field(default=None, max_length=27962028)

    @model_validator(mode='after')
    def meaningful(self):
        if self.captured_at.tzinfo is None:
            raise ValueError('Timezone required')
        if not (self.text and self.text.strip()) and not self.file_base64:
            raise ValueError('Meaningful source required')
        if self.file_base64 and not (self.filename and self.mime_type):
            raise ValueError('Filename and MIME required')
        if self.input_type in ('text', 'voice_transcript') and (not self.text or self.file_base64):
            raise ValueError('Transcript/text required')
        if self.input_type in ('photo', 'file') and not self.file_base64:
            raise ValueError('Original file required')
        if self.filename and re.search(r'[/\\\x00-\x1f\x7f]', self.filename):
            raise ValueError('Unsafe filename')
        return self


class FinancialProposal(StrictModel):
    counterparty_id: UUID | None = None
    amount: str | None = None
    currency: Literal['TRY', 'USD', 'EUR', 'UAH'] = 'TRY'
    event_date: date | None = None
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    direction: Literal['payable', 'receivable', 'in', 'out'] | None = None
    money_account_id: UUID | None = None
    obligation_id: UUID | None = None
    attributions: tuple[AttributionInput, ...] = ()
    duplicate_reviewed: bool = False

    @field_validator('amount')
    @classmethod
    def exact_amount(cls, value):
        if value is not None and not re.fullmatch(r'\d{1,18}(?:\.\d{1,2})?', value):
            raise ValueError('Exact positive decimal amount required')
        return value


class StockProposal(StrictModel):
    event_date: date | None = None
    lines: list[InventoryLine] = Field(default_factory=list, max_length=100)
    supplier_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)
    duplicate_reviewed: bool = False


class DepotProposal(StrictModel):
    depot_id: UUID | None = None
    event_date: date | None = None
    currency: Literal['TRY', 'USD', 'EUR', 'UAH'] = 'TRY'
    document_number: str | None = Field(default=None, max_length=250)
    lines: list[LineInput] = Field(default_factory=list, max_length=100)
    qualities: list[Literal['HIGHEST', 'FIRST', 'SECOND', 'UNKNOWN']] = Field(default_factory=list)
    stated_total: str | None = None
    alacak_value: str | None = None
    review_reason: str | None = Field(default=None, max_length=2000)
    duplicate_reviewed: bool = False


class TaskProposal(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    original_instruction: str = Field(min_length=1, max_length=2000)
    greenhouse_id: UUID | None = None
    planned_date: date | None = None
    worker_ids: list[UUID] = Field(default_factory=list)
    rendering_candidate: str | None = None
    language: Literal['ru', 'tr', 'fa', 'uz'] = 'ru'


class UnresolvedProposal(StrictModel):
    text: str = Field(max_length=10000)


SCHEMAS = {'PAYABLE': FinancialProposal, 'PAYMENT': FinancialProposal, 'DIRECT_EXPENSE': FinancialProposal,
           'INVENTORY_RECEIPT': StockProposal, 'INVENTORY_APPLICATION': StockProposal,
           'DEPOT_SETTLEMENT': DepotProposal, 'PERSONAL_TASK': TaskProposal, 'WORKER_TASK': TaskProposal,
           'UNRESOLVED': UnresolvedProposal}


class OwnerRevision(StrictModel):
    expected_version: int = Field(gt=0)
    payload: dict


class OwnerDecision(StrictModel):
    expected_version: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=2000)


class OwnerConfirm(OwnerDecision):
    confirmed: Literal[True]
    preview_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class QuickCounterparty(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    confirmed: Literal[True]

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Name required')
        return value.strip()
