from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    py_files = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "alembic").rglob("*.py"))
    for path in py_files:
        ast.parse(path.read_text(), filename=str(path))
    spec = yaml.safe_load((ROOT / "openapi/owner_core_v1.yaml").read_text())
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["version"] == "0.1.0"
    print(f"OK: parsed {len(py_files)} Python files; OpenAPI 3.1.0 contract loaded")


if __name__ == "__main__":
    main()
