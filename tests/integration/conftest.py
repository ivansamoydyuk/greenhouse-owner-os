from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.models import ActorRow, CounterpartyRow, EntityRow, LedgerAccountRow, MoneyAccountRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService

ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.getenv("TEST_DATABASE_URL")

if not DATABASE_URL:
    pytest.skip(
        "TEST_DATABASE_URL not configured; PostgreSQL integration tests intentionally do not substitute SQLite",
        allow_module_level=True,
    )

from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
try:
    require_disposable_database(DATABASE_URL, os.getenv('APP_ENV'))
except ValueError as exc:
    pytest.fail(str(exc))


@dataclass(frozen=True)
class PgFixture:
    database_url: str
    session_factory: object
    service: FinanceService
    actor_id: UUID
    entity_id: UUID
    counterparty_id: UUID
    other_counterparty_id: UUID
    money_account_id: UUID
    offset_account_id: UUID


def _reset_and_migrate(database_url: str) -> None:
    require_disposable_database(database_url, os.getenv('APP_ENV'))
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS inventory CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS harvest CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS audit CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS finance CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS core CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    engine.dispose()

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> str:
    _reset_and_migrate(DATABASE_URL)
    return DATABASE_URL


@pytest.fixture
def pgfx(migrated_database: str) -> PgFixture:
    session_factory = make_session_factory(migrated_database)
    actor_id = uuid4()
    entity_id = uuid4()
    counterparty_id = uuid4()
    other_counterparty_id = uuid4()
    money_account_id = uuid4()
    bank_ledger_id = uuid4()
    offset_account_id = uuid4()

    with session_factory.begin() as session:
        session.add(EntityRow(id=entity_id, name="Test Entity", default_currency="TRY"))
        session.add(ActorRow(id=actor_id, actor_type="owner", display_name="Test Owner"))
        # These mappings have no ORM relationships; flush FK parents explicitly.
        session.flush()
        session.add_all([
            CounterpartyRow(id=counterparty_id, entity_id=entity_id, name="Supplier A", counterparty_type="company"),
            CounterpartyRow(id=other_counterparty_id, entity_id=entity_id, name="Supplier B", counterparty_type="company"),
        ])
        accounts = [
            LedgerAccountRow(id=uuid4(), entity_id=entity_id, code="AP", name="AP Control", account_class="payable", normal_side="credit", currency="TRY", system_code="AP_CONTROL"),
            LedgerAccountRow(id=uuid4(), entity_id=entity_id, code="AR", name="AR Control", account_class="receivable", normal_side="debit", currency="TRY", system_code="AR_CONTROL"),
            LedgerAccountRow(id=uuid4(), entity_id=entity_id, code="SUPADV", name="Supplier Advance", account_class="advance", normal_side="debit", currency="TRY", system_code="SUPPLIER_ADVANCE"),
            LedgerAccountRow(id=uuid4(), entity_id=entity_id, code="CUSTADV", name="Customer Advance", account_class="advance", normal_side="credit", currency="TRY", system_code="CUSTOMER_ADVANCE"),
            LedgerAccountRow(id=offset_account_id, entity_id=entity_id, code="COST", name="Test Cost", account_class="expense", normal_side="debit", currency="TRY", system_code=None),
            LedgerAccountRow(id=bank_ledger_id, entity_id=entity_id, code="BANK", name="Test Bank", account_class="cash", normal_side="debit", currency="TRY", system_code=None),
        ]
        session.add_all(accounts)
        session.flush()
        session.add(MoneyAccountRow(id=money_account_id, entity_id=entity_id, ledger_account_id=bank_ledger_id, account_type="bank", name="Test Bank", currency="TRY"))

    return PgFixture(
        database_url=migrated_database,
        session_factory=session_factory,
        service=FinanceService(lambda: SqlAlchemyUnitOfWork(session_factory)),
        actor_id=actor_id,
        entity_id=entity_id,
        counterparty_id=counterparty_id,
        other_counterparty_id=other_counterparty_id,
        money_account_id=money_account_id,
        offset_account_id=offset_account_id,
    )
