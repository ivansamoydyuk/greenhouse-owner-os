"""R2 transition races, capture contract binding and atomic failure boundaries."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic,sleep
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ConflictError
from greenhouse_owner_os.domain.workers import ActionInput,TaskInput,WorkerInput
from greenhouse_owner_os.services.workers import WorkersService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from tests.integration.test_postgres_workers import setup,act
from tests.integration.test_postgres_workers_r2 import r2,body,respond,current,physical
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_owner_capture import source,queue,revise,confirm


@pytest.mark.parametrize('owner_kind',['reopen','cancel'])
@pytest.mark.parametrize('first',['worker','owner'])
def test_done_versus_owner_transition_serializes_and_preserves_late_evidence(setup,owner_kind,first):
    f,svc,w,_=setup;t,a=r2(setup);respond(setup,w,t,a);respond(setup,w,t,a,'PROBLEM',comment='TEST needs owner decision')
    version=current(setup,t)['version'];held=Event();release=Event();pids={}
    class ControlledUow(SqlAlchemyUnitOfWork):
        def __init__(self,role):super().__init__(f.session_factory);self.role=role
        def __enter__(self):
            super().__enter__();pids[self.role]=self.session.scalar(text('SELECT pg_backend_pid()'));return self
        def commit(self):
            if self.role==first:held.set();assert release.wait(10)
            super().commit()
    worker_service=WorkersService(lambda:ControlledUow('worker'));owner_service=WorkersService(lambda:ControlledUow('owner'))
    command=body(a,'DONE')
    def worker():return worker_service.respond(f.entity_id,UUID(w['actor_id']),'race-worker',UUID(t['id']),UUID(w['id']),command)
    def owner():return owner_service.action(f.entity_id,f.actor_id,'race-owner',UUID(t['id']),ActionInput(kind=owner_kind,expected_version=version,reason='TEST concurrent owner decision',locator=str(uuid4())))
    calls={'worker':worker,'owner':owner};second='owner' if first=='worker' else 'worker'
    with ThreadPoolExecutor(2) as pool:
        one=pool.submit(calls[first]);assert held.wait(10);two=pool.submit(calls[second])
        try:
            blocked=False;until=monotonic()+5
            while monotonic()<until:
                if second in pids:
                    with f.session_factory() as db:blocked=db.scalar(text('SELECT cardinality(pg_blocking_pids(:p))>0'),dict(p=pids[second]))
                    if blocked:break
                sleep(.01)
            assert blocked
        finally:release.set()
        one.result(timeout=10)
        if first=='worker' or owner_kind=='reopen':
            with pytest.raises(ConflictError):two.result(timeout=10)
        else:assert two.result(timeout=10)['task_status']=='cancelled'
    result=current(setup,t)
    assert all(a['started_at'] is None for a in result['assignments'])
    if first=='worker':assert result['status']=='reported' and result['cycle']==1 and len(result['reports'])==2
    elif owner_kind=='reopen':assert result['status']=='reopened' and result['cycle']==2 and len(result['reports'])==1
    else:assert result['status']=='cancelled' and result['cycle']==1 and len(result['reports'])==2
    assert not result['can_complete'] and physical(setup)==[0,0,0]


def test_failure_after_report_before_submission_rolls_back_every_child(setup,monkeypatch):
    f,svc,w,_=setup;t,a=r2(setup);respond(setup,w,t,a);command=body(a,'DONE',comment='TEST preserved retry text')
    def fingerprint():
        with f.session_factory() as db:
            return {table:db.scalar(text('SELECT md5(COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text)::text,\'[]\')) FROM '+table+' r')) for table in ('workers.tasks','workers.assignments','workers.sources','workers.reports','workers.actions','audit.events','audit.idempotency_keys')}
    before=fingerprint();original=svc._action
    def fail(*args,**kw):raise RuntimeError('TEST fault after draft report before submission')
    monkeypatch.setattr(svc,'_action',fail)
    with pytest.raises(RuntimeError):svc.respond(f.entity_id,UUID(w['actor_id']),'fault-response',UUID(t['id']),UUID(w['id']),command)
    assert fingerprint()==before
    monkeypatch.setattr(svc,'_action',original)
    result=svc.respond(f.entity_id,UUID(w['actor_id']),'fault-response',UUID(t['id']),UUID(w['id']),command)
    assert result['report_id'] and len(current(setup,t)['reports'])==1


def test_captured_worker_contract_propagates_and_approved_revision_cannot_mismatch(inv):
    i=inv;sf,actor,*_=i['preview'];svc=WorkersService(lambda:SqlAlchemyUnitOfWork(sf))
    worker=svc.worker(UUID(i['e']),actor,str(uuid4()),WorkerInput(display_name='TEST Muhammed',language='fa',source='test:r2'))
    def proposal():
        s=source(i,'TEST Muhammed завтра проверить давление')
        p=next(p for p in queue(i) if p['inbox_id']==s['id'])
        assert p['action_type']=='WORKER_TASK' and p['revision']['payload']['execution_contract']=='R2_RECEIVED_REPORT'
        fields=dict(p['revision']['payload'],greenhouse_id=i['g'],planned_date='2026-10-09',rendered_text='35 دقیقه، نه 50',language='fa',worker_id=worker['id'])
        return revise(i,p,fields)
    p=proposal();effect=confirm(i,p)['effect']
    assert effect['execution_contract']=='R2_RECEIVED_REPORT' and effect['not_sent'] is True
    assert svc.get(UUID(i['e']),UUID(effect['id']))['assignments']==[]
    # A separately created R1 task cannot be substituted for an R2 approval.
    bad=proposal();f=bad['revision']['payload']
    wrong=svc.create(UUID(i['e']),actor,str(uuid4()),TaskInput(title=f['title'],original_instruction=f['original_instruction'],description=f['original_instruction'],
        greenhouse_id=f['greenhouse_id'],planned_date=f['planned_date'],source='owner:r2-action',locator=bad['id']))
    with pytest.raises(DBAPIError) as exc:
        with sf.begin() as db:
            db.execute(text('INSERT INTO core.confirmation_effects(proposal_id,entity_id,version,actor_id,preview_digest,worker_task_id,response) VALUES(:p,:e,:v,:a,:d,:t,\'{}\')'),dict(p=bad['id'],e=i['e'],v=bad['version'],a=actor,d=bad['revision']['preview_digest'],t=wrong['id']))
            db.execute(text('SET CONSTRAINTS core.approved_worker_contract IMMEDIATE'))
    assert exc.value.orig.sqlstate=='23514' and 'execution contract differs' in str(exc.value)
    with sf() as db:assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE proposal_id=:p'),dict(p=bad['id']))==0
