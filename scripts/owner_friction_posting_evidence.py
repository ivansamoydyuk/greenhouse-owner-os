"""Read-only posting evidence, restricted to the disposable local E2E database."""
import json
import os
import sys
from uuid import UUID
from sqlalchemy import create_engine, text
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database


def read_posting(posting_id, *, database_url=None, environment=None):
    database_url=database_url or os.environ['TEST_DATABASE_URL']
    environment=environment or os.environ.get('APP_ENV')
    require_disposable_database(database_url,environment)
    posting_id=str(UUID(str(posting_id)))
    engine=create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            posting=connection.execute(text('SELECT to_jsonb(p) FROM finance.postings p WHERE id=:id'),{'id':posting_id}).scalar_one()
            entries=connection.execute(text("SELECT to_jsonb(e)||jsonb_build_object('amount',e.amount::text) FROM finance.posting_entries e WHERE posting_id=:id ORDER BY id"),{'id':posting_id}).scalars().all()
            assert entries,'Posting entries must exist'
            return dict(posting=posting,entries=entries)
    finally:
        engine.dispose()


if __name__=='__main__':
    print(json.dumps(read_posting(sys.argv[1]),sort_keys=True))
