"""Bounded day-labor forms. Exact lexical fields remain immutable evidence."""
from datetime import date
from decimal import Decimal
import re
import json
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from .commands import StrictModel
from .owner_form_source import OwnerFormSource


def positive_integer(value: str) -> int:
    if not re.fullmatch(r'[0-9]{1,6}', value) or not 0 < int(value) <= 100000:
        raise ValueError('Positive whole people/day count required')
    return int(value)


def actual_people(value: str) -> int:
    if not re.fullmatch(r'[0-9]{1,6}',value) or not 0 <= int(value) <= 100000:
        raise ValueError('Explicit nonnegative whole actual headcount required')
    return int(value)


def exact_rate(value: str) -> Decimal:
    if not re.fullmatch(r'[0-9]{1,12}(?:[.,][0-9]{1,2})?', value) or Decimal(value.replace(',', '.')) <= 0:
        raise ValueError('Explicit positive full-day rate required')
    return Decimal(value.replace(',', '.'))


def calendar_date(value: str) -> date:
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):
        raise ValueError('Explicit ISO calendar date YYYY-MM-DD required')
    return date.fromisoformat(value)


def plan_fields(fields):
    return dict(people=positive_integer(fields['people']), days=positive_integer(fields['days']),
                work=fields['work'], start_date=calendar_date(fields['start_date']) if fields['start_date'] else None,
                note=fields['note'])


class SourceInput(StrictModel):
    source: OwnerFormSource


class RequestInput(SourceInput):
    greenhouse_id: UUID
    @model_validator(mode='after')
    def valid(self):
        if self.source.form_kind != 'day_request': raise ValueError('Day request form required')
        plan_fields(self.source.fields)
        if not self.source.fields['work'].strip(): raise ValueError('Work description required')
        return self


class DecisionInput(SourceInput):
    expected_version: int = Field(gt=0)
    @model_validator(mode='after')
    def valid(self):
        f=self.source.fields
        if self.source.form_kind != 'day_decision' or f['decision'] not in ('APPROVE','REVISE_APPROVE','REJECT','CLARIFY'):
            raise ValueError('Supported owner decision required')
        if f['start_date']:calendar_date(f['start_date'])
        if f['decision'] in ('APPROVE','REVISE_APPROVE'):
            plan_fields(f)
            if not f['work'].strip(): raise ValueError('Work description required')
        elif not f['reason'].strip(): raise ValueError('Short rejection or clarification reason required')
        return self


class ClarificationInput(SourceInput):
    expected_version: int = Field(gt=0)
    @model_validator(mode='after')
    def valid(self):
        if self.source.form_kind != 'day_clarification' or not self.source.fields['text'].strip():
            raise ValueError('Clarification text required')
        return self


class ActualAllocation(StrictModel):
    greenhouse_id: UUID
    work_area_id: UUID | None = None
    people: int = Field(gt=0,le=100000,strict=True)


def parse_allocations(value):
    raw=json.loads(value)
    if not isinstance(raw,list) or len(raw)>100: raise ValueError('Bounded explicit allocation rows required')
    result=[ActualAllocation.model_validate(x) for x in raw]
    if len({(x.greenhouse_id,x.work_area_id) for x in result})!=len(result): raise ValueError('One explicit row per greenhouse/part required')
    return result


class DayWorkersActualInput(SourceInput):
    greenhouse_id: UUID | None = None
    request_id: UUID | None = None
    replaces_id: UUID | None = None
    @model_validator(mode='after')
    def valid(self):
        f=self.source.fields
        if self.source.form_kind != 'day_actual': raise ValueError('Day actual form required')
        actual_people(f['people']); calendar_date(f['event_date'])
        allocations=parse_allocations(f['allocations'])
        if sum(x.people for x in allocations)>actual_people(f['people']): raise ValueError('Allocation exceeds actual crew headcount')
        if not f['work'].strip() or f['full_day'] not in ('true','false','unknown'):
            raise ValueError('Work and explicit full-day coverage required')
        return self


# Internal compatibility; the unique class name preserves Fertilizer OpenAPI identity.
ActualInput = DayWorkersActualInput


class ConfirmationInput(SourceInput):
    confirmed: Literal[True]
    @model_validator(mode='after')
    def valid(self):
        if self.source.form_kind != 'day_confirmation': raise ValueError('Owner fact confirmation form required')
        return self


class AccrualInput(SourceInput):
    confirmed: Literal[True]
    @model_validator(mode='after')
    def valid(self):
        f=self.source.fields
        if self.source.form_kind != 'day_accrual': raise ValueError('Day accrual form required')
        exact_rate(f['daily_rate']); UUID(f['counterparty_id']); UUID(f['expense_account_id'])
        if f['currency'] not in ('TRY','USD','EUR','UAH') or not f['rate_basis'].strip():
            raise ValueError('Known currency and explicit rate provenance required')
        if f['due_date']: calendar_date(f['due_date'])
        return self


class ScopeInput(SourceInput):
    @model_validator(mode='after')
    def valid(self):
        f=self.source.fields
        if self.source.form_kind != 'day_scope' or f['action'] not in ('GRANT','REVOKE') or not f['reason'].strip():
            raise ValueError('Explicit verified scope decision required')
        UUID(f['worker_id']); UUID(f['greenhouse_id'])
        return self
