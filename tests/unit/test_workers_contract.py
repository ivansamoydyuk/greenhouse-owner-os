from datetime import date
from types import SimpleNamespace
from uuid import uuid4
import pytest
from greenhouse_owner_os.services.tasks_capture import extract_task
from greenhouse_owner_os.domain.workers import ReportInput,RuleInput,TaskInput

@pytest.mark.parametrize('raw',['Завтра открыть окна во всех теплицах утром и вечером закрыть.','Сегодня полить первую и вторую часть Имамлы по 35 минут.','Хиддаят завтра режет маленький банан во всех теплицах.','Раз в неделю помогать Инаяту на Озвади по пятницам.','Проверьте в Озвади, осталось ли много плохого банана.'])
def test_wrk_031_032_conservative_instruction(raw):
    r,f,gaps=extract_task(raw,today=date(2026,10,6))
    assert r=='TASKS' and not f.get('greenhouse_id') and not f['suggested_worker_ids'] and gaps and f['original_instruction']==raw

def test_known_current_worker_only_suggested_never_assigned():
    worker=dict(id=str(uuid4()),display_name='TEST Worker',native_name=None,active=True)
    area=SimpleNamespace(id=uuid4(),name='İmamlı',kind='greenhouse')
    r,f,g=extract_task('TEST Worker завтра полить Имамлы',workers=[worker],areas=[area],today=date(2026,10,6))
    assert f['planned_date']=='2026-10-07' and f['suggested_worker_ids']==[worker['id']] and f['greenhouse_id']==str(area.id)
    assert 'assignment' not in f

@pytest.mark.parametrize('duration',[True,-1,1.5,1441])
def test_irrigation_duration_never_infers_volume(duration):
    with pytest.raises(ValueError):ReportInput(cycle=1,assignment_id=uuid4(),original_text='report',original_language='ru',locator='1',normalized={'duration_minutes':duration})

def test_worker_openapi_contract():
    from greenhouse_owner_os.api.app import create_app
    app=create_app(finance_service=None,actor_provider=lambda:None)
    paths=app.openapi()['paths']
    assert '/v1/worker/tasks' in paths and '/v1/entities/{entity_id}/tasks/{task_id}/assignments' in paths

def test_workers_openapi_artifact_matches_request_schemas():
    import yaml
    from pathlib import Path
    from greenhouse_owner_os.api.app import create_app
    runtime=create_app(finance_service=None,actor_provider=lambda:None).openapi()
    artifact=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    for name in ('ActionInput','greenhouse_owner_os__domain__workers__ReportInput'):
        assert artifact['components']['schemas'][name]==runtime['components']['schemas'][name]
    for path,value in runtime['paths'].items():
        if '/tasks' in path or '/workers' in path or '/task-rules' in path:assert artifact['paths'][path.removeprefix('/v1')]==value
