"""INBOX-001..020: real PostgreSQL lifecycle, races and bypass guards."""
import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.infrastructure.inbox_models import InboxRow
from greenhouse_owner_os.infrastructure.models import CaptureRow, PostingRow, PaymentRow, ObligationRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork, SqlAlchemyAuditRepository
from greenhouse_owner_os.services.inbox import InboxService, InboxInput, InboxConfirm
from greenhouse_owner_os.domain.errors import ConflictError
from tests.integration.test_postgres_readiness import preview, setup, send
from tests.integration.test_postgres_business_capture import prepare, overview

def context(preview):
    c,e,cash,bank,cp,ids,area=prepare(preview)
    return c,e,dict(kind='payment',direction='out',amount='2600',currency='TRY',counterparty_id=cp,money_account_id=cash,event_date='2026-10-05',allocations=[]),ids

def ingest(c,e,**kw):return send(c,f'/entities/{e}/inbox',dict(raw_text='Сегодня заплатил Серкану 2600 лир из Cash TR',**kw))
def confirm(c,e,r,f,**kw):return send(c,f"/entities/{e}/inbox/{r['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f,reason='TEST reviewed original',**kw),expected=200)
def counts(sf,e):
    with sf() as s:return [s.scalar(select(func.count()).select_from(m).where(m.entity_id==UUID(e))) for m in (InboxRow,CaptureRow,PaymentRow,ObligationRow,PostingRow)]

def test_manual_review_atomic_draft_only_and_separate_extraction(preview):
    c,e,f,ids=context(preview);before=overview(c,e)['balances'];r=ingest(c,e)
    assert r['normalized']['amount']=='2600' and r['extracted_text']==r['raw_text']
    assert r['extraction']['method']=='original_text' and r['validation']['review_required']
    confirmed=confirm(c,e,r,f)
    assert confirmed['state']=='CONFIRMED' and confirmed['capture_id']
    assert overview(c,e)['balances']==before and counts(preview[0],e)==[1,1,1,0,0]
    assert confirm(c,e,r,f)['capture_id']==confirmed['capture_id']

def test_stable_retry_conflict_original_preserved(preview):
    c,e,f,_=context(preview);r=ingest(c,e,locator='email:42:attachment:1')
    assert ingest(c,e,locator='email:42:attachment:1')['id']==r['id']
    changed=send(c,f'/entities/{e}/inbox',dict(raw_text='Другой текст',locator='email:42:attachment:1'))
    assert changed['state']=='CONFLICT' and changed['conflict_of_id']==r['id']
    assert c.get(f"/v1/entities/{e}/inbox/{r['id']}").json()['raw_text']==r['raw_text']
    send(c,f"/entities/{e}/inbox/{changed['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f,reason='TEST'),expected=409)
    assert send(c,f'/entities/{e}/inbox',dict(raw_text='Другой текст',locator='email:42:attachment:1'))['id']==changed['id']
    assert counts(preview[0],e)[1:]==[0,0,0,0]

def test_equal_bytes_distinct_locators_preserved_and_download_scoped(preview):
    c,e,f,_=context(preview);binary=b'%PDF-1.7\nTEST original'
    body=dict(source_type='email',source='email_payload',filename='receipt.pdf',mime_type='application/pdf',file_base64=base64.b64encode(binary).decode(),metadata={'sender':'<script>untrusted</script>','attachment':'1'})
    r=send(c,f'/entities/{e}/inbox',dict(body,locator='mail:1'))
    other=send(c,f'/entities/{e}/inbox',dict(body,locator='mail:2'))
    assert r['id']!=other['id'] and other['duplicate_of_id']==r['id']
    assert r['confidence']=='UNAVAILABLE' and r['normalized']=={} and r['extracted_text'] is None
    assert c.get(f"/v1/entities/{e}/inbox/{r['id']}/original").content==binary
    assert c.get(f"/v1/entities/{uuid4()}/inbox/{r['id']}/original").status_code==404
    assert c.get(f"/v1/entities/{uuid4()}/inbox/{r['id']}").status_code==404

def test_unlocated_possible_duplicate_requires_review(preview):
    c,e,f,_=context(preview);r=ingest(c,e);other=ingest(c,e)
    assert r['id']!=other['id'] and other['duplicate_of_id']==r['id']
    send(c,f"/entities/{e}/inbox/{other['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f,reason='TEST'),expected=422)
    confirm(c,e,other,f,duplicate_reviewed=True)

def test_concurrent_retry_and_confirm_one_draft(preview):
    c,e,f,_=context(preview);a=preview[1];service=InboxService(lambda:SqlAlchemyUnitOfWork(preview[0]));barrier=Barrier(4)
    def create(_):barrier.wait();return service.ingest(UUID(e),a,str(uuid4()),InboxInput(raw_text='Сегодня заплатил Серкану 2600 лир из Cash TR',locator='race:1'))
    with ThreadPoolExecutor(4) as pool:rows=list(pool.map(create,range(4)))
    assert len({r['id'] for r in rows})==1
    barrier=Barrier(4);body=InboxConfirm(expected_version=1,confirmed=True,reviewed=f,reason='TEST same')
    def confirm_once(_):barrier.wait();return service.confirm(UUID(e),UUID(rows[0]['id']),a,str(uuid4()),body)
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(confirm_once,range(4)))
    assert len({r['capture_id'] for r in results})==1 and counts(preview[0],e)==[1,1,1,0,0]

def test_confirmation_late_failure_rolls_back_and_retry(preview,monkeypatch):
    c,e,f,_=context(preview);r=ingest(c,e);original=SqlAlchemyAuditRepository.append
    def fail(self,**kw):
        if kw['action']=='inbox_confirm':raise RuntimeError('TEST late failure')
        return original(self,**kw)
    monkeypatch.setattr(SqlAlchemyAuditRepository,'append',fail)
    with pytest.raises(RuntimeError):confirm(c,e,r,f)
    assert counts(preview[0],e)==[1,0,0,0,0]
    assert c.get(f"/v1/entities/{e}/inbox/{r['id']}").json()['state']!='CONFIRMED'
    monkeypatch.setattr(SqlAlchemyAuditRepository,'append',original);confirm(c,e,r,f)
    assert counts(preview[0],e)==[1,1,1,0,0]

@pytest.mark.parametrize('field,value',[('raw_text',"'changed'"),('file_bytes',"decode('255044462d','hex')"),('normalized',"'{}'::jsonb"),('extracted_text',"'invented'"),('metadata',"'{}'::jsonb")])
def test_original_and_extraction_immutable_sql(preview,field,value):
    c,e,f,_=context(preview);r=ingest(c,e,metadata={'sender':'TEST'})
    with pytest.raises(DBAPIError):
        with preview[0].begin() as s:s.execute(text(f'UPDATE core.inbox_items SET {field}={value} WHERE id=:id'),{'id':r['id']})

@pytest.mark.parametrize('raw_text',['Неизвестно что это','Серкану должен 8000','Invoice #ABC — amount unreadable'])
def test_missing_and_unknown_not_guessed(preview,raw_text):
    c,e,f,_=context(preview);r=send(c,f'/entities/{e}/inbox',dict(raw_text=raw_text))
    assert r['state']=='NEEDS_REVIEW' and r['validation']['missing_fields']
    assert not {'amount','currency','event_date','money_account_id','due_date','vat','invoice_number'}&r['normalized'].keys()
    if r['route']=='UNRESOLVED':send(c,f"/entities/{e}/inbox/{r['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f,reason='TEST'),expected=422)
    assert counts(preview[0],e)[1:]==[0,0,0,0]

def test_rejection_audit_and_final_interpretation_immutable(preview):
    c,e,f,_=context(preview);r=ingest(c,e)
    rejected=send(c,f"/entities/{e}/inbox/{r['id']}/reject",dict(expected_version=1,reason='TEST rejected'),expected=200)
    assert rejected['state']=='REJECTED' and rejected['reviewed_by']==str(preview[1])
    assert counts(preview[0],e)[1:]==[0,0,0,0]
    with pytest.raises(DBAPIError):
        with preview[0].begin() as s:s.execute(text("UPDATE core.inbox_items SET reviewed='{}' WHERE id=:id"),{'id':r['id']})
    other=ingest(c,e,locator='confirmed:1');confirm(c,e,other,f,duplicate_reviewed=True)
    send(c,f"/entities/{e}/inbox/{other['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=dict(f,amount='1'),duplicate_reviewed=True,reason='TEST reviewed original'),expected=409)

def test_exact_money_idempotency_and_cross_entity_references(preview):
    c,e,f,_=context(preview);key=str(uuid4());url=f'/v1/entities/{e}/inbox';body=dict(raw_text='Сегодня заплатил Серкану 999999999999999999.99 лир из Cash TR')
    r=c.post(url,json=body,headers={'Idempotency-Key':key});assert r.status_code==201;r=r.json()
    assert r['normalized']['amount']=='999999999999999999.99'
    assert c.post(url,json=body,headers={'Idempotency-Key':key}).json()['id']==r['id']
    assert c.post(url,json=dict(body,raw_text='changed'),headers={'Idempotency-Key':key}).status_code==409
    for reviewed in [dict(f,amount=1.1),dict(f,counterparty_id=str(uuid4())),dict(f,money_account_id=str(uuid4())),dict(f,currency='USD')]:
        send(c,f"/entities/{e}/inbox/{r['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=reviewed,reason='TEST'),expected=422)
    result=confirm(c,e,r,dict(f,amount='999999999999999999.99'))
    capture=c.get(f"/v1/entities/{e}/captures/{result['capture_id']}").json()
    payment=c.get('/v1/payments/'+capture['finance_id']).json();assert payment['amount']=='999999999999999999.99'
    assert counts(preview[0],e)[-1]==0

def test_obligation_requires_explicit_basis(preview):
    c,e,f,ids=context(preview);r=send(c,f'/entities/{e}/inbox',dict(raw_text='Сегодня Серкану должен 8000 лир'))
    fields=dict(kind='obligation',direction='payable',amount='8000',currency='TRY',counterparty_id=f['counterparty_id'],event_date='2026-10-05',description='TEST repair')
    send(c,f"/entities/{e}/inbox/{r['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=fields,reason='TEST'),expected=422)
    confirm(c,e,r,dict(fields,offset_ledger_account_id=ids['GENERAL_EXPENSE']))
    assert counts(preview[0],e)[-1]==0

def test_depot_binary_duplicates_reuse_authoritative_receipt_alacak_no_cash(preview):
    c,e,f,_=context(preview)
    depot=send(c,f'/entities/{e}/counterparties',dict(name='TEST Depot',role='depot',source='test:inbox'))['id']
    original=dict(source_type='pdf',filename='check.pdf',mime_type='application/pdf',file_base64=base64.b64encode(b'%PDF-1.7 TEST depot').decode(),raw_text='Сегодня депо TEST Depot Grand 100 кг по 40 лир')
    fields=dict(kind='settlement',depot_id=depot,event_date='2026-10-05',currency='TRY',original_greenhouse='İmamlı',alacak_value='4000',lines=[dict(original_quality_name='Grand',net_weight_kg='100',unit_price='40',count=5,source_line='1')])
    a=send(c,f'/entities/{e}/inbox',dict(original,locator='receipt:A'));b=send(c,f'/entities/{e}/inbox',dict(original,locator='receipt:B'))
    assert a['route']=='HARVEST_DEPOT' and b['duplicate_of_id']==a['id']
    a=confirm(c,e,a,fields)
    send(c,f"/entities/{e}/inbox/{b['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=fields,reason='TEST reviewed original',duplicate_reviewed=True),expected=409)
    assert c.get(f"/v1/entities/{e}/inbox/{b['id']}").json()['state']!='CONFIRMED'
    receipt=c.get(f"/v1/entities/{e}/harvest/settlements/{a['harvest_id']}").json()
    assert receipt['status']=='draft' and receipt['payment_id'] is None and receipt['obligation_id'] is None
    assert receipt['alacak_value']=='4000.00' and receipt['lines'][0]['normalized_quality']=='FIRST'
    assert counts(preview[0],e)[-3:]==[0,0,0]

def test_database_confirmed_link_and_identity_bypass_rejected(preview):
    c,e,f,_=context(preview);r=ingest(c,e,locator='stable:sql')
    confirm(c,e,r,f)
    with pytest.raises(DBAPIError):
        with preview[0].begin() as s:s.execute(text("UPDATE core.inbox_items SET capture_id=NULL WHERE id=:id"),{'id':r['id']})
    with pytest.raises(DBAPIError):
        with preview[0].begin() as s:s.execute(text("INSERT INTO core.inbox_items SELECT :newid,entity_id,source_type,source,locator,filename,mime_type,file_bytes,raw_text,metadata,received_at,fingerprint,content_hash,identity_hash,conflict_of_id,duplicate_of_id,extractor_version,extracted_text,extraction,normalized,validation,route,confidence,'READY',1,NULL,NULL,NULL,NULL,NULL,created_by,created_at FROM core.inbox_items WHERE id=:id"),{'id':r['id'],'newid':str(uuid4())})

@pytest.mark.parametrize('patch',[dict(filename='../escape.pdf',mime_type='application/pdf',file_base64='JVBERi0='),dict(source_type='pdf',filename='bad.pdf',mime_type='application/pdf',file_base64=base64.b64encode(b'NOT PDF').decode()),dict(metadata={'sender':'x'*10001})])
def test_untrusted_input_limits(preview,patch):
    c,e,f,_=context(preview);send(c,f'/entities/{e}/inbox',dict(raw_text='TEST',**patch),expected=422)

def test_failed_extractor_preserves_original_and_no_guesses(preview):
    c,e,f,_=context(preview)
    class FailedExtractor:
        version='TEST-failure'
        def extract(self,*args):raise RuntimeError('TEST private provider detail')
    service=InboxService(lambda:SqlAlchemyUnitOfWork(preview[0]),FailedExtractor())
    row=service.ingest(UUID(e),preview[1],str(uuid4()),InboxInput(raw_text='TEST unreadable original'))
    assert row['raw_text']=='TEST unreadable original' and row['normalized']=={}
    assert row['extraction']['method']=='extraction_failed' and row['confidence']=='UNAVAILABLE'
    assert 'private provider' not in str(row) and counts(preview[0],e)[1:]==[0,0,0,0]

def test_relative_date_anchors_explicit_received_timestamp(preview):
    c,e,f,_=context(preview)
    row=ingest(c,e,received_at='2026-10-03T21:30:00Z')
    assert row['normalized']['event_date']=='2026-10-04'

def test_sql_cannot_attach_unrelated_confirmed_capture(preview):
    c,e,f,_=context(preview)
    capture=send(c,f'/entities/{e}/captures',dict(text='Сегодня заплатил Серкану 2600 лир из Cash TR'))
    send(c,f"/entities/{e}/captures/{capture['id']}/confirm",dict(expected_version=1,reviewed=f,confirmed=True),expected=200)
    row=send(c,f'/entities/{e}/inbox',dict(raw_text='Другой оригинал неизвестного документа'))
    with pytest.raises(DBAPIError) as error:
        with preview[0].begin() as s:s.execute(text("UPDATE core.inbox_items SET state='CONFIRMED',version=2,capture_id=:child,reviewed='{}',reviewed_by=:actor,reviewed_at=now() WHERE id=:id"),dict(child=capture['id'],actor=str(preview[1]),id=row['id']))
    assert error.value.orig.sqlstate=='23514'
