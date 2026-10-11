from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from uuid import UUID
from fastapi import Depends,Header,HTTPException,Response
from greenhouse_owner_os.domain.fertilizer import PlanInput,RevisionInput,ReferenceInput,DispatchInput,FertilizerResponseInput,ActualInput,ActualConfirm,PlanPreview,PlanView,DispatchView,ReportView,ActualView,ActualPreview,WorkerPlanView,WorkerCalendarView,ReferenceView,ResolutionInput
from greenhouse_owner_os.services.fertilizer import FertilizerService

def mount_fertilizer(app,finance,owner_provider,worker_provider=None):
    svc=FertilizerService(finance.uow_factory) if finance else None
    def unavailable():raise HTTPException(401,'Scoped worker session required')
    worker_provider=worker_provider or unavailable;root='/v1/entities/{entity_id}/fertilizer'
    @app.get(root+'/references')
    def references(entity_id:UUID,event_date:date|None=None,a:UUID=Depends(owner_provider)):return svc.references(entity_id,a,event_date)
    @app.post(root+'/references',status_code=201,response_model=ReferenceView)
    def reference(entity_id:UUID,b:ReferenceInput,key:str=Header(alias='Idempotency-Key'),a:UUID=Depends(owner_provider)):return svc.reference(entity_id,a,key,b)
    @app.get(root)
    def calendar(entity_id:UUID,date_from:date,date_to:date,greenhouse_id:UUID|None=None,a:UUID=Depends(owner_provider)):return svc.calendar(entity_id,a,date_from,date_to,greenhouse_id)
    @app.post(root+'/plans/preview',response_model=PlanPreview)
    def preview(entity_id:UUID,b:PlanInput,a:UUID=Depends(owner_provider)):return svc.preview(entity_id,a,b)
    @app.post(root+'/plans',status_code=201,response_model=PlanView)
    def create(entity_id:UUID,b:PlanInput,key:str=Header(alias='Idempotency-Key'),a:UUID=Depends(owner_provider)):return svc.save(entity_id,a,key,b)
    @app.get(root+'/plans/{plan_id}',response_model=PlanView)
    def get(entity_id:UUID,plan_id:UUID,a:UUID=Depends(owner_provider)):return svc.get(entity_id,a,plan_id)
    @app.post(root+'/plans/{plan_id}/revisions',response_model=PlanView)
    def revise(entity_id:UUID,plan_id:UUID,b:RevisionInput,key:str=Header(alias='Idempotency-Key'),a:UUID=Depends(owner_provider)):return svc.save(entity_id,a,key,b.input,plan_id,b.expected_version)
    @app.post(root+'/plans/{plan_id}/dispatch',response_model=DispatchView)
    def dispatch(entity_id:UUID,plan_id:UUID,b:DispatchInput,key:str=Header(alias='Idempotency-Key'),a:UUID=Depends(owner_provider)):return svc.dispatch(entity_id,a,key,plan_id,b)
    @app.post(root+'/plans/{plan_id}/actual-preview',response_model=ActualPreview)
    def actual_preview(entity_id:UUID,plan_id:UUID,b:ActualInput,a:UUID=Depends(owner_provider)):return svc.actual_preview(entity_id,a,plan_id,b)
    @app.post(root+'/plans/{plan_id}/actual',response_model=ActualView)
    def actual(entity_id:UUID,plan_id:UUID,b:ActualConfirm,key:str=Header(alias='Idempotency-Key'),a:UUID=Depends(owner_provider)):return svc.actual(entity_id,a,key,plan_id,b)
    @app.post(root+'/plans/{plan_id}/resolutions')
    def resolution(entity_id:UUID,plan_id:UUID,b:ResolutionInput,key:str=Header(alias='Idempotency-Key'),a:UUID=Depends(owner_provider)):return svc.resolve(entity_id,a,key,plan_id,b)
    def photo_response(data):return Response(data[0],media_type=data[1],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    @app.get(root+'/plans/{plan_id}/revisions/{revision}/cards/{card_id}/components/{component_id}/photo')
    def photo(entity_id:UUID,plan_id:UUID,revision:int,card_id:UUID,component_id:UUID,a:UUID=Depends(owner_provider)):return photo_response(svc.photo(entity_id,a,plan_id,revision,card_id,component_id))
    @app.get('/v1/worker/fertilizer',response_model=WorkerCalendarView)
    def worker_list(date_from:date|None=None,date_to:date|None=None,identity=Depends(worker_provider)):
        e,w,a=identity;today=datetime.now(ZoneInfo('Europe/Istanbul')).date();return svc.calendar(e,a,date_from or today-timedelta(days=180),date_to or today+timedelta(days=180),w=w)
    @app.get('/v1/worker/fertilizer/{plan_id}',response_model=WorkerPlanView)
    def worker_detail(plan_id:UUID,identity=Depends(worker_provider)):
        e,w,a=identity;return svc.get(e,a,plan_id,w)
    @app.post('/v1/worker/fertilizer/{plan_id}/responses',response_model=ReportView)
    def response(plan_id:UUID,b:FertilizerResponseInput,key:str=Header(alias='Idempotency-Key'),identity=Depends(worker_provider)):
        e,w,a=identity;return svc.respond(e,a,key,plan_id,w,b)
    @app.get('/v1/worker/fertilizer/{plan_id}/revisions/{revision}/cards/{card_id}/components/{component_id}/photo')
    def worker_photo(plan_id:UUID,revision:int,card_id:UUID,component_id:UUID,identity=Depends(worker_provider)):
        e,w,a=identity;return photo_response(svc.photo(e,a,plan_id,revision,card_id,component_id,w))
