# Owner-language audience contract v2

This is a separately reviewed contract implementing the owner's explicit
2026-10-12 20:54:54 UTC audience instruction. It does not edit, replace, or
reinterpret any file in `../owner-language/` or its original scorer, diagnostic,
runner, generator, reporting code, or immutable first-unseen FAIL.

## What is evaluated

- All 618 original training cases, 187 exposed holdout cases, 5,200 deterministic
  semantic variants, and 5,200 deterministic noise variants: 11,205 total.
- Zero audience denominator exclusions. Original examples/labels stay intact.
- Independent source/context review covers all 208 original task cases and the
  unrelated comparison case GOLD-0228. Each reviewed entry records original
  text, SHA256, expected audience and rationale; generated cases inherit only
  the immutable generator's bounded, verified transformations.
- Bare or otherwise audience-ambiguous instructions need a structured task
  candidate, exact original instruction, source-bound title, meaningful category,
  all known facts, explicit unknowns, honest provenance and the actual review-validator
  question `Для себя или работнику?`. Generic UNRESOLVED is not success.
- Explicit personal intent remains PERSONAL_TASK. Explicit worker intent remains
  WORKER_TASK even without a resolved worker; it is a proposal, not assignment.
- Frozen v1 field/unknown/preservation/safety checks run on task facts. Audience
  route is independently checked against v2 before this field-only projection.
  Money/stock/harvest slots cannot borrow task exceptions. Non-task sibling
  payloads remain byte-equivalent to their baseline canonical representation.

## Historical baseline

The baseline is the real preparation output at
`7c323f13e35477a355f00853756e38b5d3f45561`, using the frozen reference fixture and
all original cases. It was extracted in an isolated source snapshot. The
baseline-manifest declares counts, ordered shard boundaries, per-shard SHA256,
a deduplicated dictionary of original score records, and a SHA256 of the
canonical reconstituted full content. Every case keeps its payload hash, score,
slot count, exact task instruction/category and non-task sibling hashes.
Shards are at most 60 KB for review/publication tooling; no case was sampled or
removed to reduce size. The loader rejects changed bytes, duplicate IDs,
changed boundaries, missing cases and canonical-content mismatches.

Historical unchanged coverage gaps remain named gaps; they never receive strict
correctness credit. No historical substantive safety error is waived. New or
changed failures, unreviewed payload/route changes, unsupported task substitution,
source mutation, incomplete evaluation, and text/voice mismatch fail integration.

A separately reviewed field-improvement annotation may admit a source-backed
worker prefill only when the worker equals the original expected reference and
the language equals its current active, same-entity reference. Resetting only
that worker to null and removing the added language must reproduce the entire
historical payload hash and frozen score. This proves every other field and
remaining omission unchanged; an improved count alone cannot waive a failure.
The remaining historical gap still fails strict coverage. GOLD-0769 retains
its original expected Ozvadi greenhouse even while its supported worker prefill
improves; no original example or expectation is rewritten.

## Running

`python scripts/run_owner_language_contract_v2.py`

The wrapper first regenerates and verifies all frozen variant bytes. It actually
executes the untouched v1 scorer on all four corpora and the untouched v1
post-evaluation diagnostic, preserving their reports and real exit codes. It then
runs v2's independent route and useful-candidate checks on all cases, with
text/voice parity, source/oracle fingerprints, and baseline-bound non-regression.
The fingerprint includes pyproject.toml. Exact installed pymorphy3, Russian
dictionary, dawg2-python and setuptools versions must match declared pins; all installed
package/data bytes and package RECORD hashes are compared before and after.
The helper must use that installed dictionary data path explicitly, regardless
of dictionary-path environment overrides.

Default reports are written only to `artifacts/owner-language/v2/`. They include
all per-case assessments and changed outputs, raw v1 safety/strict coverage,
unchanged gap IDs, original diagnostic, and the immutable first FAIL. Statuses
are separate: raw-v1 aggregate, reviewed-v2 non-regression integration, and
strict-v2 contract coverage. Integration PASS must not be described as aggregate
benchmark PASS, unseen success, or physical iPhone acceptance.

`--review-draft` permits recording evidence for an unaccepted policy but always
keeps that run's integration status FAIL. It does not approve expectations.

SQL persistence/concurrency/recovery, browser behavior, and historical personal
task compatibility have separate mandatory suites. This read-only preparation
benchmark does not claim to prove those effects.
