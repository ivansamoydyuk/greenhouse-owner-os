"""Immutable non-posting evidence storage with source-level concurrent deduplication."""
import copy
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from greenhouse_owner_os.domain.errors import ConflictError, NotFoundError, ValidationError
from greenhouse_owner_os.infrastructure.models import EntityRow, ImportBatchRow, HistoricalCandidateRow


def serialize_batch(row):
    return {k: (str(getattr(row, k)) if k in ('id', 'entity_id', 'created_by') else
                getattr(row, k).isoformat() if k == 'imported_at' else getattr(row, k))
            for k in ('id', 'entity_id', 'source', 'source_file', 'fingerprint', 'imported_at', 'created_by')}


def serialize_candidate(row, batch):
    result = {k: getattr(row, k) for k in ('source_key', 'source_row', 'source_document', 'verification_state', 'raw_value', 'source_reference')}
    result.update(id=str(row.id), import_batch_id=str(row.import_batch_id),
                  entity_id=str(batch.entity_id), source=batch.source, source_file=batch.source_file,
                  fingerprint=batch.fingerprint, event_date=row.event_date.isoformat() if row.event_date else None,
                  imported_at=row.imported_at.isoformat(), created_by=str(row.created_by),
                  confidence=str(row.confidence) if row.confidence is not None else None,
                  posting_policy='NON_POSTING')
    return result


class SqlHistoryRepositoryMixin:
    def get_cutover(self, entity_id):
        row = self.session.get(EntityRow, entity_id)
        if not row: raise NotFoundError('entity not found')
        return row.finance_cutover_date, row.finance_opening_policy

    def assert_live_date(self, entity_id, event_date):
        cutoff, _ = self.get_cutover(entity_id)
        if event_date < cutoff: raise ValidationError('pre-cut-over live posting prohibited')

    def ensure_import_batch(self, values):
        stmt = insert(ImportBatchRow).values(id=uuid4(), **values).on_conflict_do_nothing(
            constraint='uq_import_batch_source').returning(ImportBatchRow.id)
        new_id = self.session.scalar(stmt)
        row = self.session.get(ImportBatchRow, new_id) if new_id else self.session.scalar(select(ImportBatchRow).where(
            ImportBatchRow.entity_id == values['entity_id'], ImportBatchRow.source == values['source'],
            ImportBatchRow.fingerprint == values['fingerprint']))
        if row.source_file != values['source_file']: raise ConflictError('same source fingerprint has different source_file')
        return serialize_batch(row), bool(new_id)

    def get_import_batch(self, batch_id):
        row = self.session.get(ImportBatchRow, batch_id)
        if not row: raise NotFoundError('import batch not found')
        return serialize_batch(row)

    def ensure_historical_candidate(self, values):
        batch = self.session.get(ImportBatchRow, values['import_batch_id'])
        if not batch: raise NotFoundError('import batch not found')
        stmt = insert(HistoricalCandidateRow).values(id=uuid4(), **values).on_conflict_do_nothing(
            constraint='uq_historical_candidate_source').returning(HistoricalCandidateRow.id)
        new_id = self.session.scalar(stmt)
        row = self.session.get(HistoricalCandidateRow, new_id) if new_id else self.session.scalar(select(HistoricalCandidateRow).where(
            HistoricalCandidateRow.import_batch_id == batch.id, HistoricalCandidateRow.source_key == values['source_key']))
        if row.content_hash != values['content_hash']: raise ConflictError('source locator already imported with different content')
        return serialize_candidate(row, batch), bool(new_id)

    def get_historical_candidate(self, candidate_id):
        row = self.session.get(HistoricalCandidateRow, candidate_id)
        if not row: raise NotFoundError('historical candidate not found')
        return serialize_candidate(row, self.session.get(ImportBatchRow, row.import_batch_id))

    def historical_candidates(self, batch_id):
        self.get_import_batch(batch_id)
        return [self.get_historical_candidate(rid) for rid in self.session.scalars(select(HistoricalCandidateRow.id).where(
            HistoricalCandidateRow.import_batch_id == batch_id).order_by(HistoricalCandidateRow.id))]


class MemoryHistoryRepositoryMixin:
    def get_cutover(self, entity_id):
        if entity_id not in self.store.cutovers: raise NotFoundError('entity cut-over configuration missing')
        return self.store.cutovers[entity_id], 'ZERO'

    def assert_live_date(self, entity_id, event_date):
        cutoff, _ = self.get_cutover(entity_id)
        if event_date < cutoff: raise ValidationError('pre-cut-over live posting prohibited')

    def ensure_import_batch(self, values):
        for old in self.store.import_batches.values():
            if (old['entity_id'], old['source'], old['fingerprint']) == (str(values['entity_id']), values['source'], values['fingerprint']):
                if old['source_file'] != values['source_file']: raise ConflictError('same source fingerprint has different source_file')
                return copy.deepcopy(old), False
        row = dict(values, id=str(uuid4()), imported_at=datetime.now(timezone.utc).isoformat())
        row['entity_id'], row['created_by'] = str(row['entity_id']), str(row['created_by'])
        self.store.import_batches[row['id']] = row
        return copy.deepcopy(row), True

    def get_import_batch(self, batch_id):
        if str(batch_id) not in self.store.import_batches: raise NotFoundError('import batch not found')
        return copy.deepcopy(self.store.import_batches[str(batch_id)])

    def ensure_historical_candidate(self, values):
        batch = self.get_import_batch(values['import_batch_id'])
        for old in self.store.historical_candidates.values():
            if (old['import_batch_id'], old['source_key']) == (str(values['import_batch_id']), values['source_key']):
                if old['content_hash'] != values['content_hash']: raise ConflictError('source locator already imported with different content')
                return self.get_historical_candidate(old['id']), False
        row = dict(values, id=str(uuid4()), imported_at=datetime.now(timezone.utc).isoformat())
        row.update(import_batch_id=str(row['import_batch_id']), created_by=str(row['created_by']),
                   event_date=row['event_date'].isoformat() if row['event_date'] else None,
                   confidence=str(row['confidence']) if row['confidence'] is not None else None,
                   verification_state=str(row['verification_state']))
        self.store.historical_candidates[row['id']] = row
        return self.get_historical_candidate(row['id']), True

    def get_historical_candidate(self, candidate_id):
        row = self.store.historical_candidates.get(str(candidate_id))
        if not row: raise NotFoundError('historical candidate not found')
        batch = self.get_import_batch(row['import_batch_id'])
        return copy.deepcopy({**{k:v for k,v in row.items() if k != 'content_hash'},
            'entity_id':batch['entity_id'], 'source':batch['source'], 'source_file':batch['source_file'],
            'fingerprint':batch['fingerprint'], 'posting_policy':'NON_POSTING'})

    def historical_candidates(self, batch_id):
        self.get_import_batch(batch_id)
        return [self.get_historical_candidate(rid) for rid in sorted(self.store.historical_candidates)
                if self.store.historical_candidates[rid]['import_batch_id'] == str(batch_id)]
