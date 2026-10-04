"""Immutable opening evidence linked to the ordinary payable subledger."""
from sqlalchemy import select
from greenhouse_owner_os.domain.errors import ValidationError
from greenhouse_owner_os.infrastructure.models import EntityRow, OpeningPayableRow, LedgerAccountRow


def serialize_opening(row):
    return dict(obligation_id=str(row.obligation_id), entity_id=str(row.entity_id),
        counterparty_id=str(row.counterparty_id), amount=f'{row.amount:.2f}', currency=row.currency,
        original_event_date=row.original_event_date.isoformat(), opening_date=row.opening_date.isoformat(),
        description=row.description, source=row.source, approval_reference=row.approval_reference,
        verification_state=row.verification_state, verified_by=str(row.verified_by),
        verified_at=row.verified_at.isoformat(), status_at_cutover='unpaid',
        recognition_policy='EXPLICIT_OPENING_PAYABLE_NOT_HISTORICAL_REPLAY')


class SqlOpeningRepositoryMixin:
    def assert_regular_offset(self, account_id):
        row = self.session.get(LedgerAccountRow, account_id)
        if row and (row.account_class == 'opening_equity' or row.system_code == 'OPENING_PAYABLE_OFFSET'):
            raise ValidationError('opening offset reserved for explicit owner-verified opening payable')

    def get_opening_payable(self, obligation_id):
        row = self.session.get(OpeningPayableRow, obligation_id)
        return serialize_opening(row) if row else None

    def get_opening_payables(self, entity_id):
        return [serialize_opening(r) for r in self.session.scalars(select(OpeningPayableRow).where(
            OpeningPayableRow.entity_id == entity_id).order_by(OpeningPayableRow.approval_reference))]

    def opening_scope_lock(self, entity_id):
        entity = self.session.scalar(select(EntityRow).where(EntityRow.id == entity_id).with_for_update())
        if not entity:
            raise ValidationError('opening entity missing')
        return entity

    def opening_by_approval(self, entity_id, approval_reference, source):
        return self.session.scalar(select(OpeningPayableRow).where(
            OpeningPayableRow.entity_id == entity_id,
            (OpeningPayableRow.approval_reference == approval_reference) | (OpeningPayableRow.source == source)))
