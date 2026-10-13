"""Real PostgreSQL recovery authority, retained context, replay and atomicity."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from greenhouse_owner_os.domain.errors import ConflictError
from greenhouse_owner_os.domain.owner_capture import CaptureSource, ConfirmAction, ExpenseRecoveryAction, ReviseAction
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_owner_capture import source, queue, revise, confirm, confirm_body


def service(i):
    sf,actor,*_=i['preview']
    return OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf)),UUID(i['e']),actor


def recover(i,s,p=None,key=None,expected=200):
    body=dict(origin_proposal_id=p['id'],expected_version=p['version']) if p else {}
    return send(i['c'],f"/entities/{i['e']}/owner/captures/{s['id']}/direct-expense-recovery",body,key=key,expected=expected)


def fill(i,p,**edits):
    return revise(i,p,dict(amount='517.25',currency='TRY',event_date='2026-10-08',money_account_id=i['cash'],description='TEST ручной бензин',**edits))


def counts(i):
    with i['preview'][0]() as db:
        result={table:db.scalar(text(f'SELECT count(*) FROM {table} WHERE entity_id=:e'),{'e':i['e']})
                for table in ('core.proposed_actions','core.proposal_revisions','core.proposal_actions','core.confirmation_effects','core.owner_recovery_links','finance.direct_expenses','finance.postings','finance.obligations','finance.payments','finance.advances')}
        for table in ('audit.events','audit.idempotency_keys'):
            result[table]=db.scalar(text(f'SELECT count(*) FROM {table} WHERE actor_id=:a'),{'a':i['preview'][1]})
        return result


def failed_source(i):
    svc,e,a=service(i)
    s=svc.capture(e,a,str(uuid4()),CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),text='TEST непонятный сохранённый текст 777'))
    with i['preview'][0].begin() as db:
        db.execute(text("UPDATE core.processing_jobs SET state='FAILED',attempts=3,error_code='TEST_EXHAUSTED',lease_token=NULL,lease_until=NULL WHERE inbox_id=:s"),{'s':s['id']})
    return s


def test_unresolved_recovery_has_context_not_invented_fields_and_same_chain(inv):
    s=source(inv,'TEST неопределённая запись 777 TRY');origin=queue(inv)[0]
    assert origin['action_type']=='UNRESOLVED'
    old=deepcopy(origin);p=recover(inv,s,origin,key='handoff')
    assert p['source']['id']==s['id'] and p['source']['text']==s['text']
    assert p['recovery']['origin_proposal_id']==origin['id']
    assert all(p['revision']['payload'][k] is None for k in ('amount','currency','event_date','description','counterparty_id'))
    assert set(p['expense_context']['unknown_fields'])=={'amount','currency','event_date','money_account_id','description'}
    assert recover(inv,s,origin,key='handoff')==p and recover(inv,s,origin)['id']==p['id']
    assert [x['id'] for x in queue(inv)]==[p['id']]
    before=inv['c'].get(f"/v1/entities/{inv['e']}/owner/confirmations/{origin['id']}/preview").json()
    assert before['version']==old['version'] and before['revision']==old['revision'] and not before['reminder_eligible']
    confirm(inv,origin,expected=409)
    send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{origin['id']}/reopen",dict(expected_version=origin['version']),expected=409)
    p=fill(inv,p);assert p['state']=='READY'
    result=confirm(inv,p,key='lost-response');assert result['effect']['counterparty_id'] is None
    assert confirm(inv,p,key='lost-response')==result and confirm(inv,p,key='fresh-confirm')==result
    assert recover(inv,s,origin)['id']==p['id']
    current=inv['c'].get(f"/v1/entities/{inv['e']}/owner/confirmations/{origin['id']}/preview").json()
    assert current['state']=='REJECTED' and current['version']==origin['version']+1
    assert current['revision']['payload']==old['revision']['payload']
    assert current['revision']['provenance']==old['revision']['provenance']
    assert inv['preview'][3].reconcile(entity_id=UUID(inv['e']))['ok']
    assert counts(inv)['finance.direct_expenses']==1


def test_existing_expense_retains_known_fields_and_only_corrected_fact_changes(inv):
    s=source(inv,'Купил бензин за 500 TRY.');p=queue(inv)[0];old=deepcopy(p)
    assert p['action_type']=='DIRECT_EXPENSE'
    handed=recover(inv,s,p)
    assert handed['id']==p['id'] and handed['version']==p['version'] and handed['recovery'] is None
    assert handed['expense_context']['description_kind']=='RETAINED CONTEXT'
    corrected=revise(inv,p,dict(money_account_id=inv['cash']))
    assert corrected['revision']['payload']['amount']==old['revision']['payload']['amount']
    assert corrected['revision']['provenance']['amount']==old['revision']['provenance']['amount']
    assert corrected['revision']['provenance']['currency']==old['revision']['provenance']['currency']
    assert corrected['revision']['provenance']['money_account_id']=={'category':'OWNER INPUT'}
    assert corrected['version']==old['version']+1
    assert counts(inv)['core.owner_recovery_links']==0
    confirm(inv,p,expected=409);confirm(inv,corrected)


@pytest.mark.parametrize('phrase',['Заплатил Серкану 5000','Должен Серкану 7000 за ремонт','Завтра полить İmamlı'])
def test_needs_input_never_authorizes_other_economic_type(inv,phrase):
    s=source(inv,phrase);p=queue(inv)[0]
    assert p['action_type'] != 'DIRECT_EXPENSE'
    if p['action_type']=='UNRESOLVED':assert p['revision']['payload'].get('task_candidates')
    recover(inv,s,p,expected=409)
    assert counts(inv)['core.owner_recovery_links']==0


def test_exhausted_source_reuses_target_and_preparation_is_fenced(inv):
    s=failed_source(inv);p=recover(inv,s)
    assert p['recovery']['origin_proposal_id'] is None and p['recovery']['failed_job_id']
    assert recover(inv,s)['id']==p['id']
    svc,e,a=service(inv);svc.process(UUID(s['id']))
    send(inv['c'],f"/entities/{inv['e']}/owner/captures/{s['id']}/retry-preparation",{},expected=409)
    for sql,params in [
        ("UPDATE core.processing_jobs SET state='QUEUED',attempts=0 WHERE inbox_id=:s",{}),
        ("INSERT INTO core.processing_jobs(id,entity_id,inbox_id,engine_version) VALUES(:id,:e,:s,'new-engine')",{'id':uuid4(),'e':inv['e']}),
        ("INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by) VALUES(:id,:e,:s,'UNRESOLVED:late','UNRESOLVED','PREPARING',1,0,:a)",{'id':uuid4(),'e':inv['e'],'a':a}),
    ]:
        with pytest.raises(DBAPIError):
            with inv['preview'][0].begin() as db:db.execute(text(sql),dict(s=s['id'],**params))
    assert counts(inv)['core.proposed_actions']==1
    original=inv['c'].get(f"/v1/entities/{inv['e']}/owner/captures/{s['id']}/original")
    assert original.content==s['text'].encode()
    confirm(inv,fill(inv,p))


@pytest.mark.parametrize('state,attempts',[('QUEUED',0),('RUNNING',1),('FAILED',2)])
def test_active_or_retryable_sources_do_not_create_manual_target(inv,state,attempts):
    s=failed_source(inv)
    with inv['preview'][0].begin() as db:
        db.execute(text('UPDATE core.processing_jobs SET state=:state,attempts=:n WHERE inbox_id=:s'),dict(state=state,n=attempts,s=s['id']))
    recover(inv,s,expected=409)
    assert counts(inv)['core.proposed_actions']==0


def test_all_engine_jobs_are_considered_not_just_latest(inv):
    s=failed_source(inv)
    with inv['preview'][0].begin() as db:
        db.execute(text("INSERT INTO core.processing_jobs(id,entity_id,inbox_id,engine_version,state,attempts) VALUES(:id,:e,:s,'other-engine','FAILED',1)"),dict(id=uuid4(),e=inv['e'],s=s['id']))
    recover(inv,s,expected=409)
    assert counts(inv)['core.owner_recovery_links']==0


def test_cancel_reload_reject_resume_and_reversal_keep_original_and_target(inv):
    s=source(inv,'TEST неопределённое событие');origin=queue(inv)[0]
    baseline=counts(inv)
    # Opening/closing a source or proposal performs read-only requests.
    inv['c'].get(f"/v1/entities/{inv['e']}/owner/confirmations/{origin['id']}/preview")
    assert counts(inv)==baseline
    p=recover(inv,s,origin);p=fill(inv,p)
    rejected=send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{p['id']}/reject",dict(expected_version=p['version']),expected=200)
    assert recover(inv,s,origin)['id']==p['id'] and counts(inv)['finance.direct_expenses']==0
    opened=send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{p['id']}/reopen",dict(expected_version=rejected['version']),expected=200)
    result=confirm(inv,opened)
    send(inv['c'],f"/entities/{inv['e']}/owner/direct-expenses/{result['effect']['id']}/reverse",dict(expected_version=1,event_date='2026-10-08',reason='TEST reversal'),expected=200)
    current=recover(inv,s,origin)
    assert current['current_effect']['status']=='reversed' and current['recovery']==p['recovery']
    assert confirm(inv,opened)['effect']['status']=='posted' # Immutable historical response.
    assert counts(inv)['finance.direct_expenses']==1 and counts(inv)['core.owner_recovery_links']==1
    assert inv['preview'][3].reconcile(entity_id=UUID(inv['e']))['ok']


def test_independent_same_source_siblings_remain_independent(inv):
    s=source(inv,'TEST неопределённое событие.\nКупил бензин за 321 TRY.\nДля меня завтра полить İmamlı.')
    ps=queue(inv);origin=next(p for p in ps if p['action_type']=='UNRESOLVED');siblings={p['id']:deepcopy(p) for p in ps if p['id']!=origin['id']}
    recovered=fill(inv,recover(inv,s,origin));confirm(inv,recovered)
    for pid,prior in siblings.items():
        p=next(p for p in queue(inv) if p['id']==pid)
        assert p['revision']==prior['revision'] and p['state']==prior['state'] and p['version']==prior['version']
        if p['action_type']=='DIRECT_EXPENSE':p=revise(inv,p,dict(money_account_id=inv['cash']))
        confirm(inv,p)
    assert counts(inv)['finance.direct_expenses']==2


def test_concurrent_different_handoff_and_confirm_keys_reuse_one_identity(inv):
    s=source(inv,'TEST неопределённое событие');origin=queue(inv)[0];svc,e,a=service(inv)
    body=ExpenseRecoveryAction(origin_proposal_id=origin['id'],expected_version=origin['version'])
    with ThreadPoolExecutor(2) as pool:
        targets=list(pool.map(lambda k:svc.recover_expense(e,a,UUID(s['id']),k,body),['recovery-race-a','recovery-race-b']))
    assert targets[0]['id']==targets[1]['id']
    p=fill(inv,targets[0]);b=ConfirmAction(**confirm_body(p))
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda k:svc.confirm(e,a,UUID(p['id']),k,b),['confirm-race-a','confirm-race-b']))
    assert results[0]==results[1] and counts(inv)['finance.direct_expenses']==1


@pytest.mark.parametrize('stage',['after_effect','retirement_audit','outer_audit','before_replay','after_journal'])
def test_injected_failure_rolls_back_effect_lineage_audit_and_replay(inv,monkeypatch,stage):
    s=source(inv,'TEST неопределённое событие');origin=queue(inv)[0];p=fill(inv,recover(inv,s,origin));before=counts(inv)
    svc,e,a=service(inv)
    def fail(*args,**kwargs):raise RuntimeError('TEST injected failure')
    if stage=='after_effect':monkeypatch.setattr(svc,'_retire_recovery_origin',fail)
    elif stage=='retirement_audit':
        old=svc._audit
        def audited(u,p,a,action,*args,**kwargs):
            if action=='REJECT':fail()
            return old(u,p,a,action,*args,**kwargs)
        monkeypatch.setattr(svc,'_audit',audited)
    elif stage=='outer_audit':
        from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyAuditRepository
        monkeypatch.setattr(SqlAlchemyAuditRepository,'append',fail)
    elif stage=='before_replay':
        from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyIdempotencyRepository
        monkeypatch.setattr(SqlAlchemyIdempotencyRepository,'put',fail)
    else:
        from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyFinanceRepository
        original=SqlAlchemyFinanceRepository.add_posting
        def posted(self,*args,**kwargs):
            original(self,*args,**kwargs);self.session.flush();fail()
        monkeypatch.setattr(SqlAlchemyFinanceRepository,'add_posting',posted)
    with pytest.raises(RuntimeError):svc.confirm(e,a,UUID(p['id']),'atomic-retry',ConfirmAction(**confirm_body(p)))
    assert counts(inv)==before
    monkeypatch.undo()
    result=svc.confirm(e,a,UUID(p['id']),'atomic-retry',ConfirmAction(**confirm_body(p)))
    assert result['state']=='POSTED' and counts(inv)['finance.direct_expenses']==1


def test_raw_sql_immutable_link_wrong_scope_and_repeatable_read_fail(inv):
    s=source(inv,'TEST неопределённое событие');origin=queue(inv)[0];p=recover(inv,s,origin)
    sf=inv['preview'][0]
    for sql in ["UPDATE core.owner_recovery_links SET created_at=clock_timestamp() WHERE recovery_proposal_id=:p",
                "DELETE FROM core.owner_recovery_links WHERE recovery_proposal_id=:p",
                "UPDATE core.proposed_actions SET state='READY' WHERE id=:o"]:
        with pytest.raises(DBAPIError):
            with sf.begin() as db:db.execute(text(sql),dict(p=p['id'],o=origin['id']))
    with pytest.raises(DBAPIError,match='READ COMMITTED'):
        with sf.begin() as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
            db.execute(text("UPDATE core.proposed_actions SET state='DEFERRED' WHERE id=:p"),dict(p=p['id']))


def test_raw_sql_source_try_lock_fails_closed_without_deadlock(inv):
    s=source(inv,'TEST неопределённое событие');origin=queue(inv)[0];svc,e,a=service(inv);sf=inv['preview'][0]
    with SqlAlchemyUnitOfWork(sf) as u:
        svc._source_lock(u,e,s['id'])
        def raw_update():
            with sf.begin() as db:
                db.execute(text("SET LOCAL statement_timeout='3s'"))
                db.execute(text("UPDATE core.proposed_actions SET state='DEFERRED' WHERE id=:p"),dict(p=origin['id']))
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(DBAPIError,match='R2 source is busy'):pool.submit(raw_update).result(timeout=5)
    assert next(x for x in queue(inv) if x['id']==origin['id'])['state']==origin['state']


def test_direct_expense_purpose_mismatch_rejected_with_no_partial_effect(inv,monkeypatch):
    s=source(inv,'TEST неопределённое событие');origin=queue(inv)[0];p=fill(inv,recover(inv,s,origin));svc,e,a=service(inv);before=counts(inv)
    original=svc._apply
    def mismatched(u,e,a,p,fields):return original(u,e,a,p,dict(fields,description='TEST unapproved different purpose'))
    monkeypatch.setattr(svc,'_apply',mismatched)
    from greenhouse_owner_os.domain.errors import ValidationError
    with pytest.raises(ValidationError) as error:svc.confirm(e,a,UUID(p['id']),'purpose-mismatch',ConfirmAction(**confirm_body(p)))
    assert 'Expense purpose approval snapshot mismatch' in str(error.value.__cause__)
    assert counts(inv)==before


def test_stale_account_basis_requires_new_exact_preview(inv):
    s=source(inv,'TEST неопределённое событие');origin=queue(inv)[0];p=fill(inv,recover(inv,s,origin))
    with inv['preview'][0].begin() as db:
        db.execute(text("UPDATE finance.money_accounts SET name=name||' revised' WHERE id=:a"),dict(a=inv['cash']))
    stale=confirm(inv,p,expected=409)
    assert stale['state']=='STALE' and counts(inv)['finance.direct_expenses']==0
    confirm(inv,p,expected=409)
    current=inv['c'].get(f"/v1/entities/{inv['e']}/owner/confirmations/{p['id']}/preview").json()
    assert current['version']==p['version']+1
    confirm(inv,current)


def test_cross_owner_source_and_origin_substitution_fail(inv):
    from tests.integration.test_postgres_owner_review import other_owner
    s=source(inv,'TEST неопределённый первый источник');origin=queue(inv)[0]
    other=source(inv,'TEST неопределённый второй источник')
    recover(inv,other,origin,expected=404)
    _,client=other_owner(inv)
    response=client.post(f"/v1/entities/{inv['e']}/owner/captures/{s['id']}/direct-expense-recovery",json=dict(origin_proposal_id=origin['id'],expected_version=origin['version']),headers={'Idempotency-Key':'wrong-owner'})
    assert response.status_code in (403,422) and counts(inv)['core.owner_recovery_links']==0


def raw_target(db,i,s,origin=None,slot=None,kind='DIRECT_EXPENSE'):
    """Create a valid draft plus immutable revision in the caller's real UoW."""
    svc,e,a=service(i);pid=uuid4()
    db.execute(text("""INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by)
        VALUES(:id,:e,:s,:slot,:kind,'PREPARING',1,0,:a)"""),dict(id=pid,e=e,s=s['id'],slot=slot or 'manual:direct-expense:'+str(origin['id'] if origin else s['id']),kind=kind,a=a))
    u=type('SessionUow',(),{'session':db})()
    p=svc._proposal(u,e,pid)
    svc._revision(u,p,{'currency':None} if kind=='DIRECT_EXPENSE' else {'note':'TEST SQL source'}, {},a,'PREPARE')
    return pid


def raw_link(db,i,source_record,origin_record,target_id,**changes):
    values=dict(e=i['e'],s=source_record['id'],origin=origin_record['id'] if origin_record else None,version=origin_record['version'] if origin_record else None,target=target_id,a=i['preview'][1],job=None)
    values.update(changes)
    db.execute(text('''INSERT INTO core.owner_recovery_links(entity_id,inbox_id,origin_proposal_id,origin_version,recovery_proposal_id,created_by,failed_job_id)
        VALUES(:e,:s,:origin,:version,:target,:a,:job)'''),values)


@pytest.mark.parametrize('case',['orphan','wrong_source','wrong_owner','wrong_version','wrong_kind','null_pair','self_link'])
def test_raw_sql_invalid_recovery_contract_is_rejected(inv,case):
    s=source(inv,'TEST неопределённый источник');origin=queue(inv)[0]
    other=source(inv,'TEST второй независимый источник')
    before=counts(inv)
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            target=raw_target(db,inv,s,origin,kind='UNRESOLVED' if case=='wrong_kind' else 'DIRECT_EXPENSE')
            if case!='orphan':
                changes={}
                if case=='wrong_source':changes['s']=other['id']
                if case=='wrong_owner':changes['a']=uuid4()
                if case=='wrong_version':changes['version']=origin['version']+1
                if case=='null_pair':changes['version']=None
                if case=='self_link':changes['origin']=target
                raw_link(db,inv,s,origin,target,**changes)
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    assert counts(inv)==before


def test_two_independent_origins_on_one_source_each_recover(inv):
    s=source(inv,'TEST первый неопределённый фрагмент.\nTEST второй неопределённый фрагмент.')
    # Current conservative parser groups unknown prose. Seed a second valid
    # independent typed slot explicitly to test relational cardinality, not a
    # claim that arbitrary prose is automatically split into two facts.
    with inv['preview'][0].begin() as db:
        raw_target(db,inv,s,slot='UNRESOLVED:1',kind='UNRESOLVED')
    origins=queue(inv);assert len(origins)==2 and all(p['action_type']=='UNRESOLVED' for p in origins)
    first=recover(inv,s,origins[0]);second=recover(inv,s,origins[1])
    assert first['id']!=second['id']
    confirm(inv,fill(inv,first));confirm(inv,fill(inv,second))
    assert counts(inv)['finance.direct_expenses']==2


def test_second_target_and_chain_fail_without_releasing_original(inv):
    s=source(inv,'TEST неопределённый источник');origin=queue(inv)[0];p=recover(inv,s,origin);before=counts(inv)
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            raw_target(db,inv,s,origin) # Stable slot also prevents a second orphan.
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            new=raw_target(db,inv,s,p)
            raw_link(db,inv,s,p,new)
    assert counts(inv)==before


@pytest.mark.parametrize('first',['edit','recovery'])
def test_origin_edit_and_recovery_have_deterministic_serial_order(inv,monkeypatch,first):
    from threading import current_thread
    s=source(inv,'TEST неопределённый источник');origin=queue(inv)[0];svc,e,a=service(inv)
    locked=Event();release=Event();second_attempt=Event();original_lock=svc._source_lock
    def controlled_lock(u,e,s):
        is_first=current_thread().name.startswith('first')
        if not is_first:second_attempt.set()
        original_lock(u,e,s)
        if is_first:
            locked.set();assert release.wait(10),'test coordinator did not release source'
    monkeypatch.setattr(svc,'_source_lock',controlled_lock)
    def edit():return svc.review(e,a,UUID(origin['id']),'race-edit',ReviseAction(expected_version=origin['version'],payload={'note':'TEST corrected original'}),'EDIT')
    def recovery():return svc.recover_expense(e,a,UUID(s['id']),'race-recovery',ExpenseRecoveryAction(origin_proposal_id=origin['id'],expected_version=origin['version']))
    actions={'edit':edit,'recovery':recovery};other='recovery' if first=='edit' else 'edit'
    with ThreadPoolExecutor(1,thread_name_prefix='first') as one,ThreadPoolExecutor(1,thread_name_prefix='second') as two:
        leading=one.submit(actions[first]);assert locked.wait(10)
        following=two.submit(actions[other]);assert second_attempt.wait(10)
        release.set();leading.result(timeout=10)
        with pytest.raises(ConflictError):following.result(timeout=10)
    assert counts(inv)['finance.direct_expenses']==0
    assert counts(inv)['core.owner_recovery_links']==(first=='recovery')


def test_late_preparation_result_is_fenced_after_exhaustion_recovery(inv):
    import base64
    started=Event();release=Event()
    class SlowRecognizer:
        def recognize(self,*args):
            started.set();assert release.wait(10)
            return dict(provider='TEST controlled',version='TEST',text='TEST late неопределённый текст',pages=[],warnings=[])
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf),recognizer=SlowRecognizer());e=UUID(inv['e'])
    binary=b'\x89PNG\r\n\x1a\nTEST retained synthetic bytes'
    s=svc.capture(e,a,'slow-source',CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),mode='photo',file_base64=base64.b64encode(binary).decode(),filename='TEST.png',mime_type='image/png'))
    with sf.begin() as db:db.execute(text('UPDATE core.processing_jobs SET attempts=2 WHERE inbox_id=:s'),dict(s=s['id']))
    with ThreadPoolExecutor(1) as pool:
        worker=pool.submit(svc.process,UUID(s['id']));assert started.wait(10)
        recover(inv,s,expected=409)
        with sf.begin() as db:db.execute(text("UPDATE core.processing_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE inbox_id=:s"),dict(s=s['id']))
        svc.process(UUID(s['id'])) # Terminal expired final lease, no OCR invocation.
        target=recover(inv,s)
        release.set();worker.result(timeout=10)
    assert counts(inv)['core.proposed_actions']==1 and recover(inv,s)['id']==target['id']
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.extraction_runs WHERE inbox_id=:s'),dict(s=s['id']))==0
    assert svc.original(e,a,UUID(s['id']))[0]==binary


def test_opposite_forged_source_pairs_fail_before_any_source_lock(inv,monkeypatch):
    from threading import Barrier,Lock
    from greenhouse_owner_os.domain.errors import NotFoundError
    first=source(inv,'TEST первый неопределённый источник')
    second=source(inv,'TEST второй неопределённый источник')
    proposals={p['inbox_id']:p for p in queue(inv)}
    svc,e,a=service(inv);barrier=Barrier(2);observed=[];guard=Lock();original_lock=svc._source_lock
    def observed_lock(u,entity,source_id):
        with guard:observed.append(str(source_id))
        return original_lock(u,entity,source_id)
    monkeypatch.setattr(svc,'_source_lock',observed_lock)
    def forged(pair):
        requested,foreign=pair;barrier.wait(timeout=5)
        with pytest.raises(NotFoundError):
            svc.recover_expense(e,a,UUID(requested['id']),str(uuid4()),ExpenseRecoveryAction(
                origin_proposal_id=proposals[foreign['id']]['id'],expected_version=proposals[foreign['id']]['version']))
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(forged,pair) for pair in ((first,second),(second,first))]
        for future in futures:future.result(timeout=8)
    assert observed==[]
    assert counts(inv)['core.owner_recovery_links']==0 and counts(inv)['finance.direct_expenses']==0


@pytest.mark.parametrize('first',['confirm','recovery'])
def test_typed_expense_handoff_and_old_confirmation_serialize_on_same_identity(inv,monkeypatch,first):
    from threading import current_thread
    s=source(inv,'Купил бензин за 500 TRY.');p=revise(inv,queue(inv)[0],dict(money_account_id=inv['cash']))
    svc,e,a=service(inv);locked=Event();release=Event();second_attempt=Event();original_lock=svc._source_lock
    def controlled_lock(u,entity,source_id):
        is_first=current_thread().name.startswith('first')
        if not is_first:second_attempt.set()
        original_lock(u,entity,source_id)
        if is_first:
            locked.set();assert release.wait(10)
    monkeypatch.setattr(svc,'_source_lock',controlled_lock)
    actions=dict(confirm=lambda:svc.confirm(e,a,UUID(p['id']),'typed-old-confirm',ConfirmAction(**confirm_body(p))),
        recovery=lambda:svc.recover_expense(e,a,UUID(s['id']),'typed-handoff',ExpenseRecoveryAction(origin_proposal_id=p['id'],expected_version=p['version'])))
    other='recovery' if first=='confirm' else 'confirm'
    with ThreadPoolExecutor(1,thread_name_prefix='first') as one,ThreadPoolExecutor(1,thread_name_prefix='second') as two:
        leading=one.submit(actions[first]);assert locked.wait(10)
        following=two.submit(actions[other]);assert second_attempt.wait(10)
        release.set();results=[leading.result(timeout=10),following.result(timeout=10)]
    assert all(result['id']==p['id'] for result in results)
    assert counts(inv)['finance.direct_expenses']==1 and counts(inv)['core.owner_recovery_links']==0


@pytest.mark.parametrize('order',['effect_first','link_first'])
def test_raw_sql_origin_effect_and_recovery_link_reject_both_orderings(inv,order):
    s=source(inv,'TEST неопределённый источник');origin=queue(inv)[0];before=counts(inv)
    # UNRESOLVED cannot produce a valid typed effect even without recovery. This
    # adversarial provisional effect specifically probes family guards in both
    # SQL operation orders; it is not an authorized old expense confirmation.
    expected='Selected origin is not eligible' if order=='effect_first' else 'Recovery origin cannot create an effect'
    with pytest.raises(DBAPIError,match=expected):
        with inv['preview'][0].begin() as db:
            target=raw_target(db,inv,s,origin)
            def effect():
                db.execute(text('''INSERT INTO core.confirmation_effects(proposal_id,entity_id,version,actor_id,preview_digest,response)
                    VALUES(:p,:e,:v,:a,:digest,'{}'::jsonb)'''),dict(p=origin['id'],e=inv['e'],v=origin['version'],a=inv['preview'][1],digest=origin['revision']['preview_digest']))
            if order=='effect_first':effect()
            raw_link(db,inv,s,origin,target)
            if order=='link_first':effect()
    assert counts(inv)==before


def test_failed_source_target_first_requires_no_other_proposal_at_commit(inv):
    s=failed_source(inv);before=counts(inv)
    with pytest.raises(DBAPIError,match='Exhausted source without proposals required'):
        with inv['preview'][0].begin() as db:
            target=raw_target(db,inv,s)
            raw_target(db,inv,s,slot='UNRESOLVED:extra',kind='UNRESOLVED')
            job=db.scalar(text('SELECT id FROM core.processing_jobs WHERE inbox_id=:s'),dict(s=s['id']))
            raw_link(db,inv,s,None,target,job=job)
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    assert counts(inv)==before


def test_link_first_cannot_reserve_missing_target(inv):
    s=failed_source(inv);before=counts(inv)
    with pytest.raises(DBAPIError,match='Recovery source/target/owner mismatch'):
        with inv['preview'][0].begin() as db:
            job=db.scalar(text('SELECT id FROM core.processing_jobs WHERE inbox_id=:s'),dict(s=s['id']))
            raw_link(db,inv,s,None,uuid4(),job=job)
    assert counts(inv)==before


def contradictory_expense_fields(i,case):
    return {'debt':dict(payment_purpose='debt'),'advance':dict(payment_purpose='advance'),
        'obligation':dict(obligation_id=str(uuid4())),'due_date':dict(due_date='2026-10-10'),
        'attributions':dict(attributions=[dict(work_area_id=i['g'],amount='100')])}[case]


@pytest.mark.parametrize('case',['debt','advance','obligation','due_date','attributions'])
def test_recovery_cannot_silently_drop_unsupported_money_meaning(inv,case):
    s=source(inv,'TEST неопределённый расход');origin=queue(inv)[0];p=fill(inv,recover(inv,s,origin))
    unsupported=contradictory_expense_fields(inv,case)
    p=revise(inv,p,unsupported)
    assert p['state']=='NEEDS_INPUT' and p['revision']['missing']
    assert all(p['revision']['payload'][key]==value for key,value in unsupported.items())
    before=counts(inv);balances=inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()['finance']['balances']
    confirm(inv,p,expected=422)
    assert counts(inv)==before
    assert inv['c'].get(f"/v1/entities/{inv['e']}/owner/home").json()['finance']['balances']==balances


@pytest.mark.parametrize('case',['debt','advance','obligation','due_date','attributions'])
def test_raw_sql_cannot_confirm_unsupported_direct_expense_meaning(inv,case):
    from greenhouse_owner_os.services.owner_capture import js
    from greenhouse_owner_os.services.finance import _hash_request
    s=source(inv,'Купил бензин за 500 TRY.');p=revise(inv,queue(inv)[0],dict(money_account_id=inv['cash']))
    svc,e,a=service(inv);before=counts(inv)
    fields=dict(p['revision']['payload'],**contradictory_expense_fields(inv,case));version=p['version']+1
    digest=_hash_request(dict(id=p['id'],version=version,type='DIRECT_EXPENSE',payload=fields,basis=p['revision']['basis'],missing=[]))
    with pytest.raises(DBAPIError,match='DirectExpense does not support debt, advance, due date or attribution'):
        with SqlAlchemyUnitOfWork(inv['preview'][0]) as u:
            svc._source_lock(u,e,s['id'])
            # Adversarial SQL inserts a structurally valid immutable revision
            # that falsely claims no missing facts, then bypasses _validate.
            u.session.execute(text('''INSERT INTO core.proposal_revisions(proposal_id,entity_id,version,schema_version,payload,provenance,missing,basis,preview_digest,actor_id,reason)
                VALUES(:p,:e,:v,1,CAST(:payload AS jsonb),CAST(:provenance AS jsonb),'[]',CAST(:basis AS jsonb),:digest,:a,'EDIT')'''),
                dict(p=p['id'],e=e,v=version,payload=js(fields),provenance=js(p['revision']['provenance']),basis=js(p['revision']['basis']),digest=digest,a=a))
            u.session.execute(text("UPDATE core.proposed_actions SET version=:v,state='READY' WHERE id=:p"),dict(p=p['id'],v=version))
            actual=svc._proposal(u,e,p['id'])
            effect,_,_=svc._apply(u,e,a,actual,fields)
            svc._audit(u,actual,a,'CONFIRM',version,dict(preview_digest=digest,payload=fields,effect=effect))
            u.session.execute(text("UPDATE core.proposed_actions SET state='POSTED' WHERE id=:p"),dict(p=p['id']))
            u.session.execute(text('''INSERT INTO core.confirmation_effects(proposal_id,entity_id,version,actor_id,preview_digest,direct_expense_id,response)
                VALUES(:p,:e,:v,:a,:digest,:effect,CAST(:response AS jsonb))'''),
                dict(p=p['id'],e=e,v=version,a=a,digest=digest,effect=effect['id'],response=js(effect)))
            u.session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            u.commit()
    assert counts(inv)==before and inv['preview'][3].reconcile(entity_id=e)['ok']


def test_unsupported_expense_obligation_never_locks_real_foreign_debt(inv):
    from tests.integration.test_postgres_owner_review import other_owner
    from tests.integration.test_postgres_readiness import setup
    from greenhouse_owner_os.domain.errors import ValidationError
    foreign_actor,client=other_owner(inv)
    sf,_,refs,finance,_=inv['preview']
    c,foreign_entity,cash,_,party,_=setup((sf,foreign_actor,refs,finance,client))
    foreign=dict(c=c,e=foreign_entity)
    source(foreign,'Должен 7000 TRY за ремонт')
    debt_proposal=revise(foreign,queue(foreign)[0],dict(counterparty_id=party,amount='7000',currency='TRY',event_date='2026-10-08'))
    debt=confirm(foreign,debt_proposal)['effect']
    assert foreign_entity!=inv['e'] and debt['entity_id']==foreign_entity
    saved=source(inv,'TEST неопределённый расход');origin=queue(inv)[0]
    p=fill(inv,recover(inv,saved,origin))
    p=revise(inv,p,dict(obligation_id=debt['id']))
    assert p['state']=='NEEDS_INPUT'
    before=counts(inv)
    class BoundedUow(SqlAlchemyUnitOfWork):
        def __enter__(self):
            super().__enter__()
            self.session.execute(text("SET LOCAL lock_timeout='500ms'"))
            self.session.execute(text("SET LOCAL statement_timeout='2s'"))
            return self
    svc=OwnerCaptureService(lambda:BoundedUow(sf))
    with sf.begin() as locked:
        locked.execute(text('SELECT id FROM finance.obligations WHERE id=:id FOR UPDATE'),dict(id=debt['id']))
        foreign_before=locked.execute(text('SELECT * FROM finance.obligation_balances WHERE obligation_id=:id'),dict(id=debt['id'])).mappings().one()
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(svc.confirm,UUID(inv['e']),inv['preview'][1],UUID(p['id']),'foreign-debt-lock',ConfirmAction(**confirm_body(p)))
            # A real foreign tuple lock is held. A forbidden attempted lock would
            # fail with PostgreSQL lock_timeout, not this normal validation error.
            with pytest.raises(ValidationError,match='Уточните'):future.result(timeout=5)
        foreign_after=locked.execute(text('SELECT * FROM finance.obligation_balances WHERE obligation_id=:id'),dict(id=debt['id'])).mappings().one()
        assert foreign_after==foreign_before
    assert counts(inv)==before


def test_selected_owner_note_remains_visible_separate_from_original_and_expense_fields(inv):
    s=source(inv,'TEST первоначальный нераспознанный текст 777 TRY')
    origin=queue(inv)[0]
    origin=revise(inv,origin,{'note':'TEST уточнение владельца: бензин для поездки, сумму уточню'})
    assert origin['revision']['payload']['note']!=s['text']
    target=recover(inv,s,origin)
    expected=dict(proposal_id=origin['id'],version=origin['version'],
        note=origin['revision']['payload']['note'],provenance=origin['revision']['provenance']['note'])
    assert target['recovery_origin_context']==expected
    assert target['source']['text']==s['text']
    assert target['revision']['payload']['description'] is None
    assert target['revision']['payload']['amount'] is None and target['revision']['payload']['currency'] is None
    target=fill(inv,target);confirm(inv,target)
    for pid in (target['id'],origin['id']):
        current=inv['c'].get(f"/v1/entities/{inv['e']}/owner/confirmations/{pid}/preview").json()
        assert current['recovery_origin_context']==expected
        assert current['source']['text']==s['text']
    assert recover(inv,s,origin)['id']==target['id']
