"""ADR036 real PostgreSQL: source plan -> evidence -> one reviewed physical effect."""
import copy,json
from datetime import datetime,date,timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.fertilizer import *
from greenhouse_owner_os.domain.workers import WorkerInput
from greenhouse_owner_os.domain.errors import ValidationError,ConflictError,NotFoundError
from greenhouse_owner_os.domain.inventory import InventoryReverse,InventoryInput,InventoryPost
from greenhouse_owner_os.services.fertilizer import FertilizerService
from greenhouse_owner_os.services.workers import WorkersService
from greenhouse_owner_os.services.inventory import InventoryService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from tests.integration.test_postgres_readiness import preview,send
from tests.integration.test_postgres_inventory import inv,event,post,line

STAMP=datetime(2026,10,10,5,tzinfo=timezone.utc)
def source():return dict(form_id=uuid4(),captured_at=STAMP,source_kind='OWNER_FORM')
@pytest.fixture
def fertilizer(inv):
 i=inv;sf,actor,*_=i['preview'];e=UUID(i['e']);factory=lambda:SqlAlchemyUnitOfWork(sf);svc=FertilizerService(factory)
 worker=WorkersService(factory).worker(e,actor,str(uuid4()),WorkerInput(display_name='TEST fertilizer Dari',language='fa',source='test:fertilizer'))
 parts=[];refs=[]
 for n in range(2):
  p=uuid4();parts.append(p)
  with sf.begin() as db:db.execute(text("INSERT INTO core.work_areas(id,entity_id,parent_id,name,kind,source) VALUES(:p,:e,:g,:name,'work_area','test:explicit synthetic topology')"),dict(p=p,e=e,g=i['g'],name='TEST part '+str(n)))
  refs.append(svc.reference(e,actor,str(uuid4()),ReferenceInput(client_reference_id=uuid4(),captured_at=STAMP,work_area_id=p,area_da='5,00',verification='TEST explicit synthetic area, not inferred from name',effective_from=date(2026,10,1),confirmed=True)))
 liquid=send(i['c'],i['root']+'/products',dict(name='TEST liquid',base_unit='L',source='test:synthetic'))
 post(i,event(i,lines=[line(i,unit_cost='2',currency='TRY')]))
 post(i,event(i,lines=[dict(line(i,'50'),product_id=liquid['id'],original_product_name='TEST liquid',unit='L')]))
 cards=[dict(card_id=uuid4(),work_area_id=p,area_reference_id=ref['id'],mix_label='TEST mix '+str(n),worker_id=worker['id'],rendered_text='5 kg اضافه نکنید؛ 35 دقیقه، نه 50',rendering_reviewed=True,components=[dict(component_id=uuid4(),product_id=i['p']['id'],location_id=i['l']['id'],dose_per_da='2,00',unit='kg'),dict(component_id=uuid4(),product_id=liquid['id'],location_id=i['l']['id'],dose_per_da='1.5',unit='L')]) for n,(p,ref) in enumerate(zip(parts,refs))]
 body=PlanInput(source=source(),greenhouse_id=i['g'],event_date=date(2026,10,10),intended_send_at=STAMP,cards=cards)
 return dict(i=i,sf=sf,a=actor,e=e,svc=svc,w=worker,body=body,liquid=liquid)
def save(f,b=None):return f['svc'].save(f['e'],f['a'],str(uuid4()),b or f['body'])
def current(f,p):return f['svc'].get(f['e'],f['a'],UUID(p['id']))
def dispatch(f,p):
 d=current(f,p);return f['svc'].dispatch(f['e'],f['a'],str(uuid4()),UUID(p['id']),DispatchInput(client_approval_id=uuid4(),captured_at=STAMP,expected_version=d['version'],revision=d['revision'],preview_digest=d['preview_digest'],confirmed=True))
def respond(f,p,card,kind='DONE',**kw):
 d=current(f,p);return f['svc'].respond(f['e'],UUID(f['w']['actor_id']),str(uuid4()),UUID(p['id']),UUID(f['w']['id']),FertilizerResponseInput(client_response_id=uuid4(),card_id=card['card_id'],expected_revision=d['revision'],dispatch_id=d['current_dispatch_id'],response=kind,captured_at=STAMP,button_label={'DONE':'Готово','PARTIAL':'Частично','PROBLEM':'Проблема'}[kind],original_language='ru',**kw))
def ready(f):
 p=save(f);dispatch(f,p)
 for c in p['cards']:respond(f,p,c)
 return current(f,p)
def actual_input(f,p):
 d=current(f,p)
 return ActualInput(source=source(),expected_version=d['version'],revision=d['revision'],event_date=date(2026,10,10),lines=[dict(card_id=c['card_id'],component_id=ln['component_id'],report_id=c['current_report']['id'],quantity=ln['proposed_quantity'],disposition='APPLIED') for c in d['cards'] for ln in c['components']],completed_card_ids=[c['card_id'] for c in d['cards']])
def confirm(f,p,b):
 preview=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b)
 assert preview['can_confirm'],preview
 return f['svc'].actual(f['e'],f['a'],str(uuid4()),UUID(p['id']),ActualConfirm(input=b,actual_preview_digest=preview['actual_preview_digest'],confirmed=True))
def effects(f):
 with f['sf']() as db:
  return {table:db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),dict(e=f['e'])) for table in ('inventory.events','inventory.movements','finance.postings','harvest.worker_reports')}

def test_plan_worker_evidence_then_one_exact_application_and_unknown_cost(fertilizer):
 f=fertilizer;before=effects(f);p=ready(f)
 assert effects(f)==before and all(c['components'][0]['proposed_quantity']=='10' for c in p['cards'])
 assert p['input']['cards'][0]['components'][0]['dose_per_da']=='2,00'
 assert {t['unit']:t['quantity'] for t in p['totals']}=={'kg':'20','L':'15.0'}
 assert p['source_snapshot']['source_kind']=='OWNER_FORM' and p['source_snapshot']['original_text'] is None
 result=confirm(f,p,actual_input(f,p));d=current(f,p)
 assert d['status']=='APPLIED' and d['actual']['application_id']==result['application_id']
 with f['sf']() as db:
  stock=list(db.execute(text('SELECT product_id,quantity,uncosted_quantity FROM inventory.stock WHERE entity_id=:e ORDER BY product_id'),dict(e=f['e'])).mappings())
 assert {str(s['product_id']):(s['quantity'],s['uncosted_quantity']) for s in stock}=={f['i']['p']['id']:(Decimal(80),Decimal(0)),f['liquid']['id']:(Decimal(35),Decimal(35))}
 after=effects(f);assert after['inventory.events']==before['inventory.events']+1 and after['inventory.movements']==before['inventory.movements']+4 and after['finance.postings']==before['finance.postings']==0
 assert not d['remainder']

def test_unknown_area_draft_and_copy_revision_preserve_old_sources(fertilizer):
 f=fertilizer;payload=f['body'].model_dump(mode='json');payload['cards'][0]['area_reference_id']=None
 p=save(f,PlanInput(**payload));assert 'AREA_UNKNOWN' in p['issues'] and p['cards'][0]['components'][0]['proposed_quantity'] is None
 with pytest.raises(ValidationError):dispatch(f,p)
 payload=f['body'].model_dump(mode='json');payload['source']['form_id']=str(uuid4());fixed=PlanInput(**payload)
 d=f['svc'].save(f['e'],f['a'],str(uuid4()),fixed,UUID(p['id']),p['version']);assert d['revision']==2 and d['revisions'][0]['input']==p['input']
 dispatch(f,d);old=current(f,d)
 payload=fixed.model_dump(mode='json');payload['source']['form_id']=str(uuid4());payload.update(copied_from_plan_id=p['id'],copied_from_revision=2,copy_reason='TEST separately requested next application',event_date='2026-10-11');copied=PlanInput(**payload)
 fresh=save(f,copied);assert fresh['id']!=p['id'] and fresh['reports']==[] and fresh['dispatches']==[] and fresh['actual'] is None
 assert current(f,d)==old

def test_partial_problem_zero_unknown_and_selected_scope_remainder(fertilizer):
 f=fertilizer;p=save(f);dispatch(f,p);a,b=p['cards']
 problem=respond(f,p,a,'PROBLEM',comment='Нет воды',quantities=[dict(component_id=a['components'][0]['component_id'],quantity='0')])
 respond(f,p,b,'PARTIAL',comment='',quantities=[dict(component_id=b['components'][0]['component_id'],quantity='4,00')])
 d=current(f,p);assert all(x['quantity'] is None for x in d['proposed_actual_lines'] if x['unit']=='L')
 payload=actual_input(f,p).model_dump(mode='json');payload['completed_card_ids']=[];payload['lines']=payload['lines'][:1];payload['lines'][0].update(quantity='0',disposition='NOT_APPLIED',reason='TEST confirmed not applied');inp=ActualInput(**payload)
 preview=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),inp);assert 'ALL_ZERO_APPLICATION_UNSUPPORTED' in preview['issues']
 payload=actual_input(f,p).model_dump(mode='json');payload['completed_card_ids']=[];payload['lines']=payload['lines'][2:3];payload['lines'][0].update(quantity='4,00',reason='TEST explicitly reviewed partial quantity');selected=ActualInput(**payload)
 confirm(f,p,selected);d=current(f,p);assert d['status']=='PARTIALLY_APPLIED' and len(d['remainder'])==3 and not d['subsequent_scope_supported']
 newer=actual_input(f,p);preview=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),newer);assert 'SUBSEQUENT_SAME_PLAN_SCOPE_UNSUPPORTED' in preview['issues']

def test_actual_retry_concurrency_changed_source_and_stale_new_report(fertilizer):
 f=fertilizer;p=ready(f);b=actual_input(f,p);view=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b)
 respond(f,p,p['cards'][0],'PROBLEM',comment='TEST later discrepancy')
 with pytest.raises(ConflictError):f['svc'].actual(f['e'],f['a'],'stale',UUID(p['id']),ActualConfirm(input=b,actual_preview_digest=view['actual_preview_digest'],confirmed=True))
 payload=actual_input(f,p).model_dump(mode='json')
 for ln in payload['lines']:ln['reason']='TEST owner resolves latest evidence'
 b=ActualInput(**payload)
 view=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b);command=ActualConfirm(input=b,actual_preview_digest=view['actual_preview_digest'],confirmed=True)
 with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda n:f['svc'].actual(f['e'],f['a'],'actual-'+str(n),UUID(p['id']),command),range(2)))
 assert results[0]==results[1]
 payload=command.model_dump(mode='json');payload['input']['lines'][0]['quantity']='11';changed=ActualConfirm(**payload)
 with pytest.raises(ConflictError):f['svc'].actual(f['e'],f['a'],'changed',UUID(p['id']),changed)
 with f['sf']() as db:assert db.scalar(text("SELECT count(*) FROM inventory.events WHERE entity_id=:e AND kind='application'"),dict(e=f['e']))==1

@pytest.mark.parametrize('failure',['after_create','after_post'])
def test_outer_actual_rollback_has_no_inventory_source_audit_or_approval_orphan(fertilizer,monkeypatch,failure):
 f=fertilizer;p=ready(f);b=actual_input(f,p);v=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b);command=ActualConfirm(input=b,actual_preview_digest=v['actual_preview_digest'],confirmed=True)
 def snapshot():
  with f['sf']() as db:return {t:db.scalar(text("SELECT md5(coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text)::text,'[]')) FROM "+t+' r')) for t in ('inventory.events','inventory.movements','inventory.fertilizer_actuals','audit.events','audit.idempotency_keys')}
 before=snapshot();name='create' if failure=='after_create' else 'post';original=getattr(InventoryService,name)
 def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('TEST injected atomic fault')
 monkeypatch.setattr(InventoryService,name,fail)
 with pytest.raises(RuntimeError):f['svc'].actual(f['e'],f['a'],'retry-fault',UUID(p['id']),command)
 assert snapshot()==before
 monkeypatch.setattr(InventoryService,name,original);assert f['svc'].actual(f['e'],f['a'],'retry-fault',UUID(p['id']),command)['application_id']

def test_raw_sql_and_worker_entity_scope_and_immutable_history(fertilizer):
 f=fertilizer;p=save(f);dispatch(f,p);before=effects(f)
 with pytest.raises(NotFoundError):f['svc'].get(f['e'],f['a'],UUID(p['id']),uuid4())
 with pytest.raises(ValidationError):f['svc'].get(uuid4(),f['a'],UUID(p['id']))
 with pytest.raises(DBAPIError):
  with f['sf'].begin() as db:db.execute(text("UPDATE inventory.fertilizer_cards SET snapshot=snapshot||'{\"area_da\":\"20\"}' WHERE plan_id=:p"),dict(p=p['id']))
 with pytest.raises(ValidationError):
  InventoryService(lambda:SqlAlchemyUnitOfWork(f['sf'])).create(f['e'],f['a'],'orphan',__import__('greenhouse_owner_os.domain.inventory',fromlist=['InventoryInput']).InventoryInput(kind='application',event_date=date(2026,10,10),lines=[dict(line(f['i'],'1'),work_area_id=p['cards'][0]['work_area_id'])],evidence=dict(source='fertilizer:actual',locator=str(uuid4()))))
 assert effects(f)==before

def test_actual_reversal_is_history_not_new_plan_eligibility(fertilizer):
 f=fertilizer;p=ready(f);actual=confirm(f,p,actual_input(f,p))
 InventoryService(lambda:SqlAlchemyUnitOfWork(f['sf'])).reverse(f['e'],f['a'],'reverse-actual',UUID(actual['application_id']),InventoryReverse(event_date=date(2026,10,10),reason='TEST audited physical correction'))
 d=current(f,p);assert d['actual']['inventory_status']=='reversed' and not d['subsequent_scope_supported'] and d['actual']['input']['lines']
 with pytest.raises(ValidationError):f['svc'].save(f['e'],f['a'],'edit-after-actual',f['body'].model_copy(update={'source':FormSource(**source())}),UUID(p['id']),d['version'])


def test_existing_reversal_replacement_preserves_original_fertilizer_approval(fertilizer):
 f=fertilizer;p=ready(f);actual=confirm(f,p,actual_input(f,p));svc=InventoryService(lambda:SqlAlchemyUnitOfWork(f['sf']))
 original=svc.get(f['e'],UUID(actual['application_id']))
 svc.reverse(f['e'],f['a'],'reverse-for-replacement',UUID(original['id']),InventoryReverse(event_date=date(2026,10,10),reason='TEST corrected actual'))
 assert current(f,p)['status']=='ACTUAL_REVERSED'
 payload=copy.deepcopy(original['payload']);payload['lines'][0]['quantity']='9'
 child=svc.create(f['e'],f['a'],'replacement',InventoryInput(kind='application',event_date=date(2026,10,10),evidence=original['evidence'],replacement_of_id=original['id'],**payload))
 svc.post(f['e'],f['a'],'post-replacement',UUID(child['id']),InventoryPost(confirmed=True,expected_version=1))
 result=current(f,p);assert result['status']=='ACTUAL_CORRECTED' and result['actual']['replacement_id']==child['id'] and result['actual']['current_inventory_status']=='posted'
 assert result['actual']['input']==actual['input'] and len(result['actual']['application_history'])==2
