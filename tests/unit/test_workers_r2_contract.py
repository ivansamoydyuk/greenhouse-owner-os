from datetime import datetime,timezone
from uuid import uuid4
import pytest
from pydantic import ValidationError
from greenhouse_owner_os.domain.workers import TaskInput,WorkerResponseInput
from greenhouse_owner_os.domain.owner_capture import TaskProposal


def test_old_task_and_proposal_hash_shapes_remain_omitted():
    old=TaskInput(title='TEST',original_instruction='TEST exact source',description='TEST',locator='old')
    assert old.execution_contract=='R1' and 'execution_contract' not in old.model_dump(mode='json')
    assert 'execution_contract' not in TaskProposal().model_dump(mode='json')
    assert old.model_copy(update={'execution_contract':'R2_RECEIVED_REPORT'}).model_dump(mode='json')['execution_contract']=='R2_RECEIVED_REPORT'


def test_ui_source_response_is_strict_and_preserves_exact_strings():
    b=dict(client_response_id=uuid4(),assignment_id=uuid4(),expected_cycle=1,response='PROBLEM',captured_at=datetime.now(timezone.utc),button_label='مشکل',original_language='fa',comment='  35 دقیقه، نه 50  ')
    assert WorkerResponseInput(**b).comment==b['comment']
    assert WorkerResponseInput(**dict(b,comment='')).comment==''
    assert WorkerResponseInput(**dict(b,comment=None)).comment is None
    for change in ({'expected_cycle':0},{'captured_at':datetime.now()},{'response':'START'},{'button_label':' '},{'worker_id':uuid4()}):
        with pytest.raises(ValidationError):WorkerResponseInput(**dict(b,**change))


def test_worker_response_openapi_and_task_execution_contract_match_runtime():
    from pathlib import Path
    import yaml
    from greenhouse_owner_os.api.app import create_app
    live=create_app(finance_service=None,actor_provider=lambda:None).openapi();saved=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    path='/v1/worker/tasks/{task_id}/responses'
    assert saved['paths'][path.removeprefix('/v1')]==live['paths'][path]
    for name in ('TaskInput','WorkerResponseInput','WorkerResponseResult'):
        assert saved['components']['schemas'][name]==live['components']['schemas'][name]
