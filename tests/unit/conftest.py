from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from greenhouse_owner_os.repositories.memory import MemoryStore, MemoryUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService


@dataclass
class FinanceFixture:
    store: MemoryStore
    service: FinanceService
    actor_id: UUID
    entity_id: UUID
    counterparty_id: UUID
    other_counterparty_id: UUID
    money_account_id: UUID
    offset_account_id: UUID


@pytest.fixture
def fx() -> FinanceFixture:
    store = MemoryStore()
    actor_id = uuid4()
    entity_id = uuid4()
    counterparty_id = uuid4()
    other_counterparty_id = uuid4()
    for system_code in ["AP_CONTROL", "AR_CONTROL", "SUPPLIER_ADVANCE", "CUSTOMER_ADVANCE"]:
        store.seed_ledger_account(entity_id=entity_id, currency="TRY", system_code=system_code)
    offset_account_id = store.seed_ledger_account(entity_id=entity_id, currency="TRY")
    money_account_id, _ = store.seed_money_account(entity_id=entity_id, currency="TRY")
    return FinanceFixture(
        store=store,
        service=FinanceService(lambda: MemoryUnitOfWork(store)),
        actor_id=actor_id,
        entity_id=entity_id,
        counterparty_id=counterparty_id,
        other_counterparty_id=other_counterparty_id,
        money_account_id=money_account_id,
        offset_account_id=offset_account_id,
    )
