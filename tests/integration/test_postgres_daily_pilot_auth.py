"""Real PostgreSQL identity/session tests; network adapter separately E2E verified."""
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID,uuid4
import secrets
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.daily_pilot import PilotAuth,COOKIE,digest,receipt_status
from greenhouse_owner_os.domain.workers import WorkerInput
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.services.references import ReferenceService,provision_authenticated_owner
from greenhouse_owner_os.services.workers import WorkersService
from greenhouse_owner_os.synthetic_pilot import setup_synthetic_profile,OWNER_REF,OWNER_NAME

EXPIRY=(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat()
PASSWORD=secrets.token_urlsafe(24)
SHA='35826c5a7f66140779e4884e53c84bd990b94723'

@pytest.fixture
def pilot_auth(migrated_database):
    sf=make_session_factory(migrated_database);factory=lambda:SqlAlchemyUnitOfWork(sf)
    actor=provision_authenticated_owner(sf,external_ref=OWNER_REF,display_name=OWNER_NAME,environment='TEST_PREVIEW')
    setup=create_app(finance_service=FinanceService(factory),reference_service=ReferenceService(factory,environment='TEST_PREVIEW'),actor_provider=lambda:actor)
    # The reusable seed has a deliberately stable public-test entity. Each auth
    # case needs its own entity and request namespace so its assigned tasks do
    # not contaminate the retained seed/replay contract (which starts unassigned).
    namespace='daily-auth-'+str(uuid4())
    class IsolatedSeedClient(TestClient):
        def post(self,url,**kwargs):
            headers=dict(kwargs.get('headers') or {})
            if 'Idempotency-Key' in headers:
                headers['Idempotency-Key']=namespace+':'+headers['Idempotency-Key']
            kwargs['headers']=headers
            if url=='/v1/entities':
                kwargs['json']={**kwargs['json'],'name':kwargs['json']['name']+' '+namespace}
            return super().post(url,**kwargs)
    with IsolatedSeedClient(setup) as seed_client:
        profile=setup_synthetic_profile(seed_client,factory,actor)
    eid=UUID(profile['entity']['id'])
    auth=PilotAuth(factory,eid,actor,expires_at=EXPIRY,build_sha=SHA,secure=False)
    owner=auth.provision('daily-owner',PASSWORD)
    worker=WorkersService(factory).worker(eid,actor,str(uuid4()),WorkerInput(display_name='TEST Daily Worker '+str(uuid4()),language='fa',source='test:daily-auth'))
    username='w-'+str(uuid4());pid=auth.provision(username,PASSWORD,worker_id=worker['id'])
    return SimpleNamespace(auth=auth,factory=factory,owner=owner,worker=worker,pid=pid,username=username,setup=setup)


def request(token):return SimpleNamespace(cookies={COOKIE:token})

def test_independent_sessions_logout_and_restart(pilot_auth):
    a=pilot_auth.auth;t1,p1=a.login('daily-owner',PASSWORD,'test-peer');t2,p2=a.login('daily-owner',PASSWORD,'test-peer')
    assert digest(t1)!=digest(t2) and a.authenticate(request(t1))['id']==UUID(pilot_auth.owner)
    a.logout(request(t1),p1)
    with pytest.raises(HTTPException) as exc:a.authenticate(request(t1))
    assert exc.value.status_code==401
    restarted=PilotAuth(a.factory,a.entity_id,a.owner_id,expires_at=EXPIRY,build_sha=SHA,secure=False)
    assert restarted.authenticate(request(t2))['id']==p2['id']
    with pytest.raises(HTTPException):restarted.authenticate(request(t1))
    with a.factory() as u:
        hashes=list(u.session.scalars(text('SELECT token_hash FROM core.pilot_sessions WHERE participant_id=:p'),dict(p=p1['id'])))
        assert digest(t1) in hashes and t1 not in hashes
        assert digest(u.session.scalar(text('SELECT password_hash FROM core.pilot_participants WHERE id=:p'),dict(p=p1['id'])))!=digest(PASSWORD)


def test_worker_revoke_invalidates_every_session_and_replay(pilot_auth):
    a=pilot_auth.auth;_,owner=a.login('daily-owner',PASSWORD,'owner-peer')
    t,p=a.login(pilot_auth.username,PASSWORD,'worker-peer');t2,_=a.login(pilot_auth.username,PASSWORD,'worker-peer')
    assert a.authenticate(request(t))['worker_id']==UUID(pilot_auth.worker['id'])
    r=a.revoke(owner,p['id'],1);assert r['version']==2 and r['enabled'] is False
    assert a.revoke(owner,p['id'],1)==r
    for token in (t,t2):
        with pytest.raises(HTTPException) as exc:a.authenticate(request(token))
        assert exc.value.status_code==401
    with pytest.raises(HTTPException):a.login(pilot_auth.username,PASSWORD,'worker-peer')
    with a.factory() as u:
        assert u.session.scalar(text("SELECT count(*) FROM core.pilot_access_events WHERE participant_id=:p AND kind='REVOKE'"),dict(p=p['id']))==1


def test_worker_cannot_revoke_or_list_others(pilot_auth):
    a=pilot_auth.auth;_,p=a.login(pilot_auth.username,PASSWORD,'worker-peer')
    for fn in (lambda:a.participants(p),lambda:a.revoke(p,UUID(pilot_auth.owner),1)):
        with pytest.raises(HTTPException) as exc:fn()
        assert exc.value.status_code==403


def test_stale_and_cross_scope_revoke_refused(pilot_auth):
    a=pilot_auth.auth;_,p=a.login('daily-owner',PASSWORD,'owner-peer')
    for pid,v,status in ((UUID(pilot_auth.pid),2,409),(uuid4(),1,404),(p['id'],1,422)):
        with pytest.raises(HTTPException) as exc:a.revoke(p,pid,v)
        assert exc.value.status_code==status


def test_concurrent_revoke_has_one_audit_result(pilot_auth):
    a=pilot_auth.auth;_,p=a.login('daily-owner',PASSWORD,'owner-peer')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:a.revoke(p,UUID(pilot_auth.pid),1),range(2)))
    assert results[0]==results[1]
    with a.factory() as u:
        assert u.session.scalar(text("SELECT count(*) FROM core.pilot_access_events WHERE participant_id=:p AND kind='REVOKE'"),dict(p=pilot_auth.pid))==1


def test_expiry_restart_cannot_extend_and_expired_session_denied(pilot_auth,monkeypatch):
    a=pilot_auth.auth;token,p=a.login('daily-owner',PASSWORD,'owner-peer')
    with pytest.raises(ValueError):PilotAuth(a.factory,a.entity_id,a.owner_id,expires_at=(a.expires_at+timedelta(hours=1)).isoformat(),build_sha=SHA,secure=False)
    monkeypatch.setattr('greenhouse_owner_os.daily_pilot.now',lambda:a.expires_at+timedelta(seconds=1))
    for fn in (lambda:a.authenticate(request(token)),lambda:a.login('daily-owner',PASSWORD,'owner-peer')):
        with pytest.raises(HTTPException) as exc:fn()
        assert exc.value.status_code==410


def test_disabled_worker_rechecked_without_relogin(pilot_auth):
    a=pilot_auth.auth;t,p=a.login(pilot_auth.username,PASSWORD,'worker-peer')
    WorkersService(a.factory).active(a.entity_id,a.owner_id,str(uuid4()),UUID(pilot_auth.worker['id']),False)
    with pytest.raises(HTTPException) as exc:a.authenticate(request(t))
    assert exc.value.status_code==401


def test_rate_limit_is_persistent_and_bad_users_indistinguishable(pilot_auth):
    a=pilot_auth.auth
    for name in ('absent-user',pilot_auth.username):
        with pytest.raises(HTTPException) as exc:a.login(name,'not-a-real-password','bad-peer')
        assert exc.value.status_code==401 and exc.value.detail=='Не удалось войти. Проверьте имя и пароль.'
    for _ in range(8):
        with pytest.raises(HTTPException) as exc:a.login('absent-user','not-a-real-password','bad-peer')
        assert exc.value.status_code==401
    restarted=PilotAuth(a.factory,a.entity_id,a.owner_id,expires_at=EXPIRY,build_sha=SHA,secure=False)
    with pytest.raises(HTTPException) as exc:restarted.login('daily-owner',PASSWORD,'bad-peer')
    assert exc.value.status_code==429


def test_identity_and_expiry_sql_guards(pilot_auth):
    a=pilot_auth.auth
    statements=[('UPDATE core.pilot_deployments SET expires_at=expires_at+interval \'1 hour\' WHERE id=:id',a.deployment_id),
      ("UPDATE core.pilot_participants SET role='owner',worker_id=NULL WHERE id=:id",UUID(pilot_auth.pid)),
      ('DELETE FROM core.pilot_participants WHERE id=:id',UUID(pilot_auth.pid))]
    for sql,identity in statements:
        with pytest.raises(DBAPIError):
            with a.factory() as u:u.session.execute(text(sql),dict(id=identity));u.commit()


def test_cookie_response_and_validation_hide_credentials(pilot_auth):
    a=pilot_auth.auth;a.secure=True;app=FastAPI();a.mount(app);c=TestClient(app,base_url='https://pilot.test')
    r=c.post('/pilot/login',json={'username':'daily-owner','password':PASSWORD})
    assert r.status_code==200
    cookie=r.headers['set-cookie'];assert all(x in cookie for x in ('HttpOnly','Secure','SameSite=strict'))
    assert 'password' not in r.json() and 'token' not in r.json()
    assert c.get('/pilot/session').json()['role']=='owner'
    assert c.post('/pilot/logout',json={}).status_code==200 and c.get('/pilot/session').status_code==401


def test_receipt_checks_real_domain_operation_and_target(pilot_auth):
    a=pilot_auth.auth;_,p=a.login('daily-owner',PASSWORD,'owner-peer')
    client=TestClient(pilot_auth.setup);root='/v1/entities/'+str(a.entity_id)
    body=dict(client_capture_id=str(uuid4()),captured_at='2026-10-11T12:00:00+03:00',timezone='Europe/Istanbul',mode='text',text='8500 насос ремонт')
    key='test-daily-source-'+str(uuid4());r=client.post(root+'/owner/captures',json=body,headers={'Idempotency-Key':key});assert r.status_code in (200,201)
    assert receipt_status(a,p,root+'/owner/captures',key)['state']=='COMMITTED'
    assert receipt_status(a,p,root+'/owner/captures','absent-key')['state']=='NOT_FOUND'
    assert receipt_status(a,p,root+'/fertilizer/plans',key)['state']=='NOT_FOUND'
    with pytest.raises(HTTPException) as exc:receipt_status(a,p,'/v1/entities/'+str(uuid4())+'/owner/captures',key)
    assert exc.value.status_code==403
    # Real created task has a cached response; a different target cannot inherit it.
    wkey='task-'+str(uuid4());task=client.post(root+'/tasks',json=dict(title='TEST Receipt binding',description='TEST',original_instruction='TEST receipt binding '+str(uuid4()),source='test:daily',locator=str(uuid4())),headers={'Idempotency-Key':wkey})
    assert task.status_code==201
    tid=task.json()['id'];action_key='cancel-'+str(uuid4())
    canceled=client.post(root+'/tasks/'+tid+'/actions',json=dict(kind='cancel',expected_version=task.json()['version'],reason='TEST only',locator=str(uuid4())),headers={'Idempotency-Key':action_key})
    assert canceled.status_code==200
    assert receipt_status(a,p,root+'/tasks/'+tid+'/actions',action_key)['state']=='COMMITTED'
    assert receipt_status(a,p,root+'/tasks/'+str(uuid4())+'/actions',action_key)['state']=='UNKNOWN'


def test_worker_receipt_cannot_probe_owner_operations(pilot_auth):
    a=pilot_auth.auth;_,p=a.login(pilot_auth.username,PASSWORD,'worker-peer')
    with pytest.raises(HTTPException) as exc:receipt_status(a,p,'/v1/payments','known-or-guessed-key')
    assert exc.value.status_code==403


def test_draft_keys_are_private_stable_and_revoked(pilot_auth):
    a=pilot_auth.auth;app=FastAPI();a.mount(app)
    owner=TestClient(app);worker=TestClient(app);anonymous=TestClient(app)
    assert anonymous.get('/pilot/draft-key').status_code==401
    owner.post('/pilot/login',json={'username':'daily-owner','password':PASSWORD})
    worker.post('/pilot/login',json={'username':pilot_auth.username,'password':PASSWORD})
    first=owner.get('/pilot/draft-key').json();other=worker.get('/pilot/draft-key').json()
    assert digest(first['key'])!=digest(other['key']) and first['participant_id']!=other['participant_id']
    assert len(first['key'])==43 and 'key' not in owner.get('/pilot/session').json()
    assert digest(owner.get('/pilot/draft-key?participant_id='+pilot_auth.pid).json()['key'])==digest(first['key'])
    owner.post('/pilot/logout',json={});assert owner.get('/pilot/draft-key').status_code==401
    owner.post('/pilot/login',json={'username':'daily-owner','password':PASSWORD})
    assert digest(owner.get('/pilot/draft-key').json()['key'])==digest(first['key'])
    assert owner.post('/pilot/participants/'+pilot_auth.pid+'/revoke',json={'expected_version':1}).status_code==200
    assert worker.get('/pilot/draft-key').status_code==401


def test_cookie_switch_cannot_reassign_captured_worker_intent(pilot_auth):
    """Both workers can act on this task; cookie swap must not switch intent."""
    from fastapi import Request
    from greenhouse_owner_os.domain.workers import TaskInput,AssignmentInput
    a=pilot_auth.auth;svc=WorkersService(a.factory)
    second=svc.worker(a.entity_id,a.owner_id,str(uuid4()),WorkerInput(display_name='TEST second assigned worker',language='ru',source='test:pilot-intent'))
    second_name='w-'+str(uuid4());a.provision(second_name,PASSWORD,worker_id=second['id'])
    _,first=a.login(pilot_auth.username,PASSWORD,'first-peer')
    token,second_identity=a.login(second_name,PASSWORD,'second-peer')
    original='TEST source '+str(uuid4())
    task=svc.create(a.entity_id,a.owner_id,str(uuid4()),TaskInput(title='TEST shared assignment',description=original,original_instruction=original,source='test:pilot-intent',locator=str(uuid4())))
    for worker in (pilot_auth.worker,second):
        svc.assign(a.entity_id,a.owner_id,str(uuid4()),UUID(task['id']),AssignmentInput(worker_id=worker['id'],rendered_text=original,language='ru',rendering_reviewed=True))
    def worker_identity(request:Request):
        identity=a.authenticate(request)
        a.require_expected_participant(request,identity,required=request.method!='GET')
        return identity['entity_id'],identity['worker_id'],identity['actor_id']
    app=create_app(finance_service=FinanceService(a.factory),actor_provider=lambda:a.owner_id,worker_provider=worker_identity)
    c=TestClient(app);c.cookies.set(COOKIE,token)
    path='/v1/worker/tasks/'+task['id']+'/actions'
    body=dict(kind='acknowledge',expected_cycle=1,source='worker:mobile',locator=str(uuid4()))
    headers={'Idempotency-Key':'intent-'+str(uuid4()),'X-Pilot-Participant':str(first['id'])}
    with a.factory() as u:
        before=u.session.scalar(text('SELECT count(*) FROM workers.actions WHERE task_id=:t'),dict(t=task['id']))
    assert c.post(path,json=body,headers=headers).status_code==409
    missing={k:v for k,v in headers.items() if k!='X-Pilot-Participant'}
    assert c.post(path,json=body,headers=missing).status_code==409
    with a.factory() as u:
        assert u.session.scalar(text('SELECT count(*) FROM workers.actions WHERE task_id=:t'),dict(t=task['id']))==before
    headers['X-Pilot-Participant']=str(second_identity['id'])
    assert c.post(path,json=body,headers=headers).status_code==200
    with a.factory() as u:
        rows=list(u.session.execute(text("SELECT worker_id FROM workers.actions WHERE task_id=:t AND kind='acknowledge'"),dict(t=task['id'])))
        assert rows==[(UUID(second['id']),)]


def test_collection_receipt_cannot_borrow_same_owner_other_entity(pilot_auth):
    a=pilot_auth.auth;_,p=a.login('daily-owner',PASSWORD,'owner-peer')
    client=TestClient(pilot_auth.setup)
    other=client.post('/v1/entities',json=dict(name='TEST separate receipt scope '+str(uuid4()),currency='TRY',source='test:daily-scope'),headers={'Idempotency-Key':'entity-'+str(uuid4())})
    assert other.status_code==201
    other_id=other.json()['id'];path='/v1/entities/'+other_id+'/owner/captures'
    key='cross-entity-source-'+str(uuid4())
    body=dict(client_capture_id=str(uuid4()),captured_at='2026-10-11T12:00:00+03:00',timezone='Europe/Istanbul',mode='text',text='TEST separate original')
    created=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert created.status_code in (200,201)
    own_path='/v1/entities/'+str(a.entity_id)+'/owner/captures'
    assert receipt_status(a,p,own_path,key)=={'state':'UNKNOWN','retry_same_identity':True}
    # The same stored command is proven only when its authenticated entity matches.
    other_auth=PilotAuth(a.factory,UUID(other_id),a.owner_id,expires_at=EXPIRY,build_sha=SHA,secure=False)
    other_auth.provision('daily-other-owner',PASSWORD)
    _,other_participant=other_auth.login('daily-other-owner',PASSWORD,'other-owner-peer')
    assert receipt_status(other_auth,other_participant,path,key)['state']=='COMMITTED'
    with a.factory() as u:
        assert u.session.scalar(text('SELECT entity_id FROM core.inbox_items WHERE id=:id'),dict(id=created.json()['id']))==UUID(other_id)
