"""Source-linked purchase cards, with physical and financial effects kept separate."""
import base64
import hashlib
import json
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.domain.errors import NotFoundError, ValidationError
from greenhouse_owner_os.domain.purchases import PurchaseHistory, PurchaseReceipt, InventoryHistory, InventoryHistoryEvent
from .inventory import InventoryService, rows, precise
from .owner_capture import OwnerCaptureService
from .finance_analytics import FinanceAnalyticsService


class PurchaseHistoryService:
    def __init__(self,factory):
        self.factory=factory;self.owner=OwnerCaptureService(factory)
        self.inventory=InventoryService(factory);self.finance=FinanceAnalyticsService(factory)

    def _references(self,u,e):
        products={r['id']:r for r in rows(u,'SELECT * FROM inventory.products WHERE entity_id=:e ORDER BY name,id',e=e)}
        locations={r['id']:r for r in rows(u,'SELECT * FROM inventory.locations WHERE entity_id=:e ORDER BY name,id',e=e)}
        suppliers={r['id']:r for r in rows(u,'SELECT * FROM core.counterparties WHERE entity_id=:e ORDER BY name,id',e=e)}
        return products,locations,suppliers

    def _filters(self,u,e,date_from,date_to,supplier_id,product_id,location_id,kind,offset,limit,refs):
        if date_from and date_to and date_from>date_to:raise ValidationError('Начало периода позже окончания')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValidationError('Недопустимая страница истории')
        if kind not in (None,'receipt','application','transfer','writeoff','plan'):raise ValidationError('Неизвестный вид складского события')
        products,locations,suppliers=refs
        for value,known in ((supplier_id,suppliers),(product_id,products),(location_id,locations)):
            if value and str(value) not in known:raise ValidationError('Фильтр относится к другой компании или неизвестной записи')
        return dict(date_from=date_from,date_to=date_to,supplier_id=supplier_id,product_id=product_id,location_id=location_id,kind=kind)

    def _query(self,filters):
        clauses=['ev.entity_id=:e']
        params={}
        for key,operator in (('date_from','>='),('date_to','<=')):
            if filters[key]:clauses.append('ev.event_date '+operator+' :'+key);params[key]=filters[key]
        if filters['kind']:clauses.append('ev.kind=:kind');params['kind']=filters['kind']
        if filters['supplier_id']:
            clauses.append("""(ev.payload->>'supplier_id'=:supplier OR EXISTS(
                SELECT 1 FROM core.invoice_receipt_links l JOIN finance.obligations o ON o.id=l.finance_obligation_id
                WHERE l.receipt_id=ev.id AND l.entity_id=ev.entity_id AND o.entity_id=ev.entity_id AND o.counterparty_id=CAST(:supplier AS uuid)) OR EXISTS(
                SELECT 1 FROM finance.obligations o WHERE o.entity_id=ev.entity_id
                AND o.id::text=ev.payload->>'finance_obligation_id' AND o.counterparty_id=CAST(:supplier AS uuid)))""")
            params['supplier']=str(filters['supplier_id'])
        if filters['product_id']:
            clauses.append("EXISTS(SELECT 1 FROM jsonb_array_elements(ev.payload->'lines') ln WHERE ln->>'product_id'=:product)")
            params['product']=str(filters['product_id'])
        if filters['location_id']:
            clauses.append("EXISTS(SELECT 1 FROM jsonb_array_elements(ev.payload->'lines') ln WHERE ln->>'location_id'=:location OR ln->>'destination_id'=:location)")
            params['location']=str(filters['location_id'])
        return ' AND '.join(clauses),params

    @staticmethod
    def _original(e,event):
        evidence=event['evidence'];binary=evidence.get('file_base64');structured=evidence.get('structured_original');raw=evidence.get('raw_text')
        content=None;basis='UNAVAILABLE'
        if binary:content=base64.b64decode(binary);basis='BINARY_BYTES'
        elif structured is not None:content=json.dumps(structured,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();basis='CANONICAL_TYPED_FORM_JSON'
        elif raw is not None:content=raw.encode();basis='RAW_TEXT_UTF8'
        return dict(source=event['source'],locator=event['locator'],source_document=evidence.get('source_document'),raw_text=raw,
            structured_original=structured,filename=evidence.get('filename'),mime_type=evidence.get('mime_type'),has_file=bool(binary),
            original_url=f'/v1/entities/{e}/inventory/events/{event["id"]}/original' if binary else None,
            sha256=hashlib.sha256(content).hexdigest() if content is not None else None,hash_basis=basis)

    def _event_view(self,u,e,event,refs):
        products,locations,suppliers=refs
        full=self.inventory._view(u,event);payload=event['payload'];lines=[];movements=[]
        for index,line in enumerate(payload['lines']):
            product=products.get(line.get('product_id'),{});location=locations.get(line.get('location_id'),{});destination=locations.get(line.get('destination_id'),{})
            converted=next((r for r in full['converted'] if r['line_no']==index),{})
            lines.append(dict(line_no=index,**{k:line.get(k) for k in ('product_id','original_product_name','quantity','unit','original_quantity','original_unit','location_id','destination_id','greenhouse_id','work_area_id','unit_cost','currency')},
                product_name=product.get('name'),base_quantity=converted.get('quantity'),base_unit=product.get('base_unit'),location_name=location.get('name'),destination_name=destination.get('name')))
        for movement in full['movements']:
            product=products[movement['product_id']]
            movements.append(dict(**{k:movement[k] for k in ('seq','event_id','role','product_id','location_id','quantity','uncosted_quantity','known_cost','reversal_of_seq')},
                event_date=event['reversal_event_date'] if movement['role']=='reversal' else event['event_date'],
                product_name=product['name'],location_name=locations[movement['location_id']]['name'],base_unit=product['base_unit'],currency=product['currency']))
        supplier=payload.get('supplier_id')
        return dict(**{k:full.get(k) for k in ('id','kind','event_date','status','version','created_at','replacement_of_id','replacement_id','reversal_event_date','reversal_reason','issues','issue_messages')},
            supplier_id=supplier,supplier_name=suppliers.get(supplier,{}).get('name'),finance_obligation_id=payload.get('finance_obligation_id'),
            lines=lines,movements=movements,original=self._original(e,event))

    def _documents(self,u,e,refs):
        sources,_,_,parties=self.finance._sources(u,e)
        areas={r['id']:r for r in rows(u,'SELECT * FROM core.work_areas WHERE entity_id=:e',e=e)}
        documents=self.finance._debt_documents(u,e,None,sources,areas,parties)['documents']
        return {d['obligation_id']:d for d in documents}

    def _workflows(self,u,e,receipt_id):
        sources=rows(u,"""SELECT i.* FROM core.inbox_items i WHERE i.entity_id=:e AND i.source='owner:r2' AND
            (i.metadata->>'invoice_receipt_id'=:r OR EXISTS(
                SELECT 1 FROM core.proposed_actions p JOIN core.proposal_revisions v ON v.proposal_id=p.id AND v.version=p.version
                WHERE p.entity_id=i.entity_id AND p.inbox_id=i.id AND p.action_type='INVOICE_MATCH' AND v.payload->>'receipt_id'=:r))
            ORDER BY i.created_at,i.id""",e=e,r=str(receipt_id))
        workflows=[]
        for source in sources:
            job=rows(u,'SELECT state FROM core.processing_jobs WHERE entity_id=:e AND inbox_id=:s ORDER BY created_at DESC,id DESC LIMIT 1',e=e,s=source['id'])
            proposals=rows(u,"""SELECT p.id,p.action_type,p.state,p.version,v.payload,
                    c.obligation_id,c.invoice_match_id,c.response
                FROM core.proposed_actions p JOIN core.proposal_revisions v ON v.proposal_id=p.id AND v.version=p.version
                LEFT JOIN core.confirmation_effects c ON c.proposal_id=p.id AND c.entity_id=p.entity_id
                WHERE p.entity_id=:e AND p.inbox_id=:s AND p.action_type IN ('PAYABLE','INVOICE_MATCH') ORDER BY p.slot,p.id""",e=e,s=source['id'])
            states=[]
            for p in proposals:
                states.append(dict(**{k:p[k] for k in ('id','action_type','state','version')},confirmed_effect_id=p['obligation_id'] or p['invoice_match_id'],
                    current_receipt_id=p['payload'].get('receipt_id'),finance_obligation_id=p['obligation_id'] or p['payload'].get('finance_obligation_id')))
            context=source['metadata'];workflows.append(dict(source_id=source['id'],saved_at=source['created_at'],capture_context=context.get('capture_context'),
                invoice_intent=context.get('invoice_intent'),invoice_receipt_id=context.get('invoice_receipt_id'),job_state=job[0]['state'] if job else 'UNAVAILABLE',
                filename=source['filename'],text=source['raw_text'],has_file=source['file_bytes'] is not None,
                original_url=f'/v1/entities/{e}/owner/captures/{source["id"]}/original',proposals=states))
        return workflows

    def _purchase(self,u,e,event,refs,documents):
        item=self._event_view(u,e,event,refs);links=[];document_ids=set()
        if item['finance_obligation_id']:document_ids.add(item['finance_obligation_id'])
        for link in rows(u,'SELECT * FROM core.invoice_receipt_links WHERE entity_id=:e AND receipt_id=:r ORDER BY created_at,id',e=e,r=event['id']):
            oid=link['finance_obligation_id'];doc=documents.get(oid)
            consistency='UNKNOWN'
            if doc and item['supplier_id']:consistency='MATCH' if item['supplier_id']==doc['counterparty_id'] else 'LEGACY_MISMATCH'
            if oid:document_ids.add(oid)
            links.append(dict(id=link['id'],original_source_id=link['inbox_id'],proposal_id=link['proposal_id'],created_at=link['created_at'],finance_obligation_id=oid,
                valuation_state=link['valuation_state'],supplier_consistency=consistency,note=link['note'],original_url=f'/v1/entities/{e}/owner/captures/{link["inbox_id"]}/original'))
        return dict(item,invoice_links=links,pending_invoice_actions=self._workflows(u,e,event['id']),linked_documents=[documents[oid] for oid in sorted(document_ids) if oid in documents])

    @precise
    def event_detail(self,e,a,event_id):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));self.owner.owner(u,e,a)
            event=self.inventory._event(u,e,event_id)
            return InventoryHistoryEvent.model_validate(self._event_view(u,e,event,self._references(u,e))).model_dump(mode='json')

    @precise
    def detail(self,e,a,receipt_id):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));self.owner.owner(u,e,a)
            found=rows(u,"SELECT * FROM inventory.events WHERE entity_id=:e AND id=:r AND kind='receipt'",e=e,r=receipt_id)
            if not found:raise NotFoundError('Физический приход не найден в этой компании')
            refs=self._references(u,e)
            return PurchaseReceipt.model_validate(self._purchase(u,e,found[0],refs,self._documents(u,e,refs))).model_dump(mode='json')

    @precise
    def read(self,e,a,*,purchases=False,date_from=None,date_to=None,supplier_id=None,product_id=None,location_id=None,kind=None,offset=0,limit=50):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));self.owner.owner(u,e,a)
            refs=self._references(u,e);products,locations,suppliers=refs
            filters=self._filters(u,e,date_from,date_to,supplier_id,product_id,location_id,'receipt' if purchases else kind,offset,limit,refs)
            where,params=self._query(filters);params['e']=e
            total=u.session.scalar(text('SELECT count(*) FROM inventory.events ev WHERE '+where),params)
            events=rows(u,'SELECT ev.* FROM inventory.events ev WHERE '+where+' ORDER BY ev.event_date DESC NULLS LAST,ev.created_at DESC,ev.id DESC OFFSET :offset LIMIT :limit',**params,offset=offset,limit=limit) if offset<total else []
            common=dict(entity_id=e,snapshot_at=u.session.scalar(text('SELECT transaction_timestamp()')),filters=filters,offset=offset,limit=limit,total=total,next_offset=offset+limit if offset+limit<total else None,
                available_products=[dict(id=p['id'],name=p['name'],base_unit=p['base_unit'],currency=p['currency']) for p in products.values()],
                available_locations=[dict(id=r['id'],name=r['name']) for r in locations.values()],available_suppliers=[dict(id=r['id'],name=r['name']) for r in suppliers.values()])
            if purchases:
                documents=self._documents(u,e,refs);items=[self._purchase(u,e,event,refs,documents) for event in events]
                linked={d['obligation_id']:d for item in items for d in item['linked_documents']}
                return PurchaseHistory.model_validate(dict(common,items=items,linked_documents=list(linked.values()))).model_dump(mode='json')
            stock=[]
            for r in rows(u,'SELECT * FROM inventory.stock WHERE entity_id=:e ORDER BY product_id,location_id',e=e):
                if product_id and r['product_id']!=str(product_id) or location_id and r['location_id']!=str(location_id):continue
                product=products[r['product_id']]
                stock.append(dict(**{k:r[k] for k in ('product_id','location_id','quantity','uncosted_quantity','known_cost')},product_name=product['name'],location_name=locations[r['location_id']]['name'],base_unit=product['base_unit'],currency=product['currency']))
            return InventoryHistory.model_validate(dict(common,events=[self._event_view(u,e,event,refs) for event in events],stock=stock)).model_dump(mode='json')
