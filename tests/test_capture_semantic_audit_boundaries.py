"""Exposed AUD-001/002 regressions, frozen before implementation.

These bounded synthetic cases are not a new unseen language benchmark.
PostgreSQL effect assertions live in the separate integration module.
"""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from greenhouse_owner_os.domain.owner_capture import DTO
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.services.owner_preparation import prepare_owner_source


FIXTURES = Path(__file__).parent / 'fixtures'
REFERENCES = json.loads((FIXTURES / 'capture_semantic_references.json').read_text())
CASES = [json.loads(line) for name in ('capture_semantic_audit_regressions.jsonl',
         'capture_semantic_reviewer_regressions.jsonl')
         for line in (FIXTURES / name).read_text().splitlines()]
DAY = date(2026, 10, 9)


def prepare(source):
    slots = prepare_owner_source(source, today=DAY, refs=REFERENCES)
    for kind, payload, _ in slots:
        DTO[kind].model_validate(payload)
    return slots


def project(slots, detailed=False):
    result = dict(stock_receipts=[], stock_applications=[])
    products = {p['id']: p['name'] for p in REFERENCES['products']}
    locations = {p['id']: p['name'] for p in REFERENCES['locations']}
    for kind, payload, _ in slots:
        key = {'STOCK_RECEIPT': 'stock_receipts',
               'STOCK_APPLICATION': 'stock_applications'}.get(kind)
        if not key:
            continue
        for line in payload['lines']:
            row = dict(product=products.get(line['product_id']), quantity=line['quantity'])
            if detailed:
                row.update(unit=line['unit'], location=locations.get(line['location_id']),
                           event_date=payload['event_date'])
            result[key].append(row)
    return {key: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
            for key, rows in result.items()}


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_frozen_semantic_boundary(case):
    slots = prepare(case['text'])
    expected = case['expected']
    actual = project(slots, detailed=case['id'].startswith('REV-'))
    if expected.get('safety_only'):
        assert not actual['stock_receipts']
        assert all(line['quantity'] is None for line in actual['stock_applications'])
    else:
        for key, rows in actual.items():
            assert rows == sorted(expected[key], key=lambda row: json.dumps(row, sort_keys=True))
    for kind, payload, provenance in slots:
        if kind == 'INVOICE_MATCH':
            assert not payload.get('receipt_id') and not payload.get('finance_obligation_id')
        if kind == 'STOCK_APPLICATION' and expected.get('clarify_total'):
            assert provenance['lines']['quantity_evidence']
            assert any(e['role'] == 'RATE' for e in provenance['lines']['quantity_evidence'])
    if expected.get('invoice_route'):
        assert any(kind in ('INVOICE_MATCH', 'UNRESOLVED') for kind, _, _ in slots)


@pytest.mark.parametrize('case_id', ['AUD001-01', 'AUD001-15', 'REV-17'])
def test_invoice_known_facts_survive_as_nonposting_evidence(case_id):
    case = next(case for case in CASES if case['id'] == case_id)
    slots = prepare(case['text'])
    kind, payload, provenance = next(slot for slot in slots
                                    if slot[0] in ('INVOICE_MATCH', 'UNRESOLVED'))
    assert payload['note'] == case['text']
    assert provenance['note']['source_text'] == case['text']
    evidence = provenance['note']['nonposting_inventory_evidence']
    assert len(evidence) == 1
    assert evidence[0]['original_product_name'] == 'Atocrop'
    assert evidence[0]['quantity'] == ('37' if case_id == 'REV-17' else '100')
    assert evidence[0]['unit'] == 'kg'
    assert evidence[0]['product_id'] and evidence[0]['location_id']
    assert provenance['note']['no_new_arrival'] is True


def validated_missing(source):
    slots = prepare(source)
    kind, payload, _ = next(slot for slot in slots if slot[0] == 'STOCK_APPLICATION')
    entity = '20000000-0000-0000-0000-000000000001'
    def rows(_u, sql, **params):
        if 'FROM inventory.stock' in sql:
            return [dict(quantity='100', uncosted_quantity='100', known_cost='0')]
        if 'max(seq)' in sql:
            return [dict(seq=1)]
        for table, key in [('inventory.products', 'products'),
                           ('inventory.locations', 'locations'), ('core.work_areas', 'areas')]:
            if 'FROM ' + table in sql:
                return [dict(row, entity_id=entity) for row in REFERENCES[key]
                        if str(row['id']) == str(params['id'])]
        raise AssertionError(sql)
    with patch('greenhouse_owner_os.services.owner_capture.rows', rows):
        fields, missing, _ = OwnerCaptureService(lambda: None)._validate(
            SimpleNamespace(), dict(action_type=kind, entity_id=entity), payload)
    return fields, missing


def test_typed_review_explains_rate_area_and_unknown_total():
    source = next(case['text'] for case in CASES if case['id'] == 'AUD002-02')
    fields, missing = validated_missing(source)
    assert fields['lines'][0]['quantity'] is None
    assert fields['notes'] == source
    assert len(missing) == 1
    assert 'Укажите общее количество' in missing[0]
    assert 'доза на площадь, не общий расход' in missing[0]
    assert '0,5' in missing[0] and '10 донюм' in missing[0]


def test_explicit_conflict_keeps_both_values_and_blocks_actual_total():
    source = next(case['text'] for case in CASES if case['id'] == 'AUD002-11')
    _, payload, provenance = prepare(source)[0]
    assert len(payload['lines']) == 1 and payload['lines'][0]['quantity'] is None
    assert payload['lines'][0]['original_quantity'] in source
    evidence = provenance['lines']['quantity_evidence']
    assert [entry['quantity'] for entry in evidence] == ['0.5', '6']
    assert evidence[0]['area']['quantity'] == '10'
    assert evidence[1]['consistency_check']['conflict'] is True
    fields, missing = validated_missing(source)
    assert fields['notes'] == source
    assert '6 кг' in missing[0] and '0,5 кг' in missing[0]


@pytest.mark.parametrize('denominator', ['0', '0.0', '0,0', '-1', '+1', '1e2', '999999999999999999999999999999'])
def test_invalid_rate_denominator_stays_unknown_without_arithmetic_error(denominator):
    source = ('Внесли 0,5 кг Atocrop/' + denominator + ' da, площадь 10 da Имамлы '
              'со склада Имамлы, всего внесли 5 кг Atocrop.')
    actual = project(prepare(source))
    assert actual['stock_applications']
    assert all(line['quantity'] is None for line in actual['stock_applications'])


def test_explicit_actual_total_never_posts_derived_number():
    source = next(case['text'] for case in CASES if case['id'] == 'AUD002-10')
    fields, missing = validated_missing(source)
    assert fields['lines'][0]['quantity'] == '5'
    assert not missing
    assert fields['notes'] == source


def test_absence_of_arrival_does_not_negate_actual_application():
    source = 'Внесли 5 кг Atocrop в Имамлы со склада Имамлы. Нового прихода не было.'
    slots = prepare(source)
    assert project(slots) == dict(stock_receipts=[], stock_applications=[dict(product='Atocrop', quantity='5')])
    assert slots[0][1]['notes'] == source


DIFF_CASES = [json.loads(line) for name in ('capture_semantic_diff_review_regressions.jsonl',
              'capture_semantic_scope_review_regressions.jsonl')
              for line in (FIXTURES / name).read_text().splitlines()]


@pytest.mark.parametrize('case', DIFF_CASES, ids=lambda case: case['id'])
def test_reviewed_event_scope_boundaries(case):
    slots = prepare(case['text'])
    actual = project(slots, detailed=True)
    if case['id'] in ('DIFF-01', 'DIFF-02'):
        assert actual['stock_receipts'] == [dict(product='Atocrop', quantity='100', unit='kg',
            location='Склад Имамлы', event_date='2026-10-08' if case['id'] == 'DIFF-02' else '2026-10-09')]
        assert not actual['stock_applications']
    elif case['id'] in ('DIFF-03', 'DIFF-04'):
        applications = actual['stock_applications']
        assert len(applications) == 2
        assert next(row for row in applications if row['location'] == 'Склад Имамлы')['quantity'] is None
        assert next(row for row in applications if row['location'] == 'Склад Озвади')['quantity'] == ('5' if case['id'] == 'DIFF-03' else '6')
    elif case['id'] in ('DIFF-05', 'DIFF-06'):
        assert len(actual['stock_applications']) == 1
        assert actual['stock_applications'][0]['quantity'] is None
    elif case['id'] == 'DIFF-07':
        assert project(slots) == dict(stock_receipts=[], stock_applications=[dict(product='Atocrop', quantity='5')])
    else:
        assert case['id'] == 'DIFF-08'
        assert len(actual['stock_applications']) == 2
        assert sorted(str(row['quantity']) for row in actual['stock_applications']) == ['5', 'None']
        evidence = slots[0][2]['lines']['quantity_evidence']
        assert [entry['scope']['part'] for entry in evidence] == ['первой части', 'второй части']
        assert all('work_area_id' not in entry['scope'] for entry in evidence)
