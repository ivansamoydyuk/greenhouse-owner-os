from uuid import UUID
from datetime import date,datetime
from fastapi import Depends,Header,Response
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from greenhouse_owner_os.domain.harvest import ReportInput,BatchInput,SettlementInput,SettlementPost
from greenhouse_owner_os.domain.commands import ReverseCommand
from greenhouse_owner_os.domain.errors import NotFoundError
from greenhouse_owner_os.infrastructure.harvest_models import EvidenceRow
from greenhouse_owner_os.services.harvest import HarvestService

class HarvestEvidenceMetadata(BaseModel):
    # OpenAPI documentation only (responses.model, not response_model): retain
    # the read adapter's original SQL projection and serialization unchanged.
    model_config = ConfigDict(extra='forbid')
    id: UUID
    entity_id: UUID
    source: str
    source_reference: str | None
    raw_text: str | None = Field(description='Exact immutable original text or source-associated caption; never synthesized from prepared report fields.')
    filename: str | None
    mime_type: str | None
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_by: UUID
    received_at: datetime
    has_file: bool
    original_url: str | None = Field(json_schema_extra={'format': 'uri-reference'},
        description='Same-origin existing /v1/entities/{entity_id}/harvest/evidence/{evidence_id} binary route when has_file is true; otherwise null. Never a source-provided URL.')


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
    @app.get('/v1/entities/{entity_id}/harvest/evidence/{evidence_id}/metadata',
        summary='Read immutable Harvest original metadata',
        description='Authenticated, entity-scoped read of original source text and provenance. Does not infer source text from prepared fields or return file bytes. The existing binary evidence endpoint remains unchanged.',
        responses={
            200: {'model': HarvestEvidenceMetadata,
                'description': 'Immutable original metadata, including exact nullable raw text and binary availability.',
                'headers': {'Cache-Control': {'schema': {'type': 'string', 'const': 'private, no-store'}}}},
            401: {'description': 'Authenticated owner required.'},
            403: {'description': 'The authenticated owner cannot access the requested managed entity.'},
            404: {'description': 'Evidence is missing or does not belong to the requested entity.'},
        })
    def evidence_metadata(entity_id:UUID,evidence_id:UUID,response:Response,actor:UUID=Depends(actor_provider)):
        # Original provenance only. Do not load/serialize bytes or infer text
        # from prepared report fields; the existing binary endpoint is unchanged.
        with service.factory() as u:
            columns=[column for column in EvidenceRow.__table__.columns if column.name!='file_bytes']
            row=u.session.execute(select(*columns,EvidenceRow.file_bytes.is_not(None).label('has_file')).where(
                EvidenceRow.id==evidence_id,EvidenceRow.entity_id==entity_id)).mappings().one_or_none()
            if row is None:raise NotFoundError('original evidence not found')
            result=dict(row)
            result['original_url']=f'/v1/entities/{entity_id}/harvest/evidence/{evidence_id}' if result['has_file'] else None
            response.headers['Cache-Control']='private, no-store'
            return result
