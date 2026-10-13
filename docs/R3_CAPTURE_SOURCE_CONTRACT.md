# R3 capture friction correction: typed owner form originals

Status: bounded implementation candidate; owner iPhone acceptance PENDING.
Base: PR #21, 296833f0ed4eca252dcbbda3bff4dc9797423d9d. No merge or production authorization.

## Existing defect and narrow decision

The connected Harvest and Inventory callers required a duplicate narrative despite already knowing the owner-entered fields. Merely removing that requirement would lose lexical source values. The owner explicitly authorizes structured form entry as original evidence. This is a source-representation clarification, not new accounting semantics.

`structured_original` is a versioned typed envelope: schema_version=1, form_kind, stable form_id, offset-bearing captured_at, timezone, and the exact bounded set of string fields. Empty optional input remains an empty string in the original and nullable UNKNOWN in the typed command. Source time is separate from server receipt/creation time and event date. A current-date UI candidate is displayed in the form and included only as the owner's submitted form value; it is never represented as dictated speech.

Field harvest uses source `owner_form:v1:field_harvest` and source_reference=form_id. Inventory receipt uses source `owner_form:v1:inventory_receipt` and locator=form_id. Initial typed commands must match their original snapshot after only existing exact decimal/date/UUID parsing. Different lexical snapshots under one identity conflict, including with a fresh request key. Files and optional notes remain distinct evidence. The receipt comment remains solely in the immutable structured source; its accounting payload notes stay null, preserving the existing physical-duplicate comparison and database confirmation gate. Corrections retain the original; a corrected interpretation never overwrites it.

The existing immutable Harvest evidence row has no JSON column. A bounded storage adapter serializes the validated envelope into its raw_text column with the reserved `GREENHOUSE_OWNER_FORM_V1` prefix and dedicated namespace. The API exposes `structured_original` and raw_text=null for this source kind. It does not decode JSON-looking legacy prose. Inventory retains the same typed envelope inside its existing immutable evidence JSON. No migration or historical row rewrite is needed. The raw storage encoding is not a spoken original and must not be shown as one.

## Expense shorthand and authority

Only Capture's explicit quick_expense context enables the bounded `8500 насос ремонт` / `8500 ремонт насоса` grammar. It proposes the exact amount, normalized purpose, visible editable TRY and current Istanbul date. It does not infer account, payment, debt or advance. Source text remains exact. The account and actual paid-expense decision must be explicitly reviewed. Changes to critical facts invalidate a previous paid decision; stale version/digest confirmation remains rejected. Unsupported or ambiguous expressions stay in the existing preparation/recovery path. No general-language or AI-provider claim is made.

## Scope and compatibility

Physical receipt changes only permitted Inventory quantity after owner confirmation, with cost and physical opening UNKNOWN. Field report does not create depot kilograms, sale, payment or automatic link. Existing authority, source locking, idempotency, SQL immutability and audit are retained. Legacy evidence remains readable. Existing explicit-paid text routes retain their behavior.

The new browser checks compare the same three owner cases on the immutable delivered baseline and this candidate. A separate guarded diagnostic adds an explicitly synthetic TEST İmamlı reference to baseline TEST data without changing baseline application files. No real greenhouse data or topology is imported.

Canonical ADR/Data Dictionary writeback, if needed, is a separate factual technical clarification reviewed by the coordinating owner-facing task; this document does not claim a new accepted product policy.

## Capture Daily Completion extension (2026-10-12)

This section describes the bounded implementation based on merged PR #27,
`9a981766f217fe33364f32b601bfc2a4468b4f59`; the earlier base/status above is
historical. Exact execution evidence and release status belong to the delivery
record and applicable CI, not an assumed PASS in this contract.

### Existing recognition and immutable layers

Explicit depot and supplier-document contexts now use the same local
Poppler/Tesseract recognizer as generic file capture. Source bytes and the owner's
separate caption are saved before recognition. Context identifies the intended
editor; it is not evidence of payment, amount, supplier, quality or field linkage.
OCR page/line evidence and engine version remain derivatives of the immutable
original. Unchanged facts retain their extraction provenance when the owner
reviews another fact. An older edited revision displays the extraction bound to
its history rather than a newer unaccepted retry. Legacy manual-context captions
are labelled not recognized, never retroactively treated as OCR.

A failed recognition keeps the existing context slots and bounded preparation
job lifecycle. Successful retry can enrich only untouched automatic slots under
source/lease fences. Any owner action prevents automatic replacement of that
slot's interpretation. Manual correction stays linked to the same source and
proposal family. Document warnings, contradictory captions and unreadable rows
require explicit original review; omission or null cannot remove a recorded
review requirement. Quality acknowledgement is specific to the selected depot
and reviewed label/category pairs.

Document-level ADET may be retained in the proposal and approved review snapshot.
It is not allocated to quality lines and does not make unknown authoritative
line counts known. Field branches, document ADET and depot kilograms remain
independent. The Harvest source-linked evidence record deliberately references
Inbox originals instead of duplicating their binary payload.

### Deferred invoice intent

A supplier source associated with an existing physical receipt can be saved
without selecting its accounting treatment first. Preparation retains known
facts. A newly prepared payable remains blocked while its invoice intent is
undecided; removing the field does not erase that requirement. Selecting an
already recorded debt explicitly rejects an unposted new-payable proposal on
the server before linking the existing obligation. A posted sibling cannot be
hidden or rejected by this choice. Invoice linkage never repeats the physical
receipt, creates payment or silently reprices inventory.

### Bounded ambiguous-money recovery and migration 0033

A narrow nominal money source can retain amount, party and purpose without
inventing paid status. The owner explicitly chooses an operating payable, paid
expense or payment against an existing debt. The server recomputes eligibility
and known candidates from the immutable source, not editable request candidates.
Plan, negation, third-party statements and unsupported ambiguity do not become
actual transactions through this path. The resulting typed proposal still needs
its existing critical facts and individual owner confirmation.

`POST /entities/{entity_id}/owner/captures/{item_id}/money-recovery` accepts only
an origin proposal, expected version, explicit supported intent and optional
reason. It creates no accounting effect. Stable source/origin identity and the
existing recovery-family uniqueness, locking, audit and finality gates prevent
competing intent choices from creating independently postable siblings. Existing
source-linked DirectExpense recovery remains supported; null-origin general
money recovery is not introduced.

Migration `20261012_0033` is necessary because existing proposal action types are
immutable and the installed 0025 recovery guard only permits DirectExpense.
It extends only the accepted typed recovery slots/targets in the existing
forward-only guard functions, failing closed on unexpected prior definitions.
It creates no parallel ledger or business aggregate and rewrites no applied
migration, original or historical instruction. Clean installation and populated
0032-to-0033 upgrade, including old confirmed replay, are release gates.

This is a technical extension of accepted owner authority and recovery, not a
new valuation, cost-classification or period policy. D01–D06 remain open;
physical iPhone acceptance and production deployment are separate decisions.

### Explicit task audience and source-linked recovery (owner decision 2026-10-12)

The owner instruction `Sentinel_23ec5a187ea481919e0b1ac30a8ad153` clarifies
ADR-030/031/038/039: detecting an action does not establish its recipient.
An explicit worker instruction prepares a worker task; a missing executor stays
missing and is requested in review. Explicit personal intent prepares a personal
task. Ambiguous audience retains meaningful task candidates in UNRESOLVED with a
short owner/worker question. Task numbering and an agricultural subject alone
are not audience evidence. A negative instruction is not inverted into a
positive task. No preparation, audience selection or reference suggestion
assigns or dispatches work.

A greenhouse access grant is not a task assignment. A fertilizer-specific default
is not an ordinary-task assignment. A default recipient may be shown only where
an applicable, current, same-entity confirmed reference contract supports it;
its provenance must identify the contextual basis rather than claim source fact.
Ambiguous, inactive, foreign or multiple possible recipients are not silently
resolved. Existing confirmed personal tasks and their originals are unchanged.

The additive `task_candidates` envelope retains the original instruction and
prepared known fields with field provenance. The owner selects an audience via
`POST /entities/{entity_id}/owner/captures/{item_id}/task-recovery`, supplying
origin proposal, expected version, audience `personal` or `worker` and an
optional reason. This creates a source-linked ordinary review, not a task effect.
Existing owner review/confirmation and worker assignment/translation gates still
apply. Old unconfirmed task proposals may be corrected explicitly; no automatic
reclassification or historical reparse is performed. Confirmed effects cannot
be reclassified by this endpoint.

Migration `20261012_0035` extends the existing recovery-family guards to typed
task targets. Prior migrations, sources and instructions remain immutable.
Source/origin locks, expected versions, recovery-family uniqueness and atomic
confirmation prevent different HTTP keys or concurrent choices from producing
independently postable duplicate tasks. Original classifications and owner
choice remain separate audit evidence. Clean install, populated upgrade and
cached replay are required gates; this paragraph is a contract, not a PASS claim.

The frozen v1 benchmark and reports remain historical evidence. Its unconditional
WORKER_TASK labels for isolated GOLD-0235/0236/0237 are not business authority:
those excerpts do not identify an audience. Their original text must remain in
a separately versioned, independently reviewed oracle that requires useful
retained task meaning plus clarification. Generic UNRESOLVED output cannot count
as correct preparation. Every changed expectation needs its source/owner-rule
basis, and unrelated financial, stock or harvest failures remain failures.
