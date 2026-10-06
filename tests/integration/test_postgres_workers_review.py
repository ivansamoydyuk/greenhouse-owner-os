"""Independent review: partial teams, non-completion claims and SQL bypass."""
from tests.integration.test_postgres_workers import setup, assign, act, report
from greenhouse_owner_os.domain.workers import WorkerInput, ReportInput, ActionInput
from greenhouse_owner_os.domain.errors import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from uuid import UUID, uuid4
import pytest

def second(s):
    f,svc,w,t=s
    return svc.worker(f.entity_id,f.actor_id,str(uuid4()),WorkerInput(display_name='TEST second',source='test:review'))

@pytest.mark.parametrize('claim',['note','issue'])
def test_non_done_claim_cannot_complete(setup,claim):
    f,svc,w,t=setup;a=assign(setup,w,t)
    act(setup,w,t,'acknowledge',a);act(setup,w,t,'start',a)
    r=svc.report(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(assignment_id=a['id'],original_text='TEST observation',original_language='ru',claim=claim,locator=str(uuid4())))
    act(setup,w,t,'submit_report',a,r)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')

def test_team_cannot_complete_with_only_one_worker_report(setup):
    f,svc,w,t=setup;b=second(setup);a=assign(setup,w,t);assign(setup,b,t)
    act(setup,w,t,'acknowledge',a);act(setup,w,t,'start',a)
    r=report(setup,w,t,a);act(setup,w,t,'submit_report',a,r)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')

def test_team_acknowledgement_is_aggregate(setup):
    f,svc,w,t=setup;b=second(setup);a=assign(setup,w,t);assign(setup,b,t)
    act(setup,w,t,'acknowledge',a)
    assert svc.get(f.entity_id,UUID(t['id']))['status']=='assigned'

def test_done_cannot_bypass_acknowledge_and_start(setup):
    f,svc,w,t=setup;a=assign(setup,w,t);r=report(setup,w,t,a)
    with pytest.raises(ValidationError):act(setup,w,t,'submit_report',a,r)

def test_sql_invalid_translation_source_rejected(setup):
    f,svc,w,t=setup
    with pytest.raises(DBAPIError),f.session_factory.begin() as session:
        session.execute(text("INSERT INTO workers.assignments(id,entity_id,task_id,worker_id,assigned_by,language,rendered_text,translation_source,translation_version,rendering_reviewed) VALUES(:id,:e,:t,:w,:a,'fa','TEST arbitrary','unreviewed','',true)"),dict(id=uuid4(),e=f.entity_id,t=t['id'],w=w['id'],a=f.actor_id))

from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timezone,timedelta
from greenhouse_owner_os.domain.workers import TaskInput,RuleInput
from greenhouse_owner_os.domain.errors import ConflictError,NotFoundError
from greenhouse_owner_os.services.telegram_tasks import TelegramTasksAdapter,TelegramMessage

def started(s,w,t,a):
    act(s,w,t,'acknowledge',a);act(s,w,t,'start',a)

def done(s,w,t,a):
    started(s,w,t,a);r=report(s,w,t,a);act(s,w,t,'submit_report',a,r);return r

def test_two_workers_parallel_then_reopened_cycle(setup):
    f,svc,w,t=setup;b=second(setup);a=assign(setup,w,t);ab=assign(setup,b,t)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda wa:done(setup,*wa),[(w,t,a),(b,t,ab)]))
    current=svc.get(f.entity_id,UUID(t['id']))
    assert current['status']=='reported' and len(current['reports'])==2
    act(setup,w,t,'complete');act(setup,w,t,'reopen')
    with pytest.raises(ValidationError):act(setup,w,t,'submit_report',a,results[0])
    done(setup,w,t,a)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')
    done(setup,b,t,ab);assert act(setup,w,t,'complete')['status']=='completed'

@pytest.mark.parametrize('race',['complete_reopen','report_cancel'])
def test_terminal_and_reopen_races(setup,race):
    f,svc,w,t=setup;a=assign(setup,w,t);r=done(setup,w,t,a)
    version=svc.get(f.entity_id,UUID(t['id']))['version']
    def command(kind):
        try:
            return svc.action(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),ActionInput(kind=kind,expected_version=version,reason='TEST race',locator=str(uuid4())))['status']
        except (ValidationError,ConflictError):return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        if race=='complete_reopen':
            out=list(pool.map(command,['complete','reopen']));assert out.count('conflict')==1
        else:
            x=pool.submit(command,'cancel');y=pool.submit(report,setup,w,t,a,locator='report-race');x.result();assert y.result()['original_text']
    state=svc.get(f.entity_id,UUID(t['id']))
    assert state['status'] in ('completed','reopened','cancelled')
    assert bool(state['completed_at'])==(state['status']=='completed')
    assert bool(state['cancelled_at'])==(state['status']=='cancelled')

def test_note_submission_and_review_clear_attention(setup):
    f,svc,w,t=setup;a=assign(setup,w,t)
    r=svc.report(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(assignment_id=a['id'],claim='note',original_text='TEST note',original_language='ru',locator='note'))
    act(setup,w,t,'submit_report',a,r)
    assert svc.get(f.entity_id,UUID(t['id']))['status']=='assigned'
    act(setup,w,t,'review_report',report=r)
    assert not any('Новый или поздний' in z for item in svc.overview(f.entity_id)['attention'] for z in item['reasons'])

def test_issue_requires_explicit_review_before_all_done_acceptance(setup):
    f,svc,w,t=setup;a=assign(setup,w,t);done(setup,w,t,a)
    r=svc.report(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(assignment_id=a['id'],claim='issue',original_text='TEST issue',original_language='ru',locator='issue'))
    act(setup,w,t,'submit_report',a,r)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')
    act(setup,w,t,'review_report',report=r)
    assert act(setup,w,t,'complete')['status']=='completed'

def test_late_report_explicit_review_preserves_terminal_timestamp(setup):
    f,svc,w,t=setup;a=assign(setup,w,t);done(setup,w,t,a);completed=act(setup,w,t,'complete')
    late=report(setup,w,t,a,locator='late');act(setup,w,t,'submit_report',a,late)
    act(setup,w,t,'review_report',report=late)
    state=svc.get(f.entity_id,UUID(t['id']))
    assert state['completed_at']==completed['completed_at']
    assert not svc.overview(f.entity_id)['attention']

def test_telegram_direct_report_race(setup):
    f,svc,w,t=setup;a=assign(setup,w,t);stamp=datetime.now(timezone.utc)
    event=TelegramMessage(123,7,8,w['telegram_ref'],'TEST report',stamp)
    adapter=TelegramTasksAdapter(svc,f.actor_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        x=pool.submit(adapter.receive,f.entity_id,UUID(t['id']),event)
        y=pool.submit(svc.report,f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(assignment_id=a['id'],original_text=event.text,original_language=w['language'],source='telegram:message',locator='7:8',source_timestamp=stamp))
        assert x.result()['id']==y.result()['id']
    assert svc.get(f.entity_id,UUID(t['id']))['status']=='assigned'

def test_rule_replacement_generation_race(setup):
    f,svc,w,t=setup;rule=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=date(2026,10,1)))
    def generate():
        try:return svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(rule['id']),date(2026,10,10))
        except ValidationError:return None
    def replace():
        try:return svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=date(2026,10,10),replaces_id=rule['id']))
        except ValidationError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        x=pool.submit(generate);y=pool.submit(replace);assert (x.result() is None)!=(y.result() is None)

@pytest.mark.parametrize('kind',['weekly_wrong_day','before_from','after_to','disabled'])
def test_invalid_occurrence_dates(setup,kind):
    f,svc,w,t=setup
    rule=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='weekly',weekday=4,effective_from=date(2026,10,1),effective_to=date(2026,10,10),active=kind!='disabled'))
    d={'weekly_wrong_day':date(2026,10,8),'before_from':date(2026,9,25),'after_to':date(2026,10,16),'disabled':date(2026,10,9)}[kind]
    with pytest.raises(ValidationError):svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(rule['id']),d)

@pytest.mark.parametrize('change',["worker_id=:other","cycle=cycle+1","original_text='changed'","normalized='{}'::jsonb"])
def test_sql_report_mutation_rejected(setup,change):
    f,svc,w,t=setup;a=assign(setup,w,t);r=done(setup,w,t,a)
    with pytest.raises(DBAPIError),f.session_factory.begin() as session:
        session.execute(text('UPDATE workers.reports SET '+change+' WHERE id=:id'),dict(id=r['id'],other=uuid4()))

@pytest.mark.parametrize('kind',['complete','reopen','cancel'])
def test_sql_direct_action_cannot_skip_lifecycle(setup,kind):
    f,svc,w,t=setup
    # Cancellation from draft is legal; completed/reopen without evidence is not.
    if kind=='cancel':
        a=assign(setup,w,t);done(setup,w,t,a);act(setup,w,t,'complete')
    with pytest.raises(DBAPIError),f.session_factory.begin() as session:
        session.execute(text('INSERT INTO workers.actions(entity_id,task_id,kind,actor_id,cycle,reason,source,locator) VALUES(:e,:t,:k,:a,1,\'TEST\',\'sql\',:l)'),dict(e=f.entity_id,t=t['id'],k=kind,a=f.actor_id,l=str(uuid4())))

def test_future_rule_keeps_current_attention(setup):
    f,svc,w,t=setup;today=datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Europe/Istanbul')).date()
    r=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=today))
    svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=today+timedelta(days=1),replaces_id=r['id']))
    assert any(x.get('rule_id')==r['id'] for x in svc.overview(f.entity_id)['attention'])

def test_planned_day_is_overdue_without_inferred_due_time(setup):
    f,svc,w,t=setup;body=TaskInput(**t['interpretation']).model_copy(update=dict(locator='past',planned_date=date(2020,1,1)))
    past=svc.create(f.entity_id,f.actor_id,str(uuid4()),body)
    assert past['due_at'] is None
    assert any('Просрочено' in x['reasons'] for x in svc.overview(f.entity_id)['attention'] if x['id']==past['id'])

@pytest.mark.parametrize('corruption',['worker','cycle','source'])
def test_sql_report_insert_scope_and_source(setup,corruption):
    f,svc,w,t=setup;a=assign(setup,w,t);r=report(setup,w,t,a,locator='original')
    replacement=dict(id=str(uuid4()),locator='new')
    if corruption=='worker':replacement['worker_id']=second(setup)['id']
    if corruption=='cycle':replacement['cycle']=2
    if corruption=='source':replacement['locator']='original';replacement['original_text']='changed'
    import json
    with pytest.raises(DBAPIError),f.session_factory.begin() as session:
        session.execute(text("INSERT INTO workers.reports SELECT (jsonb_populate_record(NULL::workers.reports,to_jsonb(r)||CAST(:change AS jsonb))).* FROM workers.reports r WHERE id=:id"),dict(change=json.dumps(replacement),id=r['id']))

@pytest.mark.parametrize('sql',["UPDATE workers.actions SET reason='changed' WHERE task_id=:t", "DELETE FROM workers.assignments WHERE task_id=:t"])
def test_sql_audit_rewrite_rejected(setup,sql):
    f,svc,w,t=setup;assign(setup,w,t)
    with pytest.raises(DBAPIError),f.session_factory.begin() as session:session.execute(text(sql),dict(t=t['id']))

def test_cross_entity_existing_inbox_link_sql_and_service(setup):
    f,svc,w,t=setup
    from greenhouse_owner_os.domain.workers import LinkInput
    from greenhouse_owner_os.services.inbox import InboxService,InboxInput
    e=uuid4()
    with f.session_factory.begin() as session:session.execute(text("INSERT INTO core.entities(id,name,default_currency) VALUES(:e,'TEST other','TRY')"),dict(e=e))
    item=InboxService(svc.factory).ingest(e,f.actor_id,str(uuid4()),InboxInput(source='test:other',locator=str(uuid4()),raw_text='TEST other document'))
    with pytest.raises(ValidationError):svc.link(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),LinkInput(domain='inbox',target_id=item['id']))
    with pytest.raises(DBAPIError),f.session_factory.begin() as session:
        session.execute(text("INSERT INTO workers.links(entity_id,task_id,domain,target_id,created_by) VALUES(:e,:t,'inbox',:target,:a)"),dict(e=f.entity_id,t=t['id'],target=item['id'],a=f.actor_id))

def test_direct_sql_recurrence_replace_generate_race(setup):
    f,svc,w,t=setup;rule=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=date(2026,10,1)))
    import json
    from threading import Barrier
    barrier=Barrier(2);payload=TaskInput(**t['interpretation']).model_copy(update=dict(source='recurrence',locator='sql-occurrence',planned_date=date(2026,10,10))).model_dump(mode='json')
    def generate():
        try:
            with f.session_factory.begin() as session:
                barrier.wait(timeout=10)
                session.execute(text("INSERT INTO workers.tasks(entity_id,owner_language,title,original_instruction,description,category,priority,planned_date,source,locator,interpretation,created_by,rule_id,template_id,occurrence_date) VALUES(:e,'ru',:title,:raw,:description,:category,'normal','2026-10-10','recurrence','sql-occurrence',CAST(:p AS jsonb),:a,:rule,:template,'2026-10-10')"),dict(e=f.entity_id,a=f.actor_id,title=payload['title'],raw=payload['original_instruction'],description=payload['description'],category=payload['category'],p=json.dumps(payload),rule=rule['id'],template=rule['template_id']))
            return True
        except DBAPIError:return False
    def replace():
        try:
            with f.session_factory.begin() as session:
                barrier.wait(timeout=10)
                session.execute(text("INSERT INTO workers.rules(entity_id,template_id,version,replaces_id,definition,frequency,effective_from,created_by) SELECT entity_id,template_id,2,id,definition,'daily','2026-10-10',created_by FROM workers.rules WHERE id=:id"),dict(id=rule['id']))
            return True
        except DBAPIError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        x=pool.submit(generate);y=pool.submit(replace);assert x.result()!=y.result()

def test_migration_rejects_conflicting_completed_history(setup,monkeypatch):
    f,svc,w,t=setup;a=assign(setup,w,t);r=done(setup,w,t,a);act(setup,w,t,'complete')
    import importlib.util
    spec=importlib.util.spec_from_file_location('workers_review_migration','alembic/versions/20261006_0021_workers_review.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with f.session_factory.begin() as session:
        session.execute(text('ALTER TABLE workers.reports DISABLE TRIGGER report_guard'))
        session.execute(text("UPDATE workers.reports SET claim='note' WHERE id=:id"),dict(id=r['id']))
        session.execute(text('ALTER TABLE workers.reports ENABLE TRIGGER report_guard'))
        with monkeypatch.context() as m:
            m.setattr(module.op,'execute',lambda sql:session.execute(text(sql)))
            with pytest.raises(DBAPIError,match='reconcile explicitly'),session.begin_nested():module.upgrade()
        assert session.scalar(text('SELECT original_text FROM workers.reports WHERE id=:id'),dict(id=r['id']))==r['original_text']
        # Restore synthetic deliberately detached corruption so the shared DB is valid.
        session.execute(text('ALTER TABLE workers.reports DISABLE TRIGGER report_guard'))
        session.execute(text("UPDATE workers.reports SET claim='done' WHERE id=:id"),dict(id=r['id']))
        session.execute(text('ALTER TABLE workers.reports ENABLE TRIGGER report_guard'))

@pytest.mark.parametrize('endpoint',['finance-overview','inventory','harvest','tasks'])
def test_worker_session_denies_owner_routes(setup,endpoint):
    f,svc,w,t=setup;assign(setup,w,t)
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.api.app import create_app
    def owner():raise HTTPException(401,'owner required')
    c=TestClient(create_app(finance_service=f.service,actor_provider=owner,worker_provider=lambda:(f.entity_id,UUID(w['id']),UUID(w['actor_id']))))
    assert c.get('/v1/entities/'+str(f.entity_id)+'/'+endpoint).status_code==401

@pytest.mark.parametrize('source',['unreviewed','original'])
def test_sql_rendering_constraint_fails_on_insert(setup,source):
    f,svc,w,t=setup
    with f.session_factory.begin() as session:
        with pytest.raises(DBAPIError),session.begin_nested():
            session.execute(text("INSERT INTO workers.assignments(entity_id,task_id,worker_id,assigned_by,language,rendered_text,translation_source,translation_version,rendering_reviewed) VALUES(:e,:t,:w,:a,'fa','TEST forged',:s,'',true)"),dict(e=f.entity_id,t=t['id'],w=w['id'],a=f.actor_id,s=source))

def test_inactive_worker_assignment_and_historical_rendering(setup):
    f,svc,w,t=setup;a=assign(setup,w,t,text_value='TEST reviewed Persian')
    svc.active(f.entity_id,f.actor_id,str(uuid4()),UUID(w['id']),False)
    other=svc.create(f.entity_id,f.actor_id,str(uuid4()),TaskInput(**t['interpretation']).model_copy(update={'locator':'other'}))
    with pytest.raises(ValidationError):assign(setup,w,other)
    with pytest.raises(DBAPIError),f.session_factory.begin() as session:session.execute(text("UPDATE workers.people SET language='tr' WHERE id=:id"),dict(id=w['id']))
    assert svc.get(f.entity_id,UUID(t['id']))['assignments'][0]['rendered_text']==a['rendered_text']
