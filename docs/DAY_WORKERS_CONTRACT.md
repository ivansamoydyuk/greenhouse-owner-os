# DayWorkers / Daily Control contract

Additive extension after migration 0033. Existing Worker tasks, Finance accounting and Harvest evidence remain authoritative. No production deployment or data change is implied.

## Scope and originals

Owner authorization is `core.r2_owner(entity_id,actor_id)`. Worker identity is trusted provider `(entity_id,worker_id,actor_id)` and active worker reference. New owner-only append-only greenhouse scope GRANT/REVOKE controls requests, reports and reads. No task/name inference. No scopes are automatically installed by migrations.

Each write carries `source` using the existing typed form envelope: `{schema_version:1,form_kind,form_id,captured_at,timezone:'Europe/Istanbul',fields:{...}}`. All fields below are strings and exact lexical originals; optional values are empty strings. Actor/entity are server-bound. Originals are stored in existing immutable `workers.sources`; no generated narrative replaces them. Stable form identity survives retries with different HTTP Idempotency-Key values, and changed identity content is a conflict. Request version is 1 + decision and clarification count. Every owner decision and worker clarification requires expected_version.

## Endpoints and form fields

Owner base `/v1/entities/{entity_id}/day-workers`; worker base `/v1/worker/day-workers`. GET base provides greenhouses, requests, actuals, and owner-only references.

- POST `/requests`: `{greenhouse_id,source}`; `day_request` fields people, days, work, start_date, note. The last two are optional empty strings; never silently default date. Only three user-entered request fields; greenhouse comes from selected permitted context. Owner can create a plan as well.
- Owner POST `/requests/{id}/decisions`: `{expected_version,source}`; `day_decision` fields decision, people, days, work, start_date, note, reason. Decision APPROVE uses original plan; REVISE_APPROVE explicitly changes plan; REJECT and CLARIFY require a short reason. Decision history is immutable. Approval never posts money.
- Worker POST `/requests/{id}/clarifications`: `{expected_version,source}`; `day_clarification` field text. Only the original requester, still in scope, may respond to a current CLARIFY. Returns to owner review without erasing the question.
- POST `/actuals`: `{greenhouse_id:null|uuid,request_id:null|uuid,replaces_id:null|uuid,source}`; `day_actual` fields event_date, people, work, full_day, note, allocations. The top greenhouse is reporting-access context, never an automatic allocation. Each actual is ONE dated brigade/source event with total people. `allocations` is an exact JSON-string array of `{greenhouse_id,work_area_id:null|uuid,people:integer}` explicit full-day headcount rows. Known part IDs must belong to their root; every worker root must be explicitly permitted. Row totals cannot exceed the brigade total. A one-greenhouse form submits one explicit row; unknown distribution submits `[]`, preserving the unknown remainder without equal split or requester attribution. full_day is true/false/unknown. Partial/unknown rows do not become full-day people-days. Source identity binds the whole report, allocations and request/replacement context; a date is not a unique event identity.
- Owner POST `/actuals/{id}/confirmations`: `{confirmed:true,source}`; `day_confirmation` field note. Explicit factual confirmation is independent of known rate/creditor and never posts Finance. Replacements are append-only and allowed only before accrual; they require a new confirmation. A prior accrued fact cannot be replaced through this capability, including after generic Finance reversal.
- Owner POST `/actuals/{id}/accruals`: `{confirmed:true,source}`; `day_accrual` fields counterparty_id, daily_rate, currency, expense_account_id, rate_basis, due_date. Existing known creditor, exact positive explicit rate for the actual date, currency, existing expense ledger account, provenance and explicit full-day fact required. Complete allocation must equal actual total. One explicit review atomically confirms the fact if needed and creates/posts ONE OPERATIONAL Finance payable for the entire brigade/day; each attribution equals allocated people × rate. No payment/direct expense is created. Due date may be unknown. Same actual cannot accrue twice, including concurrent requests; all confirmation/source/ledger writes roll back together on failure. Later Finance payments reduce this same debt separately. Generic Finance reversal is shown as FINANCIAL_REVIEW_REQUIRED with current obligation status and remaining amount, never silently reaccrued.
- Owner POST `/scopes`: `{source}`; `day_scope` fields worker_id, greenhouse_id, action (GRANT/REVOKE), reason.

Reads contain request `id`, `version`, `state` (PENDING/APPROVED/REJECTED/CLARIFICATION_REQUIRED), `greenhouse_id`, `worker_id`, `original` form, `current_plan:{people,days,work}`, `planned_people_days`, `actual_people_days`, decisions, clarifications, actuals. Owner actuals include `accrual` and exact Finance link; worker actuals omit rates, creditors and financial effects. Owner-only accrual read includes its immutable original, known creditor, current obligation status and remaining debt. Partial/unknown-day and unallocated reports have explicit coverage IDs. Counts are full-day people-days only, never unique people. Plan5×2=10, actual dated4+3=7, accrued actual uses7 only.

## Daily cockpit integration

`DayWorkersService.overview(entity_id,actor_id,worker_id=None)` is the common source-backed read. `summary(entity_id,actor_id,date_from,date_to,greenhouse_id=None)` provides same-source per-greenhouse reported/confirmed people-days, request plan totals and actual/obligation IDs for the read-only cockpit. FinanceAnalyticsService provides actual cost and debt/payment effects; HarvestJourneyService provides exact/estimated/unknown source-backed seasonal metrics and quality provenance. Cash and all-date entity debts are not relabelled greenhouse-scoped.

Unknown rate/creditor/full-day stays unposted. No default2500, historical hours/day, payroll identities, invented classifications, automatic payment, new D04/D05 exception, changed season or quality mapping.

Undated request/plan totals remain explicitly undated in each greenhouse bucket. Global unresolved coverage is labeled ENTITY_ALL_GREENHOUSES even when a selected greenhouse filters buckets; it is never silently treated as selected scope.

## Integrity hardening

All admission paths require explicit ISO calendar dates `YYYY-MM-DD` (or empty optional start/due dates); database-relative words, infinite dates and alternate lexical forms are not business evidence. The source-trigger shares the service's entity lock, and worker replacements revalidate both the old report's reporting context and every old allocation scope, even when the new report points to a still-permitted greenhouse.

An explicit zero headcount records known no-work and zero people-days independently of the unknown/partial day-basis label. It cannot accrue money. This does not convert positive partial-day attendance into full-day units. Undated attention IDs include pending, clarification-required and approved plans; rejected requests remain source history, not attention items. Historical approvals remain visible but stop contributing active planned people-days after rejection or clarification.
