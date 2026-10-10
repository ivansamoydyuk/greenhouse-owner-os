"""Inventory writes borrow the same atomic UoW as Inbox/Capture/Finance.

PostgreSQL generates every stock/cost effect. Managerial value uses explicit
product currency; known value and uncosted quantity never become a guessed zero.
"""
import json,base64
from functools import wraps
from uuid import UUID,uuid4
from datetime import datetime,timezone
from decimal import Decimal,localcontext
from sqlalchemy import text,select
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ValidationError,ConflictError,NotFoundError
from greenhouse_owner_os.infrastructure.models import EntityRow,WorkAreaRow,CounterpartyRow,ObligationRow
from .finance import FinanceService,_hash_request
from .owner_view import json_value

MESSAGES={'product':'Неизвестный товар: выберите из справочника','quantity':'Укажите количество','unit':'Укажите единицу','conversion':'Нет проверенного пересчёта упаковки или плотности','location':'Выберите склад','greenhouse':'Выберите теплицу','destination':'Выберите другой склад назначения','cost':'Стоимость неизвестна','allocation':'Часть теплицы не указана: распределение неизвестно','review':'Черновик требует проверки'}
def val(x):
    if isinstance(x,Decimal):return format(x,'f')
    if isinstance(x,dict):return {k:val(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [val(v) for v in x]
    return json_value(x)
def rows(u,sql,**params):return [val(dict(r)) for r in u.session.execute(text(sql),params).mappings()]

def precise(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        with localcontext() as ctx:
            ctx.prec=100
            return fn(*args,**kwargs)
    return wrapped

class InventoryService:
    def __init__(self,factory):self.factory=factory;self.finance=FinanceService(factory)
    def _lock(self,u,e):u.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('inventory:'||CAST(:e AS text),0))"),{'e':str(e)})
    def _write(self,e,a,key,operation,payload,fn):
        h=_hash_request(dict(entity_id=str(e),**payload))
        try:
            with self.factory() as u:
                u.idempotency.lock(a,operation,key)
                replay=self.finance._replay_if_exists(u,a,operation,key,h)
                if replay:return replay
                if not u.session.get(EntityRow,e):raise NotFoundError('entity not found')
                self._lock(u,e);r=fn(u)
                u.audit.append(actor_id=a,action=operation,entity_type='inventory',entity_id=UUID(r['id']),before=None,after=r)
                u.idempotency.put(actor_id=a,operation=operation,key=key,request_hash=h,resource_type='inventory',resource_id=UUID(r['id']),response=r)
                u.commit();return r
        except DBAPIError as ex:
            code=getattr(ex.orig,'sqlstate',None)
            if code=='23505':raise ConflictError('Источник или запись уже существует; проверьте дубль') from ex
            if code in ('23514','23503','55000','22P02'):
                m=str(ex.orig).split('\n')[0]
                raise ValidationError(m if any('А'<=c<='я' for c in m) else 'Проверьте товар, единицы, склад и связи события') from ex
            raise
    def products(self,e):
        with self.factory() as u:return rows(u,'SELECT * FROM inventory.products WHERE entity_id=:e ORDER BY name',e=e)
    def product(self,e,a,key,b):
        def run(u):
            names={b.name.casefold(),*(x.casefold() for x in b.aliases)}
            for r in rows(u,'SELECT * FROM inventory.products WHERE entity_id=:e',e=e):
                if names&{r['name'].casefold(),*(s.casefold() for s in r['aliases'])}:
                    if all(r[k]==v for k,v in b.model_dump(mode='json').items()):return r
                    raise ConflictError('Название или алиас уже связан с товаром')
            p=b.model_dump(mode='json');p.update(id=str(uuid4()),e=str(e),a=str(a));p['aliases']=json.dumps(p['aliases'])
            return rows(u,'INSERT INTO inventory.products(id,entity_id,name,aliases,category,base_unit,currency,supplier_reference,source,created_by) VALUES(:id,:e,:name,CAST(:aliases AS jsonb),:category,:base_unit,:currency,:supplier_reference,:source,:a) RETURNING *',**p)[0]
        return self._write(e,a,key,'inventory_product',b.model_dump(mode='json'),run)
    def conversion(self,e,a,key,p,b):
        def run(u):
            self._product(u,e,p)
            prior=rows(u,'SELECT * FROM inventory.conversions WHERE product_id=:p AND unit=:unit',p=p,unit=b.unit)
            if prior:
                if Decimal(prior[0]['factor'])==Decimal(b.factor) and prior[0]['verification']==b.verification:return prior[0]
                raise ConflictError('Проверенный пересчёт уже существует')
            return rows(u,'INSERT INTO inventory.conversions(id,product_id,unit,factor,verification,created_by) VALUES(:id,:p,:unit,CAST(:factor AS numeric),:verification,:a) RETURNING *',id=uuid4(),p=p,a=a,**b.model_dump())[0]
        return self._write(e,a,key,'inventory_conversion',dict(product_id=str(p),**b.model_dump()),run)
    def _product(self,u,e,p):
        rs=rows(u,'SELECT * FROM inventory.products WHERE id=:p AND entity_id=:e',p=p,e=e)
        if not rs:raise NotFoundError('product not found')
        return rs[0]
    def active(self,e,a,key,p,active):
        def run(u):
            self._product(u,e,p)
            return rows(u,'UPDATE inventory.products SET active=:active WHERE id=:p RETURNING *',active=active,p=p)[0]
        return self._write(e,a,key,'inventory_product_active',dict(product_id=str(p),active=active),run)
    def establish_currency(self,e,a,key,p,b):
        def run(u):
            product=self._product(u,e,p)
            if product['currency'] is not None:
                if product['currency']==b.currency:return product
                raise ConflictError('Валюта оценки уже установлена: '+product['currency']+'. Изменение запрещено')
            u.session.execute(text('INSERT INTO inventory.currency_establishments(product_id,currency,verification,established_by) VALUES(:p,:currency,:verification,:a)'),dict(p=p,a=a,**b.model_dump()))
            return self._product(u,e,p)
        return self._write(e,a,key,'inventory_valuation_currency',dict(product_id=str(p),**b.model_dump()),run)
    def location(self,e,a,key,b):
        def run(u):
            if b.work_area_id:self._area(u,e,b.work_area_id)
            old=rows(u,'SELECT * FROM inventory.locations WHERE entity_id=:e AND name=:name',e=e,name=b.name)
            if old:
                if all(old[0][k]==v for k,v in b.model_dump(mode='json').items()):return old[0]
                raise ConflictError('Склад с таким названием уже есть')
            return rows(u,'INSERT INTO inventory.locations(id,entity_id,name,work_area_id,source,created_by) VALUES(:id,:e,:name,:work_area_id,:source,:a) RETURNING *',id=uuid4(),e=e,a=a,**b.model_dump())[0]
        return self._write(e,a,key,'inventory_location',b.model_dump(mode='json'),run)
    def _area(self,u,e,aid,gid=None):
        r=u.session.get(WorkAreaRow,aid)
        if not r or r.entity_id!=e or (gid and r.parent_id!=gid):raise ValidationError('Выберите проверенную теплицу или её часть')
        return r
    def create(self,e,a,key,b):
        payload=b.model_dump(mode='json',exclude={'evidence','replacement_of_id','event_date','kind'});ev=b.evidence.model_dump(mode='json')
        def run(u):
            if b.replacement_of_id and b.evidence.structured_original:
                old=self._event(u,e,b.replacement_of_id)
                if old['evidence']!=ev or old['source']!=b.evidence.source or old['locator']!=b.evidence.locator:
                    raise ConflictError('Correction must retain the immutable original form evidence')
            for k,model in [('supplier_id',CounterpartyRow),('finance_obligation_id',ObligationRow)]:
                if payload[k]:
                    r=u.session.get(model,UUID(payload[k]))
                    if not r or r.entity_id!=e:raise ValidationError('Связанная запись относится к другой компании')
            if ev.get('import_batch_id'):
                if not rows(u,'SELECT id FROM core.import_batches WHERE id=:id AND entity_id=:e',id=ev['import_batch_id'],e=e):raise ValidationError('Import batch scope mismatch')
            for ln in b.lines:
                if ln.product_id:self._product(u,e,ln.product_id)
                for loc in [ln.location_id,ln.destination_id]:
                    if loc and not rows(u,'SELECT id FROM inventory.locations WHERE id=:id AND entity_id=:e',id=loc,e=e):raise ValidationError('Выберите склад этой компании')
                if ln.greenhouse_id:
                    r=self._area(u,e,ln.greenhouse_id)
                    if r.kind!='greenhouse':raise ValidationError('Выберите теплицу')
                if ln.work_area_id:self._area(u,e,ln.work_area_id,ln.greenhouse_id)
            prior=rows(u,'SELECT * FROM inventory.events WHERE entity_id=:e AND source=:source AND locator=:loc AND replacement_of_id IS NOT DISTINCT FROM CAST(:replacement AS uuid)',e=e,source=b.evidence.source,loc=b.evidence.locator,replacement=b.replacement_of_id) if b.evidence.locator else []
            if prior:
                r=prior[0]
                if r['kind']!=b.kind:raise ConflictError('Источник уже связан с другим видом складского события. Проверьте исходное событие; повторное движение запрещено')
                if (r['payload'],r['evidence'],r['event_date'])!=(payload,ev,str(b.event_date) if b.event_date else None):raise ConflictError('Данные стабильного источника изменились; оригинал сохранён')
                return self._view(u,r)
            r=rows(u,'INSERT INTO inventory.events(id,entity_id,kind,event_date,payload,evidence,source,locator,replacement_of_id,created_by) VALUES(:id,:e,:kind,:day,CAST(:payload AS jsonb),CAST(:evidence AS jsonb),:source,:loc,:replacement,:a) RETURNING *',id=uuid4(),e=e,kind=b.kind,day=b.event_date,payload=json.dumps(payload),evidence=json.dumps(ev),source=b.evidence.source,loc=b.evidence.locator,replacement=b.replacement_of_id,a=a)[0]
            return self._view(u,r)
        return self._write(e,a,key,'inventory_draft',b.model_dump(mode='json'),run)
    def _event(self,u,e,i):
        r=rows(u,'SELECT * FROM inventory.events WHERE entity_id=:e AND id=:id',e=e,id=i)
        if not r:raise NotFoundError('inventory event not found')
        return r[0]
    @precise
    def _view(self,u,r):
        issues=[];converted=[]
        products={x['id']:x for x in rows(u,'SELECT * FROM inventory.products WHERE entity_id=:e',e=r['entity_id'])}
        for idx,ln in enumerate(r['payload']['lines']):
            p=products.get(ln['product_id']);q=None
            if not p or not p['active']:issues.append('product')
            if not ln['quantity']:issues.append('quantity')
            if not ln['unit']:issues.append('unit')
            if p and ln['quantity'] and ln['unit']:
                if ln['unit']==p['base_unit']:q=Decimal(ln['quantity'])
                else:
                    c=rows(u,'SELECT factor FROM inventory.conversions WHERE product_id=:p AND unit=:unit',p=p['id'],unit=ln['unit'])
                    if c:q=Decimal(ln['quantity'])*Decimal(c[0]['factor'])
                    else:issues.append('conversion')
            if not ln['location_id']:issues.append('location')
            if r['kind'] in ('application','plan'):
                if not ln['greenhouse_id']:issues.append('greenhouse')
                elif not ln['work_area_id']:issues.append('allocation')
            if r['kind']=='transfer' and (not ln['destination_id'] or ln['destination_id']==ln['location_id']):issues.append('destination')
            if r['kind']=='receipt' and ln['unit_cost'] is None:issues.append('cost')
            if q and r['kind'] in ('application','transfer','writeoff') and r['status']=='draft' and ln['location_id']:
                stock=rows(u,'SELECT quantity FROM inventory.stock WHERE entity_id=:e AND product_id=:p AND location_id=:l',e=r['entity_id'],p=p['id'],l=ln['location_id'])
                if not stock or Decimal(stock[0]['quantity'])<q:issues.append('insufficient')
            converted.append(dict(line_no=idx,quantity=val(q),base_unit=p['base_unit'] if p else None))
        if r['event_date'] is None:issues.append('date')
        if r['status']=='draft':
            issues.append('review')
            if not r['replacement_of_id'] and rows(u,"SELECT id FROM inventory.events ev WHERE ev.id<>:id AND ev.entity_id=:e AND ev.kind=:kind AND (ev.event_date IS NOT DISTINCT FROM CAST(:day AS date) AND ev.payload=CAST(:payload AS jsonb) OR CAST(:raw AS text) IS NOT NULL AND ev.evidence->>'raw_text'=CAST(:raw AS text) OR CAST(:binary AS text) IS NOT NULL AND ev.evidence->>'file_base64'=CAST(:binary AS text)) LIMIT 1",id=r['id'],e=r['entity_id'],kind=r['kind'],day=r['event_date'],payload=json.dumps(r['payload']),raw=r['evidence'].get('raw_text'),binary=r['evidence'].get('file_base64')):issues.append('duplicate')
        replacement=rows(u,'SELECT id FROM inventory.events WHERE replacement_of_id=:id',id=r['id'])
        movements=rows(u,'SELECT * FROM inventory.movements WHERE event_id=:id ORDER BY seq',id=r['id'])
        return dict(r,replacement_id=replacement[0]['id'] if replacement else None,issues=list(dict.fromkeys(issues)),issue_messages=[MESSAGES.get(i,{'insufficient':'Недостаточно товара на складе','date':'Укажите дату события','duplicate':'Возможный дубль: требуется явная проверка'}.get(i,i)) for i in dict.fromkeys(issues)],converted=converted,movements=movements,posted=r['status']=='posted')
    def get(self,e,i):
        with self.factory() as u:return self._view(u,self._event(u,e,i))
    def post(self,e,a,key,i,b):
        def run(u):
            r=self._event(u,e,i)
            if r['status']=='posted':return self._view(u,r)
            if r['status']!='draft' or r['version']!=b.expected_version:raise ConflictError('Событие уже изменено')
            if r['evidence']['verification']=='HISTORY':raise ValidationError('Исторический источник не проводится в текущие остатки')
            if r['kind']=='receipt':
                parent=r['evidence'].get('source_document')
                if parent:
                    prior=rows(u,"SELECT ev.id,ev.evidence FROM inventory.events ev WHERE ev.entity_id=:e AND ev.kind='receipt' AND ev.status='posted' AND ev.id<>:id AND ev.evidence->>'source_document'=:s",e=e,id=i,s=parent)
                    if prior and (r['source']!='owner:r2-action' or any(v['evidence'].get('source')!='owner:r2-action' for v in prior)):raise ConflictError('Этот источник уже принят: используйте связь со счётом, не второй приход')
                for ln in r['payload']['lines']:
                    if ln['unit_cost'] is not None and ln['product_id']:
                        currency=self._product(u,e,ln['product_id'])['currency']
                        if currency is None:raise ValidationError('Сначала установите валюту оценки товара для прихода с известной стоимостью')
                        if ln['currency']!=currency:raise ValidationError('Валюта цены не совпадает с валютой оценки товара: '+currency+'. Автоматический пересчёт валют не выполняется')
            r=rows(u,"UPDATE inventory.events SET status='posted',version=version+1,posted_by=:a,posted_at=clock_timestamp(),duplicate_reviewed=:duplicate WHERE id=:i RETURNING *",a=a,i=i,duplicate=b.duplicate_reviewed)[0]
            return self._view(u,r)
        return self._write(e,a,key,'inventory_post',dict(id=str(i),**b.model_dump()),run)
    def reverse(self,e,a,key,i,b):
        def run(u):
            r=self._event(u,e,i)
            if r['status']=='reversed':
                if r['reversal_reason']!=b.reason or r['reversal_event_date']!=str(b.event_date):raise ConflictError('Причина отмены уже сохранена')
                return self._view(u,r)
            if r['status']!='posted':raise ValidationError('Отменить можно проведённое событие')
            r=rows(u,"UPDATE inventory.events SET status='reversed',version=version+1,reversed_by=:a,reversed_at=clock_timestamp(),reversal_reason=:reason,reversal_event_date=:day WHERE id=:i RETURNING *",a=a,i=i,reason=b.reason,day=b.event_date)[0]
            return self._view(u,r)
        return self._write(e,a,key,'inventory_reverse',dict(id=str(i),**b.model_dump(mode='json')),run)
    def overview(self,e,date_from=None,date_to=None):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            if not u.session.get(EntityRow,e):raise NotFoundError('entity not found')
            return self.read(u,e,date_from,date_to)
    @precise
    def read(self,u,e,date_from=None,date_to=None):
        products=rows(u,'SELECT * FROM inventory.products WHERE entity_id=:e ORDER BY name',e=e)
        locations=rows(u,'SELECT * FROM inventory.locations WHERE entity_id=:e ORDER BY name',e=e)
        stock=rows(u,'SELECT s.*,p.base_unit,p.currency,p.name product_name,l.name location_name FROM inventory.stock s JOIN inventory.products p ON p.id=s.product_id JOIN inventory.locations l ON l.id=s.location_id WHERE s.entity_id=:e ORDER BY p.name,l.name',e=e)
        events=[self._view(u,r) for r in rows(u,'SELECT * FROM inventory.events WHERE entity_id=:e ORDER BY created_at DESC,id LIMIT 100',e=e)]
        drafts=[self._view(u,r) for r in rows(u,"SELECT * FROM inventory.events WHERE entity_id=:e AND status='draft' ORDER BY created_at DESC,id",e=e)]
        attention=[dict(type='inventory_review',kind='inventory',id=r['id'],message='; '.join(r['issue_messages'])) for r in drafts if not r['replacement_id']]
        attention.extend(dict(type='inbox_review',kind='inbox',id=r['id'],message='Проверьте входящий складской источник · '+r['state']) for r in rows(u,"SELECT id,state FROM core.inbox_items WHERE entity_id=:e AND route='INVENTORY' AND state NOT IN ('CONFIRMED','REJECTED')",e=e))
        attention.extend(dict(type='uncosted_stock',kind='inventory_stock',id=r['product_id'],message='Стоимость неизвестна: '+r['product_name']+' · '+r['uncosted_quantity']+' '+r['base_unit']) for r in stock if Decimal(r['uncosted_quantity'])>0)
        applied=rows(u,"SELECT m.product_id,p.name product_name,p.base_unit,p.currency,m.greenhouse_id,m.work_area_id,-sum(m.quantity) quantity,-sum(m.uncosted_quantity) uncosted_quantity,-sum(m.known_cost) known_cost FROM inventory.movements m JOIN inventory.events ev ON ev.id=m.event_id JOIN inventory.products p ON p.id=m.product_id WHERE m.entity_id=:e AND ev.kind='application' AND ev.event_date>=COALESCE(CAST(:df AS date),'-infinity'::date) AND ev.event_date<=COALESCE(CAST(:dt AS date),'infinity'::date) GROUP BY m.product_id,p.name,p.base_unit,p.currency,m.greenhouse_id,m.work_area_id HAVING sum(m.quantity)<>0",e=e,df=date_from,dt=date_to)
        totals=[]
        for p in products:
            ss=[s for s in stock if s['product_id']==p['id']]
            totals.append(dict(product_id=p['id'],base_unit=p['base_unit'],quantity=val(sum((Decimal(s['quantity']) for s in ss),Decimal(0))),uncosted_quantity=val(sum((Decimal(s['uncosted_quantity']) for s in ss),Decimal(0))),known_cost=val(sum((Decimal(s['known_cost']) for s in ss),Decimal(0))),currency=p['currency']))
        recent=rows(u,"SELECT m.product_id,p.base_unit,ev.kind,sum(m.quantity) signed_quantity FROM inventory.movements m JOIN inventory.events ev ON ev.id=m.event_id JOIN inventory.products p ON p.id=m.product_id WHERE m.entity_id=:e AND ev.kind IN ('receipt','application','writeoff') AND ev.event_date>=COALESCE(CAST(:df AS date),CURRENT_DATE-30) AND ev.event_date<=COALESCE(CAST(:dt AS date),CURRENT_DATE) GROUP BY m.product_id,p.base_unit,ev.kind",e=e,df=date_from,dt=date_to)
        movements=rows(u,'SELECT m.*,ev.event_date,p.base_unit FROM inventory.movements m JOIN inventory.events ev ON ev.id=m.event_id JOIN inventory.products p ON p.id=m.product_id WHERE m.entity_id=:e ORDER BY seq DESC LIMIT 200',e=e)
        findings=[s for s in stock if Decimal(s['quantity'])<0 or Decimal(s['uncosted_quantity'])<0 or Decimal(s['uncosted_quantity'])>Decimal(s['quantity']) or Decimal(s['known_cost'])<0]
        for r in events:
            r['evidence'].pop('file_base64',None)
        return dict(products=products,locations=locations,stock=stock,totals=totals,events=events,attention=attention,applied=applied,recent=recent,movements=movements,opening_state='UNINITIALIZED_VERIFY',valuation='MANAGERIAL_MOVING_AVERAGE',invariants=dict(ok=not findings,findings=findings))
