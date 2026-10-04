from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.domain.history import HistoricalCandidateCommand
from tests.cutover_cases import CASES

@pytest.mark.parametrize('case',CASES,ids=lambda c:c.__name__)
def test_cutover_acceptance(fx,case):case(fx)

@pytest.mark.parametrize('values',[{}, {'source_row':' '}, {'source_row':'Sheet!1'},
    {'source_row':'1','raw_value':{},'confidence':'1.01'},
    {'source_row':'1','raw_value':{},'verification_state':'posted'},
    {'source_row':'1','raw_value':{},'imported_at':'2026-11-10'},
    {'source_row':'1','raw_value':{},'posting_id':'fake'}])
def test_invalid_import_input(values):
    with pytest.raises(ValueError):HistoricalCandidateCommand(**values)


def test_api_nonposting_evidence_and_posting_guard(fx):
    from tests.cutover_cases import draft
    client=TestClient(create_app(finance_service=fx.service,actor_provider=lambda:fx.actor_id))
    assert client.get(f'/v1/entities/{fx.entity_id}/cutover').json()['cutover_date']=='2026-10-04'
    b=client.post('/v1/import-batches',headers={'Idempotency-Key':'batch'},json={
        'entity_id':str(fx.entity_id),'source':'file','source_file':'old.csv','fingerprint':'f'*64})
    assert b.status_code==201
    r=client.post(f"/v1/import-batches/{b.json()['id']}/candidates",headers={'Idempotency-Key':'row'},json={
        'source_row':'1','event_date':'2025-09-15','raw_value':{'amount':'10'}})
    assert r.status_code==201 and r.json()['posting_policy']=='NON_POSTING'
    assert client.get(f"/v1/historical-candidates/{r.json()['id']}").json()==r.json()
    assert client.post(f"/v1/payments/{r.json()['id']}/post?expected_version=1",headers={'Idempotency-Key':'no-post'}).status_code==404
    old=draft(fx)
    response=client.post(f"/v1/obligations/{old['id']}/post?expected_version=1",headers={'Idempotency-Key':'blocked'})
    assert response.status_code==422 and 'pre-cut-over' in response.json()['error']['message']


def test_cutover_openapi_matches_runtime(fx):
    import yaml
    from pathlib import Path
    spec=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    runtime=create_app(finance_service=fx.service,actor_provider=lambda:fx.actor_id).openapi()
    for path,ops in runtime['paths'].items():
        if any(x in path for x in ('/cutover','/import-batches','/historical-candidates')):
            assert spec['paths'][path.removeprefix('/v1')]==ops
    for name in ('ImportBatchCommand','HistoricalCandidateCommand','VerificationState'):
        assert spec['components']['schemas'][name]==runtime['components']['schemas'][name]
