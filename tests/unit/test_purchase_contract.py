import copy
from uuid import uuid4
import pytest
from pydantic import ValidationError
from greenhouse_owner_os.domain.owner_capture import CaptureSource
from greenhouse_owner_os.domain.inventory import InventoryInput
from greenhouse_owner_os.domain.owner_form_source import OwnerFormSource


def capture(**changes):
    return dict(client_capture_id=uuid4(),captured_at='2026-10-06T12:00:00+03:00',text='TEST original invoice',**changes)


def test_invoice_context_requires_explicit_intent_and_receipt_and_legacy_shape_unchanged():
    assert 'invoice_intent' not in CaptureSource(**capture()).model_dump(mode='json')
    for intent in ('operating_expense','existing_payable'):
        r=CaptureSource(**capture(capture_context='supplier_invoice',invoice_intent=intent,invoice_receipt_id=uuid4()))
        assert r.invoice_intent==intent
    for changes in (dict(capture_context='supplier_invoice'),dict(invoice_intent='existing_payable'),dict(capture_context='supplier_invoice',invoice_intent='asset',invoice_receipt_id=uuid4())):
        with pytest.raises(ValidationError):CaptureSource(**capture(**changes))


def test_transfer_original_preserves_exact_strings_and_destination_binding():
    pid,src,dst,form=[str(uuid4()) for _ in range(4)]
    original=dict(schema_version=1,form_kind='inventory_transfer',form_id=form,captured_at='2026-10-06T12:00:00+03:00',timezone='Europe/Istanbul',
        fields=dict(event_date='2026-10-06',product_id=pid,original_product_name='KNO3',location_id=src,destination_id=dst,quantity='20,00',unit='kg',notes='  Original note  '))
    body=dict(kind='transfer',event_date='2026-10-06',lines=[dict(product_id=pid,original_product_name='KNO3',location_id=src,destination_id=dst,quantity='20',unit='kg')],evidence=dict(source='owner_form:v1:inventory_transfer',locator=form,structured_original=original))
    parsed=InventoryInput(**body)
    assert parsed.evidence.structured_original.fields['quantity']=='20,00'
    assert parsed.evidence.structured_original.fields['notes']=='  Original note  '
    for key,value in [('destination_id',src),('location_id',dst),('quantity','21'),('unit','L')]:
        changed=copy.deepcopy(body);changed['lines'][0][key]=value
        with pytest.raises(ValidationError):InventoryInput(**changed)
    changed=copy.deepcopy(body);changed['evidence']['raw_text']='Invented spoken original'
    with pytest.raises(ValidationError):InventoryInput(**changed)


def test_purchase_and_history_openapi_are_read_only_typed_runtime_contracts():
    from pathlib import Path
    import yaml
    from greenhouse_owner_os.api.app import create_app
    runtime=create_app(finance_service=None,reference_service=object(),actor_provider=lambda:None).openapi()
    artifact=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    paths={p:v for p,v in runtime['paths'].items() if '/purchases' in p or '/inventory/history' in p}
    assert len(paths)==4 and all(set(v)=={'get'} for v in paths.values())
    for path,value in paths.items():assert artifact['paths'][path.removeprefix('/v1')]==value
    for name in ('PurchaseHistory','PurchaseReceipt','InventoryHistory','InventoryHistoryEvent','OwnerFormSource','CaptureSource','InvoiceWorkflow','InvoiceProposalState'):
        assert artifact['components']['schemas'][name]==runtime['components']['schemas'][name]
