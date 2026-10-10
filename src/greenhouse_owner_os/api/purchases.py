from datetime import date
from uuid import UUID
from fastapi import Depends,Query
from greenhouse_owner_os.domain.purchases import PurchaseHistory,PurchaseReceipt,InventoryHistory,InventoryHistoryEvent
from greenhouse_owner_os.services.purchases import PurchaseHistoryService


def mount_purchases(app,finance,actor_provider):
    service=PurchaseHistoryService(finance.uow_factory) if finance else None

    @app.get('/v1/entities/{entity_id}/purchases',response_model=PurchaseHistory)
    def purchases(entity_id:UUID,date_from:date|None=None,date_to:date|None=None,supplier_id:UUID|None=None,product_id:UUID|None=None,
                  location_id:UUID|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),actor:UUID=Depends(actor_provider)):
        return service.read(entity_id,actor,purchases=True,date_from=date_from,date_to=date_to,supplier_id=supplier_id,product_id=product_id,location_id=location_id,offset=offset,limit=limit)

    @app.get('/v1/entities/{entity_id}/purchases/{receipt_id}',response_model=PurchaseReceipt)
    def purchase(entity_id:UUID,receipt_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.detail(entity_id,actor,receipt_id)

    @app.get('/v1/entities/{entity_id}/inventory/history',response_model=InventoryHistory)
    def history(entity_id:UUID,date_from:date|None=None,date_to:date|None=None,supplier_id:UUID|None=None,product_id:UUID|None=None,
                location_id:UUID|None=None,kind:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),actor:UUID=Depends(actor_provider)):
        return service.read(entity_id,actor,date_from=date_from,date_to=date_to,supplier_id=supplier_id,product_id=product_id,location_id=location_id,kind=kind,offset=offset,limit=limit)

    @app.get('/v1/entities/{entity_id}/inventory/history/{event_id}',response_model=InventoryHistoryEvent)
    def history_event(entity_id:UUID,event_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.event_detail(entity_id,actor,event_id)
