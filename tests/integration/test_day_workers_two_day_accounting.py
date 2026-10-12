"""Exact owner example: five people including the leader on each of two dates."""
from datetime import date
from decimal import Decimal
import json
from uuid import UUID, uuid4

from greenhouse_owner_os.domain.commands import AllocationInput, CreatePaymentCommand
from greenhouse_owner_os.domain.day_workers import ActualInput
from tests.integration.test_postgres_day_workers import (
    accrue, accrual_body, day, decide, physical, request, source,
)
from tests.integration.test_postgres_readiness import preview


def test_five_including_leader_each_of_two_days_2500_rate_10000_payment(day):
    i = day
    dates = ('2026-10-11', '2026-10-12')
    work = 'TEST five people total including one team leader, each of two days'
    original, _ = request(i, people='5', days='2', work=work, start_date=dates[0])
    assert original['requested_people_days'] == 10
    assert physical(i) == [0, 0, 0, 0]
    approved = decide(i, original)
    assert approved['planned_people_days'] == 10
    assert physical(i) == [0, 0, 0, 0]

    actuals = []
    for event_date in dates:
        body = ActualInput(
            greenhouse_id=i['g'], request_id=UUID(original['id']),
            source=source(
                'actual', event_date=event_date, people='5', work=work,
                full_day='true', note='TEST five includes the leader; do not add a sixth person',
                allocations=json.dumps([
                    dict(greenhouse_id=str(i['g']), work_area_id=None, people=5),
                ]),
            ),
        )
        actuals.append(i['svc'].actual(i['e'], i['wa'], str(uuid4()), body, i['w']))
        assert physical(i) == [0, 0, 0, 0]
    observed = i['svc'].get_request(i['e'], i['a'], UUID(original['id']))
    assert observed['original']['fields'] == original['original']['fields']
    assert [(a['event_date'], a['people'], a['people_days']) for a in observed['actuals']] == [
        (dates[0], 5, 5), (dates[1], 5, 5),
    ]
    assert observed['planned_people_days'] == observed['actual_people_days'] == 10

    accrued = [accrue(i, actual, accrual_body(
        i, daily_rate='2500',
        rate_basis='TEST owner explicitly verified2500TRY per person per full day on2026-10-11 and2026-10-12',
    )) for actual in actuals]
    obligation_ids = {a['obligation_id'] for a in accrued}
    assert len(obligation_ids) == 2
    assert [a['actual']['accrual']['original_amount'] for a in accrued] == ['12500.00', '12500.00']
    assert physical(i) == [2, 0, 2, 2]

    def finance_view():
        response = i['c'].get(f"/v1/entities/{i['e']}/finance-overview")
        assert response.status_code == 200, response.text
        return response.json()

    def cockpit():
        response = i['c'].get(f"/v1/entities/{i['e']}/greenhouse-daily", params=dict(
            date_from=dates[0], date_to=dates[1], currency='TRY', greenhouse_id=str(i['g']),
        ))
        assert response.status_code == 200, response.text
        return response.json()['selected']

    before = finance_view()
    assert sum(Decimal(event['amount']) for event in before['economics']) == Decimal('25000')
    assert cockpit()['finance']['operating_cost'] == '25000.00'
    obligations = [o for o in before['obligations'] if o['obligation_id'] in obligation_ids]
    assert len(obligations) == 2
    assert sorted(o['event_date'] for o in obligations) == list(dates)
    assert all(o['original_amount'] == '12500.00' for o in obligations)

    payment = i['finance'].create_payment(
        actor_id=i['a'], idempotency_key='two-day-explicit-payment',
        command=CreatePaymentCommand(
            entity_id=i['e'], counterparty_id=i['cp'], money_account_id=i['cash'],
            direction='out', amount='10000', currency='TRY', event_date=date(2026, 10, 12),
            allocations=(AllocationInput(obligation_id=UUID(accrued[0]['obligation_id']), amount='10000'),),
        ),
    )
    assert physical(i) == [2, 1, 2, 2]  # A payment draft is not a posted payout.
    assert finance_view()['economics'] == before['economics']
    i['finance'].post_payment(
        actor_id=i['a'], idempotency_key='two-day-explicit-payment-post',
        payment_id=UUID(payment['id']), expected_version=1,
    )
    after = finance_view()
    remaining = [Decimal(o['remaining_amount']) for o in after['obligations'] if o['obligation_id'] in obligation_ids]
    assert sorted(remaining) == [Decimal('2500'), Decimal('12500')]
    assert sum(remaining) == Decimal('15000')
    assert after['economics'] == before['economics']
    assert after['business_balances']['TRY']['operational_payable'] == '15000.00'
    assert physical(i) == [2, 1, 3, 2]
    selected = cockpit()
    assert selected['finance']['operating_cost'] == selected['finance']['exact_cost'] == '25000.00'
    facts = selected['work']['day_workers']
    assert facts['reported_people_days'] == facts['confirmed_people_days'] == 10
    assert facts['accrued_amount'] == '25000.00'
    assert facts['paid_amount'] == '10000.00'
    assert facts['current_debt'] == '15000.00'
    assert not facts['payment_allocation_unknown']
    reconciliation = i['finance'].reconcile(entity_id=i['e'])
    assert reconciliation['ok'] and reconciliation['findings'] == []
