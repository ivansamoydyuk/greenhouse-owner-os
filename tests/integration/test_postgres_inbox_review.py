"""Independent adversarial review: adapter identity, stale lifecycle and races."""
import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.services.inbox import InboxService,InboxInput,InboxConfirm,InboxReject
from greenhouse_owner_os.domain.errors import ConflictError
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from tests.integration.test_postgres_inbox import context,ingest,confirm,counts
from tests.integration.test_postgres_readiness import preview,send

@pytest.mark.parametrize('direct_first',[True,False])
def test_finance_same_source_cross_adapter_one_child(preview,direct_first):
    c,e,f,_=context(preview)
    original='Сегодня заплатил Серкану 2600 лир из Cash TR'
    source=dict(text=original,source_namespace='test:mail',source_reference='message:1')
    if direct_first:
        child=send(c,f'/entities/{e}/captures',source)
        send(c,f"/entities/{e}/captures/{child['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f),expected=200)
    item=ingest(c,e,source='test:mail',locator='message:1')
    result=confirm(c,e,item,f)
    child=send(c,f'/entities/{e}/captures',source)
    assert result['capture_id']==child['id']
    send(c,f"/entities/{e}/captures/{child['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f),expected=200)
    assert counts(preview[0],e)==[1,1,1,0,0]
    send(c,f'/entities/{e}/captures',dict(source,text='changed original'),expected=409)

@pytest.mark.parametrize('direct_first',[True,False])
def test_harvest_same_text_source_cross_adapter_one_chain(preview,direct_first):
    c,e,f,_=context(preview)
    depot=send(c,f'/entities/{e}/counterparties',dict(name='TEST Depot',role='depot',source='test:review'))['id']
    raw='Сегодня депо TEST Depot Grand 100 кг по 40 лир'
    fields=dict(depot_id=depot,event_date='2026-10-05',currency='TRY',original_greenhouse='İmamlı',lines=[dict(original_quality_name='Grand',net_weight_kg='100',unit_price='40',count=5,source_line='1')])
    body=dict(fields,evidence=dict(source='test:depot',source_reference='msg:1',raw_text=raw))
    if direct_first:prior=send(c,f'/entities/{e}/harvest/settlements',body)
    item=send(c,f'/entities/{e}/inbox',dict(raw_text=raw,source='test:depot',locator='msg:1'))
    result=confirm(c,e,item,dict(fields,kind='settlement'))
    prior=send(c,f'/entities/{e}/harvest/settlements',body)
    assert prior['id']==result['harvest_id']
    assert confirm(c,e,item,dict(fields,kind='settlement'))['harvest_id']==prior['id']
    assert counts(preview[0],e)[-3:]==[0,0,0]

@pytest.mark.parametrize('winner',['reject','confirm'])
def test_stale_request_cannot_change_final_state(preview,winner):
    c,e,f,_=context(preview);item=ingest(c,e)
    if winner=='reject':
        send(c,f"/entities/{e}/inbox/{item['id']}/reject",dict(expected_version=1,reason='TEST'),expected=200)
        send(c,f"/entities/{e}/inbox/{item['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f,reason='TEST reviewed original'),expected=409)
    else:
        confirm(c,e,item,f)
        send(c,f"/entities/{e}/inbox/{item['id']}/reject",dict(expected_version=1,reason='TEST'),expected=409)


def test_confirm_reject_race_one_final_state(preview):
    c,e,f,_=context(preview);item=ingest(c,e)
    service=InboxService(lambda:SqlAlchemyUnitOfWork(preview[0]));barrier=Barrier(2)
    def run(action):
        barrier.wait()
        try:
            if action=='confirm':return service.confirm(UUID(e),UUID(item['id']),preview[1],str(uuid4()),InboxConfirm(expected_version=1,confirmed=True,reviewed=f,reason='TEST'))['state']
            return service.reject(UUID(e),UUID(item['id']),preview[1],str(uuid4()),InboxReject(expected_version=1,reason='TEST'))['state']
        except ConflictError:return 'CONFLICT'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,['confirm','reject']))
    assert results.count('CONFLICT')==1
    final=service.get(UUID(e),UUID(item['id']))
    assert counts(preview[0],e)[2]==(1 if final['state']=='CONFIRMED' else 0)


def test_capture_stable_identity_sql_immutable(preview):
    c,e,f,_=context(preview);item=ingest(c,e,source='mail',locator='1');child=confirm(c,e,item,f)
    with pytest.raises(DBAPIError):
        with preview[0].begin() as s:s.execute(text("UPDATE core.capture_drafts SET source_reference='other' WHERE id=:id"),{'id':child['capture_id']})


def test_changed_source_timestamp_conflicts_without_changing_extraction(preview):
    c,e,f,_=context(preview)
    item=ingest(c,e,locator='date:1',received_at='2026-10-01T00:00:00Z')
    send(c,f'/entities/{e}/inbox',dict(raw_text=item['raw_text'],locator='date:1',received_at='2026-10-02T00:00:00Z'),expected=409)
    assert c.get(f"/v1/entities/{e}/inbox/{item['id']}").json()['normalized']['event_date']=='2026-10-01'


def test_reused_capture_request_key_remains_payload_bound(preview):
    c,e,f,_=context(preview)
    body=dict(text='Сегодня заплатил Серкану 2600 лир из Cash TR',source_namespace='mail',source_reference='1')
    first=send(c,f'/entities/{e}/captures',body)
    assert send(c,f'/entities/{e}/captures',body,key='reuse')['id']==first['id']
    send(c,f'/entities/{e}/captures',dict(body,source_reference='2'),key='reuse',expected=409)
    request=dict(expected_version=1,confirmed=True,reviewed=f)
    send(c,f"/entities/{e}/captures/{first['id']}/confirm",request,expected=200)
    send(c,f"/entities/{e}/captures/{first['id']}/confirm",request,key='reuse-confirm',expected=200)
    send(c,f"/entities/{e}/captures/{first['id']}/confirm",dict(request,reviewed=dict(f,amount='1')),key='reuse-confirm',expected=409)


@pytest.mark.parametrize('direct_first',[True,False])
def test_binary_email_identity_distinguishes_recurring_events_and_retries(preview,direct_first):
    c,e,f,_=context(preview)
    depot=send(c,f'/entities/{e}/counterparties',dict(name='TEST Depot',role='depot',source='test:review'))['id']
    original=dict(raw_text='Сегодня депо TEST Depot Grand 100 кг по 40 лир',filename='receipt.pdf',mime_type='application/pdf',file_base64=base64.b64encode(b'%PDF-1.7 TEST recurring').decode())
    fields=dict(depot_id=depot,event_date='2026-10-05',currency='TRY',original_greenhouse='İmamlı',lines=[dict(original_quality_name='Grand',net_weight_kg='100',unit_price='40',count=5,source_line='1')])
    direct=dict(fields,evidence=dict(original,source='email_payload',source_reference='msg:A'))
    if direct_first:prior=send(c,f'/entities/{e}/harvest/settlements',direct)
    item=send(c,f'/entities/{e}/inbox',dict(original,source_type='email',source='email_payload',locator='msg:A'))
    confirmed=confirm(c,e,item,dict(fields,kind='settlement'))
    prior=send(c,f'/entities/{e}/harvest/settlements',direct)
    assert confirmed['harvest_id']==prior['id']
    other=send(c,f'/entities/{e}/inbox',dict(original,source_type='email',source='email_payload',locator='msg:B'))
    second=confirm(c,e,other,dict(fields,kind='settlement',event_date='2026-10-06'),duplicate_reviewed=True)
    assert second['harvest_id']!=prior['id']
    assert c.get(f"/v1/entities/{e}/harvest/settlements/{second['harvest_id']}").json()['evidence']['source_reference']=='msg:B'
    assert counts(preview[0],e)[-3:]==[0,0,0]


def test_real_entities_isolate_source_and_linkage(preview):
    c,e,f,_=context(preview)
    other=send(c,'/entities',dict(name='TEST Other Entity',currency='TRY',source='test:other:'+str(uuid4())))['id']
    send(c,f'/entities/{other}/reference-bootstrap',dict(currency='TRY'),expected=200)
    cp=send(c,f'/entities/{other}/counterparties',dict(name='Other Supplier',role='supplier',source='test:other'))['id']
    cash=send(c,f'/entities/{other}/money-accounts',dict(name='Other Cash',account_type='cash',currency='TRY',source='test:other'))['id']
    g=dict(f,counterparty_id=cp,money_account_id=cash)
    first=ingest(c,e,source='mail',locator='shared:1')
    second=ingest(c,other,source='mail',locator='shared:1')
    assert first['id']!=second['id'] and second['duplicate_of_id'] is None
    assert c.get(f"/v1/entities/{other}/inbox/{first['id']}").status_code==404
    assert c.get(f"/v1/entities/{other}/inbox/{first['id']}/original").status_code==404
    send(c,f"/entities/{other}/inbox/{first['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=g,reason='TEST'),expected=404)
    send(c,f"/entities/{other}/inbox/{second['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f,reason='TEST'),expected=422)
    with pytest.raises(DBAPIError):
        with preview[0].begin() as s:s.execute(text("INSERT INTO core.inbox_items SELECT (jsonb_populate_record(NULL::core.inbox_items,to_jsonb(i)||jsonb_build_object('id',CAST(:newid AS text),'entity_id',CAST(:other AS text),'duplicate_of_id',i.id,'locator','foreign'))).* FROM core.inbox_items i WHERE i.id=:id"),dict(newid=str(uuid4()),other=other,id=first['id']))

@pytest.mark.parametrize('raw',[b'',b'%PDF-'+b'x'*5242880])
def test_binary_empty_and_oversized_rejected(preview,raw):
    c,e,f,_=context(preview)
    send(c,f'/entities/{e}/inbox',dict(source_type='pdf',filename='test.pdf',mime_type='application/pdf',file_base64=base64.b64encode(raw).decode()),expected=422)

@pytest.mark.parametrize('amount',['-100','1e100','100usdfoo'])
def test_malformed_source_amount_not_truncated_or_guessed(preview,amount):
    c,e,f,_=context(preview)
    item=send(c,f'/entities/{e}/inbox',dict(raw_text=f'Сегодня заплатил Серкану {amount} лир'))
    assert 'amount' not in item['normalized'] and item['state']=='NEEDS_REVIEW'


def test_migration_refuses_existing_confirmed_history_instead_of_rewriting(preview,monkeypatch):
    import importlib.util
    from pathlib import Path
    c,e,f,_=context(preview);item=ingest(c,e);confirm(c,e,item,f)
    snapshot=c.get(f"/v1/entities/{e}/inbox/{item['id']}").json()
    path=Path('alembic/versions/20261005_0014_inbox_handoff_identity.py')
    spec=importlib.util.spec_from_file_location('review_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(DBAPIError) as error:
        with preview[0].begin() as s:
            monkeypatch.setattr(module.op,'execute',lambda sql:s.execute(text(sql)))
            module.upgrade()
    assert error.value.orig.sqlstate=='23514'
    assert c.get(f"/v1/entities/{e}/inbox/{item['id']}").json()==snapshot


def test_inbox_direct_finance_confirmation_race_one_draft(preview):
    from greenhouse_owner_os.services.capture import CaptureService,CaptureInput,CaptureConfirm
    c,e,f,_=context(preview);item=ingest(c,e,source='mail',locator='race:1')
    factory=lambda:SqlAlchemyUnitOfWork(preview[0]);barrier=Barrier(2)
    def run(adapter):
        barrier.wait()
        if adapter=='inbox':return InboxService(factory).confirm(UUID(e),UUID(item['id']),preview[1],str(uuid4()),InboxConfirm(expected_version=1,confirmed=True,reviewed=f,reason='TEST'))['capture_id']
        capture=CaptureService(factory)
        child=capture.create(UUID(e),preview[1],str(uuid4()),CaptureInput(text=item['raw_text'],source_namespace='mail',source_reference='race:1'))
        return capture.confirm(UUID(e),UUID(child['id']),preview[1],str(uuid4()),CaptureConfirm(expected_version=1,confirmed=True,reviewed=f))['capture']['id']
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,['inbox','direct']))
    assert len(set(results))==1 and counts(preview[0],e)==[1,1,1,0,0]

@pytest.mark.parametrize('binary',[False,True])
def test_inbox_direct_harvest_race_one_chain(preview,binary):
    from greenhouse_owner_os.services.harvest import HarvestService
    from greenhouse_owner_os.domain.harvest import SettlementInput
    c,e,f,_=context(preview)
    depot=send(c,f'/entities/{e}/counterparties',dict(name='TEST Depot',role='depot',source='test:review'))['id']
    original=dict(raw_text='Сегодня депо TEST Depot Grand 100 кг по 40 лир')
    if binary:original.update(filename='receipt.pdf',mime_type='application/pdf',file_base64=base64.b64encode(b'%PDF-1.7 TEST race').decode())
    fields=dict(depot_id=depot,event_date='2026-10-05',currency='TRY',original_greenhouse='İmamlı',lines=[dict(original_quality_name='Grand',net_weight_kg='100',unit_price='40',count=5,source_line='1')])
    item=send(c,f'/entities/{e}/inbox',dict(original,source_type='email',source='email_payload',locator='race:1'))
    factory=lambda:SqlAlchemyUnitOfWork(preview[0]);barrier=Barrier(2)
    def run(adapter):
        barrier.wait()
        if adapter=='inbox':return InboxService(factory).confirm(UUID(e),UUID(item['id']),preview[1],str(uuid4()),InboxConfirm(expected_version=1,confirmed=True,reviewed=dict(fields,kind='settlement'),reason='TEST'))['harvest_id']
        return HarvestService(factory).settlement(UUID(e),preview[1],str(uuid4()),SettlementInput(**fields,evidence=dict(original,source='email_payload',source_reference='race:1')))['id']
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,['inbox','direct']))
    assert len(set(results))==1 and counts(preview[0],e)[-3:]==[0,0,0]


def test_sql_handoff_cannot_misrepresent_confirmed_capture_review(preview):
    import json
    c,e,f,_=context(preview)
    original='Сегодня заплатил Серкану 2600 лир из Cash TR'
    child=send(c,f'/entities/{e}/captures',dict(text=original,source_namespace='mail',source_reference='review:1'))
    send(c,f"/entities/{e}/captures/{child['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f),expected=200)
    item=ingest(c,e,source='mail',locator='review:1')
    with pytest.raises(DBAPIError) as error:
        with preview[0].begin() as s:s.execute(text("UPDATE core.inbox_items SET state='CONFIRMED',version=2,capture_id=:child,reviewed=CAST(:review AS jsonb),reviewed_by=:actor,reviewed_at=now() WHERE id=:id"),dict(child=child['id'],actor=str(preview[1]),id=item['id'],review=json.dumps(dict(expected_version=1,confirmed=True,reviewed=dict(f,amount='1'),duplicate_reviewed=False,reason='TEST forged'))))
    assert error.value.orig.sqlstate=='23514'

@pytest.mark.parametrize('before_ingest',[True,False])
def test_unlocated_finance_duplicate_never_auto_approved(preview,before_ingest):
    c,e,f,_=context(preview)
    original='Сегодня заплатил Серкану 2600 лир из Cash TR'
    if not before_ingest:item=ingest(c,e)
    child=send(c,f'/entities/{e}/captures',dict(text=original))
    send(c,f"/entities/{e}/captures/{child['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f),expected=200)
    if before_ingest:item=ingest(c,e)
    detail=c.get(f"/v1/entities/{e}/inbox/{item['id']}").json()
    assert detail['duplicate_warning']
    if before_ingest:assert any('Похожее' in gap for gap in detail['validation']['missing_fields'])
    send(c,f"/entities/{e}/inbox/{item['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=f,reason='TEST'),expected=422)
    assert counts(preview[0],e)==[1,1,1,0,0]
    # The owner can explicitly assert a distinct event; absence of a locator
    # must never make the adapter silently take that decision itself.
    confirm(c,e,item,f,duplicate_reviewed=True)
    assert counts(preview[0],e)==[1,2,2,0,0]


def test_unlocated_harvest_source_requires_explicit_duplicate_review(preview):
    c,e,f,_=context(preview)
    depot=send(c,f'/entities/{e}/counterparties',dict(name='TEST Depot',role='depot',source='test:review'))['id']
    raw='Сегодня депо TEST Depot Grand 100 кг по 40 лир'
    fields=dict(depot_id=depot,event_date='2026-10-05',currency='TRY',original_greenhouse='İmamlı',lines=[dict(original_quality_name='Grand',net_weight_kg='100',unit_price='40',count=5,source_line='1')])
    item=send(c,f'/entities/{e}/inbox',dict(raw_text=raw))
    send(c,f'/entities/{e}/harvest/settlements',dict(fields,evidence=dict(source='owner_mobile:manual',raw_text=raw)))
    assert c.get(f"/v1/entities/{e}/inbox/{item['id']}").json()['duplicate_warning']
    send(c,f"/entities/{e}/inbox/{item['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=dict(fields,kind='settlement'),reason='TEST'),expected=422)
    assert counts(preview[0],e)[-3:]==[0,0,0]


def test_synthetic_manual_locator_cannot_defeat_binary_duplicate_guard(preview):
    c,e,f,_=context(preview)
    depot=send(c,f'/entities/{e}/counterparties',dict(name='TEST Depot',role='depot',source='test:review'))['id']
    original=dict(raw_text='Сегодня депо TEST Depot Grand 100 кг по 40 лир',source_type='pdf',filename='receipt.pdf',mime_type='application/pdf',file_base64=base64.b64encode(b'%PDF-1.7 TEST manual duplicate').decode())
    fields=dict(kind='settlement',depot_id=depot,event_date='2026-10-05',currency='TRY',original_greenhouse='İmamlı',lines=[dict(original_quality_name='Grand',net_weight_kg='100',unit_price='40',count=5,source_line='1')])
    first=send(c,f'/entities/{e}/inbox',original);confirm(c,e,first,fields)
    other=send(c,f'/entities/{e}/inbox',original)
    send(c,f"/entities/{e}/inbox/{other['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=fields,reason='TEST',duplicate_reviewed=True),expected=409)
    with pytest.raises(DBAPIError):
        with preview[0].begin() as s:s.execute(text("INSERT INTO harvest.evidence SELECT (jsonb_populate_record(NULL::harvest.evidence,to_jsonb(ev)||jsonb_build_object('id',CAST(:newid AS text),'source_reference','forged:manual'))).* FROM harvest.evidence ev WHERE ev.entity_id=:e"),dict(newid=str(uuid4()),e=e))


def test_unlocated_direct_harvest_retry_cannot_duplicate_inbox_chain(preview):
    c,e,f,_=context(preview)
    depot=send(c,f'/entities/{e}/counterparties',dict(name='TEST Depot',role='depot',source='test:review'))['id']
    raw='Сегодня депо TEST Depot Grand 100 кг по 40 лир'
    fields=dict(depot_id=depot,event_date='2026-10-05',currency='TRY',original_greenhouse='İmamlı',lines=[dict(original_quality_name='Grand',net_weight_kg='100',unit_price='40',count=5,source_line='1')])
    item=send(c,f'/entities/{e}/inbox',dict(raw_text=raw));confirm(c,e,item,dict(fields,kind='settlement'))
    send(c,f'/entities/{e}/harvest/settlements',dict(fields,evidence=dict(source='owner_mobile:manual',raw_text=raw)),expected=409)
    assert counts(preview[0],e)[-3:]==[0,0,0]


def test_0015_refuses_legacy_manual_binary_duplicates_without_repair(preview,monkeypatch):
    import importlib.util
    from pathlib import Path
    c,e,f,_=context(preview)
    depot=send(c,f'/entities/{e}/counterparties',dict(name='TEST Depot',role='depot',source='test:review'))['id']
    original=dict(raw_text='Сегодня депо TEST Depot Grand 100 кг по 40 лир',source_type='pdf',filename='receipt.pdf',mime_type='application/pdf',file_base64=base64.b64encode(b'%PDF-1.7 TEST migration').decode())
    fields=dict(kind='settlement',depot_id=depot,event_date='2026-10-05',currency='TRY',original_greenhouse='İmamlı',lines=[dict(original_quality_name='Grand',net_weight_kg='100',unit_price='40',count=5,source_line='1')])
    item=send(c,f'/entities/{e}/inbox',original);confirm(c,e,item,fields)
    before=c.get(f"/v1/entities/{e}/inbox/{item['id']}").json()
    spec=importlib.util.spec_from_file_location('review_migration_15',Path('alembic/versions/20261005_0015_inbox_manual_duplicates.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(DBAPIError) as error:
        with preview[0].begin() as s:
            s.execute(text("DROP INDEX harvest.evidence_binary_sha; CREATE UNIQUE INDEX evidence_binary_sha ON harvest.evidence(entity_id,fingerprint) WHERE file_bytes IS NOT NULL AND (source_reference IS NULL OR source='owner_mobile:manual')"))
            s.execute(text("INSERT INTO harvest.evidence SELECT (jsonb_populate_record(NULL::harvest.evidence,to_jsonb(ev)||jsonb_build_object('id',CAST(:newid AS text),'source_reference','legacy:duplicate'))).* FROM harvest.evidence ev WHERE ev.entity_id=:e"),dict(newid=str(uuid4()),e=e))
            monkeypatch.setattr(module.op,'execute',lambda sql:s.execute(text(sql)))
            module.upgrade()
    assert error.value.orig.sqlstate=='23505'
    assert c.get(f"/v1/entities/{e}/inbox/{item['id']}").json()==before
