"""AUD-009: field count coverage is independent of depot weight and money."""
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from greenhouse_owner_os.infrastructure.harvest_models import EvidenceRow, ReportRow
from greenhouse_owner_os.services.harvest import row_dict
from tests.integration.test_postgres_harvest import prepare, receipt, draft, post
from tests.integration.test_postgres_readiness import preview, send


def report(c, e, count, greenhouse='Poncho', day='2026-10-05'):
    return send(c, '/entities/'+e+'/harvest/reports', dict(
        event_date=day, original_greenhouse=greenhouse, cut_count=count,
        reporter='TEST synthetic worker', evidence=dict(
            source='test:synthetic', raw_text='TEST unchanged field evidence '+str(uuid4()))))


def coverage(total, known):
    return dict(report_count=total, known_report_count=known,
                unknown_report_count=total-known, complete=total > 0 and total == known)


def evidence_rows(preview, entity):
    with preview[0]() as session:
        return {table.__tablename__: [row_dict(r) for r in session.scalars(
            select(table).where(table.entity_id == UUID(entity)).order_by(table.id))]
            for table in (ReportRow, EvidenceRow)}


def record_raw(name, actual):
    if path := os.environ.get('HARVEST_COVERAGE_EVIDENCE'):
        with Path(path).open('a') as output:
            output.write(json.dumps(dict(scenario=name, response=actual), ensure_ascii=False)+'\n')


@pytest.mark.parametrize('counts,known_sum', [([], None), ([None], None), ([7, None], 7), ([7], 7)],
                         ids=['no-reports', 'unknown-only', 'seven-plus-unknown', 'known-seven'])
def test_field_count_four_distinct_states_and_immutable_sources(preview, counts, known_sum):
    c, e, cash, depot, areas = prepare(preview)
    for count in counts:
        report(c, e, count)
    before = evidence_rows(preview, e)
    response = c.get('/v1/entities/'+e+'/harvest')
    assert response.status_code == 200, response.text
    view = response.json()
    record_raw(str(counts), view)
    assert evidence_rows(preview, e) == before
    expected = coverage(len(counts), sum(n is not None for n in counts))
    assert view['total_cut_count'] == known_sum
    assert view['cut_count_coverage'] == expected
    field = next(g for g in view['cut_counts'] if g['greenhouse_id'] == str(areas['Özvadi']))
    assert field['count'] == known_sum and field['count_coverage'] == expected
    empty = next(g for g in view['cut_counts'] if g['greenhouse_id'] == str(areas['İmamlı']))
    assert empty['count'] is None and empty['count_coverage'] == coverage(0, 0)
    assert view['unknown_cut_count'] is None
    assert view['unknown_cut_count_coverage'] == coverage(0, 0)
    assert [r['cut_count'] for r in view['reports']] == [r['cut_count'] for r in sorted(before['worker_reports'], key=lambda r: r['id'])]
    assert view['analytics'] == []  # Field evidence must not become depot kg/revenue.


def test_coverage_respects_entity_inclusive_dates_and_unknown_geography(preview):
    c, e, cash, depot, areas = prepare(preview)
    report(c, e, 99, day='2026-10-04')
    report(c, e, 7, day='2026-10-05')
    report(c, e, None, day='2026-10-06')
    report(c, e, 88, day='2026-10-07')
    report(c, e, None, greenhouse='TEST unrecognized place', day='2026-10-05')
    report(c, e, 3, greenhouse='TEST unrecognized place', day='2026-10-06')
    other = send(c, '/entities', dict(name='TEST other entity '+str(uuid4()), currency='TRY', source='test:synthetic'))['id']
    report(c, other, 1234)
    before = evidence_rows(preview, e)
    view = c.get('/v1/entities/'+e+'/harvest?date_from=2026-10-05&date_to=2026-10-06').json()
    record_raw('scope-date-geography', view)
    assert view['total_cut_count'] == 10
    assert view['cut_count_coverage'] == coverage(4, 2)
    assert view['unknown_cut_count'] == 3
    assert view['unknown_cut_count_coverage'] == coverage(2, 1)
    assert next(g for g in view['cut_counts'] if g['greenhouse_id'] == str(areas['Özvadi']))['count_coverage'] == coverage(2, 1)
    assert evidence_rows(preview, e) == before
    assert len(view['reports']) == 6  # Existing raw lists remain unfiltered; totals use the selected range.
    empty = c.get('/v1/entities/'+e+'/harvest?date_from=2026-10-08').json()
    assert empty['total_cut_count'] is None and empty['cut_count_coverage'] == coverage(0, 0)
    assert c.get('/v1/entities/'+e+'/harvest?date_from=2026-10-07&date_to=2026-10-05').status_code == 422


def test_depot_partial_average_exposes_only_corresponding_count_coverage(preview):
    c, e, cash, depot, areas = prepare(preview)
    report(c, e, 7)
    report(c, e, None)
    body = receipt(depot, areas)
    body['lines'] = [dict(original_quality_name='Grand', net_weight_kg=kg, unit_price='30',
                          count=count, source_line=str(index), allocations=[])
                     for index, (kg, count) in enumerate([('70', 7), ('30', None)])]
    settlement = post(c, e, draft(c, e, body))
    view = c.get('/v1/entities/'+e+'/harvest').json()
    record_raw('partial-depot-average', view)
    bucket = view['analytics'][0]
    assert bucket['total_depot_kg'] == '100.000'
    assert bucket['confirmed_kg'] == '70.000' and bucket['confirmed_count'] == 7
    assert Decimal(bucket['average_weight_kg']) == 10  # Never 100 / 7 or field+depot count.
    assert bucket['count_coverage_complete'] is False
    assert bucket['cut_count_coverage'] == coverage(0, 0)
    detail = c.get('/v1/entities/'+e+'/harvest/settlements/'+settlement['id']).json()
    assert detail['count_coverage_complete'] is False
    assert detail['confirmed_kg'] == '70.000' and detail['confirmed_count'] == 7
    assert detail['reconciliation']['depot_count'] is None
    assert detail['reconciliation']['field_count'] is None


def test_coverage_uses_all_reports_beyond_the_100_row_source_list(preview):
    c, e, cash, depot, areas = prepare(preview)
    # The oldest UNKNOWN is deliberately outside the newest-first raw list.
    unknown = report(c, e, None, day='2026-10-04')
    for _ in range(100):
        report(c, e, 1, day='2026-10-05')
    before = evidence_rows(preview, e)
    view = c.get('/v1/entities/'+e+'/harvest').json()
    assert len(view['reports']) == 100
    assert unknown['id'] not in {r['id'] for r in view['reports']}
    assert view['total_cut_count'] == 100
    assert view['cut_count_coverage'] == coverage(101, 100)
    assert evidence_rows(preview, e) == before


def test_field_coverage_matches_existing_greenhouse_analytics_without_depot_counts(preview):
    c, e, cash, depot, areas = prepare(preview)
    report(c, e, 7)
    report(c, e, None)
    post(c, e, draft(c, e, receipt(depot, areas)))
    before = evidence_rows(preview, e)
    view = c.get('/v1/entities/'+e+'/harvest').json()
    bucket = next(a for a in view['analytics'] if a['greenhouse_id'] == str(areas['Özvadi']))
    assert bucket['total_cut_count'] == 7
    assert bucket['cut_count_coverage'] == coverage(2, 1)
    assert bucket['confirmed_count'] == 50
    assert bucket['confirmed_kg'] == '1000.000'
    assert bucket['count_coverage_complete'] is True
    assert evidence_rows(preview, e) == before


@pytest.mark.parametrize('counts,complete,expected_average', [
    ([None, None], False, None), ([2, 8], True, Decimal(10)),
])
def test_depot_average_unknown_and_complete_keep_weighted_scope(preview, counts, complete, expected_average):
    c, e, cash, depot, areas = prepare(preview)
    body = receipt(depot, areas)
    body['lines'] = [dict(original_quality_name='Grand', net_weight_kg=kg, unit_price='30',
                          count=count, source_line=str(index), allocations=[])
                     for index, (kg, count) in enumerate(zip(('60', '40'), counts))]
    settlement = post(c, e, draft(c, e, body))
    # A different currency must not contaminate the TRY numerator or count.
    send(c, '/entities/'+e+'/reference-bootstrap', {'currency': 'USD'}, expected=200)
    usd = receipt(depot, areas)
    usd['currency'] = 'USD'
    post(c, e, draft(c, e, usd))
    view = c.get('/v1/entities/'+e+'/harvest').json()
    assert len(view['analytics']) == 2
    bucket = next(a for a in view['analytics'] if a['currency'] == 'TRY')
    assert bucket['total_depot_kg'] == '100.000'
    assert bucket['count_coverage_complete'] is complete
    assert (Decimal(bucket['average_weight_kg']) if bucket['average_weight_kg'] is not None else None) == expected_average
    assert settlement['count_coverage_complete'] is complete
    assert (Decimal(settlement['average_weight_kg']) if settlement['average_weight_kg'] is not None else None) == expected_average
    if expected_average is None:
        assert bucket['confirmed_kg'] == '0' and bucket['confirmed_count'] == 0
