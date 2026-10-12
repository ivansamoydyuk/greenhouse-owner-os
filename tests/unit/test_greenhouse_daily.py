"""The cockpit retains source scope, missingness and authoritative money bases."""
from datetime import date
from decimal import Decimal
from uuid import UUID
import pytest
from greenhouse_owner_os.domain.errors import ValidationError
from greenhouse_owner_os.services.finance_analytics import FinanceAnalyticsService
from greenhouse_owner_os.services.greenhouse_daily import (
    comparison_window, period_months, verified_area, selected_line, quality_summary,
    day_worker_money, field_metric, upcoming_application,
)


def uid(i): return str(UUID(int=i))


def areas():
    return {uid(1): dict(id=uid(1), kind='greenhouse', parent_id=None, name='One'),
            uid(2): dict(id=uid(2), kind='part', parent_id=uid(1), name='Part A'),
            uid(3): dict(id=uid(3), kind='part', parent_id=uid(1), name='Part B'),
            uid(4): dict(id=uid(4), kind='greenhouse', parent_id=None, name='Two')}


def reference(i, area, value, day='2026-10-01'):
    return dict(id=uid(i), work_area_id=uid(area), area_da=value, effective_from=day, verification='Explicit synthetic owner reference')


def test_area_requires_all_parts_and_whole_period_validity_not_policy_weights():
    start, end = date(2026, 10, 1), date(2026, 10, 12)
    result = verified_area(uid(1), areas(), [], start, end)
    assert result['area_da'] is None and result['status'] == 'UNKNOWN'
    refs = [reference(10, 2, '3'), reference(11, 3, '7')]
    assert verified_area(uid(1), areas(), refs, start, end)['area_da'] == '10'
    # A current reference cannot retroactively establish an earlier denominator.
    assert verified_area(uid(1), areas(), refs, date(2026, 9, 30), end)['area_da'] is None
    refs.append(reference(12, 3, '8', '2026-10-05'))
    changed = verified_area(uid(1), areas(), refs, start, end)
    assert changed['status'] == 'CHANGED_IN_PERIOD' and changed['area_da'] is None
    assert verified_area(uid(1), areas(), refs, date(2026, 10, 5), end)['area_da'] == '11'


def test_whole_greenhouse_reference_and_same_value_revision_are_valid():
    refs = [reference(10, 1, '20'), reference(11, 1, '20', '2026-10-04')]
    result = verified_area(uid(1), areas(), refs, date(2026, 10, 1), date(2026, 10, 12))
    assert result['area_da'] == '20' and result['reference_ids'] == [uid(10), uid(11)]


def test_comparison_same_elapsed_days_no_future_and_leap_season_overrun():
    window, reason = comparison_window(date(2026, 10, 1), date(2027, 9, 30), date(2026, 10, 12))
    assert reason is None
    assert window == (date(2026, 10, 1), date(2026, 10, 12), date(2025, 10, 1), date(2025, 10, 12))
    window, _ = comparison_window(date(2023, 10, 1), date(2024, 9, 30), date(2024, 10, 1))
    assert window[1] == date(2024, 9, 29)
    assert (window[1] - window[0]).days == (window[3] - window[2]).days
    assert comparison_window(date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 12))[1] == 'PERIOD_SPANS_SEASONS'
    assert comparison_window(date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 12))[1] == 'PERIOD_HAS_NOT_ELAPSED'


def test_quality_uses_selected_allocations_never_full_receipt_or_field_branches():
    line = dict(id=uid(10), net_weight_kg='100', amount='2500', allocations=[dict(work_area_id=uid(2), net_weight_kg='20', amount='500')])
    estimates = [dict(line_id=uid(10), work_area_id=uid(3), net_weight_kg='30', amount='750'),
                 dict(line_id=uid(10), work_area_id=uid(4), net_weight_kg='40', amount='1000'),
                 dict(line_id=uid(10), work_area_id=None, net_weight_kg='10', amount='250')]
    scoped = selected_line(line, estimates, uid(1), areas())
    assert scoped['net_weight_kg'] == '50.000' and scoped['amount'] == '1250.00'
    assert scoped['unallocated_kg'] == '10.000'
    assert selected_line(line, estimates, uid(1), areas(), 'exact')['net_weight_kg'] == '20.000'
    assert selected_line(line, estimates, uid(1), areas(), 'estimated')['net_weight_kg'] == '30.000'


def test_weighted_prices_unknown_quality_and_currency_denominator_are_preserved():
    events = [dict(currency='TRY', lines=[dict(id=uid(10), quality='FIRST', net_weight_kg='10', amount='100'),
                                         dict(id=uid(11), quality='FIRST', net_weight_kg='30', amount='900'),
                                         dict(id=uid(12), quality='UNKNOWN', net_weight_kg='10', amount='500')]),
              dict(currency='EUR', lines=[dict(id=uid(13), quality='FIRST', net_weight_kg='50', amount='999')])]
    result = {x['quality']: x for x in quality_summary(events, 'TRY')}
    assert result['FIRST']['weighted_price_per_kg'] == '25.0000'  # 1000 / 40, not mean(10,30)
    assert result['FIRST']['net_weight_kg'] == '90.000' and result['FIRST']['priced_kg'] == '40.000'
    assert result['UNKNOWN']['share_percent'] == '10.0000'
    assert sum(Decimal(x['net_weight_kg']) for x in result.values()) == 100
    assert result['HIGHEST']['weighted_price_per_kg'] is None


def financial_document(oid, assigned, remaining='300', history=None):
    return dict(obligation_id=oid, status='posted', direction='payable', original_amount='700', remaining_amount=remaining,
                attributions=[dict(greenhouse_id=g, amount=n) for g, n in assigned], history=history or [])


def test_accrual_is_cost_once_payment_separate_and_current_debt_all_dates():
    payment = dict(id=uid(12), kind='payment', status='posted', event_date='2026-10-12', amount='400')
    doc = financial_document(uid(10), [(uid(1), '700')], history=[payment])
    older = financial_document(uid(11), [(uid(1), '700')], remaining='200')
    finance = dict(currency='TRY', economic_events=[dict(classification='OPERATING_COST', amount='700', source=dict(kind='obligation', id=uid(10)), allocations=doc['attributions'])], debts=dict(documents=[doc, older]))
    result = day_worker_money(dict(greenhouse_id=uid(1), obligation_ids=[uid(10)], current_obligation_ids=[uid(10), uid(11)]), finance, date(2026,10,1), date(2026,10,12))
    assert result['accrued_amount'] == '700.00' and result['paid_amount'] == '400.00' and result['current_debt'] == '500.00'
    assert result['cost_basis'] == 'EXISTING_FINANCE_COST_ALREADY_INCLUDED'


def test_mixed_payable_does_not_copy_cost_or_payment_or_debt_to_each_greenhouse():
    payment = dict(id=uid(12), kind='payment', status='posted', event_date='2026-10-12', amount='400')
    doc = financial_document(uid(10), [(uid(1), '400'), (uid(4), '300')], history=[payment])
    finance = dict(currency='TRY', economic_events=[dict(classification='OPERATING_COST', amount='700', source=dict(kind='obligation', id=uid(10)), allocations=doc['attributions'])], debts=dict(documents=[doc]))
    result = day_worker_money(dict(greenhouse_id=uid(1), obligation_ids=[uid(10)]), finance, date(2026,10,1), date(2026,10,12))
    assert result['accrued_amount'] == '400.00'
    assert result['paid_amount'] is None and result['current_debt'] is None
    assert result['payment_allocation_unknown'] is True


def test_no_field_data_is_not_zero_and_missing_count_is_measurable():
    assert field_metric([])['value'] is None
    report = dict(cut_count=None, evidence_id=uid(1))
    assert field_metric([report])['classification'] == 'PARTIAL'
    result = field_metric([report, dict(cut_count=7, evidence_id=uid(2))])
    assert result['value'] == '7' and result['missing_count'] == 1 and result['source_count'] == 2


def test_internal_snapshot_flag_cannot_downgrade_a_finance_read():
    class Session:
        def __init__(self, isolation='read committed', readonly='off'):
            self.values = [isolation, readonly]; self.executed = []
        def scalar(self, statement): return self.values.pop(0)
        def execute(self, statement): self.executed.append(str(statement))
    class Uow:
        def __init__(self, session): self.session = session
    svc = FinanceAnalyticsService(lambda: None, snapshot_owned=True)
    with pytest.raises(ValidationError): svc._snapshot(Uow(Session()))
    with pytest.raises(ValidationError): svc._snapshot(Uow(Session('repeatable read', 'off')))
    svc._snapshot(Uow(Session('repeatable read', 'on')))
    ordinary = FinanceAnalyticsService(lambda: None); session = Session()
    ordinary._snapshot(Uow(session))
    assert session.executed == ['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY']


def test_upcoming_applications_use_authoritative_fertilizer_states():
    today = date(2026, 10, 12)
    for state in ('APPLIED', 'CLOSED', 'ACTUAL_CORRECTED', 'ACTUAL_REVERSED', 'CORRECTION_PENDING'):
        assert not upcoming_application(dict(status=state, event_date=str(today)), today)
    for state in ('DRAFT', 'APPROVED', 'PARTIALLY_APPLIED', 'REVIEWED_NOT_APPLIED'):
        assert upcoming_application(dict(status=state, event_date=str(today)), today)
        assert not upcoming_application(dict(status=state, event_date='2026-10-11'), today)


def test_month_buckets_follow_selected_range_across_october_boundary():
    assert period_months(date(2026, 9, 1), date(2026, 10, 12)) == ['2026-09', '2026-10']
    assert period_months(date(2026, 10, 1), date(2027, 9, 30)) == [f'2026-{m:02}' for m in range(10, 13)] + [f'2027-{m:02}' for m in range(1, 10)]


@pytest.mark.parametrize('remaining,history,expected_debt,expected_paid', [
    ('700', [], '400.00', '0.00'),
    ('0', [], '0.00', '0.00'),
    ('0', [dict(id=uid(12), kind='payment', status='posted', event_date='2026-10-12', amount='700')], '0.00', None),
    ('300', [dict(id=uid(12), kind='payment', status='posted', event_date='2026-10-10', amount='400')], None, '0.00'),
    ('300', [dict(id=uid(12), kind='payment', status='posted', event_date='2026-10-12', amount='400')], None, None),
])
def test_mixed_payable_debt_known_at_unpaid_and_settled_boundaries(remaining, history, expected_debt, expected_paid):
    doc = financial_document(uid(10), [(uid(1), '400'), (uid(4), '300')], remaining=remaining, history=history)
    finance = dict(currency='TRY', economic_events=[], debts=dict(documents=[doc]))
    result = day_worker_money(dict(greenhouse_id=uid(1), obligation_ids=[uid(10)]), finance, date(2026, 10, 12), date(2026, 10, 12))
    assert result['current_debt'] == expected_debt and result['paid_amount'] == expected_paid
    assert result['debt_allocation_unknown'] is (expected_debt is None)
    assert result['payment_allocation_unknown'] is (expected_paid is None)


def test_mixed_direct_settlement_uncertainty_does_not_invent_cash_payment():
    history = [dict(id=uid(12), kind='direct_settlement', status='posted', event_date='2026-10-12', amount='400')]
    doc = financial_document(uid(10), [(uid(1), '400'), (uid(4), '300')], history=history)
    finance = dict(currency='TRY', economic_events=[], debts=dict(documents=[doc]))
    result = day_worker_money(dict(greenhouse_id=uid(1), obligation_ids=[uid(10)]), finance, date(2026, 10, 12), date(2026, 10, 12))
    assert result['paid_amount'] == '0.00' and result['current_debt'] is None
    assert result['settled_without_payment_amount'] is None
    assert result['settlement_allocation_unknown'] is True
