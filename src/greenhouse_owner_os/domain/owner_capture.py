"""Versioned, non-executable review DTOs. Unknown facts remain nullable."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from .commands import StrictModel
from .inventory import InventoryLine
from .harvest import LineInput

class CaptureSource(StrictModel):
    capture_context: Literal['quick_expense','depot_receipt','supplier_invoice'] | None = Field(default=None,exclude_if=lambda v:v is None)
    invoice_intent: Literal['operating_expense','existing_payable'] | None = Field(default=None,exclude_if=lambda v:v is None)
    invoice_receipt_id: UUID | None = Field(default=None,exclude_if=lambda v:v is None)
    client_capture_id: UUID
    captured_at: datetime
    timezone: Literal['Europe/Istanbul'] = 'Europe/Istanbul'
    mode: Literal['text','voice_transcript','photo','file'] = 'text'
    text: str | None = Field(default=None, min_length=1, max_length=10000)
    file_base64: str | None = Field(default=None, max_length=27962028)
    filename: str | None = Field(default=None, max_length=200)
    mime_type: Literal['image/jpeg','image/png','application/pdf'] | None = None
    @model_validator(mode='after')
    def meaningful(self):
        if self.capture_context=='supplier_invoice':
            if self.invoice_receipt_id is None:raise ValueError('Physical receipt context is required')
        elif self.invoice_intent is not None or self.invoice_receipt_id is not None:raise ValueError('Invoice fields require supplier_invoice context')
        if self.captured_at.tzinfo is None: raise ValueError('Capture timestamp must include timezone')
        if not ((self.text and self.text.strip()) or self.file_base64): raise ValueError('Meaningful source required')
        if self.file_base64 and not (self.filename and self.mime_type): raise ValueError('File type and name required')
        if self.filename:
            import re
            if re.search(r'[/\\\x00-\x1f\x7f]', self.filename) or self.filename in ('.','..'): raise ValueError('Unsafe filename')
        return self

class ProposalAttribution(StrictModel):
    work_area_id: UUID
    amount: str
    @field_validator('amount',mode='before')
    @classmethod
    def exact(cls,v):
        import re
        from decimal import Decimal
        if not isinstance(v,str) or not re.fullmatch(r'\d{1,18}(?:\.\d{1,2})?',v) or Decimal(v)<=0:
            raise ValueError('Positive exact attribution required')
        return v

class MoneyProposal(StrictModel):
    document_reviewed: bool | None = Field(default=None,strict=True,exclude_if=lambda v:v is None)
    invoice_intent: Literal['undecided','operating_expense'] | None = Field(default=None,exclude_if=lambda v:v is None)
    amount: str | None = None
    currency: Literal['TRY','USD','EUR','UAH'] | None = 'TRY'
    event_date: date | None = None
    counterparty_id: UUID | None = None
    money_account_id: UUID | None = None
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    obligation_id: UUID | None = None
    payment_purpose: Literal['debt','advance','expense'] | None = None
    attributions: list[ProposalAttribution] = Field(default_factory=list,max_length=100)
    @field_validator('amount', mode='before')
    @classmethod
    def exact(cls,v):
        import re
        from decimal import Decimal
        if v is not None and (not isinstance(v,str) or not re.fullmatch(r'\d{1,18}(?:\.\d{1,2})?',v) or Decimal(v)<=0): raise ValueError('Positive exact amount required')
        return v

class StockProposal(StrictModel):
    event_date: date | None = None
    lines: list[InventoryLine] = Field(default_factory=list, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)
    separate_event_reviewed: bool = False

class TaskProposal(StrictModel):
    execution_contract:Literal['R1','R2_RECEIVED_REPORT']|None=Field(default=None,exclude_if=lambda v:v is None)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    original_instruction: str | None = Field(default=None, max_length=10000)
    greenhouse_id: UUID | None = None
    planned_date: date | None = None
    worker_id: UUID | None = None
    language: Literal['ru','tr','fa','uz'] | None = None
    rendered_text: str | None = Field(default=None, max_length=10000)
    note: str | None = Field(default=None, max_length=2000)
    # Preparation metadata only. Omitting absent additions preserves the exact
    # serialized shape (and approval digest) of existing task snapshots.
    category: str | None = Field(default=None, min_length=1, max_length=100, exclude_if=lambda v:v is None)
    relative_time_context: str | None = Field(default=None, min_length=1, max_length=250, exclude_if=lambda v:v is None)

class DepotProposal(StrictModel):
    document_reviewed: bool | None = Field(default=None,strict=True,exclude_if=lambda v:v is None)
    # A document total is not a count allocation across quality lines.
    document_count: int | None = Field(default=None,gt=0,le=2147483647,strict=True,exclude_if=lambda v:v is None)
    due_date: date | None = Field(default=None,exclude_if=lambda v:v is None)
    document_number: str | None = Field(default=None,max_length=250,exclude_if=lambda v:v is None)
    depot_id: UUID | None = None
    event_date: date | None = None
    currency: Literal['TRY','USD','EUR','UAH'] | None = 'TRY'
    original_greenhouse: str | None = Field(default=None,max_length=250)
    batch_ids: list[UUID] = Field(default_factory=list,max_length=100)
    link_evidence: str | None = Field(default=None,max_length=2000)
    separate_event_reviewed: bool = False
    lines: list[LineInput] = Field(default_factory=list, max_length=100)
    qualities: list[Literal['HIGHEST','FIRST','SECOND'] | None] = Field(default_factory=list,max_length=100)
    quality_reviewed: bool = False
    remember_quality_mapping: bool = False
    arithmetic_reviewed: bool = False
    stated_total: str | None = None
    alacak_value: str | None = None
    unreadable_lines: list[str] = Field(default_factory=list,max_length=100)
    @field_validator('stated_total','alacak_value',mode='before')
    @classmethod
    def exact_total(cls,v):
        import re
        if v is not None and (not isinstance(v,str) or not re.fullmatch(r'\d{1,18}(?:\.\d{1,2})?',v)):raise ValueError('Nonnegative exact source amount required')
        return v
    @field_validator('batch_ids')
    @classmethod
    def independent_batches(cls,v):
        if len(set(v))!=len(v):raise ValueError('Repeated batch link')
        return v

class InvoiceMatchProposal(StrictModel):
    receipt_id: UUID | None = None
    finance_obligation_id: UUID | None = None
    note: str | None = Field(default=None,max_length=2000)

class UnknownProposal(StrictModel):
    note: str | None = Field(default=None,max_length=2000)
    money_candidates: MoneyProposal | None = Field(default=None,exclude_if=lambda v:v is None)
    task_candidates: TaskProposal | None = Field(default=None,exclude_if=lambda v:v is None)

DTO = {'PAYABLE':MoneyProposal,'PAYMENT':MoneyProposal,'DIRECT_EXPENSE':MoneyProposal,
       'STOCK_RECEIPT':StockProposal,'STOCK_APPLICATION':StockProposal,'PERSONAL_TASK':TaskProposal,
       'WORKER_TASK':TaskProposal,'DEPOT_RECEIPT':DepotProposal,'INVOICE_MATCH':InvoiceMatchProposal,'UNRESOLVED':UnknownProposal}

class ReviewAction(StrictModel):
    expected_version: int = Field(gt=0)
    reason: str = Field(default='',max_length=2000)

class ReviseAction(ReviewAction):
    payload: dict

class ConfirmAction(ReviewAction):
    confirmed: Literal[True]
    preview_digest: str = Field(pattern=r'^[0-9a-f]{64}$')

class ExpenseRecoveryAction(StrictModel):
    origin_proposal_id: UUID | None = None
    expected_version: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def selected_origin(self):
        if (self.origin_proposal_id is None) != (self.expected_version is None):
            raise ValueError('Selected origin and its version must be supplied together')
        return self

class MoneyRecoveryAction(ReviewAction):
    origin_proposal_id: UUID
    intent: Literal['operating_payable','paid_expense','debt_payment']

class TaskRecoveryAction(ReviewAction):
    origin_proposal_id: UUID
    audience: Literal['personal','worker']

class TaskStateAction(StrictModel):
    expected_version: int = Field(gt=0)
    state: Literal['TO_DO','DONE']

class NameOnlyParty(StrictModel):
    name: str = Field(min_length=1,max_length=250)
    confirmed: Literal[True]
    @field_validator('name')
    @classmethod
    def nonblank(cls,v):
        if not v.strip(): raise ValueError('Name required')
        return v.strip()
