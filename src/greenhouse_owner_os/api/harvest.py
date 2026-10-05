from uuid import UUID
from datetime import date
from fastapi import Depends,Header,Response
from greenhouse_owner_os.domain.harvest import ReportInput,BatchInput,SettlementInput,SettlementPost
from greenhouse_owner_os.domain.commands import ReverseCommand
from greenhouse_owner_os.domain.errors import NotFoundError
from greenhouse_owner_os.infrastructure.harvest_models import EvidenceRow
from greenhouse_owner_os.services.harvest import HarvestService

def mount_harvest(app,finance,actor_provider):
    service=HarvestService(finance.uow_factory) if finance else None
    @app.get('/v1/entities/{entity_id}/harvest')
    def overview(entity_id:UUID,date_from:date|None=None,date_to:date|None=None,actor:UUID=Depends(actor_provider)):
        return service.overview(entity_id,date_from,date_to)
    @app.post('/v1/entities/{entity_id}/harvest/reports',status_code=201)
    def report(entity_id:UUID,body:ReportInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.report(entity_id,actor,key,body)
    @app.post('/v1/entities/{entity_id}/harvest/batches',status_code=201)
    def batch(entity_id:UUID,body:BatchInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.batch(entity_id,actor,key,body)
    @app.post('/v1/entities/{entity_id}/harvest/settlements',status_code=201)
    def settlement(entity_id:UUID,body:SettlementInput,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.settlement(entity_id,actor,key,body)
    @app.get('/v1/entities/{entity_id}/harvest/settlements/{settlement_id}')
    def get(entity_id:UUID,settlement_id:UUID,actor:UUID=Depends(actor_provider)):
        return service.get(entity_id,settlement_id)
    @app.post('/v1/entities/{entity_id}/harvest/settlements/{settlement_id}/post')
    def post(entity_id:UUID,settlement_id:UUID,body:SettlementPost,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.post(entity_id,actor,key,settlement_id,body)
    @app.post('/v1/entities/{entity_id}/harvest/settlements/{settlement_id}/reverse')
    def reverse(entity_id:UUID,settlement_id:UUID,body:ReverseCommand,key:str=Header(alias='Idempotency-Key'),actor:UUID=Depends(actor_provider)):
        return service.reverse(entity_id,actor,key,settlement_id,body)
    @app.get('/v1/entities/{entity_id}/harvest/evidence/{evidence_id}')
    def evidence(entity_id:UUID,evidence_id:UUID,actor:UUID=Depends(actor_provider)):
        with service.factory() as u:
            r=u.session.get(EvidenceRow,evidence_id)
            if not r or r.entity_id!=entity_id or r.file_bytes is None:raise NotFoundError('original file not found')
            return Response(r.file_bytes,media_type=r.mime_type,headers={'Content-Disposition':'attachment; filename="receipt"','X-Content-Type-Options':'nosniff'})
