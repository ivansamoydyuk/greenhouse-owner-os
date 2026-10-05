from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))


UTC_NOW = text("now()")


class EntityRow(Base):
    __tablename__ = "entities"
    __table_args__ = (CheckConstraint("finance_opening_policy IN ('ZERO','EXPLICIT_AP')", name="ck_entity_opening_policy"), {"schema": "core"})

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    finance_cutover_date: Mapped[date] = mapped_column(Date, nullable=False, server_default=text("DATE '2026-10-04'"))
    finance_opening_policy: Mapped[str] = mapped_column(String(20), nullable=False, server_default='ZERO')
    reference_class: Mapped[str] = mapped_column(String(20), nullable=False, server_default='OWNER_VERIFIED')
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="TRY")
    active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class ActorRow(Base):
    __tablename__ = "actors"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class CounterpartyRow(Base):
    __tablename__ = "counterparties"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    counterparty_type: Mapped[str] = mapped_column(String(30), nullable=False, default="company")
    active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class LedgerAccountRow(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("entity_id", "currency", "system_code", name="uq_ledger_account_system_code"),
        CheckConstraint("normal_side in ('debit','credit')", name="ck_ledger_account_normal_side"),
        {"schema": "finance"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_class: Mapped[str] = mapped_column(String(40), nullable=False)
    normal_side: Mapped[str] = mapped_column(String(6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    system_code: Mapped[str | None] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))


class MoneyAccountRow(Base):
    __tablename__ = "money_accounts"
    __table_args__ = (
        UniqueConstraint("ledger_account_id", name="uq_money_account_ledger"),
        CheckConstraint("account_type in ('cash','bank')", name="ck_money_account_type"),
        {"schema": "finance"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    ledger_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.ledger_accounts.id"), nullable=False)
    account_type: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))


class PostingRow(Base):
    __tablename__ = "postings"
    __table_args__ = (
        Index("uq_posting_reversal_once", "reversal_of_id", unique=True, postgresql_where=text("reversal_of_id is not null")),
        {"schema": "finance"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    posting_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.postings.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class PostingEntryRow(Base):
    __tablename__ = "posting_entries"
    __table_args__ = (
        CheckConstraint("side in ('debit','credit')", name="ck_posting_entry_side"),
        CheckConstraint("amount > 0", name="ck_posting_entry_amount_positive"),
        {"schema": "finance"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.postings.id", ondelete="RESTRICT"), nullable=False)
    ledger_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.ledger_accounts.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    memo: Mapped[str | None] = mapped_column(Text)


class WorkAreaRow(Base):
    __tablename__ = "work_areas"
    __table_args__ = {"schema": "core"}
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.work_areas.id"))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class AttributionRow(Base):
    __tablename__ = "obligation_attributions"
    __table_args__ = {"schema": "finance"}
    obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.obligations.id"), primary_key=True)
    work_area_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.work_areas.id"), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20,2), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"), nullable=False)


class CaptureRow(Base):
    harvest_kind: Mapped[str | None] = mapped_column(String(30))
    harvest_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    __tablename__ = "capture_drafts"
    __table_args__ = {"schema": "core"}
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    route: Mapped[str] = mapped_column(String(30), nullable=False)
    extracted: Mapped[dict] = mapped_column(JSONB, nullable=False)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    finance_kind: Mapped[str | None] = mapped_column(String(30))
    finance_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class ObligationRow(Base):
    obligation_class: Mapped[str] = mapped_column(String(20), nullable=False, default="OPERATIONAL", server_default="OPERATIONAL")
    economic_source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual_other", server_default="manual_other")
    description: Mapped[str | None] = mapped_column(Text)
    __tablename__ = "obligations"
    __table_args__ = (
        CheckConstraint("direction in ('payable','receivable')", name="ck_obligation_direction"),
        CheckConstraint("status in ('draft','review','posted','reversed')", name="ck_obligation_status"),
        CheckConstraint("original_amount > 0", name="ck_obligation_amount_positive"),
        Index("ix_obligation_counterparty_status", "counterparty_id", "status"),
        {"schema": "finance"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    counterparty_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.counterparties.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    offset_ledger_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.ledger_accounts.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft", server_default="draft")
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.postings.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"))
    reversal_reason: Mapped[str | None] = mapped_column(Text)
    reversal_posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.postings.id"), unique=True)
    replacement_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("finance." + __tablename__ + ".id"), unique=True)


class PaymentRow(Base):
    source: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("direction in ('in','out')", name="ck_payment_direction"),
        CheckConstraint("status in ('draft','review','posted','reversed')", name="ck_payment_status"),
        CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        Index("ix_payment_counterparty_status", "counterparty_id", "status"),
        {"schema": "finance"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    money_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.money_accounts.id"), nullable=False)
    counterparty_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.counterparties.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft", server_default="draft")
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.postings.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"))
    reversal_reason: Mapped[str | None] = mapped_column(Text)
    reversal_posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.postings.id"), unique=True)
    replacement_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("finance." + __tablename__ + ".id"), unique=True)


class PaymentAllocationRow(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_allocation_amount_positive"),
        UniqueConstraint("payment_id", "obligation_id", name="uq_payment_allocation_pair"),
        Index("ix_payment_allocation_obligation", "obligation_id"),
        {"schema": "finance"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.payments.id", ondelete="CASCADE"), nullable=False)
    obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.obligations.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class AdvanceRow(Base):
    __tablename__ = "advances"
    __table_args__ = (
        CheckConstraint("direction in ('paid','received')", name="ck_advance_direction"),
        CheckConstraint("status in ('open','closed','reversed')", name="ck_advance_status"),
        CheckConstraint("original_amount > 0", name="ck_advance_amount_positive"),
        UniqueConstraint("source_payment_id", name="uq_advance_source_payment"),
        {"schema": "finance"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.entities.id"), nullable=False)
    counterparty_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.counterparties.id"), nullable=False)
    source_payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finance.payments.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "idempotency_key", name="uq_idempotency_actor_operation_key"),
        {"schema": "audit"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class AuditEventRow(Base):
    __tablename__ = "events"
    __table_args__ = {"schema": "audit"}

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.actors.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)
    reason: Mapped[str | None] = mapped_column(Text)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class CashLifecycleMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.entities.id'), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default='draft')
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.actors.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW, nullable=False)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.postings.id'), unique=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.postings.id'), unique=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('core.actors.id'))
    reversal_reason: Mapped[str | None] = mapped_column(Text)
    # Reserved lifecycle field lets these records use the common integrity guard.
    replacement_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class TransferRow(CashLifecycleMixin, Base):
    __tablename__ = 'own_account_transfers'
    __table_args__ = {'schema': 'finance'}
    source_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.money_accounts.id'), nullable=False)
    destination_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.money_accounts.id'), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal('0.00'))
    fee_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.ledger_accounts.id'))


class FundingRow(CashLifecycleMixin, Base):
    __tablename__ = 'owner_fundings'
    __table_args__ = {'schema': 'finance'}
    money_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.money_accounts.id'), nullable=False)


class ApplicationRow(CashLifecycleMixin, Base):
    __tablename__ = 'advance_applications'
    __table_args__ = {'schema': 'finance'}
    advance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.advances.id'), nullable=False)
    obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.obligations.id'), nullable=False)
    counterparty_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.counterparties.id'), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)


class SettlementRow(CashLifecycleMixin, Base):
    __tablename__ = 'direct_settlements'
    __table_args__ = {'schema': 'finance'}
    receivable_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.obligations.id'), nullable=False)
    payable_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.obligations.id'), nullable=False)


class ImportBatchRow(Base):
    __tablename__ = 'import_batches'
    __table_args__ = (
        UniqueConstraint('entity_id', 'source', 'fingerprint', name='uq_import_batch_source'),
        CheckConstraint("fingerprint ~ '^[a-f0-9]{64}$'", name='ck_import_batch_fingerprint'),
        CheckConstraint("btrim(source) <> '' AND btrim(source_file) <> ''", name='ck_import_batch_source'),
        {'schema': 'core'},
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.entities.id'), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.actors.id'), nullable=False)


class HistoricalCandidateRow(Base):
    __tablename__ = 'historical_candidates'
    __table_args__ = (
        UniqueConstraint('import_batch_id', 'source_key', name='uq_historical_candidate_source'),
        CheckConstraint("verification_state IN ('VERIFIED','VERIFY','HISTORY')", name='ck_candidate_verification'),
        CheckConstraint("source_key ~ '^[a-f0-9]{64}$' AND content_hash ~ '^[a-f0-9]{64}$'", name='ck_candidate_hashes'),
        CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 1)', name='ck_candidate_confidence'),
        CheckConstraint("(source_row IS NOT NULL AND btrim(source_row) <> '') OR (source_document IS NOT NULL AND btrim(source_document) <> '')", name='ck_candidate_locator'),
        CheckConstraint("raw_value IS NOT NULL OR (source_reference IS NOT NULL AND btrim(source_reference) <> '')", name='ck_candidate_evidence'),
        {'schema': 'core'},
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    import_batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.import_batches.id'), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_row: Mapped[str | None] = mapped_column(Text)
    source_document: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[date | None] = mapped_column(Date)
    verification_state: Mapped[str] = mapped_column(String(10), nullable=False, server_default='HISTORY')
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(7,6))
    raw_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    source_reference: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.actors.id'), nullable=False)


class ReferenceRecordRow(Base):
    __tablename__ = 'reference_records'
    __table_args__ = (UniqueConstraint('entity_id', 'kind', 'canonical_key', name='uq_reference_identity'), {'schema': 'core'})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.entities.id'), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    canonical_key: Mapped[str] = mapped_column(Text, nullable=False)
    reference_class: Mapped[str] = mapped_column(String(20), nullable=False)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.actors.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)


class OpeningPayableRow(Base):
    __tablename__ = 'opening_payables'
    __table_args__ = (UniqueConstraint('entity_id', 'approval_reference', name='uq_opening_payable_approval'),
                     UniqueConstraint('entity_id', 'source', name='uq_opening_payable_source'), {'schema': 'finance'})
    obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('finance.obligations.id'), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.entities.id'), nullable=False)
    counterparty_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.counterparties.id'), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    original_event_date: Mapped[date] = mapped_column(Date, nullable=False)
    opening_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    approval_reference: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_state: Mapped[str] = mapped_column(String(20), nullable=False, server_default='OWNER_VERIFIED')
    verified_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('core.actors.id'), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTC_NOW)
