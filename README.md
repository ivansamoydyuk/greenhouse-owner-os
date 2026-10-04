# Greenhouse Owner OS — Module 01 Architecture & Database

Implementation baseline for the owner-first core. Scope is backend/domain/database only.

Implemented vertical slice:

`obligation -> payment -> allocation -> remaining balance`

Key rules:
- PostgreSQL is the authoritative datastore.
- Money uses exact `Decimal` / PostgreSQL `NUMERIC`, never float.
- Draft/review records have no financial effect.
- Posted financial effects create immutable balanced managerial postings.
- One payment can allocate to many obligations; one obligation can receive many payments.
- Allocation can never exceed payment amount or obligation remaining amount.
- Overpayment creates an explicit advance; debt never goes negative.
- All externally triggered writes use idempotency keys.
- Posting uses row locking and one database transaction.
- Posted financial postings are reversed by compensating postings; they are not edited.

## Layout

- `alembic/versions/20261004_0001_architecture_foundation.py` — core/audit/posting foundation.
- `alembic/versions/20261004_0002_finance_vertical.py` — obligations, payments, allocations, advances, balance views.
- `src/greenhouse_owner_os/` — domain, repositories, services and FastAPI routes.
- `openapi/owner_core_v1.yaml` — API contract for the implemented vertical slice.
- `tests/unit/` — deterministic business-rule tests.
- `tests/integration/` — PostgreSQL tests; skipped unless `TEST_DATABASE_URL` is configured.

## Local verification

```bash
python -m pytest -q
python scripts/verify_artifacts.py
```

PostgreSQL integration:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://...'
python -m pytest -q tests/integration
```

The runtime dependency list includes `psycopg[binary]`; the current artifact build environment may not have a PostgreSQL server, so integration tests are designed to skip rather than silently substitute SQLite for PostgreSQL semantics.
