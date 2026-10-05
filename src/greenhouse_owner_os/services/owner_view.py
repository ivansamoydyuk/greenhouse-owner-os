"""Read-only owner projection. Amounts come from Finance's PostgreSQL views.

One REPEATABLE READ snapshot; drafts/history never enter live amounts. No writes,
repair or alternative accounting rules. Existing command services own mutations.
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select, text
from greenhouse_owner_os.domain.errors import NotFoundError
from greenhouse_owner_os.infrastructure.models import (
    EntityRow, ReferenceRecordRow, ObligationRow, PaymentRow, TransferRow,
    FundingRow, SettlementRow, ApplicationRow, OpeningPayableRow, WorkAreaRow, AttributionRow, CaptureRow,
)
from greenhouse_owner_os.services.references import serialize


def json_value(v):
    if isinstance(v, Decimal): return f'{v:.2f}'
    if isinstance(v, UUID): return str(v)
    if isinstance(v, (datetime, date)): return v.isoformat()
    return v


def owner_view(factory, entity_id, *, offset=0, limit=50):
    with factory() as uow:
        s = uow.session
        s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        entity = s.get(EntityRow, entity_id)
        if not entity: raise NotFoundError('entity not found')
        def view(name):
            return [{k:json_value(v) for k,v in row.items()} for row in s.execute(
                text('SELECT * FROM finance.'+name+' WHERE entity_id=:e ORDER BY 1'),
                {'e':entity_id}).mappings()]
        references = [serialize(r) for r in s.scalars(select(ReferenceRecordRow).where(
            ReferenceRecordRow.entity_id==entity_id).order_by(ReferenceRecordRow.kind, ReferenceRecordRow.canonical_key))]
        openings = {str(r.obligation_id):dict(description=r.description,
            original_event_date=str(r.original_event_date), opening_date=str(r.opening_date),
            source=r.source, approval_reference=r.approval_reference)
            for r in s.scalars(select(OpeningPayableRow).where(OpeningPayableRow.entity_id==entity_id))}
        operations = []
        for kind, model in [('obligation',ObligationRow),('payment',PaymentRow),('transfer',TransferRow),
                            ('owner_funding',FundingRow),('direct_settlement',SettlementRow),('advance_application',ApplicationRow)]:
            # Fetch at most the requested page horizon per kind, not all journal history.
            rows = s.scalars(select(model).where(model.entity_id==entity_id).order_by(
                model.created_at.desc(),model.id.desc()).limit(offset+limit+1))
            for r in rows:
                data = {c.name:json_value(getattr(r,c.name)) for c in model.__table__.columns}
                operations.append(dict(data,kind=kind,opening=openings.get(str(r.id))))
        operations.sort(key=lambda r:(r['created_at'],r['id']),reverse=True)
        obligations = view('obligation_balances')
        basis={str(r.id):r for r in s.scalars(select(ObligationRow).where(ObligationRow.entity_id==entity_id))}
        areas=[{c.name:json_value(getattr(r,c.name)) for c in WorkAreaRow.__table__.columns} for r in s.scalars(select(WorkAreaRow).where(WorkAreaRow.entity_id==entity_id).order_by(WorkAreaRow.name))]
        today=datetime.now(__import__('zoneinfo').ZoneInfo('Europe/Istanbul')).date().isoformat()
        grouped={}
        for o in obligations:
            oid=o.get('obligation_id',o.get('id'));r=basis[oid]
            o.update(opening=openings.get(oid),obligation_class=r.obligation_class,economic_source=r.economic_source,
                description=r.description,event_date=str(r.event_date),due_date=str(r.due_date) if r.due_date else None,
                attributions=uow.finance.business_attributions(r.id))
            o['unknown_attribution_amount']=f"{r.original_amount-sum((Decimal(a['amount']) for a in o['attributions']),Decimal('0')):.2f}"
            o['overdue']=bool(o['settlement_status'] in ('open','partial') and o['due_date'] and o['due_date']<today)
            if o['settlement_status'] in ('open','partial'):
                b=grouped.setdefault(o['currency'],{k:Decimal('0') for k in ('operational_payable','strategic_payable','overdue_receivable','upcoming_receivable','undated_receivable')})
                if o['direction']=='payable':b['strategic_payable' if r.obligation_class=='STRATEGIC' else 'operational_payable']+=Decimal(o['remaining_amount'])
                else:b['overdue_receivable' if o['overdue'] else 'upcoming_receivable' if o['due_date'] else 'undated_receivable']+=Decimal(o['remaining_amount'])
        # Exception list spans the whole entity, independent of operation pagination.
        attention=[]
        for o in obligations:
            if o['overdue']:attention.append(dict(type='overdue_receivable',message='Просрочен платёж от клиента / депо',kind='obligation',id=o['obligation_id'],currency=o['currency']))
            if o['settlement_status'] in ('open','partial') and o['unknown_attribution_amount']!='0.00':attention.append(dict(type='unknown_attribution',message='Место экономического события не указано',kind='obligation',id=o['obligation_id'],currency=o['currency']))
        captures=[]
        for r in s.scalars(select(CaptureRow).where(CaptureRow.entity_id==entity_id,CaptureRow.state!='LINKED').order_by(CaptureRow.created_at.desc()).limit(100)):
            captures.append({c.name:json_value(getattr(r,c.name)) for c in CaptureRow.__table__.columns})
            attention.append(dict(type='capture_review',message='Проверьте сообщение и недостающие данные',kind='capture',id=str(r.id)))
        economics=[]
        # Original economic amounts, not debt remaining and never payment geography.
        for o in obligations:
            if basis[o['obligation_id']].status!='posted' or o['opening'] or o['obligation_class']=='STRATEGIC':continue
            for a in o['attributions']:economics.append(dict(obligation_id=o['obligation_id'],work_area_id=a['work_area_id'],amount=a['amount'],currency=o['currency'],direction=o['direction']))
        return dict(entity_id=str(entity_id), entity_name=entity.name, reference_class=entity.reference_class,
            cutover_date=str(entity.finance_cutover_date), references=references,
            as_of_date=today, work_areas=areas, business_balances={c:{k:f'{v:.2f}' for k,v in b.items()} for c,b in grouped.items()},
            attention=attention, captures=captures, economics=economics,
            balances={x:{k:f'{v:.2f}' for k,v in b.items()} for x,b in sorted(uow.finance.currency_balances(entity_id).items())},
            accounts=view('money_account_balances'), obligations=obligations, advances=view('advance_balances'),
            operations=operations[offset:offset+limit], offset=offset, limit=limit,
            has_more=len(operations)>offset+limit, history_policy='NON_POSTING')
