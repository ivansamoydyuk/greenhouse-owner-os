"""Source omission/substitution and serialized owner/worker actual boundaries."""
import copy,json,os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic,sleep
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ConflictError,ValidationError
from greenhouse_owner_os.domain.fertilizer import ActualConfirm,PlanInput,FormSource,FertilizerResponseInput
from greenhouse_owner_os.services.fertilizer import FertilizerService
from greenhouse_owner_os.services.inventory import InventoryService
from greenhouse_owner_os.domain.inventory import InventoryInput,InventoryPost
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.capture import BorrowedUow
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_fertilizer import fertilizer,ready,save,dispatch,respond,current,actual_input,source,STAMP,effects

@pytest.mark.parametrize('missing',['captured_at','original_language','source_kind','schema_version'])
def test_raw_worker_source_cannot_omit_typed_original(fertilizer,missing):
 f=fertilizer;p=save(f);dispatch(f,p);before=effects(f);report=respond(f,p,p['cards'][0],'PROBLEM',comment='TEST original')
 bad=copy.deepcopy(report['source_snapshot']);rid=str(uuid4());bad['client_response_id']=rid;bad.pop(missing)
 with pytest.raises(DBAPIError) as exc:
  with f['sf'].begin() as db:db.execute(text('INSERT INTO inventory.fertilizer_reports(id,entity_id,plan_id,revision_id,dispatch_id,card_id,worker_id,actor_id,response,source_snapshot) VALUES(:id,:entity_id,:plan_id,:revision_id,:dispatch_id,:card_id,:worker_id,:actor_id,:response,CAST(:s AS jsonb))'),dict(report,id=rid,s=json.dumps(bad)))
 assert exc.value.orig.sqlstate=='23514' and effects(f)==before
 assert len(current(f,p)['reports'])==1


def test_raw_duplicate_component_and_missing_plan_timestamp_rollback(fertilizer):
 f=fertilizer;p=save(f)
 with f['sf']() as db:r=dict(db.execute(text('SELECT * FROM inventory.fertilizer_revisions WHERE plan_id=:p'),dict(p=p['id'])).mappings().one());cards=[dict(x) for x in db.execute(text('SELECT * FROM inventory.fertilizer_cards WHERE plan_id=:p'),dict(p=p['id'])).mappings()]
 for defect in ('duplicate_component','missing_timestamp'):
  pid=uuid4();rid=uuid4();sid=uuid4();inp=copy.deepcopy(r['input']);v=copy.deepcopy(r['preview']);inp['source']['form_id']=str(sid)
  if defect=='missing_timestamp':inp['source'].pop('captured_at')
  else:
   inp['cards'][0]['components'][1]=copy.deepcopy(inp['cards'][0]['components'][0]);v['cards'][0]['components'][1]=copy.deepcopy(v['cards'][0]['components'][0])
  v['input']=inp
  with pytest.raises(DBAPIError) as exc:
   with f['sf'].begin() as db:
    db.execute(text('INSERT INTO inventory.fertilizer_plans(id,entity_id,created_by) VALUES(:p,:e,:a)'),dict(p=pid,e=f['e'],a=f['a']))
    db.execute(text('INSERT INTO inventory.fertilizer_revisions(id,entity_id,plan_id,revision,source_id,input,preview,preview_digest,created_by) VALUES(:r,:e,:p,1,:s,CAST(:b AS jsonb),CAST(:v AS jsonb),:d,:a)'),dict(r=rid,e=f['e'],p=pid,s=sid,b=json.dumps(inp),v=json.dumps(v),d=r['preview_digest'],a=f['a']))
    for c in v['cards']:db.execute(text('INSERT INTO inventory.fertilizer_cards(revision_id,card_id,entity_id,plan_id,work_area_id,worker_id,snapshot) VALUES(:r,:c,:e,:p,:w,:worker,CAST(:s AS jsonb))'),dict(r=rid,c=c['card_id'],e=f['e'],p=pid,w=c['work_area_id'],worker=c['worker_id'],s=json.dumps(c)))
  assert exc.value.orig.sqlstate=='23514'
  with f['sf']() as db:assert db.scalar(text('SELECT count(*) FROM inventory.fertilizer_plans WHERE id=:p'),dict(p=pid))==0

@pytest.mark.parametrize('defect',['missing_capture','foreign_source','quantity_substitution','report_substitution','false_complete'])
def test_raw_owner_actual_cannot_substitute_source_or_selected_effect(fertilizer,defect):
 f=fertilizer;p=ready(f);b=actual_input(f,p);before=effects(f);v=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b)
 raw=b.model_dump(mode='json');effect=v['inventory_effect'];pid=UUID(p['id'])
 if defect=='missing_capture':raw['source'].pop('captured_at')
 elif defect=='foreign_source':
  from tests.integration.test_postgres_owner_capture import source as capture_source
  other=inv.__wrapped__(preview.__wrapped__(os.environ['TEST_DATABASE_URL']));raw['source']['inbox_id']=capture_source(other,'TEST foreign fertilizer source')['id']
 elif defect=='quantity_substitution':raw['lines'][0]['quantity']='11';raw['lines'][0]['reason']='TEST substituted'
 elif defect=='report_substitution':raw['lines'][0]['report_id']=str(uuid4())
 elif defect=='false_complete':raw['lines']=raw['lines'][:1]
 with pytest.raises(DBAPIError) as exc:
  with SqlAlchemyUnitOfWork(f['sf']) as u:
   InventoryService(f['svc'].factory)._lock(u,f['e']);svc=InventoryService(lambda:BorrowedUow(u))
   ev=svc.create(f['e'],f['a'],str(uuid4()),InventoryInput(kind='application',event_date=b.event_date,lines=effect['lines'],evidence=dict(source='fertilizer:actual',locator=str(b.source.form_id),source_document='fertilizer-plan:'+str(pid),verification='VERIFIED')))
   svc.post(f['e'],f['a'],str(uuid4()),UUID(ev['id']),InventoryPost(confirmed=True,expected_version=1))
   rid=u.session.scalar(text('SELECT id FROM inventory.fertilizer_revisions WHERE plan_id=:p'),dict(p=pid))
   u.session.execute(text('INSERT INTO inventory.fertilizer_actuals(id,entity_id,plan_id,revision_id,source_id,application_id,input,source_snapshot,preview_digest,created_by) VALUES(:i,:e,:p,:r,:s,:v,CAST(:b AS jsonb),CAST(:original AS jsonb),:d,:a)'),dict(i=uuid4(),e=f['e'],p=pid,r=rid,s=b.source.form_id,v=ev['id'],b=json.dumps(raw),original=json.dumps(dict(input=raw,actual_preview_digest=v['actual_preview_digest'],confirmed=True)),d=v['actual_preview_digest'],a=f['a']))
   u.commit()
 assert exc.value.orig.sqlstate=='23514' and effects(f)==before
 assert current(f,p)['actual'] is None

@pytest.mark.parametrize('first',['actual','report'])
def test_actual_versus_new_worker_report_has_serialized_freshness(fertilizer,first):
 f=fertilizer;p=ready(f);b=actual_input(f,p);preview=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b);cmd=ActualConfirm(input=b,actual_preview_digest=preview['actual_preview_digest'],confirmed=True)
 held=Event();release=Event();pids={}
 class ControlledUow(SqlAlchemyUnitOfWork):
  def __init__(self,role):super().__init__(f['sf']);self.role=role
  def __enter__(self):super().__enter__();pids[self.role]=self.session.scalar(text('SELECT pg_backend_pid()'));return self
  def commit(self):
   if self.role==first:held.set();assert release.wait(10)
   super().commit()
 services={r:FertilizerService(lambda r=r:ControlledUow(r)) for r in ('actual','report')}
 def actual():return services['actual'].actual(f['e'],f['a'],'race-actual',UUID(p['id']),cmd)
 def report():return services['report'].respond(f['e'],UUID(f['w']['actor_id']),'race-report',UUID(p['id']),UUID(f['w']['id']),FertilizerResponseInput(client_response_id=uuid4(),card_id=p['cards'][0]['card_id'],expected_revision=p['revision'],dispatch_id=p['current_dispatch_id'],response='PROBLEM',captured_at=STAMP,button_label='Проблема',original_language='ru',comment='TEST new evidence'))
 calls={'actual':actual,'report':report};second='report' if first=='actual' else 'actual'
 with ThreadPoolExecutor(2) as pool:
  one=pool.submit(calls[first]);assert held.wait(10);two=pool.submit(calls[second])
  try:
   blocked=False;until=monotonic()+5
   while monotonic()<until:
    if second in pids:
     with f['sf']() as db:blocked=db.scalar(text('SELECT cardinality(pg_blocking_pids(:p))>0'),dict(p=pids[second]))
     if blocked:break
    sleep(.01)
   assert blocked
  finally:release.set()
  one.result(timeout=10)
  if first=='report':
   with pytest.raises(ConflictError):two.result(timeout=10)
  else:two.result(timeout=10)
 d=current(f,p);assert len(d['reports'])==3
 assert bool(d['actual'])==(first=='actual')
 if first=='actual':assert d['actual']['input']==b.model_dump(mode='json')


def test_worker_http_scope_and_source_history_are_separate_from_owner_values(fertilizer):
 from fastapi.testclient import TestClient
 from greenhouse_owner_os.api.app import create_app
 from greenhouse_owner_os.services.workers import WorkersService
 from greenhouse_owner_os.domain.workers import WorkerInput
 f=fertilizer;other=WorkersService(f['svc'].factory).worker(f['e'],f['a'],str(uuid4()),WorkerInput(display_name='TEST other fertilizer worker',language='fa',source='test:synthetic'))
 payload=f['body'].model_dump(mode='json');payload['cards'][1]['worker_id']=other['id'];p=save(f,PlanInput(**payload));dispatch(f,p)
 client=TestClient(create_app(finance_service=f['i']['preview'][3],actor_provider=lambda:f['a'],worker_provider=lambda:(f['e'],UUID(f['w']['id']),UUID(f['w']['actor_id']))))
 result=client.get('/v1/worker/fertilizer/'+p['id']);assert result.status_code==200,result.text
 detail=result.json();assert len(detail['cards'])==1 and 'input' not in detail and 'stock' not in detail and 'actual' not in detail
 bad=dict(client_response_id=str(uuid4()),card_id=p['cards'][1]['card_id'],expected_revision=1,dispatch_id=current(f,p)['current_dispatch_id'],response='DONE',captured_at=STAMP.isoformat(),button_label='Готово',original_language='ru')
 assert client.post('/v1/worker/fertilizer/'+p['id']+'/responses',json=bad,headers={'Idempotency-Key':'foreign-card'}).status_code==422
 owner_only=TestClient(create_app(finance_service=f['i']['preview'][3],actor_provider=lambda:f['a']))
 assert owner_only.get('/v1/worker/fertilizer/'+p['id']).status_code==401


def test_reference_versions_and_stale_content_preserve_originals(fertilizer):
 from greenhouse_owner_os.domain.fertilizer import ReferenceInput,DispatchInput
 from greenhouse_owner_os.domain.workers import WorkerInput
 from greenhouse_owner_os.services.workers import WorkersService
 from datetime import date
 f=fertilizer;p=ready(f);old=current(f,p);card=old['cards'][0];old_report=card['current_report'];oldref=card['area_provenance']
 worker=WorkersService(f['svc'].factory).worker(f['e'],f['a'],str(uuid4()),WorkerInput(display_name='TEST new verified responsible',language='fa',source='test:synthetic'))
 reference=f['svc'].reference(f['e'],f['a'],str(uuid4()),ReferenceInput(client_reference_id=uuid4(),captured_at=STAMP,work_area_id=card['work_area_id'],area_da='6,00',default_worker_id=worker['id'],verification='TEST prospective verified change',effective_from=date(2026,10,11),replaces_id=oldref['id'],confirmed=True))
 assert current(f,p)['cards'][0]['area_provenance']==oldref
 payload=f['body'].model_dump(mode='json');payload['source']['form_id']=str(uuid4());payload['event_date']='2026-10-11';payload['cards'][0].update(area_reference_id=reference['id'],worker_reference_id=reference['id'],worker_id=worker['id'],rendered_text='TEST новая проверенная версия')
 revised=f['svc'].save(f['e'],f['a'],'new-revision',PlanInput(**payload),UUID(p['id']),old['version'])
 assert __import__('decimal').Decimal(revised['cards'][0]['area_da'])==6 and revised['cards'][0]['components'][0]['proposed_quantity']=='12'
 assert revised['revision']==2 and revised['current_dispatch_id'] is None and revised['revisions'][0]['input']==old['input']
 command=FertilizerResponseInput(**{k:v for k,v in old_report['source_snapshot'].items() if k not in ('schema_version','source_kind')})
 assert f['svc'].respond(f['e'],UUID(f['w']['actor_id']),'logical-old-replay',UUID(p['id']),UUID(f['w']['id']),command)==old_report
 with pytest.raises(ConflictError):f['svc'].respond(f['e'],UUID(f['w']['actor_id']),'new-stale-response',UUID(p['id']),UUID(f['w']['id']),command.model_copy(update={'client_response_id':uuid4()}))
 with pytest.raises(ConflictError):f['svc'].dispatch(f['e'],f['a'],'new-stale-dispatch',UUID(p['id']),DispatchInput(client_approval_id=uuid4(),captured_at=STAMP,expected_version=old['version'],revision=1,preview_digest=old['preview_digest'],confirmed=True))
 assert current(f,p)['reports']==old['reports'] and current(f,p)['version']==revised['version']
 history=f['svc'].get(f['e'],UUID(f['w']['actor_id']),UUID(p['id']),UUID(f['w']['id']))
 assert history['revision']==1 and history['current_revision']==2 and not history['can_respond']
 assert history['cards'][0]['area_da']==old['cards'][0]['area_da'] and history['reports']==old['reports']
 assert all(c['worker_id']==f['w']['id'] for c in history['cards'])
 listing=f['svc'].calendar(f['e'],UUID(f['w']['actor_id']),date(2026,10,10),date(2026,10,10),w=UUID(f['w']['id']))
 assert listing['items'][0]['event_date']=='2026-10-10' and not listing['items'][0]['can_respond']


def test_exact_three_product_one_two_four_part_user_contrasts(fertilizer):
 from greenhouse_owner_os.domain.fertilizer import ReferenceInput
 from tests.integration.test_postgres_readiness import send
 from datetime import date
 from decimal import Decimal
 f=fertilizer;extra=send(f['i']['c'],f['i']['root']+'/products',dict(name='TEST second kg product',base_unit='kg',source='test:synthetic'))
 payload=f['body'].model_dump(mode='json');template=copy.deepcopy(payload['cards'][0])
 for n in range(2):
  part=uuid4()
  with f['sf'].begin() as db:db.execute(text("INSERT INTO core.work_areas(id,entity_id,parent_id,name,kind,source) VALUES(:p,:e,:g,:n,'work_area','test:explicit five da')"),dict(p=part,e=f['e'],g=f['i']['g'],n='TEST extra part '+str(n)))
  ref=f['svc'].reference(f['e'],f['a'],str(uuid4()),ReferenceInput(client_reference_id=uuid4(),captured_at=STAMP,work_area_id=part,area_da='5',verification='TEST explicit verified5da',effective_from=date(2026,10,1),confirmed=True))
  c=copy.deepcopy(template);c.update(card_id=str(uuid4()),work_area_id=str(part),area_reference_id=ref['id']);payload['cards'].append(c)
 for c in payload['cards']:
  c['components']=[dict(component_id=str(uuid4()),product_id=product,location_id=f['i']['l']['id'],dose_per_da=dose,unit=unit) for product,dose,unit in [(f['i']['p']['id'],'10','kg'),(f['liquid']['id'],'5','L'),(extra['id'],'2','kg')]]
 before=effects(f)
 for count,expected in [(1,[50,25,10]),(2,[100,50,20]),(4,[200,100,40])]:
  b=PlanInput(**dict(payload,cards=payload['cards'][:count]));v=f['svc'].preview(f['e'],f['a'],b)
  assert [Decimal(t['quantity']) for t in v['totals']]==expected and [t['unit'] for t in v['totals']]==['kg','L','kg']
  assert all([Decimal(x['proposed_quantity']) for x in c['components']]==[50,25,10] for c in v['cards'])
 assert effects(f)==before


def test_exact_selected_photo_bytes_remain_scoped_after_revision(fertilizer):
 import base64,io,hashlib
 from PIL import Image
 from fastapi.testclient import TestClient
 from greenhouse_owner_os.api.app import create_app
 from greenhouse_owner_os.domain.owner_capture import CaptureSource
 from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
 from greenhouse_owner_os.services.workers import WorkersService
 from greenhouse_owner_os.domain.workers import WorkerInput
 f=fertilizer;buf=io.BytesIO();Image.new('RGB',(7,9),(51,102,153)).save(buf,format='PNG');raw=buf.getvalue()
 original=OwnerCaptureService(f['svc'].factory).capture(f['e'],f['a'],'TEST selected photo',CaptureSource(client_capture_id=uuid4(),captured_at=STAMP,mode='photo',file_base64=base64.b64encode(raw).decode(),filename='TEST-product-reference.png',mime_type='image/png'))
 payload=f['body'].model_dump(mode='json');payload['cards'][0]['components'][0]['photo_source_id']=original['id'];p=save(f,PlanInput(**payload));card=p['cards'][0];component=card['components'][0]
 with pytest.raises(__import__('greenhouse_owner_os.domain.errors',fromlist=['NotFoundError']).NotFoundError):f['svc'].photo(f['e'],UUID(f['w']['actor_id']),UUID(p['id']),1,UUID(card['card_id']),UUID(component['component_id']),UUID(f['w']['id']))
 dispatch(f,p)
 client=TestClient(create_app(finance_service=f['i']['preview'][3],actor_provider=lambda:f['a'],worker_provider=lambda:(f['e'],UUID(f['w']['id']),UUID(f['w']['actor_id']))))
 detail=client.get('/v1/worker/fertilizer/'+p['id']).json();url=detail['cards'][0]['components'][0]['photo_url'];response=client.get(url)
 assert response.status_code==200 and response.content==raw and response.headers['content-type']=='image/png' and response.headers['cache-control']=='private, no-store'
 payload['source']['form_id']=str(uuid4());payload['cards'][0]['rendering_reviewed']=False
 f['svc'].save(f['e'],f['a'],'TEST revised photo plan',PlanInput(**payload),UUID(p['id']),current(f,p)['version'])
 assert not client.get('/v1/worker/fertilizer/'+p['id']).json()['can_respond'] and hashlib.sha256(client.get(url).content).digest()==hashlib.sha256(raw).digest()
 other=WorkersService(f['svc'].factory).worker(f['e'],f['a'],'TEST photo stranger',WorkerInput(display_name='TEST photo stranger',language='fa',source='test:synthetic'))
 foreign=TestClient(create_app(finance_service=f['i']['preview'][3],actor_provider=lambda:f['a'],worker_provider=lambda:(f['e'],UUID(other['id']),UUID(other['actor_id']))))
 assert foreign.get(url).status_code==404


def test_late_problem_then_done_retains_owner_attention_without_rewriting_actual(fertilizer):
 from datetime import date
 from tests.integration.test_postgres_fertilizer import confirm
 f=fertilizer;p=ready(f);confirm(f,p,actual_input(f,p));before=current(f,p);physical=effects(f)
 assert before['late_evidence_count']==0
 respond(f,p,p['cards'][0],'PROBLEM',comment='TEST late exception after accepted actual')
 respond(f,p,p['cards'][0],'DONE',comment='TEST later done does not owner-review exception')
 after=current(f,p)
 assert after['late_evidence_count']==2 and after['late_evidence_review_supported']
 assert {r['response'] for r in after['unresolved_reports']}=={'PROBLEM','DONE'}
 assert 'LATE_EVIDENCE_REQUIRES_REVIEW' in after['issues']
 assert after['actual']==before['actual'] and after['status']=='APPLIED' and effects(f)==physical
 calendar=f['svc'].calendar(f['e'],f['a'],date(2026,10,10),date(2026,10,10))
 assert calendar['attention']==1 and calendar['items'][0]['needs_owner_review'] and calendar['items'][0]['late_evidence_count']==2
 assert calendar['items'][0]['late_evidence_review_supported']
