"""Upgrade evidence must validate every database route before touching a DB."""
import pytest
from tests.helpers import capture_daily_upgrade_0033 as proof

TEST_URL='postgresql+psycopg://postgres:postgres@localhost:5432/greenhouse_owner_os_test'


@pytest.mark.parametrize('override',[
    'postgresql+psycopg://postgres:postgres@localhost:5432/another_database',
    'postgresql+psycopg://postgres:postgres@remote.invalid:5432/greenhouse_owner_os_test',
    'postgresql+psycopg://postgres:postgres@127.0.0.1:5432/greenhouse_owner_os_test',
])
def test_mismatched_alembic_override_never_connects_or_migrates(monkeypatch,override):
    monkeypatch.setenv('APP_ENV','test')
    monkeypatch.setenv('TEST_DATABASE_URL',TEST_URL)
    monkeypatch.setenv('DATABASE_URL',override)
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append('database touched')
        raise AssertionError('Database was reached before target validation')
    monkeypatch.setattr(proof,'create_engine',forbidden)
    monkeypatch.setattr(proof.command,'upgrade',forbidden)
    with pytest.raises(ValueError,match='must exactly match'):
        proof.main()
    assert calls==[]


@pytest.mark.parametrize('override',[None,'',TEST_URL])
def test_validated_route_matches_actual_alembic_override_semantics(monkeypatch,override):
    monkeypatch.setenv('APP_ENV','test')
    monkeypatch.setenv('TEST_DATABASE_URL',TEST_URL)
    if override is None:monkeypatch.delenv('DATABASE_URL',raising=False)
    else:monkeypatch.setenv('DATABASE_URL',override)
    assert proof.validated_database_url()==TEST_URL


def test_matching_non_test_target_never_connects(monkeypatch):
    url='postgresql+psycopg://postgres:postgres@localhost:5432/production'
    monkeypatch.setenv('APP_ENV','test')
    monkeypatch.setenv('TEST_DATABASE_URL',url)
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setattr(proof,'create_engine',lambda *a,**k:pytest.fail('must not connect'))
    with pytest.raises(ValueError,match='exact local disposable target'):
        proof.main()
