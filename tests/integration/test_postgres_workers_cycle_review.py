"""Independent adversarial pre-merge verification; PostgreSQL is authoritative."""
from tests.integration.test_postgres_workers import setup, assign, act, report, reported
from greenhouse_owner_os.domain.workers import *
from greenhouse_owner_os.domain.errors import ConflictError, ValidationError
from uuid import UUID, uuid4
from datetime import date
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from concurrent.futures import ThreadPoolExecutor
import pytest

def second(s):
    f,svc,w,t=s
    return svc.worker(f.entity_id,f.actor_id,str(uuid4()),WorkerInput(display_name='TEST second',source='test:review'))

def test_all_assignees_must_report_done_before_acceptance(setup):
    f,svc,w,t=setup; a=assign(setup,w,t); wb=second(setup); ab=assign(setup,wb,t)
    act(setup,w,t,'acknowledge',a)
    assert svc.get(f.entity_id,UUID(t['id']))['status']=='assigned'
    act(setup,w,t,'start',a)
    assert svc.get(f.entity_id,UUID(t['id']))['status']=='in_progress'
    r=report(setup,w,t,a);act(setup,w,t,'submit_report',a,r)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')
    act(setup,wb,t,'acknowledge',ab);act(setup,wb,t,'start',ab)
    rb=report(setup,wb,t,ab);act(setup,wb,t,'submit_report',ab,rb)
    assert act(setup,w,t,'complete')['status']=='completed'

@pytest.mark.parametrize('claim',['note','issue'])
def test_note_or_issue_cannot_satisfy_owner_completion(setup,claim):
    f,svc,w,t=setup;a=assign(setup,w,t)
    r=svc.report(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(cycle=1,assignment_id=a['id'],original_text='TEST not done',original_language='fa',claim=claim,locator=str(uuid4())))
    act(setup,w,t,'submit_report',a,r)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')

def test_superseded_occurrence_can_continue_its_lifecycle(setup):
    f,svc,w,t=setup
    rule=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=date(2026,10,1)))
    occurrence=svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(rule['id']),date(2026,10,2))
    svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=date(2026,10,3),replaces_id=rule['id']))
    a=assign(setup,w,occurrence);act(setup,w,occurrence,'acknowledge',a);act(setup,w,occurrence,'start',a)
    r=report(setup,w,occurrence,a);act(setup,w,occurrence,'submit_report',a,r)
    assert act(setup,w,occurrence,'complete')['status']=='completed'

def test_new_issue_invalidates_older_done_claim(setup):
    f,svc,w,t=setup;a,r=reported(setup)
    issue=svc.report(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(cycle=1,assignment_id=a['id'],original_text='TEST work failed',original_language='fa',claim='issue',locator=str(uuid4())))
    act(setup,w,t,'submit_report',a,issue)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')

@pytest.mark.parametrize('kind',['complete','reopen'])
def test_owner_terminal_races_have_one_legal_winner(setup,kind):
    f,svc,w,t=setup;reported(setup);version=svc.get(f.entity_id,UUID(t['id']))['version']
    def run(k):
        try:return svc.action(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),ActionInput(kind=k,reason='TEST race',expected_version=version,locator=str(uuid4())))['status']
        except (ConflictError,ValidationError):return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(run,[kind,'cancel' if kind=='complete' else 'complete']))
    assert r.count('conflict')==1

def test_reopened_cycle_rejects_previous_report(setup):
    f,svc,w,t=setup;a,r=reported(setup);act(setup,w,t,'reopen')
    with pytest.raises((ValidationError,ConflictError)):act(setup,w,t,'submit_report',a,r)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')

def test_concurrent_workers_are_isolated(setup):
    f,svc,w,t=setup;a=assign(setup,w,t);wb=second(setup);ab=assign(setup,wb,t)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda p:act(setup,p[0],t,'acknowledge',p[1]),[(w,a),(wb,ab)]))
    detail=svc.get(f.entity_id,UUID(t['id']));assert all(x['acknowledged_at'] for x in detail['assignments'])
    assert detail['status']=='acknowledged'

def test_direct_sql_cannot_accept_only_one_assignee(setup):
    f,svc,w,t=setup;a=assign(setup,w,t);assign(setup,second(setup),t)
    act(setup,w,t,'acknowledge',a);act(setup,w,t,'start',a);r=report(setup,w,t,a);act(setup,w,t,'submit_report',a,r)
    with pytest.raises(DBAPIError),f.session_factory.begin() as s:
        s.execute(text("INSERT INTO workers.actions(entity_id,task_id,kind,actor_id,cycle,reason,source,locator) VALUES(:e,:t,'complete',:a,1,'TEST bad accept','test:sql',:l)"),dict(e=f.entity_id,t=t['id'],a=f.actor_id,l=str(uuid4())))

def test_late_report_attention_can_be_explicitly_reviewed(setup):
    f,svc,w,t=setup;a,r=reported(setup);act(setup,w,t,'complete');late=report(setup,w,t,a,original='TEST late note')
    assert any(x['id']==t['id'] for x in svc.overview(f.entity_id)['attention'])
    act(setup,w,t,'review_report',report=late,reason='TEST preserved and reviewed late source')
    assert not any(x['id']==t['id'] for x in svc.overview(f.entity_id)['attention'])
    assert svc.get(f.entity_id,UUID(t['id']))['status']=='completed'

def test_future_rule_replacement_does_not_hide_today_attention(setup):
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    f,svc,w,t=setup;today=datetime.now(ZoneInfo('Europe/Istanbul')).date()
    rule=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=today))
    svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=today+timedelta(days=1),active=False,replaces_id=rule['id']))
    assert any(x.get('rule_id')==rule['id'] for x in svc.overview(f.entity_id)['attention'])
    svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(rule['id']),today)
    assert not any(x.get('rule_id')==rule['id'] for x in svc.overview(f.entity_id)['attention'])

def test_report_cancel_race_preserves_source_and_terminal_state(setup):
    f,svc,w,t=setup;a=assign(setup,w,t)
    with ThreadPoolExecutor(max_workers=2) as pool:
        incoming=pool.submit(report,setup,w,t,a,locator='report-cancel-race')
        cancel=pool.submit(act,setup,w,t,'cancel')
        r=incoming.result();cancel.result()
    state=svc.get(f.entity_id,UUID(t['id']))
    assert state['status']=='cancelled' and any(x['id']==r['id'] for x in state['reports'])

def test_recurrence_version_generation_race(setup):
    f,svc,w,t=setup
    rule=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=date(2026,10,1)))
    def replace():
        try:return svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='daily',effective_from=date(2026,10,2),replaces_id=rule['id']))
        except (ConflictError,ValidationError):return None
    def generate():
        try:return svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(rule['id']),date(2026,10,2))
        except (ConflictError,ValidationError):return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(replace);b=pool.submit(generate);results=[a.result(),b.result()]
    assert sum(x is not None for x in results)==1

def test_telegram_and_direct_report_race(setup):
    from greenhouse_owner_os.services.telegram_tasks import TelegramMessage,TelegramTasksAdapter
    from datetime import datetime,timezone
    f,svc,w,t=setup;a=assign(setup,w,t);stamp=datetime.now(timezone.utc)
    event=TelegramMessage(789,100,900,w['telegram_ref'],'TEST same original',stamp,cycle=1)
    b=ReportInput(cycle=1,assignment_id=a['id'],original_text=event.text,original_language=w['language'],source='telegram:message',locator='100:900',source_timestamp=stamp)
    with ThreadPoolExecutor(max_workers=2) as pool:
        direct=pool.submit(svc.report,f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),b)
        telegram=pool.submit(TelegramTasksAdapter(svc,f.actor_id).receive,f.entity_id,UUID(t['id']),event)
        assert direct.result()['id']==telegram.result()['id']

def test_telegram_update_identity_cannot_change_message_or_kind(setup):
    from greenhouse_owner_os.services.telegram_tasks import TelegramMessage,TelegramTasksAdapter
    from datetime import datetime,timezone
    f,svc,w,t=setup;assign(setup,w,t);stamp=datetime.now(timezone.utc);adapter=TelegramTasksAdapter(svc,f.actor_id)
    adapter.receive(f.entity_id,UUID(t['id']),TelegramMessage(789,100,900,w['telegram_ref'],'TEST original',stamp,cycle=1))
    with pytest.raises(ConflictError):adapter.receive(f.entity_id,UUID(t['id']),TelegramMessage(789,100,901,w['telegram_ref'],'TEST original',stamp,cycle=1))
    with pytest.raises(ConflictError):adapter.receive(f.entity_id,UUID(t['id']),TelegramMessage(789,100,900,w['telegram_ref'],'TEST original',stamp,cycle=1),kind='acknowledge')

def test_worker_payload_omits_owner_interpretation(setup):
    f,svc,w,t=setup;assign(setup,w,t)
    r=svc.get(f.entity_id,UUID(t['id']),UUID(w['id']))
    assert all(k not in r for k in ['description','interpretation','gaps','links','created_by'])

def test_stale_worker_form_cannot_acknowledge_or_complete_new_cycle(setup):
    f,svc,w,t=setup;a,r=reported(setup);act(setup,w,t,'reopen')
    with pytest.raises(ConflictError):
        svc.action(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),ActionInput(kind='acknowledge',expected_cycle=1,assignment_id=a['id'],locator=str(uuid4())),worker_id=UUID(w['id']))
    act(setup,w,t,'acknowledge',a);act(setup,w,t,'start',a)
    late=svc.report(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(cycle=1,assignment_id=a['id'],original_text='TEST late old cycle',original_language='fa',locator=str(uuid4())))
    assert late['cycle']==1 and svc.get(f.entity_id,UUID(t['id']))['cycle']==2
    with pytest.raises(ValidationError):act(setup,w,t,'submit_report',a,late)
    with pytest.raises(ValidationError):act(setup,w,t,'complete')

def test_same_timestamp_report_different_retry_key_reuses_original(setup):
    from datetime import datetime,timezone
    f,svc,w,t=setup;a=assign(setup,w,t);stamp=datetime.now(timezone.utc)
    first=report(setup,w,t,a,source_timestamp=stamp,locator='timestamp-identity')
    assert report(setup,w,t,a,source_timestamp=stamp,locator='timestamp-identity')['id']==first['id']

def test_unknown_telegram_cycle_requires_review(setup):
    from greenhouse_owner_os.services.telegram_tasks import TelegramMessage,TelegramTasksAdapter
    from datetime import datetime,timezone
    f,svc,w,t=setup;assign(setup,w,t)
    item=TelegramTasksAdapter(svc,f.actor_id).receive(f.entity_id,UUID(t['id']),TelegramMessage(19,100,90,w['telegram_ref'],'TEST old message',datetime.now(timezone.utc)))
    assert item['route']=='WORKER_REPORTS' and 'cycle' not in item['normalized']
    assert svc.get(f.entity_id,UUID(t['id']))['reports']==[]

@pytest.mark.parametrize('mode',['duplicate_source','duplicate_occurrence','wrong_weekday','changed_rule_instruction'])
def test_sql_source_and_recurrence_identity_rejects(setup,mode):
    import json
    f,svc,w,t=setup
    if mode=='duplicate_source':p=t
    else:
        rule=svc.rule(f.entity_id,f.actor_id,str(uuid4()),RuleInput(task=TaskInput(**t['interpretation']),frequency='weekly',weekday=4,effective_from=date(2026,10,1)))
        p=svc.occurrence(f.entity_id,f.actor_id,str(uuid4()),UUID(rule['id']),date(2026,10,9))
    with pytest.raises(DBAPIError),f.session_factory.begin() as s:
        if mode=='wrong_weekday':
            p=dict(p,locator=p['template_id']+':2026-10-10',occurrence_date='2026-10-10',planned_date='2026-10-10');p['interpretation']=dict(p['interpretation'],locator=p['locator'],planned_date=p['planned_date'])
        if mode=='changed_rule_instruction':
            p=dict(p,locator=p['template_id']+':2026-10-16',occurrence_date='2026-10-16',planned_date='2026-10-16',description='TEST fabricated');p['interpretation']=dict(p['interpretation'],locator=p['locator'],planned_date=p['planned_date'],description=p['description'])
        s.execute(text("INSERT INTO workers.tasks(entity_id,owner_language,title,original_instruction,description,category,priority,planned_date,source,locator,interpretation,created_by,rule_id,template_id,occurrence_date) VALUES(:entity_id,:owner_language,:title,:original_instruction,:description,:category,:priority,:planned_date,:source,:locator,CAST(:snapshot AS jsonb),:created_by,:rule_id,:template_id,:occurrence_date)"),dict(p,snapshot=json.dumps(p['interpretation'])))

def test_inactive_worker_cannot_receive_assignment(setup):
    f,svc,w,t=setup;svc.active(f.entity_id,f.actor_id,str(uuid4()),UUID(w['id']),False)
    with pytest.raises(ValidationError):assign(setup,w,t)

def test_irrigation_report_scope_duration_survives_submission(setup):
    f,svc,w,t=setup;greenhouse,area=uuid4(),uuid4()
    with f.session_factory.begin() as s:
        s.execute(text("INSERT INTO core.work_areas(id,entity_id,name,kind,source) VALUES(:g,:e,'TEST greenhouse','greenhouse','test:review')"),dict(g=greenhouse,e=f.entity_id))
        s.execute(text("INSERT INTO core.work_areas(id,entity_id,parent_id,name,kind,source) VALUES(:a,:e,:g,'TEST section','work_area','test:review')"),dict(a=area,e=f.entity_id,g=greenhouse))
    task=svc.create(f.entity_id,f.actor_id,str(uuid4()),TaskInput(**t['interpretation']).model_copy(update={'locator':str(uuid4()),'greenhouse_id':greenhouse,'work_area_id':area}))
    a=assign(setup,w,task);act(setup,w,task,'acknowledge',a);act(setup,w,task,'start',a)
    r=svc.report(f.entity_id,UUID(w['actor_id']),str(uuid4()),UUID(task['id']),UUID(w['id']),ReportInput(cycle=1,assignment_id=a['id'],original_text='TEST 35 minutes',original_language='fa',normalized=dict(work_area_id=str(area),duration_minutes=35),locator=str(uuid4())))
    act(setup,w,task,'submit_report',a,r)
    assert svc.get(f.entity_id,UUID(task['id']))['reports'][0]['normalized']==dict(work_area_id=str(area),duration_minutes=35)

@pytest.mark.parametrize('operation',['cross_entity','wrong_worker','wrong_cycle','changed_action','changed_translation','unknown_translation'])
def test_direct_sql_critical_corruption_rejects(setup,operation):
    f,svc,w,t=setup;a,r=reported(setup);wb=second(setup)
    with pytest.raises(DBAPIError),f.session_factory.begin() as s:
        if operation=='cross_entity':
            s.execute(text("INSERT INTO workers.assignments(entity_id,task_id,worker_id,assigned_by,language,rendered_text,translation_source,translation_version,rendering_reviewed) VALUES(:e,:t,:w,:a,'ru','TEST','original','TEST',true)"),dict(e=uuid4(),t=t['id'],w=w['id'],a=f.actor_id))
        elif operation in ('wrong_worker','wrong_cycle'):
            s.execute(text("INSERT INTO workers.reports(entity_id,task_id,assignment_id,worker_id,cycle,original_text,original_language,claim,source,locator) VALUES(:e,:t,:asgn,:w,:cycle,'TEST','fa','done','test:sql',:l)"),dict(e=f.entity_id,t=t['id'],asgn=a['id'],w=wb['id'] if operation=='wrong_worker' else w['id'],cycle=99 if operation=='wrong_cycle' else 1,l=str(uuid4())))
        elif operation=='changed_action':s.execute(text("UPDATE workers.actions SET reason='changed' WHERE task_id=:t"),dict(t=t['id']))
        elif operation=='changed_translation':s.execute(text("UPDATE workers.assignments SET rendered_text='changed' WHERE id=:id"),dict(id=a['id']))
        else:
            other=svc.create(f.entity_id,f.actor_id,str(uuid4()),TaskInput(**t['interpretation']).model_copy(update={'locator':str(uuid4())}))
            s.execute(text("INSERT INTO workers.assignments(entity_id,task_id,worker_id,assigned_by,language,rendered_text,translation_source,translation_version,rendering_reviewed) VALUES(:e,:t,:w,:a,'fa','TEST','unreviewed-ai','TEST',true)"),dict(e=f.entity_id,t=other['id'],w=w['id'],a=f.actor_id))

@pytest.mark.parametrize('field,value',[('claim','issue'),('cycle',2),('normalized',{'duration_minutes':99}),('worker_id','00000000-0000-0000-0000-000000000001')])
def test_sql_inbox_report_cannot_misrepresent_reviewed_snapshot(setup,field,value):
    from greenhouse_owner_os.services.inbox import InboxService,InboxInput
    import json
    f,svc,w,t=setup;a=assign(setup,w,t)
    i=InboxService(svc.factory).ingest(f.entity_id,f.actor_id,str(uuid4()),InboxInput(source='telegram:message',locator='snapshot-guard',raw_text='TEST preserved report',metadata=dict(task_report=True,sender_reference=w['telegram_ref'],task_id=t['id'],assignment_id=a['id'],language='fa',cycle=1)))
    r=svc.report(f.entity_id,f.actor_id,str(uuid4()),UUID(t['id']),UUID(w['id']),ReportInput(cycle=1,assignment_id=a['id'],original_text=i['raw_text'],original_language='fa',source=i['source'],locator=i['locator']))
    fields=dict(i['normalized']);fields[field]=value
    snapshot=dict(expected_version=1,confirmed=True,reason='TEST SQL bypass',reviewed=fields)
    with pytest.raises(DBAPIError,match='Inbox worker report review/assignment snapshot mismatch'),f.session_factory.begin() as s:
        s.execute(text("UPDATE core.inbox_items SET task_report_id=:r,state='CONFIRMED',version=2,reviewed=CAST(:p AS jsonb),reviewed_by=:a,reviewed_at=clock_timestamp() WHERE id=:i"),dict(r=r['id'],p=json.dumps(snapshot),a=f.actor_id,i=i['id']))
