from uuid import UUID
from typing import Literal
from fastapi import Depends,Header,Query,Response,BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.domain.owner_capture import CaptureSource,ReviewAction,ReviseAction,ConfirmAction,TaskStateAction,NameOnlyParty
from greenhouse_owner_os.domain.commands import ReverseCommand

def mount_owner_capture(app,finance,actor_provider):
    service=OwnerCaptureService(finance.uow_factory) if finance else None
    root='/v1/entities/{entity_id}/owner'
    @app.get(root+'/home')
    def home(entity_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.home(entity_id,actor)
    @app.post(root+'/captures',status_code=201)
    def capture(entity_id:UUID,body:CaptureSource,background:BackgroundTasks,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        result=service.capture(entity_id,actor,key,body)
        background.add_task(service.process,UUID(result['id']))
        return result
    @app.get(root+'/captures/{item_id}')
    def source(entity_id:UUID,item_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.source(entity_id,actor,item_id)
    @app.get(root+'/captures')
    def sources(entity_id:UUID,offset:int=Query(0,ge=0,le=10000),actor:UUID=Depends(actor_provider)):
        return service.sources(entity_id,actor,offset)
    @app.get(root+'/captures/{item_id}/original')
    def original(entity_id:UUID,item_id:UUID,actor:UUID=Depends(actor_provider)):
        data,mime=service.original(entity_id,actor,item_id)
        return Response(data,media_type=mime,headers={'Content-Disposition':'attachment; filename="original"','X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})
    @app.post(root+'/captures/{item_id}/retry-preparation')
    def retry(entity_id:UUID,item_id:UUID,background:BackgroundTasks,actor:UUID=Depends(actor_provider)):
        result=service.source(entity_id,actor,item_id);background.add_task(service.process,item_id);return result
    @app.get(root+'/confirmations')
    def queue(entity_id:UUID,offset:int=Query(0,ge=0,le=10000),limit:int=Query(50,ge=1,le=100),actor:UUID=Depends(actor_provider)):
        return service.queue(entity_id,actor,offset,limit)
    @app.get(root+'/confirmations/{item_id}/preview')
    def preview(entity_id:UUID,item_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.preview(entity_id,actor,item_id)
    @app.post(root+'/confirmations/{item_id}/revise')
    def revise(entity_id:UUID,item_id:UUID,body:ReviseAction,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.review(entity_id,actor,item_id,key,body,'EDIT')
    @app.post(root+'/confirmations/{item_id}/defer')
    def defer(entity_id:UUID,item_id:UUID,body:ReviewAction,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.review(entity_id,actor,item_id,key,body,'DEFER')
    @app.post(root+'/confirmations/{item_id}/reject')
    def reject(entity_id:UUID,item_id:UUID,body:ReviewAction,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.review(entity_id,actor,item_id,key,body,'REJECT')
    @app.post(root+'/confirmations/{item_id}/reopen')
    def reopen(entity_id:UUID,item_id:UUID,body:ReviewAction,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.review(entity_id,actor,item_id,key,body,'REOPEN')
    @app.post(root+'/confirmations/{item_id}/confirm')
    def confirm(entity_id:UUID,item_id:UUID,body:ConfirmAction,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        result=service.confirm(entity_id,actor,item_id,key,body)
        return JSONResponse(jsonable_encoder(result),status_code=409) if result.get('stale') else result
    @app.post(root+'/tasks/{item_id}/state')
    def task_state(entity_id:UUID,item_id:UUID,body:TaskStateAction,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.task_state(entity_id,actor,item_id,key,body)
    @app.get(root+'/tasks')
    def tasks(entity_id:UUID,state:Literal['TO_DO','DONE']='TO_DO',offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),actor:UUID=Depends(actor_provider)):
        return service.tasks(entity_id,actor,state,offset,limit)
    @app.get(root+'/tasks/{item_id}')
    def task(entity_id:UUID,item_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.task(entity_id,actor,item_id)
    @app.post(root+'/counterparties',status_code=201)
    def party(entity_id:UUID,body:NameOnlyParty,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.counterparty(entity_id,actor,key,body)
    @app.post(root+'/reminder-eligibility')
    def reminders(entity_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.reminders(entity_id,actor)
    @app.post(root+'/direct-expenses/{item_id}/reverse')
    def reverse(entity_id:UUID,item_id:UUID,body:ReverseCommand,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.reverse_expense(entity_id,actor,item_id,key,body)
