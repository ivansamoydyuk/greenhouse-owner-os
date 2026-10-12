from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from greenhouse_owner_os.domain.owner_capture import ExpenseRecoveryAction, MoneyProposal
from greenhouse_owner_os.domain.errors import ValidationError as DomainValidationError
from greenhouse_owner_os.services.owner_recovery import expense_revision


def prior():
    payload = MoneyProposal(amount='500', currency='TRY', description='Купил бензин за 500 TRY.').model_dump(mode='json')
    return dict(payload=payload, provenance={k: {'category': 'SOURCE FACT' if k in ('amount','currency','description') else 'UNKNOWN', 'source_id': 'retained-source'} for k in payload})


def test_only_actual_owner_edits_change_server_provenance():
    previous=prior(); snapshot=deepcopy(previous)
    fields,provenance=expense_revision(previous, {'description':'Бензин'})
    assert fields['amount']=='500' and fields['currency']=='TRY' and fields['counterparty_id'] is None
    assert provenance['amount']==previous['provenance']['amount']
    assert provenance['currency']==previous['provenance']['currency']
    assert provenance['description']=={'category':'OWNER INPUT'}
    assert previous==snapshot


def test_normalized_exact_amount_retains_provenance_and_unknown_stays_unknown():
    previous=prior()
    fields,provenance=expense_revision(previous, {'amount':'500.00'})
    assert fields['amount']=='500.00' and provenance['amount']==previous['provenance']['amount']
    assert provenance['counterparty_id']==previous['provenance']['counterparty_id']


@pytest.mark.parametrize('edit',[{'amount':1.5},{'provenance':{'amount':{'category':'SOURCE FACT'}}},{'currency':'GBP'}])
def test_untrusted_fact_categories_and_inexact_inputs_rejected(edit):
    with pytest.raises(DomainValidationError):expense_revision(prior(),edit)


@pytest.mark.parametrize('body',[{'origin_proposal_id':str(uuid4())},{'expected_version':1},{'origin_proposal_id':str(uuid4()),'expected_version':0}])
def test_selected_origin_requires_exact_positive_version(body):
    with pytest.raises(ValidationError):ExpenseRecoveryAction(**body)


def test_failed_source_has_no_fabricated_origin_or_default_currency():
    assert ExpenseRecoveryAction().origin_proposal_id is None
    unknown=MoneyProposal(currency=None)
    assert unknown.amount is None and unknown.currency is None and unknown.event_date is None and unknown.description is None


def test_recovery_openapi_matches_live_request_contract():
    from pathlib import Path
    import yaml
    from greenhouse_owner_os.api.app import create_app
    runtime=create_app(finance_service=None,actor_provider=lambda:None).openapi()
    saved=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    path='/v1/entities/{entity_id}/owner/captures/{item_id}/direct-expense-recovery'
    assert saved['paths'][path.removeprefix('/v1')]==runtime['paths'][path]
    assert saved['components']['schemas']['ExpenseRecoveryAction']==runtime['components']['schemas']['ExpenseRecoveryAction']


def test_recovery_ci_gate_keeps_prior_gates_and_requires_own_native_and_pg_proof():
    from pathlib import Path
    import yaml
    workflow=yaml.load(Path('.github/workflows/postgresql-verification.yml').read_text(),Loader=yaml.BaseLoader)
    branches=workflow['on']['push']['branches']
    assert all(branch in branches for branch in ('06-owner-direct-expense-recovery','06-owner-personal-task-access','06-owner-source-queue-errors'))
    browser=[step.get('run') for step in workflow['jobs']['owner-r2-browser']['steps']]
    assert all(command in browser for command in ('python scripts/run_owner_e2e.py','python scripts/run_source_queue_e2e.py','python scripts/run_personal_tasks_e2e.py','python scripts/run_expense_recovery_e2e.py'))
    checks='\n'.join(step.get('run','') for step in workflow['jobs']['verify']['steps'])
    assert "assert sum('test_postgres_owner_expense_recovery' in case.get('classname', '') for case in cases) >= 54" in checks
    assert "assert revisions == ['20261012_0034']" in checks
    assert "assert not any(case.find('skipped') is not None for case in cases), 'PostgreSQL tests skipped'" in checks
    assert "assert not any(case.find('skipped') is not None for case in cases), 'Finance tests skipped'" in checks
