from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_openapi_contract_parses_and_has_vertical_paths():
    spec = yaml.safe_load((ROOT / "openapi/owner_core_v1.yaml").read_text())
    assert spec["openapi"] == "3.1.0"
    paths = spec["paths"]
    assert "/obligations" in paths
    assert "/obligations/{id}/post" in paths
    assert "/payments" in paths
    assert "/payments/{id}/post" in paths


def test_migration_chain_is_linear_and_importable():
    files = [
        ROOT / "alembic/versions/20261004_0001_architecture_foundation.py",
        ROOT / "alembic/versions/20261004_0002_finance_vertical.py",
        ROOT / "alembic/versions/20261004_0003_integrity_corrections.py",
        ROOT / "alembic/versions/20261004_0004_cash_settlements.py",
        ROOT / "alembic/versions/20261004_0005_advance_applications.py",
        ROOT / "alembic/versions/20261004_0006_cutover_history.py",
        ROOT / "alembic/versions/20261004_0007_reference_readiness.py",
    ]
    mods = []
    for idx, path in enumerate(files):
        spec = importlib.util.spec_from_file_location(f"migration_{idx}", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        mods.append(module)
    assert mods[0].revision == "20261004_0001"
    assert mods[0].down_revision is None
    assert mods[1].revision == "20261004_0002"
    assert mods[1].down_revision == mods[0].revision
    assert mods[2].revision == "20261004_0003"
    assert mods[2].down_revision == mods[1].revision
    assert mods[3].revision == "20261004_0004"
    assert mods[3].down_revision == mods[2].revision
    assert mods[4].revision == "20261004_0005"
    assert mods[4].down_revision == mods[3].revision
    assert mods[5].revision == "20261004_0006"
    assert mods[5].down_revision == mods[4].revision
    assert mods[6].revision == "20261004_0007"
    assert mods[6].down_revision == mods[5].revision


def test_sqlalchemy_metadata_imports_with_expected_tables():
    from greenhouse_owner_os.infrastructure.models import Base
    expected = {
        "core.entities", "core.actors", "core.counterparties",
        "finance.ledger_accounts", "finance.money_accounts", "finance.postings", "finance.posting_entries",
        "finance.obligations", "finance.payments", "finance.payment_allocations", "finance.advances",
        "audit.idempotency_keys", "audit.events",
    }
    assert expected.issubset(set(Base.metadata.tables))

