"""Explicit disposable PostgreSQL preview adapter. Never production.

Run migrations separately, then APP_ENV=test TEST_DATABASE_URL=<exact local
allowlisted target> PREVIEW_SECRET=<random key> python -m ...preview.
No automatic actual payment or production provisioning. Cookie authentication
belongs only to this isolated adapter; production identity is a separate task.
"""
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlsplit
from pathlib import Path
from uuid import UUID
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.services.references import ReferenceService, provision_authenticated_owner



def _preview_headers(response):
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


def _connected_duration(value):
    try:
        duration = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('R3 preview duration must be 60–3600 seconds') from exc
    if not 60 <= duration <= 3600:
        raise ValueError('R3 preview duration must be 60–3600 seconds')
    return duration


def _require_connected_runtime(session):
    """Fail closed on role elevation or an unmarked/shared preview database."""
    from greenhouse_owner_os.synthetic_pilot import DATABASE_MARKER
    role = session.execute(text("""SELECT current_user AS current_role, session_user AS session_role,
        current_database() AS database_name, host(inet_server_addr()) AS server_address,
        inet_server_port() AS server_port, current_setting('listen_addresses') AS listen_addresses, r.rolsuper, r.rolcreatedb, r.rolcreaterole,
        r.rolreplication, r.rolbypassrls,
        (SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid) AS memberships,
        shobj_description(d.oid, 'pg_database') AS database_marker,
        has_database_privilege(current_user, d.oid, 'CREATE') AS database_create,
        (SELECT count(*) FROM pg_namespace n WHERE n.nspname IN
            ('public','core','finance','inventory','harvest','workers','audit')
            AND (n.nspowner=r.oid OR has_schema_privilege(current_user,n.oid,'CREATE'))) AS writable_schemas,
        (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relowner=r.oid AND n.nspname NOT LIKE 'pg_%' AND n.nspname!='information_schema') AS owned_objects
        FROM pg_roles r JOIN pg_database d ON d.datname=current_database()
        WHERE r.rolname=current_user""")).mappings().one()
    if (role['current_role'] != 'postgres' or role['session_role'] != role['current_role']
        or role['database_name'] != 'greenhouse_owner_os_test'
        or role['server_address'] != '127.0.0.1' or role['server_port'] != 5432
        or role['listen_addresses'] not in ('127.0.0.1','localhost')
        or role['database_marker'] != DATABASE_MARKER or role['memberships'] != 0
        or role['database_create'] or role['writable_schemas'] or role['owned_objects']
        or any(role[k] for k in ('rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls'))):
        raise ValueError('R3 pilot requires its marked synthetic database and restricted non-superuser runtime role')



async def _require_pilot_scope(request, entity_id, factory):
    """Preview-only fence, including unregistered legacy foundation entities."""
    if not request.url.path.startswith('/v1/'):
        return
    parts = request.url.path.split('/')
    target = None
    if request.url.path == '/v1/entities' and request.method == 'POST':
        raise HTTPException(403, 'R3 pilot is restricted to its synthetic entity')
    if len(parts) > 3 and parts[2] == 'entities':
        target = parts[3]
    elif request.url.path == '/v1/finance/reconciliation':
        target = request.query_params.get('entity_id')
    elif len(parts) > 3:
        tables = {'obligations':'finance.obligations', 'payments':'finance.payments',
            'supplier-advances':'finance.payments', 'advances':'finance.advances',
            'transfers':'finance.own_account_transfers', 'owner-fundings':'finance.owner_fundings',
            'direct-settlements':'finance.direct_settlements', 'advance-applications':'finance.advance_applications',
            'import-batches':'core.import_batches', 'historical-candidates':'core.historical_candidates'}
        if parts[2] in tables:
            try:
                resource_id = UUID(parts[3])
            except ValueError:
                raise HTTPException(422, 'Invalid resource identity')
            with factory() as u:
                target = u.session.scalar(text('SELECT entity_id FROM ' + tables[parts[2]] + ' WHERE id=:id'), dict(id=resource_id))
            if target is None:
                raise HTTPException(404, 'Resource not found')
    if target is None and request.method in ('POST','PUT','PATCH'):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if isinstance(body, dict):
            target = body.get('entity_id')
    if target is not None:
        try:
            scoped_id = UUID(str(target))
        except ValueError:
            raise HTTPException(422, 'Invalid entity identity')
        if scoped_id != entity_id:
            raise HTTPException(403, 'R3 pilot is restricted to its synthetic entity')

_LOGIN_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Greenhouse · Connected TEST</title>
<link rel="stylesheet" href="/r3/login.css"></head><body><main><h1>Greenhouse · TEST</h1>
<p>Закрытый connected TEST. Только синтетические данные; доступ временный.</p>
<form id="login"><label for="secret">Временный ключ доступа</label>
<input id="secret" type="password" autocomplete="off" required minlength="24">
<button type="submit">Войти</button><p id="error" role="alert"></p></form>
</main><script src="/r3/login.js" defer></script></body></html>"""
_LOGIN_JS = """document.getElementById('login').addEventListener('submit',async event=>{
 event.preventDefault();const input=document.getElementById('secret'),button=event.target.querySelector('button'),error=document.getElementById('error');
 button.disabled=true;error.textContent='';try{const response=await fetch('/preview/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({secret:input.value})});
 input.value='';if(!response.ok){error.textContent=response.status===410?'Срок TEST preview истёк.':response.status===429?'Слишком много попыток. Подождите минуту.':'Ключ не подошёл. Проверьте его и повторите.';return;}
 location.replace('/r3/connected.html');}catch{error.textContent='Не удалось подключиться. Попробуйте ещё раз.';}finally{button.disabled=false;}});"""
_LOGIN_CSS = """body{margin:0;background:#f6f7f3;color:#173a2b;font:16px system-ui,sans-serif}main{max-width:28rem;margin:12vh auto;padding:1.5rem}h1{font-size:1.6rem}form{display:grid;gap:1rem}input,button{box-sizing:border-box;width:100%;padding:.9rem;border:1px solid #738b7c;border-radius:.7rem;font:inherit}button{background:#173a2b;color:white}#error{color:#943721}button:disabled{opacity:.6}"""


def _mount_connected_ui(app, owner, cookie_value, pilot_auth=None):
    directory = (Path(__file__).resolve().parents[2] / 'prototype' / 'owner-r3').resolve()

    @app.get('/r3')
    def connected_redirect():
        return RedirectResponse('/r3/', status_code=307)

    @app.get('/r3/')
    def connected_login(request: Request):
        if pilot_auth:
            try:
                identity=pilot_auth.authenticate(request)
                return RedirectResponse('/r3/connected.html' if identity['role']=='owner' else '/worker',status_code=303)
            except HTTPException:
                return RedirectResponse('/pilot/',status_code=303)
        if hmac.compare_digest(request.cookies.get('preview_session',''), cookie_value):
            return RedirectResponse('/r3/connected.html', status_code=303)
        return HTMLResponse(_LOGIN_HTML)

    @app.get('/r3/login.js')
    def connected_login_js():
        return Response(_LOGIN_JS, media_type='text/javascript')

    @app.get('/r3/login.css')
    def connected_login_css():
        return Response(_LOGIN_CSS, media_type='text/css')

    @app.get('/r3/{asset:path}')
    def connected_asset(asset: str, actor_id=Depends(owner)):
        target = (directory / asset).resolve()
        if not target.is_relative_to(directory) or not target.is_file() or target.suffix not in ('.html','.js','.css','.svg','.png','.jpg','.jpeg','.webp'):
            raise HTTPException(404, 'R3 asset not found')
        return FileResponse(target)

def build_preview(database_url, secret, *, environment, public_origin=None):
    require_disposable_database(database_url, environment)
    connected = os.getenv('R3_CONNECTED_PILOT') == '1'
    daily = os.getenv('R3_DAILY_PILOT') == '1'
    if daily and not connected:
        raise ValueError('Daily pilot requires the synthetic connected TEST adapter')
    if daily and not public_origin and os.getenv('PREVIEW_BIND','127.0.0.1') not in ('127.0.0.1','localhost'):
        raise ValueError('Local daily TEST must bind only to loopback')
    if daily and not public_origin and os.getenv('PILOT_LOCAL_TEST')!='1':
        raise ValueError('Daily pilot requires an explicit HTTPS origin or isolated local TEST mode')
    if connected:
        from sqlalchemy.engine import make_url
        if make_url(database_url).query:
            raise ValueError('R3 pilot connection overrides are not allowed')
    if len(secret)<24: raise ValueError('preview requires a random secret of at least 24 characters')
    if public_origin:
        parsed=urlsplit(public_origin)
        if parsed.scheme!='https' or not parsed.hostname or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('preview public origin must be an explicit HTTPS origin')
        public_origin=public_origin.rstrip('/')
    sf = make_session_factory(database_url)
    # Verify the connected server's identity as well as the supplied URL.
    with sf() as s:
        if s.scalar(text('SELECT current_database()')) != 'greenhouse_owner_os_test':
            raise ValueError('preview database identity mismatch')
    factory=lambda:SqlAlchemyUnitOfWork(sf)
    finance=FinanceService(factory)
    if connected:
        with sf() as session:
            _require_connected_runtime(session)
        duration = _connected_duration(os.getenv('R3_PREVIEW_DURATION_SECONDS', '2100'))
        deadline = time.monotonic() + duration
        expires_at = time.time() + duration
        from greenhouse_owner_os.synthetic_pilot import OWNER_REF, OWNER_NAME, setup_synthetic_profile
        refs = ReferenceService(factory, environment='TEST_PREVIEW')
        actor = provision_authenticated_owner(sf, external_ref=OWNER_REF,
            display_name=OWNER_NAME, environment='TEST_PREVIEW')
        from fastapi.testclient import TestClient
        setup_app = create_app(finance_service=finance, reference_service=refs, actor_provider=lambda:actor)
        with TestClient(setup_app) as setup_client:
            profile = setup_synthetic_profile(setup_client, factory, actor)
        eid = UUID(profile['entity']['id'])
    else:
        refs=ReferenceService(factory,environment='OWNER_VERIFIED')
        from scripts.validate_owner_opening import TEST_PROFILE_OWNER_REF,TEST_PROFILE_OWNER_NAME
        actor=provision_authenticated_owner(sf,external_ref=TEST_PROFILE_OWNER_REF,
            display_name=TEST_PROFILE_OWNER_NAME,environment='OWNER_VERIFIED')
        # Reuse the reviewed, idempotent explicit owner-input API workflow. No money seed.
        from scripts.validate_owner_opening import setup_confirmed_profile
        from fastapi.testclient import TestClient
        setup_app=create_app(finance_service=finance,reference_service=refs,actor_provider=lambda:actor)
        manifest=json.loads((Path(__file__).resolve().parents[2]/'config/owner_finance_opening.json').read_text())
        profile=setup_confirmed_profile(TestClient(setup_app),manifest)
        eid=UUID(profile['entity']['id'])
        from greenhouse_owner_os.services.dimensions import provision_owner_dimensions
        provision_owner_dimensions(factory,eid,actor,profile['opening']['id'])
    review_fixture = profile['worker_assignment_fixture'] if connected else {}
    if not connected and os.getenv('R2_REVIEW_FIXTURES')=='1':
        # Explicit disposable TEST references only; no stock, cash, or debt seeded.
        fixture_client=TestClient(setup_app)
        def fixture(path,payload,key):
            response=fixture_client.post('/v1/entities/'+str(eid)+path,json=payload,headers={'Idempotency-Key':'test:r2-preview:'+key})
            if response.status_code not in (200,201):raise RuntimeError('R2 preview reference setup failed')
            return response.json()
        fixture('/inventory/products',dict(name='TEST KNO3',aliases=['KNO3'],base_unit='kg',currency='TRY',source='test:r2-review-fixture'),'product')
        fixture('/inventory/locations',dict(name='İmamlı',source='test:r2-review-fixture'),'location')
        fixture('/counterparties',dict(name='Görgün',role='depot',source='test:r2-review-fixture'),'depot')
        # One authored example proves the reviewed-rendering path, not a general
        # translation service. It remains unassigned and has no external contact.
        worker=fixture('/workers',dict(display_name='TEST Muhammed',native_name='TEST محمد',language='fa',
            notes='Синтетический работник только для закрытого TEST preview.',
            source='test:r2-review-fixture'),'worker-muhammed')
        from uuid import uuid5,NAMESPACE_URL
        areas=fixture_client.get('/v1/entities/'+str(eid)+'/tasks').json()['areas']
        greenhouse=next(a for a in areas if a['kind']=='greenhouse' and a['name']=='İmamlı')
        original='TEST Muhammed, проверьте трубу в İmamlı 2026-10-10.'
        rendered='TEST Muhammed، لوله را در گلخانهٔ İmamlı در تاریخ ۲۰۲۶-۱۰-۱۰ بررسی کنید.'
        source=fixture('/owner/captures',dict(client_capture_id=str(uuid5(NAMESPACE_URL,str(eid)+':test-iphone-task-v1')),
            captured_at='2026-10-09T00:00:00+03:00',timezone='Europe/Istanbul',mode='text',text=original),'worker-source')
        source=fixture_client.get('/v1/entities/'+str(eid)+'/owner/captures/'+source['id']).json()
        if len(source.get('proposals',[]))!=1:raise RuntimeError('TEST worker proposal was not prepared')
        proposal_id=source['proposals'][0]['id']
        proposal_path='/owner/confirmations/'+proposal_id
        proposal=fixture_client.get('/v1/entities/'+str(eid)+proposal_path+'/preview').json()
        if proposal['action_type']!='WORKER_TASK':raise RuntimeError('TEST fixture must remain a worker task')
        if proposal['state']!='CONFIRMED_NONPOSTING':
            payload={**proposal['revision']['payload'],'title':'TEST Проверить трубу · пример назначения',
                'worker_id':worker['id'],'language':'fa','rendered_text':rendered,
                'greenhouse_id':greenhouse['id'],'planned_date':'2026-10-10'}
            proposal=fixture(proposal_path+'/revise',dict(expected_version=proposal['version'],payload=payload,
                reason='TEST: заранее подготовленный пример перевода только для этого поручения.'),'worker-review')
            if proposal['revision']['missing']:raise RuntimeError('TEST worker fixture is incomplete')
            fixture(proposal_path+'/confirm',dict(expected_version=proposal['version'],
                preview_digest=proposal['revision']['preview_digest'],confirmed=True,
                reason='TEST: создать только черновик для проверки назначения владельцем.'),'worker-draft')
        tasks=fixture_client.get('/v1/entities/'+str(eid)+'/tasks').json()['tasks']
        example=next(t for t in tasks if t['title']=='TEST Проверить трубу · пример назначения')
        review_fixture={'worker_id':worker['id'],'task_id':example['id'],'source_id':source['id'],
            'translation_scope':'ONE_AUTHORED_TEST_EXAMPLE','real_dispatch':False}

    pilot_auth=None
    if daily:
        from greenhouse_owner_os.daily_pilot import PilotAuth
        pilot_auth=PilotAuth(factory,eid,actor,expires_at=os.environ.get('PILOT_EXPIRES_AT'),
            build_sha=os.getenv('OWNER_PREVIEW_FEATURE_SHA',os.getenv('GITHUB_SHA')),secure=bool(public_origin))
    cookie_value=secrets.token_urlsafe(48)
    async def owner(request:Request):
        if pilot_auth:
            participant=pilot_auth.authenticate(request)
            if participant['role']!='owner':raise HTTPException(403,'Только владелец имеет доступ к этому разделу.')
            await _require_pilot_scope(request,eid,factory)
            return participant['actor_id']
        if connected and time.monotonic() >= deadline:
            raise HTTPException(410, 'TEST preview expired')
        if not hmac.compare_digest(request.cookies.get('preview_session',''),cookie_value):
            raise HTTPException(401,'preview login required')
        if connected:
            await _require_pilot_scope(request, eid, factory)
        return actor
    worker_sessions={}
    def worker_identity(request:Request):
        if pilot_auth:
            participant=pilot_auth.authenticate(request)
            if participant['role']!='worker':raise HTTPException(403,'Требуется вход работника.')
            return participant['entity_id'],participant['worker_id'],participant['actor_id']
        from greenhouse_owner_os.services.inventory import rows
        token=request.headers.get('authorization','').removeprefix('Bearer ')
        session=worker_sessions.get(token)
        if not session:raise HTTPException(401,'Worker session required')
        e,w,a,expiry=session
        if time.monotonic()>expiry:raise HTTPException(401,'Worker session expired')
        with factory() as u:
            if not rows(u,'SELECT id FROM workers.people WHERE id=:w AND entity_id=:e AND active',w=w,e=e):raise HTTPException(401,'Worker inactive')
        return e,w,a
    app=create_app(finance_service=finance,reference_service=refs,actor_provider=owner,worker_provider=worker_identity)
    @app.post('/preview/workers/{worker_id}/session')
    def worker_session(worker_id:UUID,actor_id=Depends(owner)):
        if pilot_auth:raise HTTPException(404,'Legacy TEST token access is disabled in daily pilot')
        from greenhouse_owner_os.services.inventory import rows
        with factory() as u:
            rs=rows(u,'SELECT * FROM workers.people WHERE id=:w AND entity_id=:e AND active',w=worker_id,e=eid)
            if not rs:raise HTTPException(404,'Worker not found')
            w=rs[0]
        token=secrets.token_urlsafe(48)
        lifetime = min(3600, max(1, int(deadline-time.monotonic()))) if connected else 3600
        worker_sessions[token]=(eid,worker_id,UUID(w['actor_id']),time.monotonic()+lifetime)
        return {'token':token,'worker_id':str(worker_id),'expires_in':lifetime,'environment':'TEST_PREVIEW'}

    attempts={}

    @app.middleware('http')
    async def preview_boundary(request, call_next):
        if pilot_auth:
            try:
                pilot_auth.live()
                path=request.url.path
                if path in ('/preview/login','/preview/logout') or path.startswith('/preview/workers/'):
                    raise HTTPException(404,'Legacy preview access is disabled in daily pilot')
                if request.method not in ('GET','HEAD','OPTIONS'):
                    expected=public_origin or str(request.base_url).rstrip('/')
                    if request.headers.get('origin','').rstrip('/')!=expected or request.headers.get('sec-fetch-site')=='cross-site':
                        raise HTTPException(403,'same-origin required')
                    if request.headers.get('content-type','').split(';')[0].strip()!='application/json':
                        raise HTTPException(415,'JSON required')
                public_paths={'/pilot/','/pilot/public-config','/pilot/login','/health','/assets/pilot-login.html','/assets/pilot-login.js','/assets/pilot-login.css'}
                if path not in public_paths:
                    participant=pilot_auth.authenticate(request)
                    pilot_auth.require_expected_participant(request,participant,required=request.method not in ('GET','HEAD','OPTIONS'))
                    if participant['role']=='worker' and not (path.startswith('/v1/worker/') or path.startswith('/pilot/') or path.startswith('/assets/') or path=='/worker'):
                        raise HTTPException(403,'Worker access is limited to assigned tasks')
                if path=='/':return _preview_headers(RedirectResponse('/pilot/',status_code=303))
            except HTTPException as exc:
                if exc.status_code==401 and request.method=='GET' and request.url.path in ('/r3/','/r3','/r3/connected.html','/worker','/'):
                    return _preview_headers(RedirectResponse('/pilot/',status_code=303))
                return _preview_headers(JSONResponse({'detail':exc.detail},status_code=exc.status_code))
        if connected and not pilot_auth and time.monotonic() >= deadline:
            response = JSONResponse({'error': {'code': 'expired', 'message': 'TEST preview expired'}}, status_code=410)
            return _preview_headers(response)
        if request.method not in ('GET','HEAD','OPTIONS'):
            # Browsers must mutate same-origin; non-browser API tools still authenticate.
            origin=request.headers.get('origin')
            expected_origin=public_origin or str(request.base_url).rstrip('/')
            if origin and origin.rstrip('/') != expected_origin:
                return _preview_headers(JSONResponse({'error':{'code':'origin','message':'same-origin required'}},status_code=403))
            if request.headers.get('sec-fetch-site')=='cross-site':
                return _preview_headers(JSONResponse({'error':{'code':'origin','message':'same-origin required'}},status_code=403))
        response=await call_next(request)
        return _preview_headers(response)

    class Login(BaseModel): secret:str
    @app.post('/preview/login')
    def login(body:Login,request:Request):
        ip=request.client.host if request.client else 'unknown'
        now=time.monotonic();times=[t for t in attempts.get(ip,[]) if now-t<60]
        if len(times)>=5:raise HTTPException(429,'try again later')
        if not hmac.compare_digest(body.secret,secret):
            attempts[ip]=times+[now];raise HTTPException(401,'invalid preview secret')
        attempts.pop(ip,None)
        r=JSONResponse({'ok':True})
        r.set_cookie('preview_session',cookie_value,httponly=True,samesite='strict',
            secure=bool(public_origin) or request.url.scheme=='https',
            max_age=max(1, int(deadline-time.monotonic())) if connected else 8*3600)
        return r

    @app.post('/preview/logout')
    def logout(actor_id=Depends(owner)):
        r=JSONResponse({'ok':True});r.delete_cookie('preview_session');return r

    @app.get('/preview/config')
    def config(request:Request,actor_id=Depends(owner)):
        result = {'entity_id':str(eid),'environment':'TEST_PREVIEW','actual_operations':False,'r2_fixtures':not connected and os.getenv('R2_REVIEW_FIXTURES')=='1','worker_assignment_fixture':review_fixture,'feature_sha':os.getenv('OWNER_PREVIEW_FEATURE_SHA',os.getenv('GITHUB_SHA'))}
        if connected:
            from datetime import datetime, timezone
            result.update(mode='CONNECTED_TEST', backend_connected=True, synthetic_only=True,
                owner_id=str(actor), expires_at=datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
                capabilities={'capture':True, 'review_confirm':True, 'finance':True, 'harvest':True,
                    'inventory':True, 'personal_tasks':True, 'worker_tasks':'R1_AND_R2_INTERNAL_TEST',
                    'fertilizer_planning':'CONNECTED_TEST_BOUNDED_ACTUAL_SCOPE', 'real_dispatch':False, 'external_integrations':False},
                synthetic_fixture=profile)
        if pilot_auth:
            participant=pilot_auth.authenticate(request)
            result.update(daily_pilot=True,participant_id=str(participant['id']),deployment_id=str(pilot_auth.deployment_id),session=pilot_auth.view(participant),expires_at=pilot_auth.expires_at.isoformat())
        return result

    @app.get('/preview/fixtures/gorgun.png')
    def representative_receipt(actor_id=Depends(owner)):
        if connected or os.getenv('R2_REVIEW_FIXTURES')!='1':raise HTTPException(404,'fixture unavailable')
        from tests.ocr_corpus import document
        original,mime=document(0)
        return Response(original,media_type=mime,headers={'Content-Disposition':'attachment; filename="TEST-representative-Gorgun.png"'})

    @app.get('/health')
    def health():
        with sf() as s:s.execute(text('SELECT 1'))
        return {'ok':True,'environment':'TEST_PREVIEW'}

    web=Path(__file__).parent/'web'
    app.mount('/assets',StaticFiles(directory=web),name='assets')
    @app.get('/worker')
    def worker_page():return FileResponse(web/'worker.html')

    @app.get('/')
    def index():return FileResponse(web/'index.html')
    if connected:
        _mount_connected_ui(app, owner, cookie_value, pilot_auth)
    if pilot_auth:
        pilot_auth.mount(app)
    return app


def main():
    import uvicorn
    app=build_preview(os.environ['TEST_DATABASE_URL'],os.environ['PREVIEW_SECRET'],environment=os.getenv('APP_ENV'),
        public_origin=os.getenv('PREVIEW_PUBLIC_ORIGIN'))
    uvicorn.run(app,host=os.getenv('PREVIEW_BIND','127.0.0.1'),port=int(os.getenv('PREVIEW_PORT','4173')),
                proxy_headers=False)


if __name__=='__main__': main()
