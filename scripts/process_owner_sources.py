"""Bounded preparation worker. No confirmation, posting or dispatch authority.

Run periodically with the application's configured database credentials.
Fenced PostgreSQL leases permit concurrent invocations and crash recovery.
"""
import os
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService,ENGINE

def main():
    sf=make_session_factory(os.environ['DATABASE_URL'])
    with sf() as session:
        ids=session.scalars(text("SELECT inbox_id FROM core.processing_jobs WHERE engine_version=:v AND (attempts<3 AND (state='QUEUED' OR state='FAILED' AND retry_at<=clock_timestamp()) OR state='RUNNING' AND lease_until<clock_timestamp()) ORDER BY created_at LIMIT 20"),dict(v=ENGINE)).all()
    service=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    for source in ids:service.process(UUID(str(source)))

if __name__=='__main__':main()
