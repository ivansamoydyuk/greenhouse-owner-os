"""Assemble source-count, per-family coverage, safety and friction evidence.

Only pass a holdout report after its independent custodian authorizes evaluation.
This reporting tool never reads a holdout corpus or invokes preparation.
"""
import argparse,hashlib,json
from pathlib import Path
from scripts.owner_language_benchmark import CORPUS,write_json


def read(path):
    return json.loads(path.read_text()) if path else None


def validate_first_holdout(manifest,report_sha256):
    frozen=manifest.get('first_holdout_evaluation',{}).get('report_sha256')
    if not frozen or report_sha256!=frozen:
        raise ValueError('Only the sealed first report can be called unseen; later runs are exposed regression')


def build(manifest,taxonomy,train,baseline=None,semantic=None,noise=None,holdout=None,browser=None,private_evidence=None,holdout_report_sha256=None,exposed_regression=None):
    private_evidence=private_evidence or {}
    if holdout:
        validate_first_holdout(manifest,holdout_report_sha256)
    if baseline and baseline['corpus_sha256']!=train['corpus_sha256']:
        raise ValueError('Before/after GOLD corpus hashes differ')
    if baseline and baseline.get('harness_sha256')!=train.get('harness_sha256'):
        raise ValueError('Before/after scoring harness definitions differ')
    for name,report,count in [('train',train,manifest['train_gold']),('semantic',semantic,manifest['variants']['semantic']),('noise',noise,manifest['variants']['noise']),('holdout',holdout,manifest['holdout_gold']),('exposed_regression',exposed_regression,manifest['holdout_gold'])]:
        if report and (report['summary']['cases']!=count or report.get('partial_evaluation')):raise ValueError(f'{name} report is partial, not the frozen full corpus')
        if report and report.get('harness_sha256')!=train.get('harness_sha256'):
            raise ValueError(f'{name} report uses different scoring definitions')
    for name,report in [('semantic',semantic),('noise',noise)]:
        if report and report.get('implementation',{}).get('service_sha256')!=train.get('implementation',{}).get('service_sha256'):
            raise ValueError(f'{name} report uses different candidate implementation files')
    if train['corpus_sha256']!=manifest['train_sha256']:
        raise ValueError('Training report does not measure frozen current GOLD')
    for name,report in [('semantic_variants',semantic),('noise_variants',noise)]:
        if report and report['corpus_sha256']!=manifest['variants']['files'][name]['sha256']:
            raise ValueError(f'{name} report does not match frozen generated hash')
    if holdout and holdout['corpus_sha256']!=manifest['holdout_sha256']:
        raise ValueError('Holdout report does not match frozen unseen bytes')
    if exposed_regression and exposed_regression['corpus_sha256']!=manifest['holdout_sha256']:
        raise ValueError('Exposed regression must retain the original frozen labels; diagnostic errata are separate')
    definitions={x['family']:x for x in taxonomy['families']}
    rows=[]
    for family,counts in sorted(manifest['counts_by_family'].items()):
        stats=train['families'].get(family)
        failures=[f for f in train['failures'] if f['family']==family]
        rows.append(dict(event_family=family,historical_examples=private_evidence.get('counts_by_family',{}).get(family,{}).get('historical_examples'),
            gold_counts={key:counts.get(key,0) for key in ('gold','train','holdout','quarantined')},
            generated_variants=manifest['variants']['by_family'].get(family,{}),
            supported_current_route=definitions[family]['supported_current_route'],
            baseline_training=baseline['families'].get(family) if baseline else None,
            candidate_training=stats,
            semantic_variants=semantic['families'].get(family) if semantic else None,
            noise_variants=noise['families'].get(family) if noise else None,
            unseen_holdout=holdout['families'].get(family) if holdout else None,
            exposed_regression=exposed_regression['families'].get(family) if exposed_regression else None,
            train_gap_case_ids=[f['id'] for f in failures],
            known_gaps=dict(coverage_failure_cases=len(failures),
                unsafe_routes=sum(bool(f['unsafe']) for f in failures),
                factual_contradictions=sum(bool(f['invented']) for f in failures),
                unassessed_critical_fact_cases=sum(bool(f['unassessed_critical_facts']) for f in failures),
                contract_boundaries=definitions[family]['ambiguities_and_dangerous_confusions']),
            manual_input_scope='Corpus missing-label/manual-selection proxies only; actual browser measurements are separate evidence, never assumed zero'))
    def weakest_metric(row):
        stats=row['candidate_training'] or {}
        return (stats.get('supported_route_recall') if stats.get('supported_route_recall') is not None else 1,
                stats.get('key_field_accuracy') if stats.get('key_field_accuracy') is not None else 1)
    weakest=[r['event_family'] for r in sorted(rows,key=weakest_metric)[:5]]
    return dict(version=manifest['version'],population=dict(evaluated_gold=manifest['evaluated_gold'],
            training_gold=manifest['train_gold'],unseen_gold=manifest['holdout_gold'],
            quarantined_excluded=manifest['quarantined_gold'],catalogued_gold=manifest['gold_total'],
            exact_historical_catalog=private_evidence.get('exact_historical_gold'),authored_catalog=private_evidence.get('authored_grounded_gold'),
            semantic=manifest['variants']['semantic'],noise=manifest['variants']['noise']),
        provenance='Historical messages are TEST language evidence only. Family source counts overlap. Catalog counts include20 quarantined cases; evaluated GOLD does not.',
        candidate_implementation=train['implementation'],harness_sha256=train.get('harness_sha256'),
        strict_coverage_gate=train.get('coverage_gate'),hard_safety_gate=train.get('safety_gate'),
        training=train['summary'],baseline=baseline['summary'] if baseline else None,
        unseen=holdout['summary'] if holdout else None,
        first_unseen_gates=dict(safety=holdout.get('safety_gate'),strict=holdout.get('coverage_gate')) if holdout else None,
        first_unseen_report_sha256=holdout_report_sha256,
        exposed_regression=exposed_regression['summary'] if exposed_regression else None,
        exposed_regression_gates=dict(safety=exposed_regression.get('safety_gate'),strict=exposed_regression.get('coverage_gate')) if exposed_regression else None,
        holdout_status='First unseen outcome retained; cases subsequently exposed for remediation' if manifest.get('holdout_run') else 'NOT RUN / embargoed pending authorized stabilization',
        actual_browser_friction=browser,
        browser_measurement_note='Use browser report completed-only denominator and blocked scenarios explicitly. No corpus proxy is measured taps. Physical iPhone acceptance remains OWNER_PENDING.',
        weakest_five_training_families=weakest,matrix=rows)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--train',required=True,type=Path);ap.add_argument('--baseline',type=Path);ap.add_argument('--semantic',type=Path);ap.add_argument('--noise',type=Path);ap.add_argument('--holdout',type=Path,help='Sealed original first-unseen report only');ap.add_argument('--exposed-regression',type=Path,help='Later run on the same frozen cases, explicitly no longer unseen');ap.add_argument('--browser-friction',type=Path);ap.add_argument('--private-owner-manifest',type=Path,help='Owner-only report enrichment; never use for repository/CI publication');ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();report=build(read(CORPUS/'manifest.json'),read(CORPUS/'taxonomy.json'),read(a.train),read(a.baseline),read(a.semantic),read(a.noise),read(a.holdout),read(a.browser_friction),read(a.private_owner_manifest),hashlib.sha256(a.holdout.read_bytes()).hexdigest() if a.holdout else None,read(a.exposed_regression));write_json(a.out,report)
    print(json.dumps(dict(population=report['population'],weakest_five=report['weakest_five_training_families'],holdout=report['holdout_status']),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
