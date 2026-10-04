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
    FundingRow, SettlementRow, ApplicationRow, OpeningPayableRow,
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
        for o in obligations: o['opening'] = openings.get(o.get('obligation_id',o.get('id')))
        return dict(entity_id=str(entity_id), entity_name=entity.name, reference_class=entity.reference_class,
            cutover_date=str(entity.finance_cutover_date), references=references,
            balances={x:{k:f'{v:.2f}' for k,v in b.items()} for x,b in sorted(uow.finance.currency_balances(entity_id).items())},
            accounts=view('money_account_balances'), obligations=obligations, advances=view('advance_balances'),
            operations=operations[offset:offset+limit], offset=offset, limit=limit,
            has_more=len(operations)>offset+limit, history_policy='NON_POSTING')
