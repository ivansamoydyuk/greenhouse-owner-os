import pytest
from pydantic import ValidationError
from uuid import uuid4
from greenhouse_owner_os.domain.opening import OpeningPayableCommand


@pytest.mark.parametrize('changes',[
    {'amount':2600.0}, {'amount':'0'}, {'amount':'-1'}, {'amount':'1.001'},
    {'amount':'NaN'}, {'source':' '}, {'opening_date':'2026-10-04'},
    {'verification_state':'VERIFIED'}, {'historical_candidate_id':str(uuid4())},
])
def test_opening_command_has_no_date_override_or_import_promotion(changes):
    body=dict(counterparty_id=uuid4(),amount='2600.00',currency='TRY',original_event_date='2026-10-02',
              description='pipe repair',source='owner:source-item',approval_reference='owner:approval')
    with pytest.raises(ValidationError):OpeningPayableCommand(**(body|changes))


def test_opening_openapi_and_owner_authentication():
    import yaml
    from pathlib import Path
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.api.app import create_app
    path='/v1/entities/{entity_id}/opening-payables'
    app=create_app(finance_service=None,actor_provider=lambda:None)
    spec=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    assert spec['paths'][path.removeprefix('/v1')]==app.openapi()['paths'][path]
    def denied():raise HTTPException(status_code=401,detail='owner authentication required')
    c=TestClient(create_app(finance_service=None,actor_provider=denied))
    assert c.post('/v1/entities/'+str(uuid4())+'/opening-payables',headers={'Idempotency-Key':'a'},json={}).status_code==401
