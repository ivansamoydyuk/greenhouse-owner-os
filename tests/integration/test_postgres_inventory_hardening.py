"""Explicit 0018 currency and source lifecycle acceptance on PostgreSQL."""
import copy
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4
from decimal import Decimal
from pathlib import Path
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.inventory import InventoryInput,ValuationCurrencyInput
from greenhouse_owner_os.domain.errors import ConflictError
from greenhouse_owner_os.services.inventory import InventoryService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork,SqlAlchemyAuditRepository
from tests.integration.test_postgres_inventory import inv,line,event,post,rev,view,qty
from tests.integration.test_postgres_readiness import preview,send


def currency(i,value='TRY',expected=200,key=None):
 return send(i['c'],i['root']+f"/products/{i['p']['id']}/valuation-currency",dict(currency=value,verification='TEST verified supplier document'),key=key,expected=expected)


def unknown(i):
 i['p']=send(i['c'],i['root']+'/products',dict(name='TEST Unknown KNO3',base_unit='kg',currency=None,source='test:physical'))
 assert i['p']['currency'] is None
 return i


def pool(i):
 s=next(s for s in view(i)['stock'] if s['product_id']==i['p']['id'])
 return tuple(Decimal(s[k]) for k in ['quantity','uncosted_quantity','known_cost'])


def test_currency_unknown_stock_establish_known_receipt_and_retry(inv):
 i=unknown(inv);r=post(i,event(i));assert pool(i)==(100,100,0)
 before=copy.deepcopy(r['movements']);key=str(uuid4());assert currency(i,key=key)['currency']=='TRY'
 assert currency(i,key=key)['currency']=='TRY' and currency(i)['currency']=='TRY'
 assert pool(i)==(100,100,0)
 assert i['c'].get('/v1'+i['root']+'/events/'+r['id']).json()['movements']==before
 post(i,event(i,lines=[line(i,unit_cost='20',currency='TRY')]))
 assert pool(i)==(200,100,2000)
 with i['preview'][0]() as s:
  assert s.scalar(text('SELECT count(*) FROM inventory.currency_establishments WHERE product_id=:p'),dict(p=i['p']['id']))==1


@pytest.mark.parametrize('value',['TRY','USD','EUR','UAH'])
def test_supported_currency_establishment(inv,value):
 i=unknown(inv);assert currency(i,value)['currency']==value


@pytest.mark.parametrize('value',['USD',None])
def test_established_currency_change_or_unset_rejected(inv,value):
 i=unknown(inv);currency(i);currency(i,value,409 if value else 422)
 assert next(p for p in view(i)['products'] if p['id']==i['p']['id'])['currency']=='TRY'


@pytest.mark.parametrize('value',['TRY','USD',None])
def test_currency_direct_product_sql_rejected(inv,value):
 i=unknown(inv)
 if value!='TRY':currency(i)
 with pytest.raises(DBAPIError):
  with i['preview'][0].begin() as s:s.execute(text('UPDATE inventory.products SET currency=:c WHERE id=:p'),dict(c=value,p=i['p']['id']))
 assert next(p for p in view(i)['products'] if p['id']==i['p']['id'])['currency']==(None if value=='TRY' else 'TRY')


@pytest.mark.parametrize('statement',[
 "UPDATE inventory.currency_establishments SET currency='USD' WHERE product_id=:p",
 'DELETE FROM inventory.currency_establishments WHERE product_id=:p',
 "INSERT INTO inventory.currency_establishments(product_id,currency,verification,established_by) VALUES(:p,'USD','TEST',:a)"
])
def test_establishment_basis_sql_immutable(inv,statement):
 i=unknown(inv);currency(i)
 with pytest.raises(DBAPIError):
  with i['preview'][0].begin() as s:s.execute(text(statement),dict(p=i['p']['id'],a=i['preview'][1]))


def test_established_try_rejects_usd_known_receipt_http_and_sql(inv):
 i=unknown(inv);currency(i);r=event(i,lines=[line(i,unit_cost='20',currency='USD')]);post(i,r,422)
 with pytest.raises(DBAPIError):
  with i['preview'][0].begin() as s:s.execute(text("UPDATE inventory.events SET status='posted',version=2,posted_by=:a,posted_at=now() WHERE id=:i"),dict(i=r['id'],a=i['preview'][1]))
 assert qty(i)==0


def test_currency_different_concurrent_commands_one_winner(inv):
 i=unknown(inv);service=InventoryService(lambda:SqlAlchemyUnitOfWork(i['preview'][0]));barrier=Barrier(2)
 def establish(c):
  barrier.wait()
  try:return service.establish_currency(UUID(i['e']),i['preview'][1],str(uuid4()),UUID(i['p']['id']),ValuationCurrencyInput(currency=c,verification='TEST'))['currency']
  except ConflictError:return 'CONFLICT'
 with ThreadPoolExecutor(2) as pool_:results=list(pool_.map(establish,['TRY','USD']))
 assert results.count('CONFLICT')==1
 established=next(c for c in results if c!='CONFLICT');assert currency(i,established)['currency']==established


def test_currency_late_failure_rolls_back_product_and_basis(inv,monkeypatch):
 i=unknown(inv);original=SqlAlchemyAuditRepository.append
 def fail(self,**kw):
  if kw['action']=='inventory_valuation_currency':raise RuntimeError('TEST audit failure')
  return original(self,**kw)
 with monkeypatch.context() as m:
  m.setattr(SqlAlchemyAuditRepository,'append',fail)
  with pytest.raises(RuntimeError):currency(i)
 assert next(p for p in view(i)['products'] if p['id']==i['p']['id'])['currency'] is None
 currency(i)


def source():return dict(source='mail',locator='attachment:123',raw_text='TEST original 100 kg')


@pytest.mark.parametrize('kind',['application','transfer','plan','writeoff'])
def test_stable_source_cross_kind_conflict_and_same_retry(inv,kind):
 i=inv;ev=source();root=event(i,evidence=ev);post(i,root)
 assert event(i,evidence=ev)['id']==root['id']
 body=dict(kind=kind,event_date='2026-10-05',lines=[line(i,destination_id=i['b']['id'])],evidence=ev)
 send(i['c'],i['root']+'/events',body,expected=409)
 assert qty(i)==100 and len(view(i)['events'])==1


def test_stable_source_changed_original_same_and_different_kind(inv):
 i=inv;ev=source();root=event(i,evidence=ev)
 for kind in ['receipt','application']:
  send(i['c'],i['root']+'/events',dict(kind=kind,event_date='2026-10-05',lines=[line(i)],evidence=dict(ev,raw_text='changed')),expected=409)
 assert i['c'].get('/v1'+i['root']+'/events/'+root['id']).json()['evidence']['raw_text']==ev['raw_text']


def test_distinct_locators_equal_content_and_manual_suspicion(inv):
 i=inv;a=event(i,evidence=source());b=event(i,evidence=dict(source(),locator='attachment:124'))
 assert a['id']!=b['id'] and 'duplicate' in b['issues']
 manual=dict(source='owner_mobile:inventory',raw_text=source()['raw_text']);c=event(i,evidence=manual);d=event(i,evidence=manual)
 assert c['id']!=d['id'] and 'duplicate' in d['issues']


def test_concurrent_receipt_application_source_one_root(inv):
 i=inv;service=InventoryService(lambda:SqlAlchemyUnitOfWork(i['preview'][0]));barrier=Barrier(2)
 def create(kind):
  barrier.wait()
  try:return service.create(UUID(i['e']),i['preview'][1],str(uuid4()),InventoryInput(kind=kind,event_date='2026-10-05',lines=[line(i)],evidence=source()))['id']
  except ConflictError:return 'CONFLICT'
 with ThreadPoolExecutor(2) as pool_:results=list(pool_.map(create,['receipt','application']))
 assert results.count('CONFLICT')==1 and len(view(i)['events'])==1 and qty(i)==0


@pytest.mark.parametrize('kind',['application','transfer'])
def test_sql_cross_kind_stable_root_insertion_rejected(inv,kind):
 i=inv;r=event(i,evidence=source())
 with pytest.raises(DBAPIError):
  with i['preview'][0].begin() as s:s.execute(text('INSERT INTO inventory.events(id,entity_id,kind,event_date,payload,evidence,source,locator,created_by) SELECT CAST(:new AS uuid),entity_id,:kind,event_date,payload,evidence,source,locator,created_by FROM inventory.events WHERE id=:i'),dict(new=str(uuid4()),kind=kind,i=r['id']))
 assert len(view(i)['events'])==1


def test_inbox_confirmed_inventory_rejects_direct_other_kind(inv):
 i=inv;raw='Сегодня привезли 100 кг KNO3';fields=dict(kind='receipt',event_date='2026-10-05',lines=[line(i)])
 item=send(i['c'],f"/entities/{i['e']}/inbox",dict(raw_text=raw,source='mail',locator='attachment:123'))
 confirmed=send(i['c'],f"/entities/{i['e']}/inbox/{item['id']}/confirm",dict(expected_version=1,confirmed=True,reviewed=fields,reason='TEST'),expected=200)
 ev=dict(source='mail',locator='attachment:123',raw_text=raw)
 assert event(i,evidence=ev)['id']==confirmed['inventory_id']
 send(i['c'],i['root']+'/events',dict(fields,kind='application',evidence=ev),expected=409)
 assert len(view(i)['events'])==1 and qty(i)==0


def test_stable_source_reversed_root_and_two_replacements(inv):
 i=inv;ev=source();root=event(i,evidence=ev);post(i,root);rev(i,root)
 assert event(i,evidence=ev)['id']==root['id']
 child=event(i,qty='90',evidence=ev,replacement_of_id=root['id']);post(i,child);rev(i,child)
 grandchild=event(i,qty='80',evidence=ev,replacement_of_id=child['id']);post(i,grandchild)
 assert event(i,qty='90',evidence=ev,replacement_of_id=root['id'])['id']==child['id']
 assert event(i,evidence=ev)['id']==root['id'] and qty(i)==80
 send(i['c'],i['root']+'/events',dict(kind='application',event_date='2026-10-05',lines=[line(i)],evidence=ev),expected=409)


def test_0018_conflicting_old_source_roots_fail_safely(inv,monkeypatch):
 i=inv;r=event(i,evidence=source());new=str(uuid4())
 path=Path('alembic/versions/20261006_0018_inventory_hardening.py')
 spec=importlib.util.spec_from_file_location('hardening_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 with i['preview'][0]() as s:
  with s.begin():
   s.execute(text('DROP INDEX inventory.event_source_root'))
   s.execute(text('CREATE UNIQUE INDEX event_source_root ON inventory.events(entity_id,source,locator,kind) WHERE locator IS NOT NULL AND replacement_of_id IS NULL'))
   s.execute(text("INSERT INTO inventory.events(id,entity_id,kind,event_date,payload,evidence,source,locator,created_by) SELECT CAST(:new AS uuid),entity_id,'application',event_date,payload,evidence,source,locator,created_by FROM inventory.events WHERE id=:i"),dict(new=new,i=r['id']))
   with monkeypatch.context() as m:
    m.setattr(module.op,'execute',lambda sql:s.execute(text(sql)))
    with pytest.raises(DBAPIError,match='reconciliation required'):
     with s.begin_nested():module.upgrade()
   assert s.scalar(text('SELECT count(*) FROM inventory.events WHERE id IN (:a,:b)'),dict(a=r['id'],b=new))==2
   assert 'kind' in s.scalar(text("SELECT indexdef FROM pg_indexes WHERE schemaname='inventory' AND indexname='event_source_root'"))
   s.rollback() # Entire synthetic old-schema setup is disposable, never retained.
