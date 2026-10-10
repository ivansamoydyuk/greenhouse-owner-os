"""Diagnostic fixture parity only. Never changes baseline application source."""
import os,json,argparse
from uuid import UUID,uuid5,NAMESPACE_URL
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.models import WorkAreaRow
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.synthetic_pilot import SOURCE,OWNER_REF,DATABASE_MARKER
p=argparse.ArgumentParser();p.add_argument('--entity',required=True);p.add_argument('--owner',required=True);a=p.parse_args()
eid=UUID(a.entity);actor=UUID(a.owner);url=os.environ['TEST_DATABASE_URL'];require_disposable_database(url,'test')
sf=make_session_factory(url);name='TEST İmamlı';rid=uuid5(NAMESPACE_URL,f'{SOURCE}/{eid}/greenhouse/{name}')
with SqlAlchemyUnitOfWork(sf) as u:
 assert u.session.scalar(text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"))==DATABASE_MARKER
 assert u.session.scalar(text('SELECT count(*) FROM core.entities'))==1
 assert u.session.scalar(text('SELECT external_ref FROM core.actors WHERE id=:a'),dict(a=actor))==OWNER_REF
 assert u.session.scalar(text('SELECT core.r2_owner(:e,:a)'),dict(e=eid,a=actor)) is True
 role=u.session.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one();assert not any(role)
 assert u.session.scalar(text('SELECT host(inet_server_addr())'))=='127.0.0.1'
 u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,73))'),dict(key=str(rid)))
 old=u.session.get(WorkAreaRow,rid)
 if old:assert (old.entity_id,old.name,old.kind,old.source,old.parent_id)==(eid,name,'greenhouse',SOURCE,None)
 else:
  u.session.add(WorkAreaRow(id=rid,entity_id=eid,name=name,kind='greenhouse',source=SOURCE));u.session.flush()
  u.audit.append(actor_id=actor,action='reference_create',entity_type='work_area',entity_id=rid,before=None,after=dict(id=str(rid),name=name,source=SOURCE,classification='TEST_PREVIEW'))
 u.commit()
print(json.dumps(dict(id=str(rid),name=name,entity_id=str(eid),diagnostic_fixture=True,baseline_application_unchanged=True)))
