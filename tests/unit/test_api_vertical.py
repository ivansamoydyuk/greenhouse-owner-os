from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi.testclient import TestClient

from greenhouse_owner_os.api.app import create_app


def test_api_obligation_payment_partial_flow(fx):
    app = create_app(finance_service=fx.service, actor_provider=lambda: fx.actor_id)
    client = TestClient(app)

    create_ob = client.post(
        "/v1/obligations",
        headers={"Idempotency-Key": "api-create-ob"},
        json={
            "entity_id": str(fx.entity_id),
            "counterparty_id": str(fx.counterparty_id),
            "direction": "payable",
            "original_amount": "10000.00",
            "currency": "TRY",
            "event_date": "2026-10-04",
            "due_date": "2026-10-10",
            "offset_ledger_account_id": str(fx.offset_account_id),
        },
    )
    assert create_ob.status_code == 201
    ob = create_ob.json()
    assert ob["status"] == "draft"

    post_ob = client.post(
        f"/v1/obligations/{ob['id']}/post?expected_version={ob['version']}",
        headers={"Idempotency-Key": "api-post-ob"},
    )
    assert post_ob.status_code == 200

    create_pay = client.post(
        "/v1/payments",
        headers={"Idempotency-Key": "api-create-pay"},
        json={
            "entity_id": str(fx.entity_id),
            "money_account_id": str(fx.money_account_id),
            "counterparty_id": str(fx.counterparty_id),
            "direction": "out",
            "amount": "4000.00",
            "currency": "TRY",
            "event_date": "2026-10-04",
            "allocations": [{"obligation_id": ob["id"], "amount": "4000.00"}],
        },
    )
    assert create_pay.status_code == 201
    pay = create_pay.json()

    post_pay = client.post(
        f"/v1/payments/{pay['id']}/post?expected_version={pay['version']}",
        headers={"Idempotency-Key": "api-post-pay"},
    )
    assert post_pay.status_code == 200
    assert post_pay.json()["obligation_balances"][ob["id"]] == "6000.00"

    read_ob = client.get(f"/v1/obligations/{ob['id']}")
    assert read_ob.status_code == 200
    assert read_ob.json()["remaining_amount"] == "6000.00"
