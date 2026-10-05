"""Harvest evidence and immutable source ownership, separate from Finance ledger."""
from datetime import date,datetime
from decimal import Decimal
from uuid import UUID as U
from sqlalchemy import ForeignKey,Text,String,Integer,Numeric,Date,DateTime,LargeBinary,Boolean
from sqlalchemy.dialects.postgresql import UUID,JSONB
from sqlalchemy.orm import Mapped,mapped_column as col
from .models import Base,uuid_pk,UTC_NOW

class EvidenceRow(Base):
    __tablename__='evidence';__table_args__={'schema':'harvest'}
    id:Mapped[U]=uuid_pk()
    entity_id:Mapped[U]=col(UUID,ForeignKey('core.entities.id'))
    source:Mapped[str]=col(Text)
    source_reference:Mapped[str|None]=col(Text)
    raw_text:Mapped[str|None]=col(Text)
    filename:Mapped[str|None]=col(Text)
    mime_type:Mapped[str|None]=col(Text)
    file_bytes:Mapped[bytes|None]=col(LargeBinary)
    fingerprint:Mapped[str]=col(String(64))
    created_by:Mapped[U]=col(UUID,ForeignKey('core.actors.id'))
    received_at:Mapped[datetime]=col(DateTime(timezone=True),server_default=UTC_NOW)

class ReportRow(Base):
    __tablename__='worker_reports';__table_args__={'schema':'harvest'}
    id:Mapped[U]=uuid_pk()
    entity_id:Mapped[U]=col(UUID,ForeignKey('core.entities.id'))
    event_date:Mapped[date]=col(Date)
    original_greenhouse:Mapped[str]=col(Text)
    greenhouse_id:Mapped[U|None]=col(UUID,ForeignKey('core.work_areas.id'))
    work_area_id:Mapped[U|None]=col(UUID,ForeignKey('core.work_areas.id'))
    cut_count:Mapped[int|None]=col(Integer)
    field_weight_kg:Mapped[Decimal|None]=col(Numeric(20,3))
    reporter:Mapped[str]=col(Text)
    notes:Mapped[str|None]=col(Text)
    evidence_id:Mapped[U]=col(UUID,ForeignKey('harvest.evidence.id'))
    created_by:Mapped[U]=col(UUID,ForeignKey('core.actors.id'))
    created_at:Mapped[datetime]=col(DateTime(timezone=True),server_default=UTC_NOW)

class BatchRow(Base):
    __tablename__='batches';__table_args__={'schema':'harvest'}
    id:Mapped[U]=uuid_pk()
    entity_id:Mapped[U]=col(UUID,ForeignKey('core.entities.id'))
    event_date:Mapped[date]=col(Date)
    label:Mapped[str]=col(Text)
    original_greenhouse:Mapped[str|None]=col(Text)
    greenhouse_id:Mapped[U|None]=col(UUID,ForeignKey('core.work_areas.id'))
    link_evidence:Mapped[str]=col(Text)
    created_by:Mapped[U]=col(UUID,ForeignKey('core.actors.id'))
    created_at:Mapped[datetime]=col(DateTime(timezone=True),server_default=UTC_NOW)

class BatchReportRow(Base):
    __tablename__='batch_reports';__table_args__={'schema':'harvest'}
    batch_id:Mapped[U]=col(UUID,ForeignKey('harvest.batches.id'),primary_key=True)
    report_id:Mapped[U]=col(UUID,ForeignKey('harvest.worker_reports.id'),primary_key=True)

class SettlementRow(Base):
    __tablename__='depot_settlements';__table_args__={'schema':'harvest'}
    id:Mapped[U]=uuid_pk()
    entity_id:Mapped[U]=col(UUID,ForeignKey('core.entities.id'))
    depot_id:Mapped[U]=col(UUID,ForeignKey('core.counterparties.id'))
    event_date:Mapped[date]=col(Date)
    due_date:Mapped[date|None]=col(Date)
    expected_payment:Mapped[bool]=col(Boolean)
    currency:Mapped[str]=col(String(3))
    original_greenhouse:Mapped[str|None]=col(Text)
    greenhouse_id:Mapped[U|None]=col(UUID,ForeignKey('core.work_areas.id'))
    document_number:Mapped[str|None]=col(Text)
    mixed:Mapped[bool]=col(Boolean)
    stated_total:Mapped[Decimal|None]=col(Numeric(20,2))
    alacak_value:Mapped[Decimal|None]=col(Numeric(20,2))
    total_amount:Mapped[Decimal]=col(Numeric(20,2))
    evidence_id:Mapped[U]=col(UUID,ForeignKey('harvest.evidence.id'))
    link_evidence:Mapped[str|None]=col(Text)
    status:Mapped[str]=col(String(10),default='draft')
    version:Mapped[int]=col(Integer,default=1)
    obligation_id:Mapped[U|None]=col(UUID,ForeignKey('finance.obligations.id',deferrable=True,initially='DEFERRED'))
    payment_id:Mapped[U|None]=col(UUID,ForeignKey('finance.payments.id'))
    application_ids:Mapped[list]=col(JSONB,default=list)
    reviewed_issues:Mapped[list]=col(JSONB,default=list)
    review_reason:Mapped[str|None]=col(Text)
    posted_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    reversed_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    reversal_reason:Mapped[str|None]=col(Text)
    replacement_of_id:Mapped[U|None]=col(UUID,ForeignKey('harvest.depot_settlements.id'))
    created_by:Mapped[U]=col(UUID,ForeignKey('core.actors.id'))
    created_at:Mapped[datetime]=col(DateTime(timezone=True),server_default=UTC_NOW)

class SettlementBatchRow(Base):
    __tablename__='settlement_batches';__table_args__={'schema':'harvest'}
    settlement_id:Mapped[U]=col(UUID,ForeignKey('harvest.depot_settlements.id'),primary_key=True)
    batch_id:Mapped[U]=col(UUID,ForeignKey('harvest.batches.id'),primary_key=True)

class LineRow(Base):
    __tablename__='settlement_lines';__table_args__={'schema':'harvest'}
    id:Mapped[U]=uuid_pk()
    settlement_id:Mapped[U]=col(UUID,ForeignKey('harvest.depot_settlements.id'))
    original_quality_name:Mapped[str]=col(Text)
    normalized_quality:Mapped[str]=col(String(10))
    net_weight_kg:Mapped[Decimal]=col(Numeric(20,3))
    unit_price:Mapped[Decimal]=col(Numeric(20,2))
    calculated_amount:Mapped[Decimal]=col(Numeric(20,2))
    stated_amount:Mapped[Decimal|None]=col(Numeric(20,2))
    amount:Mapped[Decimal]=col(Numeric(20,2))
    count:Mapped[int|None]=col(Integer)
    source_line:Mapped[str]=col(Text)

class AllocationRow(Base):
    __tablename__='line_allocations';__table_args__={'schema':'harvest'}
    line_id:Mapped[U]=col(UUID,ForeignKey('harvest.settlement_lines.id'),primary_key=True)
    work_area_id:Mapped[U]=col(UUID,ForeignKey('core.work_areas.id'),primary_key=True)
    net_weight_kg:Mapped[Decimal]=col(Numeric(20,3))
    amount:Mapped[Decimal]=col(Numeric(20,2))
    confirmed_count:Mapped[int|None]=col(Integer)
