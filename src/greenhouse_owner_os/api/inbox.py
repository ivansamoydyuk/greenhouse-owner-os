from uuid import UUID
from fastapi import Depends, Header, Query, Response
from pydantic import ValidationError as InputError
from fastapi.exceptions import RequestValidationError
from greenhouse_owner_os.services.inbox import InboxService, InboxInput, InboxConfirm, InboxReject

def mount_inbox(app,finance,actor_provider):
    service=InboxService(finance.uow_factory) if finance else None
    @app.post('/v1/entities/{entity_id}/inbox',status_code=201)
    def ingest(entity_id:UUID,body:InboxInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.ingest(entity_id,actor,key,body)
    @app.get('/v1/entities/{entity_id}/inbox')
    def queue(entity_id:UUID,offset:int=Query(0,ge=0,le=10000),limit:int=Query(50,ge=1,le=100),actor:UUID=Depends(actor_provider)):
        return service.list(entity_id,offset,limit)
    @app.get('/v1/entities/{entity_id}/inbox/{item_id}')
    def get(entity_id:UUID,item_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.get(entity_id,item_id)
    @app.get('/v1/entities/{entity_id}/inbox/{item_id}/original')
    def original(entity_id:UUID,item_id:UUID,actor:UUID=Depends(actor_provider)):
        content,mime=service.original(entity_id,item_id)
        return Response(content,media_type=mime,headers={'Content-Disposition':'attachment; filename="original"','X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})
    @app.post('/v1/entities/{entity_id}/inbox/{item_id}/confirm')
    def confirm(entity_id:UUID,item_id:UUID,body:InboxConfirm,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        try:return service.confirm(entity_id,item_id,actor,key,body)
        except InputError as exc:raise RequestValidationError([dict(e,loc=('body','reviewed',*e['loc'])) for e in exc.errors()]) from exc
    @app.post('/v1/entities/{entity_id}/inbox/{item_id}/reject')
    def reject(entity_id:UUID,item_id:UUID,body:InboxReject,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.reject(entity_id,item_id,actor,key,body)
