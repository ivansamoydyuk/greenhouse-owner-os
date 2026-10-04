# Greenhouse Owner OS — Architecture v2

Status: implementation baseline for Module 01.
Date: 2026-10-04.

## Architecture style

The system is a modular monolith with PostgreSQL as the authoritative structured datastore. Mobile, AI and future integrations are adapters; they do not write database tables directly. Business posting is deterministic application code inside database transactions.

The first release does not need microservices. Module boundaries are represented in code packages and PostgreSQL schemas, and can be split later only if operational evidence requires it.

## Domain boundaries

### `core`
Economic/legal entities, actors, counterparties, greenhouses, work areas and aliases. The implemented finance slice currently migrates only entities, actors and counterparties. Greenhouse/work-area tables will arrive with the harvest/operations slice rather than as unused schema.

### `finance`
Own money accounts, obligations, payments, many-to-many allocations, explicit advances and immutable balanced managerial postings. Later finance slices add advance application, reversal workflows, own-account transfers and direct depot-to-supplier settlements.

### `harvest` — planned R1 slice
Worker harvest reports, harvest batches, depot settlements/receipts, one quality line per original document line, receipt adjustments and reconciliation. Worker evidence and depot evidence remain independent.

### `docs` — planned R1 slice
Original source metadata, immutable file fingerprint/checksum, business document metadata and explicit links to business records.

### `ai` — planned R1 slice
Inbox items, immutable extraction versions and owner-reviewable draft commands. AI cannot post finance.

### `audit`
Append-only audit events and idempotency records. External writes are serialized by `(actor, operation, idempotency_key)` and protected with a transaction-scoped PostgreSQL advisory lock plus a unique constraint.

### `legacy` — planned migration slice
Staging/import batches, row-level provenance, parser/mapping version, confidence and verification state. It never becomes the runtime accounting source of truth.

## Finance aggregate model

```text
Counterparty
   ├─ Obligation (payable or receivable)
   │      └─ derived remaining = original - posted allocations (+ future adjustments)
   └─ Payment (actual own-account cash movement)
          ├─ PaymentAllocation * -> Obligation *
          └─ Advance (explicit remainder/prepayment)

Posted Obligation/Payment
          └─ FinancialPosting
                 └─ PostingEntry (debit/credit, immutable)
```

`financial_posting` is a managerial integrity journal, not statutory Turkish bookkeeping. Domain records carry business meaning; postings carry immutable accounting effect.

## Status model

Financial domain aggregates use:

`draft -> review -> posted -> reversed`

Only `posted` records affect finance. `reversed` is reached by a dedicated correcting/reversal workflow; posted business fields are immutable. A posted record is never hard-deleted or silently rewritten.

## Database invariants

1. Money is exact decimal: Python `Decimal`, PostgreSQL `NUMERIC(20,2)` for R1 money.
2. A posting contains at least two entries and must balance debit = credit.
3. Every posting entry account must belong to the posting entity and currency.
4. Posting rows, posting entries and audit events are append-only at database level.
5. One idempotency key is unique per actor + operation; concurrent retries are serialized.
6. Allocation amount is positive and one payment/obligation pair has at most one allocation row.
7. Allocation total cannot exceed payment amount.
8. A payment may allocate only to posted obligations of the same entity, counterparty and currency, with matching direction.
9. Posted allocations may not push an obligation below zero.
10. Overpayment/prepayment becomes an explicit `advance`, never a negative obligation.
11. Payment and referenced obligations are locked in a stable order while posting to prevent concurrent over-allocation.
12. Draft/review rows have no posting and no balance effect.

## Migration strategy

Executable schema changes are Alembic-only. Architecture does not pre-create dozens of unused production tables. Instead, target domain relationships are designed first and tables are introduced through small vertical migrations when the corresponding behavior is implemented and testable.

Current chain:

- `20261004_0001`: `core` + `audit` + balanced posting foundation.
- `20261004_0002`: obligation/payment/allocation/advance vertical + derived balance views.

Next migrations are blocked from production until these two have been executed and tested against a real PostgreSQL test database.

## API contract principles

- REST commands are business commands, not table CRUD.
- Every external write requires `Idempotency-Key`.
- Posting commands require optimistic `expected_version` and server-side validation.
- `409` is used for version/idempotency conflicts; `422` for deterministic business-rule rejection.
- Domain amounts are serialized as decimal strings in responses.
- AI/service credentials cannot invoke finance posting endpoints.
- Clients retry only with the same idempotency key after an ambiguous network failure.

## Repository structure

```text
src/greenhouse_owner_os/
  api/              # transport only
  domain/           # entities, value rules, commands, errors
  services/         # transactional use cases / posting orchestration
  repositories/     # repository protocols + SQLAlchemy + test memory adapters
  infrastructure/   # SQLAlchemy mappings/database wiring
alembic/versions/   # authoritative schema migrations
openapi/            # API contract
 tests/             # unit/API + PostgreSQL integration gates
```

Domain/service tests do not depend on Telegram, AI, Google Sheets or UI. PostgreSQL integration tests must use PostgreSQL; SQLite is deliberately not treated as proof for schemas, JSONB, deferred constraint triggers, row locking or advisory-lock behavior.
