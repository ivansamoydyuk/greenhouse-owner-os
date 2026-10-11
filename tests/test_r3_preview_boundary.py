"""Pure boundary tests; PostgreSQL smoke is separately reproducible on restricted role."""
import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from greenhouse_owner_os.preview import _connected_duration, _mount_connected_ui, _require_connected_runtime, _require_pilot_scope
from greenhouse_owner_os.synthetic_pilot import DATABASE_MARKER


def runtime(**changes):
    values = dict(current_role='postgres', session_role='postgres', database_name='greenhouse_owner_os_test',
        rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolreplication=False, rolbypassrls=False,
        server_address='127.0.0.1', server_port=5432, listen_addresses='127.0.0.1',
        memberships=0, database_marker=DATABASE_MARKER, database_create=False, writable_schemas=0, owned_objects=0)
    return dict(values, **changes)


class Session:
    def __init__(self, role): self.role = role
    def execute(self, statement):
        sql = str(statement)
        assert 'pg_auth_members' in sql and 'session_user' in sql and 'shobj_description' in sql
        assert 'has_schema_privilege' in sql and 'has_database_privilege' in sql
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: self.role))


def test_runtime_accepts_only_explicit_restricted_marked_target():
    _require_connected_runtime(Session(runtime()))


@pytest.mark.parametrize('changes', [
    {'current_role':'r3_pilot_bootstrap'}, {'session_role':'r3_pilot_bootstrap'},
    {'database_name':'production'}, {'database_marker':None}, {'database_marker':'test'}, {'memberships':1},
    {'rolsuper':True}, {'rolcreatedb':True}, {'rolcreaterole':True}, {'rolreplication':True}, {'rolbypassrls':True},
    {'server_address':'203.0.113.1'}, {'server_port':5433}, {'listen_addresses':'*'},
    {'database_create':True}, {'writable_schemas':1}, {'owned_objects':1},
])
def test_runtime_fails_closed(changes):
    with pytest.raises(ValueError, match='restricted non-superuser'):
        _require_connected_runtime(Session(runtime(**changes)))


@pytest.mark.parametrize('value', ['0','59','3601','-1','forever',None])
def test_preview_duration_cannot_be_unbounded(value):
    with pytest.raises(ValueError): _connected_duration(value)


def test_preview_duration_bounds():
    assert _connected_duration('60') == 60
    assert _connected_duration('2100') == 2100
    assert _connected_duration('3600') == 3600


ENTITY = UUID('00000000-0000-0000-0000-000000000001')
OTHER = UUID('00000000-0000-0000-0000-000000000002')


def request(path, method='GET', body=None, query=''):
    async def receive():
        return dict(type='http.request', body=json.dumps(body or {}).encode(), more_body=False)
    return Request(dict(type='http', method=method, path=path, raw_path=path.encode(), query_string=query.encode(),
        headers=[], scheme='http', server=('testserver',80), client=('127.0.0.1',1)), receive)


class ScopeUnit:
    def __init__(self, target): self.session = SimpleNamespace(scalar=lambda *a, **k: target)
    def __enter__(self): return self
    def __exit__(self, *args): pass


@pytest.mark.parametrize('req', [
    request('/v1/entities/' + str(OTHER) + '/owner/home'),
    request('/v1/finance/reconciliation', query='entity_id='+str(OTHER)),
    request('/v1/payments', 'POST', {'entity_id':str(OTHER)}),
    request('/v1/entities', 'POST', {'name':'another'}),
    request('/v1/obligations/'+str(OTHER)),
])
def test_pilot_rejects_other_or_new_entity_even_legacy_unmanaged(req):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_require_pilot_scope(req, ENTITY, lambda:ScopeUnit(OTHER)))
    assert exc.value.status_code == 403


@pytest.mark.parametrize('req', [request('/v1/entities/'+str(ENTITY)+'/owner/home'),
    request('/v1/payments','POST',{'entity_id':str(ENTITY)}),
    request('/v1/obligations/'+str(ENTITY)), request('/r3/components.js')])
def test_pilot_keeps_its_existing_entity_routes(req):
    asyncio.run(_require_pilot_scope(req, ENTITY, lambda:ScopeUnit(ENTITY)))


def test_r3_static_assets_are_opt_in_authenticated_and_traversal_safe():
    app = FastAPI()
    def owner(req: Request):
        if req.cookies.get('preview_session') != 'private-cookie': raise HTTPException(401)
        return ENTITY
    _mount_connected_ui(app, owner, 'private-cookie')
    client = TestClient(app)
    login = client.get('/r3/')
    assert login.status_code == 200 and 'Только синтетические' in login.text
    assert 'private-cookie' not in login.text
    assert client.get('/r3/login.js').status_code == 200
    assert client.get('/r3/login.css').status_code == 200
    assert client.get('/r3/styles.css').status_code == 401
    assert client.get('/r3/cold-bootstrap.js').status_code == 401
    assert client.get('/r3/index.html').status_code == 401
    client.cookies.set('preview_session','private-cookie')
    assert client.get('/r3/',follow_redirects=False).headers['location'] == '/r3/connected.html'
    assert client.get('/r3/styles.css').status_code == 200
    cold_bootstrap = client.get('/r3/cold-bootstrap.js')
    assert cold_bootstrap.status_code == 200
    assert 'javascript' in cold_bootstrap.headers['content-type']
    assert 'export class ColdBootstrapAttempt' in cold_bootstrap.text
    assert 'Demo' in client.get('/r3/index.html').text
    assert client.get('/r3/%2e%2e/%2e%2e/config/owner_finance_opening.json').status_code == 404
    assert client.get('/r3/../preview.py').status_code == 404


def test_connection_options_cannot_override_exact_local_allowlist(monkeypatch):
    from greenhouse_owner_os.preview import build_preview
    monkeypatch.setenv('R3_CONNECTED_PILOT','1')
    with pytest.raises(ValueError,match='overrides'):
        build_preview('postgresql+psycopg://postgres@127.0.0.1:5432/greenhouse_owner_os_test?host=remote.invalid', 'x'*32, environment='test')
