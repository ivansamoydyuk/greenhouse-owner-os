import copy
from uuid import UUID
import pytest
from greenhouse_owner_os.repositories.memory import MemoryAuditRepository
from tests.finance_integrity_cases import CASES, obligation, payment, reverse

@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
def test_finance_integrity(fx, case):
    case(fx)


def test_failed_reversal_rolls_back_every_effect_and_can_retry(fx, monkeypatch):
    ob=obligation(fx);pay=payment(fx,[(ob["id"],"10000")],"12000")
    before=copy.deepcopy(fx.store.__dict__)
    def fail(*args,**kwargs): raise RuntimeError("Injected audit failure")
    with monkeypatch.context() as m:
        m.setattr(MemoryAuditRepository,"append",fail)
        with pytest.raises(RuntimeError): reverse(fx,"payment",pay,key="retry-failed")
    assert fx.store.__dict__==before
    reverse(fx,"payment",pay,key="retry-failed")


def test_reversal_and_linkage_api(fx):
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.api.app import create_app
    ob=obligation(fx);pay=payment(fx,[(ob["id"],"4000")])
    client=TestClient(create_app(finance_service=fx.service,actor_provider=lambda:fx.actor_id))
    body={"expected_version":2,"event_date":"2026-10-04","reason":"API correction"}
    path=f'/v1/payments/{pay["id"]}/reverse'
    headers={"Idempotency-Key":"api-reverse"}
    first=client.post(path,json=body,headers=headers)
    assert first.status_code==200
    assert first.json()==client.post(path,json=body,headers=headers).json()
    assert client.post(path,json=body,headers={"Idempotency-Key":"api-double"}).status_code==409
    assert client.get(f'/v1/payments/{pay["id"]}/linkage').json()["reason"]=="API correction"
    assert client.get(f'/v1/entities/{fx.entity_id}/balances').json()["balances"]["TRY"]["cash"]=="0.00"
    result=client.post(f'/v1/obligations/{ob["id"]}/reverse',json=body,headers={"Idempotency-Key":"api-ob-reverse"})
    assert result.status_code==200
    assert client.get(f'/v1/obligations/{ob["id"]}/linkage').json()["status"]=="reversed"
    assert client.post(path,json={**body,"reason":" "},headers=headers).status_code==422
