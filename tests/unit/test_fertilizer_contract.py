from datetime import datetime,timezone
from uuid import uuid4
from decimal import Decimal
import pytest
from pydantic import ValidationError
from greenhouse_owner_os.domain.fertilizer import decimal_string,FormSource,ComponentInput,ActualLine,FertilizerResponseInput
from greenhouse_owner_os.services.fertilizer import number,CANONICAL

@pytest.mark.parametrize('value',['2,00','2.00','0.001','100000000000000000'])
def test_exact_owner_strings_survive_decimal_parsing(value):
 assert decimal_string(value)==value
 assert number(value)==Decimal(value.replace(',','.'))
@pytest.mark.parametrize('value',[2.5,'-1','1e2','1,2.3','0','1000000000000000000','0.0000000000001'])
def test_unknown_float_or_unsupported_precision_not_guessed(value):
 with pytest.raises(ValueError):decimal_string(value)
def test_zero_is_explicit_and_requires_reason():
 values=dict(card_id=uuid4(),component_id=uuid4(),report_id=uuid4(),quantity='0,0',disposition='NOT_APPLIED')
 with pytest.raises(ValidationError):ActualLine(**values)
 assert ActualLine(**values,reason='Not applied').quantity=='0,0'
def test_worker_ui_action_does_not_accept_ack_start_or_done_quantity_override():
 b=dict(client_response_id=uuid4(),card_id=uuid4(),expected_revision=1,dispatch_id=uuid4(),response='DONE',captured_at=datetime.now(timezone.utc),button_label='Готово',original_language='ru')
 for response in ['RECEIVED','START']:
  with pytest.raises(ValidationError):FertilizerResponseInput(**dict(b,response=response))
 with pytest.raises(ValidationError):FertilizerResponseInput(**b,quantities=[dict(component_id=uuid4(),quantity='3')])
 assert FertilizerResponseInput(**b,comment='').comment==''
def test_canonical_topology_is_not_demo_five_five_ten():
 assert {root:sum(number(c['area_da']) for c in CANONICAL if c['greenhouse_name']==root and c['area_da']) for root in ('Yeşilöz','İmamlı','Özvadi')}=={'Yeşilöz':Decimal(20),'İmamlı':Decimal(20),'Özvadi':Decimal(30)}
 assert [c['area_da'] for c in CANONICAL if c['greenhouse_name']=='Yeşilöz' and c['area_da']]==['3','5','5','3.5','3.5']

def test_worker_default_window_uses_operating_timezone(monkeypatch):
 from datetime import datetime as RealDateTime,date
 from fastapi.testclient import TestClient
 from greenhouse_owner_os.api.app import create_app
 import greenhouse_owner_os.api.fertilizer as module
 calls=[]
 class Clock:
  @classmethod
  def now(cls,zone):
   calls.append(str(zone));return RealDateTime(2026,10,10,22,30,tzinfo=timezone.utc).astimezone(zone)
 class Stub:
  def calendar(self,e,a,start,end,w=None):
   calls.append((start,end));return dict(items=[],attention=0,filters={})
 monkeypatch.setattr(module,'datetime',Clock);monkeypatch.setattr(module,'FertilizerService',lambda _:Stub())
 class Finance:uow_factory=None
 client=TestClient(create_app(finance_service=Finance(),actor_provider=lambda:uuid4(),worker_provider=lambda:(uuid4(),uuid4(),uuid4())))
 assert client.get('/v1/worker/fertilizer').status_code==200
 assert calls[0]=='Europe/Istanbul' and (calls[1][0]+__import__('datetime').timedelta(days=180))==date(2026,10,11)

def test_generated_fertilizer_contract_matches_runtime():
 from pathlib import Path
 import yaml
 from greenhouse_owner_os.api.app import create_app
 live=create_app(finance_service=None,actor_provider=lambda:None).openapi();saved=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
 for path,body in live['paths'].items():
  if 'fertilizer' in path:assert saved['paths'][path.removeprefix('/v1')]==body
 for name in ('PlanInput','PlanView','PlanPreview','ActualInput','ActualView','ActualPreview','WorkerPlanView','FertilizerResponseInput'):
  assert saved['components']['schemas'][name]==live['components']['schemas'][name]

def test_test_capability_labels_never_claim_external_delivery():
 from pathlib import Path
 text=Path('src/greenhouse_owner_os/preview.py').read_text()
 assert "'worker_tasks':'R1_AND_R2_INTERNAL_TEST'" in text and "'fertilizer_planning':'CONNECTED_TEST_BOUNDED_ACTUAL_SCOPE'" in text
 assert "'real_dispatch':False, 'external_integrations':False" in text

def test_legacy_optional_completion_fields_do_not_rewrite_original_payload():
 from greenhouse_owner_os.domain.fertilizer import ActualInput
 b=dict(client_response_id=uuid4(),card_id=uuid4(),expected_revision=1,dispatch_id=uuid4(),response='DONE',captured_at=datetime.now(timezone.utc),button_label='Готово',original_language='ru')
 raw=FertilizerResponseInput(**b).model_dump(mode='json')
 assert 'physical_execution_id' not in raw and 'quantity_mode' not in raw and 'late_evidence' not in raw
 actual=ActualInput(source=dict(form_id=uuid4(),captured_at=datetime.now(timezone.utc)),expected_version=3,revision=1,event_date='2026-10-11',lines=[dict(card_id=b['card_id'],component_id=uuid4(),report_id=b['client_response_id'],quantity='1',disposition='APPLIED')])
 serialized=actual.model_dump(mode='json')
 assert 'execution_kind' not in serialized and 'physical_execution_id' not in serialized['lines'][0] and 'remainder_decision' not in serialized['lines'][0]

@pytest.mark.parametrize('defect',['duplicate_part','duplicate_order','inconsistent_order','order_without_identity'])
def test_ordered_application_identity_is_explicit_and_consistent(defect):
 from greenhouse_owner_os.domain.fertilizer import PlanInput
 from copy import deepcopy
 app=uuid4();parts=[uuid4(),uuid4()]
 cards=[dict(card_id=uuid4(),work_area_id=p,application_id=app,application_order=1,mixing_intent='SINGLE',mix_label='Внесение 1',components=[dict(component_id=uuid4(),product_id=uuid4(),unit='kg',dose_per_da='1')]) for p in parts]
 if defect=='duplicate_part':cards[1]['work_area_id']=parts[0]
 if defect=='duplicate_order':cards[1]['application_id']=uuid4()
 if defect=='inconsistent_order':cards[1]['application_order']=2
 if defect=='order_without_identity':cards[1].pop('application_id')
 with pytest.raises(ValidationError):PlanInput(source=dict(form_id=uuid4(),captured_at=datetime.now(timezone.utc)),greenhouse_id=uuid4(),event_date='2026-10-11',cards=cards)

def test_zero_and_remainder_decision_are_independent():
 ln=ActualLine(card_id=uuid4(),component_id=uuid4(),physical_execution_id=uuid4(),quantity='0',disposition='NOT_APPLIED',reason='В этот раз не вносили',remainder_decision='KEEP_OPEN')
 assert ln.report_id is None and ln.quantity=='0' and ln.remainder_decision=='KEEP_OPEN'
 assert ln.model_copy(update={'remainder_decision':'CLOSE'}).quantity=='0'
