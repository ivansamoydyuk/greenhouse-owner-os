from datetime import datetime,timezone,timedelta
from greenhouse_owner_os.services.owner_capture import reminder_eligible

def test_exact_36_hours_and_defer_does_not_reset_age():
    start=datetime(2026,10,8,tzinfo=timezone.utc)
    assert not reminder_eligible(start,start+timedelta(hours=36),'READY')
    assert reminder_eligible(start,start+timedelta(hours=36,microseconds=1),'DEFERRED')
    for state in ('POSTED','REJECTED','CONFIRMED_NONPOSTING'):
        assert not reminder_eligible(start,start+timedelta(days=10),state)
