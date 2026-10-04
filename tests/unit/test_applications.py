from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.domain.cash import AdvanceApplicationCommand
from tests.application_cases import CASES,setup,command,remaining

@pytest.fixture
def appfx(fx):
    fx=SimpleNamespace(**fx.__dict__)
    fx.other_counterparty_id=uuid4()
    return fx

@pytest.mark.parametrize('case',CASES,ids=lambda c:c.__name__)
@pytest.mark.parametrize('direction',['paid','received'])
def test_application_acceptance(appfx,case,direction):case(appfx,direction)

@pytest.mark.parametrize('amount',['0','-1','0.001',1.1,'NaN','Infinity'])
def test_exact_application_amount(appfx,amount):
    pay,ob=setup(appfx)
    with pytest.raises(ValidationError): command(appfx,pay,ob,amount) if not isinstance(amount,float) else AdvanceApplicationCommand(**{**command(appfx,pay,ob).model_dump(),'amount':amount})

@pytest.mark.parametrize('direction',['paid','received'])
def test_application_api(appfx,direction):
    pay,ob=setup(appfx,direction)
    client=TestClient(create_app(finance_service=appfx.service,actor_provider=lambda:appfx.actor_id))
    body=command(appfx,pay,ob,'100',direction).model_dump(mode='json')
    path='/v1/'+('supplier' if direction=='paid' else 'customer')+'-advance-applications'
    result=client.post(path,json=body,headers={'Idempotency-Key':'api'})
    assert result.status_code==201,result.text
    r=result.json()
    assert client.post(path,json=body,headers={'Idempotency-Key':'api'}).json()==r
    assert client.get('/v1/advances/'+pay['advance_id']).json()['applications'][0]['id']==r['id']
    assert client.get('/v1/advance-applications/'+r['id']+'/linkage').json()['status']=='posted'
    position=client.get(f'/v1/entities/{appfx.entity_id}/counterparties/{appfx.counterparty_id}/financial-position?currency=TRY')
    assert position.status_code==200
    result=client.post('/v1/advance-applications/'+r['id']+'/reverse',json={'expected_version':2,'event_date':'2026-10-04','reason':'correct'},headers={'Idempotency-Key':'api-undo'})
    assert result.status_code==200,result.text

def test_application_openapi_matches_runtime():
    from pathlib import Path
    import yaml
    spec=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    runtime=create_app(finance_service=None,actor_provider=lambda:None).openapi()
    for path in runtime['paths']:
        if 'advance-applications' in path or '/advances/' in path or 'financial-position' in path:
            assert spec['paths'][path.removeprefix('/v1')]==runtime['paths'][path]
    assert spec['components']['schemas']['AdvanceApplicationCommand']==runtime['components']['schemas']['AdvanceApplicationCommand']

def test_application_linkage_has_source_ids(appfx):
    from tests.application_cases import apply
    from uuid import UUID
    pay,ob=setup(appfx)
    r=apply(appfx,command(appfx,pay,ob,'100'))
    link=appfx.service.get_linkage('advance_application',UUID(r['id']))
    assert link['advance_id']==pay['advance_id'] and link['obligation_id']==ob['id']
    assert link['counterparty_id']==str(appfx.counterparty_id) and link['amount']=='100.00'
