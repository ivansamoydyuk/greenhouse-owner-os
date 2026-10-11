from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from greenhouse_owner_os.daily_pilot import PilotAuth,password_hash,password_matches,parse_expiry


def test_password_hash_uses_independent_salts_and_no_plaintext():
    value='synthetic-unit-password-only'
    first=password_hash(value);second=password_hash(value)
    assert first!=second and value not in first and password_matches(value,first)
    assert not password_matches(value+'x',first)
    assert not password_matches(value,'invalid')

@pytest.mark.parametrize('value',['','short','x'*201])
def test_password_bounds(value):
    with pytest.raises(ValueError):password_hash(value)

@pytest.mark.parametrize('value',['2026-10-11','not-date',None])
def test_expiry_requires_explicit_timezone(value):
    with pytest.raises(ValueError):parse_expiry(value)


def test_expiry_normalizes_instant():
    assert parse_expiry('2026-10-11T18:00:00+03:00')==datetime(2026,10,11,15,tzinfo=timezone.utc)

@pytest.mark.parametrize('payload',[{'password':'x'*201,'username':'owner'}, {'password':'sensitive-fixture'}, {'username':'owner','password':'sensitive-fixture','extra':'private-value'}])
def test_login_validation_never_echoes_credentials(payload):
    auth=PilotAuth.__new__(PilotAuth); app=FastAPI();auth.mount(app)
    response=TestClient(app).post('/pilot/login',json=payload)
    assert response.status_code==422
    assert response.json()=={'detail':'Проверьте имя и пароль.'}


def test_login_malformed_json_is_sanitized():
    auth=PilotAuth.__new__(PilotAuth);app=FastAPI();auth.mount(app)
    r=TestClient(app).post('/pilot/login',content='{"password":"private-fragment',headers={'Content-Type':'application/json'})
    assert r.status_code==422 and r.json()=={'detail':'Проверьте имя и пароль.'}
