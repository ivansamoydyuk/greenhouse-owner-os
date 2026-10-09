"""Legacy and metric browser suites never share immutable TEST history."""
import sys
from types import ModuleType
import pytest
from scripts import run_owner_e2e


@pytest.mark.parametrize('r2',[False,True])
def test_browser_metrics_start_only_after_stopped_legacy_preview_and_fresh_reset(monkeypatch,r2):
    events=[]
    fixtures=ModuleType('tests.integration.conftest')
    fixtures._reset_and_migrate=lambda url:events.append(('reset',url))
    monkeypatch.setitem(sys.modules,'tests.integration.conftest',fixtures)
    monkeypatch.setenv('APP_ENV','test')
    monkeypatch.setenv('TEST_DATABASE_URL','postgresql+psycopg://postgres:postgres@localhost:5432/greenhouse_owner_os_test')
    for key in ('R2_E2E_ONLY','HARVEST_E2E_ONLY','INBOX_E2E_ONLY','INVENTORY_E2E_ONLY','AGENT_BROWSER','R2_REVIEW_FIXTURES'):
        monkeypatch.delenv(key,raising=False)
    if r2:monkeypatch.setenv('R2_E2E_ONLY','1')
    monkeypatch.setattr(run_owner_e2e,'run_preview',lambda env,suites,log_name,**kwargs:events.append(('run',dict(env),suites,log_name)))
    run_owner_e2e.main()
    assert [event[0] for event in events]==['reset','run','reset','run']
    legacy,metrics=events[1],events[3]
    expected=['owner-r2-ui-fencing']+([] if r2 else ['owner','business','harvest','inbox','inventory','workers'])+['owner-r2','owner-modern-ui','owner-iphone-ui']
    assert legacy[2]==expected
    assert metrics[2]==['owner-friction']
    assert legacy[3]=='preview.log' and metrics[3]=='preview-friction.log'
    assert legacy[1]['PREVIEW_SECRET']!=metrics[1]['PREVIEW_SECRET']
    assert str(run_owner_e2e.ROOT/'src') in legacy[1]['PYTHONPATH']
    assert bool(legacy[1].get('R2_REVIEW_FIXTURES'))==r2


def test_preview_runner_still_rejects_non_test_environment(monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('TEST_DATABASE_URL','postgresql+psycopg://postgres:postgres@localhost:5432/greenhouse_owner_os_test')
    with pytest.raises(ValueError):run_owner_e2e.main()
