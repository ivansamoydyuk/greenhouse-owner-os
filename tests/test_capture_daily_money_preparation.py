"""Bounded daily-money candidates preserve ambiguity and original exclusions."""
from datetime import date
from uuid import uuid4
import pytest
from greenhouse_owner_os.domain.owner_capture import CaptureSource, DTO
from greenhouse_owner_os.services.owner_preparation import prepare_owner_source, nominal_money_candidate, invoice_supplier_candidates

DAY=date(2026,10,12)
PARTY=str(uuid4())
REFS={'counterparties':[{'id':PARTY,'name':'Серкан','aliases':['Серкану']}]}


def test_nominal_note_keeps_facts_without_payment_status():
    raw='Серкан, ремонт насоса, 5 000 TRY.'
    slots=prepare_owner_source(raw,today=DAY,refs=REFS)
    assert len(slots)==1 and slots[0][0]=='UNRESOLVED'
    kind,payload,provenance=slots[0];DTO[kind].model_validate(payload)
    f=payload['money_candidates']
    assert (f['amount'],f['currency'],f['counterparty_id'],f['description'])==('5000','TRY',PARTY,'ремонт насоса')
    assert f['payment_purpose'] is f['money_account_id'] is f['obligation_id'] is None
    assert provenance['money_candidates']['basis']=='payment_status_unknown'
    assert provenance['money_candidates']['fields']['event_date']['category']=='CONTEXT DEFAULT'


@pytest.mark.parametrize('raw',[
    'Завтра нужно заплатить Серкану 5 000 TRY за ремонт.',
    'Завтра нужно оплатить Серкану 5 000 TRY за ремонт.',
    'Не заплатил Серкану 5 000 TRY за ремонт.',
    'Умар написал: «Серкан, ремонт насоса, 5 000 TRY.»',
    'Завтра Серкан, ремонт насоса, 5 000 TRY.',
    'Серкан, не ремонт насоса, 5 000 TRY.',
])
def test_plans_negation_and_reported_speech_never_gain_candidate_recovery(raw):
    assert nominal_money_candidate(raw,today=DAY,refs=REFS) is None
    assert not any(kind in ('PAYABLE','PAYMENT','DIRECT_EXPENSE') for kind,_,_ in prepare_owner_source(raw,today=DAY,refs=REFS))


def test_current_debt_remains_debt_with_future_payment_date():
    assert prepare_owner_source('Должен Серкану 5000 TRY за ремонт, завтра нужно заплатить.',today=DAY,refs=REFS)[0][0]=='PAYABLE'


def test_supplier_label_does_not_match_shared_document_number_prefix():
    parties=[{'id':str(uuid4()),'name':'SYN Gübre Tedarik'},{'id':str(uuid4()),'name':'SYN Another Supplier'}]
    raw='FATURA\nTarih:12.10.2026 Fatura No:SYN-P-001\nSatıcı: SYN Gübre Tedarik\nKNO3 100 kg\nTutar 500 TRY'
    assert invoice_supplier_candidates(raw,parties)==[parties[0]]
    assert invoice_supplier_candidates(raw+'\nSeller: SYN Another Supplier',parties)==[]
    p=prepare_owner_source(raw,today=DAY,refs={'counterparties':parties},binary=True)[0]
    assert p[0]=='PAYABLE' and p[1]['counterparty_id']==parties[0]['id']


def test_supplier_intake_can_save_before_financial_intent():
    p=CaptureSource(client_capture_id=uuid4(),captured_at='2026-10-12T12:00:00+03:00',text='FATURA',capture_context='supplier_invoice',invoice_receipt_id=uuid4())
    assert p.invoice_intent is None
