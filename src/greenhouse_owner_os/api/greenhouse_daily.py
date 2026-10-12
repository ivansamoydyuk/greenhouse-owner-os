"""Owner-authenticated read-only Greenhouse Daily Control projection."""
from datetime import date
from uuid import UUID
from fastapi import Depends
from greenhouse_owner_os.domain.greenhouse_daily import GreenhouseDaily
from greenhouse_owner_os.services.greenhouse_daily import GreenhouseDailyService


def mount_greenhouse_daily(app, finance, actor_provider):
    service = GreenhouseDailyService(finance.uow_factory) if finance else None

    @app.get('/v1/entities/{entity_id}/greenhouse-daily', response_model=GreenhouseDaily)
    def greenhouse_daily(entity_id: UUID, date_from: date, date_to: date, currency: str = 'TRY',
                         greenhouse_id: UUID | None = None, allocation: str = 'all', compare_previous: bool = True,
                         actor: UUID = Depends(actor_provider)):
        return service.read(entity_id, actor, date_from, date_to, currency, greenhouse_id, allocation, compare_previous)
