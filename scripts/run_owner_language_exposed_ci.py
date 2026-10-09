"""Preserve original failures while gating the separately reviewed diagnostic.

This integrates existing scorers; it never changes their assertions or labels.
An integration PASS is deliberately not a raw benchmark aggregate PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.owner_language_benchmark import CORPUS, write_json
from scripts.report_owner_language_benchmark import build

SCORER_SHA = '09036c0d426dbfe7bdae6982a36673b9c9aa87f98d76e05ffca856266434242c'
CORPUS_SHA = '1321173a79c494ee1db7e824cc3798035de225d879cf4cb931b6a403f3c87249'
OVERLAY_SHA = 'c5b8e40eb5e944146527551819905cab2b57c02dd765088605d22315586d529b'
FIRST_SHA = '8fc37d3c65976ae0056383e670898791ff978157c3e2a3226d3543c0be1974ff'
KNOWN_AUDIENCE_FLAGS = ['GOLD-0235', 'GOLD-0236', 'GOLD-0237']
AUDIENCE_EXCLUSIONS = ['GOLD-0125', 'GOLD-0126', *KNOWN_AUDIENCE_FLAGS]


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_original(report, returncode):
    """Reject unknown failures rather than treating a nonzero run as success."""
    if report['corpus_sha256'] != CORPUS_SHA or report['harness_sha256'] != SCORER_SHA:
        raise ValueError('Original frozen corpus/scorer changed')
    if any(report[k] != 187 for k in ('corpus_case_count', 'evaluated_case_count')) or report['summary']['cases'] != 187 or report['partial_evaluation']:
        raise ValueError('Incomplete original evaluation')
    if report['errors'] or report['parity']['failures'] or report['parity']['cases'] != 187 or report['implementation']['source_changed_during_run']:
        raise ValueError('Original source stability, preparation or parity defect')
    expected = [dict(id=i, reasons=['unsafe_authoritative_route_or_duplicate']) for i in KNOWN_AUDIENCE_FLAGS]
    safety = report['safety_gate']
    if returncode != 1 or safety['passed'] or not safety['required_checks_complete'] or safety['case_failures'] != expected or safety['preparation_exceptions'] or safety['text_voice_mismatches']:
        raise ValueError('Unexpected original safety result; preserve and review it')


def validate_diagnostic(report, original, original_path):
    if report['status'] != 'EXPOSED_DIAGNOSTIC_NOT_UNSEEN' or report['base_corpus_sha256'] != CORPUS_SHA or report['base_scorer_sha256'] != SCORER_SHA:
        raise ValueError('Diagnostic provenance mismatch')
    if report['original_report_sha256'] != digest(original_path) or report['original_frozen_summary'] != original['summary'] or report['implementation'] != original['implementation']:
        raise ValueError('Diagnostic original report or implementation mismatch')
    if not report['source_and_oracle_stable'] or report['text_voice_parity'] != dict(cases=187, failures=[]):
        raise ValueError('Diagnostic source stability or parity defect')
    if report['original_case_count'] != 187 or report['audience_route_excluded_count'] != 5 or report['audience_route_excluded_case_ids'] != AUDIENCE_EXCLUSIONS or report['determinate_route_denominator'] != 182:
        raise ValueError('Diagnostic audience exclusions or denominator changed')
    if report['original_frozen_safety_case_ids'] != KNOWN_AUDIENCE_FLAGS or not report['diagnostic_safety_gate']['passed'] or report['diagnostic_safety_gate']['case_failures']:
        raise ValueError('Diagnostic substantive safety failure')
    if len(report['ambiguous_task_assessment']) != 5 or any(not r['excluded_from_audience_route_denominator'] or r['audience_classification'] != 'NOT_DETERMINABLE' for r in report['ambiguous_task_assessment']):
        raise ValueError('Excluded audience classification received correctness credit')


def run(command, logfile):
    with logfile.open('w') as stream:
        result = subprocess.run([sys.executable, *command], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=False)
    print(f'{logfile.name}: exit {result.returncode}', flush=True)
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', type=Path, default=ROOT/'artifacts/owner-language')
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for path, expected in [(CORPUS/'audience_diagnostic_overlay.json', OVERLAY_SHA), (CORPUS/'holdout.jsonl', CORPUS_SHA), (CORPUS/'first-unseen-report.json', FIRST_SHA), (ROOT/'scripts/owner_language_benchmark.py', SCORER_SHA)]:
        if digest(path) != expected:
            raise ValueError(f'Frozen evidence changed: {path.name}')
    raw_path = out/'exposed-original-report.json'
    raw_exit = run(['scripts/owner_language_benchmark.py', '--require-safety', '--corpus', str(CORPUS/'holdout.jsonl'), '--out', str(raw_path)], out/'exposed-original.log')
    raw = read(raw_path)
    validate_original(raw, raw_exit)
    diagnostic_path = out/'exposed-audience-diagnostic.json'
    diagnostic_exit = run(['scripts/diagnose_owner_language_audience.py', '--require-safety', '--corpus', str(CORPUS/'holdout.jsonl'), '--overlay', str(CORPUS/'audience_diagnostic_overlay.json'), '--original-report', str(raw_path), '--out', str(diagnostic_path)], out/'exposed-audience-diagnostic.log')
    if diagnostic_exit:
        raise ValueError('Separate audience diagnostic failed; raw result remains preserved')
    diagnostic = read(diagnostic_path)
    validate_diagnostic(diagnostic, raw, raw_path)
    first = read(CORPUS/'first-unseen-report.json')
    reports = {name: read(out/f'{name}-report.json') for name in ('train', 'semantic', 'noise')}
    for name, report in reports.items():
        if not report['safety_gate']['passed'] or report['implementation'] != raw['implementation']:
            raise ValueError(f'{name}: safety or candidate implementation mismatch')
    report = build(read(CORPUS/'manifest.json'), read(CORPUS/'taxonomy.json'), reports['train'], semantic=reports['semantic'], noise=reports['noise'], holdout=first, holdout_report_sha256=FIRST_SHA, exposed_regression=raw)
    report.update(raw_aggregate_status='FAIL', reviewed_diagnostic_integration_status='PASS', original_label_exit_code=raw_exit, separate_audience_diagnostic=diagnostic, strict_coverage_by_corpus={name: r['coverage_gate'] for name, r in reports.items()}, first_unseen_outcome='IMMUTABLE_FAIL_NOT_REPLACED', scope='Reviewed diagnostic integration only; raw original-label benchmark remains FAIL. Physical iPhone OWNER_PENDING.')
    write_json(out/'combined-report.json', report)
    print('Reviewed diagnostic integration PASS; raw benchmark aggregate FAIL; strict coverage gaps retained.')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('### Owner language evidence\n\nRaw original-label benchmark aggregate: **FAIL** (three preserved audience-label flags). Separate reviewed diagnostic integration: **PASS**. Five audience classifications excluded with no correctness credit. Strict coverage gaps and immutable first 17-failure result remain in artifacts; physical iPhone acceptance pending.\n')


if __name__ == '__main__':
    main()
