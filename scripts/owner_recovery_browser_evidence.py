"""Synthetic fixture/read-only lineage evidence for the disposable G browser test."""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import text
from greenhouse_owner_os.domain.owner_capture import CaptureSource
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure import harvest_models  # Register Inbox's harvest FK target in this standalone process.
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService


def main():
    require_disposable_database(os.environ['TEST_DATABASE_URL'], os.getenv('APP_ENV'))
    sf = make_session_factory(os.environ['TEST_DATABASE_URL'])
    mode, entity, identifier = sys.argv[1], UUID(sys.argv[2]), UUID(sys.argv[3])
    with sf() as db:
        assert db.scalar(text('SELECT current_database()')) == 'greenhouse_owner_os_test'
    if mode == 'failed':
        # Deliberately synthetic exhausted-job state, not a claim that the real
        # recognizer failed this text. No background preparation is launched.
        svc = OwnerCaptureService(lambda: SqlAlchemyUnitOfWork(sf))
        source = svc.capture(entity, identifier, str(uuid4()), CaptureSource(
            client_capture_id=uuid4(), captured_at=datetime.now(timezone.utc),
            text='TEST G browser exhausted source, original preserved'))
        with sf.begin() as db:
            db.execute(text("UPDATE core.processing_jobs SET state='FAILED',attempts=3,error_code='TEST_EXHAUSTED' WHERE inbox_id=:s AND entity_id=:e"), dict(s=source['id'], e=entity))
        print(json.dumps(source))
        return
    if mode != 'read':
        raise ValueError('Unsupported evidence mode')
    with sf.begin() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        source = db.execute(text('SELECT raw_text,file_bytes FROM core.inbox_items WHERE id=:s AND entity_id=:e'), dict(s=identifier, e=entity)).mappings().one()
        result = dict(source_id=str(identifier), original_sha256=hashlib.sha256(source['file_bytes'] or source['raw_text'].encode()).hexdigest())
        queries = {
            'links': 'SELECT to_jsonb(l) FROM core.owner_recovery_links l WHERE inbox_id=:s',
            'proposals': 'SELECT to_jsonb(p) FROM core.proposed_actions p WHERE inbox_id=:s ORDER BY id',
            'effects': 'SELECT to_jsonb(c) FROM core.confirmation_effects c JOIN core.proposed_actions p ON p.id=c.proposal_id WHERE p.inbox_id=:s ORDER BY p.id',
            'revisions': 'SELECT to_jsonb(r) FROM core.proposal_revisions r JOIN core.proposed_actions p ON p.id=r.proposal_id WHERE p.inbox_id=:s ORDER BY p.id,r.version',
            'actions': 'SELECT to_jsonb(a) FROM core.proposal_actions a JOIN core.proposed_actions p ON p.id=a.proposal_id WHERE p.inbox_id=:s ORDER BY p.id,a.created_at',
            'expenses': 'SELECT to_jsonb(d) FROM finance.direct_expenses d JOIN core.proposed_actions p ON p.id=d.proposal_id WHERE p.inbox_id=:s ORDER BY d.id',
        }
        for key, sql in queries.items():
            result[key] = db.execute(text(sql), dict(s=identifier)).scalars().all()
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
