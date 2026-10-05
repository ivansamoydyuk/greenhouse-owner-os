"""Require explicit same-evidence lineage for receipt corrections after reversal.

Forward-only recovery: restore verified backup separately, preserve/reconcile
subsequent evidence and financial journals. Never delete historical receipts.
"""
from alembic import op

revision = '20261005_0012'
down_revision = '20261005_0011'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE UNIQUE INDEX receipt_evidence_root ON harvest.depot_settlements(evidence_id) WHERE replacement_of_id IS NULL')
    op.execute("""
    CREATE FUNCTION harvest.guard_receipt_lineage() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE predecessor harvest.depot_settlements%ROWTYPE;
    BEGIN
      PERFORM id FROM harvest.evidence WHERE id=NEW.evidence_id FOR UPDATE;
      IF NEW.replacement_of_id IS NULL THEN
        IF EXISTS(SELECT 1 FROM harvest.depot_settlements WHERE evidence_id=NEW.evidence_id) THEN
          RAISE EXCEPTION 'historical receipt evidence requires explicit replacement lineage' USING ERRCODE='23514';
        END IF;
      ELSE
        SELECT * INTO predecessor FROM harvest.depot_settlements WHERE id=NEW.replacement_of_id FOR UPDATE;
        IF NOT FOUND OR (predecessor.entity_id,predecessor.depot_id,predecessor.currency,predecessor.evidence_id,predecessor.status)
          IS DISTINCT FROM (NEW.entity_id,NEW.depot_id,NEW.currency,NEW.evidence_id,'reversed'::varchar) THEN
          RAISE EXCEPTION 'replacement requires reversed predecessor with same entity/depot/currency/evidence' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER receipt_lineage BEFORE INSERT ON harvest.depot_settlements
      FOR EACH ROW EXECUTE FUNCTION harvest.guard_receipt_lineage();
    """)


def downgrade():
    raise RuntimeError('Forward-only: verified backup and preserved evidence/journal reconciliation required')
