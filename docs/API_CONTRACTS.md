# API contracts — first finance vertical

Canonical machine-readable contract: `openapi/owner_core_v1.yaml`.

Implemented commands:

- `POST /v1/obligations` — create draft obligation; no financial effect.
- `POST /v1/obligations/{id}/post?expected_version=N` — post obligation + balanced posting atomically.
- `GET /v1/obligations/{id}` — return obligation with derived remaining amount.
- `POST /v1/payments` — create payment draft with proposed allocations; no financial effect.
- `POST /v1/payments/{id}/post?expected_version=N` — lock obligations, validate allocation, create advance remainder where needed, create balanced posting and post payment in one transaction.

All writes require `Idempotency-Key`. Future transports call these application use cases rather than duplicating finance rules.
