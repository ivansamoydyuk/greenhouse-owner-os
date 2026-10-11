"""Browser proof is tied to its job mode before any read-only SQL attestation."""
import json
from uuid import UUID

import pytest

from scripts import r3_connected_sql_evidence as evidence

SHA = 'a' * 40
AUDITS = {
    'CONNECTED_TEST': 'AUD011 stored adversarial and ordinary legacy modal title',
    'PRIVATE_DAILY_TEST': 'AUD011 private legacy boundary and R3 literal product CSP',
}
SHARED = {
    'two-tab stale confirmation rejected before any effect',
    'A source revision trusted expense lost response hard reload',
    'E all tasks DONE reload completed restore',
    'two-tab old DONE replay preserves later restore and reads current state',
    'D inline product receipt exact physical movement UNKNOWN opening',
    'C field original file caption explicit depot link mismatch',
    'late A detail cannot target visible B action',
    'F cold and warm secondary outage keeps Home authenticated and explicit retry recovers',
    'offline persisted original reload session expiry recovery',
}


def manifest(mode):
    ids = iter(str(UUID(int=i)) for i in range(1, 100))
    records = {
        'expenses': [{'proposal_id': next(ids), 'source_id': next(ids), 'effect': {'id': next(ids)}} for _ in range(2)],
        'payments': [next(ids) for _ in range(4)],
        'receipts': [{'id': next(ids)} for _ in range(4)],
        'reports': [next(ids) for _ in range(2)],
        'settlements': [next(ids) for _ in range(2)],
        'products': [next(ids) for _ in range(2)],
        'tasks': [next(ids)] * 2,
        'workerTasks': [next(ids)] * 2,
    }
    records['settlementEvidence'] = [dict(
        id=records['settlements'][index], report_id=records['reports'][index],
        original_sha256='b' * 64, original_bytes=1,
        caption=f'TEST {engine} separate owner caption',
        field_original=f'TEST {engine} field original seven cuts',
        unit_price=('10.00', '123456789.12')[index],
    ) for index, engine in enumerate(('chromium', 'webkit'))]
    return dict(status='PASS', mode=mode, sha=SHA, errors=[], applicationFailures=[], records=records,
                journeys=[dict(engine=engine, name=name, **{'pass': True})
                          for engine, direction in (('chromium', 'payable'), ('webkit', 'receivable'))
                          for name in sorted(SHARED | {AUDITS[mode], f'B {direction} partial and full allocated settlement'})])


@pytest.mark.parametrize('mode', AUDITS)
def test_complete_manifest_accepts_only_its_exact_mode_and_all_original_records(mode):
    result = manifest(mode)
    assert len(result['journeys']) == 22
    assert evidence.validate_browser(result, SHA, mode) is result['records']
    assert evidence.JOURNEYS == SHARED


@pytest.mark.parametrize('mode', AUDITS)
@pytest.mark.parametrize('missing', sorted(SHARED))
def test_each_shared_domain_journey_is_required_in_each_mode(mode, missing):
    result = manifest(mode)
    result['journeys'] = [j for j in result['journeys'] if not (j['engine'] == 'webkit' and j['name'] == missing)]
    with pytest.raises(AssertionError, match='Incomplete webkit'):
        evidence.validate_browser(result, SHA, mode)


@pytest.mark.parametrize('mode', AUDITS)
@pytest.mark.parametrize('change', ['missing', 'opposite', 'both'])
def test_aud011_is_required_and_cannot_cross_modes(mode, change):
    result = manifest(mode)
    row = next(j for j in result['journeys'] if j['engine'] == 'chromium' and j['name'] == AUDITS[mode])
    opposite = AUDITS[next(value for value in AUDITS if value != mode)]
    if change == 'missing':
        result['journeys'].remove(row)
    elif change == 'opposite':
        row['name'] = opposite
    else:
        result['journeys'].append({**row, 'name': opposite})
    with pytest.raises(AssertionError):
        evidence.validate_browser(result, SHA, mode)


@pytest.mark.parametrize('mode', AUDITS)
@pytest.mark.parametrize('change', ['missing-mode', 'unknown-mode', 'opposite-mode', 'missing-sha', 'wrong-sha', 'failed-status', 'errors', 'application-failure', 'missing-engine', 'unknown-engine', 'failed-journey', 'duplicate-journey'])
def test_incomplete_wrong_identity_or_failed_manifest_is_rejected(mode, change):
    result = manifest(mode)
    if change == 'missing-mode': result.pop('mode')
    elif change == 'unknown-mode': result['mode'] = 'PRODUCTION'
    elif change == 'opposite-mode': result['mode'] = next(value for value in AUDITS if value != mode)
    elif change == 'missing-sha': result.pop('sha')
    elif change == 'wrong-sha': result['sha'] = 'c' * 40
    elif change == 'failed-status': result['status'] = 'FAIL'
    elif change == 'errors': result['errors'] = ['TEST browser failure']
    elif change == 'application-failure': result['applicationFailures'] = ['TEST application failure']
    elif change == 'missing-engine': result['journeys'] = [j for j in result['journeys'] if j['engine'] == 'chromium']
    elif change == 'unknown-engine': result['journeys'][0]['engine'] = 'firefox'
    elif change == 'failed-journey': result['journeys'][0]['pass'] = False
    elif change == 'duplicate-journey': result['journeys'].append(dict(result['journeys'][0]))
    with pytest.raises(AssertionError):
        evidence.validate_browser(result, SHA, mode)


@pytest.mark.parametrize('mode', AUDITS)
@pytest.mark.parametrize('key', ['expenses', 'payments', 'receipts', 'reports', 'settlements', 'settlementEvidence', 'products', 'tasks', 'workerTasks'])
def test_original_record_count_requirements_remain_strict(mode, key):
    result = manifest(mode)
    result['records'][key].pop()
    with pytest.raises(AssertionError):
        evidence.validate_browser(result, SHA, mode)


@pytest.mark.parametrize('expected_sha', [None, '', 'not-a-sha', 'A' * 40])
def test_expected_sha_must_be_exact(expected_sha):
    with pytest.raises(AssertionError, match='Exact feature SHA'):
        evidence.validate_browser(manifest('CONNECTED_TEST'), expected_sha, 'CONNECTED_TEST')


def test_unknown_expected_mode_is_rejected():
    with pytest.raises(AssertionError, match='Known expected browser mode'):
        evidence.validate_browser(manifest('CONNECTED_TEST'), SHA, 'UNKNOWN')


@pytest.mark.parametrize('job_mode', AUDITS)
def test_cli_binds_manifest_mode_to_current_job_before_database_access(job_mode, tmp_path, monkeypatch):
    result_mode = next(mode for mode in AUDITS if mode != job_mode)
    source, output = tmp_path / 'browser.json', tmp_path / 'proof.json'
    source.write_text(json.dumps(manifest(result_mode)))
    monkeypatch.setenv('OWNER_PREVIEW_FEATURE_SHA', SHA)
    monkeypatch.setenv('R3_CONNECTED_PILOT', '1')
    if job_mode == 'PRIVATE_DAILY_TEST': monkeypatch.setenv('R3_DAILY_PILOT', '1')
    else: monkeypatch.delenv('R3_DAILY_PILOT', raising=False)
    monkeypatch.setattr(evidence.sys, 'argv', ['evidence', str(source), str(output)])
    monkeypatch.setattr(evidence, 'make_session_factory', lambda *_: pytest.fail('Wrong-mode proof reached database'))
    with pytest.raises(SystemExit) as exc:
        evidence.main()
    assert exc.value.code == 1
    proof = json.loads(output.read_text())
    assert proof['status'] == 'FAIL' and proof['phase'] == 'browser_manifest'
    assert proof['failure_type'] == 'AssertionError'
    assert proof['failure'] == 'Complete same-SHA connected browser PASS required'
