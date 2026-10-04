# API contracts — first finance vertical

Canonical machine-readable contract: `openapi/owner_core_v1.yaml`.

Implemented commands:

- `POST /v1/obligations` — create draft obligation; no financial effect.
- `POST /v1/obligations/{id}/post?expected_version=N` — post obligation + balanced posting atomically.
- `GET /v1/obligations/{id}` — return obligation with derived remaining amount.
- `POST /v1/payments` — create payment draft with proposed allocations; no financial effect.
- `POST /v1/payments/{id}/post?expected_version=N` — lock obligations, validate allocation, create advance remainder where needed, create balanced posting and post payment in one transaction.

All writes require `Idempotency-Key`. Future transports call these application use cases rather than duplicating finance rules.

Integrity/corrections:

- `POST /v1/payments/{id}/reverse` and `POST /v1/obligations/{id}/reverse` accept `{expected_version, event_date, reason}`. Idempotent replays return the saved result; another key for an already reversed record returns 409. Event date must not precede the original. Active allocated payments must be reversed before an obligation.
- `GET /v1/payments/{id}/linkage` and `GET /v1/obligations/{id}/linkage` expose original posting, reversal posting, actor/timestamp/reason and optional replacement links.
- Draft creation accepts optional `replacement_of_id`. Original must already be reversed; exactly one replacement of same type/entity/counterparty/currency/direction is permitted. A corrected replacement can itself be reversed/replaced to form an explicit chain.
- `GET /v1/entities/{entity_id}/balances` returns Decimal strings under currency keys for cash/payable/receivable/advance_paid/advance_received. No combined total, FX rate or conversion is inferred.

0003 is forward-only: recover using a verified pre-upgrade backup in a separate database, retaining/reconciling subsequent immutable journal history before cutover. In-place downgrade must not drop used correction history.

Cash & Settlements (FIN-008–FIN-012): `/v1/transfers`, `/v1/owner-fundings`,
`/v1/supplier-advances`, `/v1/direct-settlements` create drafts; `/{id}/post`
uses expected_version and Idempotency-Key; `/{id}/reverse` uses ReverseCommand.
Actor comes from the existing injected actor provider. New cash requests require
exact Decimal strings (floats/sub-cent/nonfinite/overflow rejected), event_date,
entity and currency, with optional source/reason. Supplier advance IDs are payment
IDs and share payment post/reversal idempotency scopes. They persist an unallocated
outgoing payment and explicit paid advance; no automatic expense or payable.
Transfers persist source/destination links and optional explicit fee/expense account.
Owner funding requires an entity/currency OWNER_FUNDING account of class owner_funding.
Fees require an explicit configured expense account. Reference-account provisioning
is required before posting; the service does not guess categories or create accounts.
Direct settlements link one posted receivable and one posted payable, do not use a
money account, and reduce both remaining balances. Cash journal balances may be
negative under the existing model; this slice adds no overdraft/opening-balance policy.
Advance application is a subsequent non-cash workflow; this slice retains its explicit
source/counterparty/currency/value foundation without inventing procurement rules.
