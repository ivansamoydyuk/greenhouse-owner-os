"""Synthetic pilot fixture keeps real owner opening data out of connected TEST."""
from uuid import UUID
from fastapi.testclient import TestClient
from sqlalchemy import text

from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.services.references import ReferenceService, provision_authenticated_owner
from greenhouse_owner_os.synthetic_pilot import OWNER_NAME, OWNER_REF, setup_synthetic_profile


def test_synthetic_seed_replays_normal_services_without_real_owner_opening(migrated_database):
    sf = make_session_factory(migrated_database)
    actor = provision_authenticated_owner(sf, external_ref=OWNER_REF, display_name=OWNER_NAME, environment='TEST_PREVIEW')
    factory = lambda:SqlAlchemyUnitOfWork(sf)
    app = create_app(finance_service=FinanceService(factory), reference_service=ReferenceService(factory,environment='TEST_PREVIEW'), actor_provider=lambda:actor)
    with TestClient(app) as client:
        profile = setup_synthetic_profile(client, factory, actor)
        assert setup_synthetic_profile(client, factory, actor) == profile
        eid = profile['entity']['id']; root = '/v1/entities/' + eid
        assert profile['synthetic_only'] and profile['owner_opening_loaded'] is False
        assert profile['greenhouse_warehouse_mapping'] is None and profile['physical_area_mapping'] is None
        assert len(profile['locations']) == 2 and profile['locations'][0] == profile['location']
        assert len({location['id'] for location in profile['locations']}) == 2
        assert profile['entity']['classification'] == 'TEST_PREVIEW'
        assert client.get(root+'/cutover').json()['opening_policy'] == 'ZERO'
        finance = client.get(root+'/finance-overview').json()
        assert finance['balances']['TRY'] == dict(cash='5000.00', payable='1200.00', receivable='800.00',advance_paid='0.00',advance_received='0.00')
        assert all(o['opening'] is None for o in finance['obligations'])
        assert {a['money_account_id'] for a in finance['accounts']} == {a['id'] for a in profile['money_accounts']}
        assert all(a['original_name'].startswith('TEST ') for a in profile['money_accounts'])
        assert client.get(root+'/readiness').json()['ok']
        inventory = client.get(root+'/inventory').json()
        assert inventory['stock'] == [] and inventory['opening_state'] == 'UNINITIALIZED_VERIFY'
        assert inventory['invariants']['ok']
        personal = client.get(root+'/owner/tasks').json()
        assert personal['total'] == 5 and {t['id'] for t in personal['items']} == {t['task_id'] for t in profile['personal_tasks']}
        worker = profile['worker_assignment_fixture']
        task = client.get(root+'/tasks/'+worker['task_id']).json()
        assert task['status'] == 'draft' and task['assignments'] == []
        assert task['prepared_assignment']['worker_id'] == worker['worker_id']
        assert task['original_instruction'] != task['prepared_assignment']['rendered_text']
        assert client.get(root+'/owner/captures/'+worker['source_id']+'/original').text == task['original_instruction']
        assert all(w['contact'] is None and w['telegram_ref'] is None for w in client.get(root+'/tasks').json()['workers'])
        assert client.get('/v1/finance/reconciliation', params={'entity_id':eid}).json()['ok']
    with sf() as db:
        assert db.scalar(text('SELECT count(*) FROM finance.opening_payables WHERE entity_id=:e'), dict(e=UUID(eid))) == 0
        assert db.scalar(text('SELECT count(*) FROM finance.postings WHERE entity_id=:e'), dict(e=UUID(eid))) == 3
        assert db.scalar(text('SELECT count(*) FROM inventory.movements WHERE entity_id=:e'), dict(e=UUID(eid))) == 0
        assert db.scalar(text('SELECT count(*) FROM workers.assignments WHERE entity_id=:e'), dict(e=UUID(eid))) == 0
