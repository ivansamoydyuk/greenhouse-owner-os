"""Authenticated, entity-scoped, typed read-only Finance drilldowns."""
from datetime import date
from uuid import UUID
from fastapi import Depends
from greenhouse_owner_os.domain.finance_analytics import FinanceAnalytics, DebtDocument
from greenhouse_owner_os.services.finance_analytics import FinanceAnalyticsService


def mount_finance_analytics(app, finance, actor_provider):
    service = FinanceAnalyticsService(finance.uow_factory) if finance else None

    @app.get('/v1/entities/{entity_id}/finance/analytics', response_model=FinanceAnalytics)
    def analytics(entity_id: UUID, date_from: date, date_to: date, currency: str = 'TRY',
                  greenhouse_id: UUID | None = None, money_account_id: UUID | None = None,
                  actor: UUID = Depends(actor_provider)):
        return service.read(entity_id, actor, date_from, date_to, currency, greenhouse_id, money_account_id)

    @app.get('/v1/entities/{entity_id}/finance/analytics/documents/{obligation_id}', response_model=DebtDocument)
    def document(entity_id: UUID, obligation_id: UUID, actor: UUID = Depends(actor_provider)):
        return service.document(entity_id, actor, obligation_id)
