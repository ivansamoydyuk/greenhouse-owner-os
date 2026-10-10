import copy
from datetime import date
from uuid import uuid4
import pytest
from pydantic import ValidationError
from greenhouse_owner_os.domain.harvest import ReportInput
from greenhouse_owner_os.domain.inventory import InventoryInput
from greenhouse_owner_os.domain.owner_form_source import OwnerFormSource,encode_original,decode_original
from greenhouse_owner_os.services.owner_preparation import prepare_quick_expense

def form(kind,fields):
    return dict(schema_version=1,form_kind=kind,form_id=str(uuid4()),captured_at='2026-10-10T18:05:00+03:00',timezone='Europe/Istanbul',fields=fields)

def harvest():
    fields=dict(event_date='2026-10-10',original_greenhouse='İmamlı',work_area_id='',cut_count='135',field_weight_kg='',reporter='TEST Owner',notes='')
    original=form('field_harvest',fields)
    return dict(event_date='2026-10-10',original_greenhouse='İmamlı',cut_count=135,reporter='TEST Owner',evidence=dict(source='owner_form:v1:field_harvest',source_reference=original['form_id'],structured_original=original))

def receipt(product=None,location=None):
    product=product or str(uuid4());location=location or str(uuid4())
    fields=dict(event_date='2026-10-10',product_id=product,original_product_name='TEST Fertilizer',location_id=location,quantity='120.00',unit='kg',notes='')
    original=form('inventory_receipt',fields)
    return dict(kind='receipt',event_date='2026-10-10',lines=[dict(product_id=product,original_product_name='TEST Fertilizer',location_id=location,quantity='120',unit='kg')],evidence=dict(source='owner_form:v1:inventory_receipt',locator=original['form_id'],structured_original=original))

def test_structured_original_preserves_lexical_values_and_does_not_decode_prose():
    r=InventoryInput.model_validate(receipt())
    assert r.evidence.structured_original.fields['quantity']=='120.00' and r.lines[0].quantity=='120'
    h=ReportInput.model_validate(harvest()).evidence.structured_original
    assert decode_original('owner_form:v1:field_harvest',encode_original(h))==h.model_dump(mode='json')
    assert decode_original('legacy',encode_original(h)) is None
    assert decode_original('legacy','{"form_kind":"field_harvest"}') is None

@pytest.mark.parametrize('change',[lambda b:b.update(cut_count=136),lambda b:b.update(original_greenhouse='Other'),lambda b:b['evidence'].update(raw_text='fabricated'),lambda b:b['evidence'].update(existing_id=str(uuid4())),lambda b:b['evidence'].update(source='legacy'),lambda b:b['evidence']['structured_original']['fields'].update(extra='x'),lambda b:b['evidence']['structured_original'].update(schema_version=2),lambda b:b['evidence']['structured_original'].update(captured_at='2026-10-10T18:05:00')])
def test_invalid_harvest_form_rejected(change):
    b=harvest();change(b)
    with pytest.raises(ValidationError):ReportInput.model_validate(b)

@pytest.mark.parametrize('key,value',[('quantity','121'),('unit','L'),('location_id',str(uuid4())),('product_id',str(uuid4()))])
def test_receipt_command_cannot_disagree_with_original(key,value):
    b=receipt();b['lines'][0][key]=value
    with pytest.raises(ValidationError):InventoryInput.model_validate(b)

@pytest.mark.parametrize('raw',['8500 насос ремонт',' 8500 ремонт насоса ','8500,50 насос ремонт'])
def test_narrow_quick_expense_does_not_infer_payment(raw):
    kind,payload,prov=prepare_quick_expense(raw,today=date(2026,10,10))[0]
    assert kind=='DIRECT_EXPENSE' and payload['description']=='Ремонт насоса'
    assert payload['money_account_id'] is None and payload['payment_purpose'] is None
    assert prov['amount']['category']=='SOURCE FACT' and prov['description']['category']=='INFERENCE'
    assert prov['currency']['basis']==prov['event_date']['basis']=='visible_context_default'
    assert all(p['source_text']==raw for p in prov.values())

@pytest.mark.parametrize('raw',['8500 кг насос ремонт','не платил 8500 насос ремонт','8500 насос ремонт завтра','8500 насос ремонт; ещё 50','8500 насос ремонт USD','Серкан сказал «8500 насос ремонт»','0 насос ремонт','-8500 насос ремонт','8500 8600 насос ремонт'])
def test_ambiguous_quick_expense_not_narrowly_inferred(raw):
    assert prepare_quick_expense(raw,today=date(2026,10,10)) is None

def test_decimal_comma_comparison_preserves_exact_original():
    b=receipt();b['evidence']['structured_original']['fields']['quantity']='120,00'
    assert InventoryInput.model_validate(b).evidence.structured_original.fields['quantity']=='120,00'
    h=harvest();h['field_weight_kg']='1.5';h['evidence']['structured_original']['fields']['field_weight_kg']='1,5'
    assert str(ReportInput.model_validate(h).field_weight_kg)=='1.5'

@pytest.mark.parametrize('extra',[{'original_quantity':'999'},{'original_unit':'L'},{'unit_cost':'0'},{'currency':'TRY'}])
def test_receipt_cannot_assert_extra_original_or_cost(extra):
    b=receipt();b['lines'][0].update(extra)
    with pytest.raises(ValidationError):InventoryInput.model_validate(b)
