"""Run every frozen v1 case truthfully and gate separately reviewed v2 semantics.

Never patch the original labels/scorer/diagnostic or require a particular raw
failure count. Original reports, immutable first FAIL, and remaining strict gaps
are retained separately from the reviewed non-regression integration decision.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.owner_language_benchmark import CORPUS, load, prepare_case, write_json
from scripts.owner_language_contract_v2 import (
    V2, digest, load_contract, load_baseline, evaluate_case, validate_lineage, verify_contract,
)
from scripts.report_owner_language_benchmark import build


def fingerprint():
    files = sorted((ROOT/'src').rglob('*.py')) + [
        ROOT/'scripts/owner_language_contract_v2.py', Path(__file__),
        ROOT/'pyproject.toml', V2/'contract.json', *sorted(V2.glob('baseline*.json')),
        *sorted((ROOT/'scripts').glob('*owner_language*.py'))]
    return {str(p.relative_to(ROOT)): digest(p) for p in files}


LEXICAL_DEPENDENCIES = ('pymorphy3', 'pymorphy3-dicts-ru', 'dawg2-python', 'setuptools')


def lexical_dependency_fingerprint(project_path=ROOT/'pyproject.toml'):
    """Bind dictionary-only recognition to exact declared/installed bytes."""
    declared = tomllib.loads(project_path.read_text())['project']['dependencies']
    result = {}
    for name in LEXICAL_DEPENDENCIES:
        pins = [value.split('==', 1)[1] for value in declared if value.startswith(name + '==')]
        if len(pins) != 1:
            raise ValueError(f'{name}: one exact semantic dependency pin is required')
        installed = metadata.distribution(name)
        if installed.version != pins[0]:
            raise ValueError(f'{name}: installed {installed.version} differs from declared exact pin {pins[0]}')
        prefixes = {'pymorphy3': ('pymorphy3/',), 'pymorphy3-dicts-ru': ('pymorphy3_dicts_ru/',),
                    'dawg2-python': ('dawg_python/',),
                    'setuptools': ('setuptools/', '_distutils_hack/', 'pkg_resources/')}[name]
        files = {str(path): digest(installed.locate_file(path))
                 for path in installed.files or ()
                 if str(path).startswith(prefixes) and not str(path).endswith('.pyc')
                 and '/__pycache__/' not in str(path)}
        if not files:
            raise ValueError(f'{name}: installed semantic package bytes unavailable')
        records = [path for path in installed.files or () if str(path).endswith('.dist-info/RECORD') and len(Path(str(path)).parts) == 2]
        if len(records) != 1:
            raise ValueError(f'{name}: installed package RECORD unavailable')
        result[name] = dict(declared_pin=pins[0], installed_version=installed.version,
            package_files_sha256=files, installed_record_sha256=digest(installed.locate_file(records[0])))
    from pymorphy3_dicts_ru import get_path
    from greenhouse_owner_os.services.task_audience_language import _dictionary
    declared_path = Path(metadata.distribution('pymorphy3-dicts-ru').locate_file('pymorphy3_dicts_ru/data')).resolve()
    package_path = Path(get_path()).resolve()
    used_path = Path(_dictionary().dictionary.path).resolve()
    if not declared_path == package_path == used_path:
        raise ValueError('Actual task-recipient dictionary path differs from pinned installed package')
    result['pymorphy3-dicts-ru'].update(installed_data_path=str(declared_path), helper_data_path=str(used_path))
    return result


def run(command, logfile):
    with logfile.open('w') as stream:
        result = subprocess.run([sys.executable, *command], cwd=ROOT, stdout=stream,
                                stderr=subprocess.STDOUT, check=False)
    print(f'{logfile.name}: exit {result.returncode}', flush=True)
    return result.returncode


def validate_raw(report, exit_code, path, contract, name):
    count = contract['case_counts'][name]
    if report['corpus_sha256'] != digest(path):
        raise ValueError(f'{name}: raw report source hash mismatch')
    if report['harness_sha256'] != contract['frozen_v1_files']['scripts/owner_language_benchmark.py']:
        raise ValueError('Raw report no longer uses frozen v1 scorer')
    if report['summary']['cases'] != count or report['corpus_case_count'] != count or report['evaluated_case_count'] != count or report['partial_evaluation']:
        raise ValueError(f'{name}: incomplete raw corpus')
    if report['errors'] or report['parity'] != dict(cases=count, failures=[], scope=report['parity']['scope']) or report['implementation']['source_changed_during_run']:
        raise ValueError(f'{name}: preparation, parity or stability failure')
    if exit_code != (0 if report['safety_gate']['passed'] else 1):
        raise ValueError(f'{name}: raw exit and safety result disagree')
    if not report['safety_gate']['required_checks_complete']:
        raise ValueError(f'{name}: raw safety checks incomplete')


def evaluate_corpus(name, cases, baseline, expectations, gold):
    records = []
    errors = []
    for case in cases:
        try:
            entry = expectations.get(case.get('parent_id', case['id']))
            if entry:
                validate_lineage(case, gold[case.get('parent_id', case['id'])], entry)
            slots = prepare_case(case)
            record = evaluate_case(case, slots, baseline[case['id']], entry)
            if slots != prepare_case(case, 'voice_transcript'):
                record['failures'].append('text_voice_mismatch')
                record['strict_passed'] = False
            if record['payload_changed_from_baseline'] or record['failures']:
                record['source_text'] = case['text']
                record['actual_slots'] = slots
            records.append(record)
        except Exception as exc:
            errors.append(dict(id=case['id'], error=type(exc).__name__ + ': ' + str(exc)))
    if len(cases) != len(baseline) or {c['id'] for c in cases} != set(baseline):
        errors.append(dict(id='CORPUS_INTEGRITY', error='Missing, added or duplicate cases'))
    failures = [r for r in records if r['failures']]
    gaps = [r['id'] for r in records if r['historical_coverage_gap']]
    return dict(corpus=name, cases=len(cases), evaluated=len(records), errors=errors,
                integration_gate=dict(passed=not failures and not errors,
                                      failure_cases=len(failures)),
                strict_contract_gate=dict(passed=not failures and not errors and not gaps,
                                          historical_coverage_gaps=gaps),
                changed_case_count=sum(r['payload_changed_from_baseline'] for r in records),
                changed_cases=[r for r in records if r['payload_changed_from_baseline']],
                failures=failures, records=records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', type=Path, default=ROOT/'artifacts/owner-language/v2')
    parser.add_argument('--review-draft', action='store_true',
                        help='Produce review evidence but never pass an unaccepted contract')
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    contract_errors = []
    try:
        contract, baseline = load_contract()
    except ValueError as exc:
        if not args.review_draft:
            raise
        contract_errors.append(str(exc))
        contract = json.loads((V2/'contract.json').read_text())
        baseline = load_baseline(contract)
    before = fingerprint()
    before_dependencies = lexical_dependency_fingerprint()
    generation_exit = run(['scripts/generate_owner_language_variants.py', '--output-dir', str(out)], out/'generation.log')
    if generation_exit:
        raise ValueError('Frozen generated variants did not reproduce')
    paths = dict(train=CORPUS/'train.jsonl', holdout=CORPUS/'holdout.jsonl',
                 semantic=out/'semantic_variants.jsonl', noise=out/'noise_variants.jsonl')
    frozen_manifest = json.loads((CORPUS/'manifest.json').read_text())
    expected_hashes = dict(train=frozen_manifest['train_sha256'], holdout=frozen_manifest['holdout_sha256'],
        semantic=frozen_manifest['variants']['files']['semantic_variants']['sha256'],
        noise=frozen_manifest['variants']['files']['noise_variants']['sha256'])
    if any(digest(path) != expected_hashes[name] for name, path in paths.items()):
        raise ValueError('Corpus bytes differ from frozen generator lineage')
    def raw_run(item):
        name, path = item
        report_path = out/f'{name}-raw-v1-report.json'
        code = run(['scripts/owner_language_benchmark.py', '--require-safety', '--corpus', str(path), '--out', str(report_path)], out/f'{name}-raw-v1.log')
        report = json.loads(report_path.read_text())
        validate_raw(report, code, path, contract, name)
        return name, report, code
    with ThreadPoolExecutor(max_workers=2) as pool:
        raw_results = list(pool.map(raw_run, paths.items()))
    raw = {name: report for name, report, _ in raw_results}
    exits = {name: code for name, _, code in raw_results}
    source_hashes = raw['train']['implementation']['service_sha256']
    if any(r['implementation']['service_sha256'] != source_hashes for r in raw.values()):
        contract_errors.append('Raw reports used different source implementations')
    diagnostic_path = out/'exposed-audience-diagnostic-v1.json'
    diagnostic_exit = run(['scripts/diagnose_owner_language_audience.py', '--require-safety',
        '--corpus', str(paths['holdout']), '--overlay', str(CORPUS/'audience_diagnostic_overlay.json'),
        '--original-report', str(out/'holdout-raw-v1-report.json'), '--out', str(diagnostic_path)], out/'exposed-audience-diagnostic-v1.log')
    diagnostic = json.loads(diagnostic_path.read_text()) if diagnostic_path.exists() else None
    corpora = {name: load(path) for name, path in paths.items()}
    gold = {case['id']: case for name in ('train', 'holdout') for case in corpora[name]}
    reports = {}
    for name, cases in corpora.items():
        reports[name] = evaluate_corpus(name, cases, baseline[name], contract['reviewed_expectations'], gold)
        write_json(out/f'{name}-versioned-v2-report.json', reports[name])
        print(f"{name}: v2 {reports[name]['integration_gate']}; strict {reports[name]['strict_contract_gate']['passed']}", flush=True)
    after = fingerprint()
    after_dependencies = lexical_dependency_fingerprint()
    dependencies_stable = before_dependencies == after_dependencies
    if not dependencies_stable:
        contract_errors.append('Installed semantic dependency bytes changed during run')
    if before != after:
        contract_errors.append('Source or v2 oracle changed during run; rerun stable sources')
    try:
        verify_contract(contract, baseline)
    except ValueError as exc:
        contract_errors.append(str(exc))
    first = json.loads((CORPUS/'first-unseen-report.json').read_text())
    first_hash = contract['frozen_v1_files']['tests/benchmarks/owner-language/first-unseen-report.json']
    combined = build(json.loads((CORPUS/'manifest.json').read_text()),
                     json.loads((CORPUS/'taxonomy.json').read_text()), raw['train'],
                     semantic=raw['semantic'], noise=raw['noise'], holdout=first,
                     holdout_report_sha256=first_hash, exposed_regression=raw['holdout'])
    passed = not contract_errors and all(r['integration_gate']['passed'] for r in reports.values())
    raw_passed = all(r['safety_gate']['passed'] and r['coverage_gate']['passed'] for r in raw.values())
    strict_passed = all(r['strict_contract_gate']['passed'] for r in reports.values()) and not contract_errors
    combined.update(versioned_contract='owner-task-audience-v2',
        raw_v1_aggregate_status='PASS' if raw_passed else 'FAIL', raw_v1_exit_codes=exits,
        reviewed_v2_integration_status='PASS' if passed else 'FAIL',
        strict_v2_contract_status='PASS' if strict_passed else 'FAIL',
        original_diagnostic_exit_code=diagnostic_exit,
        original_diagnostic_v1=diagnostic,
        first_unseen_outcome='IMMUTABLE_FAIL_NOT_REPLACED',
        case_count=sum(len(c) for c in corpora.values()), audience_denominator_exclusions=0,
        source_stable=before == after and dependencies_stable, source_fingerprint=before,
        lexical_dependencies=before_dependencies, lexical_dependencies_stable=dependencies_stable,
        v2_contract_sha256=digest(V2/'contract.json'), contract_errors=contract_errors,
        versioned_corpora={name: {key: report[key] for key in ('cases', 'evaluated', 'integration_gate', 'strict_contract_gate', 'changed_case_count')} for name, report in reports.items()},
        scope='Reviewed audience non-regression integration; unchanged coverage gaps are retained without correctness credit. Physical iPhone OWNER_PENDING.')
    write_json(out/'combined-report.json', combined)
    print(f"Raw v1 aggregate {combined['raw_v1_aggregate_status']}; reviewed v2 integration {combined['reviewed_v2_integration_status']}; strict v2 contract {combined['strict_v2_contract_status']}")
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(f"### Owner language separately reviewed v2\n\nRaw frozen-v1 aggregate: **{combined['raw_v1_aggregate_status']}**. Reviewed v2 non-regression integration: **{combined['reviewed_v2_integration_status']}**. Strict v2 coverage: **{combined['strict_v2_contract_status']}**. All 11,205 cases retained; zero audience exclusions. Immutable first FAIL, current raw failures, old diagnostic and all remaining coverage gaps are attached. Physical iPhone acceptance remains OWNER_PENDING.\n")
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
