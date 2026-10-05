"""Physical units and reviewable originals; no guessed conversions."""
import re,base64
from datetime import date,datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field,field_validator,model_validator
from .commands import StrictModel

Unit=Literal['kg','L','piece','bag','container','package']
def exact(v):
    if not isinstance(v,str) or not re.fullmatch(r'\d{1,18}(?:\.\d{1,12})?',v) or Decimal(v)>=Decimal('1e18'):raise ValueError('Use exact decimal strings, maximum 12 decimal places')
    result=format(Decimal(v),'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result

class ProductInput(StrictModel):
    name:str=Field(min_length=1,max_length=200)
    aliases:list[str]=Field(default_factory=list,max_length=30)
    category:Literal['fertilizer','biostimulant','biological','crop_protection','other']='fertilizer'
    base_unit:Literal['kg','L','piece']
    currency:Literal['TRY','USD','EUR','UAH']|None=None
    supplier_reference:str|None=Field(default=None,max_length=500)
    source:str=Field(min_length=1,max_length=2000)
    @field_validator('name')
    @classmethod
    def name_valid(cls,v):
        if not v.strip():raise ValueError('Name required')
        return v.strip()
    @field_validator('aliases')
    @classmethod
    def aliases_valid(cls,v):
        if any(not s.strip() or len(s)>200 for s in v) or len(set(s.casefold() for s in v))!=len(v):raise ValueError('Distinct bounded aliases required')
        return v

class ConversionInput(StrictModel):
    unit:Unit
    factor:str
    verification:str=Field(min_length=1,max_length=2000)
    @field_validator('factor',mode='before')
    @classmethod
    def factor_valid(cls,v):
        v=exact(v)
        if Decimal(v)<=0:raise ValueError('Positive verified factor required')
        return v

class LocationInput(StrictModel):
    name:str=Field(min_length=1,max_length=200)
    work_area_id:UUID|None=None
    source:str=Field(min_length=1,max_length=2000)

class InventoryLine(StrictModel):
    product_id:UUID|None=None
    original_product_name:str=Field(min_length=1,max_length=200)
    quantity:str|None=None
    unit:Unit|None=None
    original_unit:str|None=Field(default=None,max_length=100)
    original_quantity:str|None=Field(default=None,max_length=200)
    location_id:UUID|None=None
    destination_id:UUID|None=None
    greenhouse_id:UUID|None=None
    work_area_id:UUID|None=None
    unit_cost:str|None=None # explicitly per BASE physical unit, never bag cost
    currency:Literal['TRY','USD','EUR','UAH']|None=None
    @field_validator('quantity','unit_cost',mode='before')
    @classmethod
    def quantities(cls,v):return None if v is None else exact(v)
    @model_validator(mode='after')
    def qty_positive(self):
        if self.quantity is not None and Decimal(self.quantity)<=0:raise ValueError('Quantity must be positive')
        return self

class InventoryEvidence(StrictModel):
    source:str=Field(default='owner_mobile:inventory',min_length=1,max_length=200)
    locator:str|None=Field(default=None,min_length=1,max_length=2000)
    raw_text:str|None=Field(default=None,max_length=10000)
    file_base64:str|None=Field(default=None,max_length=7000000)
    filename:str|None=Field(default=None,max_length=200)
    mime_type:Literal['image/jpeg','image/png','application/pdf']|None=None
    source_file:str|None=Field(default=None,max_length=2000)
    source_row:str|None=Field(default=None,max_length=2000)
    source_document:str|None=Field(default=None,max_length=2000)
    import_batch_id:UUID|None=None
    imported_at:datetime|None=None
    confidence:Literal['LOW','MEDIUM','HIGH']|None=None
    verification:Literal['VERIFY','VERIFIED','HISTORY']='VERIFY'
    @model_validator(mode='after')
    def binary(self):
        if self.filename and re.search(r'[/\\\x00-\x1f\x7f]',self.filename):raise ValueError('Unsafe filename')
        if self.file_base64:
            try:b=base64.b64decode(self.file_base64,validate=True)
            except ValueError:raise ValueError('Invalid file encoding')
            signatures={'application/pdf':b'%PDF-','image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8\xff'}
            if not self.filename or self.mime_type not in signatures or not b.startswith(signatures[self.mime_type]) or len(b)>5242880:raise ValueError('Invalid original file, maximum 5 MB')
        return self

class InventoryInput(StrictModel):
    kind:Literal['receipt','application','transfer','writeoff','plan']
    event_date:date|None=None
    lines:list[InventoryLine]=Field(min_length=1,max_length=100)
    supplier_id:UUID|None=None
    finance_obligation_id:UUID|None=None
    method:str|None=Field(default=None,max_length=500)
    notes:str|None=Field(default=None,max_length=2000)
    evidence:InventoryEvidence=Field(default_factory=InventoryEvidence)
    replacement_of_id:UUID|None=None

class InventoryPost(StrictModel):
    duplicate_reviewed:bool=False
    confirmed:Literal[True]
    expected_version:int=Field(gt=0)

class InventoryReverse(StrictModel):
    event_date:date
    reason:str=Field(min_length=1,max_length=2000)
