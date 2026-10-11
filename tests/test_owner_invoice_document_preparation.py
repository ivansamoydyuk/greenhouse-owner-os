"""Real document pixels and independent controls for invoice-heading evidence."""
from datetime import date
import io

from PIL import Image, ImageDraw, ImageFont
import pytest

from greenhouse_owner_os.domain.owner_capture import DTO
from greenhouse_owner_os.services.ocr import LocalOCR
from greenhouse_owner_os.services.owner_preparation import prepare_owner_source, invoice_document_evidence
from tests.ocr_corpus import document

DAY = date(2026, 10, 9)
BODY = '\n08.10.2026\nKNO3 100 kg\nTutar 5000 TRY\nÖdeme yapılmadı'


def rendered_pdf(heading):
    picture = Image.new('RGB', (1100, 800), 'white')
    font = ImageFont.truetype('DejaVuSans.ttf', 38)
    ImageDraw.Draw(picture).multiline_text((50, 40), heading + BODY, font=font, fill='black', spacing=20)
    stream = io.BytesIO()
    picture.save(stream, format='PDF', save_all=True, append_images=[picture])
    return stream.getvalue(), 'application/pdf'


@pytest.mark.parametrize('heading', [None, 'ACME TARIM FATURA', 'ACME TARIM LTD\nTAX INVOICE'])
def test_real_two_page_invoice_ocr_with_supplier_heading_retains_two_independent_slots(heading):
    content, mime = document(28) if heading is None else rendered_pdf(heading)
    result = LocalOCR().recognize(content, mime)
    assert len(result['pages']) == 2 and result['warnings']
    assert result['preparation_text'].count('KNO3') == 1
    text = result['preparation_text']
    assert invoice_document_evidence(text)
    slots = prepare_owner_source(text, today=DAY, refs={}, binary=True, pages=result['pages'])
    assert [kind for kind, _, _ in slots] == ['PAYABLE', 'INVOICE_MATCH']
    assert prepare_owner_source(text, today=DAY, refs={}) == prepare_owner_source(text, today=DAY, refs={}, binary=True)
    for kind, payload, _ in slots:
        DTO[kind].model_validate(payload)
    payable = slots[0][1]
    assert payable['amount'] == '5000' and payable['currency'] == 'TRY'
    assert payable['event_date'] == '2026-10-08'
    assert payable.get('counterparty_id') is None
    assert payable['description'] == text and slots[1][1]['note'] == text


@pytest.mark.parametrize('source', [
    'ACME FATURA',
    'ACME FATURA\n08.10.2026\nKNO3 100 kg',
    'ACME FATURA\nTutar 5000 TRY',
    'Напомни проверить ACME FATURA' + BODY,
    'Умар написал: «ACME FATURA' + BODY + '»',
    'Это образец ACME FATURA' + BODY,
    'Это ACME FATURA?' + BODY,
    'Завтра пришлют ACME FATURA' + BODY,
    'Умар получил ACME FATURA' + BODY,
    'PLEASE SEND INVOICE' + BODY,
    'NOT AN INVOICE' + BODY,
    'DRAFT INVOICE' + BODY,
    'SAMPLE INVOICE' + BODY,
    'ACME TARIM\nREQUESTED INVOICE' + BODY,
])
def test_invoice_mentions_requests_quotes_and_incomplete_structure_do_not_gain_document_authority(source):
    assert not invoice_document_evidence(source)
    slots = prepare_owner_source(source, today=DAY, refs={}, binary=True)
    assert not any(kind in ('PAYABLE', 'INVOICE_MATCH', 'PAYMENT', 'DIRECT_EXPENSE') for kind, _, _ in slots)


def test_document_body_question_and_attributed_actor_stay_unresolved():
    for source in ['ACME FATURA' + BODY + '\nЭто мой долг?', 'ACME FATURA' + BODY + '\nУмар сообщил, что он должен 5000 TRY']:
        slots = prepare_owner_source(source, today=DAY, refs={}, binary=True)
        assert not any(kind in ('PAYABLE', 'INVOICE_MATCH', 'PAYMENT') for kind, _, _ in slots)
