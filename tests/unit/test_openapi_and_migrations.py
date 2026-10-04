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
