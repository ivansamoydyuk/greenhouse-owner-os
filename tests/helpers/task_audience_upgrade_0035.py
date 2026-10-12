"""Synthetic default0033 ->0034 ->0035 with exact retained rows and receipts.

The historical0034 helper remains unchanged. This new proof builds on its full
populated fixture, adds a confirmed personal task while still on0034, then checks
all domain/audit rows and cached confirmation across the additive0035 upgrade.
"""
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid4
from unittest.mock import patch
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from greenhouse_owner_os.domain.owner_capture import CaptureSource, ConfirmAction
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.services.reconciliation import reconcile
from tests.helpers.capture_daily_upgrade_0033 import validated_database_url
from tests.helpers.day_workers_upgrade_0034 import main as retained_0034


def main():
    url=validated_database_url()
    config=Config('alembic.ini');config.set_main_option('sqlalchemy.url',url)
    assert ScriptDirectory.from_config(config).get_heads()==['20261012_0035']
    retained_0034()
    engine=create_engine(url);sf=make_session_factory(url)
    with engine.connect() as db:
        assert db.scalar(text('SELECT version_num FROM public.alembic_version'))=='20261012_0034'
        identity=db.execute(text("SELECT entity_id,created_by FROM core.inbox_items WHERE source='owner:r2' ORDER BY received_at LIMIT 1")).one()
    entity,owner=identity
    service=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    raw='Для меня завтра проверить насос: TEST личная задача сохранена до0035'
    captured=service.capture(entity,owner,'task-upgrade-personal-source',CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),text=raw))
    source_id=UUID(captured['id']);service.process(source_id)
    source=service.source(entity,owner,source_id);assert len(source['proposals'])==1
    proposal=service.preview(entity,owner,UUID(source['proposals'][0]['id']))
    assert proposal['action_type']=='PERSONAL_TASK' and not proposal['revision']['missing']
    body=ConfirmAction(expected_version=proposal['version'],preview_digest=proposal['revision']['preview_digest'],confirmed=True)
    effect=service.confirm(entity,owner,UUID(proposal['id']),'task-upgrade-personal-confirm',body)
    retained_personals=[dict(source_id=source_id,proposal=proposal,body=body,effect=effect,key='task-upgrade-personal-confirm',raw=raw)]
    # Bounded historical routing fixture, never an application fallback. The
    # exact old bare phrase is stored as PERSONAL while the old version label
    # is active; the current parser is restored before migration/readback.
    import greenhouse_owner_os.services.owner_preparation as preparation
    legacy_raw='Сегодня полить İmamlı.'
    with patch.object(preparation,'task_audience',lambda *args,**kwargs:'PERSONAL_TASK'), patch.object(preparation,'INTERPRETATION_VERSION','owner-language-preparation-r2-v4'):
        legacy=service.capture(entity,owner,'task-upgrade-legacy-personal-source',CaptureSource(client_capture_id=uuid4(),captured_at=datetime.now(timezone.utc),text=legacy_raw))
        legacy_id=UUID(legacy['id']);service.process(legacy_id)
    legacy_source=service.source(entity,owner,legacy_id)
    legacy_proposal=service.preview(entity,owner,UUID(legacy_source['proposals'][0]['id']))
    assert legacy_proposal['action_type']=='PERSONAL_TASK' and not legacy_proposal['revision']['missing']
    assert legacy_proposal['revision']['provenance']['original_instruction']['interpretation_version']=='owner-language-preparation-r2-v4'
    legacy_body=ConfirmAction(expected_version=legacy_proposal['version'],preview_digest=legacy_proposal['revision']['preview_digest'],confirmed=True)
    legacy_effect=service.confirm(entity,owner,UUID(legacy_proposal['id']),'task-upgrade-legacy-personal-confirm',legacy_body)
    retained_personals.append(dict(source_id=legacy_id,proposal=legacy_proposal,body=legacy_body,effect=legacy_effect,key='task-upgrade-legacy-personal-confirm',raw=legacy_raw))
    assert preparation.INTERPRETATION_VERSION=='owner-language-preparation-r2-v5'
    with engine.connect() as db:
        tables=db.execute(text("SELECT schemaname||'.'||tablename FROM pg_tables WHERE schemaname IN ('core','finance','harvest','inventory','workers','audit') ORDER BY 1")).scalars().all()
    def snapshot():
        with engine.connect() as db:
            return {table:db.execute(text('SELECT to_jsonb(t) FROM '+table+' t ORDER BY to_jsonb(t)::text')).scalars().all() for table in tables}
    before=snapshot();assert before['core.owner_tasks'] and before['workers.sources'] and before['core.owner_recovery_links']
    command.upgrade(config,'20261012_0035');after=snapshot()
    assert after==before, '0035 changed existing original/task/history/audit rows'
    with engine.connect() as db:assert db.scalar(text('SELECT version_num FROM public.alembic_version'))=='20261012_0035'
    for retained in retained_personals:
        service.process(retained['source_id'])
        assert service.confirm(entity,owner,UUID(retained['proposal']['id']),retained['key'],retained['body'])==retained['effect']
        assert service.source(entity,owner,retained['source_id'])['text']==retained['raw']
        current=service.preview(entity,owner,UUID(retained['proposal']['id']))
        assert current['confirmed_effect']==retained['effect'] and current['action_type']=='PERSONAL_TASK'
        assert current['revision']==retained['proposal']['revision']
    with sf() as db:
        result=reconcile(db,include_classification=True)
        assert result['ok'] and not result['findings']
    assert snapshot()==after, 'Cached receipt/readback/reconciliation changed retained rows'
    digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    print(json.dumps(dict(proof='populated_default0033_via0034_to0035',migration_head='20261012_0035',tables=len(tables),rows=sum(map(len,before.values())),before_sha256=digest(before),after_sha256=digest(after),retained_personal_task_ids=[r['effect']['effect']['id'] for r in retained_personals],legacy_bare_source=legacy_raw,legacy_interpretation='owner-language-preparation-r2-v4',table_rows={t:len(r) for t,r in before.items()},cached_receipt_and_readback_no_effect=True,reconciliation=result),sort_keys=True))
    print('PASS populated0033 ->0034 ->0035: exact retained rows, originals, personal task, cached receipt and clean reconciliation')
    engine.dispose()


if __name__=='__main__':main()
