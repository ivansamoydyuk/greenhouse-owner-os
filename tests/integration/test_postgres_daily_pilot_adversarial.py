"""Independent adversarial checks against real PostgreSQL participant state."""
from datetime import timedelta
from uuid import UUID,uuid4
import base64
import hashlib
import json
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.daily_pilot import PilotAuth,receipt_status
from test_postgres_daily_pilot_auth import pilot_auth,request,PASSWORD,SHA,EXPIRY


def client_for(auth):
    app=FastAPI();auth.mount(app)
    return TestClient(app)


@pytest.mark.parametrize('body',[
 {'username':'daily-owner','password':'SECRET_SENTINEL_'+'x'*201},
 {'password':'SECRET_SENTINEL_validlong'},
 {'username':'daily-owner','password':'SECRET_SENTINEL_validlong','extra':'PRIVATE_SENTINEL'},
 {'username':'daily-owner','password':{'private':'SECRET_SENTINEL_nested'}},
])
def test_login_validation_never_echoes_credentials(pilot_auth,body):
    response=client_for(pilot_auth.auth).post('/pilot/login',json=body)
    assert response.status_code==422
    assert 'SECRET_SENTINEL' not in response.text
    assert 'PRIVATE_SENTINEL' not in response.text
    assert 'input' not in response.text


def test_dek_restart_is_private_and_cannot_be_replaced(pilot_auth):
    a=pilot_auth.auth;c=client_for(a)
    assert c.post('/pilot/login',json={'username':pilot_auth.username,'password':PASSWORD}).status_code==200
    original=c.get('/pilot/draft-key').json()
    assert len(base64.urlsafe_b64decode(original['key']+'='))==32
    for path in ('/pilot/session','/pilot/public-config'):
        response=c.get(path)
        assert original['key'] not in response.text
    restarted=PilotAuth(a.factory,a.entity_id,a.owner_id,expires_at=EXPIRY,build_sha=SHA,secure=False)
    d=client_for(restarted)
    assert d.post('/pilot/login',json={'username':pilot_auth.username,'password':PASSWORD}).status_code==200
    assert hashlib.sha256(json.dumps(d.get('/pilot/draft-key').json(),sort_keys=True).encode()).hexdigest()==hashlib.sha256(json.dumps(original,sort_keys=True).encode()).hexdigest()
    with pytest.raises(DBAPIError):
        with a.factory() as u:
            u.session.execute(text("UPDATE core.pilot_participants SET draft_key=decode(repeat('00',32),'hex') WHERE id=:p"),dict(p=pilot_auth.pid));u.commit()
    _,owner=a.login('daily-owner',PASSWORD,'independent-owner')
    a.revoke(owner,UUID(pilot_auth.pid),1)
    assert c.get('/pilot/draft-key').status_code==401
    assert d.get('/pilot/draft-key').status_code==401


def test_logout_session_cannot_fetch_draft_key_even_after_restart(pilot_auth):
    a=pilot_auth.auth;c=client_for(a)
    assert c.post('/pilot/login',json={'username':'daily-owner','password':PASSWORD}).status_code==200
    token=c.cookies.get('pilot_session')
    assert c.post('/pilot/logout',json={}).status_code==200
    d=client_for(PilotAuth(a.factory,a.entity_id,a.owner_id,expires_at=EXPIRY,build_sha=SHA,secure=False))
    d.cookies.set('pilot_session',token)
    assert d.get('/pilot/draft-key').status_code==401
    assert d.get('/pilot/session').status_code==401


def test_wrong_worker_cannot_probe_task_receipt(pilot_auth):
    a=pilot_auth.auth;owner=TestClient(pilot_auth.setup);root='/v1/entities/'+str(a.entity_id)
    created=owner.post(root+'/tasks',json=dict(title='TEST Unassigned private task',description='TEST',original_instruction='TEST private '+str(uuid4()),source='test:independent-pilot',locator=str(uuid4())),headers={'Idempotency-Key':str(uuid4())})
    assert created.status_code==201
    tid=created.json()['id'];key=str(uuid4())
    cancel=owner.post(root+'/tasks/'+tid+'/actions',json=dict(kind='cancel',expected_version=created.json()['version'],reason='TEST independent proof',locator=str(uuid4())),headers={'Idempotency-Key':key})
    assert cancel.status_code==200
    worker=client_for(a)
    assert worker.post('/pilot/login',json={'username':pilot_auth.username,'password':PASSWORD}).status_code==200
    # FastAPI adapter uses existing domain handlers in the real app. Here direct
    # service denial is asserted so no exception-handler substitution masks it.
    _,participant=a.login(pilot_auth.username,PASSWORD,'independent-worker')
    from greenhouse_owner_os.domain.errors import NotFoundError
    with pytest.raises(NotFoundError):
        receipt_status(a,participant,'/v1/worker/tasks/'+tid+'/actions',key)


def test_participant_cannot_be_rebound_or_reenabled(pilot_auth):
    a=pilot_auth.auth
    for statement in (
        'UPDATE core.pilot_participants SET actor_id=:other WHERE id=:pid',
        'UPDATE core.pilot_participants SET entity_id=:other WHERE id=:pid',
    ):
        with pytest.raises(DBAPIError):
            with a.factory() as u:
                u.session.execute(text(statement),dict(other=uuid4(),pid=pilot_auth.pid));u.commit()
    _,owner=a.login('daily-owner',PASSWORD,'independent-owner')
    a.revoke(owner,UUID(pilot_auth.pid),1)
    with pytest.raises(DBAPIError):
        with a.factory() as u:
            u.session.execute(text('UPDATE core.pilot_participants SET enabled=true,version=version+1 WHERE id=:pid'),dict(pid=pilot_auth.pid));u.commit()
