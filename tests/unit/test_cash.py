from uuid import uuid4
from decimal import Decimal
import pytest
from pydantic import ValidationError as InputError
from greenhouse_owner_os.domain.cash import TransferCommand, FundingCommand, SupplierAdvanceCommand
from greenhouse_owner_os.domain.errors import ValidationError
from tests.cash_cases import CASES, transfer, create, base


@pytest.fixture
def cashfx(fx):
    fx.destination_id,_=fx.store.seed_money_account(entity_id=fx.entity_id,currency='TRY')
    fx.funding_ledger_id=fx.store.seed_ledger_account(entity_id=fx.entity_id,currency='TRY',system_code='OWNER_FUNDING')
    return fx


@pytest.mark.parametrize('case',CASES,ids=lambda c:c.__name__)
def test_cash_acceptance(cashfx,case): case(cashfx)


@pytest.mark.parametrize('amount',['0','-1','0.001','NaN','Infinity','1000000000000000000.00'])
def test_invalid_exact_amount(cashfx,amount):
    with pytest.raises(InputError): FundingCommand(**base(cashfx,amount),money_account_id=cashfx.money_account_id)


def test_float_rejected(cashfx):
    data=base(cashfx,'1');data['amount']=1.01
    with pytest.raises(InputError): FundingCommand(**data,money_account_id=cashfx.money_account_id)


def test_transfer_account_validation(cashfx):
    data=transfer(cashfx).model_dump();data['destination_account_id']=cashfx.money_account_id
    with pytest.raises(InputError): TransferCommand(**data)
    foreign,_=cashfx.store.seed_money_account(entity_id=cashfx.entity_id,currency='USD')
    data['destination_account_id']=foreign
    with pytest.raises(ValidationError): create(cashfx,'transfer',TransferCommand(**data))
    data['destination_account_id']=cashfx.destination_id;data['entity_id']=uuid4()
    with pytest.raises(ValidationError): create(cashfx,'transfer',TransferCommand(**data))


def test_api_cash_flows(cashfx):
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.api.app import create_app
    client=TestClient(create_app(finance_service=cashfx.service,actor_provider=lambda:cashfx.actor_id))
    for path,cmd in [('transfers',transfer(cashfx)),('owner-fundings',FundingCommand(**base(cashfx,'100000'),money_account_id=cashfx.money_account_id)),('supplier-advances',SupplierAdvanceCommand(**base(cashfx,'50000'),money_account_id=cashfx.money_account_id,counterparty_id=cashfx.counterparty_id))]:
        res=client.post('/v1/'+path,json=cmd.model_dump(mode='json'),headers={'Idempotency-Key':path})
        assert res.status_code==201,res.text
        r=res.json();res=client.post(f"/v1/{path}/{r['id']}/post?expected_version=1",headers={'Idempotency-Key':'post-'+path})
        assert res.status_code==200,res.text
        r=res.json();res=client.post(f"/v1/{path}/{r['id']}/reverse",json={'expected_version':2,'event_date':'2026-10-04','reason':'Test reversal'},headers={'Idempotency-Key':'reverse-'+path})
        assert res.status_code==200,res.text
    assert client.post('/v1/transfers',json={},headers={'Idempotency-Key':'invalid'}).status_code==422
