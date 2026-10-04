"""Non-posting import commands: verification describes evidence, never permission to post."""
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from .commands import StrictModel


class VerificationState(StrEnum):
    VERIFIED = 'VERIFIED'
    VERIFY = 'VERIFY'
    HISTORY = 'HISTORY'


class ImportBatchCommand(StrictModel):
    entity_id: UUID
    source: str = Field(min_length=1, max_length=200)
    source_file: str = Field(min_length=1, max_length=2000)
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')

    @field_validator('source', 'source_file')
    @classmethod
    def not_blank(cls, value):
        if not value.strip(): raise ValueError('source cannot be blank')
        return value


class HistoricalCandidateCommand(StrictModel):
    source_row: str | None = Field(default=None, min_length=1, max_length=2000)
    source_document: str | None = Field(default=None, min_length=1, max_length=2000)
    event_date: date | None = None
    verification_state: VerificationState = VerificationState.HISTORY
    confidence: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=6)
    raw_value: dict[str, Any] | None = None
    source_reference: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator('source_row', 'source_document', 'source_reference')
    @classmethod
    def not_blank(cls, value):
        if value is not None and not value.strip(): raise ValueError('source locator/reference cannot be blank')
        return value

    @model_validator(mode='after')
    def evidence_required(self):
        if not (self.source_row or self.source_document): raise ValueError('source_row or source_document required')
        if self.raw_value is None and self.source_reference is None: raise ValueError('raw_value or source_reference required')
        return self
