"""ADR031: explicit current-cycle responses, immutable originals, no START."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from greenhouse_owner_os.domain.workers import TaskInput,AssignmentInput,WorkerInput,WorkerResponseInput,ReportInput,ActionInput
from greenhouse_owner_os.domain.errors import ValidationError,ConflictError,NotFoundError
from greenhouse_owner_os.api.app import create_app
from tests.integration.test_postgres_workers import setup,assign,act,report


def r2(s):
    f,svc,w,old=s
    task=svc.create(f.entity_id,f.actor_id,str(uuid4()),TaskInput(**dict(old['interpretation'],locator=str(uuid4()),execution_contract='R2_RECEIVED_REPORT')))
    assignment=assign(s,w,task,text_value='امروز 35 دقیقه آبیاری کنید؛ 5 kg اضافه نکنید')
    return task,assignment


def body(assignment,response='RECEIVED',cycle=1,**changes):
    return WorkerResponseInput(**dict(dict(client_response_id=uuid4(),assignment_id=assignment['id'],expected_cycle=cycle,response=response,
        captured_at=datetime(2026,10,10,12,tzinfo=timezone.utc),button_label={'RECEIVED':'دریافت شد','DONE':'انجام شد','PROBLEM':'مشکل'}[response],original_language='fa'),**changes))


def respond(s,w,t,a,response='RECEIVED',**kw):
    f,svc,*_=s;command=body(a,response,**kw)
    return svc.respond(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),command)


def current(s,t,w=None):
    f,svc,*_=s;return svc.get(f.entity_id,UUID(t['id']),UUID(w['id']) if w else None)


def physical(s):
    with s[0].session_factory() as db:
        return [db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),dict(e=s[0].entity_id)) for table in ('finance.postings','inventory.movements','harvest.worker_reports')]


def test_r1_stays_r1_and_r2_received_done_never_invents_start(setup):
    f,svc,w,old=setup;t,a=r2(setup);before=physical(setup)
    assert old['execution_contract']=='R1' and 'execution_contract' not in old['interpretation']
    with pytest.raises(ValidationError):respond(setup,w,t,a,'DONE')
    assert current(setup,t)['reports']==[] and current(setup,t)['response_history']==[]
    respond(setup,w,t,a);done=respond(setup,w,t,a,'DONE')
    v=current(setup,t);assert v['can_complete'] and v['status']=='reported'
    assert v['assignments'][0]['acknowledged_at'] and v['assignments'][0]['started_at'] is None
    assert not any(x['kind']=='start' for x in v['actions'])
    assert done['execution_contract']=='R2_RECEIVED_REPORT' and done['cycle']==1
    assert v['reports'][0]['original_text']=='انجام شد' and v['reports'][0]['normalized']['source_kind']=='UI_ACTION'
    assert act(setup,w,t,'complete')['status']=='completed' and physical(setup)==before
    ar1=assign(setup,w,old);act(setup,w,old,'acknowledge',ar1);r=report(setup,w,old,ar1)
    with pytest.raises(ValidationError):act(setup,w,old,'submit_report',ar1,r)
    act(setup,w,old,'start',ar1);act(setup,w,old,'submit_report',ar1,r);assert act(setup,w,old,'complete')['status']=='completed'


def test_problem_before_received_is_evidence_and_requires_owner_review(setup):
    f,svc,w,_=setup;t,a=r2(setup)
    result=respond(setup,w,t,a,'PROBLEM',comment='  آب نیست؛ 35 دقیقه انجام نشد  ')
    v=current(setup,t);assert v['status']=='reported' and not v['can_complete']
    assert v['assignments'][0]['acknowledged_at'] is None and v['assignments'][0]['started_at'] is None
    history=v['response_history'][0];assert history['source_kind']=='UI_ACTION' and history['comment']=='  آب نیست؛ 35 دقیقه انجام نشد  '
    assert v['reports'][0]['original_text']==history['comment']
    respond(setup,w,t,a);respond(setup,w,t,a,'DONE')
    with pytest.raises(ValidationError):act(setup,w,t,'complete')
    act(setup,w,t,'review_report',report={'id':result['report_id']});assert act(setup,w,t,'complete')['status']=='completed'


def test_all_assignments_required_reopen_clears_eligibility_and_preserves_sources(setup):
    f,svc,w,_=setup;t,a=r2(setup)
    w2=svc.worker(f.entity_id,f.actor_id,str(uuid4()),WorkerInput(display_name='TEST R2 second',language='fa',source='test:r2'))
    a2=assign(setup,w2,t,text_value='35 دقیقه؛ 5 kg اضافه نکنید')
    for worker,assignment in ((w,a),(w2,a2)):
        respond(setup,worker,t,assignment);respond(setup,worker,t,assignment,'DONE')
        if worker==w:
            assert not current(setup,t)['can_complete']
            with pytest.raises(ValidationError):act(setup,w,t,'complete')
    act(setup,w,t,'complete');before=current(setup,t);act(setup,w,t,'reopen')
    reopened=current(setup,t);assert reopened['cycle']==2 and not reopened['can_complete']
    assert reopened['reports']==before['reports'] and reopened['response_history']==before['response_history']
    assert all(x['acknowledged_at'] is None and x['started_at'] is None for x in reopened['assignments'])
    with pytest.raises(ConflictError):respond(setup,w,t,a,'DONE')
    respond(setup,w,t,a,cycle=2);respond(setup,w,t,a,'DONE',cycle=2)
    assert not current(setup,t)['can_complete']
    respond(setup,w2,t,a2,cycle=2);respond(setup,w2,t,a2,'DONE',cycle=2)
    assert act(setup,w,t,'complete')['status']=='completed'


def test_stable_response_retry_concurrency_and_changed_original(setup):
    f,svc,w,_=setup;t,a=r2(setup);command=body(a)
    first=svc.respond(f.entity_id,UUID(w['actor_id']),'received-key',UUID(t['id']),UUID(w['id']),command)
    assert svc.respond(f.entity_id,UUID(w['actor_id']),'received-key',UUID(t['id']),UUID(w['id']),command)==first
    assert svc.respond(f.entity_id,UUID(w['actor_id']),'another-key',UUID(t['id']),UUID(w['id']),command)==first
    changed=command.model_copy(update={'button_label':'changed source'})
    with pytest.raises(ConflictError):svc.respond(f.entity_id,UUID(w['actor_id']),'changed',UUID(t['id']),UUID(w['id']),changed)
    done=body(a,'DONE',comment='TEST exact report')
    with ThreadPoolExecutor(2) as pool:
        outcomes=list(pool.map(lambda n:svc.respond(f.entity_id,UUID(w['actor_id']),'done-'+str(n),UUID(t['id']),UUID(w['id']),done),range(2)))
    assert outcomes[0]==outcomes[1]
    v=current(setup,t);assert len(v['reports'])==1 and len(v['response_history'])==2
    act(setup,w,t,'complete');act(setup,w,t,'reopen');before=current(setup,t)
    assert svc.respond(f.entity_id,UUID(w['actor_id']),'done-0',UUID(t['id']),UUID(w['id']),done)==outcomes[0]
    assert current(setup,t)==before
    assert svc.respond(f.entity_id,UUID(w['actor_id']),'new-old-cycle',UUID(t['id']),UUID(w['id']),done)==outcomes[0]
    with pytest.raises(ConflictError):svc.respond(f.entity_id,UUID(w['actor_id']),'changed-old',UUID(t['id']),UUID(w['id']),done.model_copy(update={'comment':'changed'}))
    with pytest.raises(ConflictError):svc.respond(f.entity_id,UUID(w['actor_id']),'new-stale',UUID(t['id']),UUID(w['id']),done.model_copy(update={'client_response_id':uuid4()}))
    assert current(setup,t)==before


def test_legacy_worker_actions_and_sql_cannot_bypass_r2_parent(setup):
    f,svc,w,_=setup;t,a=r2(setup)
    for kind in ('acknowledge','start'):
        with pytest.raises(ValidationError):act(setup,w,t,kind,a)
        with pytest.raises(DBAPIError):
            with f.session_factory.begin() as db:
                db.execute(text("INSERT INTO workers.actions(id,entity_id,task_id,kind,assignment_id,worker_id,cycle,source,locator) VALUES(:id,:e,:t,:kind,:a,:w,1,'test:untyped',:loc)"),dict(id=uuid4(),e=f.entity_id,t=t['id'],kind=kind,a=a['id'],w=w['id'],loc=str(uuid4())))
    draft=report(setup,w,t,a)
    with pytest.raises(ValidationError):act(setup,w,t,'submit_report',a,draft)
    assert current(setup,t)['reports'][0]['state']=='draft' and not current(setup,t)['can_complete']
    # Owner may review retained draft/late evidence without pretending it is DONE.
    act(setup,w,t,'review_report',report=draft)
    assert not current(setup,t)['can_complete']


def test_response_source_sql_requires_exact_parent_and_atomic_children(setup):
    f,svc,w,_=setup;t,a=r2(setup);b=body(a)
    source=dict(schema_version=1,source_kind='UI_ACTION',entity_id=str(f.entity_id),task_id=t['id'],worker_id=w['id'],actor_id=w['actor_id'],execution_contract='R2_RECEIVED_REPORT',**b.model_dump(mode='json'))
    def count():
        with f.session_factory() as db:return db.scalar(text("SELECT count(*) FROM workers.sources WHERE entity_id=:e AND source LIKE 'worker:r2-ui%'"),dict(e=f.entity_id))
    before=count()
    for delta in ({},{'worker_id':str(uuid4())},{'expected_cycle':2},{'source_kind':'VOICE'},{'extra':'untyped'}):
        with pytest.raises(DBAPIError):
            with f.session_factory.begin() as db:
                db.execute(text("INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(:e,'worker:r2-ui',:loc,'r2_ui_response',CAST(:s AS jsonb))"),dict(e=f.entity_id,loc=str(b.client_response_id),s=json.dumps(dict(source,**delta))))
    assert count()==before and current(setup,t)['response_history']==[]
    with pytest.raises(DBAPIError):
        with f.session_factory.begin() as db:
            db.execute(text("INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(:e,'worker:r2-ui:action',:loc,'actions','{}')"),dict(e=f.entity_id,loc=str(uuid4())))
    respond(setup,w,t,a)
    with pytest.raises(DBAPIError):
        with f.session_factory.begin() as db:db.execute(text("UPDATE workers.sources SET snapshot=snapshot||'{\"comment\":\"changed\"}' WHERE entity_id=:e AND source='worker:r2-ui'"),dict(e=f.entity_id))


def test_execution_contract_and_reviewed_rendering_immutable(setup):
    f,svc,w,old=setup;t,a=r2(setup)
    for query in ("UPDATE workers.tasks SET execution_contract='R1' WHERE id=:id", "UPDATE workers.assignments SET rendered_text='changed 50 kg' WHERE task_id=:id"):
        with pytest.raises(DBAPIError):
            with f.session_factory.begin() as db:db.execute(text(query),dict(id=t['id']))
    r1=svc.create(f.entity_id,f.actor_id,str(uuid4()),TaskInput(**old['interpretation']));assert r1['id']==old['id']
    with pytest.raises(ConflictError):svc.create(f.entity_id,f.actor_id,str(uuid4()),TaskInput(**dict(old['interpretation'],execution_contract='R2_RECEIVED_REPORT')))
    assert current(setup,t)['assignments'][0]['rendered_text']=='امروز 35 دقیقه آبیاری کنید؛ 5 kg اضافه نکنید'


def test_scoped_worker_api_and_foreign_assignment_denials(setup):
    f,svc,w,_=setup;t,a=r2(setup);command=body(a)
    client=TestClient(create_app(finance_service=f.service,actor_provider=lambda:f.actor_id,worker_provider=lambda:(f.entity_id,UUID(w['id']),UUID(w['actor_id']))))
    path='/v1/worker/tasks/'+t['id']+'/responses'
    result=client.post(path,json=command.model_dump(mode='json'),headers={'Idempotency-Key':'api-response'})
    assert result.status_code==200,result.text
    assert client.get('/v1/worker/tasks/'+t['id']).json()['response_history'][0]['client_response_id']==str(command.client_response_id)
    no_worker=TestClient(create_app(finance_service=f.service,actor_provider=lambda:f.actor_id))
    assert no_worker.post(path,json=command.model_dump(mode='json'),headers={'Idempotency-Key':'owner-not-worker'}).status_code==401
    bad=body(a,'DONE',assignment_id=uuid4())
    assert client.post(path,json=bad.model_dump(mode='json'),headers={'Idempotency-Key':'foreign-assignment'}).status_code==404
    with pytest.raises(NotFoundError):svc.respond(f.entity_id,f.actor_id,'spoof-owner',UUID(t['id']),UUID(w['id']),body(a,'DONE'))
    svc.active(f.entity_id,f.actor_id,'deactivate',UUID(w['id']),False)
    assert client.post(path,json=body(a,'DONE').model_dump(mode='json'),headers={'Idempotency-Key':'inactive'}).status_code==422


@pytest.mark.parametrize('comment', ['omitted', None, ''], ids=['omitted','null','empty'])
def test_optional_comment_original_preserved_and_button_report_fallback(setup,comment):
    f,svc,w,_=setup;t,a=r2(setup);extra={} if comment=='omitted' else {'comment':comment}
    problem=respond(setup,w,t,a,'PROBLEM',**extra)
    respond(setup,w,t,a,**extra);respond(setup,w,t,a,'DONE',**extra)
    result=current(setup,t);expected=None if comment=='omitted' else comment
    assert [r['comment'] for r in result['response_history']]==[expected]*3
    assert {r['original_text'] for r in result['reports']}=={'مشکل','انجام شد'}
    assert all(r['normalized']['comment_present']==(expected is not None) for r in result['reports'])
    assert result['assignments'][0]['started_at'] is None and not result['can_complete']
    act(setup,w,t,'review_report',report={'id':problem['report_id']});assert current(setup,t)['can_complete']
