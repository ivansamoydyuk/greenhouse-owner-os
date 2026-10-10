"""Owner-confirmed append-only Harvest review, never a second financial effect."""
from decimal import Decimal, ROUND_FLOOR
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from .harvest import ExactModel, EvidenceInput

class JourneyCommand(ExactModel):
    expected_version: int = Field(ge=0)
    confirmed: Literal[True]
    reason: str = Field(min_length=1,max_length=2000)
    @model_validator(mode='after')
    def reason_required(self):
        if not self.reason.strip(): raise ValueError('Explain the owner decision')
        return self

class JourneyLinks(JourneyCommand):
    batch_ids: tuple[UUID,...] = Field(max_length=100)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.batch_ids))!=len(self.batch_ids):raise ValueError('Repeated batch')
        return self

class EstimateBasis(ExactModel):
    work_area_id: UUID
    basis_count: Decimal = Field(gt=0,max_digits=18,decimal_places=3)

class EstimatePreview(ExactModel):
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1,max_length=2000)
    basis: tuple[EstimateBasis,...] = Field(min_length=1,max_length=100)
    total_basis_count: Decimal = Field(gt=0,max_digits=18,decimal_places=3)
    basis_description: str = Field(min_length=1,max_length=2000)
    @model_validator(mode='after')
    def complete(self):
        if len({x.work_area_id for x in self.basis})!=len(self.basis):raise ValueError('Repeated area')
        if sum((x.basis_count for x in self.basis),Decimal(0))>self.total_basis_count:raise ValueError('Basis exceeds explicit total')
        if not self.basis_description.strip():raise ValueError('Explain approximate basis')
        return self

class JourneyEstimate(EstimatePreview):
    confirmed: Literal[True]

class JourneyAttachment(JourneyCommand):
    evidence: EvidenceInput
    @model_validator(mode='after')
    def independent_original(self):
        if self.evidence.structured_original:raise ValueError('Field form is not a depot original')
        return self

ALGORITHM='largest-remainder-per-line-v1'

def apportion(total,quantum,basis,total_basis):
    """Exact Decimal, stable UUID ties; None denotes explicitly unallocated basis."""
    total,quantum,total_basis=Decimal(total),Decimal(quantum),Decimal(total_basis)
    weights={str(k):Decimal(v) for k,v in basis.items()}
    unknown=total_basis-sum(weights.values(),Decimal(0))
    if total_basis<=0 or unknown<0 or any(v<=0 for v in weights.values()):raise ValueError('Invalid allocation basis')
    # Place the explicit unallocated bucket after all UUIDs on exact ties.
    if unknown:weights['~unallocated']=unknown
    units=total/quantum
    if units!=units.to_integral_value():raise ValueError('Unsupported precision')
    # Integer quotient/remainder avoids Decimal context rounding at maximum sizes.
    denominator=int(total_basis*1000)
    parts={k:divmod(int(units)*int(w*1000),denominator) for k,w in weights.items()}
    whole={k:v[0] for k,v in parts.items()}
    remainder=int(units)-sum(whole.values())
    for k in sorted(parts,key=lambda k:(-parts[k][1],k))[:remainder]:whole[k]+=1
    return {None if k=='~unallocated' else k:Decimal(v)*quantum for k,v in whole.items()}
