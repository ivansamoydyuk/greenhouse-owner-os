"""Run directly on the owned, marked, restricted-role pilot (never resets DB).

APP_ENV=test R3_CONNECTED_PILOT=1 TEST_DATABASE_URL=<guarded URL>
PYTHONPATH=src:. python tests/r3_connected_preview_smoke.py
PREVIEW_SECRET must be a fresh temporary value supplied only through the environment.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.models import EntityRow
from greenhouse_owner_os.preview import build_preview
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.services.references import ReferenceService, provision_authenticated_owner


def run():
    assert os.environ['APP_ENV'] == 'test' and os.environ['R3_CONNECTED_PILOT'] == '1'
    url = os.environ['TEST_DATABASE_URL']
    secret = os.environ['PREVIEW_SECRET']
    assert len(secret) >= 24, 'Temporary preview secret must contain at least 24 characters'
    app = build_preview(url, secret, environment='test')
    checks = []
    with TestClient(app) as client:
        assert client.get('/r3/').status_code == 200
        assert client.get('/r3/styles.css').status_code == 401
        assert client.get('/preview/config').status_code == 401
        assert client.post('/preview/login', json={'secret':secret}, headers={'Origin':'https://attacker.invalid'}).status_code == 403
        response = client.post('/preview/login', json={'secret':secret})
        assert response.status_code == 200 and 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
        config = client.get('/preview/config').json()
        assert config['mode'] == 'CONNECTED_TEST' and config['backend_connected'] and config['synthetic_only']
        assert config['synthetic_fixture']['owner_opening_loaded'] is False
        assert config['r2_fixtures'] is False and config['actual_operations'] is False
        assert 'secret' not in json.dumps(config).lower()
        eid = config['entity_id']; root = '/v1/entities/' + eid
        assert client.get('/r3/styles.css').status_code == 200
        assert client.get('/r3/index.html').status_code == 200
        assert client.get('/r3/%2e%2e/%2e%2e/config/owner_finance_opening.json').status_code == 404
        assert client.get('/preview/fixtures/gorgun.png').status_code == 404
        assert "connect-src 'self'" in client.get('/r3/styles.css').headers['content-security-policy']
        assert client.get(root+'/owner/tasks').json()['total'] == 5
        assert client.get(root+'/finance-overview').json()['balances']['TRY']['cash'] == '5000.00'
        assert client.get(root+'/inventory').json()['opening_state'] == 'UNINITIALIZED_VERIFY'
        assert client.get('/v1/finance/reconciliation',params={'entity_id':eid}).json()['ok']
        checks.extend(['restricted_runtime_guard','synthetic_profile','login_auth','origin','CSP','assets_auth','traversal','no_real_owner_opening','reconciliation'])
        sf = make_session_factory(url); factory = lambda:SqlAlchemyUnitOfWork(sf)
        other = provision_authenticated_owner(sf, external_ref='test:r3:scope-other',display_name='TEST Other Owner',environment='TEST_PREVIEW')
        otherapp = create_app(finance_service=FinanceService(factory),reference_service=ReferenceService(factory,environment='TEST_PREVIEW'),actor_provider=lambda:other)
        with TestClient(otherapp) as setup:
            def post(path, body, key):
                result = setup.post('/v1'+path,json=body,headers={'Idempotency-Key':'test:r3:scope:'+key})
                assert result.status_code in (200,201), result.text
                return result.json()
            foreign = post('/entities',dict(name='TEST R3 Other Owner Farm',currency='TRY',source='test:r3:scope'),'entity')['id']
            refs = post('/entities/'+foreign+'/reference-bootstrap',dict(currency='TRY'),'bootstrap')
            party = post('/entities/'+foreign+'/counterparties',dict(name='TEST Other Party',role='supplier',source='test:r3:scope'),'party')
            ledger = next(r['id'] for r in refs['references'] if r['system_code']=='GENERAL_EXPENSE')
            obligation = post('/obligations',dict(entity_id=foreign,counterparty_id=party['id'],direction='payable',original_amount='3.00',currency='TRY',event_date='2026-10-10',offset_ledger_account_id=ledger),'obligation')
        assert client.get('/v1/entities/'+foreign+'/owner/home').status_code == 403
        assert client.get('/v1/obligations/'+obligation['id']).status_code == 403
        assert client.post('/v1/obligations/'+obligation['id']+'/post?expected_version=1',headers={'Idempotency-Key':'forbidden-other'}).status_code == 403
        assert client.post('/v1/entities',json={'name':'another'},headers={'Idempotency-Key':'forbidden-create'}).status_code == 403
        assert client.post('/v1/payments',json={'entity_id':foreign},headers={'Idempotency-Key':'forbidden-other-body'}).status_code == 403
        assert client.get('/v1/finance/reconciliation',params={'entity_id':foreign}).status_code == 403
        unmanaged = uuid4()
        with sf.begin() as session:
            session.add(EntityRow(id=unmanaged,name='TEST unmanaged ACL negative fixture',default_currency='TRY'))
        assert client.get('/v1/entities/'+str(unmanaged)+'/finance-overview').status_code == 403
        checks.append('managed_unmanaged_global_resource_scope_denied')
        assert client.post('/preview/logout').status_code == 200
        assert client.get('/preview/config').status_code == 401
        assert client.post('/preview/login',json={'secret':secret}).status_code == 200
        import greenhouse_owner_os.preview as preview
        clock = preview.time
        with patch.object(preview,'time',SimpleNamespace(monotonic=lambda:clock.monotonic()+4000,time=clock.time)):
            for path in ('/health','/preview/config',root+'/owner/home','/r3/styles.css','/r3/'):
                assert client.get(path).status_code == 410
            assert client.post('/preview/login',json={'secret':secret}).status_code == 410
        checks.extend(['logout','expiry_all_routes'])
    replay = TestClient(build_preview(url,secret,environment='test'))
    assert replay.post('/preview/login',json={'secret':secret}).status_code == 200
    assert replay.get('/preview/config').json()['synthetic_fixture'] == config['synthetic_fixture']
    checks.append('fixture_restart_idempotency')
    result = dict(status='PASS', checks=checks, fixture=config['synthetic_fixture'], backend_connected=True,
        runtime='restricted non-superuser; real PostgreSQL', feature_sha=config['feature_sha'])
    output = os.environ.get('R3_BACKEND_EVIDENCE')
    if output:Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status='PASS',checks=checks),ensure_ascii=False))


if __name__ == '__main__':run()
