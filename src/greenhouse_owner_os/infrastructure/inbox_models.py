"""Inbox originals and immutable extraction snapshots; no financial ledger."""
from sqlalchemy import Column, String, Text, Integer, DateTime, LargeBinary, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from .models import Base

class InboxRow(Base):
    __tablename__ = 'inbox_items'
    __table_args__ = {'schema': 'core'}
    id = Column(UUID(as_uuid=True), primary_key=True)
    entity_id = Column(UUID(as_uuid=True), ForeignKey('core.entities.id'), nullable=False)
    source_type = Column(String(30), nullable=False)
    source = Column(Text, nullable=False)
    locator = Column(Text)
    filename = Column(Text)
    mime_type = Column(Text)
    file_bytes = Column(LargeBinary)
    raw_text = Column(Text)
    metadata_json = Column('metadata', JSONB, nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    content_hash = Column(String(64), nullable=False)
    identity_hash = Column(String(64), nullable=False)
    conflict_of_id = Column(UUID(as_uuid=True), ForeignKey('core.inbox_items.id'))
    duplicate_of_id = Column(UUID(as_uuid=True), ForeignKey('core.inbox_items.id'))
    extractor_version = Column(Text, nullable=False)
    extracted_text = Column(Text)
    extraction = Column(JSONB, nullable=False)
    normalized = Column(JSONB, nullable=False)
    validation = Column(JSONB, nullable=False)
    route = Column(String(30), nullable=False)
    confidence = Column(String(20), nullable=False)
    state = Column(String(30), nullable=False)
    version = Column(Integer, nullable=False)
    capture_id = Column(UUID(as_uuid=True), ForeignKey('core.capture_drafts.id'), unique=True)
    task_id = Column(UUID(as_uuid=True))
    task_report_id = Column(UUID(as_uuid=True))
    inventory_id = Column(UUID(as_uuid=True))
    harvest_id = Column(UUID(as_uuid=True), ForeignKey('harvest.depot_settlements.id'))
    reviewed = Column(JSONB)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey('core.actors.id'))
    reviewed_at = Column(DateTime(timezone=True))
    created_by = Column(UUID(as_uuid=True), ForeignKey('core.actors.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
