"""Non-economic references and explicit, audited opening annotation only."""
from uuid import UUID, uuid5, NAMESPACE_URL
from sqlalchemy import select
from greenhouse_owner_os.infrastructure.models import WorkAreaRow, AttributionRow, OpeningPayableRow
from greenhouse_owner_os.domain.errors import ValidationError

def provision_owner_dimensions(factory, entity_id, actor_id, opening_id):
    # Exact owner-authorized locations; no amounts guessed or synthetic transactions.
    with factory() as uow:
        ids={}
        for name in ('İmamlı','Özvadi','Yeşilöz'):
            rid=uuid5(NAMESPACE_URL,f'greenhouse-owner-os/work-area/{entity_id}/{name}')
            ids[name]=rid
            if not uow.session.get(WorkAreaRow,rid):
                uow.session.add(WorkAreaRow(id=rid,entity_id=entity_id,name=name,kind='greenhouse',source='owner:workflow-alignment:2026-10-05'))
        uow.session.flush()
        if opening_id:
            opening=uow.session.get(OpeningPayableRow,UUID(str(opening_id)))
            if not opening or opening.entity_id!=entity_id or opening.description!='ремонт трубы для İmamlı':
                raise ValidationError('opening annotation requires exact verified owner basis')
            key=(opening.obligation_id,ids['İmamlı'])
            if not uow.session.get(AttributionRow,key):
                uow.session.add(AttributionRow(obligation_id=opening.obligation_id,work_area_id=ids['İmamlı'],amount=opening.amount,
                    source='owner:opening-attribution:2026-10-05:serkan-imamli-pipe',created_by=actor_id))
                uow.audit.append(actor_id=actor_id,action='attribution_approve',entity_type='obligation',entity_id=opening.obligation_id,
                    before=None,after={'work_area_id':str(ids['İmamlı']),'amount':f'{opening.amount:.2f}','source':'owner:workflow-alignment:2026-10-05'})
        uow.commit()
