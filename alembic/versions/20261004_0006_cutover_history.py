"""Zero-opening entity cut-over and immutable non-posting import evidence.

Forward-only recovery: restore verified pre-upgrade backup to a separate DB;
retain and reconcile all subsequent live journal and import evidence. Never
remove guards or replay history in place to downgrade.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '20261004_0006'
down_revision = '20261004_0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('entities', sa.Column('finance_cutover_date', sa.Date(), nullable=False,
        server_default=sa.text("DATE '2026-10-04'")), schema='core')
    op.add_column('entities', sa.Column('finance_opening_policy', sa.String(10), nullable=False,
        server_default='ZERO'), schema='core')
    op.create_check_constraint('ck_entity_opening_policy', 'entities', "finance_opening_policy = 'ZERO'", schema='core')
    op.create_table('import_batches',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('entity_id', UUID(as_uuid=True), sa.ForeignKey('core.entities.id'), nullable=False),
        sa.Column('source', sa.String(200), nullable=False),
        sa.Column('source_file', sa.Text(), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('core.actors.id'), nullable=False),
        sa.UniqueConstraint('entity_id', 'source', 'fingerprint', name='uq_import_batch_source'),
        sa.CheckConstraint("fingerprint ~ '^[a-f0-9]{64}$'", name='ck_import_batch_fingerprint'),
        sa.CheckConstraint("btrim(source) <> '' AND btrim(source_file) <> ''", name='ck_import_batch_source'),
        schema='core')
    op.create_table('historical_candidates',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('import_batch_id', UUID(as_uuid=True), sa.ForeignKey('core.import_batches.id'), nullable=False),
        sa.Column('source_key', sa.String(64), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('source_row', sa.Text()), sa.Column('source_document', sa.Text()),
        sa.Column('event_date', sa.Date()),
        sa.Column('verification_state', sa.String(10), nullable=False, server_default='HISTORY'),
        sa.Column('confidence', sa.Numeric(7,6)),
        sa.Column('raw_value', JSONB(none_as_null=True)), sa.Column('source_reference', sa.Text()),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('core.actors.id'), nullable=False),
        sa.UniqueConstraint('import_batch_id', 'source_key', name='uq_historical_candidate_source'),
        sa.CheckConstraint("verification_state IN ('VERIFIED','VERIFY','HISTORY')", name='ck_candidate_verification'),
        sa.CheckConstraint("source_key ~ '^[a-f0-9]{64}$' AND content_hash ~ '^[a-f0-9]{64}$'", name='ck_candidate_hashes'),
        sa.CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 1)', name='ck_candidate_confidence'),
        sa.CheckConstraint("(source_row IS NOT NULL AND btrim(source_row) <> '') OR (source_document IS NOT NULL AND btrim(source_document) <> '')", name='ck_candidate_locator'),
        sa.CheckConstraint("raw_value IS NOT NULL OR (source_reference IS NOT NULL AND btrim(source_reference) <> '')", name='ck_candidate_evidence'),
        schema='core')
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM finance.postings p JOIN core.entities e ON e.id=p.entity_id
                 WHERE p.event_date < e.finance_cutover_date) THEN
        RAISE EXCEPTION 'pre-cut-over ledger exists; explicit reconciliation required' USING ERRCODE='23514';
      END IF;
    END $$;
    CREATE FUNCTION core.guard_cutover_config() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.finance_cutover_date <> DATE '2026-10-04' OR NEW.finance_opening_policy <> 'ZERO' THEN
          RAISE EXCEPTION 'entity requires accepted zero-opening cut-over policy' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.finance_cutover_date IS DISTINCT FROM OLD.finance_cutover_date OR
            NEW.finance_opening_policy IS DISTINCT FROM OLD.finance_opening_policy THEN
        RAISE EXCEPTION 'cut-over policy is immutable; separate controlled workflow required' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER entity_cutover_guard BEFORE INSERT OR UPDATE ON core.entities
      FOR EACH ROW EXECUTE FUNCTION core.guard_cutover_config();

    CREATE FUNCTION finance.guard_live_cutover() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE cutoff date;
    BEGIN
      SELECT finance_cutover_date INTO cutoff FROM core.entities WHERE id=NEW.entity_id;
      IF cutoff IS NULL OR NEW.event_date < cutoff THEN
        RAISE EXCEPTION 'pre-cut-over live posting prohibited' USING ERRCODE='23514';
      END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended(NEW.source_id::text, 42));
      IF EXISTS (SELECT 1 FROM core.historical_candidates WHERE id=NEW.source_id) THEN
        RAISE EXCEPTION 'import candidates are non-posting evidence' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER posting_cutover_guard BEFORE INSERT ON finance.postings
      FOR EACH ROW EXECUTE FUNCTION finance.guard_live_cutover();

    CREATE FUNCTION finance.guard_domain_cutover() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE cutoff date;
    BEGIN
      IF NEW.status IN ('posted','reversed') THEN
        SELECT finance_cutover_date INTO cutoff FROM core.entities WHERE id=NEW.entity_id;
        IF cutoff IS NULL OR NEW.event_date < cutoff THEN
          RAISE EXCEPTION 'pre-cut-over financial record cannot become live' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION core.guard_candidate_source() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtextextended(NEW.id::text, 42));
      IF EXISTS (SELECT 1 FROM finance.postings WHERE source_id=NEW.id) THEN
        RAISE EXCEPTION 'import candidate cannot be linked to live ledger' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER candidate_nonposting_guard BEFORE INSERT ON core.historical_candidates
      FOR EACH ROW EXECUTE FUNCTION core.guard_candidate_source();
    """)
    for table in ('obligations', 'payments', 'own_account_transfers', 'owner_fundings', 'direct_settlements', 'advance_applications'):
        op.execute(f'CREATE TRIGGER domain_cutover_guard BEFORE INSERT OR UPDATE ON finance.{table} FOR EACH ROW EXECUTE FUNCTION finance.guard_domain_cutover()')
    for table in ('import_batches', 'historical_candidates'):
        op.execute(f'CREATE TRIGGER immutable_import_evidence BEFORE UPDATE OR DELETE ON core.{table} FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()')


def downgrade():
    raise RuntimeError('Forward-only recovery: separate backup restore + journal/import reconciliation required')
