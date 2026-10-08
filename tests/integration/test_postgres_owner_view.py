from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient
from greenhouse_owner_os.services.owner_view import owner_view
from greenhouse_owner_os.preview import build_preview
from scripts.validate_owner_opening import setup_confirmed_profile
from tests.integration.test_postgres_readiness import preview, setup, send


def test_owner_projection_scoped_exact_read_only_and_drafts_excluded(preview):
    c,eid,cash,bank,cp,ids=setup(preview)
    before=preview[3].get_currency_balances(UUID(eid))
    ob=send(c,'/obligations',dict(entity_id=eid,counterparty_id=cp,direction='payable',original_amount='100',currency='TRY',event_date='2026-10-04',offset_ledger_account_id=ids['GENERAL_EXPENSE']))
    result=c.get('/v1/entities/'+eid+'/finance-overview').json()
    assert result['balances']==before['balances']
    assert result['operations'][0]['status']=='draft'
    assert result['obligations'][0]['settlement_status']=='draft'
    assert {a['balance'] for a in result['accounts']}=={'0.00'}
    send(c,f"/obligations/{ob['id']}/post?expected_version=1",expected=200)
    result=c.get('/v1/entities/'+eid+'/finance-overview').json()
    assert result['balances']['TRY']['payable']=='100.00'
    assert result['obligations'][0]['remaining_amount']=='100.00'
    assert all(o['entity_id']==eid for o in result['operations'])
    assert preview[3].reconcile(entity_id=UUID(eid))['ok']
    for suffix in ['?limit=0','?limit=101','?offset=-1']:
        assert c.get('/v1/entities/'+eid+'/finance-overview'+suffix).status_code==422
    assert c.get('/v1/entities/'+str(uuid4())+'/finance-overview').status_code==404


def test_owner_projection_pagination(preview):
    c,eid,cash,bank,cp,ids=setup(preview)
    for i in range(3):
        send(c,'/owner-fundings',dict(entity_id=eid,money_account_id=cash,amount=str(i+1),currency='TRY',event_date='2026-10-04'))
    first=c.get(f'/v1/entities/{eid}/finance-overview?limit=2').json()
    second=c.get(f'/v1/entities/{eid}/finance-overview?offset=2&limit=2').json()
    assert first['has_more'] and not second['has_more']
    assert len(first['operations'])==2 and len(second['operations'])==1
    assert not {o['id'] for o in first['operations']} & {o['id'] for o in second['operations']}


def test_guarded_preview_auth_cookie_origin_owner_opening_and_health(migrated_database,monkeypatch):
    monkeypatch.setenv('GITHUB_SHA','f'*40)
    monkeypatch.setenv('OWNER_PREVIEW_FEATURE_SHA','e'*40)
    secret='test-preview-secret-at-least-24-chars'
    app=build_preview(migrated_database,secret,environment='test')
    c=TestClient(app)
    assert c.get('/health').json()=={'ok':True,'environment':'TEST_PREVIEW'}
    assert c.get('/preview/config').status_code==401

    assert c.get('/v1/finance/reconciliation').status_code==401
    assert c.post('/preview/login',json={'secret':'wrong'}).status_code==401
    assert c.post('/preview/login',json={'secret':secret},headers={'Origin':'https://attacker.invalid'}).status_code==403
    login=c.post('/preview/login',json={'secret':secret})
    assert login.status_code==200 and 'HttpOnly' in login.headers['set-cookie'] and 'SameSite=strict' in login.headers['set-cookie']
    eid=c.get('/preview/config').json()['entity_id']
    assert c.get('/preview/config').json()['feature_sha']=='e'*40
    view=c.get(f'/v1/entities/{eid}/finance-overview').json()
    assert view['balances']['TRY']['payable']=='2600.00'
    assert view['balances']['TRY']['cash']=='0.00'
    assert view['obligations'][0]['opening']['original_event_date']=='2026-10-02'
    assert view['obligations'][0]['opening']['opening_date']=='2026-10-04'
    assert any(r['original_name']=='Серкан Ремонт насосов' for r in view['references'])
    assert c.post('/v1/obligations',json={},headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert c.get('/').status_code==200
    assert 'frame-ancestors' in c.get('/').headers['content-security-policy']
    assert c.get('/assets/app.js').status_code==200
    assert c.post('/preview/logout').status_code==200
    assert c.get('/preview/config').status_code==401


def test_hosted_preview_explicit_https_origin_secure_cookie(migrated_database):
    secret='test-preview-https-secret-at-least-24-chars'
    origin='https://isolated-preview.example'
    c=TestClient(build_preview(migrated_database,secret,environment='test',public_origin=origin))
    response=c.post('/preview/login',json={'secret':secret},headers={'Origin':origin})
    assert response.status_code==200 and 'Secure' in response.headers['set-cookie']
    assert c.post('/preview/login',json={'secret':secret},headers={'Origin':'https://evil.invalid'}).status_code==403
    assert c.post('/preview/login',json={'secret':secret},headers={'Origin':'http://testserver'}).status_code==403
    with pytest.raises(ValueError):build_preview(migrated_database,secret,environment='test',public_origin='http://unsafe.invalid')


@pytest.mark.parametrize('environment,url,secret',[
    ('production','postgresql+psycopg://postgres@localhost:5432/greenhouse_owner_os_test','x'*24),
    ('test','postgresql+psycopg://postgres@db.invalid:5432/greenhouse_owner_os_test','x'*24),
    ('test','postgresql+psycopg://postgres@localhost:5432/production','x'*24),
    ('test','postgresql+psycopg://postgres@localhost:5432/greenhouse_owner_os_test','short'),
])
def test_preview_refuses_non_disposable_environment(environment,url,secret):
    with pytest.raises(ValueError):build_preview(url,secret,environment=environment)
