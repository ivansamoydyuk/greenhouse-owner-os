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
        if n%3==1:
            assert '5000' in out['text'] and '100' in out['text'] and 'KNO3' in out['text']
        else:
            assert all(v in out['text'] for v in ('Grand','Birinci','2000','750','250','Alacak'))
            assert ('3100' if n%3==2 else '3000') in out['text']
    for p in out['pages']:
        for line in p['lines']:assert line['page']==p['page'] and len(line['bbox'])==4

def test_provider_failure_is_explicit_not_zero():
    with pytest.raises(RecognitionFailure):LocalOCR().recognize(b'not an image','image/png')
