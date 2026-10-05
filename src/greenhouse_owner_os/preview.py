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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.services.references import ReferenceService, provision_authenticated_owner


def build_preview(database_url, secret, *, environment, public_origin=None):
    require_disposable_database(database_url, environment)
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
    refs=ReferenceService(factory,environment='OWNER_VERIFIED')
    actor=provision_authenticated_owner(sf,external_ref='test:owner-mobile-preview',
        display_name='TEST PREVIEW Owner',environment='OWNER_VERIFIED')
    # Reuse the reviewed, idempotent explicit owner-input API workflow. No money seed.
    from scripts.validate_owner_opening import setup_confirmed_profile
    from fastapi.testclient import TestClient
    setup_app=create_app(finance_service=finance,reference_service=refs,actor_provider=lambda:actor)
    manifest=json.loads((Path(__file__).resolve().parents[2]/'config/owner_finance_opening.json').read_text())
    profile=setup_confirmed_profile(TestClient(setup_app),manifest)
    eid=UUID(profile['entity']['id'])
    from greenhouse_owner_os.services.dimensions import provision_owner_dimensions
    provision_owner_dimensions(factory,eid,actor,profile['opening']['id'])
    cookie_value=secrets.token_urlsafe(48)
    def owner(request:Request):
        if not hmac.compare_digest(request.cookies.get('preview_session',''),cookie_value):
            raise HTTPException(401,'preview login required')
        return actor
    app=create_app(finance_service=finance,reference_service=refs,actor_provider=owner)
    attempts={}

    @app.middleware('http')
    async def preview_boundary(request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            # Browsers must mutate same-origin; non-browser API tools still authenticate.
            origin=request.headers.get('origin')
            expected_origin=public_origin or str(request.base_url).rstrip('/')
            if origin and origin.rstrip('/') != expected_origin:
                return JSONResponse({'error':{'code':'origin','message':'same-origin required'}},status_code=403)
            if request.headers.get('sec-fetch-site')=='cross-site':
                return JSONResponse({'error':{'code':'origin','message':'same-origin required'}},status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

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
            secure=bool(public_origin) or request.url.scheme=='https',max_age=8*3600)
        return r

    @app.post('/preview/logout')
    def logout(actor_id=Depends(owner)):
        r=JSONResponse({'ok':True});r.delete_cookie('preview_session');return r

    @app.get('/preview/config')
    def config(actor_id=Depends(owner)):
        return {'entity_id':str(eid),'environment':'TEST_PREVIEW','actual_operations':False}

    @app.get('/health')
    def health():
        with sf() as s:s.execute(text('SELECT 1'))
        return {'ok':True,'environment':'TEST_PREVIEW'}

    web=Path(__file__).parent/'web'
    app.mount('/assets',StaticFiles(directory=web),name='assets')
    @app.get('/')
    def index():return FileResponse(web/'index.html')
    return app


def main():
    import uvicorn
    app=build_preview(os.environ['TEST_DATABASE_URL'],os.environ['PREVIEW_SECRET'],environment=os.getenv('APP_ENV'),
        public_origin=os.getenv('PREVIEW_PUBLIC_ORIGIN'))
    uvicorn.run(app,host=os.getenv('PREVIEW_BIND','127.0.0.1'),port=int(os.getenv('PREVIEW_PORT','4173')),
                proxy_headers=False)


if __name__=='__main__': main()
