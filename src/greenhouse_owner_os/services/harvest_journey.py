"""Read/append adapters for Harvest. No journal or original row is rewritten."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID,uuid4
from sqlalchemy import select,text
from greenhouse_owner_os.domain.errors import ValidationError,ConflictError,NotFoundError
from greenhouse_owner_os.domain.harvest_journey import apportion,ALGORITHM
from greenhouse_owner_os.infrastructure.harvest_models import ReportRow,EvidenceRow,BatchRow,SettlementRow,LineRow,AllocationRow
from greenhouse_owner_os.infrastructure.models import WorkAreaRow
from .harvest import HarvestService,row_dict,root
from .owner_capture import OwnerCaptureService,rows
from .capture import BorrowedUow

class HarvestJourneyService:
    def __init__(self,factory):self.factory=factory;self.owner=OwnerCaptureService(factory);self.harvest=HarvestService(factory)
    def _revision(self,u,e,a,sid,b,kind):
        receipt=self.harvest._get(u,e,sid,True)
        if receipt.status=='reversed':raise ConflictError('Отменённый документ: откройте исправленный документ')
        if kind=='ESTIMATE' and receipt.status!='posted':raise ValidationError('Сначала подтвердите документ депо; оценка доступна только для неизменяемых подтверждённых строк')
        latest=rows(u,'SELECT coalesce(max(version),0) v FROM harvest.journey_revisions WHERE settlement_id=:s',s=sid)[0]['v']
        if latest!=b.expected_version:raise ConflictError('Проверка устарела. Откройте текущую связь и повторите подтверждение')
        return receipt,latest+1
    def _insert(self,u,e,a,sid,b,kind,version,**extras):
        rid=uuid4()
        rows(u,'INSERT INTO harvest.journey_revisions(id,entity_id,settlement_id,version,kind,reason,created_by,link_revision_id,total_basis_count,basis_description,algorithm) VALUES(:id,:e,:s,:v,:k,:reason,:a,:link,:total,:description,:algorithm) RETURNING id',
             id=rid,e=e,s=sid,v=version,k=kind,reason=b.reason,a=a,link=extras.get('link'),total=extras.get('total'),description=extras.get('description'),algorithm=extras.get('algorithm'))
        return rid
    def _links(self,u,sid):return [r['batch_id'] for r in rows(u,'SELECT batch_id FROM harvest.current_batch_links(:s) ORDER BY batch_id',s=sid)]
    def _view(self,u,e,sid):
        receipt=self.harvest._get(u,e,sid)
        history=rows(u,'SELECT * FROM harvest.journey_revisions WHERE settlement_id=:s ORDER BY version',s=sid)
        links=[r for r in history if r['kind']=='LINKS'];estimates=[];attachments=[]
        lid=links[-1]['id'] if links else None
        for r in history:
            if r['kind']=='LINKS':r['batch_ids']=[x['batch_id'] for x in rows(u,'SELECT batch_id FROM harvest.journey_links WHERE revision_id=:r ORDER BY batch_id',r=r['id'])]
            if r['kind']=='ESTIMATE':
                r['basis']=rows(u,'SELECT work_area_id,basis_count FROM harvest.estimate_basis WHERE revision_id=:r ORDER BY work_area_id',r=r['id'])
                r['lines']=rows(u,'SELECT line_id,work_area_id,net_weight_kg,amount FROM harvest.estimate_lines WHERE revision_id=:r ORDER BY line_id,work_area_id NULLS LAST',r=r['id'])
                r['unknown_basis_count']=str(Decimal(r['total_basis_count'])-sum((Decimal(x['basis_count']) for x in r['basis']),Decimal(0)))
                r['stale']=r['link_revision_id']!=lid;r['classification']='ESTIMATED';r['affects_finance']=False;estimates.append(r)
            if r['kind']=='EVIDENCE':
                ev=rows(u,'SELECT evidence_id FROM harvest.journey_evidence WHERE revision_id=:r',r=r['id'])[0]['evidence_id']
                r['evidence']=row_dict(u.session.get(EvidenceRow,UUID(ev)));attachments.append(r)
        current=estimates[-1] if estimates and not estimates[-1]['stale'] else None
        bids=self._links(u,sid)
        reports=rows(u,'SELECT DISTINCT r.id FROM harvest.worker_reports r JOIN harvest.batch_reports b ON b.report_id=r.id WHERE b.batch_id=ANY(CAST(:ids AS uuid[])) ORDER BY r.id',ids=bids) if bids else []
        return dict(id=str(sid),version=history[-1]['version'] if history else 0,batch_ids=bids,report_ids=[r['id'] for r in reports],link_revision_id=lid,link_history=links,
                    estimates=estimates,current_estimate=current,attachments=attachments,original_evidence_id=str(receipt.evidence_id),financial_effect_changed=False)
    def get(self,e,a,sid):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));self.owner.owner(u,e,a)
            return self._view(u,e,sid)
    def links(self,e,a,sid,key,b):
        def run(u):
            receipt,v=self._revision(u,e,a,sid,b,'LINKS')
            all_ids=sorted(set(self._links(u,sid))|{str(x) for x in b.batch_ids})
            for bid in all_ids:
                batch=u.session.scalar(select(BatchRow).where(BatchRow.id==UUID(bid)).with_for_update())
                if not batch or batch.entity_id!=e:raise ValidationError('Партия принадлежит другой компании')
            for bid in b.batch_ids:
                occupied=rows(u,"SELECT s.id FROM harvest.depot_settlements s CROSS JOIN LATERAL harvest.current_batch_links(s.id) l WHERE l.batch_id=:b AND s.id<>:s AND s.status<>'reversed'",b=bid,s=sid)
                if occupied:raise ConflictError('Партия уже связана с действующим документом')
            rid=self._insert(u,e,a,sid,b,'LINKS',v)
            for bid in sorted(b.batch_ids,key=str):rows(u,'INSERT INTO harvest.journey_links(revision_id,batch_id) VALUES(:r,:b) RETURNING batch_id',r=rid,b=bid)
            return self._view(u,e,sid)
        return self.owner._write(e,a,key,'harvest_journey_links',dict(id=str(sid),**b.model_dump(mode='json')),run)
    def estimate_preview(self,e,a,sid,b):
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));self.owner.owner(u,e,a)
            receipt=self.harvest._get(u,e,sid)
            current=self._view(u,e,sid)
            if receipt.status!='posted':raise ValidationError('Сначала подтвердите документ депо')
            if current['version']!=b.expected_version:raise ConflictError('Проверка устарела')
            lines=list(u.session.scalars(select(LineRow).where(LineRow.settlement_id==sid).order_by(LineRow.id)))
            if u.session.scalar(select(AllocationRow.line_id).join(LineRow,LineRow.id==AllocationRow.line_id).where(LineRow.settlement_id==sid).limit(1)):raise ValidationError('Точное распределение нельзя заменять оценкой')
            aids={x.work_area_id for x in b.basis}
            for x in b.basis:
                area=self.harvest._area(u,e,x.work_area_id)
                if area.parent_id in aids:raise ValidationError('Пересекающиеся части в основании')
            basis={x.work_area_id:x.basis_count for x in b.basis};result=[]
            for line in lines:
                kg=apportion(line.net_weight_kg,'.001',basis,b.total_basis_count);money=apportion(line.amount,'.01',basis,b.total_basis_count)
                for aid in kg:result.append(dict(line_id=str(line.id),original_quality_name=line.original_quality_name,work_area_id=aid,net_weight_kg=str(kg[aid]),amount=str(money[aid]),confirmed_count=None))
            return dict(id=str(sid),version=current['version'],classification='ESTIMATED',algorithm=ALGORITHM,lines=result,currency=receipt.currency,total_amount=str(receipt.total_amount),
                total_weight_kg=str(sum((l.net_weight_kg for l in lines),Decimal(0))),total_basis_count=str(b.total_basis_count),unknown_basis_count=str(b.total_basis_count-sum(basis.values(),Decimal(0))),
                link_revision_id=current['link_revision_id'],affects_finance=False,requires_owner_confirmation=True)
    def estimate(self,e,a,sid,key,b):
        def run(u):
            receipt,v=self._revision(u,e,a,sid,b,'ESTIMATE')
            lines=list(u.session.scalars(select(LineRow).where(LineRow.settlement_id==sid).order_by(LineRow.id)))
            if u.session.scalar(select(AllocationRow.line_id).join(LineRow,LineRow.id==AllocationRow.line_id).where(LineRow.settlement_id==sid).limit(1)):
                raise ValidationError('У документа есть точное распределение. Используйте проверяемое исправление документа; оценка его не заменяет')
            area_ids={x.work_area_id for x in b.basis}
            for item in b.basis:
                area=self.harvest._area(u,e,item.work_area_id)
                if area.parent_id in area_ids:raise ValidationError('Теплица и её часть не могут повторяться в основании')
            current=rows(u,"SELECT id FROM harvest.journey_revisions WHERE settlement_id=:s AND kind='LINKS' ORDER BY version DESC LIMIT 1",s=sid)
            rid=self._insert(u,e,a,sid,b,'ESTIMATE',v,link=current[0]['id'] if current else None,total=b.total_basis_count,description=b.basis_description,algorithm=ALGORITHM)
            for item in sorted(b.basis,key=lambda x:str(x.work_area_id)):
                rows(u,'INSERT INTO harvest.estimate_basis(revision_id,work_area_id,basis_count) VALUES(:r,:w,:n) RETURNING work_area_id',r=rid,w=item.work_area_id,n=item.basis_count)
            basis={x.work_area_id:x.basis_count for x in b.basis}
            for line in lines:
                weights=apportion(line.net_weight_kg,'.001',basis,b.total_basis_count);amounts=apportion(line.amount,'.01',basis,b.total_basis_count)
                for aid in weights:
                    rows(u,'INSERT INTO harvest.estimate_lines(id,revision_id,line_id,work_area_id,net_weight_kg,amount) VALUES(:id,:r,:l,:w,:kg,:amount) RETURNING id',id=uuid4(),r=rid,l=line.id,w=aid,kg=weights[aid],amount=amounts[aid])
            return self._view(u,e,sid)
        return self.owner._write(e,a,key,'harvest_journey_estimate',dict(id=str(sid),**b.model_dump(mode='json')),run)
    def attachment(self,e,a,sid,key,b):
        def run(u):
            receipt,v=self._revision(u,e,a,sid,b,'EVIDENCE')
            ev=self.harvest._evidence(u,e,a,b.evidence)
            if ev.source=='owner_form:v1:field_harvest' or u.session.scalar(select(ReportRow.id).where(ReportRow.evidence_id==ev.id).limit(1)):raise ValidationError('Оригинал поля не является документом депо')
            if ev.id==receipt.evidence_id or rows(u,'SELECT 1 FROM harvest.journey_evidence j JOIN harvest.journey_revisions r ON r.id=j.revision_id WHERE r.settlement_id=:s AND j.evidence_id=:e',s=sid,e=ev.id):raise ConflictError('Этот оригинал уже прикреплён')
            rid=self._insert(u,e,a,sid,b,'EVIDENCE',v)
            rows(u,'INSERT INTO harvest.journey_evidence(revision_id,evidence_id) VALUES(:r,:e) RETURNING revision_id',r=rid,e=ev.id)
            return self._view(u,e,sid)
        return self.owner._write(e,a,key,'harvest_journey_evidence',dict(id=str(sid),**b.model_dump(mode='json')),run)
    def report(self,e,a,rid):
        with self.factory() as u:
            self.owner.owner(u,e,a)
            r=u.session.get(ReportRow,rid)
            if not r or r.entity_id!=e:raise NotFoundError('Срез не найден')
            return dict(row_dict(r),kind='report',evidence=row_dict(u.session.get(EvidenceRow,r.evidence_id)))
    def monthly(self,e,a,year,greenhouse_id=None,metric='branches',currency='TRY',allocation='all'):
        if allocation not in ('all','exact','estimated') or metric not in ('branches','kg','revenue') or currency not in ('TRY','USD','EUR','UAH') or not 1900<=year<=9998:raise ValidationError('Недопустимый период или показатель')
        start,end=date(year,10,1),date(year+1,10,1)
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'));self.owner.owner(u,e,a)
            areas={str(r.id):r for r in u.session.scalars(select(WorkAreaRow).where(WorkAreaRow.entity_id==e))}
            if greenhouse_id and (str(greenhouse_id) not in areas or areas[str(greenhouse_id)].kind!='greenhouse'):raise ValidationError('Теплица не найдена в этой компании')
            gid=str(greenhouse_id) if greenhouse_id else None
            events=[]
            if metric=='branches':
                reports=u.session.scalars(select(ReportRow).where(ReportRow.entity_id==e,ReportRow.event_date>=start,ReportRow.event_date<end).order_by(ReportRow.event_date,ReportRow.id))
                for r in reports:
                    if gid and str(r.greenhouse_id)!=gid:continue
                    value=str(r.cut_count) if r.cut_count is not None else None
                    events.append(dict(kind='report',id=str(r.id),report_ids=[str(r.id)],event_date=str(r.event_date),name=r.original_greenhouse,value=value,exact_value=value,estimated_value='0',unallocated_value='0',missing=r.cut_count is None,evidence_id=str(r.evidence_id),source_ids=[str(r.evidence_id)],greenhouse_id=str(r.greenhouse_id) if r.greenhouse_id else None,unit='веток',field_weight_kg=str(r.field_weight_kg) if r.field_weight_kg is not None else None))
            else:
                query=select(SettlementRow).where(SettlementRow.entity_id==e,SettlementRow.status=='posted',SettlementRow.event_date>=start,SettlementRow.event_date<end).order_by(SettlementRow.event_date,SettlementRow.id)
                if metric=='revenue':query=query.where(SettlementRow.currency==currency)
                for receipt in u.session.scalars(query):
                    lines=list(u.session.scalars(select(LineRow).where(LineRow.settlement_id==receipt.id).order_by(LineRow.source_line,LineRow.id)))
                    journey=self._view(u,e,receipt.id);estimate=journey['current_estimate']
                    sources=[str(receipt.evidence_id)];sources.extend(x['id'] for x in journey['link_history']);sources.extend(x['evidence']['id'] for x in journey['attachments'])
                    if estimate:sources.append(estimate['id'])
                    source_capture=rows(u,'SELECT p.inbox_id FROM core.confirmation_effects c JOIN core.proposed_actions p ON p.id=c.proposal_id WHERE c.settlement_id=:s',s=receipt.id)
                    sources.extend(r['inbox_id'] for r in source_capture)
                    reviewed=rows(u,'SELECT q.line_no,q.quality,q.proposal_id,q.reviewed_by,r.version,r.basis FROM harvest.receipt_quality_reviews q JOIN core.confirmation_effects c ON c.proposal_id=q.proposal_id JOIN core.proposal_revisions r ON r.proposal_id=c.proposal_id AND r.version=c.version WHERE q.settlement_id=:s ORDER BY q.line_no',s=receipt.id)
                    exact=estimated=unallocated=Decimal(0);full=Decimal(0);qualities=[];selected_known=False;exact_known=False;estimated_known=False
                    for line in lines:
                        full+=line.amount if metric=='revenue' else line.net_weight_kg
                        allocs=list(u.session.scalars(select(AllocationRow).where(AllocationRow.line_id==line.id)))
                        assigned=Decimal(0)
                        for x in allocs:
                            val=x.amount if metric=='revenue' else x.net_weight_kg;assigned+=val
                            if gid is None or str(root(areas[str(x.work_area_id)]))==gid:exact+=val;selected_known=True;exact_known=True
                        if estimate:
                            for x in estimate['lines']:
                                if x['line_id']!=str(line.id):continue
                                val=Decimal(x['amount'] if metric=='revenue' else x['net_weight_kg'])
                                if x['work_area_id'] is None:continue
                                assigned+=val
                                if gid is None or str(root(areas[x['work_area_id']]))==gid:estimated+=val;selected_known=True;estimated_known=True
                        unallocated+=(line.amount if metric=='revenue' else line.net_weight_kg)-assigned
                        qualities.append(dict(id=str(line.id),original_quality_name=line.original_quality_name,quality=line.normalized_quality,provenance='OWNER_RECEIPT_REVIEW' if reviewed else 'LEGACY_UNVERIFIED',net_weight_kg=str(line.net_weight_kg),amount=str(line.amount),count=line.count,source_line=line.source_line))
                    linked_roots={r['greenhouse_id'] for r in rows(u,'SELECT DISTINCT greenhouse_id FROM harvest.worker_reports WHERE id=ANY(CAST(:ids AS uuid[]))',ids=journey['report_ids'])} if journey['report_ids'] else set()
                    linked_roots.discard(None)
                    if receipt.greenhouse_id:linked_roots.add(str(receipt.greenhouse_id))
                    unknown_basis=Decimal(estimate['unknown_basis_count']) if estimate else Decimal(0)
                    missing=bool(gid and (unallocated>0 or unknown_basis>0) and (selected_known or gid in linked_roots))
                    if gid and not selected_known and not missing:continue
                    value=full if gid is None else exact+estimated if selected_known else None
                    # Farm totals are exact source totals; estimates describe attribution only.
                    events.append(dict(kind='settlement',id=str(receipt.id),settlement_id=str(receipt.id),report_ids=journey['report_ids'],event_date=str(receipt.event_date),name=receipt.document_number or 'Документ депо',value=str(value) if value is not None else None,
                        exact_value=str(full if gid is None else exact),estimated_value=str(0 if gid is None else estimated),unallocated_value=str(unallocated),missing=missing,
                        evidence_id=str(receipt.evidence_id),source_ids=sorted(set(sources)),owner_source_id=source_capture[0]['inbox_id'] if source_capture else None,
                        currency=receipt.currency,unit=receipt.currency if metric=='revenue' else 'кг',qualities=qualities,quality_reviews=reviewed,
                        estimate_revision_id=estimate['id'] if estimate else None,estimate_version=estimate['version'] if estimate else None,
                        unknown_basis_count=str(unknown_basis),has_exact_attribution=exact_known,has_estimated_attribution=estimated_known,estimate_confirmed_at=estimate['created_at'] if estimate else None,attribution_estimated_value=str(estimated),current_link_revision_id=journey['link_revision_id']))
            if allocation!='all':
                selected=[]
                for event in events:
                    item=dict(event)
                    if metric=='branches':
                        if allocation=='exact':selected.append(item)
                        continue
                    if allocation=='estimated':
                        if not item['has_estimated_attribution']:continue
                        item['value']=item['attribution_estimated_value'];item['exact_value']='0';item['estimated_value']=item['value']
                        if gid is None:item['missing']=Decimal(item['unallocated_value'])>0 or Decimal(item['unknown_basis_count'])>0
                    elif gid:
                        if not item['has_exact_attribution']:continue
                        item['value']=item['exact_value'];item['estimated_value']='0'
                    selected.append(item)
                events=selected
            def summary(items):
                vals=[Decimal(x['value']) for x in items if x['value'] is not None]
                known=sum(not x['missing'] for x in items);missing=len(items)-known
                return dict(value=str(sum(vals,Decimal(0))) if vals else None,
                    exact_value=str(sum((Decimal(x['exact_value']) for x in items if x['exact_value'] is not None),Decimal(0))) if vals else None,
                    estimated_value=str(sum((Decimal(x['estimated_value']) for x in items),Decimal(0))) if items else None,
                    unallocated_value=str(sum((Decimal(x['unallocated_value']) for x in items),Decimal(0))) if items else None,
                    coverage=dict(known=known,missing=missing,total=len(items),complete=bool(items) and not missing),
                    state='no_data' if not items else 'incomplete' if missing else 'complete',source_ids=sorted({s for x in items for s in x['source_ids']}),events=items)
            months=[]
            for offset in range(12):
                month=(9+offset)%12+1;myear=year+(9+offset)//12;key=f'{myear:04}-{month:02}'
                months.append(dict(month=key,**summary([x for x in events if x['event_date'].startswith(key)])))
            return dict(filters=dict(season_start=str(start),season_end=str(end),season_start_year=year,greenhouse_id=gid,metric=metric,allocation=allocation,currency=currency if metric=='revenue' else None,basis='CURRENT_ACTIVE_POSTED',unit='веток' if metric=='branches' else 'кг' if metric=='kg' else currency),
                months=months,total=summary(events),greenhouses=[dict(id=k,name=v.name) for k,v in sorted(areas.items()) if v.kind=='greenhouse'],
                policy='Текущие действующие документы по дате события; это не исторический баланс на дату. Оценки отделены от точных исходных значений.')
