"""Real source locators, recurring content, retries and strong binary protection."""
import base64
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.infrastructure.harvest_models import EvidenceRow, ReportRow, SettlementRow
from greenhouse_owner_os.infrastructure.models import ObligationRow
from greenhouse_owner_os.domain.harvest import ReportInput
from greenhouse_owner_os.services.harvest import HarvestService
from tests.integration.test_postgres_harvest import prepare, receipt, post, balances
from tests.integration.test_postgres_readiness import preview, send


def report_body(reference='message:1', **changes):
    return dict(event_date='2026-10-05', original_greenhouse='Озвади', cut_count=55,
                reporter='TEST Worker', evidence=dict(source='worker:message',
                source_reference=reference, raw_text='Сегодня в Озвади срезали 55 деревьев.'), **changes)


def count(preview, entity, model):
    with preview[0]() as db:
        return db.scalar(select(func.count()).select_from(model).where(model.entity_id == UUID(entity)))


def test_same_text_distinct_locators_are_distinct_events(preview):
    c,e,*_=prepare(preview)
    a=send(c,'/entities/'+e+'/harvest/reports',report_body())
    b=report_body('message:2');b['event_date']='2026-10-06'
    b=send(c,'/entities/'+e+'/harvest/reports',b)
    assert a['id']!=b['id'] and a['evidence_id']!=b['evidence_id']
    with preview[0]() as db:
        rows=list(db.scalars(select(EvidenceRow).where(EvidenceRow.entity_id==UUID(e))))
        assert len(rows)==2 and rows[0].fingerprint==rows[1].fingerprint


def test_stable_source_retry_returns_one_report_and_changed_content_conflicts(preview):
    c,e,*_=prepare(preview);body=report_body()
    a=send(c,'/entities/'+e+'/harvest/reports',body)
    b=send(c,'/entities/'+e+'/harvest/reports',body)
    assert a==b
    body['evidence']['raw_text']='Сегодня в Озвади срезали 56 деревьев.'
    res=send(c,'/entities/'+e+'/harvest/reports',body,expected=409)
    assert 'content changed' in res['error']['message']
    assert count(preview,e,EvidenceRow)==count(preview,e,ReportRow)==1


def test_stable_source_changed_business_values_cannot_duplicate(preview):
    c,e,*_=prepare(preview);body=report_body()
    send(c,'/entities/'+e+'/harvest/reports',body)
    body['cut_count']=56
    send(c,'/entities/'+e+'/harvest/reports',body,expected=409)
    assert count(preview,e,ReportRow)==1


def test_identical_capture_text_distinct_capture_ids_confirm_distinct_drafts(preview):
    c,e,*_=prepare(preview)
    caps=[send(c,'/entities/'+e+'/captures',dict(text=report_body()['evidence']['raw_text'],source='text')) for _ in range(2)]
    assert caps[0]['id']!=caps[1]['id']
    drafts=[]
    for i,cap in enumerate(caps):
        body=report_body();body.pop('evidence');body.update(kind='report',event_date=f'2026-10-0{5+i}',duplicate_reviewed=True)
        result=send(c,'/entities/'+e+'/captures/'+cap['id']+'/confirm',dict(expected_version=1,confirmed=True,reviewed=body),expected=200)
        assert result['posted'] is False
        drafts.append(result['harvest'])
    assert drafts[0]['id']!=drafts[1]['id'] and count(preview,e,EvidenceRow)==2
    with preview[0]() as db:
        assert set(db.scalars(select(EvidenceRow.source_reference).where(EvidenceRow.entity_id==UUID(e))))=={x['id'] for x in caps}
    assert balances(c,e)['cash']==balances(c,e)['receivable']=='0.00'


def test_concurrent_stable_source_different_request_keys_one_report(preview):
    c,e,*_=prepare(preview);service=HarvestService(preview[3].uow_factory)
    body=ReportInput(**report_body())
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda i:service.report(UUID(e),preview[1],str(uuid4()),body),range(4)))
    assert len({r['id'] for r in results})==1
    assert count(preview,e,EvidenceRow)==count(preview,e,ReportRow)==1


def test_settlement_stable_source_retry_one_financial_effect(preview):
    c,e,cash,depot,areas=prepare(preview);body=receipt(depot,areas)
    body['evidence'].update(source_reference='receipt:1')
    path='/entities/'+e+'/harvest/settlements'
    a=send(c,path,body);b=send(c,path,body)
    assert a['id']==b['id']
    posted=post(c,e,a)
    retry=send(c,path,body)
    assert retry['id']==posted['id'] and retry['status']=='posted'
    assert count(preview,e,SettlementRow)==count(preview,e,ObligationRow)==1
    body['lines'][0]['unit_price']='31'
    send(c,path,body,expected=409)
    assert balances(c,e)['receivable']=='30000.00'


@pytest.mark.parametrize('mime,raw',[('application/pdf',b'%PDF-1.4\nTEST identity\n%%EOF'),('image/png',b'\x89PNG\r\n\x1a\nTEST identity'),('image/jpeg',b'\xff\xd8\xffTEST identity')])
def test_exact_binary_protected_across_locators_and_text_versions(preview,mime,raw):
    c,e,cash,depot,areas=prepare(preview);body=receipt(depot,areas)
    body['evidence']=dict(source='owner_mobile:manual',source_reference='upload:1',raw_text='TEST original',filename='receipt',mime_type=mime,file_base64=base64.b64encode(raw).decode())
    path='/entities/'+e+'/harvest/settlements'
    a=send(c,path,body);assert send(c,path,body)['id']==a['id']
    body['evidence']['raw_text']='TEST changed'
    send(c,path,body,expected=409)
    body['evidence'].update(source_reference='upload:2',raw_text='TEST original')
    send(c,path,body,expected=409)
    assert count(preview,e,EvidenceRow)==count(preview,e,SettlementRow)==1


def test_database_indexes_cannot_bypass_identity_or_binary_protection(preview):
    c,e,*_=prepare(preview);send(c,'/entities/'+e+'/harvest/reports',report_body())
    # Copy directly with another id/content: source identity still protected by PG.
    with pytest.raises(DBAPIError),preview[0].begin() as db:
        db.execute(text("INSERT INTO harvest.evidence SELECT :id,entity_id,source,source_reference,raw_text,filename,mime_type,file_bytes,repeat('a',64),created_by,received_at FROM harvest.evidence WHERE entity_id=:e"),{'id':uuid4(),'e':UUID(e)})
    assert count(preview,e,EvidenceRow)==1


def test_unlocated_text_duplicate_protection_retained(preview):
    c,e,*_=prepare(preview);b=report_body(None)
    send(c,'/entities/'+e+'/harvest/reports',b)
    send(c,'/entities/'+e+'/harvest/reports',b,expected=409)
    assert count(preview,e,EvidenceRow)==1
