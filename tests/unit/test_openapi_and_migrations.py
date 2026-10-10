from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_openapi_contract_parses_and_has_vertical_paths():
    spec = yaml.safe_load((ROOT / "openapi/owner_core_v1.yaml").read_text())
    assert spec["openapi"] == "3.1.0"
    paths = spec["paths"]
    assert "/obligations" in paths
    assert "/obligations/{id}/post" in paths
    assert "/payments" in paths
    assert "/payments/{id}/post" in paths
    assert "/entities/{entity_id}/finance-overview" in paths


def test_migration_chain_is_linear_and_importable():
    files = [
        ROOT / "alembic/versions/20261004_0001_architecture_foundation.py",
        ROOT / "alembic/versions/20261004_0002_finance_vertical.py",
        ROOT / "alembic/versions/20261004_0003_integrity_corrections.py",
        ROOT / "alembic/versions/20261004_0004_cash_settlements.py",
        ROOT / "alembic/versions/20261004_0005_advance_applications.py",
        ROOT / "alembic/versions/20261004_0006_cutover_history.py",
        ROOT / "alembic/versions/20261004_0007_reference_readiness.py",
        ROOT / "alembic/versions/20261004_0008_verified_opening_payable.py",
    ]
    mods = []
    for idx, path in enumerate(files):
        spec = importlib.util.spec_from_file_location(f"migration_{idx}", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        mods.append(module)
    assert mods[0].revision == "20261004_0001"
    assert mods[0].down_revision is None
    assert mods[1].revision == "20261004_0002"
    assert mods[1].down_revision == mods[0].revision
    assert mods[2].revision == "20261004_0003"
    assert mods[2].down_revision == mods[1].revision
    assert mods[3].revision == "20261004_0004"
    assert mods[3].down_revision == mods[2].revision
    assert mods[4].revision == "20261004_0005"
    assert mods[4].down_revision == mods[3].revision
    assert mods[5].revision == "20261004_0006"
    assert mods[5].down_revision == mods[4].revision
    assert mods[6].revision == "20261004_0007"
    assert mods[6].down_revision == mods[5].revision
    assert mods[7].revision == "20261004_0008"
    assert mods[7].down_revision == mods[6].revision


def test_sqlalchemy_metadata_imports_with_expected_tables():
    from greenhouse_owner_os.infrastructure.models import Base
    expected = {
        "core.entities", "core.actors", "core.counterparties",
        "finance.ledger_accounts", "finance.money_accounts", "finance.postings", "finance.posting_entries",
        "finance.obligations", "finance.payments", "finance.payment_allocations", "finance.advances",
        "audit.idempotency_keys", "audit.events",
    }
    assert expected.issubset(set(Base.metadata.tables))


def test_harvest_original_metadata_contract_matches_actual_read_route():
    from uuid import UUID
    from greenhouse_owner_os.api.app import create_app
    from greenhouse_owner_os.infrastructure.harvest_models import EvidenceRow

    spec = yaml.safe_load((ROOT / 'openapi/owner_core_v1.yaml').read_text())
    path = '/entities/{entity_id}/harvest/evidence/{evidence_id}/metadata'
    route = spec['paths'][path]
    assert set(route) == {'get'}
    operation = route['get']
    assert {p['name'] for p in operation['parameters']} == {'entity_id', 'evidence_id'}
    assert all(p['in'] == 'path' and p['required'] and p['schema']['format'] == 'uuid' for p in operation['parameters'])
    assert {'200', '401', '403', '404', '422'} <= set(operation['responses'])
    success = operation['responses']['200']
    assert success['headers']['Cache-Control']['schema']['const'] == 'private, no-store'
    assert success['content']['application/json']['schema']['$ref'] == '#/components/schemas/HarvestEvidenceMetadata'
    model = spec['components']['schemas']['HarvestEvidenceMetadata']
    fields = {column.name for column in EvidenceRow.__table__.columns if column.name != 'file_bytes'} | {'has_file', 'original_url'}
    # Typed owner forms add one optional read member without changing any
    # required legacy metadata, the binary route or the immutable SQL columns.
    assert set(model['properties']) == fields | {'structured_original'}
    assert set(model['required']) == fields
    assert model['properties']['structured_original']['anyOf'] == [
        {'$ref': '#/components/schemas/OwnerFormSource'}, {'type': 'null'}]
    typed = spec['components']['schemas']['OwnerFormSource']
    assert typed['additionalProperties'] is False
    assert set(typed['properties']) == set(typed['required']) == {
        'schema_version', 'form_kind', 'form_id', 'captured_at', 'timezone', 'fields'}
    assert typed['properties']['schema_version']['const'] == 1
    assert set(typed['properties']['form_kind']['enum']) == {'field_harvest', 'inventory_receipt'}
    assert typed['properties']['fields']['additionalProperties'] == {'type': 'string'}
    assert model['additionalProperties'] is False
    assert {item['type'] for item in model['properties']['raw_text']['anyOf']} == {'string', 'null'}
    assert {item['type'] for item in model['properties']['original_url']['anyOf']} == {'string', 'null'}
    assert model['properties']['has_file']['type'] == 'boolean'
    assert 'file_bytes' not in model['properties'] and 'file_base64' not in model['properties']
    # The original binary route remains a distinct, existing endpoint.
    assert path.removesuffix('/metadata') in spec['paths']
    app = create_app(finance_service=None, actor_provider=lambda: UUID(int=1))
    actual = app.openapi()
    # Match the complete retained contract, not only operationId: a bare route
    # previously hid its empty response schema from this focused test.
    assert actual['paths']['/v1' + path] == route
    assert actual['components']['schemas']['HarvestEvidenceMetadata'] == model
    assert actual['components']['schemas']['OwnerFormSource'] == typed
    actual_route = next(r for r in app.routes if getattr(r, 'path', None) == '/v1' + path)
    assert actual_route.response_model is None, 'OpenAPI documentation must not change existing payload serialization'
    assert actual_route.response_field is None
    assert set(actual_route.response_fields) == {200}
    validation = operation['responses']['422']['content']['application/json']['schema']
    assert validation == {'$ref': '#/components/schemas/HTTPValidationError'}


def test_finance_direct_expense_contract_is_additive_bounded_and_source_linked():
    spec = yaml.safe_load((ROOT / 'openapi/owner_core_v1.yaml').read_text())
    operation = spec['paths']['/entities/{entity_id}/finance-overview']['get']
    assert operation['responses']['200']['content']['application/json']['schema']['$ref'] == '#/components/schemas/FinanceOverview'
    model = spec['components']['schemas']['FinanceOverview']
    fields = model['properties']
    assert model['additionalProperties'] is True, 'Legacy Finance fields remain accepted'
    assert set(model['required']) == set(fields) == {'direct_expenses', 'direct_expenses_total', 'direct_expenses_limit',
        'direct_expenses_has_more', 'direct_expenses_order', 'direct_expenses_period'}
    assert fields['direct_expenses']['maxItems'] == fields['direct_expenses_limit']['const'] == 50
    assert fields['direct_expenses_total']['type'] == 'integer' and fields['direct_expenses_total']['minimum'] == 0
    assert fields['direct_expenses_has_more']['type'] == 'boolean'
    assert fields['direct_expenses_order']['const'] == 'created_at_desc_id_desc'
    assert fields['direct_expenses_period']['const'] == 'ALL_DATES'
    assert fields['direct_expenses']['items']['$ref'] == '#/components/schemas/DirectExpenseRead'
    row = spec['components']['schemas']['DirectExpenseRead']
    required = {'id', 'entity_id', 'proposal_id', 'source_id', 'amount', 'currency', 'event_date', 'money_account_id',
        'counterparty_id', 'description', 'status', 'version', 'posting_id', 'reversal_posting_id', 'created_by',
        'created_at', 'reversed_by', 'reversal_reason'}
    assert set(row['required']) == set(row['properties']) == required
    assert row['properties']['amount']['type'] == 'string'
    assert row['properties']['status']['enum'] == ['posted', 'reversed']
    assert row['properties']['source_id']['format'] == row['properties']['proposal_id']['format'] == 'uuid'
    assert not {'raw_text', 'file_bytes', 'file_base64'} & set(row['properties'])
    params = {p['name']: p for p in operation['parameters']}
    assert params['offset']['schema']['default'] == 0
    assert params['limit']['schema']['default'] == 50 and params['limit']['schema']['maximum'] == 100
