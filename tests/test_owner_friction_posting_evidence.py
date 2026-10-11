import pytest
from scripts.owner_friction_posting_evidence import read_posting

@pytest.mark.parametrize(('url','environment'),[
    ('postgresql+psycopg://postgres@test.invalid:5432/greenhouse_owner_os_test','test'),
    ('postgresql+psycopg://postgres@localhost:5432/greenhouse_owner_os','test'),
    ('postgresql+psycopg://postgres@localhost:5432/greenhouse_owner_os_test','production'),
])
def test_posting_reader_rejects_non_disposable_target_before_connecting(url,environment):
    with pytest.raises(ValueError):read_posting('00000000-0000-0000-0000-000000000001',database_url=url,environment=environment)
