"""Fertilizer planning and exact owner-approved actual scope (ADR036)."""
import re
from datetime import date,datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field,field_validator,model_validator
from .commands import StrictModel

def decimal_string(v,zero=False):
    if not isinstance(v,str) or not re.fullmatch(r'\d{1,18}(?:[.,]\d{1,12})?',v) or not (Decimal(0)<=Decimal(v.replace(',','.'))<Decimal('1e18')) or (not zero and Decimal(v.replace(',','.'))==0):
        raise ValueError('Exact supported decimal string required')
    return v # Original representation is evidence; computation is separate.
class FormSource(StrictModel):
    form_id:UUID
    captured_at:datetime
    source_kind:Literal['OWNER_FORM']='OWNER_FORM'
    origin:Literal['owner','agronomist']='owner'
    original_text:str|None=Field(default=None,max_length=10000)
    inbox_id:UUID|None=None
    @field_validator('captured_at')
    @classmethod
    def aware(cls,v):
        if v.tzinfo is None:raise ValueError('Aware capture timestamp required')
        return v
class ReferenceInput(StrictModel):
    client_reference_id:UUID
    captured_at:datetime
    work_area_id:UUID
    area_da:str|None=None
    default_worker_id:UUID|None=None
    canonical_key:str|None=Field(default=None,max_length=100)
    verification:str=Field(min_length=1,max_length=2000)
    effective_from:date
    replaces_id:UUID|None=None
    confirmed:Literal[True]
    @field_validator('area_da')
    @classmethod
    def area(cls,v):return None if v is None else decimal_string(v)
    @model_validator(mode='after')
    def valid(self):
        if self.captured_at.tzinfo is None or not self.verification.strip():raise ValueError('Timestamp and verification required')
        if self.area_da is None and self.default_worker_id is None:raise ValueError('Area or worker binding required')
        return self
class ComponentInput(StrictModel):
    component_id:UUID
    product_id:UUID
    location_id:UUID|None=None
    dose_per_da:str|None=None
    unit:Literal['kg','L']
    quantity_override:str|None=None
    override_reason:str|None=Field(default=None,max_length=2000)
    photo_source_id:UUID|None=None
    @field_validator('dose_per_da','quantity_override')
    @classmethod
    def qty(cls,v):return None if v is None else decimal_string(v)
    @model_validator(mode='after')
    def override(self):
        if self.quantity_override is not None and not (self.override_reason or '').strip():raise ValueError('Override reason required')
        return self
class CardInput(StrictModel):
    card_id:UUID
    work_area_id:UUID
    area_reference_id:UUID|None=None
    mix_label:str=Field(min_length=1,max_length=200)
    worker_id:UUID|None=None
    worker_reference_id:UUID|None=None
    original_instruction:str|None=Field(default=None,max_length=10000)
    rendered_text:str|None=Field(default=None,max_length=10000)
    language:Literal['fa']='fa'
    rendering_reviewed:bool=False
    components:list[ComponentInput]=Field(min_length=1,max_length=30)
    @model_validator(mode='after')
    def unique(self):
        if len({x.component_id for x in self.components})!=len(self.components):raise ValueError('Distinct component identity required')
        return self
class PlanInput(StrictModel):
    source:FormSource
    greenhouse_id:UUID
    event_date:date
    intended_send_at:datetime|None=None
    copied_from_plan_id:UUID|None=None
    copied_from_revision:int|None=Field(default=None,gt=0)
    copy_reason:str|None=Field(default=None,max_length=2000)
    cards:list[CardInput]=Field(min_length=1,max_length=50)
    @model_validator(mode='after')
    def valid(self):
        if self.intended_send_at and self.intended_send_at.tzinfo is None:raise ValueError('Aware intended time required')
        if len({c.card_id for c in self.cards})!=len(self.cards) or sum(len(c.components) for c in self.cards)>100:raise ValueError('Distinct cards and at most100 component rows required')
        if (self.copied_from_plan_id is None)!=(self.copied_from_revision is None):raise ValueError('Exact copy lineage required')
        if self.copied_from_plan_id and not (self.copy_reason or '').strip():raise ValueError('New independently reviewed copy reason required')
        return self
class RevisionInput(StrictModel):
    expected_version:int=Field(gt=0)
    input:PlanInput
class DispatchInput(StrictModel):
    client_approval_id:UUID
    captured_at:datetime
    expected_version:int=Field(gt=0)
    revision:int=Field(gt=0)
    preview_digest:str=Field(min_length=64,max_length=64)
    confirmed:Literal[True]
    @field_validator('captured_at')
    @classmethod
    def aware(cls,v):
        if v.tzinfo is None:raise ValueError('Aware capture timestamp required')
        return v
class ReportQuantity(StrictModel):
    component_id:UUID
    quantity:str|None=None
    @field_validator('quantity')
    @classmethod
    def qty(cls,v):return None if v is None else decimal_string(v,True)
class FertilizerResponseInput(StrictModel):
    client_response_id:UUID
    card_id:UUID
    expected_revision:int=Field(gt=0)
    dispatch_id:UUID
    response:Literal['DONE','PARTIAL','PROBLEM']
    captured_at:datetime
    button_label:str=Field(min_length=1,max_length=200)
    original_language:Literal['ru','tr','fa','uz']
    comment:str|None=Field(default=None,max_length=10000)
    quantities:list[ReportQuantity]=Field(default_factory=list,max_length=30)
    @model_validator(mode='after')
    def valid(self):
        if self.captured_at.tzinfo is None or not self.button_label.strip():raise ValueError('Exact button and aware timestamp required')
        if len({q.component_id for q in self.quantities})!=len(self.quantities):raise ValueError('Distinct quantities required')
        if self.response=='DONE' and self.quantities:raise ValueError('DONE proposes plan; use PARTIAL for differences')
        return self
class ActualLine(StrictModel):
    card_id:UUID
    component_id:UUID
    report_id:UUID
    quantity:str
    disposition:Literal['APPLIED','NOT_APPLIED']
    reason:str|None=Field(default=None,max_length=2000)
    @field_validator('quantity')
    @classmethod
    def qty(cls,v):return decimal_string(v,True)
    @model_validator(mode='after')
    def valid(self):
        if (Decimal(self.quantity.replace(',','.'))>0)!=(self.disposition=='APPLIED'):raise ValueError('APPLIED is positive; NOT_APPLIED is exact zero')
        if self.disposition=='NOT_APPLIED' and not (self.reason or '').strip():raise ValueError('Explicit zero requires owner reason')
        return self
class ActualInput(StrictModel):
    source:FormSource
    expected_version:int=Field(gt=0)
    revision:int=Field(gt=0)
    event_date:date
    duplicate_reviewed:bool=False
    lines:list[ActualLine]=Field(min_length=1,max_length=100)
    completed_card_ids:list[UUID]=Field(default_factory=list,max_length=50)
    @model_validator(mode='after')
    def unique(self):
        if len({(x.card_id,x.component_id) for x in self.lines})!=len(self.lines) or len(set(self.completed_card_ids))!=len(self.completed_card_ids):raise ValueError('Distinct selected scope required')
        return self
class ActualConfirm(StrictModel):
    input:ActualInput
    actual_preview_digest:str=Field(min_length=64,max_length=64)
    confirmed:Literal[True]

# New read APIs carry typed provenance rather than an unstructured workflow blob.
class ReferenceView(StrictModel):
    id:UUID
    entity_id:UUID
    work_area_id:UUID
    area_da:str|None
    default_worker_id:UUID|None
    canonical_key:str|None
    verification:str
    effective_from:date
    replaces_id:UUID|None
    created_by:UUID
    created_at:datetime
    source_snapshot:ReferenceInput
class ReportOriginal(FertilizerResponseInput):
    schema_version:Literal[1]
    source_kind:Literal['UI_ACTION']
class ReportView(StrictModel):
    id:UUID
    entity_id:UUID
    plan_id:UUID
    revision_id:UUID
    dispatch_id:UUID
    card_id:UUID
    worker_id:UUID
    actor_id:UUID
    response:Literal['DONE','PARTIAL','PROBLEM']
    source_snapshot:ReportOriginal
    received_at:datetime
class ComponentView(ComponentInput):
    product_name:str
    location_name:str|None
    calculated_quantity:str|None
    proposed_quantity:str|None
    quantity_basis:Literal['UNKNOWN','OWNER_OVERRIDE','DOSE_TIMES_AREA']
    photo_url:str|None=None
class CardView(CardInput):
    components:list[ComponentView]
    greenhouse_name:str
    part_name:str
    worker_name:str|None
    area_da:str|None
    area_provenance:ReferenceView|None
    prepared_instruction:str
    issues:list[str]
    current_report:ReportView|None=None
class ProductTotal(StrictModel):
    product_id:UUID
    unit:Literal['kg','L']
    quantity:str|None
class GreenhouseView(StrictModel):
    id:UUID
    name:str
class AreaView(GreenhouseView):
    kind:str
    parent_id:UUID|None
class PlanPreview(StrictModel):
    input:PlanInput
    cards:list[CardView]
    totals:list[ProductTotal]
    issues:list[str]
    preview_digest:str
    can_dispatch:bool
    greenhouse:AreaView
class DispatchView(StrictModel):
    id:UUID
    entity_id:UUID
    plan_id:UUID
    revision_id:UUID
    created_by:UUID
    created_at:datetime
    source_snapshot:DispatchInput
class ActualView(StrictModel):
    id:UUID
    entity_id:UUID
    plan_id:UUID
    revision_id:UUID
    source_id:UUID
    application_id:UUID
    input:ActualInput
    source_snapshot:ActualConfirm
    preview_digest:str
    created_by:UUID
    created_at:datetime
    inventory_status:str|None=None
    replacement_id:UUID|None=None
    current_application_id:UUID|None=None
    current_inventory_status:str|None=None
    application_history:list[dict]=Field(default_factory=list)
class ProposedActualLine(StrictModel):
    card_id:UUID
    component_id:UUID
    report_id:UUID|None
    quantity:str|None
    quantity_basis:Literal['WORKER_REPORTED','PLAN_CANDIDATE','UNKNOWN']
    product_id:UUID
    product_name:str
    unit:Literal['kg','L']
    resolution_required:bool
    remaining_status:str|None=None
class RevisionView(StrictModel):
    revision:int
    preview_digest:str
    input:PlanInput
    created_at:datetime
    created_by:UUID
class PlanView(StrictModel):
    late_evidence_count:int
    late_evidence_review_supported:Literal[False]
    id:UUID
    entity_id:UUID
    revision:int
    version:int
    status:Literal['DRAFT','APPROVED','PARTIALLY_APPLIED','APPLIED','ACTUAL_REVERSED','ACTUAL_CORRECTED']
    input:PlanInput
    source_snapshot:FormSource
    preview_digest:str
    cards:list[CardView]
    totals:list[ProductTotal]
    issues:list[str]
    can_dispatch:bool
    current_dispatch_id:UUID|None
    dispatches:list[DispatchView]
    reports:list[ReportView]
    actual:ActualView|None
    revisions:list[RevisionView]
    proposed_actual_lines:list[ProposedActualLine]
    remainder:list[ProposedActualLine]
    subsequent_scope_supported:Literal[False]
class ActualPreview(StrictModel):
    input:ActualInput
    actual_preview_digest:str
    selected_scope:list[ActualLine]
    totals:list[ProductTotal]
    zero_lines:list[ActualLine]
    remainder:list[ProposedActualLine]
    issues:list[str]
    can_confirm:bool
    inventory_effect:dict
class WorkerPlanView(StrictModel):
    current_revision:int
    can_respond:bool
    id:UUID
    plan_id:UUID
    revision:int
    version:int
    dispatch_id:UUID
    event_date:date
    greenhouse:GreenhouseView
    cards:list[CardView]
    reports:list[ReportView]
class WorkerCalendarView(StrictModel):
    items:list[WorkerPlanView]
    attention:int
    filters:dict
