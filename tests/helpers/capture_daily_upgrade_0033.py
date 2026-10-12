"""Isolated populated 0032 -> 0033 proof; no downgrade or production access.

Run only with disposable TEST_DATABASE_URL. Existing committed recovery, replay,
original and audit snapshots are compared exactly across the additive upgrade.
"""
import os
import hashlib
import json
from pathlib import Path
from uuid import UUID
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_owner_capture import source, queue, confirm
from tests.integration.test_postgres_owner_expense_recovery import recover, fill, service
from tests.integration.test_postgres_capture_daily_money import party, handoff


def validated_database_url():
    # Alembic env.py honours DATABASE_URL over the configured test URL. Validate
    # both routing inputs before opening any connection or dropping any schema.
    url=os.environ['TEST_DATABASE_URL']
    require_disposable_database(url,os.environ.get('APP_ENV'))
    override=os.environ.get('DATABASE_URL')
    if override and override != url:
        raise ValueError('DATABASE_URL must exactly match TEST_DATABASE_URL for upgrade proof')
    return url


def main():
    url=validated_database_url()
    engine=create_engine(url)
    with engine.begin() as db:
        for schema in ('workers','inventory','harvest','audit','finance','core'):
            db.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
        db.execute(text('DROP TABLE IF EXISTS public.alembic_version'))
    config=Config('alembic.ini');config.set_main_option('sqlalchemy.url',url)
    command.upgrade(config,'20261011_0032')
    i=inv.__wrapped__(preview.__wrapped__(url))
    s=source(i,'TEST retained legacy source 777');origin=queue(i)[0]
    target=fill(i,recover(i,s,origin,key='legacy-handoff'));effect=confirm(i,target,key='legacy-confirm')
    svc,e,a=service(i)
    def snapshot():
        with i['preview'][0]() as db:
            return {table:db.execute(text('SELECT to_jsonb(t) FROM '+table+' t ORDER BY to_jsonb(t)::text')).scalars().all()
                    for table in ('core.inbox_items','core.processing_jobs','core.extraction_runs','core.proposed_actions','core.proposal_revisions','core.proposal_actions','core.owner_recovery_links','core.confirmation_effects','finance.direct_expenses','finance.obligations','finance.postings','finance.posting_entries','audit.events','audit.idempotency_keys')}
    with engine.connect() as db:
        starting_version=db.scalar(text('SELECT version_num FROM alembic_version'))
    assert starting_version=='20261011_0032'
    print('Verified starting Alembic revision: '+starting_version)
    # This retained historical proof ends at 0033 even after later migrations.
    before=snapshot();command.upgrade(config,'20261012_0033');after=snapshot();assert after==before
    digest=lambda data:hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    print(json.dumps({'proof':'populated_0032_to_0033','before_sha256':digest(before),'after_sha256':digest(after),'table_rows':{table:len(items) for table,items in before.items()}},sort_keys=True))
    assert recover(i,s,origin,key='legacy-handoff')['id']==target['id']
    assert confirm(i,target,key='legacy-confirm')==effect
    party(i);new=source(i,'Серкан, ремонт насоса, 5 000 TRY.')
    p=next(p for p in queue(i) if p['source']['id']==new['id'])
    chosen=handoff(i,new,p);assert chosen['action_type']=='PAYABLE'
    confirm(i,chosen);assert i['preview'][3].reconcile(entity_id=UUID(i['e']))['ok']
    with engine.connect() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261012_0033'
    print('PASS populated 0032 -> 0033: exact legacy rows preserved, committed replay identical, new bounded payable confirms once, reconciliation clean')
    engine.dispose()


if __name__=='__main__':main()
