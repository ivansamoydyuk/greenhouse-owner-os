"""Bounded synthetic TEST login adapter, not a production identity provider.

Domain authority stays in existing owner/worker providers. No public registration,
credential delivery, or external channel. Offline provisioning accepts only the
existing synthetic pilot entity and its trusted actor/worker identities.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

COOKIE = 'pilot_session'
LOGIN_ASSETS = {'pilot-login.html', 'pilot-login.js', 'pilot-login.css'}
USERNAME = re.compile(r'^[a-z0-9][a-z0-9._-]{2,63}$')

def now():
    return datetime.now(timezone.utc)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 200:
        raise ValueError('TEST password requires 12–200 characters')
    salt = secrets.token_bytes(16)
    hashed = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return 'scrypt$' + salt.hex() + '$' + hashed.hex()

def password_matches(password, encoded):
    try:
        algorithm, salt, expected = encoded.split('$')
        if algorithm != 'scrypt' or len(password)>200 or len(salt)!=32 or len(expected)!=128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False

# Same computational path for absent/disabled usernames; contains no usable account.
DUMMY_HASH = 'scrypt$' + '00'*16 + '$' + hashlib.scrypt(b'not-a-login-account', salt=b'\x00'*16, n=16384, r=8, p=1).hex()

def parse_expiry(value):
    try:
        result = datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError, AttributeError) as exc:
        raise ValueError('PILOT_EXPIRES_AT must be an explicit timezone-aware ISO timestamp') from exc
    if result.tzinfo is None:
        raise ValueError('PILOT_EXPIRES_AT requires a timezone')
    return result.astimezone(timezone.utc)

class Login(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    username: str = Field(min_length=1,max_length=64)
    password: str = Field(min_length=1,max_length=200)

class Revoke(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)

class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1,max_length=500)
    key: str = Field(min_length=1,max_length=200)

class PilotAuth:
    def __init__(self, factory, entity_id, owner_id, *, expires_at, build_sha, secure=True):
        if os.environ.get('APP_ENV')!='test':raise ValueError('Private daily pilot is TEST-only')
        self.factory=factory; self.entity_id=UUID(str(entity_id)); self.owner_id=UUID(str(owner_id))
        self.build_sha=build_sha; self.secure=secure
        if not re.fullmatch(r'[0-9a-f]{40}',build_sha or ''):
            raise ValueError('Private daily TEST requires an exact build SHA')
        from greenhouse_owner_os.synthetic_pilot import OWNER_REF
        with factory() as u:
            if not u.session.scalar(text("SELECT EXISTS(SELECT 1 FROM core.entities e JOIN core.actors a ON a.id=:a WHERE e.id=:e AND e.reference_class='TEST_PREVIEW' AND a.external_ref=:ref)"),dict(a=self.owner_id,e=self.entity_id,ref=OWNER_REF)):
                raise ValueError('Daily pilot requires the existing synthetic TEST identity')
        self.deployment_id=uuid5(NAMESPACE_URL,'greenhouse-private-daily-test:'+str(entity_id))
        requested=parse_expiry(expires_at)
        with factory() as u:
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:id,71))'),dict(id=str(self.deployment_id)))
            old=u.session.execute(text('SELECT * FROM core.pilot_deployments WHERE id=:id'),dict(id=self.deployment_id)).mappings().first()
            if old:
                if old['entity_id']!=self.entity_id or old['expires_at']!=requested:
                    raise ValueError('Persisted pilot expiry cannot be changed or extended on restart')
                self.expires_at=old['expires_at']
            else:
                start=now()
                if not start+timedelta(minutes=1)<requested<=start+timedelta(days=7):
                    raise ValueError('New TEST pilot expiry must be 1 minute–7 days from initialization')
                u.session.execute(text('INSERT INTO core.pilot_deployments(id,entity_id,created_at,expires_at) VALUES(:id,:e,:start,:end)'),dict(id=self.deployment_id,e=self.entity_id,start=start,end=requested))
                self.expires_at=requested
            u.commit()

    def live(self):
        if now()>=self.expires_at:
            raise HTTPException(410,'Срок закрытого TEST-пилота закончился.')

    def provision(self, username, password, *, worker_id=None):
        """Offline-only synthetic fixture provisioning. Never mounted as an API."""
        self.live()
        if not USERNAME.fullmatch(username):raise ValueError('Invalid TEST username')
        encoded=password_hash(password)
        with self.factory() as u:
            if worker_id:
                worker_id=UUID(str(worker_id))
                worker=u.session.execute(text('SELECT actor_id FROM workers.people WHERE id=:w AND entity_id=:e AND active'),dict(w=worker_id,e=self.entity_id)).first()
                if not worker:raise ValueError('Worker is not in the existing active synthetic fixture')
                actor=worker[0];role='worker'
            else:actor=self.owner_id;role='owner'
            old=u.session.execute(text('SELECT * FROM core.pilot_participants WHERE deployment_id=:d AND (username=:name OR actor_id=:a)'),dict(d=self.deployment_id,name=username,a=actor)).mappings().all()
            if old:
                if len(old)!=1 or old[0]['username']!=username or old[0]['actor_id']!=actor or old[0]['worker_id']!=worker_id or not password_matches(password,old[0]['password_hash']):
                    raise ValueError('Existing participant identity or credential cannot be replaced by initialization')
                return str(old[0]['id'])
            pid=uuid4()
            u.session.execute(text('INSERT INTO core.pilot_participants(id,deployment_id,entity_id,actor_id,worker_id,role,username,password_hash,draft_key) VALUES(:id,:d,:e,:a,:w,:role,:name,:hash,:draft_key)'),dict(id=pid,d=self.deployment_id,e=self.entity_id,a=actor,w=worker_id,role=role,name=username,hash=encoded,draft_key=secrets.token_bytes(32)))
            u.session.execute(text("INSERT INTO core.pilot_access_events(deployment_id,participant_id,kind) VALUES(:d,:p,'PROVISION')"),dict(d=self.deployment_id,p=pid));u.commit()
            return str(pid)

    def _active(self,u,p):
        if not p['enabled']:return False
        if p['role']=='owner':
            return bool(u.session.scalar(text('SELECT core.r2_owner(:e,:a)'),dict(e=p['entity_id'],a=p['actor_id'])))
        return bool(u.session.scalar(text('SELECT EXISTS(SELECT 1 FROM workers.people WHERE id=:w AND entity_id=:e AND actor_id=:a AND active)'),dict(w=p['worker_id'],e=p['entity_id'],a=p['actor_id'])))

    def authenticate(self,request):
        self.live()
        token=request.cookies.get(COOKIE,'')
        if not 40<=len(token)<=100:raise HTTPException(401,'Войдите в закрытый TEST-пилот.')
        with self.factory() as u:
            p=u.session.execute(text('''SELECT p.*,s.expires_at AS session_expires_at FROM core.pilot_sessions s
                JOIN core.pilot_participants p ON p.id=s.participant_id WHERE s.token_hash=:h
                AND p.deployment_id=:d AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()'''),dict(h=digest(token),d=self.deployment_id)).mappings().first()
            if not p or not self._active(u,p):raise HTTPException(401,'Сессия закончилась или доступ отозван.')
            return dict(p)

    def require_expected_participant(self,request,p,*,required=False):
        """Bind this request to the principal captured with its form/command.

        A preflight alone cannot fence a cookie changed in another tab while a
        draft is being durably saved. Identity is compared on the actual request.
        """
        expected=request.headers.get('x-pilot-participant')
        if expected is None and not required:return
        if expected!=str(p['id']):
            raise HTTPException(409,'Сессия участника изменилась. Откройте запись после входа; действие не выполнено.')

    def view(self,p):
        with self.factory() as u:
            if p['worker_id']:
                w=u.session.execute(text('SELECT display_name,language FROM workers.people WHERE id=:w'),dict(w=p['worker_id'])).mappings().one()
                name=w['display_name'];language=w['language']
            else:name=u.session.scalar(text('SELECT display_name FROM core.actors WHERE id=:a'),dict(a=p['actor_id']));language='ru'
        return dict(mode='PRIVATE_DAILY_TEST',participant_id=str(p['id']),deployment_id=str(self.deployment_id),entity_id=str(p['entity_id']),actor_id=str(p['actor_id']),role=p['role'],worker_id=str(p['worker_id']) if p['worker_id'] else None,display_name=name,language=language,expires_at=p.get('session_expires_at',self.expires_at).isoformat(),pilot_expires_at=self.expires_at.isoformat(),build_sha=self.build_sha,synthetic_only=True)

    def login(self,username,password,peer):
        self.live();username=username.lower().strip()
        with self.factory() as u:
            # Durable limits survive app restarts. Never store addresses/passwords.
            for label in ('peer:'+peer,'name:'+username):
                bucket=digest(str(self.deployment_id)+':'+label)
                count=u.session.scalar(text('''INSERT INTO core.pilot_login_attempts(bucket,window_start,attempts)
                  VALUES(:b,clock_timestamp(),1) ON CONFLICT(bucket) DO UPDATE SET
                  attempts=CASE WHEN core.pilot_login_attempts.window_start<clock_timestamp()-interval '1 minute' THEN 1 ELSE core.pilot_login_attempts.attempts+1 END,
                  window_start=CASE WHEN core.pilot_login_attempts.window_start<clock_timestamp()-interval '1 minute' THEN clock_timestamp() ELSE core.pilot_login_attempts.window_start END RETURNING attempts'''),dict(b=bucket))
                if count>10:u.commit();raise HTTPException(429,'Слишком много попыток. Подождите минуту.')
            u.commit()
        with self.factory() as u:
            p=u.session.execute(text('SELECT * FROM core.pilot_participants WHERE deployment_id=:d AND username=:name'),dict(d=self.deployment_id,name=username)).mappings().first()
            matched=password_matches(password,p['password_hash'] if p else DUMMY_HASH)
            if not matched or not p or not self._active(u,p):raise HTTPException(401,'Не удалось войти. Проверьте имя и пароль.')
            token=secrets.token_urlsafe(48);start=now();end=min(start+timedelta(hours=12),self.expires_at)
            u.session.execute(text('INSERT INTO core.pilot_sessions(token_hash,participant_id,created_at,expires_at) VALUES(:h,:p,:start,:end)'),dict(h=digest(token),p=p['id'],start=start,end=end))
            u.session.execute(text("INSERT INTO core.pilot_access_events(deployment_id,participant_id,kind) VALUES(:d,:p,'LOGIN')"),dict(d=self.deployment_id,p=p['id']));u.commit()
            return token,dict(p,session_expires_at=end)

    def logout(self,request,p):
        with self.factory() as u:
            u.session.execute(text('UPDATE core.pilot_sessions SET revoked_at=clock_timestamp() WHERE token_hash=:h AND participant_id=:p AND revoked_at IS NULL'),dict(h=digest(request.cookies.get(COOKIE,'')),p=p['id']))
            u.session.execute(text("INSERT INTO core.pilot_access_events(deployment_id,participant_id,kind) VALUES(:d,:p,'LOGOUT')"),dict(d=self.deployment_id,p=p['id']));u.commit()

    def participants(self,p):
        if p['role']!='owner':raise HTTPException(403,'Только владелец управляет TEST-доступом.')
        with self.factory() as u:
            result=u.session.execute(text('''SELECT p.id,p.role,p.worker_id,p.enabled,p.version,a.display_name
               FROM core.pilot_participants p JOIN core.actors a ON a.id=p.actor_id WHERE p.deployment_id=:d ORDER BY p.role,p.username'''),dict(d=self.deployment_id)).mappings().all()
            return {'participants':[dict(x,id=str(x['id']),worker_id=str(x['worker_id']) if x['worker_id'] else None) for x in result]}

    def revoke(self,p,pid,version):
        if p['role']!='owner':raise HTTPException(403,'Только владелец управляет TEST-доступом.')
        if pid==p['id']:raise HTTPException(422,'Для выхода используйте «Выйти».')
        with self.factory() as u:
            target=u.session.execute(text('SELECT * FROM core.pilot_participants WHERE id=:p AND deployment_id=:d FOR UPDATE'),dict(p=pid,d=self.deployment_id)).mappings().first()
            if not target:raise HTTPException(404,'Участник не найден.')
            if not target['enabled']:return {'id':str(pid),'enabled':False,'version':target['version']}
            if target['version']!=version:raise HTTPException(409,'Доступ изменился. Обновите список.')
            u.session.execute(text('UPDATE core.pilot_participants SET enabled=false,version=version+1 WHERE id=:p'),dict(p=pid))
            u.session.execute(text('UPDATE core.pilot_sessions SET revoked_at=clock_timestamp() WHERE participant_id=:p AND revoked_at IS NULL'),dict(p=pid))
            u.session.execute(text("INSERT INTO core.pilot_access_events(deployment_id,participant_id,actor_participant_id,kind) VALUES(:d,:p,:a,'REVOKE')"),dict(d=self.deployment_id,p=pid,a=p['id']));u.commit()
            return {'id':str(pid),'enabled':False,'version':version+1}

    def mount(self,app):
        auth=self
        def identity(request:Request):return auth.authenticate(request)
        @app.get('/pilot/public-config')
        def public_config():
            auth.live()
            return dict(mode='PRIVATE_DAILY_TEST',build_sha=auth.build_sha,expires_at=auth.expires_at.isoformat(),synthetic_only=True)
        @app.get('/pilot/')
        def page():return FileResponse(Path(__file__).parent/'web'/'pilot-login.html')
        @app.post('/pilot/login')
        def login(body:Login,request:Request):
            token,p=auth.login(body.username,body.password,request.client.host if request.client else 'unknown')
            r=JSONResponse(auth.view(p));r.set_cookie(COOKIE,token,httponly=True,secure=auth.secure,samesite='strict',path='/',max_age=max(1,int((p['session_expires_at']-now()).total_seconds())))
            return r
        @app.get('/pilot/session')
        def session(p=Depends(identity)):return auth.view(p)
        @app.get('/pilot/draft-key')
        def draft_key(p=Depends(identity)):
            return dict(participant_id=str(p['id']),algorithm='AES-GCM',key=base64.urlsafe_b64encode(p['draft_key']).decode().rstrip('='))
        @app.post('/pilot/logout')
        def logout(request:Request,p=Depends(identity)):
            auth.logout(request,p);r=JSONResponse({'ok':True});r.delete_cookie(COOKIE,path='/',secure=auth.secure,httponly=True,samesite='strict');return r
        @app.get('/pilot/participants')
        def participants(p=Depends(identity)):return auth.participants(p)
        @app.post('/pilot/participants/{participant_id}/revoke')
        def revoke(participant_id:UUID,body:Revoke,p=Depends(identity)):return auth.revoke(p,participant_id,body.expected_version)
        @app.post('/pilot/receipts')
        def receipts(body:Receipt,p=Depends(identity)):return receipt_status(auth,p,body.path,body.key)
        from fastapi.exceptions import RequestValidationError
        from fastapi.exception_handlers import request_validation_exception_handler
        @app.exception_handler(RequestValidationError)
        async def validation_error(request,exc):
            if request.url.path=='/pilot/login':
                return JSONResponse({'detail':'Проверьте имя и пароль.'},status_code=422)
            return await request_validation_exception_handler(request,exc)
        app.state.pilot_auth=auth

# Receipt lookup intentionally returns status only. Actual replay still goes
# through the original service's payload hash, locks and current authorization.
def receipt_status(auth,p,path,key):
    from urllib.parse import urlsplit
    parsed=urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or '%' in parsed.path or '..' in parsed.path or '//' in parsed.path:
        raise HTTPException(422,'Invalid command route')
    path=parsed.path
    operation=None
    if p['role']=='worker' and path.startswith('/v1/worker/day-workers/'):
        root='/v1/worker/day-workers'
        patterns=[(r'/requests','day_request'),(r'/actuals','day_actual'),(r'/requests/[0-9a-f-]{36}/clarifications','day_clarification')]
        operation=next((op for pattern,op in patterns if re.fullmatch(pattern,path[len(root):])),None)
        if not operation:raise HTTPException(403,'Worker receipt scope denied')
        if operation=='day_clarification':
            from greenhouse_owner_os.services.day_workers import DayWorkersService
            DayWorkersService(auth.factory).get_request(p['entity_id'],p['actor_id'],UUID(path.split('/')[-2]),p['worker_id'])
    elif p['role']=='worker':
        m=re.fullmatch(r'/v1/worker/(tasks|fertilizer)/([0-9a-f-]{36})/(actions|reports|responses)',path)
        if not m:raise HTTPException(403,'Worker receipt scope denied')
        kind,rid,action=m.groups()
        try:rid=UUID(rid)
        except ValueError:raise HTTPException(422,'Invalid resource identity')
        if kind=='tasks':
            from greenhouse_owner_os.services.workers import WorkersService
            WorkersService(auth.factory).get(p['entity_id'],rid,p['worker_id'])
            operation={'actions':'task_action','reports':'task_report','responses':'worker_r2_response'}[action]
        elif action=='responses':
            from greenhouse_owner_os.services.fertilizer import FertilizerService
            FertilizerService(auth.factory).get(p['entity_id'],p['actor_id'],rid,p['worker_id'])
            operation='fertilizer_report'
        else:raise HTTPException(403,'Worker receipt scope denied')
    else:
        root='/v1/entities/'+str(p['entity_id'])
        if path.startswith('/v1/entities/'):
            if not path.startswith(root+'/'):raise HTTPException(403,'Entity receipt scope denied')
            relative=path[len(root):]
            patterns=[
                (r'/day-workers/scopes','day_scope'),(r'/day-workers/requests','day_request'),
                (r'/day-workers/requests/[0-9a-f-]{36}/decisions','day_decision'),
                (r'/day-workers/actuals','day_actual'),
                (r'/day-workers/actuals/[0-9a-f-]{36}/confirmations','day_confirmation'),
                (r'/day-workers/actuals/[0-9a-f-]{36}/accruals','day_accrual'),
                (r'/owner/captures','owner_capture'),
                (r'/owner/confirmations/[0-9a-f-]{36}/confirm','owner_confirm'),
                (r'/owner/confirmations/[0-9a-f-]{36}/revise','owner_edit'),
                (r'/owner/confirmations/[0-9a-f-]{36}/defer','owner_defer'),
                (r'/owner/confirmations/[0-9a-f-]{36}/reject','owner_reject'),
                (r'/inventory/products','inventory_product'),(r'/inventory/locations','inventory_location'),
                (r'/inventory/events','inventory_draft'),(r'/inventory/events/[0-9a-f-]{36}/post','inventory_post'),
                (r'/inventory/events/[0-9a-f-]{36}/reverse','inventory_reverse'),
                (r'/harvest/reports','harvest_report'),(r'/harvest/batches','harvest_batch'),
                (r'/harvest/settlements','depot_draft'),(r'/harvest/settlements/[0-9a-f-]{36}/post','depot_post'),
                (r'/fertilizer/plans','fertilizer_plan'),(r'/fertilizer/plans/[0-9a-f-]{36}/revisions','fertilizer_plan'),
                (r'/fertilizer/plans/[0-9a-f-]{36}/dispatch','fertilizer_dispatch'),(r'/fertilizer/plans/[0-9a-f-]{36}/actual','fertilizer_actual'),
                (r'/fertilizer/plans/[0-9a-f-]{36}/resolutions','fertilizer_resolution'),
                (r'/workers','worker_create'),(r'/tasks','task_create'),
                (r'/tasks/[0-9a-f-]{36}/assignments','task_assign'),(r'/tasks/[0-9a-f-]{36}/actions','task_action')]
            operation=next((op for pattern,op in patterns if re.fullmatch(pattern,relative)),None)
        else:
            m=re.fullmatch(r'/v1/(payments|obligations)(?:/([0-9a-f-]{36})/post)?',path)
            if m:
                kind,rid=m.groups();operation=('post_' if rid else 'create_')+('payment' if kind=='payments' else 'obligation')
                if rid:
                    with auth.factory() as u:
                        entity=u.session.scalar(text('SELECT entity_id FROM finance.'+kind+' WHERE id=:id'),dict(id=UUID(rid)))
                    if entity!=p['entity_id']:raise HTTPException(403,'Resource receipt scope denied')
            else:return {'state':'UNSUPPORTED','retry_same_identity':True}
    if not operation:return {'state':'UNSUPPORTED','retry_same_identity':True}
    # One trusted actor can own more than one TEST entity. Prove the stored
    # command resource's entity, including collection/create routes with no ID.
    tables={
        'day_scope':'workers.greenhouse_scope','day_request':'workers.day_requests',
        'day_decision':'workers.day_decisions','day_clarification':'workers.day_clarifications',
        'day_actual':'workers.day_actuals','day_confirmation':'workers.day_confirmations','day_accrual':'workers.day_accruals',
        'owner_capture':'core.inbox_items','owner_confirm':'core.proposed_actions',
        'owner_edit':'core.proposed_actions','owner_defer':'core.proposed_actions','owner_reject':'core.proposed_actions',
        'inventory_product':'inventory.products','inventory_location':'inventory.locations',
        'inventory_draft':'inventory.events','inventory_post':'inventory.events','inventory_reverse':'inventory.events',
        'harvest_report':'harvest.worker_reports','harvest_batch':'harvest.batches',
        'depot_draft':'harvest.depot_settlements','depot_post':'harvest.depot_settlements',
        'fertilizer_plan':'inventory.fertilizer_plans','fertilizer_dispatch':'inventory.fertilizer_dispatches',
        'fertilizer_actual':'inventory.fertilizer_actuals','fertilizer_resolution':'inventory.fertilizer_resolutions',
        'fertilizer_report':'inventory.fertilizer_reports','worker_create':'workers.people',
        'task_create':'workers.tasks','task_assign':'workers.assignments','task_action':'workers.tasks',
        'task_report':'workers.reports','worker_r2_response':'workers.actions',
        'create_payment':'finance.payments','post_payment':'finance.payments',
        'create_obligation':'finance.obligations','post_obligation':'finance.obligations'}
    with auth.factory() as u:
        receipt=u.session.execute(text('SELECT response_json,resource_id FROM audit.idempotency_keys WHERE actor_id=:a AND operation=:op AND idempotency_key=:key'),dict(a=p['actor_id'],op=operation,key=key)).first()
        if not receipt:return {'state':'NOT_FOUND','retry_same_identity':True}
        table=tables.get(operation)
        if not table:return {'state':'UNKNOWN','retry_same_identity':True}
        entity=u.session.scalar(text('SELECT entity_id FROM '+table+' WHERE id=:id'),dict(id=receipt[1]))
        if entity!=p['entity_id']:return {'state':'UNKNOWN','retry_same_identity':True}
    if p['role']=='worker' and operation.startswith('day_'):
        from greenhouse_owner_os.services.day_workers import DayWorkersService
        svc=DayWorkersService(auth.factory)
        if operation=='day_actual':svc.get_actual(p['entity_id'],p['actor_id'],receipt[1],p['worker_id'])
        else:svc.get_request(p['entity_id'],p['actor_id'],UUID(receipt[0]['request_id']) if operation=='day_clarification' else receipt[1],p['worker_id'])
    # Status belongs to the exact target, not another command sharing an operation.
    targets=re.findall(r'/([0-9a-f-]{36})(?=/|$)',path)
    targets=[v for v in targets if v!=str(p['entity_id'])]
    if targets:
        response=receipt[0]
        field=('request_id' if operation in ('day_decision','day_clarification') else 'actual_id') if '/day-workers/' in path else 'plan_id' if '/fertilizer/' in path else 'task_id' if '/tasks/' in path else 'id'
        target=response.get(field) if isinstance(response,dict) else None
        if operation in ('fertilizer_plan','task_action'):target=response.get('id')
        if str(target)!=targets[-1]:return {'state':'UNKNOWN','retry_same_identity':True}
    return {'state':'COMMITTED','retry_same_identity':True}
