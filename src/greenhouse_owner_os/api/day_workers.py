"""Owner and worker DayWorkers surfaces retain separate trusted identities."""
from uuid import UUID
from fastapi import Depends, Header, HTTPException
from greenhouse_owner_os.domain.day_workers import RequestInput,DecisionInput,ClarificationInput,ActualInput,ConfirmationInput,AccrualInput,ScopeInput
from greenhouse_owner_os.services.day_workers import DayWorkersService


def mount_day_workers(app,finance,owner_provider,worker_provider=None):
    service=DayWorkersService(finance.uow_factory) if finance else None
    def unavailable():raise HTTPException(401,'Scoped worker session required')
    worker_provider=worker_provider or unavailable

    @app.get('/v1/entities/{entity_id}/day-workers')
    def overview(entity_id:UUID,actor:UUID=Depends(owner_provider)):return service.overview(entity_id,actor)
    @app.post('/v1/entities/{entity_id}/day-workers/scopes',status_code=201)
    def scope(entity_id:UUID,body:ScopeInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.scope(entity_id,actor,key,body)
    @app.post('/v1/entities/{entity_id}/day-workers/requests',status_code=201)
    def request(entity_id:UUID,body:RequestInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.request(entity_id,actor,key,body)
    @app.get('/v1/entities/{entity_id}/day-workers/requests/{request_id}')
    def get_request(entity_id:UUID,request_id:UUID,actor:UUID=Depends(owner_provider)):return service.get_request(entity_id,actor,request_id)
    @app.post('/v1/entities/{entity_id}/day-workers/requests/{request_id}/decisions')
    def decision(entity_id:UUID,request_id:UUID,body:DecisionInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.decision(entity_id,actor,key,request_id,body)
    @app.post('/v1/entities/{entity_id}/day-workers/actuals',status_code=201)
    def actual(entity_id:UUID,body:ActualInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.actual(entity_id,actor,key,body)
    @app.get('/v1/entities/{entity_id}/day-workers/actuals/{actual_id}')
    def get_actual(entity_id:UUID,actual_id:UUID,actor:UUID=Depends(owner_provider)):return service.get_actual(entity_id,actor,actual_id)
    @app.post('/v1/entities/{entity_id}/day-workers/actuals/{actual_id}/confirmations')
    def confirmation(entity_id:UUID,actual_id:UUID,body:ConfirmationInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.confirmation(entity_id,actor,key,actual_id,body)
    @app.post('/v1/entities/{entity_id}/day-workers/actuals/{actual_id}/accruals')
    def accrual(entity_id:UUID,actual_id:UUID,body:AccrualInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.accrual(entity_id,actor,key,actual_id,body)

    @app.get('/v1/worker/day-workers')
    def worker_overview(identity=Depends(worker_provider)):
        e,w,a=identity;return service.overview(e,a,w)
    @app.post('/v1/worker/day-workers/requests',status_code=201)
    def worker_request(body:RequestInput,key:str=Header(alias='Idempotency-Key'),identity=Depends(worker_provider)):
        e,w,a=identity;return service.request(e,a,key,body,w)
    @app.get('/v1/worker/day-workers/requests/{request_id}')
    def worker_get_request(request_id:UUID,identity=Depends(worker_provider)):
        e,w,a=identity;return service.get_request(e,a,request_id,w)
    @app.post('/v1/worker/day-workers/requests/{request_id}/clarifications')
    def worker_clarification(request_id:UUID,body:ClarificationInput,key:str=Header(alias='Idempotency-Key'),identity=Depends(worker_provider)):
        e,w,a=identity;return service.clarification(e,a,key,request_id,body,w)
    @app.post('/v1/worker/day-workers/actuals',status_code=201)
    def worker_actual(body:ActualInput,key:str=Header(alias='Idempotency-Key'),identity=Depends(worker_provider)):
        e,w,a=identity;return service.actual(e,a,key,body,w)
    @app.get('/v1/worker/day-workers/actuals/{actual_id}')
    def worker_get_actual(actual_id:UUID,identity=Depends(worker_provider)):
        e,w,a=identity;return service.get_actual(e,a,actual_id,w)
