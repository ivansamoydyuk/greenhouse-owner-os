"""Real contextual OCR and same-source fallback/retry/owner-authority gates."""
import base64
from datetime import datetime, timezone
from uuid import UUID, uuid4
import pytest
from sqlalchemy import text
from greenhouse_owner_os.domain.owner_capture import CaptureSource
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_owner_capture import confirm
from tests.capture_daily_corpus import document


def service(i,recognizer=None):
    sf,a,*_=i['preview']
    return OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf),recognizer),UUID(i['e']),a


def save(i,*,caption=None,recognizer=None,case='H01'):
    svc,e,a=service(i,recognizer)
    content,mime=document(case)
    body=CaptureSource(client_capture_id=uuid4(),captured_at=datetime(2026,10,12,9,tzinfo=timezone.utc),
        capture_context='depot_receipt',mode='file' if mime=='application/pdf' else 'photo',
        mime_type=mime,filename='TEST-frozen-'+case+('.pdf' if mime=='application/pdf' else '.png'),
        file_base64=base64.b64encode(content).decode(),text=caption)
    saved=svc.capture(e,a,str(uuid4()),body)
    assert saved['server_saved'] and saved['proposals']==[]
    assert svc.original(e,a,UUID(saved['id']))[0]==content
    svc.process(UUID(saved['id']))
    return svc,e,a,saved,body


def proposal(svc,e,a,saved):
    source=svc.source(e,a,UUID(saved['id']))
    assert len(source['proposals'])==1,source
    return svc.preview(e,a,UUID(source['proposals'][0]['id']))


def edit(i,p,payload,reason='TEST explicit original review',expected=200):
    return send(i['c'],f"/entities/{i['e']}/owner/confirmations/{p['id']}/revise",
        dict(expected_version=p['version'],payload=payload,reason=reason),expected=expected)


def depot(i):
    return send(i['c'],f"/entities/{i['e']}/counterparties",dict(name='Görgün',role='depot',source='TEST frozen reference'))['id']


def retry_now(i,saved):
    # Disclosed TEST-only time acceleration; no application retry limit changed.
    with i['preview'][0].begin() as db:
        db.execute(text("UPDATE core.processing_jobs SET retry_at=clock_timestamp()-interval '1 second' WHERE inbox_id=:s"),dict(s=saved['id']))


@pytest.mark.parametrize('case',['H01','H02'])
def test_explicit_real_document_to_one_harvest_effect_and_exact_replay(inv,case):
    dep=depot(inv);svc,e,a,saved,body=save(inv,case=case)
    source=svc.source(e,a,UUID(saved['id']));assert source['job']['state']=='SUCCEEDED'
    assert source['recognition']['engine_version']=='local-tesseract-tur-eng-v1'
    assert source['recognition']['pages'] and source['text'] is None
    p=proposal(svc,e,a,saved);f=p['revision']['payload']
    assert f['depot_id']==dep and len(f['lines'])==3
    assert [x['net_weight_kg'] for x in f['lines']]==['100.000','200.000','50.000']
    assert f['stated_total']=='14000.00' and f['document_number']=='SYN-H-001'
    assert f['document_count']==93 and all(x['count'] is None for x in f['lines'])
    assert p['revision']['basis']['arithmetic']['document_count']==93
    assert p['revision']['basis']['arithmetic']['depot_count'] is None
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=e))==0
        assert db.scalar(text('SELECT count(*) FROM finance.postings WHERE entity_id=:e'),dict(e=e))==0
    p=edit(inv,p,dict(f,quality_reviewed=True,arithmetic_reviewed=True,document_reviewed=True))
    assert p['revision']['provenance']['lines']['source_layer']=='OCR'
    assert p['revision']['extraction_run_id']==source['recognition']['extraction_run_id']
    result=confirm(inv,p);assert confirm(inv,p,key='repeat-'+case)==result
    assert svc.capture(e,a,'source-repeat-'+case,body)['id']==saved['id']
    assert result['effect']['total_amount']=='14000.00' and result['effect']['payment_id'] is None
    assert result['effect']['reconciliation']['depot_count'] is None
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE proposal_id=:p'),dict(p=p['id']))==1
        assert db.scalar(text('SELECT count(*) FROM inventory.movements WHERE entity_id=:e'),dict(e=e))==0
        assert db.scalar(text('SELECT count(*) FROM finance.payments WHERE entity_id=:e'),dict(e=e))==0


class FailedRecognition:
    def recognize(self,*args):raise TimeoutError('TEST recognizer unavailable')


def test_failed_context_keeps_manual_slot_and_retry_enriches_only_untouched_slot(inv):
    depot(inv);svc,e,a,saved,_=save(inv,recognizer=FailedRecognition())
    before=proposal(svc,e,a,saved);source=svc.source(e,a,UUID(saved['id']))
    assert source['job']['state']=='FAILED' and source['retryable']
    assert source['recognition']['state']=='FAILED' and before['revision']['payload']['lines']==[]
    retry_now(inv,saved);actual,_,_=service(inv);actual.process(UUID(saved['id']))
    after=proposal(actual,e,a,saved)
    assert after['id']==before['id'] and after['version']==before['version']+1
    assert len(after['revision']['payload']['lines'])==3
    assert actual.source(e,a,UUID(saved['id']))['job']['attempts']==2
    actual.process(UUID(saved['id']));assert proposal(actual,e,a,saved)['version']==after['version']
    confirm(inv,before,expected=409)


def test_retry_never_overwrites_owner_manual_revision_and_uses_bound_derivative(inv):
    dep=depot(inv);svc,e,a,saved,_=save(inv,recognizer=FailedRecognition())
    p=proposal(svc,e,a,saved);p=edit(inv,p,dict(p['revision']['payload'],depot_id=dep,event_date='2026-10-11',document_number='OWNER-CORRECTION'))
    retained=p['revision'];retained_run=p['source']['recognition']['extraction_run_id']
    retry_now(inv,saved);actual,_,_=service(inv);actual.process(UUID(saved['id']))
    after=proposal(actual,e,a,saved)
    assert after['revision']==retained and after['version']==p['version']
    assert after['source']['recognition']['extraction_run_id']==retained_run
    assert after['source']['recognition']['state']=='FAILED'
    assert actual.source(e,a,UUID(saved['id']))['recognition']['state']=='SUCCEEDED'


@pytest.mark.parametrize('mode',['missing','null'])
def test_document_review_cannot_be_bypassed_and_unchanged_ocr_provenance_survives(inv,mode):
    depot(inv);svc,e,a,saved,_=save(inv,caption='Дата 11.10.2026. Заплатил 15000 TRY.')
    p=proposal(svc,e,a,saved);old=p['revision'];f=dict(old['payload'],quality_reviewed=True,arithmetic_reviewed=True)
    assert f['document_reviewed'] is False
    if mode=='missing':f.pop('document_reviewed')
    else:f['document_reviewed']=None
    p=edit(inv,p,f)
    assert p['revision']['payload']['document_reviewed'] is False
    assert p['revision']['payload']['event_date']=='2026-10-12'
    assert p['revision']['provenance']['lines']==old['provenance']['lines']
    assert p['revision']['provenance']['stated_total']==old['provenance']['stated_total']
    assert p['revision']['extraction_run_id']==old['extraction_run_id']
    confirm(inv,p,expected=422)
    p=edit(inv,p,dict(p['revision']['payload'],document_reviewed=True))
    assert p['state']=='READY'


def test_legacy_manual_extraction_never_claims_caption_as_ocr(inv):
    # Restore the historical output format through a genuine active lease and
    # extraction INSERT, never mutate append-only extraction history.
    from greenhouse_owner_os.services.owner_capture import ENGINE,js
    svc,e,a=service(inv);raw,mime=document('H01');body=CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),capture_context='depot_receipt',mode='photo',mime_type=mime,filename='TEST-legacy.png',file_base64=base64.b64encode(raw).decode(),text='OWNER CAPTION, NEVER OCR')
    saved=svc.capture(e,a,'legacy-save',body);token=uuid4()
    with inv['preview'][0].begin() as db:
        j=db.execute(text("UPDATE core.processing_jobs SET state='RUNNING',attempts=1,lease_token=:t WHERE inbox_id=:s RETURNING id"),dict(t=token,s=saved['id'])).scalar_one()
        fp=db.scalar(text('SELECT fingerprint FROM core.inbox_items WHERE id=:s'),dict(s=saved['id']))
        db.execute(text('INSERT INTO core.extraction_runs(id,entity_id,inbox_id,job_id,attempt,provider,engine_version,input_hash,raw_output,normalized,warnings,lease_token) VALUES(:id,:e,:s,:j,1,\'owner\',:v,:h,CAST(:raw AS jsonb),\'{}\',\'[]\',:t)'),dict(id=uuid4(),e=e,s=saved['id'],j=j,v=ENGINE,h=fp,t=token,raw=js(dict(provider='owner',engine='manual-depot-source',version=ENGINE,text=body.text,pages=[],warnings=[]))))
    source=svc.source(e,a,UUID(saved['id']))
    assert source['text']==body.text and source['recognition']['state']=='NOT_RECOGNIZED'
    assert source['recognition']['text'] is None and source['recognition']['pages']==[]
