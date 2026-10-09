# Owner-language preparation benchmark

The benchmark invokes the real `OwnerCaptureService.prepare` used by Capture R2.
It does not contain a replacement parser. All inputs are curated, source-grounded
TEST language fixtures. They do not assert current production stock, money,
worker identity or completed operations. Detailed source evidence is owner-only.

## Dataset

- 805 evaluated GOLD cases: 618 training and 187 independent holdout
- 20 additional cases quarantined from evaluation after overlap review
- 5,200 semantic/combinatorial and 5,200 formatting/voice-filler variants
- 18 operational families and all current supported proposal types

`train.jsonl` contains the publication-minimized TEST cases. Case and semantic-group
IDs are native benchmark IDs. They do not encode external message identities.
`taxonomy.json` records current route boundaries. `manifest.json` contains counts,
projected artifact hashes, evaluation status and safety criteria.

The first independent evaluation is complete and its result is retained unchanged:
65.78% route accuracy, 42.25% labelled-field accuracy, 17 safety-failure cases and
65 strict assertion-failure cases. TEXT/VOICE parity and labelled source/remainder
preservation passed. These 187 cases were then released locally for remediation;
all later runs are exposed regression, never improved unseen performance.
The metadata-minimized original corpus is published as `holdout.jsonl` with owner approval.
Its original first report is retained byte-for-byte as `first-unseen-report.json`.
Generated variants derive only from training parents. No exposed case can be
relabeled as unseen. `manifest.json` retains the sealed first-report hash.

## Reproduce

Explicitly select this checkout; an editable installation may point elsewhere:

```sh
export PYTHONPATH="$PWD/src:$PWD"
python scripts/generate_owner_language_variants.py
python scripts/owner_language_benchmark.py --require-safety \
  --out artifacts/owner-language/train-report.json
python scripts/owner_language_benchmark.py --require-safety \
  --corpus artifacts/owner-language/semantic_variants.jsonl \
  --out artifacts/owner-language/semantic-report.json
python scripts/owner_language_benchmark.py --require-safety \
  --corpus artifacts/owner-language/noise_variants.jsonl \
  --out artifacts/owner-language/noise-report.json
pytest -q tests/test_owner_language_benchmark.py
pytest -q tests/integration/test_postgres_owner_language_benchmark.py
```

Integration tests need a disposable migrated PostgreSQL database. Independent
runners must not use the same database port concurrently.

Generated files are recreated atomically in ignored `artifacts/owner-language`.
Their hashes must match the manifest. `--update-manifest` is an explicit reviewed
maintenance operation, not a way to accept unexpected data changes.

## Validated transformations

Semantic variants change exact bounded fact tokens and their independently
labelled fields. Ambiguous, negated, corrected, partial and multi-slot cases are
excluded from semantic rewriting. Actual-event dates vary only within past/present;
only task dates may move into the future. Units, currency, scope and event count
must remain sound.

Noise changes filler prefixes, whitespace and case while preserving clauses,
numbers and negation. This is formatting/voice-filler simulation. It does not
measure arbitrary ASR spelling/deletion errors or physical audio recognition.

## Coverage and safety

`--strict` retains every route/field assertion and reports its real result. Safe
unresolved inputs and absent prefills remain coverage gaps. CI GREEN does not
mean 100% understanding.

The mandatory `--require-safety` gate has zero tolerance for:

- Preparation errors or incomplete TEXT/VOICE parity checks
- Wrong or duplicate unmatched authoritative slots
- Contradictory or filled-unknown facts
- Unassessed emitted critical facts
- Empty output, lost labelled constraints or lost unresolved remainder

Two separately labelled events with equal facts remain two events. Processing
retry is tested separately at the persisted PostgreSQL boundary.

Source date/TRY defaults are accepted only as explicitly marked inference with
independently permitted values. The approved Görgün sample quality candidates are
scoped to the expected current depot and unchanged original label, marked INFERENCE,
and still require owner quality review. Alacak-derived total candidates likewise
remain inference requiring arithmetic review. They never authorize payment or
fill missing receipt lines.

The scorer validates label paths against the actual DTO schema. Case assertions
for omitted explicit source fields may be strengthened through independent
pre-evaluation review; existing assertions are not relaxed to match predictions.

## Measurement limits

- Route accuracy compares expected semantic-slot multisets.
- Supported recall is separate from safely unresolved cases.
- Critical-field accuracy, ambiguity safety and factual contradictions are separate.
- Unresolved remainder must be visible in its own review slot; hiding it inside a
  primary description is not credited.
- Manual-input and clarification values from the corpus are explicitly proxies.
  Source-omitted required facts and source-supplied but unbound aliases are reported
  separately. Unsupported cases must not dilute supported-case averages.
- Actual browser measurements use completed-case denominators, with blocked and
  unresolved scenarios separate. Unmeasured families are not assigned zero taps.

Reports record imported implementation paths/hashes and reject unstable or partial
runs as full-corpus evidence. Before/after comparisons require the same corpus and
scoring definitions. The PostgreSQL companion verifies real preparation/reference
fidelity, retained source, TEXT/VOICE payload parity, retries and no business effect
before owner confirmation. Ten explicit common training regressions remain required.

Physical iPhone microphone, keyboard and touch acceptance remains OWNER PENDING.

The report assembler accepts the sealed first result with `--holdout` and later
same-case runs separately with `--exposed-regression`. It rejects a later report
presented as the first unseen result. Any independently justified post-evaluation
oracle errata must be documented separately and cannot replace the first score.

## Exact-SHA CI evidence

`run_owner_language_exposed_ci.py` retains the original-label nonzero result and
verifies its frozen scorer, corpus and first-report hashes. Its bounded integration
check accepts only the three already documented audience-label flags, and requires
the separate source-audited diagnostic to pass all safety, provenance, parity and
source-stability guards. New failures stop CI. The five unavailable audience
classifications receive no correctness credit. This never changes original labels
or converts the raw aggregate to PASS.

The current raw aggregate remains FAIL; the integration check is named separately.
Strict coverage remains separate from safety. The known local snapshot has
18/147/161 train/semantic/noise coverage failures and two diagnostic extra-remainder
gaps. Exact-SHA runs report actual values and browser measurements in their artifacts.

```sh
python scripts/run_owner_language_exposed_ci.py
```
