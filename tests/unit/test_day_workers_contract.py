"""Bounded original forms reject invented day/rate and opaque extra fields."""
from uuid import uuid4
import json
import pytest
from pydantic import ValidationError
from greenhouse_owner_os.domain.day_workers import RequestInput,ActualInput,AccrualInput,DecisionInput


def original(kind,fields):
    return dict(schema_version=1,form_kind='day_'+kind,form_id=uuid4(),captured_at='2026-10-12T09:00:00+03:00',timezone='Europe/Istanbul',fields=fields)


def test_three_required_request_values_optional_date_stays_unknown():
    b=RequestInput(greenhouse_id=uuid4(),source=original('request',dict(people='05',days='2',work='کار',start_date='',note='')))
    assert b.source.fields['people']=='05' and b.source.fields['start_date']==''
    data=b.model_dump(mode='json');data['source']['fields']['extra']='invented'
    with pytest.raises(ValidationError):RequestInput.model_validate(data)
    data=b.model_dump(mode='json');data['source']['captured_at']='2026-10-12T09:00:00'
    with pytest.raises(ValidationError):RequestInput.model_validate(data)


@pytest.mark.parametrize('value',['', '-1','0','1.5','true','1e2'])
def test_request_people_must_be_explicit_positive_integer(value):
    with pytest.raises(ValidationError):RequestInput(greenhouse_id=uuid4(),source=original('request',dict(people=value,days='2',work='work',start_date='',note='')))


def test_actual_allocation_cannot_duplicate_total_or_invent_fraction():
    g=uuid4();fields=dict(event_date='2026-10-12',people='4',work='work',full_day='true',note='',allocations=json.dumps([dict(greenhouse_id=str(g),people=5)]))
    with pytest.raises(ValidationError):ActualInput(greenhouse_id=g,source=original('actual',fields))
    fields['allocations']=json.dumps([dict(greenhouse_id=str(g),people=2.5)])
    with pytest.raises(ValidationError):ActualInput(greenhouse_id=g,source=original('actual',fields))
    fields['allocations']='[]';b=ActualInput(greenhouse_id=g,source=original('actual',fields));assert b.source.fields['allocations']=='[]'


@pytest.mark.parametrize('rate',['','0','2500 inferred','-5','1.001','1e3'])
def test_no_default_rate_or_unbounded_precision(rate):
    fields=dict(counterparty_id=str(uuid4()),daily_rate=rate,currency='TRY',expense_account_id=str(uuid4()),rate_basis='Owner confirmed actual date',due_date='')
    with pytest.raises(ValidationError):AccrualInput(confirmed=True,source=original('accrual',fields))


def test_reject_and_clarify_require_reason_and_no_cancellation_substitute():
    fields=dict(decision='CLARIFY',people='',days='',work='',start_date='',note='',reason='')
    with pytest.raises(ValidationError):DecisionInput(expected_version=1,source=original('decision',fields))
    fields.update(decision='CANCEL',reason='wrong verb')
    with pytest.raises(ValidationError):DecisionInput(expected_version=1,source=original('decision',fields))


@pytest.mark.parametrize('value',['today','infinity','-infinity','20261012','2026-W42-1','2026-2-3','2026-02-30'])
@pytest.mark.parametrize('field',['start_date','event_date','due_date'])
def test_explicit_calendar_source_rejects_relative_special_and_noncanonical_dates(value,field):
    if field=='start_date':
        cls=RequestInput;kwargs=dict(greenhouse_id=uuid4(),source=original('request',dict(people='5',days='2',work='work',start_date=value,note='')))
    elif field=='event_date':
        cls=ActualInput;kwargs=dict(greenhouse_id=uuid4(),source=original('actual',dict(people='4',event_date=value,work='work',full_day='true',note='',allocations='[]')))
    else:
        cls=AccrualInput;kwargs=dict(confirmed=True,source=original('accrual',dict(counterparty_id=str(uuid4()),daily_rate='100',currency='TRY',expense_account_id=str(uuid4()),rate_basis='Explicit owner review',due_date=value)))
    with pytest.raises(ValidationError):cls(**kwargs)


def test_explicit_zero_actual_is_valid_without_inventing_attendance_or_day_basis():
    b=ActualInput(greenhouse_id=uuid4(),source=original('actual',dict(people='0',event_date='2026-10-12',work='Nobody attended',full_day='unknown',note='',allocations='[]')))
    assert b.source.fields['people']=='0' and b.source.fields['full_day']=='unknown'


def test_actual_model_has_unique_openapi_identity_without_renaming_fertilizer():
    from pathlib import Path
    import yaml
    from greenhouse_owner_os.api.app import create_app
    from greenhouse_owner_os.domain.day_workers import DayWorkersActualInput
    from greenhouse_owner_os.domain.fertilizer import ActualInput as FertilizerActualInput
    assert ActualInput is DayWorkersActualInput and ActualInput is not FertilizerActualInput
    live=create_app(finance_service=None,actor_provider=lambda:None).openapi()
    saved=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    for prefix in ('/entities/{entity_id}','/worker'):
        path=prefix+'/day-workers/actuals'
        reference=live['paths']['/v1'+path]['post']['requestBody']['content']['application/json']['schema']
        assert reference=={'$ref':'#/components/schemas/DayWorkersActualInput'}
        assert saved['paths'][path]==live['paths']['/v1'+path]
    for name in ('ActualInput','DayWorkersActualInput'):
        assert saved['components']['schemas'][name]==live['components']['schemas'][name]
    assert not any(name.startswith('greenhouse_owner_os__domain__') and name.endswith('ActualInput') for name in live['components']['schemas'])
