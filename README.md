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

Finance operational setup (migration `20261004_0007`): compose `ReferenceService`
with the same SQL unit-of-work factory as `FinanceService`, and explicitly choose
`OWNER_VERIFIED` or `TEST_PREVIEW`. Pass it as `reference_service` to `create_app`.
An authenticated server adapter provisions its owner actor with
`provision_authenticated_owner`; identity comes from the trusted auth provider,
never a request-supplied actor ID. Authentication/authorization deployment remains
separate; this task does not publish a server or create production credentials.

Through `/v1/entities`, create the exact owner-confirmed entity and default
currency. Run `/v1/entities/{id}/reference-bootstrap` once per used currency, then
create owner-confirmed money accounts and counterparties through their scoped
POST endpoints. `/references` returns records, IDs, classification, original names,
creator/source and currency-specific system mappings. `/readiness` is read-only
and fails on missing/unverified mappings or owner references. It never repairs.
Creating references/bootstrap has no journal or balance effect. Generic expense
and revenue offsets are technical ledger defaults; no ERP category directory is
required. Existing unregistered records are reported/conflicted, never silently
adopted. References are create/read only; reclassification, renaming, deletion or
adoption needs a separately reviewed workflow.

Setup supports TRY, USD, EUR and UAH independently; no exchange/conversion is
performed. Bootstrap includes AP/AR, both advance controls, owner funding,
transfer fee, general expense and general revenue, plus a reserved opening-equity
offset for explicitly approved opening payables. UUIDv5 identities and canonical
NFC/casefold duplicate keys are stable; original names are preserved exactly.
Requests with changed original name/definition conflict. Repeated imports retain
the first creator/source; each successful request key uses existing idempotency.

The operational preview is ephemeral PostgreSQL 17 in GitHub Actions, with an
in-process HTTP TestClient calling the real API/services. It is not a public
preview URL. Every synthetic entity/reference is TEST_PREVIEW; system ledgers are
SYSTEM within that entity. Preview setup checks the exact local disposable DB
and PostgreSQL refuses preview entity insertion into differently named databases.
No production environment is created. To reproduce in a disposable local setup,
use `APP_ENV=test`, `TEST_DATABASE_URL` targeting PostgreSQL+psycopg on
localhost:5432, user postgres, database greenhouse_owner_os_test, then run
`pytest tests/integration/test_postgres_readiness.py -v`. Credentials must come
from local environment configuration. Test reset refuses other targets even when
URL passwords or database names contain the substring `test`.

Smoke proves zero openings, reference setup/retry, obligation posting, partial
payment/undo/correction draft, funding, transfer, supplier advance application and
undo, direct settlement, audit/linkage, counterparty position and reconciliation.
`/v1/finance/reconciliation?entity_id={id}` scopes findings to one entity; omitting
it checks all entities. Existing cut-over/historical guards stay in force.

Owner-authorized opening payable correction is a separate explicit workflow:
`POST /v1/entities/{id}/opening-payables` requires an authenticated owner,
owner-confirmed entity/supplier references, a unique evidence source and approval
reference. Original event date remains on the obligation; recognition occurs on
the immutable cut-over date against reserved opening equity, never cash or P&L.
The approval, obligation, journal and audit are atomic and immutable; ordinary
payments/allocation and reversal remain supported. Other historical replay is
still forbidden. The opening snapshot is initial recognition, not current debt.

`config/owner_finance_opening.json` is explicit owner-confirmed input, NOT an
automatic seed or production migration. It records the five zero money accounts
and the approved TRY 2,600 payable to Серкан Ремонт насосов (original event
2026-10-02, opening recognition 2026-10-04). No real operation is replayed.
`python scripts/validate_owner_opening.py` validates these exact references and
opening via the API only in the guarded disposable database. It is not a
production setup command and creates no factual payment. Tests exercising later
payments label them synthetic and reverse them. No remaining owner-reference
data gate applies to this confirmed profile; production deployment remains out
of scope.

## Owner Finance mobile preview

The Russian owner interface is served by the existing FastAPI application through
an explicit **disposable preview adapter**, not a production entrypoint. Four
sections expose position/accounts per currency, open debts/advances, paginated
operations and counterparties. Existing Finance R1 command endpoints remain the
only source of posting, allocation, fee, advance, settlement and correction rules.
`finance-overview` adds a read-only REPEATABLE READ projection over existing views;
no schema migration or alternative financial arithmetic is introduced.

Every preview mutation is synthetic. The exact owner reference manifest and
opening AP are provisioned idempotently through the reviewed API. No payment to
Serkan is automatically created. The payment form can settle his opening debt
normally; create, explicit confirmation/post, reversal and supported linked
obligation/payment correction are distinct actions. Amounts remain decimal
strings in the UI and requests. HISTORY/VERIFY stays outside live balances.

Local reproduction uses PostgreSQL 17 database `greenhouse_owner_os_test`, user
`postgres`, localhost port 5432, `APP_ENV=test`, and matching `DATABASE_URL` /
`TEST_DATABASE_URL`. Install `pip install -e ".[dev]"`, `npm ci`, then:

```bash
alembic upgrade head
npm run test:ui
npx playwright install --with-deps chromium
python scripts/run_owner_e2e.py
```

The E2E command explicitly resets only the exact allowlisted disposable database,
starts the real authenticated HTTP server and runs Chromium at mobile/desktop
widths. It reverses successful synthetic postings in dependency order and proves
cash zero/AP 2600 TRY/reconciliation afterward. Failed posting drafts remain
non-posting test audit evidence. Screenshots/logs are CI artifacts, not source.

For isolated local manual review, after migrations set `PREVIEW_SECRET` to a
random key of at least 24 characters and run
`python -m greenhouse_owner_os.preview`. Port defaults to 4173, bind to loopback.
Runtime refuses production/non-local database targets. Access uses an HttpOnly
SameSite Strict cookie, same-origin mutations, CSP and failed-login throttling.
Client actor IDs are never accepted. Retry keys survive reloads in sessionStorage
as request hashes/keys only; amounts/documents are not stored there.

`.github/workflows/owner-finance-preview.yml` opens an isolated HTTPS review
window when the MVP PR is opened/reopened, or through explicit dispatch/rerun.
It uses ephemeral PostgreSQL 17 and a pinned Cloudflare quick tunnel; no hosting
account, production secret or paid resource is provisioned. The private
`owner-finance-review-access` artifact contains the URL and random key (one-day
retention). Review lasts **35 minutes** after startup; afterward URL/data expire.
This is temporary test hosting, not durable hosting or production go-live.
`PREVIEW_PUBLIC_ORIGIN` is configured from the returned HTTPS URL; forwarded
headers cannot choose an origin or relax authentication. Browser-facing cookies
are Secure for that explicitly configured HTTPS origin. Only an external review
smoke counts as proof that the temporary URL is available.

Production identity, backups, durable hosting, multi-owner authorization and
deployment remain a separate authorized task. Large-history projection scaling
is outside this owner MVP; operation pages contain 50 rows across all currencies.
