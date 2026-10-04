import pytest
from pydantic import ValidationError
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.domain.references import MoneyAccountSetup, canonical_key
from greenhouse_owner_os.services.references import reference_id


@pytest.mark.parametrize('url,env',[
    ('postgresql+psycopg://postgres:x@localhost:5432/greenhouse_owner_os_test','production'),
    ('postgresql+psycopg://postgres:x@production:5432/greenhouse_owner_os_test','test'),
    ('postgresql+psycopg://postgres:x@localhost:5432/production_test','test'),
    ('postgresql+psycopg://postgres:test@localhost:5432/production','test'),
    ('postgresql+psycopg://postgres:x@localhost:5433/greenhouse_owner_os_test','test'),
])
def test_destructive_reset_refuses_non_disposable(url,env):
    with pytest.raises(ValueError):require_disposable_database(url,env)


def test_stable_identity_and_input_validation():
    require_disposable_database('postgresql+psycopg://postgres:x@localhost:5432/greenhouse_owner_os_test','test')
    assert canonical_key('Café')==canonical_key('Cafe\u0301')
    assert reference_id('a','money_account','x')==reference_id('a','money_account','x')
    assert reference_id('a','money_account','x')!=reference_id('b','money_account','x')
    for changes in ({'name':' Cash'},{'currency':'ZZZ'},{'classification':'OWNER_VERIFIED'}):
        with pytest.raises(ValidationError):MoneyAccountSetup(**dict(name='Cash',source='owner:verified',currency='TRY',account_type='cash',**{} )|changes)


def test_reference_openapi_and_actor_dependency():
    from pathlib import Path
    import yaml
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.api.app import create_app
    app=create_app(finance_service=None,reference_service=object(),actor_provider=lambda:None)
    runtime=app.openapi()
    spec=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    for path,ops in runtime['paths'].items():
        if any(word in path for word in ('reference','readiness','/audit','/reconciliation','money-accounts','/counterparties')) or path=='/v1/entities':
            assert spec['paths'][path.removeprefix('/v1')]==ops
    def unauthenticated():raise HTTPException(status_code=401,detail='owner authentication required')
    c=TestClient(create_app(finance_service=None,reference_service=object(),actor_provider=unauthenticated))
    assert c.get('/v1/reference-currencies').status_code==401
    assert c.post('/v1/entities',json={'name':'E','source':'owner:verified','currency':'TRY'},headers={'Idempotency-Key':'x'}).status_code==401
