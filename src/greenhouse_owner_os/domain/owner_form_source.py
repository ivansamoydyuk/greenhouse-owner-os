"""Versioned owner-entered form evidence; never a generated spoken original."""
import json
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from .commands import StrictModel

PREFIX = 'GREENHOUSE_OWNER_FORM_V1\n'
SOURCE_PREFIX = 'owner_form:v1:'
FIELDS = {
    'field_harvest': {'event_date','original_greenhouse','work_area_id','cut_count','field_weight_kg','reporter','notes'},
    'inventory_receipt': {'event_date','product_id','original_product_name','location_id','quantity','unit','notes'},
}

class OwnerFormSource(StrictModel):
    schema_version: Literal[1]
    form_kind: Literal['field_harvest','inventory_receipt']
    form_id: UUID
    captured_at: str = Field(min_length=1,max_length=80)
    timezone: Literal['Europe/Istanbul']
    fields: dict[str,str]

    @field_validator('captured_at')
    @classmethod
    def zoned_timestamp(cls,value):
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError('Capture timestamp must include an offset')
        return value

    @model_validator(mode='after')
    def bounded_fields(self):
        if set(self.fields)!=FIELDS[self.form_kind]:raise ValueError('Exact versioned form field set required')
        if any(len(value)>2000 for value in self.fields.values()):raise ValueError('Form value exceeds evidence limit')
        if len(json.dumps(self.model_dump(mode='json'),ensure_ascii=False))>9000:raise ValueError('Form source exceeds bounded envelope')
        return self


def form_decimal(value):
    return value.replace(',','.') if value else None


def validate_binding(evidence,kind,locator):
    original=evidence.structured_original
    if original is None:
        if evidence.source.startswith(SOURCE_PREFIX):raise ValueError('Reserved owner form source requires typed evidence')
        return
    if original.form_kind!=kind or evidence.source!=SOURCE_PREFIX+kind or locator!=str(original.form_id):
        raise ValueError('Typed original form identity does not match source')
    if evidence.raw_text is not None or getattr(evidence,'existing_id',None) is not None:
        raise ValueError('Typed form cannot be combined with text or reused evidence identity')


def encode_original(original):
    return PREFIX+json.dumps(original.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':'))


def decode_original(source,raw_text):
    """Only our reserved storage namespace is decoded; JSON prose stays prose."""
    if source!=SOURCE_PREFIX+'field_harvest' or not isinstance(raw_text,str) or not raw_text.startswith(PREFIX):return None
    original=OwnerFormSource.model_validate_json(raw_text[len(PREFIX):])
    if original.form_kind!='field_harvest':raise ValueError('Stored owner form namespace mismatch')
    return original.model_dump(mode='json')


def original_readback(result):
    original=decode_original(result.get('source'),result.get('raw_text'))
    if original is not None:
        result=dict(result,raw_text=None,structured_original=original)
    return result
