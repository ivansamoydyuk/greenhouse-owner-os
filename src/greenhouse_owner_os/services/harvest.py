"""Source-owned Harvest workflow. Borrowed UoW retains ONE transaction boundary.

Finance owns journal, allocation, advance and reversal mechanics. Field/source
values are never repaired from a receipt, and dates never imply matching.
"""
import base64,hashlib,json
from collections import defaultdict,Counter
from datetime import datetime,date
from decimal import Decimal
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import select,text
from greenhouse_owner_os.domain.errors import ValidationError,ConflictError,NotFoundError
from greenhouse_owner_os.domain.harvest import quality,line_amount
from greenhouse_owner_os.domain.cash import AdvanceApplicationCommand
from greenhouse_owner_os.domain.finance import Obligation,ObligationDirection,RecordStatus
from greenhouse_owner_os.domain.commands import CreatePaymentCommand,AttributionInput,ReverseCommand
from greenhouse_owner_os.infrastructure.models import WorkAreaRow,CounterpartyRow,EntityRow,PaymentRow,ApplicationRow
from greenhouse_owner_os.infrastructure.harvest_models import EvidenceRow,ReportRow,BatchRow,BatchReportRow,SettlementRow,SettlementBatchRow,LineRow,AllocationRow
from .finance import FinanceService,_hash_request,_serialize_obligation
from .capture import BorrowedUow
from .owner_view import json_value

ALIASES={'poncho':'Özvadi','arabik':'Yeşilöz','green house':'İmamlı','greenhouse':'İmamlı','озвади':'Özvadi','имамлы':'İmamlı','имаммлы':'İmamlı','ешилоз':'Yeşilöz','ешилёз':'Yeşilöz'}
ISSUES={'unknown_greenhouse':'Неизвестная теплица','missing_report':'Нет отчёта о срезе','missing_depot':'Нет чека депо','unmatched_batch':'Чек не связан с партией','mixed_receipt':'Смешанный чек','unallocated':'Распределение по теплицам неизвестно','count_discrepancy':'Количество в поле и депо отличается','weight_discrepancy':'Вес в поле и депо отличается','amount_discrepancy':'Сумма источника отличается от расчёта','unknown_quality':'Неизвестное качество','missing_due_date':'Не указана ожидаемая дата оплаты','receipt_draft':'Чек ещё не проведён'}
def harvest_json(v):
    if isinstance(v,Decimal):return format(v,'f')
    if isinstance(v,dict):return {k:harvest_json(x) for k,x in v.items()}
    if isinstance(v,list):return [harvest_json(x) for x in v]
    return json_value(v)

def row_dict(r):return {c.name:harvest_json(getattr(r,c.name)) for c in r.__table__.columns if c.name!='file_bytes'}

def root(area):return area.parent_id or area.id

class HarvestService:
    def __init__(self,factory):self.factory=factory;self.finance=FinanceService(factory)
    def _write(self,entity,actor,key,operation,payload,fn):
        h=_hash_request(dict(entity_id=str(entity),**payload))
        with self.factory() as u:
            u.idempotency.lock(actor,operation,key)
            replay=self.finance._replay_if_exists(u,actor,operation,key,h)
            if replay:return replay
            if not u.session.get(EntityRow,entity):raise NotFoundError('entity not found')
            r=fn(u)
            u.audit.append(actor_id=actor,action=operation,entity_type=r.get('kind','harvest'),entity_id=UUID(r['id']),before=None,after=r)
            u.idempotency.put(actor_id=actor,operation=operation,key=key,request_hash=h,resource_type='harvest',resource_id=UUID(r['id']),response=r)
            u.commit();return r
    def _greenhouse(self,u,e,label):
        if not label:return None
        normal=ALIASES.get(' '.join(label.casefold().split()),label)
        rows=list(u.session.scalars(select(WorkAreaRow).where(WorkAreaRow.entity_id==e,WorkAreaRow.kind=='greenhouse')))
        return next((r.id for r in rows if r.name.casefold()==normal.casefold()),None)
    def _area(self,u,e,aid,gid=None):
        a=u.session.get(WorkAreaRow,aid)
        if not a or a.entity_id!=e or (gid and root(a)!=gid):raise ValidationError('work area belongs to another entity or greenhouse')
        return a
    def _evidence(self,u,e,actor,body,source_item_id=None,distinct_source_reviewed=False):
        if body.existing_id:
            r=u.session.get(EvidenceRow,body.existing_id)
            if not r or r.entity_id!=e:raise ValidationError('existing evidence scope mismatch')
            return r
        raw=None
        if body.file_base64:
            try:raw=base64.b64decode(body.file_base64,validate=True)
            except ValueError:raise ValidationError('invalid file encoding')
            signatures={'application/pdf':b'%PDF-','image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8\xff'}
            if not raw.startswith(signatures[body.mime_type]) or len(raw)>5242880:raise ValidationError('file type or size invalid; maximum 5 MB')
        fingerprint=hashlib.sha256(raw if raw is not None else (body.raw_text if body.raw_text is not None else body.source_reference).encode()).hexdigest()
        binary_identity=raw is not None
        if source_item_id is not None:
            # The immutable Inbox retains the original (including large PDFs).
            # Share its content identity with manual/R1 evidence, not a new hash
            # of the proposal UUID: a later manual upload must not post twice.
            from greenhouse_owner_os.infrastructure.inbox_models import InboxRow
            original=u.session.get(InboxRow,source_item_id)
            if not original or original.entity_id!=e or original.source!='owner:r2' or body.source!='owner:r2-action':raise ValidationError('original capture scope mismatch')
            fingerprint=original.fingerprint
            binary_identity=original.file_bytes is not None
        stable=body.source_reference is not None
        locks=[]
        if stable:
            locks.append('harvest-source:'+json.dumps([str(e),body.source,body.source_reference],ensure_ascii=False))
        locks.append('harvest-content:'+str(e)+('binary:' if binary_identity else 'text:')+fingerprint)
        # Lock all relevant identities in a fixed order, across independent request keys.
        for key in sorted(locks):
            u.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':key})
        if stable:
            existing=u.session.scalar(select(EvidenceRow).where(EvidenceRow.entity_id==e,EvidenceRow.source==body.source,EvidenceRow.source_reference==body.source_reference))
            if existing:
                if existing.raw_text!=body.raw_text or existing.file_bytes!=raw:
                    raise ConflictError('stable evidence source content changed; original retained')
                return existing
        if (source_item_id is not None and (binary_identity or not distinct_source_reviewed)) or (raw is not None and (not stable or body.source in ('owner_mobile:manual','inbox:manual'))):
            if u.session.scalar(select(EvidenceRow.id).where(EvidenceRow.entity_id==e,EvidenceRow.fingerprint==fingerprint)):
                raise ConflictError('duplicate binary document; original evidence retained')
        elif not stable:
            existing=u.session.scalar(select(EvidenceRow).where(EvidenceRow.entity_id==e,EvidenceRow.file_bytes.is_(None),EvidenceRow.fingerprint==fingerprint).order_by(EvidenceRow.received_at,EvidenceRow.id).limit(1))
            if existing and existing.source_reference is not None:raise ConflictError('possible duplicate text source; provide the original source reference or explicitly reuse its evidence')
            if existing:return existing
        r=EvidenceRow(id=uuid4(),entity_id=e,created_by=actor,fingerprint=fingerprint,file_bytes=raw,**body.model_dump(exclude={'file_base64','existing_id'}))
        u.session.add(r);u.session.flush();return r
    def report(self,e,actor,key,body):
        def run(u):
            evidence=self._evidence(u,e,actor,body.evidence)
            prior=u.session.scalar(select(ReportRow).where(ReportRow.evidence_id==evidence.id))
            if prior:
                if body.evidence.source_reference is not None and all(getattr(prior,k)==v for k,v in body.model_dump(exclude={'evidence'}).items()):
                    return dict(row_dict(prior),kind='report',issues=[] if prior.greenhouse_id else ['unknown_greenhouse'])
                raise ConflictError('duplicate worker evidence or changed report; original report retained')
            gid=self._greenhouse(u,e,body.original_greenhouse)
            if body.work_area_id:self._area(u,e,body.work_area_id,gid)
            r=ReportRow(id=uuid4(),entity_id=e,created_by=actor,greenhouse_id=gid,evidence_id=evidence.id,**body.model_dump(exclude={'evidence'}))
            u.session.add(r);u.session.flush();return dict(row_dict(r),kind='report',issues=[] if gid else ['unknown_greenhouse'])
        return self._write(e,actor,key,'harvest_report',body.model_dump(mode='json'),run)
    def batch(self,e,actor,key,body):
        def run(u):
            ids=sorted(body.report_ids,key=str);reports=[]
            for rid in ids:
                r=u.session.scalar(select(ReportRow).where(ReportRow.id==rid).with_for_update())
                if not r or r.entity_id!=e:raise ValidationError('report scope mismatch')
                if u.session.scalar(select(BatchReportRow.batch_id).where(BatchReportRow.report_id==rid)):raise ConflictError('report already assigned to a batch')
                reports.append(r)
            gid=self._greenhouse(u,e,body.original_greenhouse)
            r=BatchRow(id=uuid4(),entity_id=e,created_by=actor,greenhouse_id=gid,**body.model_dump(exclude={'report_ids'}))
            u.session.add(r);u.session.flush()
            for rid in ids:u.session.add(BatchReportRow(batch_id=r.id,report_id=rid))
            u.session.flush();return dict(row_dict(r),kind='batch',report_ids=[str(x) for x in ids])
        return self._write(e,actor,key,'harvest_batch',body.model_dump(mode='json'),run)
    def settlement(self,e,actor,key,body,*,reviewed_qualities=None,source_item_id=None,distinct_source_reviewed=False):
        if reviewed_qualities is not None:
            if len(reviewed_qualities)!=len(body.lines) or any(q not in ('HIGHEST','FIRST','SECOND') for q in reviewed_qualities):raise ValidationError('Explicit receipt quality review required')
            if body.evidence.source!='owner:r2-action':raise ValidationError('Scoped proposal adapter required')
        def run(u):
            cp=u.session.get(CounterpartyRow,body.depot_id)
            if not cp or cp.entity_id!=e or cp.counterparty_type not in ('depot','customer','both'):raise ValidationError('select an existing depot/customer counterparty')
            evidence=self._evidence(u,e,actor,body.evidence,source_item_id,distinct_source_reviewed)
            # Serialize all lifecycle creation for this immutable evidence, including
            # existing-id corrections and retries after reversal.
            u.session.scalar(select(EvidenceRow).where(EvidenceRow.id==evidence.id).with_for_update())
            prior=u.session.scalar(select(SettlementRow).where(SettlementRow.entity_id==e,SettlementRow.evidence_id==evidence.id,SettlementRow.replacement_of_id==body.replacement_of_id))
            if prior:
                if (prior.status=='reversed' or body.replacement_of_id or body.evidence.source_reference is not None or body.evidence.existing_id) and self._same_settlement(u,prior,body):
                    return self._settlement_view(u,prior)
                raise ConflictError('duplicate receipt or changed settlement; existing settlement '+str(prior.id))
            if body.document_number and u.session.scalar(select(SettlementRow.id).where(SettlementRow.entity_id==e,SettlementRow.depot_id==body.depot_id,SettlementRow.document_number==body.document_number,SettlementRow.status!='reversed')):raise ConflictError('duplicate receipt number')
            if body.replacement_of_id:
                prior=u.session.scalar(select(SettlementRow).where(SettlementRow.id==body.replacement_of_id).with_for_update())
                if not prior or (prior.entity_id,prior.depot_id,prior.currency,prior.evidence_id,prior.status)!=(e,body.depot_id,body.currency,evidence.id,'reversed'):raise ValidationError('replacement requires reversed receipt with same entity/depot/currency/evidence')
                if u.session.scalar(select(SettlementRow.id).where(SettlementRow.replacement_of_id==prior.id)):raise ConflictError('receipt already has a replacement')
            gid=self._greenhouse(u,e,body.original_greenhouse)
            total=sum((ln.confirmed_amount if ln.confirmed_amount is not None else line_amount(ln.net_weight_kg,ln.unit_price) for ln in body.lines),Decimal(0))
            r=SettlementRow(id=uuid4(),entity_id=e,created_by=actor,greenhouse_id=gid,evidence_id=evidence.id,total_amount=total,status='draft',version=1,application_ids=[],reviewed_issues=[],**body.model_dump(exclude={'lines','evidence','batch_ids'}))
            u.session.add(r);u.session.flush()
            for bid in sorted(body.batch_ids,key=str):
                b=u.session.scalar(select(BatchRow).where(BatchRow.id==bid).with_for_update())
                if not b or b.entity_id!=e:raise ValidationError('batch scope mismatch')
                if u.session.scalar(select(SettlementBatchRow.settlement_id).join(SettlementRow,SettlementRow.id==SettlementBatchRow.settlement_id).where(SettlementBatchRow.batch_id==bid,SettlementRow.status!='reversed')):raise ConflictError('batch already assigned to receipt')
                u.session.add(SettlementBatchRow(settlement_id=r.id,batch_id=bid))
            for ordinal,ln in enumerate(body.lines):
                calc=line_amount(ln.net_weight_kg,ln.unit_price);amount=ln.confirmed_amount if ln.confirmed_amount is not None else calc
                line=LineRow(id=uuid4(),settlement_id=r.id,normalized_quality=reviewed_qualities[ordinal] if reviewed_qualities is not None else quality(ln.original_quality_name),calculated_amount=calc,amount=amount,**ln.model_dump(exclude={'allocations','confirmed_amount'}))
                u.session.add(line);u.session.flush()
                for a in ln.allocations:
                    self._area(u,e,a.work_area_id)
                    u.session.add(AllocationRow(line_id=line.id,**a.model_dump()))
                u.session.flush()
            return self._settlement_view(u,r)
        return self._write(e,actor,key,'depot_draft',dict(body.model_dump(mode='json'),**({'reviewed_qualities':reviewed_qualities} if reviewed_qualities is not None else {}),**({'source_item_id':str(source_item_id),'distinct_source_reviewed':distinct_source_reviewed} if source_item_id else {})),run)
    def _same_settlement(self,u,r,body):
        # Source retries cannot silently change reviewed business values either.
        if any(getattr(r,k)!=v for k,v in body.model_dump(exclude={'evidence','lines','batch_ids'}).items()):return False
        bids=set(u.session.scalars(select(SettlementBatchRow.batch_id).where(SettlementBatchRow.settlement_id==r.id)))
        if bids!=set(body.batch_ids):return False
        lines=list(u.session.scalars(select(LineRow).where(LineRow.settlement_id==r.id)))
        def signature(ln,allocations,amount):
            values=tuple(getattr(ln,k) for k in sorted(type(body.lines[0]).model_fields) if k not in ('allocations','confirmed_amount'))
            allocation_values=tuple(sorted(((str(a.work_area_id),a.net_weight_kg,a.amount,a.confirmed_count) for a in allocations)))
            return values,amount,allocation_values
        actual=Counter(signature(ln,list(u.session.scalars(select(AllocationRow).where(AllocationRow.line_id==ln.id))),ln.amount) for ln in lines)
        expected=Counter(signature(ln,ln.allocations,ln.confirmed_amount if ln.confirmed_amount is not None else line_amount(ln.net_weight_kg,ln.unit_price)) for ln in body.lines)
        return actual==expected
    def _get(self,u,e,sid,lock=False):
        q=select(SettlementRow).where(SettlementRow.id==sid,SettlementRow.entity_id==e)
        r=u.session.scalar(q.with_for_update() if lock else q)
        if not r:raise NotFoundError('settlement not found')
        return r
    def _settlement_view(self,u,r):
        lines=list(u.session.scalars(select(LineRow).where(LineRow.settlement_id==r.id).order_by(LineRow.source_line,LineRow.id)))
        bids=list(u.session.scalars(select(SettlementBatchRow.batch_id).where(SettlementBatchRow.settlement_id==r.id)))
        report_ids=list(u.session.scalars(select(BatchReportRow.report_id).where(BatchReportRow.batch_id.in_(bids)))) if bids else []
        reports=list(u.session.scalars(select(ReportRow).where(ReportRow.id.in_(report_ids)))) if report_ids else []
        issues=[]
        if not bids:issues.append('unmatched_batch')
        if not reports:issues.append('missing_report')
        if not r.greenhouse_id and r.original_greenhouse:issues.append('unknown_greenhouse')
        if r.mixed:issues.append('mixed_receipt')
        if r.status=='draft':issues.append('receipt_draft')
        if any(l.normalized_quality=='VERIFY' for l in lines):issues.append('unknown_quality')
        if any(l.stated_amount is not None and l.stated_amount!=l.calculated_amount for l in lines) or (r.stated_total is not None and r.stated_total!=r.total_amount):issues.append('amount_discrepancy')
        field_count=sum((x.cut_count or 0 for x in reports),0) if reports and all(x.cut_count is not None for x in reports) else None
        depot_count=sum((x.count or 0 for x in lines),0) if lines and all(x.count is not None for x in lines) else None
        field_kg=sum((x.field_weight_kg for x in reports),Decimal(0)) if reports and all(x.field_weight_kg is not None for x in reports) else None
        kg=sum((x.net_weight_kg for x in lines),Decimal(0))
        if field_count is not None and depot_count is not None and field_count!=depot_count:issues.append('count_discrepancy')
        if field_kg is not None and field_kg!=kg:issues.append('weight_discrepancy')
        line_views=[]
        for l in lines:
            allocs=list(u.session.scalars(select(AllocationRow).where(AllocationRow.line_id==l.id)))
            if sum((a.amount for a in allocs),Decimal(0))<l.amount or sum((a.net_weight_kg for a in allocs),Decimal(0))<l.net_weight_kg:issues.append('unallocated')
            line_views.append(dict(row_dict(l),allocations=[row_dict(a) for a in allocs]))
        remaining=u.finance.obligation_remaining(r.obligation_id) if r.obligation_id else Decimal(0)
        if r.expected_payment and not r.due_date and (r.status=='draft' or remaining>0):issues.append('missing_due_date')
        evidenced_gids={x.greenhouse_id for x in reports if x.greenhouse_id}
        if len(evidenced_gids)>1:issues.append('mixed_receipt')
        if r.greenhouse_id and evidenced_gids and evidenced_gids!={r.greenhouse_id}:issues.append('unknown_greenhouse')
        confirmed_pairs=[l for l in lines if l.count is not None]
        confirmed_kg=sum((l.net_weight_kg for l in confirmed_pairs),Decimal(0));confirmed_count=sum((l.count for l in confirmed_pairs),0)
        evidence=u.session.get(EvidenceRow,r.evidence_id)
        replacement=u.session.scalar(select(SettlementRow.id).where(SettlementRow.replacement_of_id==r.id))
        return dict(row_dict(r),kind='settlement',lines=line_views,batch_ids=[str(b) for b in bids],reports=[row_dict(x) for x in reports],evidence=row_dict(evidence),replacement_id=str(replacement) if replacement else None,
            issues=sorted(set(issues)),reconciliation=dict(field_count=field_count,depot_count=depot_count,field_weight_kg=str(field_kg) if field_kg is not None else None,depot_weight_kg=str(kg)),
            average_weight_kg=str(confirmed_kg/confirmed_count) if confirmed_count else None,remaining_amount=f'{remaining:.2f}',overdue=bool(remaining>0 and r.due_date and r.due_date<datetime.now(ZoneInfo('Europe/Istanbul')).date()))
    def get(self,e,sid):
        with self.factory() as u:return self._settlement_view(u,self._get(u,e,sid))
    def post(self,e,actor,key,sid,body):
        def run(u):
            r=self._get(u,e,sid,True)
            if r.status!='draft' or r.version!=body.expected_version:raise ConflictError('receipt already posted or version conflict')
            view=self._settlement_view(u,r)
            required=set(view['issues'])-{'receipt_draft'}
            if body.cash_amount+sum((a.amount for a in body.advances),Decimal(0))>r.total_amount:raise ValidationError('cash and advances exceed sale amount')
            remainder=r.total_amount-body.cash_amount-sum((a.amount for a in body.advances),Decimal(0))
            if not remainder:required.discard('missing_due_date')
            if required and (not required.issubset(set(body.reviewed_issues)) or not body.review_reason or not body.review_reason.strip()):raise ValidationError('review every receipt discrepancy and provide reason: '+', '.join(sorted(required)))
            u.finance.assert_live_date(e,r.event_date)
            finance=FinanceService(lambda:BorrowedUow(u))
            ob=Obligation.draft(entity_id=e,counterparty_id=r.depot_id,direction=ObligationDirection.RECEIVABLE,original_amount=r.total_amount,currency=r.currency,event_date=r.event_date,due_date=r.due_date,offset_ledger_account_id=u.finance.get_system_ledger_account(e,r.currency,'GENERAL_REVENUE'))
            ob.economic_source='harvest_depot';ob.description='Продажа бананов · расчёт депо '+str(r.id)
            # Source link reserved before Finance INSERT; deferred FK + reciprocal
            # constraint trigger guarantee only the final coherent state can commit.
            r.obligation_id=ob.id
            u.session.flush()
            u.finance.add_obligation(ob,actor)
            totals=defaultdict(lambda:Decimal(0))
            for ln in view['lines']:
                for a in ln['allocations']:totals[UUID(a['work_area_id'])]+=Decimal(a['amount'])
            ob.attributions=tuple(AttributionInput(work_area_id=aid,amount=amt) for aid,amt in totals.items() if amt>0)
            if ob.attributions:u.finance.add_attributions(ob,actor)
            u.session.flush()
            finance.post_obligation(actor_id=actor,idempotency_key='depot:sale:'+str(sid),obligation_id=ob.id,expected_version=1,_depot_source=r.id)
            application_ids=[]
            for a in sorted(body.advances,key=lambda x:str(x.advance_id)):
                applied=finance.apply_advance(actor_id=actor,idempotency_key='depot:advance:'+str(sid)+':'+str(a.advance_id),command=AdvanceApplicationCommand(entity_id=e,counterparty_id=r.depot_id,advance_id=a.advance_id,obligation_id=ob.id,direction='received',amount=a.amount,currency=r.currency,event_date=r.event_date,source='depot:'+str(sid)))
                application_ids.append(applied['id'])
            if body.cash_amount:
                payment=finance.create_payment(actor_id=actor,idempotency_key='depot:cash:'+str(sid),command=CreatePaymentCommand(entity_id=e,counterparty_id=r.depot_id,direction='in',amount=body.cash_amount,currency=r.currency,event_date=r.event_date,money_account_id=body.money_account_id,source='depot:'+str(sid),reason='Actual payment explicitly confirmed',allocations=[dict(obligation_id=ob.id,amount=body.cash_amount)]))
                posted=finance.post_payment(actor_id=actor,idempotency_key='depot:cashpost:'+str(sid),payment_id=UUID(payment['id']),expected_version=1)
                payment_id=UUID(posted['id'])
            r.payment_id=payment_id if body.cash_amount else None
            r.application_ids=application_ids
            r.status='posted';r.version+=1;r.posted_at=datetime.now(ZoneInfo('UTC'));r.reviewed_issues=list(body.reviewed_issues);r.review_reason=body.review_reason
            u.session.flush()
            return self._settlement_view(u,r)
        return self._write(e,actor,key,'depot_post',dict(id=str(sid),**body.model_dump(mode='json')),run)
    def reverse(self,e,actor,key,sid,body):
        def run(u):
            r=self._get(u,e,sid,True)
            if r.status!='posted' or r.version!=body.expected_version:raise ConflictError('receipt is not active or version conflict')
            if r.payment_id:
                p=u.finance.get_payment(r.payment_id,for_update=True)
                if p.status==RecordStatus.POSTED:raise ValidationError('reverse the actual cash payment separately before reversing receipt')
            finance=FinanceService(lambda:BorrowedUow(u))
            for aid in sorted(r.application_ids):
                a=u.finance.get_cash_record('advance_application',UUID(aid),for_update=True)
                if a.status==RecordStatus.POSTED:finance.reverse_cash(kind='advance_application',resource_id=a.id,actor_id=actor,idempotency_key='depot:unapply:'+str(sid)+':'+aid,command=ReverseCommand(expected_version=a.version,event_date=body.event_date,reason=body.reason))
            ob=u.finance.get_obligation(r.obligation_id,for_update=True)
            finance._reverse('obligation',ob.id,actor,'depot:reverse:'+str(sid),ReverseCommand(expected_version=ob.version,event_date=body.event_date,reason=body.reason),_depot_source=r.id)
            r.status='reversed';r.version+=1;r.reversed_at=datetime.now(ZoneInfo('UTC'));r.reversal_reason=body.reason
            u.session.flush();return self._settlement_view(u,r)
        return self._write(e,actor,key,'depot_reverse',dict(id=str(sid),**body.model_dump(mode='json')),run)
    def overview(self,e,start=None,end=None):
        with self.factory() as u:
            if not isinstance(u,BorrowedUow):u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            if not u.session.get(EntityRow,e):raise NotFoundError('entity not found')
            if start and end and start>end:raise ValidationError('date range invalid')
            in_range=lambda r:(not start or r.event_date>=start) and (not end or r.event_date<=end)
            reports=list(u.session.scalars(select(ReportRow).where(ReportRow.entity_id==e).order_by(ReportRow.event_date.desc(),ReportRow.id)))
            batches=list(u.session.scalars(select(BatchRow).where(BatchRow.entity_id==e).order_by(BatchRow.event_date.desc(),BatchRow.id)))
            settlements=list(u.session.scalars(select(SettlementRow).where(SettlementRow.entity_id==e).order_by(SettlementRow.event_date.desc(),SettlementRow.id)))
            views=[self._settlement_view(u,r) for r in settlements]
            attention=[]
            for r in views:
                for issue in r['issues']:
                    if r['status']=='reversed':continue
                    if issue in r['reviewed_issues'] and issue not in ('unallocated','mixed_receipt'):continue
                    attention.append(dict(type=issue,message=ISSUES[issue],kind='harvest_settlement',id=r['id']))
                if r['overdue']:attention.append(dict(type='overdue_depot',message='Депо просрочил оплату',kind='harvest_settlement',id=r['id']))
            batch_views=[]
            for b in batches:
                ids=list(u.session.scalars(select(BatchReportRow.report_id).where(BatchReportRow.batch_id==b.id)))
                linked=u.session.scalar(select(SettlementBatchRow.settlement_id).join(SettlementRow,SettlementRow.id==SettlementBatchRow.settlement_id).where(SettlementBatchRow.batch_id==b.id,SettlementRow.status!='reversed'))
                issues=([] if ids else ['missing_report'])+([] if linked else ['missing_depot'])
                batch_views.append(dict(row_dict(b),kind='batch',report_ids=[str(x) for x in ids],settlement_id=str(linked) if linked else None,issues=issues))
                for issue in issues:attention.append(dict(type=issue,message=ISSUES[issue],kind='harvest_batch',id=str(b.id)))
            for r in reports:
                if not r.greenhouse_id:attention.append(dict(type='unknown_greenhouse',message=ISSUES['unknown_greenhouse'],kind='harvest_report',id=str(r.id)))
                if not u.session.scalar(select(BatchReportRow.batch_id).where(BatchReportRow.report_id==r.id)):attention.append(dict(type='unbatched_report',message='Срез ещё не включён в партию',kind='harvest_report',id=str(r.id)))
            areas={a.id:a for a in u.session.scalars(select(WorkAreaRow).where(WorkAreaRow.entity_id==e))}
            grouped={};unallocated={}
            def bucket(gid,currency):
                target=grouped if gid else unallocated
                key=(str(gid),currency) if gid else currency
                if key not in target:target[key]=dict(greenhouse_id=str(gid) if gid else None,name=areas[gid].name if gid else 'Нераспределено',currency=currency,total_depot_kg=Decimal(0),sales_amount=Decimal('0.00'),receivable_outstanding=Decimal('0.00'),confirmed_kg=Decimal(0),confirmed_count=0,quality_mix={},settlement_status={'posted':0})
                return target[key]
            def accumulate(b,kg,amount,count,q,remaining):
                b['total_depot_kg']+=kg;b['sales_amount']+=amount;b['receivable_outstanding']+=remaining
                if count is not None:b['confirmed_kg']+=kg;b['confirmed_count']+=count
                b['quality_mix'][q]=b['quality_mix'].get(q,Decimal(0))+kg
            for r,v in zip(settlements,views):
                if r.status!='posted' or not in_range(r):continue
                # Remaining allocation is an explicitly labelled current debt
                # basis share, NOT a new revenue/weight attribution. Arbitrary
                # profitability attribution remains forbidden.
                ob_remaining=Decimal(v['remaining_amount']);assigned_debt=Decimal(0)
                for ln in v['lines']:
                    kg=Decimal(ln['net_weight_kg']);amt=Decimal(ln['amount']);count=ln['count']
                    allocated_kg=Decimal(0);allocated_amount=Decimal(0);allocated_count=0;count_complete=True
                    for a in ln['allocations']:
                        akg=Decimal(a['net_weight_kg']);aamt=Decimal(a['amount']);ac=a['confirmed_count']
                        allocated_kg+=akg;allocated_amount+=aamt;allocated_count+=ac or 0
                        if ac is None:count_complete=False
                        # Receivable geography cannot be inferred proportionally.
                        # Expose debt only when entire sale belongs to one root.
                        accumulate(bucket(root(areas[UUID(a['work_area_id'])]),r.currency),akg,aamt,ac,ln['normalized_quality'],Decimal(0))
                    remkg=kg-allocated_kg;remamt=amt-allocated_amount
                    if remkg or remamt:
                        remcount=(count-allocated_count) if count is not None and count_complete and count>allocated_count else None
                        accumulate(bucket(None,r.currency),remkg,remamt,remcount,ln['normalized_quality'],Decimal(0))
                gids={root(areas[UUID(a['work_area_id'])]) for ln in v['lines'] for a in ln['allocations']}
                if len(gids)==1 and all(sum((Decimal(a['amount']) for a in ln['allocations']),Decimal(0))==Decimal(ln['amount']) for ln in v['lines']):bucket(next(iter(gids)),r.currency)['receivable_outstanding']+=ob_remaining
                else:bucket(None,r.currency)['receivable_outstanding']+=ob_remaining
            analytics=[]
            for b in [*grouped.values(),*unallocated.values()]:
                kg=b['total_depot_kg'];ct=b['confirmed_count']
                b['average_price_kg']=str(b['sales_amount']/kg) if kg else None
                b['average_weight_kg']=str(b['confirmed_kg']/ct) if ct else None
                b['total_cut_count']=sum((r.cut_count or 0 for r in reports if in_range(r) and str(r.greenhouse_id or '')==str(b['greenhouse_id'] or '')),0)
                b['settlement_status']={status:sum(1 for v in views if (not start or date.fromisoformat(v['event_date'])>=start) and (not end or date.fromisoformat(v['event_date'])<=end) and v['status']==status and (str(v['greenhouse_id'])==str(b['greenhouse_id']))) for status in ('draft','posted','reversed')}
                analytics.append({k:harvest_json(v) for k,v in b.items()})
            return dict(entity_id=str(e),reports=[row_dict(r) for r in reports[:100]],batches=batch_views[:100],settlements=views[:100],attention=attention,analytics=analytics,date_from=str(start) if start else None,date_to=str(end) if end else None,
                total_cut_count=sum((r.cut_count or 0 for r in reports if in_range(r)),0),cut_counts=[dict(greenhouse_id=str(a.id),name=a.name,count=sum((r.cut_count or 0 for r in reports if in_range(r) and r.greenhouse_id==a.id),0)) for a in areas.values() if a.kind=='greenhouse'],unknown_cut_count=sum((r.cut_count or 0 for r in reports if in_range(r) and r.greenhouse_id is None),0),debt_attribution_policy='Exact single-greenhouse sale only; mixed outstanding stays unallocated')
