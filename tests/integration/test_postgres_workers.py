"""WRK-001..036: actual PostgreSQL authority, not an in-memory substitute."""
from uuid import UUID,uuid4
from datetime import date,datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from greenhouse_owner_os.domain.workers import *
from greenhouse_owner_os.domain.errors import ConflictError,ValidationError,NotFoundError
from greenhouse_owner_os.services.workers import WorkersService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.capture import CaptureService,CaptureInput,CaptureConfirm
from greenhouse_owner_os.services.inbox import InboxService,InboxInput,InboxConfirm
from greenhouse_owner_os.api.app import create_app

@pytest.fixture
def setup(pgfx):
    service=WorkersService(lambda:SqlAlchemyUnitOfWork(pgfx.session_factory))
    w=service.worker(pgfx.entity_id,pgfx.actor_id,str(uuid4()),WorkerInput(display_name='TEST Worker A',language='fa',source='test:workers',telegram_ref='test:sender:A'))
    task=service.create(pgfx.entity_id,pgfx.actor_id,str(uuid4()),TaskInput(title='TEST irrigation',original_instruction='Сегодня полить Имамлы 35 минут',description='35 minutes irrigation',category='irrigation',locator=str(uuid4())))
    return pgfx,service,w,task

def assign(s,w,t,*,text_value=None):
    f,svc,_,_=s
    return svc.assign(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),AssignmentInput(worker_id=w['id'],rendered_text=text_value or t['original_instruction'],language=w['language'] if text_value else t['owner_language'],translation_source='owner_reviewed_translation' if text_value else 'original',rendering_reviewed=True))
def act(s,w,t,kind,assignment=None,report=None,reason='TEST owner decision',key=None,locator=None):
    f,svc,_,_=s;current=svc.get(f.entity_id,UUID(t['id']))
    worker=kind in ('acknowledge','start','submit_report')
    return svc.action(f.entity_id,UUID(w['actor_id']) if worker else f.actor_id,key or str(uuid4()),UUID(t['id']),ActionInput(kind=kind,assignment_id=assignment['id'] if assignment else None,report_id=report['id'] if report else None,expected_version=None if worker else current['version'],reason=reason,source='test:worker-action' if worker else 'test:owner-action',locator=locator or str(uuid4())),worker_id=UUID(w['id']) if worker else None)
def report(s,w,t,a,*,locator=None,original='تمام شد',source_timestamp=None):
    f,svc,_,_=s
    return svc.report(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(assignment_id=a['id'],original_text=original,original_language=w['language'],source='telegram:message',locator=locator or str(uuid4()),source_timestamp=source_timestamp,normalized={'duration_minutes':35}))
def reported(s):
    f,svc,w,t=s;a=assign(s,w,t);act(s,w,t,'acknowledge',a);r=report(s,w,t,a);act(s,w,t,'submit_report',a,r);return a,r

def test_wrk_001_009_complete_reopen_cancel_history(setup):
    f,svc,w,t=setup
    assert t['status']=='draft' and not svc.get(f.entity_id,UUID(t['id']))['assignments']
    a=assign(setup,w,t);assert svc.get(f.entity_id,UUID(t['id']))['status']=='assigned'
    act(setup,w,t,'acknowledge',a);assert svc.get(f.entity_id,UUID(t['id']))['status']=='acknowledged'
    act(setup,w,t,'start',a);r=report(setup,w,t,a)
    assert r['state']=='draft' and svc.get(f.entity_id,UUID(t['id']))['status']=='in_progress'
    act(setup,w,t,'submit_report',a,r);assert svc.get(f.entity_id,UUID(t['id']))['status']=='reported'
    completed=act(setup,w,t,'complete');assert completed['status']=='completed'
    act(setup,w,t,'reopen',reason='TEST incomplete');reopened=svc.get(f.entity_id,UUID(t['id']))
    assert reopened['status']=='reopened' and reopened['cycle']==2 and reopened['reports'][0]['original_text']=='تمام شد' and not reopened['assignments'][0]['acknowledged_at']
    act(setup,w,t,'cancel');assert svc.get(f.entity_id,UUID(t['id']))['status']=='cancelled'
    with pytest.raises(ValidationError):act(setup,w,t,'complete')

def test_wrk_003_multiple_workers_one_task(setup):
    f,svc,w,t=setup;a=assign(setup,w,t)
    second=svc.worker(f.entity_id,f.actor_id,str(uuid4()),WorkerInput(display_name='TEST Worker B',language='tr',source='test:workers'))
    assign(setup,second,t);assert len(svc.get(f.entity_id,UUID(t['id']))['assignments'])==2
    assert assign(setup,w,t)['id']==a['id']

def test_wrk_010_012_stable_task_source_and_changed_payload(setup):
    f,svc,w,t=setup;b=TaskInput(**t['interpretation'])
    assert svc.create(f.entity_id,f.actor_id,str(uuid4()),b)['id']==t['id']
    with pytest.raises(ConflictError):svc.create(f.entity_id,f.actor_id,str(uuid4()),b.model_copy(update={'description':'changed'}))
    key=str(uuid4());new=b.model_copy(update={'locator':'different'})
    result=svc.create(f.entity_id,f.actor_id,key,new);assert result['id']!=t['id']
    assert svc.create(f.entity_id,f.actor_id,key,new)==result
    with pytest.raises(ConflictError):svc.create(f.entity_id,f.actor_id,key,new.model_copy(update={'title':'changed'}))

def test_wrk_013_014_recurrence_idempotency_preserves_history(setup):
    f,svc,w,t=setup
    rule=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='weekly',weekday=4,effective_from=date(2026,10,1)))
    d=date(2026,10,9)
    with ThreadPoolExecutor(max_workers=4) as pool:r=list(pool.map(lambda _:svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(rule['id']),d),range(4)))
    assert len({x['id'] for x in r})==1
    later=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']).model_copy(update={'description':'new future instruction'}),frequency='daily',effective_from=date(2026,10,10),replaces_id=rule['id']))
    assert svc.get(f.entity_id,UUID(r[0]['id']))['description']==t['description']
    assert svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(later['id']),date(2026,10,10))['description']=='new future instruction'
    with pytest.raises(ValidationError):svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(rule['id']),date(2026,10,16))
    with pytest.raises(ValidationError):svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='weekly',weekday=4,effective_from=date(2026,10,9),replaces_id=rule['id']))

@pytest.mark.parametrize('category',['fertilizer','harvest','general'])
def test_wrk_015_017_019_no_domain_effects(setup,category):
    f,svc,w,t=setup
    with f.session_factory() as session:before=[session.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),{'e':f.entity_id}) for table in ['finance.postings','inventory.movements','harvest.worker_reports']]
    b=TaskInput(**t['interpretation']).model_copy(update={'category':category,'locator':str(uuid4())});r=svc.create(f.entity_id,f.actor_id,str(uuid4()),b)
    a=assign(setup,w,r);rep=report(setup,w,r,a);act(setup,w,r,'submit_report',a,rep);act(setup,w,r,'complete')
    with f.session_factory() as session:after=[session.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),{'e':f.entity_id}) for table in ['finance.postings','inventory.movements','harvest.worker_reports']]
    assert before==after==[0,0,0]

@pytest.mark.parametrize('source',['text','voice_transcript'])
def test_wrk_020_021_capture_task_draft(setup,source):
    f,svc,w,t=setup;cs=CaptureService(svc.factory)
    c=cs.create(f.entity_id,f.actor_id,str(uuid4()),CaptureInput(text='Завтра открыть окна в Имамлы',source=source,source_namespace='test:capture',source_reference=source))
    assert c['route']=='TASKS'
    reviewed={k:v for k,v in c['extracted'].items() if k not in ('suggested_worker_ids','recurrence_suggestion')}
    r=cs.confirm(f.entity_id,UUID(c['id']),f.actor_id,str(uuid4()),CaptureConfirm(expected_version=1,confirmed=True,reviewed=reviewed))
    assert r['task']['status']=='draft' and r['task']['original_instruction']==c['original_text']

def test_wrk_022_023_inbox_direct_source_race(setup):
    f,svc,w,t=setup;inbox=InboxService(svc.factory);raw='Завтра открыть окна в Имамлы';source='test:upstream';locator='42'
    i=inbox.ingest(f.entity_id,f.actor_id,str(uuid4()),InboxInput(raw_text=raw,source=source,locator=locator))
    assert i['route']=='TASKS'
    fields={k:v for k,v in i['normalized'].items() if k not in ('suggested_worker_ids','recurrence_suggestion')}
    b=TaskInput(**fields,source=source,locator=locator)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(svc.create,f.entity_id,f.actor_id,str(uuid4()),b)
        c=pool.submit(inbox.confirm,f.entity_id,UUID(i['id']),f.actor_id,str(uuid4()),InboxConfirm(expected_version=1,confirmed=True,reviewed=fields,reason='TEST reviewed'))
        direct=a.result();confirmed=c.result()
    assert confirmed['task_id']==direct['id']

@pytest.mark.parametrize('worker_bypass',['finance','other_task','other_entity','complete'])
def test_wrk_024_026_worker_api_isolation(setup,worker_bypass):
    f,svc,w,t=setup;a=assign(setup,w,t)
    from fastapi import HTTPException
    def owner():raise HTTPException(401,'Owner authentication required')
    app=create_app(finance_service=f.service,actor_provider=owner,worker_provider=lambda:(f.entity_id,UUID(w['id']),UUID(w['actor_id'])))
    c=TestClient(app)
    assert c.get('/v1/worker/tasks').status_code==200
    if worker_bypass=='finance':assert c.get('/v1/entities/'+str(f.entity_id)+'/finance-overview').status_code==401
    elif worker_bypass=='complete':assert c.post('/v1/worker/tasks/'+t['id']+'/actions',json=dict(kind='complete',reason='bad',locator='worker'),headers={'Idempotency-Key':str(uuid4())}).status_code==422
    else:assert c.get('/v1/worker/tasks/'+str(uuid4())).status_code==404
    payload=c.get('/v1/worker/tasks/'+t['id']).json();assert 'links' not in payload and len(payload['assignments'])==1

@pytest.mark.parametrize('kind',['assign','acknowledge','report'])
def test_wrk_027_028_concurrency_retry(setup,kind):
    f,svc,w,t=setup
    if kind=='assign':fn=lambda _:assign(setup,w,t)
    else:
        a=assign(setup,w,t)
        if kind=='acknowledge':fn=lambda _:act(setup,w,t,'acknowledge',a,locator='same-ack')
        else:fn=lambda _:report(setup,w,t,a,locator='same-report')
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(fn,range(5)))
    assert len({r['id'] for r in results})==1
    with f.session_factory() as session:
        if kind=='acknowledge':assert session.scalar(text("SELECT count(*) FROM workers.actions WHERE task_id=:t AND kind='acknowledge'"),{'t':t['id']})==1

@pytest.mark.parametrize('iteration',range(5))
def test_wrk_029_complete_cancel_race(setup,iteration):
    f,svc,w,t=setup;reported(setup);version=svc.get(f.entity_id,UUID(t['id']))['version']
    def execute(kind):
        try:return svc.action(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),ActionInput(kind=kind,expected_version=version,reason='TEST race',locator=str(uuid4())))['status']
        except (ConflictError,ValidationError):return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(execute,['complete','cancel']))
    assert r.count('conflict')==1 and svc.get(f.entity_id,UUID(t['id']))['status'] in ('completed','cancelled')

def test_wrk_030_late_report_preserves_terminal_and_timestamps(setup):
    f,svc,w,t=setup;a,r=reported(setup);act(setup,w,t,'complete')
    stamp=datetime.now(timezone.utc)-timedelta(days=1);late=report(setup,w,t,a,source_timestamp=stamp,original='Late report')
    act(setup,w,t,'submit_report',a,late);after=svc.get(f.entity_id,UUID(t['id']))
    assert after['status']=='completed' and datetime.fromisoformat(late['source_timestamp'])==stamp and datetime.fromisoformat(late['received_at'])>stamp
    assert any('поздний' in reason for x in svc.overview(f.entity_id)['attention'] for reason in x['reasons'])

@pytest.mark.parametrize('language,text_value',[('fa','فردا پنجره‌ها را باز کنید'),('tr','Yarın pencereleri açın'),('uz','Ertaga oynalarni oching'),('ru','Завтра открыть окна')])
def test_wrk_033_034_translation_preserves_original(setup,language,text_value):
    f,svc,w,t=setup;worker=svc.worker(f.entity_id,f.actor_id,str(uuid4()),WorkerInput(display_name='TEST '+language,language=language,source='test:workers'))
    a=assign(setup,worker,t,text_value=text_value)
    assert a['rendered_text']==text_value and svc.get(f.entity_id,UUID(t['id']))['original_instruction']==t['original_instruction']
    from greenhouse_owner_os.services.telegram_tasks import FakeDelivery
    adapter=FakeDelivery();assert adapter.present(a,t['original_instruction'])==adapter.present(a,t['original_instruction'])

def test_wrk_035_attention_overdue_reported(setup):
    f,svc,w,t=setup;reported(setup)
    b=TaskInput(**t['interpretation']).model_copy(update={'locator':str(uuid4()),'due_at':datetime.now(timezone.utc)-timedelta(hours=1)})
    svc.create(f.entity_id,f.actor_id,str(uuid4()),b)
    reasons=[r for t in svc.overview(f.entity_id)['attention'] for r in t['reasons']]
    assert 'Просрочено' in reasons and 'Отчёт требует решения владельца' in reasons

@pytest.mark.parametrize('sql',[
 "UPDATE workers.tasks SET status='completed',completed_at=now() WHERE id=:id",
 "UPDATE workers.tasks SET original_instruction='changed' WHERE id=:id",
 "DELETE FROM workers.tasks WHERE id=:id",
 "UPDATE workers.assignments SET acknowledged_at=now() WHERE task_id=:id",
 "UPDATE workers.reports SET original_text='changed' WHERE task_id=:id",
 "DELETE FROM workers.actions WHERE task_id=:id",
])
def test_wrk_036_direct_sql_cannot_rewrite_lifecycle(setup,sql):
    f,svc,w,t=setup;reported(setup);act(setup,w,t,'complete')
    with pytest.raises(DBAPIError),f.session_factory.begin() as session:session.execute(text(sql),{'id':t['id']})
    assert svc.get(f.entity_id,UUID(t['id']))['status']=='completed'

def test_wrk_036_source_edit_conflict_report(setup):
    f,svc,w,t=setup;a=assign(setup,w,t);r=report(setup,w,t,a,locator='telegram-message-1')
    assert report(setup,w,t,a,locator='telegram-message-1')['id']==r['id']
    with pytest.raises(ConflictError):report(setup,w,t,a,locator='telegram-message-1',original='edited')
    assert report(setup,w,t,a,locator='telegram-message-2')['id']!=r['id']

def test_wrk_022_worker_report_inbox_draft_preserves_original(setup):
    f,svc,w,t=setup;a=assign(setup,w,t);inbox=InboxService(svc.factory)
    i=inbox.ingest(f.entity_id,f.actor_id,str(uuid4()),InboxInput(source='telegram:message',locator='chat:999',raw_text='تمام شد',metadata=dict(task_report=True,sender_reference=w['telegram_ref'],task_id=t['id'],assignment_id=a['id'],language='fa')))
    assert i['route']=='WORKER_REPORTS'
    r=inbox.confirm(f.entity_id,UUID(i['id']),f.actor_id,str(uuid4()),InboxConfirm(expected_version=1,confirmed=True,reason='TEST review',reviewed=i['normalized']))
    task=svc.get(f.entity_id,UUID(t['id']));assert task['status']=='assigned' and task['reports'][0]['id']==r['task_report_id'] and task['reports'][0]['state']=='draft' and task['reports'][0]['original_text']=='تمام شد'

def test_telegram_identity_retries_edits_unknown_worker(setup):
    f,svc,w,t=setup;a=assign(setup,w,t)
    from greenhouse_owner_os.services.telegram_tasks import TelegramTasksAdapter,TelegramMessage
    adapter=TelegramTasksAdapter(svc,f.actor_id);stamp=datetime(2026,10,6,6,tzinfo=timezone.utc)
    message=TelegramMessage(1,100,20,w['telegram_ref'],'تمام شد',stamp)
    r=adapter.receive(f.entity_id,UUID(t['id']),message)
    assert adapter.receive(f.entity_id,UUID(t['id']),message)['id']==r['id']
    with pytest.raises(ConflictError):adapter.receive(f.entity_id,UUID(t['id']),TelegramMessage(2,100,20,w['telegram_ref'],'edited',stamp))
    other=adapter.receive(f.entity_id,UUID(t['id']),TelegramMessage(3,100,21,w['telegram_ref'],'تمام شد',stamp));assert other['id']!=r['id']
    unknown=adapter.receive(f.entity_id,UUID(t['id']),TelegramMessage(4,100,22,'unknown','message',stamp));assert unknown['route']=='WORKER_REPORTS' and unknown['validation']['missing_fields']

def test_wrk_026_true_cross_entity_and_other_worker_rejected(setup):
    f,svc,w,t=setup;a=assign(setup,w,t)
    worker_b=svc.worker(f.entity_id,f.actor_id,str(uuid4()),WorkerInput(display_name='TEST Worker B',source='test:worker'))
    with pytest.raises(NotFoundError):svc.get(f.entity_id,UUID(t['id']),UUID(worker_b['id']))
    with pytest.raises(NotFoundError):svc.get(uuid4(),UUID(t['id']))
    with pytest.raises(NotFoundError):svc.report(f.entity_id,UUID(worker_b['actor_id']),str(uuid4()),UUID(t['id']),UUID(worker_b['id']),ReportInput(assignment_id=a['id'],original_text='bad',original_language='ru',locator='bad'))
    with pytest.raises(ValidationError):svc.assign(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),AssignmentInput(worker_id=w['id'],language='ru',rendered_text='changed original',translation_source='original',rendering_reviewed=True))

def test_owner_complete_vs_new_report_is_valid_serialized_state(setup):
    f,svc,w,t=setup;a,r=reported(setup);version=svc.get(f.entity_id,UUID(t['id']))['version']
    def complete():
        try:return svc.action(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),ActionInput(kind='complete',expected_version=version,reason='TEST accept',locator=str(uuid4())))
        except ConflictError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        done=pool.submit(complete);late=pool.submit(report,setup,w,t,a,locator='late-race');done.result();new=late.result()
    state=svc.get(f.entity_id,UUID(t['id']));assert state['status'] in ('completed','reported') and any(r['id']==new['id'] for r in state['reports'])

@pytest.mark.parametrize('domain',['inventory','harvest','finance','inbox'])
def test_wrk_016_018_domain_link_requires_existing_same_entity(setup,domain):
    f,svc,w,t=setup
    with pytest.raises(ValidationError):svc.link(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),LinkInput(domain=domain,target_id=uuid4()))
    assert not svc.get(f.entity_id,UUID(t['id']))['links']

def test_wrk_018_real_harvest_link_does_not_duplicate_evidence(setup):
    f,svc,w,t=setup
    from greenhouse_owner_os.services.harvest import HarvestService
    from greenhouse_owner_os.domain.harvest import ReportInput as HarvestReportInput
    hr=HarvestService(svc.factory).report(f.entity_id,f.actor_id,str(uuid4()),HarvestReportInput(event_date=date(2026,10,6),original_greenhouse='Unknown test greenhouse',reporter='TEST worker',cut_count=1,evidence=dict(source='test:harvest-link',source_reference=str(uuid4()),raw_text='TEST one bunch')))
    link=svc.link(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),LinkInput(domain='harvest',target_id=hr['id']))
    assert link['target_id']==hr['id']
    with f.session_factory() as session:assert session.scalar(text('SELECT count(*) FROM harvest.worker_reports WHERE entity_id=:e'),{'e':f.entity_id})==1


def test_wrk_016_inventory_actual_link_does_not_duplicate_stock_effect(setup):
    from greenhouse_owner_os.services.inventory import InventoryService
    from greenhouse_owner_os.domain.inventory import ProductInput,LocationInput,InventoryInput,InventoryPost
    f,svc,w,t=setup; inventory=InventoryService(svc.factory)
    p=inventory.product(f.entity_id,f.actor_id,str(uuid4()),ProductInput(name='TEST actual KNO3',base_unit='kg',source='test:verified'))
    l=inventory.location(f.entity_id,f.actor_id,str(uuid4()),LocationInput(name='TEST stock',source='test:verified'))
    greenhouse=uuid4()
    with f.session_factory.begin() as session:
        session.execute(text("INSERT INTO core.work_areas(id,entity_id,name,kind,source) VALUES(:g,:e,'TEST greenhouse','greenhouse','test:verified')"),dict(g=greenhouse,e=f.entity_id))
    line=dict(product_id=p['id'],original_product_name='KNO3',quantity='100',unit='kg',location_id=l['id'],greenhouse_id=str(greenhouse))
    receipt=inventory.create(f.entity_id,f.actor_id,str(uuid4()),InventoryInput(kind='receipt',event_date=date(2026,10,6),lines=[line]))
    inventory.post(f.entity_id,f.actor_id,str(uuid4()),UUID(receipt['id']),InventoryPost(confirmed=True,expected_version=1))
    application=inventory.create(f.entity_id,f.actor_id,str(uuid4()),InventoryInput(kind='application',event_date=date(2026,10,6),lines=[dict(line,quantity='15')]))
    inventory.post(f.entity_id,f.actor_id,str(uuid4()),UUID(application['id']),InventoryPost(confirmed=True,expected_version=1))
    with f.session_factory() as session:
        before=session.scalar(text('SELECT sum(quantity) FROM inventory.movements WHERE entity_id=:e'),{'e':f.entity_id})
    link=svc.link(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),LinkInput(domain='inventory',target_id=application['id']))
    assert link['target_id']==application['id']
    a,r=reported(setup);act(setup,w,t,'complete')
    with f.session_factory() as session:
        assert session.scalar(text('SELECT sum(quantity) FROM inventory.movements WHERE entity_id=:e'),{'e':f.entity_id})==before==85
        assert session.scalar(text('SELECT count(*) FROM inventory.events WHERE entity_id=:e'),{'e':f.entity_id})==2


def test_service_identity_cannot_impersonate_another_worker(setup):
    f,svc,w,t=setup;a=assign(setup,w,t)
    other=svc.worker(f.entity_id,f.actor_id,str(uuid4()),WorkerInput(display_name='TEST Other identity',language='ru',source='test:synthetic'))
    with pytest.raises(NotFoundError):
        svc.action(f.entity_id,UUID(other['actor_id']),str(uuid4()),UUID(t['id']),ActionInput(kind='acknowledge',assignment_id=a['id'],locator=str(uuid4())),worker_id=UUID(w['id']))
    with pytest.raises(NotFoundError):
        svc.report(f.entity_id,UUID(other['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(assignment_id=a['id'],original_text='TEST impersonation',original_language='ru',locator=str(uuid4())))
    with pytest.raises(NotFoundError):
        svc.create(f.entity_id,UUID(w['actor_id']),str(uuid4()),TaskInput(**t['interpretation']).model_copy(update={'locator':str(uuid4())}))


def test_wrk_036_sql_assignment_without_audited_action_rejects(setup):
    f,svc,w,t=setup
    with pytest.raises(DBAPIError):
        with f.session_factory.begin() as session:
            session.execute(text("INSERT INTO workers.assignments(id,entity_id,task_id,worker_id,assigned_by,language,rendered_text,translation_source,translation_version,rendering_reviewed) VALUES(:id,:e,:t,:w,:a,'ru',:raw,'original','TEST',true)"),dict(id=uuid4(),e=f.entity_id,t=t['id'],w=w['id'],a=f.actor_id,raw=t['original_instruction']))
    assert svc.get(f.entity_id,UUID(t['id']))['assignments']==[]
