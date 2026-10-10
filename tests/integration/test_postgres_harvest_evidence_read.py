"""Reopen immutable field originals without inventing or rewriting evidence."""
import base64
import hashlib
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.services.references import ReferenceService, provision_authenticated_owner


def context(url):
    sf = make_session_factory(url)
    actor = provision_authenticated_owner(sf, external_ref='test:evidence-read:'+str(uuid4()), display_name='TEST Evidence Owner', environment='TEST_PREVIEW')
    factory = lambda:SqlAlchemyUnitOfWork(sf)
    def app(provider=None):
        return create_app(finance_service=FinanceService(factory), reference_service=ReferenceService(factory, environment='TEST_PREVIEW'), actor_provider=provider or (lambda:actor))
    client = TestClient(app())
    result = client.post('/v1/entities', json=dict(name='TEST Evidence Entity '+str(actor),currency='TRY',source='test:evidence-read'), headers={'Idempotency-Key':'evidence-entity'})
    assert result.status_code == 201, result.text
    return sf, actor, result.json()['id'], client, app


def report(client, entity, evidence):
    response = client.post('/v1/entities/'+entity+'/harvest/reports', json=dict(event_date='2026-10-10',original_greenhouse='TEST unknown greenhouse',
        cut_count=4,field_weight_kg=None,reporter='TEST Field Reporter',notes='TEST owner prepared note remains separate',evidence=evidence), headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code == 201, response.text
    return response.json()


def snapshot(sf, actor, entity):
    with sf() as db:
        values = {}
        for table in ('harvest.worker_reports','harvest.evidence'):
            values[table] = db.execute(text('SELECT to_jsonb(t) FROM '+table+' t WHERE entity_id=:e ORDER BY id'), dict(e=entity)).scalars().all()
        for table in ('finance.postings','inventory.movements'):
            values[table] = db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'), dict(e=entity))
        values['audit'] = db.scalar(text('SELECT count(*) FROM audit.events WHERE actor_id=:a'), dict(a=actor))
        values['idempotency'] = db.scalar(text('SELECT count(*) FROM audit.idempotency_keys WHERE actor_id=:a'), dict(a=actor))
        return json.dumps(values, sort_keys=True, default=str)


def test_field_text_original_reopens_after_new_client_without_effects(migrated_database):
    sf, actor, entity, client, app = context(migrated_database)
    raw = 'TEST исходный полевой отчёт: 4 грозди. Вес неизвестен.\nСтрока 2 сохранена точно.'
    saved = report(client, entity, dict(source='test:field-original',source_reference='TEST field message '+str(uuid4()),raw_text=raw))
    before = snapshot(sf,actor,entity)
    reloaded = TestClient(app())
    listed = reloaded.get('/v1/entities/'+entity+'/harvest').json()['reports']
    found = next(r for r in listed if r['id'] == saved['id'])
    path = '/v1/entities/'+entity+'/harvest/evidence/'+found['evidence_id']
    response = reloaded.get(path+'/metadata')
    assert response.status_code == 200, response.text
    original = response.json()
    assert original['id'] == found['evidence_id'] and original['entity_id'] == entity
    assert original['raw_text'] == raw and original['raw_text'] != found['notes']
    assert original['source'] == 'test:field-original' and original['source_reference'].startswith('TEST field message ')
    assert original['fingerprint'] == hashlib.sha256(raw.encode()).hexdigest()
    assert original['received_at'] and original['created_by'] == str(actor)
    assert original['has_file'] is False and original['original_url'] is None
    assert original['filename'] is None and original['mime_type'] is None
    assert 'file_bytes' not in original and response.headers['cache-control'] == 'private, no-store'
    assert reloaded.get(path).status_code == 404, 'The pre-existing binary-only endpoint stays binary-only'
    assert snapshot(sf,actor,entity) == before


def test_binary_metadata_preserves_bytes_and_owner_entity_scope(migrated_database):
    sf, actor, entity, client, app = context(migrated_database)
    from io import BytesIO
    from PIL import Image
    image = BytesIO(); Image.new('RGB',(3,3),(120,220,130)).save(image,format='PNG')
    raw = image.getvalue()
    saved = report(client, entity, dict(source='test:field-photo',source_reference='TEST photo '+str(uuid4()),
        raw_text='TEST owner caption; separate from document pixels',filename='TEST-field.png',mime_type='image/png',file_base64=base64.b64encode(raw).decode()))
    path = '/v1/entities/'+entity+'/harvest/evidence/'+saved['evidence_id']
    before = snapshot(sf,actor,entity)
    data = client.get(path+'/metadata').json()
    assert data['has_file'] is True and data['original_url'] == path
    assert data['fingerprint'] == hashlib.sha256(raw).hexdigest()
    assert data['filename'] == 'TEST-field.png' and data['mime_type'] == 'image/png'
    assert data['raw_text'] == 'TEST owner caption; separate from document pixels'
    assert 'file_bytes' not in data and 'file_base64' not in data
    original = client.get(data['original_url'])
    assert original.status_code == 200 and original.content == raw
    assert hashlib.sha256(original.content).hexdigest() == data['fingerprint']
    assert original.headers['content-type'] == 'image/png'
    assert client.get('/v1/entities/'+entity+'/harvest/evidence/'+str(uuid4())+'/metadata').status_code == 404
    other_sf, other_actor, other_entity, other_client, _ = context(migrated_database)
    foreign = report(other_client,other_entity,dict(source='test:foreign-field',raw_text='TEST other owner private original '+str(uuid4())))
    assert client.get('/v1/entities/'+entity+'/harvest/evidence/'+foreign['evidence_id']+'/metadata').status_code == 404
    assert client.get('/v1/entities/'+other_entity+'/harvest/evidence/'+foreign['evidence_id']+'/metadata').status_code == 403
    assert other_client.get(path+'/metadata').status_code == 403
    def unauthenticated():raise HTTPException(401,'Login required')
    assert TestClient(app(unauthenticated)).get(path+'/metadata').status_code == 401
    assert snapshot(sf,actor,entity) == before
