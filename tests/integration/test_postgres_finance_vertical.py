from __future__ import annotations

import os
from pathlib import Path

import pytest

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not configured; PostgreSQL integration test intentionally does not substitute SQLite")


def test_postgresql_integration_environment_is_explicit():
    # The full DB integration fixture is activated when the project has a provisioned PostgreSQL test DB.
    # Keeping this test explicit prevents false confidence from SQLite behavior that differs on schemas,
    # deferred constraint triggers, row locks and PostgreSQL numeric/JSONB semantics.
    assert DATABASE_URL.startswith("postgresql+")
