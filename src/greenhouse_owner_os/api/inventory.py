from uuid import UUID
from datetime import date
from fastapi import Depends,Header,Response
from pydantic import Field
from greenhouse_owner_os.domain.commands import StrictModel
from greenhouse_owner_os.domain.inventory import ProductInput,ConversionInput,LocationInput,InventoryInput,InventoryPost,InventoryReverse
from greenhouse_owner_os.services.inventory import InventoryService
from greenhouse_owner_os.domain.errors import NotFoundError
import base64

class ActiveInput(StrictModel):
    active:bool

def mount_inventory(app,finance,actor_provider):
    service=InventoryService(finance.uow_factory) if finance else None
    @app.get('/v1/entities/{entity_id}/inventory')
    def overview(entity_id:UUID,date_from:date|None=None,date_to:date|None=None,actor:UUID=Depends(actor_provider)):
        return service.overview(entity_id,date_from,date_to)
    @app.post('/v1/entities/{entity_id}/inventory/products',status_code=201)
    def product(entity_id:UUID,body:ProductInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.product(entity_id,actor,key,body)
    @app.post('/v1/entities/{entity_id}/inventory/products/{product_id}/active')
    def active(entity_id:UUID,product_id:UUID,body:ActiveInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.active(entity_id,actor,key,product_id,body.active)
    @app.post('/v1/entities/{entity_id}/inventory/products/{product_id}/conversions',status_code=201)
    def conversion(entity_id:UUID,product_id:UUID,body:ConversionInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.conversion(entity_id,actor,key,product_id,body)
    @app.post('/v1/entities/{entity_id}/inventory/locations',status_code=201)
    def location(entity_id:UUID,body:LocationInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.location(entity_id,actor,key,body)
    @app.post('/v1/entities/{entity_id}/inventory/events',status_code=201)
    def create(entity_id:UUID,body:InventoryInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.create(entity_id,actor,key,body)
    @app.get('/v1/entities/{entity_id}/inventory/events/{event_id}')
    def get(entity_id:UUID,event_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.get(entity_id,event_id)
    @app.get('/v1/entities/{entity_id}/inventory/events/{event_id}/original')
    def original(entity_id:UUID,event_id:UUID,actor:UUID=Depends(actor_provider)):
        r=service.get(entity_id,event_id)['evidence']
        if not r.get('file_base64'):raise NotFoundError('No original binary')
        return Response(base64.b64decode(r['file_base64']),media_type=r['mime_type'],headers={'Content-Disposition':'attachment; filename="original"','X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})
    @app.post('/v1/entities/{entity_id}/inventory/events/{event_id}/post')
    def post(entity_id:UUID,event_id:UUID,body:InventoryPost,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.post(entity_id,actor,key,event_id,body)
    @app.post('/v1/entities/{entity_id}/inventory/events/{event_id}/reverse')
    def reverse(entity_id:UUID,event_id:UUID,body:InventoryReverse,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.reverse(entity_id,actor,key,event_id,body)
