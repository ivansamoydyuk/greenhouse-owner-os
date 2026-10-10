"""HTTP validation/routing only. PostgreSQL behavior has separate integration tests."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from greenhouse_owner_os.api.owner_capture import mount_owner_capture
from greenhouse_owner_os.domain.errors import ValidationError
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService


@pytest.fixture
def api(monkeypatch):
    calls = []
    actor, entity, item = uuid4(), uuid4(), uuid4()

    class ReadService:
        def __init__(self, factory):
            pass

        def tasks(self, *args):
            calls.append(('tasks', args))
            return {'items': [], 'next_offset': None}

        def task(self, *args):
            calls.append(('task', args))
            return {'id': str(item), 'state': 'DONE', 'version': 2, 'actions': []}

    monkeypatch.setattr('greenhouse_owner_os.api.owner_capture.OwnerCaptureService', ReadService)
    app = FastAPI()
    finance = type('Finance', (), {'uow_factory': None})()
    mount_owner_capture(app, finance, lambda: actor)
    return TestClient(app), calls, actor, entity, item, app


def test_personal_list_defaults_and_owner_identity(api):
    client, calls, actor, entity, _, _ = api
    assert client.get(f'/v1/entities/{entity}/owner/tasks').status_code == 200
    assert calls == [('tasks', (entity, actor, 'TO_DO', 0, 50))]


def test_completed_list_has_no_arbitrary_last_page_cap(api):
    client, calls, actor, entity, _, _ = api
    offset = 10**30
    response = client.get(f'/v1/entities/{entity}/owner/tasks?state=DONE&offset={offset}&limit=100')
    assert response.status_code == 200
    assert calls == [('tasks', (entity, actor, 'DONE', offset, 100))]


@pytest.mark.parametrize('query', ['state=completed', 'state=ALL', 'limit=0', 'limit=101', 'offset=-1', 'offset=1.5'])
def test_invalid_personal_list_filter_is_rejected_before_service(api, query):
    client, calls, _, entity, _, _ = api
    assert client.get(f'/v1/entities/{entity}/owner/tasks?{query}').status_code == 422
    assert calls == []


def test_task_detail_keeps_authenticated_owner_and_path_identity(api):
    client, calls, actor, entity, item, _ = api
    response = client.get(f'/v1/entities/{entity}/owner/tasks/{item}')
    assert response.status_code == 200 and response.json()['id'] == str(item)
    assert calls == [('task', (entity, actor, item))]


def test_personal_read_schema_retains_existing_state_command(api):
    *_, app = api
    paths = app.openapi()['paths']
    root = '/v1/entities/{entity_id}/owner/tasks'
    assert set(paths[root]) == {'get'}
    assert set(paths[root+'/{item_id}']) == {'get'}
    assert set(paths[root+'/{item_id}/state']) == {'post'}
    params = {p['name']: p for p in paths[root]['get']['parameters']}
    assert params['state']['schema']['enum'] == ['TO_DO', 'DONE']
    assert params['limit']['schema']['maximum'] == 100
    assert 'maximum' not in params['offset']['schema']


def test_personal_read_openapi_artifact_matches_runtime():
    from pathlib import Path
    import yaml
    from greenhouse_owner_os.api.app import create_app
    runtime = create_app(finance_service=None, actor_provider=lambda: None).openapi()
    artifact = yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    for path in ('/v1/entities/{entity_id}/owner/tasks', '/v1/entities/{entity_id}/owner/tasks/{item_id}'):
        assert artifact['paths'][path.removeprefix('/v1')] == runtime['paths'][path]


@pytest.mark.parametrize('kwargs', [dict(state='ALL'), dict(offset=-1), dict(offset=True), dict(offset=0.5), dict(limit=0), dict(limit=101), dict(limit=True)])
def test_direct_service_rejects_invalid_read_filter(kwargs):
    def unexpected_factory():
        raise AssertionError('Invalid filter opened a database transaction')

    service = OwnerCaptureService(unexpected_factory)
    with pytest.raises(ValidationError):
        service.tasks(uuid4(), uuid4(), **kwargs)
