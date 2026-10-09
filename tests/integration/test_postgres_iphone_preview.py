"""Physical-iPhone acceptance fixtures exist only in the guarded disposable adapter."""
import os
from fastapi.testclient import TestClient
from sqlalchemy import text
from greenhouse_owner_os.preview import build_preview
from greenhouse_owner_os.infrastructure.database import make_session_factory

SECRET='test-iphone-acceptance-only-key-2026'

def test_iphone_fixture_is_opt_in_and_uses_real_reviewed_draft(migrated_database,monkeypatch):
    monkeypatch.delenv('R2_REVIEW_FIXTURES',raising=False)
    app=build_preview(migrated_database,SECRET,environment='test')
    c=TestClient(app)
    assert c.get('/preview/config').status_code==401
    assert c.post('/preview/login',json={'secret':SECRET}).status_code==200
    config=c.get('/preview/config').json()
    assert config['r2_fixtures'] is False and config['worker_assignment_fixture']=={}
    root='/v1/entities/'+config['entity_id']
    before=c.get(root+'/owner/home').json()['finance']['balances']
    monkeypatch.setenv('R2_REVIEW_FIXTURES','1')
    fixture_app=build_preview(migrated_database,SECRET,environment='test')
    c=TestClient(fixture_app)
    assert c.get('/preview/config').status_code==401
    assert c.post('/preview/login',json={'secret':SECRET}).status_code==200
    config=c.get('/preview/config').json();fixture=config['worker_assignment_fixture']
    assert fixture['real_dispatch'] is False
    assert fixture['translation_scope']=='ONE_AUTHORED_TEST_EXAMPLE'
    overview=c.get(root+'/tasks').json()
    worker=next(w for w in overview['workers'] if w['id']==fixture['worker_id'])
    assert worker['display_name']=='TEST Muhammed' and worker['language']=='fa'
    assert worker['contact'] is None and worker['telegram_ref'] is None
    task=c.get(root+'/tasks/'+fixture['task_id']).json()
    assert task['status']=='draft' and task['assignments']==[]
    assert task['title'].startswith('TEST ')
    assert task['prepared_assignment']==dict(worker_id=fixture['worker_id'],language='fa',
        rendered_text='TEST Muhammed، لوله را در گلخانهٔ İmamlı در تاریخ ۲۰۲۶-۱۰-۱۰ بررسی کنید.')
    assert task['original_instruction']=='TEST Muhammed, проверьте трубу в İmamlı 2026-10-10.'
    original=c.get(root+'/owner/captures/'+fixture['source_id']+'/original')
    assert original.status_code==200 and original.text==task['original_instruction']
    assert c.get(root+'/owner/home').json()['finance']['balances']==before
    sf=make_session_factory(migrated_database)
    with sf() as s:
        assert s.scalar(text('SELECT count(*) FROM workers.assignments WHERE task_id=:t'),dict(t=fixture['task_id']))==0
        assert s.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE worker_task_id=:t'),dict(t=fixture['task_id']))==1
    # Restart/setup retries do not recreate source, draft, person, or rendering.
    again=TestClient(build_preview(migrated_database,SECRET,environment='test'))
    assert again.post('/preview/login',json={'secret':SECRET}).status_code==200
    assert again.get('/preview/config').json()['worker_assignment_fixture']==fixture
    tasks=again.get(root+'/tasks').json()
    assert len([w for w in tasks['workers'] if w['display_name']=='TEST Muhammed'])==1
    assert len([t for t in tasks['tasks'] if t['id']==fixture['task_id']])==1


def test_iphone_fixtures_never_relax_disposable_environment_guard(monkeypatch):
    monkeypatch.setenv('R2_REVIEW_FIXTURES','1')
    import pytest
    with pytest.raises(ValueError):
        build_preview(os.environ['TEST_DATABASE_URL'],SECRET,environment='production')
