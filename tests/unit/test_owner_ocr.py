"""Run actual local recognition: no filename/fixture extraction substitution."""
import pytest
from tests.ocr_corpus import document
from greenhouse_owner_os.services.ocr import LocalOCR,RecognitionFailure

@pytest.mark.parametrize('n',range(30))
def test_real_turkish_document_recognition(n):
    content,mime=document(n);out=LocalOCR().recognize(content,mime)
    assert out['engine'].startswith('tesseract') and out['provider']=='local'
    assert len(out['pages'])==(2 if n//3==9 else 1)
    assert all('raw_tsv' in p and 'rotation' in p for p in out['pages'])
    if n//3 not in (6,7):
        assert '08.10.2026' in out['text']
        if n%3==1:
            assert all(v in out['text'] for v in ('5000','100','KNO3','TRY'))
        else:
            assert all(v in out['text'] for v in ('Grand','Birinci','2000','750','250','Alacak'))
            assert all(v in out['text'] for v in ('100 20 2000','50 15 750','25 10 250'))
            assert 'İkinci' in out['text'] or 'Ikinci' in out['text']
            assert ('3100' if n%3==2 else '3000') in out['text']
    if n//3==9:
        assert out['warnings'] and out['preparation_text'].count('KNO3' if n%3==1 else 'Grand')==1
    for p in out['pages']:
        for line in p['lines']:assert line['page']==p['page'] and len(line['bbox'])==4

def test_provider_failure_is_explicit_not_zero():
    with pytest.raises(RecognitionFailure):LocalOCR().recognize(b'not an image','image/png')

@pytest.mark.parametrize('value',[0,1.2,'-1','NaN','1,000','0.001'])
def test_depot_critical_source_totals_reject_inexact_values(value):
    from pydantic import ValidationError
    from greenhouse_owner_os.domain.owner_capture import DepotProposal
    with pytest.raises(ValidationError):DepotProposal(stated_total=value)

def test_unknown_quality_line_remains_unreadable_not_discarded():
    from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
    p=OwnerCaptureService(None)._depot_text('Grand 100 20 2000\n??? 25 10 250\nAlacak 2250',[])
    assert p['unreadable_lines']==['??? 25 10 250'] and len(p['lines'])==1
