"""Independent pre-merge R2 regressions. Real PostgreSQL, no replacement stores."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID, uuid4
import copy
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.domain.owner_capture import CaptureSource
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.services.references import provision_authenticated_owner
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_inventory import inv, line
from tests.integration.test_postgres_owner_capture import source, queue, revise, confirm


def service(i):
    sf,a,_,_,_=i['preview']
    return OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf)),UUID(i['e']),a


def other_owner(i):
    sf,_,refs,finance,_=i['preview']
    actor=provision_authenticated_owner(sf,external_ref='test:other:'+str(uuid4()),display_name='TEST other owner',environment='TEST_PREVIEW')
    return actor,TestClient(create_app(finance_service=finance,reference_service=refs,actor_provider=lambda:actor))


def ready_payable(i):
    source(i,'Должен 7000 TRY за ремонт')
    return revise(i,queue(i)[0],dict(amount='7000',currency='TRY',counterparty_id=i['cp'],event_date='2026-10-08',description='TEST private source'))


def depot(i,**changes):
    party=send(i['c'],f"/entities/{i['e']}/counterparties",dict(name='Görgün',role='depot',source='test:review'))
    source(i,'Görgün\n08.10.2026\nGrand 100 20 2000\nAlacak 2000')
    p=next(x for x in queue(i) if x['action_type']=='DEPOT_RECEIPT')
    f=p['revision']['payload'];f.update(quality_reviewed=True,arithmetic_reviewed=True,**changes)
    return revise(i,p,f)


def test_existing_entity_name_never_grants_another_owner_access(inv):
    actor,c=other_owner(inv)
    entity=inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()['finance']['entity_name']
    r=c.post('/v1/entities',json=dict(name=entity,currency='TRY',source='test:known-name'),headers={'Idempotency-Key':str(uuid4())})
    assert r.status_code in (403,422)
    with inv['preview'][0]() as db:
        assert not db.scalar(text('SELECT core.r2_owner(:e,:a)'),dict(e=inv['e'],a=actor))
    assert c.get(f"/v1/entities/{inv['e']}/owner/confirmations").status_code in (403,422)


def test_r2_source_and_effect_private_through_legacy_routes(inv):
    p=ready_payable(inv);effect=confirm(inv,p)['effect'];_,c=other_owner(inv)
    root=f"/v1/entities/{inv['e']}"
    for path in (root+'/inbox',root+f"/inbox/{p['inbox_id']}",root+f"/inbox/{p['inbox_id']}/original",
                 root+'/finance-overview','/v1/obligations/'+effect['id'],root+'/inventory',root+'/harvest'):
        assert c.get(path).status_code in (403,404),path
    own=send(c,'/entities',dict(name='TEST separate owner '+str(uuid4()),currency='TRY',source='test:review'))
    assert c.get('/v1/obligations/'+effect['id']+'?entity_id='+own['id']).status_code==403
    # Even the authorized owner cannot run an R2 source through R1 handoff.
    assert inv['c'].get(root+f"/inbox/{p['inbox_id']}").status_code==404
    assert not any(x['id']==p['inbox_id'] for x in inv['c'].get(root+'/inbox').json()['items'])


def test_r2_original_cannot_be_recreated_by_legacy_finance_adapter(inv):
    p=ready_payable(inv);confirm(inv,p)
    saved=inv['c'].get(f"/v1/entities/{inv['e']}/owner/captures/{p['inbox_id']}").json()
    send(inv['c'],f"/entities/{inv['e']}/captures",dict(text='Должен 7000 TRY за ремонт',source='text',source_namespace='owner:r2',source_reference=saved['client_capture_id']),expected=409)
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM finance.obligations WHERE entity_id=:e'),dict(e=inv['e']))==1


def test_final_expired_preparation_lease_is_terminal_not_forever_running(inv):
    svc,e,a=service(inv)
    saved=svc.capture(e,a,str(uuid4()),CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),text='TEST pending'))
    with inv['preview'][0].begin() as db:
        db.execute(text("UPDATE core.processing_jobs SET state='RUNNING',attempts=3,lease_token=:t,lease_until=clock_timestamp()-interval '1 second' WHERE inbox_id=:s"),dict(t=uuid4(),s=saved['id']))
    svc.process(UUID(saved['id']))
    actual=svc.source(e,a,UUID(saved['id']))
    assert actual['state']=='FAILED' and not actual['retryable']
    assert actual['job']['error_code']=='RETRY_EXHAUSTED'
    assert svc.queue(e,a)['preparing']==0 and actual['server_saved']


@pytest.mark.parametrize('currency',['USD','EUR','UAH'])
def test_explicit_currency_never_replaced_by_daily_try(inv,currency):
    source(inv,'Должен 5000 '+currency)
    p=queue(inv)[0]
    assert p['revision']['payload']['currency']==currency
    assert p['revision']['payload']['amount']=='5000'


@pytest.mark.parametrize('phrase',['Должен 5000 GBP','Должен 5000 USD или 5000 TRY'])
def test_unknown_or_conflicting_currency_requires_input(inv,phrase):
    source(inv,phrase);p=queue(inv)[0]
    assert p['revision']['payload']['currency'] is None
    assert p['state']=='NEEDS_INPUT'


def test_explicit_greenhouse_attribution_survives_payable_effect(inv):
    source(inv,'Должен 7000 TRY за ремонт в imamli');p=queue(inv)[0]
    f=p['revision']['payload'];assert f['attributions']==[dict(work_area_id=inv['g'],amount='7000')]
    f['counterparty_id']=inv['cp'];p=revise(inv,p,f);r=confirm(inv,p)
    assert r['effect']['attributions'][0]['work_area_id']==inv['g']
    assert r['effect']['attributions'][0]['amount']=='7000.00'


def test_stock_application_without_greenhouse_cannot_be_ready(inv):
    source(inv,'Внесли 10 кг KNO3');p=queue(inv)[0]
    ln=line(inv,'10');ln['greenhouse_id']=None
    p=revise(inv,p,dict(event_date='2026-10-08',lines=[ln]))
    assert p['state']=='NEEDS_INPUT' and any('Теплица' in m for m in p['revision']['missing'])
    confirm(inv,p,expected=422)


def test_independent_stock_siblings_confirm_without_false_stale_or_duplicate(inv):
    source(inv,'Привезли 10 кг KNO3 на TEST A.\nПривезли 20 кг KNO3 на TEST B.')
    ps=queue(inv);assert len(ps)==2
    ps=[revise(inv,p,dict(event_date='2026-10-08',lines=[dict(line(inv,'10' if n==0 else '20'),location_id=inv['l']['id'] if n==0 else inv['b']['id'])])) for n,p in enumerate(ps)]
    first=confirm(inv,ps[0]);second=confirm(inv,ps[1])
    assert first['state']==second['state']=='POSTED'
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==2


def test_distinct_identical_text_stock_event_requires_explicit_duplicate_review(inv):
    phrase='Привезли 100 кг KNO3 в İmamlı.'
    source(inv,phrase);p=revise(inv,queue(inv)[0],dict(event_date='2026-10-08',lines=[line(inv)]));confirm(inv,p)
    source(inv,phrase);p=queue(inv)[0]
    f=dict(event_date='2026-10-09',lines=[line(inv)])
    p=revise(inv,p,f);assert p['state']=='NEEDS_INPUT';confirm(inv,p,expected=422)
    p=revise(inv,p,dict(f,separate_event_reviewed=True));assert p['state']=='READY';confirm(inv,p)


def test_owner_review_reason_is_retained_in_immutable_action(inv):
    source(inv);p=queue(inv)[0]
    send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{p['id']}/reject",dict(expected_version=p['version'],reason='TEST wrong receipt'),expected=200)
    with inv['preview'][0]() as db:
        assert db.scalar(text("SELECT snapshot->>'reason' FROM core.proposal_actions WHERE proposal_id=:p AND action='REJECT'"),dict(p=p['id']))=='TEST wrong receipt'


def test_selected_depot_batch_requires_and_preserves_matching_evidence(inv):
    batch=send(inv['c'],f"/entities/{inv['e']}/harvest/batches",dict(event_date='2026-10-08',label='TEST same day',link_evidence='TEST owner source'))
    p=depot(inv,batch_ids=[batch['id']])
    assert p['state']=='NEEDS_INPUT'
    p=revise(inv,p,dict(p['revision']['payload'],link_evidence='TEST owner reviewed batch match'))
    result=confirm(inv,p)['effect'];assert result['batch_ids']==[batch['id']]
    assert result['link_evidence']=='TEST owner reviewed batch match'


def test_unselected_new_batch_does_not_invalidate_reviewed_depot_action(inv):
    p=depot(inv)
    send(inv['c'],f"/entities/{inv['e']}/harvest/batches",dict(event_date='2026-10-08',label='TEST unrelated candidate',link_evidence='TEST independent source'))
    assert confirm(inv,p)['state']=='POSTED'


def test_distinct_identical_depot_text_needs_explicit_new_event_review(inv):
    p=depot(inv);confirm(inv,p)
    source(inv,'Görgün\n08.10.2026\nGrand 100 20 2000\nAlacak 2000')
    p=queue(inv)[0];f=p['revision']['payload'];f.update(quality_reviewed=True,arithmetic_reviewed=True)
    p=revise(inv,p,f);assert p['state']=='NEEDS_INPUT';confirm(inv,p,expected=422)
    p=revise(inv,p,dict(f,separate_event_reviewed=True));assert confirm(inv,p)['state']=='POSTED'


def test_prepared_worker_identity_and_translation_survive_nonposting_handoff(inv):
    worker=send(inv['c'],f"/entities/{inv['e']}/workers",dict(display_name='TEST Noor',language='fa',source='test:review'))
    source(inv,'Завтра работник починит плёнку в İmamlı.')
    p=queue(inv)[0];p=revise(inv,p,dict(title='TEST repair',original_instruction='TEST original repair',greenhouse_id=inv['g'],planned_date='2026-10-09',worker_id=worker['id'],language='fa',rendered_text='TEST reviewed translation'))
    result=confirm(inv,p);assert result['state']=='CONFIRMED_NONPOSTING'
    detail=inv['c'].get(f"/v1/entities/{inv['e']}/tasks/{result['effect']['id']}").json()
    assert detail['prepared_assignment']==dict(worker_id=worker['id'],language='fa',rendered_text='TEST reviewed translation')
    assert detail['assignments']==[] and detail['status']=='draft'


@pytest.mark.parametrize('field',['inbox_id','input_hash','lease_token','attempt'])
def test_sql_extraction_run_binds_source_job_hash_and_lease(inv,field):
    svc,e,a=service(inv)
    sources=[svc.capture(e,a,str(uuid4()),CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),text='TEST '+str(n))) for n in range(2)]
    with inv['preview'][0].begin() as db:
        job=db.execute(text('SELECT id FROM core.processing_jobs WHERE inbox_id=:s'),dict(s=sources[0]['id'])).scalar_one()
        token=uuid4();db.execute(text("UPDATE core.processing_jobs SET state='RUNNING',attempts=1,lease_token=:t,lease_until=clock_timestamp()+interval '1 minute' WHERE id=:j"),dict(t=token,j=job))
        fp=db.scalar(text('SELECT fingerprint FROM core.inbox_items WHERE id=:s'),dict(s=sources[0]['id']))
    values=dict(id=uuid4(),e=e,s=sources[0]['id'],j=job,h=fp,t=token,n=1)
    values.update({'s':sources[1]['id']} if field=='inbox_id' else {'h':'0'*64} if field=='input_hash' else {'t':uuid4()} if field=='lease_token' else {'n':2})
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            db.execute(text("INSERT INTO core.extraction_runs(id,entity_id,inbox_id,job_id,attempt,provider,engine_version,input_hash,raw_output,normalized,warnings,lease_token) VALUES(:id,:e,:s,:j,:n,'local','TEST',:h,'{}','{}','[]',:t)"),values)


def test_sql_job_identity_and_revision_cannot_substitute_another_original(inv):
    source(inv,'Сегодня полить İmamlı.');source(inv,'Сегодня закрыть окна İmamlı.')
    ps=queue(inv);assert len(ps)==2
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            db.execute(text('UPDATE core.processing_jobs SET inbox_id=:other WHERE inbox_id=:first'),dict(other=ps[1]['inbox_id'],first=ps[0]['inbox_id']))
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            db.execute(text('INSERT INTO core.proposal_revisions(proposal_id,entity_id,version,schema_version,extraction_run_id,payload,provenance,missing,basis,preview_digest,actor_id,reason) SELECT proposal_id,entity_id,version+1,schema_version,:other,payload,provenance,missing,basis,preview_digest,actor_id,reason FROM core.proposal_revisions WHERE proposal_id=:p AND version=1'),dict(other=ps[1]['revision']['extraction_run_id'],p=ps[0]['id']))


def test_depot_adapter_amount_substitution_rolls_back_effect_and_authority(inv,monkeypatch):
    from greenhouse_owner_os.services.harvest import HarvestService
    p=depot(inv)
    f=copy.deepcopy(p['revision']['payload']);f['lines'][0].update(stated_amount='1900',confirmed_amount='2000')
    p=revise(inv,p,f)
    original=HarvestService.settlement
    def corrupted(self,e,a,key,body,**kw):
        ln=body.lines[0].model_copy(update={'confirmed_amount':__import__('decimal').Decimal('1900')})
        return original(self,e,a,key,body.model_copy(update={'lines':(ln,)}),**kw)
    monkeypatch.setattr(HarvestService,'settlement',corrupted)
    confirm(inv,p,expected=422)
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE proposal_id=:p'),dict(p=p['id']))==0
        assert db.scalar(text('SELECT count(*) FROM harvest.depot_settlements WHERE entity_id=:e'),dict(e=inv['e']))==0
        assert db.scalar(text('SELECT count(*) FROM finance.postings WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_depot_default_requires_separate_explicit_owner_choice(inv):
    p=depot(inv);confirm(inv,p)
    assert p['revision']['payload']['remember_quality_mapping'] is False
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            db.execute(text("INSERT INTO harvest.depot_quality_mappings(entity_id,depot_id,original_label,version,quality,proposal_id,accepted_by) VALUES(:e,:d,'grand',1,'HIGHEST',:p,:a)"),dict(e=inv['e'],d=p['revision']['payload']['depot_id'],p=p['id'],a=inv['preview'][1]))


def test_unrelated_owner_grant_rejected_by_database(inv):
    actor,_=other_owner(inv)
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            db.execute(text('INSERT INTO core.owner_entity_access(entity_id,actor_id) VALUES(:e,:a)'),dict(e=inv['e'],a=actor))
