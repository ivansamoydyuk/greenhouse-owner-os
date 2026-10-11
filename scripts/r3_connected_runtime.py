"""Set up only a newly owned CI TEST database; never a shared database.

The launcher owns the fresh PostgreSQL container and initializes the bootstrap
role. Migrations run as that bootstrap role. The HTTP process uses the separate
role literally named postgres, with no superuser/DDL/role privileges.
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from greenhouse_owner_os.synthetic_pilot import DATABASE_MARKER


def main():
    url = os.environ['R3_BOOTSTRAP_DATABASE_URL']
    target = make_url(url)
    if os.environ.get('APP_ENV') != 'test' or target.username != 'r3_pilot_bootstrap' or target.host not in ('127.0.0.1','localhost') or target.port != 5432 or target.database != 'greenhouse_owner_os_test' or target.query:
        raise ValueError('Only the exact owned local TEST bootstrap target is allowed')
    engine = create_engine(url)
    with engine.begin() as connection:
        assert connection.scalar(text('SELECT current_user')) == 'r3_pilot_bootstrap'
        assert connection.scalar(text('SELECT current_database()')) == 'greenhouse_owner_os_test'
        assert connection.scalar(text('SELECT count(*) FROM core.entities')) == 0, 'Refuse nonempty database'
        assert connection.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname='postgres'")) == 0, 'Refuse existing runtime identity'
        connection.execute(text('CREATE ROLE postgres LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'))
        connection.execute(text('REVOKE ALL ON DATABASE greenhouse_owner_os_test FROM PUBLIC'))
        connection.execute(text('GRANT CONNECT ON DATABASE greenhouse_owner_os_test TO postgres'))
        connection.execute(text('REVOKE CREATE ON SCHEMA public FROM PUBLIC'))
        for schema in ('core','finance','inventory','harvest','workers','audit'):
            connection.execute(text(f'GRANT USAGE ON SCHEMA {schema} TO postgres'))
            connection.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO postgres'))
            connection.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO postgres'))
            connection.execute(text(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO postgres'))
        connection.execute(text('GRANT SELECT ON public.alembic_version TO postgres'))
        connection.execute(text("COMMENT ON DATABASE greenhouse_owner_os_test IS '"+DATABASE_MARKER+"'"))
        connection.execute(text('ALTER ROLE r3_pilot_bootstrap NOLOGIN'))
    engine.dispose()
    print('Fresh TEST database marked; non-superuser runtime enabled; bootstrap login disabled.')


if __name__ == '__main__':
    main()
