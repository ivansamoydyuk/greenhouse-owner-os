"""Read-only historical data hash proof across owned app/PostgreSQL restart."""
import argparse,hashlib,json,os
from pathlib import Path
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.preview import _require_connected_runtime
p=argparse.ArgumentParser();p.add_argument('--write');p.add_argument('--compare');a=p.parse_args();sf=make_session_factory(os.environ['TEST_DATABASE_URL'])
with sf() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));_require_connected_runtime(db)
 tables=db.execute(text("SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN('core','finance','audit','inventory','harvest','workers') ORDER BY1,2".replace('ORDER BY1','ORDER BY 1'))).all();proof={}
 for schema,table in tables:
  if table=='pilot_login_attempts':continue
  # Only aggregate digests leave the DB; participant/session/DEK values never do.
  rows=list(db.execute(text(f'SELECT to_jsonb(r)::text FROM {schema}.{table} r ORDER BY to_jsonb(r)::text')).scalars());proof[schema+'.'+table]={'rows':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
if a.write:Path(a.write).write_text(json.dumps(proof,indent=2));print('PRIVATE_DAILY_RESTART_SNAPSHOT_READY')
else:
 old=json.loads(Path(a.compare).read_text());assert proof==old,'Restart changed persisted domain/auth data';print('PRIVATE_DAILY_RESTART_HASHES_PASS')
