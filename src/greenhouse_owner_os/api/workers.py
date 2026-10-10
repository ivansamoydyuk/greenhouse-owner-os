"""Owner and worker APIs use separate identity providers."""
from uuid import UUID
from fastapi import Depends,Header,HTTPException
from greenhouse_owner_os.domain.workers import WorkerInput,TaskInput,AssignmentInput,ActionInput,ReportInput,RuleInput,OccurrenceInput,LinkInput,WorkerResponseInput,WorkerResponseResult
from greenhouse_owner_os.domain.commands import StrictModel
from greenhouse_owner_os.services.workers import WorkersService
class ActiveInput(StrictModel):active:bool

def mount_workers(app,finance,owner_provider,worker_provider=None):
    service=WorkersService(finance.uow_factory) if finance else None
    def unavailable():raise HTTPException(401,'Scoped worker session required')
    worker_provider=worker_provider or unavailable
    @app.get('/v1/entities/{entity_id}/tasks')
    def overview(entity_id:UUID,actor:UUID=Depends(owner_provider)):return service.overview(entity_id)
    @app.post('/v1/entities/{entity_id}/workers',status_code=201)
    def worker(entity_id:UUID,body:WorkerInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.worker(entity_id,actor,key,body)
    @app.post('/v1/entities/{entity_id}/workers/{worker_id}/active')
    def active(entity_id:UUID,worker_id:UUID,body:ActiveInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.active(entity_id,actor,key,worker_id,body.active)
    @app.post('/v1/entities/{entity_id}/tasks',status_code=201)
    def create(entity_id:UUID,body:TaskInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.create(entity_id,actor,key,body)
    @app.get('/v1/entities/{entity_id}/tasks/{task_id}')
    def get(entity_id:UUID,task_id:UUID,actor:UUID=Depends(owner_provider)):return service.get(entity_id,task_id)
    @app.post('/v1/entities/{entity_id}/tasks/{task_id}/assignments',status_code=201)
    def assign(entity_id:UUID,task_id:UUID,body:AssignmentInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.assign(entity_id,actor,key,task_id,body)
    @app.post('/v1/entities/{entity_id}/tasks/{task_id}/actions')
    def action(entity_id:UUID,task_id:UUID,body:ActionInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.action(entity_id,actor,key,task_id,body)
    @app.post('/v1/entities/{entity_id}/task-rules',status_code=201)
    def rule(entity_id:UUID,body:RuleInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.rule(entity_id,actor,key,body)
    @app.post('/v1/entities/{entity_id}/task-rules/{rule_id}/occurrences',status_code=201)
    def occurrence(entity_id:UUID,rule_id:UUID,body:OccurrenceInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.occurrence(entity_id,actor,key,rule_id,body.occurrence_date)
    @app.post('/v1/entities/{entity_id}/tasks/{task_id}/links',status_code=201)
    def link(entity_id:UUID,task_id:UUID,body:LinkInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(owner_provider)):return service.link(entity_id,actor,key,task_id,body)
    @app.get('/v1/worker/tasks')
    def worker_tasks(identity=Depends(worker_provider)):
        e,w,a=identity;return service.overview(e,w)
    @app.get('/v1/worker/tasks/{task_id}')
    def worker_get(task_id:UUID,identity=Depends(worker_provider)):
        e,w,a=identity;return service.get(e,task_id,w)
    @app.post('/v1/worker/tasks/{task_id}/actions')
    def worker_action(task_id:UUID,body:ActionInput,key:str=Header(alias='Idempotency-Key'),identity=Depends(worker_provider)):
        e,w,a=identity;return service.action(e,a,key,task_id,body,worker_id=w)
    @app.post('/v1/worker/tasks/{task_id}/reports',status_code=201)
    def worker_report(task_id:UUID,body:ReportInput,key:str=Header(alias='Idempotency-Key'),identity=Depends(worker_provider)):
        e,w,a=identity;return service.report(e,a,key,task_id,w,body)

    @app.post('/v1/worker/tasks/{task_id}/responses',response_model=WorkerResponseResult)
    def worker_response(task_id:UUID,body:WorkerResponseInput,key:str=Header(alias='Idempotency-Key'),identity=Depends(worker_provider)):
        e,w,a=identity;return service.respond(e,a,key,task_id,w,body)
