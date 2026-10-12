"""Published contract retains strict, versioned owner intent, never effect payloads."""
from pathlib import Path
import yaml
from greenhouse_owner_os.domain.owner_capture import MoneyRecoveryAction


def test_money_recovery_contract_matches_strict_runtime_dto():
    spec=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    path='/entities/{entity_id}/owner/captures/{item_id}/money-recovery'
    operation=spec['paths'][path]['post']
    assert operation['requestBody']['content']['application/json']['schema']=={'$ref':'#/components/schemas/MoneyRecoveryAction'}
    assert spec['components']['schemas']['MoneyRecoveryAction']==MoneyRecoveryAction.model_json_schema()
    parameters={p['name']:p for p in operation['parameters']}
    assert parameters['Idempotency-Key']['required'] is True
    assert all(parameters[p]['required'] and parameters[p]['schema']['format']=='uuid' for p in ('entity_id','item_id'))
    schema=spec['components']['schemas']['MoneyRecoveryAction']
    assert schema['additionalProperties'] is False
    assert set(schema['required'])=={'expected_version','origin_proposal_id','intent'}
    assert set(schema['properties']['intent']['enum'])=={'operating_payable','paid_expense','debt_payment'}
    assert not any(k in schema['properties'] for k in ('amount','counterparty_id','money_candidates','payment_account','owner_id'))
