"""Exact reviewed Harvest commands. Missing evidence stays missing."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from .commands import StrictModel
from .owner_form_source import OwnerFormSource, validate_binding, form_decimal

class ExactModel(StrictModel):
    @field_validator('*', mode='before')
    @classmethod
    def no_float(cls,v):
        if isinstance(v,float):raise ValueError('Use an exact decimal string')
        return v

Money=__import__('typing').Annotated[Decimal,Field(ge=0,max_digits=20,decimal_places=2)]
Weight=__import__('typing').Annotated[Decimal,Field(gt=0,max_digits=20,decimal_places=3)]
def line_amount(kg,price):return (Decimal(kg)*Decimal(price)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
def quality(name):
    return {'grand':'FIRST','sera':'FIRST','yerli':'YERLI','ikinci':'SECOND','i̇kinci':'SECOND','2 inci':'SECOND'}.get(' '.join(name.casefold().split()),'VERIFY')

class EvidenceInput(StrictModel):
    structured_original: OwnerFormSource|None=Field(default=None,exclude_if=lambda v:v is None)
    existing_id: UUID|None=None
    source: str=Field(min_length=1,max_length=200)
    source_reference: str|None=Field(default=None,max_length=2000)
    raw_text: str|None=Field(default=None,max_length=10000)
    filename: str|None=Field(default=None,max_length=200)
    mime_type: Literal['image/jpeg','image/png','application/pdf']|None=None
    file_base64: str|None=Field(default=None,max_length=7000000)
    @model_validator(mode='after')
    def supplied(self):
        validate_binding(self,'field_harvest',self.source_reference)
        if not any((self.existing_id,self.source_reference,self.raw_text,self.file_base64,self.structured_original)):raise ValueError('Original source is required')
        if self.file_base64 and not (self.filename and self.mime_type):raise ValueError('Filename and file type required')
        return self

class ReportInput(ExactModel):
    event_date: date
    original_greenhouse: str=Field(min_length=1,max_length=250)
    work_area_id: UUID|None=None
    cut_count: int|None=Field(default=None,gt=0)
    field_weight_kg: Weight|None=None
    reporter: str=Field(min_length=1,max_length=250)
    notes: str|None=Field(default=None,max_length=2000)
    evidence: EvidenceInput
    @model_validator(mode='after')
    def original_matches(self):
        original=self.evidence.structured_original
        if original:
            f=original.fields
            expected=dict(event_date=f['event_date'],original_greenhouse=f['original_greenhouse'],work_area_id=f['work_area_id'] or None,
                cut_count=f['cut_count'] or None,field_weight_kg=form_decimal(f['field_weight_kg']),reporter=f['reporter'],notes=f['notes'] or None)
            candidate=ReportInput.model_validate(dict(expected,evidence=dict(source='comparison',raw_text='comparison')))
            if candidate.model_dump(exclude={'evidence'})!=self.model_dump(exclude={'evidence'}):raise ValueError('Initial report differs from immutable form source')
        return self

class BatchInput(StrictModel):
    event_date: date
    label: str=Field(min_length=1,max_length=250)
    report_ids: tuple[UUID,...]=()
    link_evidence: str=Field(min_length=1,max_length=2000)
    original_greenhouse: str|None=Field(default=None,max_length=250)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.report_ids))!=len(self.report_ids):raise ValueError('Repeated report')
        return self

class LineAllocation(ExactModel):
    work_area_id: UUID
    net_weight_kg: Weight
    amount: Money
    confirmed_count: int|None=Field(default=None,gt=0)

class LineInput(ExactModel):
    original_quality_name: str=Field(min_length=1,max_length=100)
    net_weight_kg: Weight
    unit_price: Money
    stated_amount: Money|None=None
    confirmed_amount: Money|None=None
    count: int|None=Field(default=None,gt=0)
    source_line: str=Field(min_length=1,max_length=1000)
    allocations: tuple[LineAllocation,...]=()
    @model_validator(mode='after')
    def valid(self):
        amount=self.confirmed_amount if self.confirmed_amount is not None else line_amount(self.net_weight_kg,self.unit_price)
        if amount<=0:raise ValueError('Line amount must be positive')
        if self.confirmed_amount is not None and self.confirmed_amount not in (line_amount(self.net_weight_kg,self.unit_price),self.stated_amount):raise ValueError('Confirmed amount must match calculated or stated evidence')
        if len({a.work_area_id for a in self.allocations})!=len(self.allocations):raise ValueError('Repeated allocation')
        if sum((a.net_weight_kg for a in self.allocations),Decimal(0))>self.net_weight_kg or sum((a.amount for a in self.allocations),Decimal(0))>amount:raise ValueError('Allocation exceeds line')
        if self.count and sum((a.confirmed_count or 0 for a in self.allocations),0)>self.count:raise ValueError('Allocation count exceeds line')
        return self

class SettlementInput(ExactModel):
    depot_id: UUID
    event_date: date
    currency: Literal['TRY','USD','EUR','UAH']
    due_date: date|None=None
    expected_payment: bool=True
    batch_ids: tuple[UUID,...]=()
    link_evidence: str|None=Field(default=None,max_length=2000)
    original_greenhouse: str|None=Field(default=None,max_length=250)
    document_number: str|None=Field(default=None,max_length=250)
    mixed: bool=False
    stated_total: Money|None=None
    alacak_value: Money|None=None
    lines: tuple[LineInput,...]=Field(min_length=1,max_length=100)
    evidence: EvidenceInput
    replacement_of_id: UUID|None=None
    @model_validator(mode='after')
    def valid(self):
        if self.evidence.structured_original is not None:raise ValueError('Field form evidence cannot originate a depot document')
        if self.due_date and self.due_date<self.event_date:raise ValueError('Due date precedes event')
        if self.batch_ids and not self.link_evidence:raise ValueError('Explicit matching evidence required')
        if len(set(self.batch_ids))!=len(self.batch_ids):raise ValueError('Repeated batch')
        return self

class AdvanceUse(ExactModel):
    advance_id: UUID
    amount: Money
    @model_validator(mode='after')
    def positive(self):
        if self.amount<=0:raise ValueError('Positive amount required')
        return self

class SettlementPost(ExactModel):
    expected_version: int=Field(gt=0)
    confirmed: Literal[True]
    review_reason: str|None=Field(default=None,max_length=2000)
    reviewed_issues: tuple[str,...]=()
    cash_amount: Money=Decimal('0')
    money_account_id: UUID|None=None
    advances: tuple[AdvanceUse,...]=()
    @model_validator(mode='after')
    def valid(self):
        if self.cash_amount and not self.money_account_id:raise ValueError('Actual payment requires money account')
        if len({a.advance_id for a in self.advances})!=len(self.advances):raise ValueError('Repeated advance')
        return self
