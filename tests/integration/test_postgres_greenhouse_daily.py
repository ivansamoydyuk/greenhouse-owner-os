"""Real current-source cockpit: no writes, historic claims or cloned ledgers."""
from uuid import UUID, uuid4
from sqlalchemy import text
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_finance_analytics import seeded, snapshot
from tests.integration.test_postgres_harvest_journey import farm, field, active, command, estimate_body
from tests.integration.test_postgres_day_workers import day, actual, accrue


def daily(c, e, **params):
    response = c.get(f'/v1/entities/{e}/greenhouse-daily', params=dict(date_from='2026-10-04', date_to='2026-10-12', currency='TRY', **params))
    assert response.status_code == 200, response.text
    return response.json()


def test_current_finance_reused_once_read_only_and_area_not_inferred(preview):
    c, e, cash, areas, seed = seeded(preview)
    before = snapshot(preview, e)
    result = daily(c, e, greenhouse_id=str(areas['İmamlı']))
    assert snapshot(preview, e) == before
    selected = result['selected']
    assert selected['finance']['revenue'] == '1000.00'
    assert selected['finance']['operating_cost'] == '142.86'
    assert selected['finance']['virtual_general_cost'] == '142.86'
    assert selected['finance']['cost_per_kg']['value'] is None
    assert selected['area']['area_da'] is None
    assert selected['finance']['per_da']['operating_cost'] is None
    assert 'accounts' not in selected['finance'] and 'cash_totals' not in selected['finance']
    assert selected['comparison']['status'] == 'NO_PRIOR_SOURCES'
    assert selected['comparison']['previous'] is None
    assert selected['harvest']['depot_kg']['value'] == '20.000'
    assert sum(float(x['finance']['operating_cost']) for x in result['greenhouses']) == 500
    assert result['filters']['season_end'] == '2027-10-01' and result['filters']['season_end_exclusive']
    assert preview[3].reconcile(entity_id=UUID(e))['ok']


def test_verified_area_entire_period_and_source_drilldown(preview):
    c, e, cash, areas, seed = seeded(preview)
    gid = str(areas['İmamlı'])
    ref = send(c, f'/entities/{e}/fertilizer/references', dict(client_reference_id=str(uuid4()), captured_at='2026-10-12T10:00:00+03:00', work_area_id=gid, area_da='20', verification='TEST owner confirmed synthetic measured area', effective_from='2026-10-04', confirmed=True))
    selected = daily(c, e, greenhouse_id=gid)['selected']
    assert selected['area']['area_da'] == '20' and ref['id'] in selected['area']['reference_ids']
    assert selected['finance']['per_da']['revenue'] == '50.0000'
    for key in ('finance', 'harvest', 'profile', 'day_workers'):
        response = c.get(selected['links'][key])
        assert response.status_code == 200, (key, response.text)
    # A later area change prevents a whole-period denominator.
    send(c, f'/entities/{e}/fertilizer/references', dict(client_reference_id=str(uuid4()), captured_at='2026-10-12T10:10:00+03:00', work_area_id=gid, area_da='21', verification='TEST later synthetic measured area', effective_from='2026-10-10', replaces_id=ref['id'], confirmed=True))
    changed = daily(c, e, greenhouse_id=gid)['selected']['area']
    assert changed['status'] == 'CHANGED_IN_PERIOD' and changed['area_da'] is None


def test_quality_estimated_selected_kg_and_stale_estimate_sources(preview):
    i = farm(preview)
    report, batch = field(i, count=131)
    source, proposal, receipt = active(i, [batch['id']])
    command(i, receipt['id'], 'estimates', estimate_body(i))
    selected = daily(i['c'], i['e'], greenhouse_id=str(i['areas']['İmamlı']))['selected']
    assert selected['harvest']['branches']['value'] == '131'
    assert selected['harvest']['depot_kg']['value'] == '600.000'
    assert selected['harvest']['weighted_price_per_kg'] == '50.0000'
    highest = next(x for x in selected['harvest']['quality_rows'] if x['quality'] == 'HIGHEST')
    assert highest['net_weight_kg'] == '600.000' and highest['share_percent'] == '100.0000'
    assert all(x['quality_reviews'] for e in selected['harvest']['events'] for x in e['lines'])
    exact = daily(i['c'], i['e'], greenhouse_id=str(i['areas']['İmamlı']), allocation='exact')['selected']['harvest']
    assert exact['depot_kg']['value'] is None and exact['depot_kg']['classification'] == 'UNAVAILABLE'
    command(i, receipt['id'], 'links', dict(expected_version=1, confirmed=True, batch_ids=[batch['id']], reason='TEST refreshed current source relationship'))
    stale = daily(i['c'], i['e'], greenhouse_id=str(i['areas']['İmamlı']))['selected']['harvest']
    assert stale['depot_kg']['value'] is None
    assert stale['denominator']['stale_estimate_count'] == 1
    assert stale['events'][0]['stale_estimate_revision_id'] is not None
    assert stale['events'][0]['selected_attribution_known'] is False
    october = next(m for m in stale['months'] if m['month'] == '2026-10')
    assert october['depot_kg'] is None and october['revenue'] is None
    assert october['state'] == 'UNKNOWN' and october['known_source_count'] == 0
    assert october['missing_source_count'] == 1
    assert october['settlement_ids'] == [receipt['id']]


def test_actual_prior_sources_enable_equal_elapsed_comparison_only(preview):
    i = farm(preview)
    field(i, count=10, day='2025-10-05')
    field(i, count=20, day='2026-10-05')
    selected = daily(i['c'], i['e'], greenhouse_id=str(i['areas']['İmamlı']))['selected']
    comparison = selected['comparison']
    assert comparison['status'] == 'AVAILABLE'
    assert comparison['date_from'] == '2025-10-04' and comparison['date_to'] == '2025-10-12'
    assert comparison['previous']['branches']['value'] == '10'
    assert comparison['current']['branches']['value'] == '20'
    assert comparison['previous']['per_da']['revenue'] is None


def test_unknown_entity_scope_and_invalid_period_are_rejected(preview):
    c, e, cash, areas, seed = seeded(preview)
    for params in (dict(greenhouse_id=str(uuid4())), dict(date_from='2026-10-13', date_to='2026-10-12'), dict(allocation='guess')):
        response = c.get(f'/v1/entities/{e}/greenhouse-daily', params=dict(dict(date_from='2026-10-04', date_to='2026-10-12'), **params))
        assert response.status_code == 422
    response = c.get(f'/v1/entities/{uuid4()}/greenhouse-daily', params=dict(date_from='2026-10-04', date_to='2026-10-12'))
    assert response.status_code == 422
    assert response.json()['error']['message'] == 'Доступ владельца к этой компании не подтверждён'


def test_mixed_day_worker_payable_initial_partial_and_settled_debt(day):
    i = day
    report, _ = actual(i, people='7', owner=True, allocations=[
        dict(greenhouse_id=str(i['g']), work_area_id=None, people=4),
        dict(greenhouse_id=str(i['g2']), work_area_id=None, people=3),
    ])
    accrued = accrue(i, report)
    def summaries():
        result = daily(i['c'], i['e'])
        return {g['id']: g for g in result['greenhouses']}
    initial = summaries()
    for gid, amount in ((str(i['g']), '400.00'), (str(i['g2']), '300.00')):
        selected = initial[gid]
        assert selected['finance']['operating_cost'] == amount
        assert selected['work']['day_workers']['accrued_amount'] == amount
        assert selected['work']['day_workers']['current_debt'] == amount
        assert selected['work']['day_workers']['paid_amount'] == '0.00'
        event = selected['finance']['economic_events'][0]
        assert event['source']['day_actual_id'] == report['id']
    def payment(amount):
        draft = send(i['c'], '/payments', dict(entity_id=str(i['e']), counterparty_id=str(i['cp']), money_account_id=str(i['cash']),
            direction='out', amount=amount, currency='TRY', event_date='2026-10-12', allocations=[dict(obligation_id=accrued['obligation_id'], amount=amount)]))
        return send(i['c'], f"/payments/{draft['id']}/post?expected_version=1", expected=200)
    payment('200')
    partial = summaries()
    for gid in (str(i['g']), str(i['g2'])):
        work = partial[gid]['work']['day_workers']
        assert work['current_debt'] is None and work['paid_amount'] is None
        assert work['debt_allocation_unknown'] and work['payment_allocation_unknown']
        assert work['documents'][0]['remaining_amount'] == '500.00'
    payment('500')
    settled = summaries()
    for gid, amount in ((str(i['g']), '400.00'), (str(i['g2']), '300.00')):
        work = settled[gid]['work']['day_workers']
        assert work['current_debt'] == '0.00' and work['paid_amount'] is None
        assert not work['debt_allocation_unknown'] and work['payment_allocation_unknown']
        assert settled[gid]['finance']['operating_cost'] == amount
    assert i['finance'].reconcile(entity_id=i['e'])['ok']
