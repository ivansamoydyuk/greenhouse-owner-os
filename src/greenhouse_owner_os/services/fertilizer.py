"""Source-preserving fertilizer plans; only owner actual approval posts Inventory."""
import json
from datetime import date
from decimal import Decimal
from uuid import UUID,uuid4
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ConflictError,NotFoundError,ValidationError
from greenhouse_owner_os.domain.fertilizer import decimal_string
from greenhouse_owner_os.domain.inventory import InventoryInput,InventoryPost
from .inventory import InventoryService,rows,precise,val
from .finance import FinanceService,_hash_request
from .owner_capture import OwnerCaptureService
from .capture import BorrowedUow

def number(v):return Decimal(str(v).replace(',','.'))
def exact_value(v):
    s=format(v,'f');return s.rstrip('0').rstrip('.') if '.' in s else s

CANONICAL=[]
for root,parts,worker in [('Yeşilöz',[('Part 1','3'),('Part 2-1','5'),('Part 2-2','5'),('Part 3-1','3.5'),('Part 3-2','3.5')],'Umar'),('İmamlı',[(f'Part {n}','5') for n in range(1,5)],'Muhammed'),('Özvadi',[('Gazipaşa 1','7'),('Gazipaşa 2','7'),('Alanya 1 / upper','6'),('Alanya 2 / middle','6'),('Alanya 3 / lower','4')],'Inayat')]:
    CANONICAL.append(dict(key=root+':worker',greenhouse_name=root,part_name=None,area_da=None,worker_name=worker,provenance='CURRENT_DICTIONARY_OWNER_REFERENCE'))
    CANONICAL.extend(dict(key=root+':'+name,greenhouse_name=root,part_name=name,area_da=area,worker_name=None,provenance='CURRENT_DICTIONARY_OWNER_REFERENCE') for name,area in parts)

class FertilizerService:
    def __init__(self,factory):self.factory=factory;self.owner=OwnerCaptureService(factory);self.finance=FinanceService(factory)
    def _write(self,e,a,key,op,payload,fn,worker=None,nested=()):
        if not key or len(key)>200:raise ValidationError('Stable request identity required')
        h=_hash_request(dict(entity_id=str(e),**payload))
        try:
            with self.factory() as u:
                if worker:self._worker(u,e,worker,a)
                else:self.owner.owner(u,e,a)
                u.idempotency.lock(a,op,key)
                for operation,nkey in nested:u.idempotency.lock(a,operation,nkey)
                previous=self.finance._replay_if_exists(u,a,op,key,h)
                if previous:return previous
                InventoryService(self.factory)._lock(u,e)
                result=fn(u)
                u.audit.append(actor_id=a,action=op,entity_type='fertilizer',entity_id=UUID(result['id']),before=None,after=result)
                u.idempotency.put(actor_id=a,operation=op,key=key,request_hash=h,resource_type='fertilizer',resource_id=UUID(result['id']),response=result)
                u.commit();return result
        except DBAPIError as exc:
            if exc.orig.sqlstate=='23505':raise ConflictError('Идентичность или область уже сохранена') from exc
            if exc.orig.sqlstate in ('23514','23503','55000','22P02'):raise ValidationError(str(exc.orig).split('\n')[0]) from exc
            raise
    def _worker(self,u,e,w,a):
        r=rows(u,'SELECT * FROM workers.people WHERE id=:w AND entity_id=:e AND actor_id=:a AND active',w=w,e=e,a=a)
        if not r:raise NotFoundError('Worker identity unavailable')
        return r[0]
    def _plan(self,u,e,p,lock=False):
        r=rows(u,'SELECT * FROM inventory.fertilizer_plans WHERE id=:p AND entity_id=:e'+(' FOR UPDATE' if lock else ''),p=p,e=e)
        if not r:raise NotFoundError('План не найден')
        return r[0]
    def _version(self,u,p):
        return sum(int(u.session.scalar(text('SELECT count(*) FROM inventory.fertilizer_'+table+' WHERE plan_id=:p'),dict(p=p))) for table in ('revisions','dispatches','reports','actuals','resolutions'))
    def _revision(self,u,p):return rows(u,'SELECT * FROM inventory.fertilizer_revisions WHERE plan_id=:p ORDER BY revision DESC LIMIT 1',p=p)[0]
    def _scope_source(self,u,e,source):
        if source.inbox_id and not rows(u,'SELECT id FROM core.inbox_items WHERE id=:i AND entity_id=:e',i=source.inbox_id,e=e):raise ValidationError('Источник другой компании')
    def _refs(self,u,e):
        return {name:rows(u,query,e=e) for name,query in {
            'work_areas':'SELECT id,name,kind,parent_id FROM core.work_areas WHERE entity_id=:e ORDER BY name,id',
            'products':'SELECT id,name,base_unit,active FROM inventory.products WHERE entity_id=:e ORDER BY name,id',
            'locations':'SELECT id,name FROM inventory.locations WHERE entity_id=:e ORDER BY name,id',
            'workers':'SELECT id,display_name,language,active FROM workers.people WHERE entity_id=:e ORDER BY display_name,id',
            'bindings':'SELECT * FROM inventory.fertilizer_reference_versions WHERE entity_id=:e ORDER BY effective_from DESC,created_at DESC,id',
            'stock':'SELECT s.*,p.currency,p.base_unit FROM inventory.stock s JOIN inventory.products p ON p.id=s.product_id WHERE s.entity_id=:e ORDER BY product_id,location_id'}.items()}
    def references(self,e,a,event_date=None):
        with self.factory() as u:
            self.owner.owner(u,e,a);r=self._refs(u,e)
            r['greenhouses']=[x for x in r['work_areas'] if x['kind']=='greenhouse' and x['parent_id'] is None]
            r['canonical_candidates']=CANONICAL
            if event_date:r['bindings']=[x for x in r['bindings'] if x['effective_from']<=str(event_date)]
            return r
    def reference(self,e,a,key,b):
        payload=b.model_dump(mode='json')
        def run(u):
            old=rows(u,'SELECT * FROM inventory.fertilizer_reference_versions WHERE id=:i AND entity_id=:e',i=b.client_reference_id,e=e)
            if old:
                if old[0]['source_snapshot']!=payload:raise ConflictError('Исходное подтверждение справочника изменилось')
                return old[0]
            if b.canonical_key:
                candidate=next((x for x in CANONICAL if x['key']==b.canonical_key),None)
                if not candidate or (candidate['area_da'] is not None and (b.area_da is None or number(b.area_da)!=number(candidate['area_da']))) or (candidate['worker_name'] and b.default_worker_id is None):raise ValidationError('Проверьте точное справочное значение')
            return rows(u,"""INSERT INTO inventory.fertilizer_reference_versions(id,entity_id,work_area_id,area_da,default_worker_id,canonical_key,verification,effective_from,replaces_id,created_by,source_snapshot)
                VALUES(:id,:e,:work_area_id,:area_da,:default_worker_id,:canonical_key,:verification,:effective_from,:replaces_id,:a,CAST(:s AS jsonb)) RETURNING *""",id=b.client_reference_id,e=e,a=a,s=json.dumps(payload),**{k:(exact_value(number(payload[k])) if k=='area_da' and payload[k] is not None else payload[k]) for k in ('work_area_id','area_da','default_worker_id','canonical_key','verification','effective_from','replaces_id')})[0]
        return self._write(e,a,key,'fertilizer_reference',payload,run)
    @precise
    def _calculate(self,u,e,b):
        self._scope_source(u,e,b.source);refs=self._refs(u,e)
        lookup={k:{x['id']:x for x in refs[k]} for k in ('work_areas','products','locations','workers','bindings')}
        root=lookup['work_areas'].get(str(b.greenhouse_id))
        if not root or root['kind']!='greenhouse' or root['parent_id']:raise ValidationError('Выберите теплицу этой компании')
        if b.copied_from_plan_id:
            self._plan(u,e,b.copied_from_plan_id)
            if not rows(u,'SELECT id FROM inventory.fertilizer_revisions WHERE plan_id=:p AND revision=:r',p=b.copied_from_plan_id,r=b.copied_from_revision):raise ValidationError('Версия копируемого плана не найдена')
        cards=[];issues=[];totals={}
        for c in b.cards:
            part=lookup['work_areas'].get(str(c.work_area_id))
            if not part or part['parent_id']!=str(b.greenhouse_id):raise ValidationError('Часть не принадлежит теплице')
            area=None;basis=None;ci=[]
            if c.area_reference_id:
                basis=lookup['bindings'].get(str(c.area_reference_id))
                if not basis or basis['work_area_id']!=str(c.work_area_id) or basis['effective_from']>str(b.event_date) or basis['area_da'] is None:raise ValidationError('Проверьте версию площади части')
                area=basis['area_da']
            if area is None:ci.append('AREA_UNKNOWN')
            if c.mixing_intent=='UNRESOLVED':ci.append('MIXING_INTENT_REVIEW_REQUIRED')
            if c.application_id and len(c.components)>1 and c.mixing_intent !='TOGETHER':ci.append('MIXING_INTENT_REVIEW_REQUIRED')
            worker=lookup['workers'].get(str(c.worker_id)) if c.worker_id else None
            if c.worker_id and (not worker or not worker['active'] or worker['language']!='fa'):raise ValidationError('Выберите действующего работника с проверенным дари')
            if c.worker_reference_id:
                binding=lookup['bindings'].get(str(c.worker_reference_id))
                if not binding or binding['work_area_id'] not in (str(b.greenhouse_id),str(c.work_area_id)) or binding['default_worker_id']!=str(c.worker_id) or binding['effective_from']>str(b.event_date):raise ValidationError('Проверьте версию ответственного')
            if not worker:ci.append('WORKER_UNKNOWN')
            if not c.rendering_reviewed or not (c.rendered_text or '').strip():ci.append('DARI_REVIEW_REQUIRED')
            components=[]
            for ln in c.components:
                product=lookup['products'].get(str(ln.product_id));location=lookup['locations'].get(str(ln.location_id)) if ln.location_id else None
                if not product or not product['active'] or product['base_unit']!=ln.unit:raise ValidationError('Товар или точная базовая единица недоступны')
                if ln.location_id and not location:raise ValidationError('Склад другой компании')
                if not location:ci.append('LOCATION_UNKNOWN')
                if ln.photo_source_id:
                    photo=rows(u,"SELECT id FROM core.inbox_items WHERE id=:i AND entity_id=:e AND file_bytes IS NOT NULL AND mime_type IN ('image/png','image/jpeg')",i=ln.photo_source_id,e=e)
                    if not photo:raise ValidationError('Выберите существующий оригинал изображения этой компании')
                q=None;calculated=None
                if area is not None and ln.dose_per_da is not None:
                    calculated=exact_value(number(area)*number(ln.dose_per_da))
                    try:decimal_string(calculated)
                    except ValueError:ci.append('QUANTITY_PRECISION_UNSUPPORTED');calculated=None
                if ln.dose_per_da is None:ci.append('DOSE_UNKNOWN')
                if area is not None:q=exact_value(number(ln.quantity_override)) if ln.quantity_override else calculated
                components.append(dict(ln.model_dump(mode='json'),product_name=product['name'],location_name=location['name'] if location else None,calculated_quantity=calculated,proposed_quantity=q,quantity_basis='UNKNOWN' if q is None else 'OWNER_OVERRIDE' if ln.quantity_override else 'DOSE_TIMES_AREA'))
                k=(str(ln.product_id),ln.unit)
                if k not in totals:totals[k]=number(0)
                totals[k]=None if q is None or totals[k] is None else totals[k]+number(q)
            cards.append(dict(c.model_dump(mode='json'),greenhouse_name=root['name'],part_name=part['name'],worker_name=worker['display_name'] if worker else None,area_da=area,area_provenance=basis,components=components,prepared_instruction=' · '.join([part['name'],('Внесение '+str(c.application_order or 1)) if c.application_id else c.mix_label,*[x['product_name']+': '+(x['proposed_quantity'] or 'UNKNOWN')+' '+x['unit'] for x in components]]),issues=sorted(set(ci))))
            issues.extend(ci)
        if b.intended_send_at is None:issues.append('INTENDED_TIME_UNKNOWN')
        # Explicit owner sequence governs the prepared instruction. The original
        # form array remains untouched; legacy cards retain their stable order.
        cards.sort(key=lambda card:card.get('application_order',1))
        payload=b.model_dump(mode='json');digest=_hash_request(dict(input=payload,cards=cards))
        return dict(input=payload,cards=cards,totals=[dict(product_id=p,unit=unit,quantity=val(q) if q is not None else None) for (p,unit),q in totals.items()],issues=sorted(set(issues)),preview_digest=digest,can_dispatch=not issues,greenhouse=root)
    def preview(self,e,a,b):
        with self.factory() as u:
            self.owner.owner(u,e,a);return self._calculate(u,e,b)
    def save(self,e,a,key,b,p=None,expected=None):
        payload=b.model_dump(mode='json')
        def run(u):
            prior=rows(u,'SELECT * FROM inventory.fertilizer_revisions WHERE entity_id=:e AND source_id=:s',e=e,s=b.source.form_id)
            if prior:
                if prior[0]['input']!=payload or (p and prior[0]['plan_id']!=str(p)):raise ConflictError('Оригинал плана изменился под тем же ID')
                return self._view(u,e,prior[0]['plan_id'])
            if p:
                self._plan(u,e,p,True)
                if self._version(u,p)!=expected:raise ConflictError('План изменился')
                self._check_revision_identity(u,p,b)
                revision=self._revision(u,p)['revision']+1;plan_id=p
            else:
                plan_id=uuid4();revision=1
                u.session.execute(text('INSERT INTO inventory.fertilizer_plans(id,entity_id,created_by) VALUES(:p,:e,:a)'),dict(p=plan_id,e=e,a=a))
            calculated=self._calculate(u,e,b);rid=uuid4()
            u.session.execute(text('INSERT INTO inventory.fertilizer_revisions(id,entity_id,plan_id,revision,source_id,input,preview,preview_digest,created_by) VALUES(:i,:e,:p,:r,:s,CAST(:b AS jsonb),CAST(:v AS jsonb),:d,:a)'),dict(i=rid,e=e,p=plan_id,r=revision,s=b.source.form_id,b=json.dumps(payload),v=json.dumps(calculated),d=calculated['preview_digest'],a=a))
            for card in calculated['cards']:
                u.session.execute(text('INSERT INTO inventory.fertilizer_cards(revision_id,card_id,entity_id,plan_id,work_area_id,worker_id,snapshot) VALUES(:r,:c,:e,:p,:w,:worker,CAST(:s AS jsonb))'),dict(r=rid,c=card['card_id'],e=e,p=plan_id,w=card['work_area_id'],worker=card['worker_id'],s=json.dumps(card)))
            return self._view(u,e,plan_id)
        return self._write(e,a,key,'fertilizer_plan',dict(plan_id=str(p) if p else None,expected_version=expected,input=payload),run)
    def _view(self,u,e,p,w=None):
        self._plan(u,e,p);r=self._revision(u,p);current_revision=r['revision'];preview=r['preview'];version=self._version(u,p)
        dispatches=rows(u,'SELECT * FROM inventory.fertilizer_dispatches WHERE plan_id=:p ORDER BY created_at,id',p=p)
        current=next((d for d in reversed(dispatches) if d['revision_id']==r['id']),None)
        if w:
            approved=next((d for d in reversed(dispatches) if rows(u,'SELECT card_id FROM inventory.fertilizer_cards WHERE revision_id=:r AND worker_id=:w',r=d['revision_id'],w=w)),None)
            if not approved:raise NotFoundError('Approved worker cards unavailable')
            r=rows(u,'SELECT * FROM inventory.fertilizer_revisions WHERE id=:r',r=approved['revision_id'])[0];preview=r['preview'];current=approved
        reports=rows(u,'SELECT * FROM inventory.fertilizer_reports WHERE plan_id=:p ORDER BY received_at,id',p=p)
        actuals=self._actual_history(u,e,p)
        actual=actuals[-1] if actuals else None;cards=[];proposed=[]
        for raw in preview['cards']:
            if w and raw['worker_id']!=str(w):continue
            card=dict(raw);history=[x for x in reports if x['revision_id']==r['id'] and x['card_id']==card['card_id']]
            report=history[-1] if history else None;card['current_report']=report
            for line in card['components']:
                changes={q['component_id']:q['quantity'] for q in report['source_snapshot']['quantities']} if report else {}
                q=changes.get(line['component_id'],line['proposed_quantity'] if report and report['response']=='DONE' else None)
                proposed.append(dict(card_id=card['card_id'],component_id=line['component_id'],report_id=report['id'] if report else None,quantity=q,quantity_basis='WORKER_REPORTED' if line['component_id'] in changes else 'PLAN_CANDIDATE' if q is not None else 'UNKNOWN',product_id=line['product_id'],product_name=line['product_name'],unit=line['unit'],resolution_required=not report or report['response']!='DONE'))
                if line['photo_source_id']:line['photo_url']=f'/v1/worker/fertilizer/{p}/revisions/{r["revision"]}/cards/{card["card_id"]}/components/{line["component_id"]}/photo' if w else f'/v1/entities/{e}/fertilizer/plans/{p}/revisions/{r["revision"]}/cards/{card["card_id"]}/components/{line["component_id"]}/photo'
            cards.append(card)
        if w:
            if not current or not cards:raise NotFoundError('Approved worker cards unavailable')
            return dict(id=str(p),plan_id=str(p),revision=r['revision'],current_revision=current_revision,can_respond=r['revision']==current_revision,version=version,dispatch_id=current['id'],event_date=r['input']['event_date'],greenhouse=dict(id=r['input']['greenhouse_id'],name=preview['greenhouse']['name']),cards=cards,reports=[x for x in reports if x['worker_id']==str(w)])
        resolutions=rows(u,'SELECT * FROM inventory.fertilizer_resolutions WHERE plan_id=:p ORDER BY created_at,id',p=p)
        summary=self._execution_summary(u,p,preview['cards'],actuals,resolutions)
        consumed={(x.get('report_id'),x['card_id'],x['component_id']) for a in actuals for x in a['input']['lines'] if x.get('report_id')}
        used=set()
        for rep in reports:
            historical=rows(u,'SELECT snapshot FROM inventory.fertilizer_cards WHERE revision_id=:r AND card_id=:c',r=rep['revision_id'],c=rep['card_id'])
            if historical and all((rep['id'],rep['card_id'],x['component_id']) in consumed for x in historical[0]['snapshot']['components']):used.add(rep['id'])
        latest_resolutions={x['report_id']:x for x in resolutions if x['report_id']}
        resolved={rid for rid,x in latest_resolutions.items() if x['kind'] in ('EVIDENCE_ONLY','CORRECTION')}
        unresolved=[x for x in reports if (x['id'] in latest_resolutions and latest_resolutions[x['id']]['kind'] in ('CONFLICT','UNRESOLVED')) or (x['id'] not in used and x['id'] not in resolved)]
        late=[x for x in unresolved if actuals and x['received_at']>actuals[0]['created_at'] or x['revision_id']!=r['id']]
        sm={(x['card_id'],x['component_id']):x for x in summary}
        remainder=[dict(x,remaining_status='CLOSED' if sm[(x['card_id'],x['component_id'])]['closed'] else 'UNRESOLVED' if sm[(x['card_id'],x['component_id'])]['confirmed_quantity'] is None else 'UNAPPROVED') for x in proposed if not sm[(x['card_id'],x['component_id'])]['complete'] and not sm[(x['card_id'],x['component_id'])]['closed']]
        full=bool(summary) and all(x['complete'] for x in summary)
        closed=bool(summary) and all(x['complete'] or x['closed'] for x in summary)
        corrected=any(a['replacement_id'] for a in actuals)
        reversed_=bool(actuals) and all(a['current_inventory_status']=='reversed' for a in actuals if a['application_id']) and any(a['application_id'] for a in actuals)
        correction_pending=any(a['replacement_id'] and a['current_inventory_status'] not in ('posted','reversed') for a in actuals)
        status='CORRECTION_PENDING' if correction_pending else 'ACTUAL_CORRECTED' if corrected else 'ACTUAL_REVERSED' if reversed_ else 'APPLIED' if full else 'CLOSED' if closed else 'PARTIALLY_APPLIED' if any(x['confirmed_quantity'] is not None and number(x['confirmed_quantity'])>0 for x in summary) else 'REVIEWED_NOT_APPLIED' if actuals else 'APPROVED' if current else 'DRAFT'
        return dict(late_evidence_count=len(late),late_evidence_review_supported=True,id=str(p),entity_id=str(e),revision=r['revision'],version=version,status=status,input=r['input'],source_snapshot=r['input']['source'],preview_digest=r['preview_digest'],cards=cards,totals=preview['totals'],issues=preview['issues']+(['LATE_EVIDENCE_REQUIRES_REVIEW'] if late else [])+(['CORRECTION_SCOPE_REQUIRES_REVIEW'] if any(x['correction_scope_unresolved'] for x in summary) else [])+(['EVIDENCE_CONFLICT_REQUIRES_REVIEW'] if any(x['kind'] in ('CONFLICT','UNRESOLVED') for x in latest_resolutions.values()) else []),can_dispatch=preview['can_dispatch'] and not current,current_dispatch_id=current['id'] if current else None,dispatches=dispatches,reports=reports,actual=actual,actuals=actuals,execution_summary=summary,resolutions=resolutions,unresolved_reports=unresolved,revisions=rows(u,'SELECT revision,preview_digest,input,created_at,created_by FROM inventory.fertilizer_revisions WHERE plan_id=:p ORDER BY revision',p=p),proposed_actual_lines=proposed,remainder=remainder,subsequent_scope_supported=True)
    def _check_revision_identity(self,u,p,b):
        if not rows(u,'SELECT id FROM inventory.fertilizer_actuals WHERE plan_id=:p',p=p):return
        old=self._revision(u,p)['input'];new=b.model_dump(mode='json')
        if old['greenhouse_id']!=new['greenhouse_id']:raise ValidationError('После факта теплица неизменна')
        def identities(payload):
            return {c['card_id']:(c['work_area_id'],c.get('application_id'),{x['component_id']:(x['product_id'],x['unit']) for x in c['components']}) for c in payload['cards']}
        before=identities(old);after=identities(new)
        # Stable plan scope: dose/location/instruction versions may change, but
        # adding/removing/reidentifying consumed scope would disguise a new event.
        if before!=after:raise ValidationError('После факта сохраните identities частей, внесений и компонентов; меняйте дозы в новой версии')
    def _actual_history(self,u,e,p):
        actuals=rows(u,"SELECT a.*,ev.status inventory_status,(SELECT id FROM inventory.events child WHERE child.replacement_of_id=ev.id) replacement_id FROM inventory.fertilizer_actuals a LEFT JOIN inventory.events ev ON ev.id=a.application_id WHERE a.plan_id=:p ORDER BY a.created_at,a.id",p=p)
        for a in actuals:
            a['application_history']=rows(u,"WITH RECURSIVE chain AS (SELECT id,status,replacement_of_id,payload,0 depth FROM inventory.events WHERE id=:i UNION ALL SELECT c.id,c.status,c.replacement_of_id,c.payload,p.depth+1 FROM inventory.events c JOIN chain p ON c.replacement_of_id=p.id) SELECT * FROM chain ORDER BY depth",i=a['application_id']) if a['application_id'] else []
            a['current_application_id']=a['application_history'][-1]['id'] if a['application_history'] else None
            a['current_inventory_status']=a['application_history'][-1]['status'] if a['application_history'] else None
        return actuals
    def _execution_summary(self,u,p,cards,actuals,resolutions):
        facts=rows(u,'SELECT * FROM inventory.fertilizer_actual_lines WHERE plan_id=:p ORDER BY created_at,id',p=p)
        byactual={a['id']:a for a in actuals};result=[]
        for card in cards:
            for comp in card['components']:
                scope=[x for x in facts if x['card_id']==card['card_id'] and x['component_id']==comp['component_id']]
                total=number(0);known=bool(scope);ambiguous=False;known_zero=False;last_close=None
                for f in scope:
                    a=byactual[f['actual_id']];q=number(f['quantity']);known_zero|=q==0
                    if f['remainder_decision']=='CLOSE':last_close=f['created_at']
                    if a['application_id'] and q>0:
                        tail=a['application_history'][-1]
                        if tail['status']=='reversed':q=number(0)
                        elif tail['status']!='posted':ambiguous=True;continue
                        else:
                            # Generic Inventory replacement has no fertilizer step map.
                            # Repeated same product/part/location cannot safely inherit
                            # positional allocation from a replacement document.
                            peers=[x for x in facts if x['actual_id']==f['actual_id'] and number(x['quantity'])>0 and (x['product_id'],x['location_id'],x['work_area_id'],x['unit'])==(f['product_id'],f['location_id'],f['work_area_id'],f['unit'])]
                            if len(a['application_history'])>1 and len(peers)>1:ambiguous=True;continue
                            ls=tail['payload']['lines'];idx=f['inventory_line_index']
                            ln=ls[idx] if idx is not None and idx<len(ls) else None
                            if not ln or (ln['product_id'],ln['location_id'],ln.get('work_area_id'),ln['unit'])!=(f['product_id'],f['location_id'],f['work_area_id'],f['unit']):ambiguous=True;continue
                            q=number(ln['quantity'])
                    total+=q
                reopen=next((x for x in reversed(resolutions) if x['kind']=='REOPEN_SCOPE' and x['card_id']==card['card_id'] and x['component_id']==comp['component_id']),None)
                closed=bool(last_close and (not reopen or reopen['created_at']<last_close))
                planned=comp['proposed_quantity'];confirmed=None if not known or ambiguous else exact_value(total)
                remaining=None if planned is None or ambiguous else exact_value(max(number(planned)-total,number(0)))
                result.append(dict(application_id=card.get('application_id'),application_order=card.get('application_order',1),card_id=card['card_id'],component_id=comp['component_id'],product_id=comp['product_id'],unit=comp['unit'],planned_quantity=planned,confirmed_quantity=confirmed,remaining_quantity=remaining,known_zero=known_zero,unknown=not known or ambiguous,closed=closed,complete=confirmed is not None and planned is not None and total>=number(planned),correction_scope_unresolved=ambiguous,physical_execution_ids=list(dict.fromkeys(x['physical_execution_id'] for x in scope))))
        return result
    def get(self,e,a,p,w=None):
        with self.factory() as u:
            if w:self._worker(u,e,w,a)
            else:self.owner.owner(u,e,a)
            return self._view(u,e,p,w)
    def calendar(self,e,a,date_from,date_to,greenhouse_id=None,w=None):
        if date_to<date_from or (date_to-date_from).days>366:raise ValidationError('Выберите период до366 дней')
        with self.factory() as u:
            if w:self._worker(u,e,w,a)
            else:self.owner.owner(u,e,a)
            if w:
                candidates=rows(u,"""SELECT p.id FROM inventory.fertilizer_plans p JOIN LATERAL(
                    SELECT r.input FROM inventory.fertilizer_revisions r JOIN inventory.fertilizer_dispatches d ON d.revision_id=r.id
                    WHERE r.plan_id=p.id AND EXISTS(SELECT 1 FROM inventory.fertilizer_cards c WHERE c.revision_id=r.id AND c.worker_id=:w)
                    ORDER BY r.revision DESC LIMIT 1) r ON true
                    WHERE p.entity_id=:e AND (r.input->>'event_date')::date BETWEEN :f AND :t ORDER BY r.input->>'event_date',p.id""",e=e,w=w,f=date_from,t=date_to)
            else:
                candidates=rows(u,"""SELECT p.id FROM inventory.fertilizer_plans p JOIN LATERAL(SELECT input FROM inventory.fertilizer_revisions WHERE plan_id=p.id ORDER BY revision DESC LIMIT 1) r ON true
                    WHERE p.entity_id=:e AND (r.input->>'event_date')::date BETWEEN :f AND :t ORDER BY r.input->>'event_date',p.id""",e=e,f=date_from,t=date_to)
            items=[]
            for row in candidates:
                try:d=self._view(u,e,row['id'],w)
                except NotFoundError:
                    if w:continue
                    raise
                if w:items.append(d);continue
                if greenhouse_id and d['input']['greenhouse_id']!=str(greenhouse_id):continue
                counts={s:sum(bool(c['current_report']) and c['current_report']['response']==s for c in d['cards']) for s in ('DONE','PARTIAL','PROBLEM')}
                items.append(dict(id=d['id'],event_date=d['input']['event_date'],greenhouse_id=d['input']['greenhouse_id'],greenhouse_name=d['cards'][0]['greenhouse_name'],status=d['status'],revision=d['revision'],version=d['version'],card_count=len(d['cards']),done_count=counts['DONE'],partial_count=counts['PARTIAL'],problem_count=counts['PROBLEM'],needs_owner_review=bool(d['unresolved_reports']),late_evidence_count=d['late_evidence_count'],late_evidence_review_supported=True,actual_id=d['actual']['id'] if d['actual'] else None,inventory_status=d['actual']['inventory_status'] if d['actual'] else None,current_inventory_status=d['actual']['current_inventory_status'] if d['actual'] else None,current_application_id=d['actual']['current_application_id'] if d['actual'] else None,replacement_id=d['actual']['replacement_id'] if d['actual'] else None))
            return dict(items=items,attention=sum(x.get('needs_owner_review',False) for x in items),filters=dict(date_from=str(date_from),date_to=str(date_to),greenhouse_id=str(greenhouse_id) if greenhouse_id else None))
    def dispatch(self,e,a,key,p,b):
        payload=b.model_dump(mode='json')
        def run(u):
            self._plan(u,e,p,True)
            old=rows(u,'SELECT * FROM inventory.fertilizer_dispatches WHERE id=:i AND entity_id=:e',i=b.client_approval_id,e=e)
            if old:
                if old[0]['plan_id']!=str(p) or old[0]['source_snapshot']!=payload:raise ConflictError('Подтверждение изменилось')
                return old[0]
            r=self._revision(u,p)
            if self._version(u,p)!=b.expected_version or r['revision']!=b.revision or r['preview_digest']!=b.preview_digest:raise ConflictError('План изменился; проверьте новую версию')
            if not r['preview']['can_dispatch']:raise ValidationError('Заполните критические поля и проверьте дари')
            return rows(u,'INSERT INTO inventory.fertilizer_dispatches(id,entity_id,plan_id,revision_id,created_by,source_snapshot) VALUES(:i,:e,:p,:r,:a,CAST(:s AS jsonb)) RETURNING *',i=b.client_approval_id,e=e,p=p,r=r['id'],a=a,s=json.dumps(payload))[0]
        return self._write(e,a,key,'fertilizer_dispatch',dict(plan_id=str(p),input=payload),run)
    def respond(self,e,a,key,p,w,b):
        payload=dict(schema_version=1,source_kind='UI_ACTION',**b.model_dump(mode='json'))
        def run(u):
            self._plan(u,e,p,True)
            old=rows(u,'SELECT * FROM inventory.fertilizer_reports WHERE id=:i AND entity_id=:e',i=b.client_response_id,e=e)
            if old:
                if old[0]['plan_id']!=str(p) or old[0]['worker_id']!=str(w) or old[0]['source_snapshot']!=payload:raise ConflictError('Исходный ответ изменился')
                return old[0]
            r=self._revision(u,p)
            if r['revision']!=b.expected_revision:
                if not b.late_evidence:raise ConflictError('Версия плана изменилась; сохраните как поздний отчёт')
                found=rows(u,'SELECT * FROM inventory.fertilizer_revisions WHERE plan_id=:p AND revision=:r',p=p,r=b.expected_revision)
                if not found:raise NotFoundError('Историческая инструкция не найдена')
                r=found[0]
            return rows(u,'INSERT INTO inventory.fertilizer_reports(id,entity_id,plan_id,revision_id,dispatch_id,card_id,worker_id,actor_id,response,source_snapshot) VALUES(:i,:e,:p,:r,:d,:c,:w,:a,:kind,CAST(:s AS jsonb)) RETURNING *',i=b.client_response_id,e=e,p=p,r=r['id'],d=b.dispatch_id,c=b.card_id,w=w,a=a,kind=b.response,s=json.dumps(payload))[0]
        return self._write(e,a,key,'fertilizer_report',dict(plan_id=str(p),worker_id=str(w),input=payload),run,worker=w)
    @precise
    def _actual_preview(self,u,e,p,b):
        self._scope_source(u,e,b.source);d=self._view(u,e,p);issues=[];lines=[];totals={};selected=[];positive=set()
        if d['version']!=b.expected_version or d['revision']!=b.revision:raise ConflictError('План или отчёты изменились; обновите проверку')
        if d['actuals'] and b.execution_kind!='ADDITIONAL':issues.append('EXPLICIT_ADDITIONAL_EXECUTION_REQUIRED')
        if not d['current_dispatch_id']:issues.append('DISPATCH_REQUIRED')
        cards={x['card_id']:x for x in d['cards']}
        facts=rows(u,'SELECT * FROM inventory.fertilizer_actual_lines WHERE plan_id=:p',p=p)
        summary={(x['card_id'],x['component_id']):x for x in d['execution_summary']}
        for x in b.lines:
            card=cards.get(str(x.card_id));component=next((c for c in card['components'] if c['component_id']==str(x.component_id)),None) if card else None
            report=next((r for r in d['reports'] if r['id']==str(x.report_id)),None) if x.report_id else None
            if not component:raise ConflictError('Выберите точный компонент карточки')
            if report and (report['revision_id']!=self._revision(u,p)['id'] or report['card_id']!=str(x.card_id)):raise ConflictError('Исторический отчёт требует отдельного решения')
            if x.report_id and not report:raise ConflictError('Отчёт не найден')
            if report and (not card['current_report'] or card['current_report']['id']!=report['id']):raise ConflictError('Есть более новый отчёт; разберите расхождение перед подтверждением')
            execution=str(x.physical_execution_id or (report['source_snapshot'].get('physical_execution_id') or report['id'] if report else ''))
            if not execution:issues.append('PHYSICAL_EXECUTION_ID_REQUIRED')
            if report and x.physical_execution_id and execution!=str(report['source_snapshot'].get('physical_execution_id') or report['id']):issues.append('REPORT_EXECUTION_ID_MISMATCH')
            if report and report['source_snapshot'].get('quantity_mode')=='CUMULATIVE':issues.append('CUMULATIVE_REPORT_REQUIRES_RESOLUTION')
            if any(f['card_id']==str(x.card_id) and f['component_id']==str(x.component_id) and (f['physical_execution_id']==execution or report and f['report_id']==report['id']) for f in facts):issues.append('PHYSICAL_SCOPE_ALREADY_CONFIRMED')
            state=summary[(str(x.card_id),str(x.component_id))]
            if state['closed']:issues.append('REOPEN_SCOPE_REQUIRED')
            if state['correction_scope_unresolved']:issues.append('CORRECTION_SCOPE_REQUIRES_REVIEW')
            quantities={q['component_id']:q['quantity'] for q in report['source_snapshot']['quantities']} if report else {}
            proposed=quantities.get(str(x.component_id),component['proposed_quantity'] if report and report['response']=='DONE' else None)
            if (not report or report['response']!='DONE' or proposed is None or number(x.quantity)!=number(proposed) or report['source_snapshot'].get('quantity_mode')=='UNSPECIFIED') and not (x.reason or '').strip():issues.append('OWNER_RESOLUTION_REQUIRED')
            if x.remainder_decision=='CLOSE' and not (x.reason or '').strip():issues.append('CLOSURE_REASON_REQUIRED')
            selected.append(x.model_dump(mode='json'))
            if number(x.quantity)>0:
                positive.add((str(x.card_id),str(x.component_id)))
                lines.append(dict(product_id=component['product_id'],original_product_name=component['product_name'],quantity=exact_value(number(x.quantity)),unit=component['unit'],location_id=component['location_id'],greenhouse_id=d['input']['greenhouse_id'],work_area_id=card['work_area_id']))
                k=(component['product_id'],component['unit']);totals[k]=totals.get(k,number(0))+number(x.quantity)
        for cid in b.completed_card_ids:
            card=cards.get(str(cid))
            if not card or any((str(cid),c['component_id']) not in positive and not summary[(str(cid),c['component_id'])]['complete'] for c in card['components']):issues.append('COMPLETED_CARD_REQUIRES_ALL_POSITIVE_COMPONENTS')
        # Inventory owns valuation and posting. Here only disclose availability;
        # no independent cost/ledger recognition or guessed conversion.
        stock={(s['product_id'],s['location_id']):s for s in self._refs(u,e)['stock']};needed={}
        for ln in lines:
            k=(ln['product_id'],ln['location_id']);needed[k]=needed.get(k,number(0))+number(ln['quantity'])
        stock_basis=[]
        for (product,location),quantity in needed.items():
            s=stock.get((product,location));stock_basis.append(dict(product_id=product,location_id=location,quantity=s['quantity'] if s else None,uncosted_quantity=s['uncosted_quantity'] if s else None))
            if not s or number(s['quantity'])<quantity:issues.append('INSUFFICIENT_OR_UNKNOWN_STOCK')
        effect=dict(kind='application',event_date=str(b.event_date),lines=lines,stock_basis=stock_basis)
        inp=b.model_dump(mode='json');digest=_hash_request(dict(plan_id=str(p),input=inp,dispatch_id=d['current_dispatch_id'],inventory_effect=effect))
        return dict(input=inp,actual_preview_digest=digest,selected_scope=selected,totals=[dict(product_id=k[0],unit=k[1],quantity=val(q)) for k,q in totals.items()],zero_lines=[x for x in selected if x['disposition']=='NOT_APPLIED'],remainder=[x for x in d['proposed_actual_lines'] if (x['card_id'],x['component_id']) not in positive],issues=sorted(set(issues)),can_confirm=not issues,inventory_effect=effect)
    def actual_preview(self,e,a,p,b):
        with self.factory() as u:
            self.owner.owner(u,e,a);return self._actual_preview(u,e,p,b)
    def actual(self,e,a,key,p,b):
        payload=b.model_dump(mode='json');form=b.input.source.form_id;create_key='fertilizer:create:'+str(form);post_key='fertilizer:post:'+str(form)
        def run(u):
            self._plan(u,e,p,True)
            old=rows(u,'SELECT * FROM inventory.fertilizer_actuals WHERE source_id=:i AND entity_id=:e',i=form,e=e)
            if old:
                if old[0]['plan_id']!=str(p) or old[0]['source_snapshot']!=payload:raise ConflictError('Исходное подтверждение факта изменилось')
                return old[0]
            preview=self._actual_preview(u,e,p,b.input)
            if preview['actual_preview_digest']!=b.actual_preview_digest:raise ConflictError('Проверка факта изменилась')
            if not preview['can_confirm']:raise ValidationError('; '.join(preview['issues']))
            inventory=InventoryService(lambda:BorrowedUow(u));effect=preview['inventory_effect']
            event=None
            if effect['lines']:
                event=inventory.create(e,a,create_key,InventoryInput(kind='application',event_date=b.input.event_date,lines=effect['lines'],evidence=dict(source='fertilizer:actual',locator=str(form),source_document='fertilizer-plan:'+str(p),verification='VERIFIED')))
                event=inventory.post(e,a,post_key,UUID(event['id']),InventoryPost(confirmed=True,expected_version=1,duplicate_reviewed=b.input.duplicate_reviewed))
            r=self._revision(u,p)
            result=rows(u,'INSERT INTO inventory.fertilizer_actuals(id,entity_id,plan_id,revision_id,source_id,application_id,input,source_snapshot,preview_digest,created_by) VALUES(:i,:e,:p,:r,:s,:v,CAST(:b AS jsonb),CAST(:original AS jsonb),:d,:a) RETURNING *',i=uuid4(),e=e,p=p,r=r['id'],s=form,v=event['id'] if event else None,b=json.dumps(b.input.model_dump(mode='json')),original=json.dumps(payload),d=b.actual_preview_digest,a=a)[0]
            return result
        return self._write(e,a,key,'fertilizer_actual',dict(plan_id=str(p),input=payload),run,nested=(('inventory_draft',create_key),('inventory_post',post_key)))
    def resolve(self,e,a,key,p,b):
        payload=b.model_dump(mode='json')
        def run(u):
            self._plan(u,e,p,True)
            prior=rows(u,'SELECT * FROM inventory.fertilizer_resolutions WHERE entity_id=:e AND source_id=:s',e=e,s=b.source.form_id)
            if prior:
                if prior[0]['plan_id']!=str(p) or prior[0]['source_snapshot']!=payload:raise ConflictError('Исходное решение изменилось')
                return prior[0]
            d=self._view(u,e,p)
            if d['version']!=b.expected_version or d['revision']!=b.revision:raise ConflictError('Состояние изменилось; проверьте заново')
            self._scope_source(u,e,b.source)
            if b.report_id and not any(x['id']==str(b.report_id) for x in d['reports']):raise ValidationError('Отчёт другого поручения')
            if b.actual_id and not any(x['id']==str(b.actual_id) for x in d['actuals']):raise ValidationError('Факт другого поручения')
            if b.actual_id and b.report_id:
                linked_report=next(x for x in d['reports'] if x['id']==str(b.report_id))
                linked_actual=next(x for x in d['actuals'] if x['id']==str(b.actual_id))
                if not any(x['card_id']==linked_report['card_id'] for x in linked_actual['input']['lines']):raise ValidationError('Факт относится к другой части или внесению')
            if b.kind=='CORRECTION':
                actual=next(x for x in d['actuals'] if x['id']==str(b.actual_id))
                if actual['current_inventory_status'] not in ('posted','reversed') or not actual['replacement_id'] and actual['inventory_status']!='reversed':raise ValidationError('Сначала завершите существующее audited исправление складского события')
            if b.kind=='REOPEN_SCOPE' and not any(x['card_id']==str(b.card_id) and x['component_id']==str(b.component_id) and x['closed'] for x in d['execution_summary']):raise ValidationError('Выберите явно закрытый остаток')
            return rows(u,"INSERT INTO inventory.fertilizer_resolutions(id,entity_id,plan_id,source_id,kind,report_id,actual_id,card_id,component_id,source_snapshot,created_by) VALUES(:i,:e,:p,:s,:kind,:report,:actual,:card,:component,CAST(:body AS jsonb),:a) RETURNING *",i=uuid4(),e=e,p=p,s=b.source.form_id,kind=b.kind,report=b.report_id,actual=b.actual_id,card=b.card_id,component=b.component_id,body=json.dumps(payload),a=a)[0]
        return self._write(e,a,key,'fertilizer_resolution',dict(plan_id=str(p),input=payload),run)
    def photo(self,e,a,p,revision,card,component,w=None):
        with self.factory() as u:
            if w:self._worker(u,e,w,a)
            else:self.owner.owner(u,e,a)
            self._plan(u,e,p)
            rows_=rows(u,'SELECT c.snapshot FROM inventory.fertilizer_cards c JOIN inventory.fertilizer_revisions r ON r.id=c.revision_id WHERE c.plan_id=:p AND r.revision=:r AND c.card_id=:c',p=p,r=revision,c=card)
            if not rows_:raise NotFoundError('Изображение недоступно')
            snapshot=rows_[0]['snapshot']
            if w and (snapshot['worker_id']!=str(w) or not rows(u,'SELECT d.id FROM inventory.fertilizer_dispatches d JOIN inventory.fertilizer_revisions r ON r.id=d.revision_id WHERE d.plan_id=:p AND r.revision=:r',p=p,r=revision)):raise NotFoundError('Изображение недоступно')
            ln=next((x for x in snapshot['components'] if x['component_id']==str(component)),None)
            if not ln or not ln['photo_source_id']:raise NotFoundError('Фото отсутствует')
            image=u.session.execute(text("SELECT file_bytes,mime_type FROM core.inbox_items WHERE id=:i AND entity_id=:e AND mime_type IN ('image/png','image/jpeg')"),dict(i=ln['photo_source_id'],e=e)).first()
            if not image or not image[0]:raise NotFoundError('Фото отсутствует')
            return bytes(image[0]),image[1]
