"""Bounded explicit document enrichment, with independent real OCR inputs."""
from datetime import date
from uuid import uuid4
import pytest
from greenhouse_owner_os.domain.owner_capture import DTO
from greenhouse_owner_os.services.owner_preparation import prepare_context_document
from greenhouse_owner_os.services.ocr import LocalOCR
from tests.ocr_corpus import document

DAY=date(2026,10,12)
DEPOT='Görgün\n08.10.2026\nGrand 100 20 2000\nBirinci 50 15 750\nİkinci 25 10 250\nToplam 3000\nAlacak 3000'
PARTY=str(uuid4())
REFS={'counterparties':[{'id':PARTY,'name':'Görgün','counterparty_type':'depot'}]}


def prepare(raw=DEPOT,**changes):
    args=dict(today=DAY,refs=REFS,context='depot_receipt',binary=True,recognition_state='SUCCEEDED',engine_version='TEST declared OCR derivative')
    args.update(changes)
    return prepare_context_document(raw,**args)


@pytest.mark.parametrize('n',[0,27])
def test_actual_png_pdf_context_depot_recognition_prefills_without_authority(n):
    content,mime=document(n);ocr=LocalOCR().recognize(content,mime)
    kind,payload,prov=prepare(ocr['preparation_text'],pages=ocr['pages'],warnings=ocr['warnings'],engine_version=ocr['version'])[0]
    DTO[kind].model_validate(payload)
    assert kind=='DEPOT_RECEIPT' and len(payload['lines'])==3
    assert payload['depot_id']==PARTY and payload['event_date']=='2026-10-08'
    assert [l['net_weight_kg'] for l in payload['lines']]==['100','50','25']
    assert payload['quality_reviewed'] is payload['arithmetic_reviewed'] is False
    assert payload['batch_ids']==[] and payload['alacak_value']=='3000'
    assert prov['lines']['source_layer']=='OCR' and prov['lines']['pages']
    assert prov['lines']['engine_version']==LocalOCR.version


def test_caption_cannot_replace_document_date_weight_or_infer_payment():
    kind,f,p=prepare(caption='12.10.2026. Заплатил 15000 TRY. Grand 999 9 8991')[0]
    assert kind=='DEPOT_RECEIPT' and f['event_date']=='2026-10-08'
    assert f['lines'][0]['net_weight_kg']=='100' and f['alacak_value']=='3000'
    assert f['document_reviewed'] is False
    assert p['document_reviewed']['owner_caption'].startswith('12.10.2026')
    assert p['lines']['source_text']==DEPOT


def test_recognition_failure_keeps_same_manual_slot_without_caption_authority():
    kind,f,p=prepare('',recognition_state='FAILED',caption=DEPOT,warnings=['TEST failure'])[0]
    assert kind=='DEPOT_RECEIPT' and f['lines']==[] and f['depot_id'] is None
    assert p['lines']['source_layer']=='MANUAL_REQUIRED'
    assert 'document_reviewed' not in f


def test_ambiguous_document_stays_contextual_manual_not_new_financial_sibling():
    slots=prepare(DEPOT+'\nЯ заплатил 9000 TRY за долг.')
    assert [s[0] for s in slots]==['DEPOT_RECEIPT']
    assert slots[0][1]['lines']==[] and slots[0][1]['document_reviewed'] is False


@pytest.mark.parametrize('intent,expected',[('operating_expense',['PAYABLE','INVOICE_MATCH']),('existing_payable',['INVOICE_MATCH'])])
def test_invoice_context_retains_only_authorized_slots_and_receipt_context(intent,expected):
    receipt=str(uuid4());raw='ACME FATURA\n08.10.2026\nKNO3 100 kg\nTutar 5000 TRY\nÖdeme yapılmadı'
    slots=prepare(raw,context='supplier_invoice',invoice_intent=intent,receipt_id=receipt,caption='TEST separate owner note')
    assert [s[0] for s in slots]==expected
    for kind,payload,_ in slots:DTO[kind].model_validate(payload)
    match=slots[-1]
    assert match[1]['receipt_id']==receipt and match[1]['finance_obligation_id'] is None
    assert match[2]['receipt_id']['category']=='OWNER CONTEXT'
    if intent=='operating_expense':
        f,p=slots[0][1:]
        assert (f['amount'],f['currency'],f['event_date'])==('5000','TRY','2026-10-08')
        assert f['counterparty_id'] is f['money_account_id'] is f['payment_purpose'] is None
        assert f['document_reviewed'] is False and p['amount']['source_layer']=='OCR'
