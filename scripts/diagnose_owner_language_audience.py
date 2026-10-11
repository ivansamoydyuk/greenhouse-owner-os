"""Separate exposed diagnostic. Never changes the frozen original benchmark."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from scripts import owner_language_benchmark as original


def normalized(text):
    return ' '.join(str(text).casefold().split())


def text_values(value):
    if isinstance(value,str):
        yield value
    elif isinstance(value,dict):
        for item in value.values():
            yield from text_values(item)
    elif isinstance(value,list):
        for item in value:
            yield from text_values(item)


def assess_ambiguous_task(case,slots,overlay):
    """Assess common task facts and safety, explicitly not audience correctness."""
    allowed=set(overlay['diagnostic_task_kinds'])
    tasks=[slot for slot in slots if slot[0] in allowed]
    violations=[]
    if not slots:
        violations.append('empty_preparation_output')
    for kind,payload,_ in slots:
        if kind not in allowed|{'UNRESOLVED'}:
            violations.append('wrong_authoritative_route:'+kind)
        for field in overlay['required_unknown_fields']:
            if payload.get(field) not in (None,[], ''):
                violations.append('invented_'+field)
        # No addressee/target language/assignment was supplied in these excerpts.
        for field in ('assignment_id','assigned_at','issued_at','confirmed_at','worker_assignment_id'):
            if payload.get(field) not in (None,[], ''):
                violations.append('invented_'+field)
        if payload.get('status') not in (None,'DRAFT','UNRESOLVED'):
            violations.append('authoritative_task_status')
        if payload.get('language') not in (None,'ru'):
            violations.append('invented_target_language')
        if payload.get('rendered_text') not in (None,''):
            violations.append('invented_translation')
        for field in ('translation_reviewed','rendering_reviewed','owner_confirmed','assigned','confirmed'):
            if payload.get(field) not in (None,False):
                violations.append('invented_'+field)
    if len(tasks)>1:
        violations.append('duplicate_task_proposals')
    def action_retained(slot):
        kind,payload,_=slot
        instruction=payload.get('original_instruction')
        # A copied source in a note cannot legitimize a different proposed action.
        if instruction:
            return normalized(case['text']) in normalized(instruction)
        return kind=='PERSONAL_TASK' and normalized(case['text']) in normalized(payload.get('title',''))
    retained=all(action_retained(slot) for slot in tasks) if tasks else bool(slots) and any(normalized(case['text']) in normalized(value) for kind,payload,_ in slots if kind=='UNRESOLVED' for value in text_values(payload))
    if not retained:
        violations.append('source_instruction_lost')
    checks=[]
    # These field checks apply regardless of proposed owner/worker audience.
    if len(tasks)==1:
        payload=tasks[0][1]
        checks=[payload.get(k) in (None,[], '') for k in overlay['required_unknown_fields']]
        checks.append(retained)
    else:
        checks=[False]*(len(overlay['required_unknown_fields'])+1)
    return dict(id=case['id'],family=case['family'],audience_classification='NOT_DETERMINABLE',
        excluded_from_audience_route_denominator=True,task_family_retained=len(tasks)==1,
        field_correct=sum(checks),field_total=len(checks),source_instruction_retained=retained,
        safety_violations=sorted(set(violations)),coverage_gap=len(tasks)!=1 or not all(checks),
        safely_unresolved=bool(slots) and all(k=='UNRESOLVED' for k,_,_ in slots) and not violations)


def build_diagnostic(cases,slots_by_id,overlay):
    selected=set(overlay['case_ids'])
    if selected!={c['id'] for c in cases if c['id'] in selected}:
        raise ValueError('Diagnostic case IDs missing from the original corpus')
    if len(selected)!=overlay['route_denominator_exclusions']:
        raise ValueError('Diagnostic exclusion count mismatch')
    originals=[original.score(c,slots_by_id[c['id']]) for c in cases]
    determinate=[r for r in originals if r['id'] not in selected]
    ambiguous=[assess_ambiguous_task(c,slots_by_id[c['id']],overlay) for c in cases if c['id'] in selected]
    unsafe=[dict(id=r['id'],reasons=original.safety_reasons(r)) for r in determinate if original.safety_reasons(r)]
    unsafe.extend(dict(id=r['id'],reasons=r['safety_violations']) for r in ambiguous if r['safety_violations'])
    coverage=[r['id'] for r in determinate if not r['route_ok'] or not r['key_fields_ok'] or original.safety_reasons(r)]
    coverage.extend(r['id'] for r in ambiguous if r['coverage_gap'])
    groups=defaultdict(list)
    for r in determinate:groups[r['family']].append(r)
    return dict(status='EXPOSED_DIAGNOSTIC_NOT_UNSEEN',original_case_count=len(cases),
        audience_route_excluded_count=len(ambiguous),audience_route_excluded_case_ids=sorted(selected),
        determinate_route_denominator=len(determinate),determinate_route_numerator=sum(r['route_ok'] for r in determinate),
        determinate_metrics=original.summarize(determinate),determinate_families={k:original.summarize(v) for k,v in sorted(groups.items())},
        ambiguous_task_assessment=ambiguous,
        ambiguous_task_family_retained=sum(r['task_family_retained'] for r in ambiguous),
        ambiguous_field_correct=sum(r['field_correct'] for r in ambiguous),ambiguous_field_total=sum(r['field_total'] for r in ambiguous),
        original_frozen_summary=original.summarize(originals),
        original_frozen_safety_case_ids=[r['id'] for r in originals if original.safety_reasons(r)],
        original_frozen_strict_failure_case_ids=[r['id'] for r in originals if not r['route_ok'] or not r['key_fields_ok'] or original.safety_reasons(r)],
        diagnostic_safety_gate=dict(passed=not unsafe,case_failures=unsafe,threshold='zero on all determinate routes and all retained fact/source/assignment guards'),
        diagnostic_coverage_gate=dict(passed=not coverage,case_failures=coverage),
        limitation='Five audience classifications are unavailable from supplied excerpts; they are not credited as correct. Safe unresolved remains a task-family coverage gap. All later runs are exposed regression.')


def exit_code(report,*,strict=False,require_safety=False):
    if require_safety and not report['diagnostic_safety_gate']['passed']:return 1
    if strict and (not report['diagnostic_coverage_gate']['passed'] or not report['diagnostic_safety_gate']['passed']):return 1
    return 0


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--corpus',required=True,type=Path)
    ap.add_argument('--overlay',required=True,type=Path)
    ap.add_argument('--original-report',required=True,type=Path,help='Same-source exposed report under original frozen labels/scorer')
    ap.add_argument('--out',required=True,type=Path)
    ap.add_argument('--require-safety',action='store_true')
    ap.add_argument('--strict',action='store_true')
    a=ap.parse_args()
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    raw=a.corpus.read_bytes();overlay=json.loads(a.overlay.read_text());prior=json.loads(a.original_report.read_text())
    overlay_hash=digest(a.overlay);prior_hash=digest(a.original_report);diagnostic_hash=digest(Path(__file__))
    scorer=Path(original.__file__)
    if hashlib.sha256(raw).hexdigest()!=overlay['base_corpus_sha256'] or digest(scorer)!=overlay['base_scorer_sha256']:
        raise ValueError('Frozen original corpus/scorer mismatch')
    if prior['corpus_sha256']!=overlay['base_corpus_sha256'] or prior['harness_sha256']!=overlay['base_scorer_sha256']:
        raise ValueError('Original comparison report does not use frozen corpus/scorer')
    root=Path(next(iter(importlib.util.find_spec('greenhouse_owner_os').submodule_search_locations)))
    fingerprint=lambda:{str(p):digest(p) for p in sorted(root.glob('services/*.py'))}
    before=fingerprint()
    if before!=prior['implementation']['before_service_sha256']:
        raise ValueError('Original comparison report has a different implementation')
    cases=[json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if prior.get('partial_evaluation') or prior['summary']['cases']!=len(cases):
        raise ValueError('Partial original report')
    slots={};parity=[]
    for c in cases:
        original.validate_case(c)
        slots[c['id']]=original.prepare_case(c)
        if slots[c['id']]!=original.prepare_case(c,'voice_transcript'):parity.append(c['id'])
    report=build_diagnostic(cases,slots,overlay)
    if report['original_frozen_summary']!=prior['summary']:
        raise ValueError('Original frozen score did not reproduce; do not present diagnostic')
    stable=before==fingerprint() and digest(scorer)==overlay['base_scorer_sha256'] and digest(a.corpus)==overlay['base_corpus_sha256'] and digest(a.overlay)==overlay_hash and digest(a.original_report)==prior_hash and digest(Path(__file__))==diagnostic_hash
    report.update(original_report_sha256=prior_hash,overlay_sha256=overlay_hash,diagnostic_harness_sha256=diagnostic_hash,
        base_corpus_sha256=overlay['base_corpus_sha256'],base_scorer_sha256=overlay['base_scorer_sha256'],implementation=prior['implementation'],source_and_oracle_stable=stable,
        text_voice_parity=dict(cases=len(cases),failures=parity))
    if parity or not stable:report['diagnostic_safety_gate']['passed']=False
    original.write_json(a.out,report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('implementation','determinate_families','ambiguous_task_assessment')},ensure_ascii=False,indent=2))
    raise SystemExit(exit_code(report,strict=a.strict,require_safety=a.require_safety))


if __name__=='__main__':main()
