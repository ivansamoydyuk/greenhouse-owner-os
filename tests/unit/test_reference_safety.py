import pytest
from pydantic import ValidationError
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.domain.references import MoneyAccountSetup, canonical_key
from greenhouse_owner_os.services.references import reference_id


@pytest.mark.parametrize('url,env',[
    ('postgresql+psycopg://postgres:x@localhost:5432/greenhouse_owner_os_test','production'),
    ('postgresql+psycopg://postgres:x@production:5432/greenhouse_owner_os_test','test'),
    ('postgresql+psycopg://postgres:x@localhost:5432/production_test','test'),
    ('postgresql+psycopg://postgres:test@localhost:5432/production','test'),
    ('postgresql+psycopg://postgres:x@localhost:5433/greenhouse_owner_os_test','test'),
])
def test_destructive_reset_refuses_non_disposable(url,env):
    with pytest.raises(ValueError):require_disposable_database(url,env)


def test_stable_identity_and_input_validation():
    require_disposable_database('postgresql+psycopg://postgres:x@localhost:5432/greenhouse_owner_os_test','test')
    assert canonical_key('Café')==canonical_key('Cafe\u0301')
    assert reference_id('a','money_account','x')==reference_id('a','money_account','x')
    assert reference_id('a','money_account','x')!=reference_id('b','money_account','x')
    for changes in ({'name':' Cash'},{'currency':'ZZZ'},{'classification':'OWNER_VERIFIED'}):
        with pytest.raises(ValidationError):MoneyAccountSetup(**dict(name='Cash',source='owner:verified',currency='TRY',account_type='cash',**{} )|changes)
