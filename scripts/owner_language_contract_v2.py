"""Reviewed audience contract, separate from the byte-frozen v1 evidence.

The v1 scorer remains the field/safety oracle. Audience changes are credited only
with an independently reviewed source-bound expectation and a useful task
candidate. An unchanged historical coverage gap is reported, never called PASS.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from scripts.owner_language_benchmark import ROOT, CORPUS, ident, score, safety_reasons, reference_fixture

V2 = ROOT / 'tests/benchmarks/owner-language-v2'
TASK_KINDS = {'PERSONAL_TASK', 'WORKER_TASK'}
SCORE_KEYS = ('route_ok', 'key_fields_ok', 'field_correct', 'field_total', 'unsafe',
              'invented', 'unassessed_critical_facts', 'remainder_ok',
              'source_preservation_ok', 'requested_prefill_gap')
CATEGORIES = {'irrigation', 'ventilation', 'harvest', 'cleanup', 'inspection',
              'fertilizer', 'repair', 'transport', 'general'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def score_contract(result):
    return {key: result[key] for key in SCORE_KEYS}


def payload_hash(slots):
    return canonical_hash([[kind, payload] for kind, payload, _ in slots])


def reviewed_worker_improvement(case, slots, baseline, entry):
    """Prove a reviewed worker prefill improves facts without changing any gap.

    Normalizing only the approved worker/language addition must recover the
    entire historical payload hash, not merely its aggregate score. Every
    remaining value, omission, sibling and source byte is therefore unchanged.
    """
    approval = entry.get('reviewed_field_improvement', {})
    if (approval.get('status') != 'INDEPENDENTLY_REVIEWED'
            or approval.get('kind') != 'existing_expected_worker_reference'
            or not approval.get('reviewer') or not approval.get('rationale')):
        return None
    index = approval.get('slot_index')
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(slots):
        return None
    expected = case['expected'][index]
    if expected['kind'] != 'WORKER_TASK' or slots[index][0] != 'WORKER_TASK':
        return None
    worker_label = expected.get('fields', {}).get('worker_id')
    if not isinstance(worker_label, dict) or set(worker_label) != {'ref'}:
        return None
    key = worker_label['ref']
    if key in case.get('omit_references', []) or key in case.get('inactive_references', []):
        return None
    workers = [row for row in reference_fixture()['sql']['workers.people']
               if row['id'] == ident(key) and row['active']
               and row['entity_id'] == case.get('entity_id', ident('entity'))]
    if len(workers) != 1:
        return None
    payload = slots[index][1]
    worker = workers[0]
    if payload.get('worker_id') != worker['id'] or payload.get('language') != worker['language']:
        return None
    normalized = deepcopy(slots)
    normalized[index][1]['worker_id'] = None
    normalized[index][1].pop('language', None)
    if payload_hash(normalized) != baseline['payload_sha256']:
        return None
    if score_contract(score(case, normalized)) != baseline['score']:
        return None
    return dict(slot_index=index, worker_id=worker['id'], language=worker['language'],
                proof='exact_historical_payload_hash_after_reviewed_worker_normalization',
                baseline_payload_sha256=baseline['payload_sha256'])


def verify_contract(contract, baseline):
    if contract['version'] != 'owner-task-audience-v2':
        raise ValueError('Unknown independently versioned contract')
    if contract['status'] != 'INDEPENDENTLY_REVIEWED':
        raise ValueError('Versioned expectations require independent review')
    review = contract.get('independent_review', {})
    if not review.get('reviewer') or not review.get('authority_message_id') or not review.get('rationale'):
        raise ValueError('Missing independent review provenance')
    for name, count in contract['case_counts'].items():
        if len(baseline[name]) != count:
            raise ValueError(f'{name}: baseline denominator changed')
    for ident_, entry in contract['reviewed_expectations'].items():
        if not entry.get('rationale') or not entry.get('source_sha256'):
            raise ValueError(f'{ident_}: unreviewed or unbound expectation')
        if entry.get('expected_kind') not in TASK_KINDS | {'UNRESOLVED', 'UNCHANGED'} and not entry.get('expected_task_kinds'):
            raise ValueError(f'{ident_}: unknown expected audience')


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate key in frozen baseline: ' + key)
        value[key] = item
    return value


def load_baseline(contract, directory=V2):
    manifest_path = directory/'baseline-manifest.json'
    if digest(manifest_path) != contract['baseline_manifest_sha256']:
        raise ValueError('Historical baseline manifest changed')
    manifest = json.loads(manifest_path.read_text(), object_pairs_hook=_unique_object)
    baseline = {}
    for name, shards in manifest['corpora'].items():
        values = {}
        for shard in shards:
            path = directory/shard['file']
            if digest(path) != shard['sha256']:
                raise ValueError('Historical baseline shard changed: ' + shard['file'])
            rows = json.loads(path.read_text(), object_pairs_hook=_unique_object)
            if len(rows) != shard['count'] or next(iter(rows)) != shard['first_id'] or next(reversed(rows)) != shard['last_id']:
                raise ValueError('Baseline shard ordering or denominator changed')
            if set(rows) & set(values):
                raise ValueError('Duplicate case across baseline shards')
            for id_, row in rows.items():
                score_ref = row.pop('score_ref')
                row['score'] = manifest['scores'][score_ref]
                values[id_] = row
        if len(values) != manifest['case_counts'][name] or len(values) != contract['case_counts'][name]:
            raise ValueError('Baseline corpus denominator changed')
        baseline[name] = values
    if canonical_hash(baseline) != manifest['canonical_content_sha256']:
        raise ValueError('Historical baseline canonical content changed')
    return baseline


def load_contract():
    contract = json.loads((V2/'contract.json').read_text())
    for name, expected in contract['frozen_v1_files'].items():
        if digest(ROOT/name) != expected:
            raise ValueError(f'Frozen v1 historical evidence changed: {name}')
    baseline = load_baseline(contract)
    verify_contract(contract, baseline)
    return contract, baseline


def validate_lineage(case, gold, entry):
    """Variants keep original labels and can inherit only frozen generation."""
    if hashlib.sha256(gold['text'].encode()).hexdigest() != entry['source_sha256']:
        raise ValueError('Reviewed source bytes do not match original gold')
    if case['id'] == gold['id'] and case['text'] != gold['text']:
        raise ValueError('Reviewed source bytes do not match original gold')
    if case['id'] != gold['id']:
        if case.get('parent_id') != gold['id']:
            raise ValueError('Unreviewed source lineage')
        prefix = {'semantic_variant': 'SEM-', 'noise_variant': 'NOISE-'}.get(case.get('tier'))
        generated_id = hashlib.sha256((gold['id'] + '|' + case['text']).encode()).hexdigest()[:18]
        if prefix is None or case['id'] != prefix + generated_id:
            raise ValueError('Generated source text/identity is not frozen lineage')


def audience_question(payload):
    """Inspect the actual review validator, not a question hardcoded by a test."""
    from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
    _, missing, _ = OwnerCaptureService(lambda: None)._validate(
        SimpleNamespace(session=None),
        dict(action_type='UNRESOLVED', entity_id=ident('entity')),
        payload)
    return missing


def validate_useful_task(case, slot, old_slot, expected_kind):
    kind, outer, evidence = slot
    failures = []
    from pydantic import ValidationError
    from greenhouse_owner_os.domain.owner_capture import DTO
    try:
        DTO[kind].model_validate(outer)
    except (ValidationError, KeyError):
        return ['invalid_task_dto_or_unexpected_authority_fields']
    if kind != expected_kind:
        return ['reviewed_audience_route_mismatch']
    ambiguous = expected_kind == 'UNRESOLVED'
    payload = outer.get('task_candidates') if ambiguous else outer
    provenance = evidence.get('task_candidates', {}) if ambiguous else evidence
    if not isinstance(payload, dict):
        return ['missing_structured_task_candidate']
    if ambiguous:
        if provenance.get('basis') != 'task_audience_requires_owner_choice':
            failures.append('missing_audience_uncertainty_provenance')
        provenance = provenance.get('fields', {})
        question = audience_question(outer)
        if question != ['Для себя или работнику?']:
            failures.append('missing_short_audience_question')
    original = old_slot['instruction']
    if payload.get('original_instruction') != original:
        failures.append('original_instruction_not_exactly_retained')
    if not original or original not in case['text']:
        failures.append('instruction_not_bound_to_source')
    if payload.get('title') != original[:200]:
        failures.append('task_title_does_not_retain_instruction')
    if payload.get('category') not in CATEGORIES:
        failures.append('missing_meaningful_task_category')
    if payload.get('category') != old_slot['category']:
        failures.append('unreviewed_task_category_change')
    source_provenance = provenance.get('original_instruction', {})
    if source_provenance.get('category') != 'SOURCE FACT' or source_provenance.get('source_text') != original:
        failures.append('instruction_source_provenance_lost')
    for key in ('worker_id', 'greenhouse_id', 'planned_date'):
        if key not in payload:
            failures.append('missing_explicit_unknown_' + key)
        if payload.get(key) is None and provenance.get(key, {}).get('category') != 'UNKNOWN':
            failures.append('unknown_claimed_as_fact_' + key)
    for key in ('worker_id', 'greenhouse_id', 'language', 'category'):
        if payload.get(key) is not None and provenance.get(key, {}).get('category') != 'INFERENCE':
            failures.append('reference_or_category_claimed_as_source_fact_' + key)
    if payload.get('planned_date') is not None and provenance.get('planned_date', {}).get('category') != 'SOURCE FACT':
        failures.append('source_date_provenance_lost')
    if expected_kind in {'UNRESOLVED', 'PERSONAL_TASK'} and (payload.get('worker_id') is not None or payload.get('language') is not None):
        failures.append('invented_or_personal_worker_assignment')
    expected_contract = 'R2_RECEIVED_REPORT' if expected_kind == 'WORKER_TASK' else None
    if payload.get('rendered_text') is not None or payload.get('execution_contract') != expected_contract:
        failures.append('unreviewed_translation_or_execution_contract')
    if expected_kind == 'WORKER_TASK' and provenance.get('execution_contract', {}).get('category') != 'CONTEXT DEFAULT':
        failures.append('execution_contract_not_labelled_context_default')
    return failures


def evaluate_case(case, slots, baseline, entry=None):
    """Return honest raw/v2 assessments and reject every unreviewed change."""
    raw = score(case, slots)
    record = dict(id=case['id'], parent_id=case.get('parent_id'), raw_v1=raw,
                  reviewed_expectation=entry, failures=[], historical_coverage_gap=False)
    projected = deepcopy(slots)
    task_slots = baseline.get('task_slots', [])
    changed = payload_hash(slots) != baseline['payload_sha256']
    record['payload_changed_from_baseline'] = changed
    if not entry or entry.get('expected_kind') == 'UNCHANGED':
        if changed:
            record['failures'].append('unreviewed_payload_or_route_change')
        record['versioned_score'] = raw
        record['failures'].extend(safety_reasons(raw))
    else:
        if len(slots) != baseline['slot_count']:
            record['failures'].append('slot_count_changed')
        for index, expected_hash in baseline.get('non_task_slot_hashes', {}).items():
            i = int(index)
            if i >= len(slots) or canonical_hash([slots[i][0], slots[i][1]]) != expected_hash:
                record['failures'].append('unrelated_sibling_payload_changed')
        expected = entry.get('expected_task_kinds') or [entry['expected_kind']] * len(task_slots)
        if len(expected) != len(task_slots):
            record['failures'].append('reviewed_task_slot_count_mismatch')
        for old_slot, want in zip(task_slots, expected):
            i = old_slot['index']
            if i >= len(slots):
                record['failures'].append('task_slot_lost')
                continue
            record['failures'].extend(validate_useful_task(case, slots[i], old_slot, want))
            kind, payload, provenance = slots[i]
            if kind == 'UNRESOLVED' and isinstance(payload.get('task_candidates'), dict):
                projected[i] = (old_slot['kind'], payload['task_candidates'],
                                provenance.get('task_candidates', {}).get('fields', {}))
            elif kind in TASK_KINDS:
                projected[i] = (old_slot['kind'], payload, provenance)
        # Retain v1 field, unknown, preservation, critical-fact and sibling-slot
        # checks. Projection is only for facts: explicit route is checked above.
        versioned = score(case, projected)
        # The three v1 WORKER-only labels conflict with the owner's rule; their
        # baseline happened to be PERSONAL. Project to the original label only
        # for v1's already frozen field checks, never for v2 route correctness.
        for old_slot in task_slots:
            i = old_slot['index']
            if i < len(projected) and i < len(case['expected']) and case['expected'][i]['kind'] in TASK_KINDS:
                projected[i] = (case['expected'][i]['kind'], projected[i][1], projected[i][2])
        versioned = score(case, projected)
        record['versioned_score'] = versioned
        if not task_slots:
            # Existing unsupported cases cannot get new correctness credit.
            if changed:
                record['failures'].append('no_reviewed_extracted_task_to_reclassify')
        if changed or task_slots:
            record['failures'].extend(safety_reasons(versioned))
            if not versioned['route_ok'] or not versioned['key_fields_ok']:
                old = baseline['score']
                if score_contract(versioned) != old:
                    improvement = reviewed_worker_improvement(case, slots, baseline, entry)
                    if improvement and versioned['field_correct'] > old['field_correct']:
                        record['reviewed_field_improvement'] = improvement
                    else:
                        record['failures'].append('new_or_changed_field_or_coverage_failure')
    v2 = record['versioned_score']
    old = baseline['score']
    if not record['failures'] and (not v2['route_ok'] or not v2['key_fields_ok']):
        record['historical_coverage_gap'] = True
    if not entry and score_contract(raw) != old:
        record['failures'].append('unreviewed_raw_score_change')
    record['failures'] = sorted(set(record['failures']))
    record['strict_passed'] = not record['failures'] and not record['historical_coverage_gap']
    return record
