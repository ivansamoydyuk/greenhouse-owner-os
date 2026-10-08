from uuid import UUID
from fastapi import BackgroundTasks, Depends, Header, Query
from fastapi.responses import Response
from greenhouse_owner_os.domain.owner_confirmation import OwnerCapture, OwnerRevision, OwnerDecision, OwnerConfirm, QuickCounterparty
from greenhouse_owner_os.services.owner_confirmation import OwnerConfirmationService


def mount_owner_confirmation(app, finance_service, actor_provider):
    service = OwnerConfirmationService(finance_service.uow_factory) if finance_service else None
    prefix = '/v1/entities/{entity_id}/owner'

    @app.post(prefix+'/captures', status_code=201)
    def capture(entity_id: UUID, body: OwnerCapture, background: BackgroundTasks,
                actor: UUID = Depends(actor_provider), key: str = Header(alias='Idempotency-Key')):
        saved=service.capture(entity_id,actor,key,body)
        if saved['source_state'] in ('SERVER_SAVED','FAILED'):
            background.add_task(service.process_one,UUID(saved['id']))
        return saved

    @app.get(prefix+'/captures/{source_id}')
    def source(entity_id: UUID, source_id: UUID, actor: UUID = Depends(actor_provider)):
        return service.source(entity_id,actor,source_id)

    @app.get(prefix+'/captures/{source_id}/original')
    def original(entity_id: UUID, source_id: UUID, actor: UUID = Depends(actor_provider)):
        from greenhouse_owner_os.infrastructure.inbox_models import InboxRow
        with service.factory() as u:
            service._owner(u,entity_id,actor)
            service._source(u,entity_id,source_id)
            r=u.session.get(InboxRow,source_id)
            return Response(r.file_bytes if r.file_bytes is not None else r.raw_text,
                            media_type=r.mime_type or 'text/plain',
                            headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store','Content-Disposition':'attachment'})

    @app.get(prefix+'/confirmations')
    def queue(entity_id: UUID, offset: int = Query(0,ge=0,le=10000), actor: UUID = Depends(actor_provider)):
        return service.queue(entity_id,actor,offset)

    @app.get(prefix+'/confirmations/{proposal_id}/preview')
    def preview(entity_id: UUID, proposal_id: UUID, actor: UUID = Depends(actor_provider)):
        return service.preview(entity_id,actor,proposal_id)

    @app.post(prefix+'/confirmations/{proposal_id}/revise')
    def revise(entity_id: UUID, proposal_id: UUID, body: OwnerRevision,
               actor: UUID = Depends(actor_provider), key: str = Header(alias='Idempotency-Key')):
        return service.revise(entity_id,actor,key,proposal_id,body)

    @app.post(prefix+'/confirmations/{proposal_id}/defer')
    def defer(entity_id: UUID, proposal_id: UUID, body: OwnerDecision,
              actor: UUID = Depends(actor_provider), key: str = Header(alias='Idempotency-Key')):
        return service.decide(entity_id,actor,key,proposal_id,body,'DEFER')

    @app.post(prefix+'/confirmations/{proposal_id}/reject')
    def reject(entity_id: UUID, proposal_id: UUID, body: OwnerDecision,
               actor: UUID = Depends(actor_provider), key: str = Header(alias='Idempotency-Key')):
        return service.decide(entity_id,actor,key,proposal_id,body,'REJECT')

    @app.post(prefix+'/confirmations/{proposal_id}/confirm')
    def confirm(entity_id: UUID, proposal_id: UUID, body: OwnerConfirm,
                actor: UUID = Depends(actor_provider), key: str = Header(alias='Idempotency-Key')):
        return service.confirm(entity_id,actor,key,proposal_id,body)

    @app.get(prefix+'/home')
    def home(entity_id: UUID, actor: UUID = Depends(actor_provider)):
        return service.home(entity_id,actor)

    @app.post(prefix+'/counterparties')
    def counterparty(entity_id: UUID, body: QuickCounterparty,
                     actor: UUID = Depends(actor_provider), key: str = Header(alias='Idempotency-Key')):
        return service.counterparty(entity_id,actor,key,body)
