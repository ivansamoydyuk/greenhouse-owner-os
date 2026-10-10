# R3 capture friction correction: typed owner form originals

Status: bounded implementation candidate; owner iPhone acceptance PENDING.
Base: PR #21, 296833f0ed4eca252dcbbda3bff4dc9797423d9d. No merge or production authorization.

## Existing defect and narrow decision

The connected Harvest and Inventory callers required a duplicate narrative despite already knowing the owner-entered fields. Merely removing that requirement would lose lexical source values. The owner explicitly authorizes structured form entry as original evidence. This is a source-representation clarification, not new accounting semantics.

`structured_original` is a versioned typed envelope: schema_version=1, form_kind, stable form_id, offset-bearing captured_at, timezone, and the exact bounded set of string fields. Empty optional input remains an empty string in the original and nullable UNKNOWN in the typed command. Source time is separate from server receipt/creation time and event date. A current-date UI candidate is displayed in the form and included only as the owner's submitted form value; it is never represented as dictated speech.

Field harvest uses source `owner_form:v1:field_harvest` and source_reference=form_id. Inventory receipt uses source `owner_form:v1:inventory_receipt` and locator=form_id. Initial typed commands must match their original snapshot after only existing exact decimal/date/UUID parsing. Different lexical snapshots under one identity conflict, including with a fresh request key. Files and optional notes remain distinct evidence. Corrections retain the original; a corrected interpretation never overwrites it.

The existing immutable Harvest evidence row has no JSON column. A bounded storage adapter serializes the validated envelope into its raw_text column with the reserved `GREENHOUSE_OWNER_FORM_V1` prefix and dedicated namespace. The API exposes `structured_original` and raw_text=null for this source kind. It does not decode JSON-looking legacy prose. Inventory retains the same typed envelope inside its existing immutable evidence JSON. No migration or historical row rewrite is needed. The raw storage encoding is not a spoken original and must not be shown as one.

## Expense shorthand and authority

Only Capture's explicit quick_expense context enables the bounded `8500 насос ремонт` / `8500 ремонт насоса` grammar. It proposes the exact amount, normalized purpose, visible editable TRY and current Istanbul date. It does not infer account, payment, debt or advance. Source text remains exact. The account and actual paid-expense decision must be explicitly reviewed. Changes to critical facts invalidate a previous paid decision; stale version/digest confirmation remains rejected. Unsupported or ambiguous expressions stay in the existing preparation/recovery path. No general-language or AI-provider claim is made.

## Scope and compatibility

Physical receipt changes only permitted Inventory quantity after owner confirmation, with cost and physical opening UNKNOWN. Field report does not create depot kilograms, sale, payment or automatic link. Existing authority, source locking, idempotency, SQL immutability and audit are retained. Legacy evidence remains readable. Existing explicit-paid text routes retain their behavior.

The new browser checks compare the same three owner cases on the immutable delivered baseline and this candidate. A separate guarded diagnostic adds an explicitly synthetic TEST İmamlı reference to baseline TEST data without changing baseline application files. No real greenhouse data or topology is imported.

Canonical ADR/Data Dictionary writeback, if needed, is a separate factual technical clarification reviewed by the coordinating owner-facing task; this document does not claim a new accepted product policy.
