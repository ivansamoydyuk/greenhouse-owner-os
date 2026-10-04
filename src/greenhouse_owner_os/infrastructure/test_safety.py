"""Exact allowlist for destructive test reset; never infer safety from a substring."""
from sqlalchemy.engine import make_url


def require_disposable_database(database_url, environment):
    url = make_url(database_url)
    if (environment != 'test' or url.drivername != 'postgresql+psycopg'
        or url.host not in ('localhost', '127.0.0.1') or url.port != 5432
        or url.database != 'greenhouse_owner_os_test' or url.username != 'postgres'):
        raise ValueError('destructive reset requires explicit test environment and exact local disposable target')
