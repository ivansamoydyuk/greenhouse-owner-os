"""Preserve upstream source identity through non-posting capture handoff.

Forward-only recovery preserves evidence and journals. Existing capture rows
remain locator-free; no historical identity is invented or rewritten.
"""
from alembic import op
revision='20261005_0014'
down_revision='20261005_0013'
branch_labels=depends_on=None

def upgrade():
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM core.inbox_items WHERE state='CONFIRMED') THEN
        RAISE EXCEPTION 'Existing confirmed Inbox handoffs require explicit source-identity reconciliation before 0014; history is not rewritten' USING ERRCODE='23514';
      END IF;
    END $$;
    DROP INDEX harvest.evidence_binary_sha;
    CREATE UNIQUE INDEX evidence_binary_sha ON harvest.evidence(entity_id,fingerprint) WHERE file_bytes IS NOT NULL AND (source_reference IS NULL OR source='owner_mobile:manual');
    ALTER TABLE core.capture_drafts ADD COLUMN source_namespace text,
      ADD COLUMN source_reference text, ADD COLUMN confirmed_review jsonb,
      ADD CHECK ((source_namespace IS NULL)=(source_reference IS NULL)),
      ADD CHECK (length(source_namespace) BETWEEN 1 AND 200),
      ADD CHECK (length(source_reference) BETWEEN 1 AND 2000);
    CREATE UNIQUE INDEX capture_stable_source ON core.capture_drafts(entity_id,source_namespace,source_reference) WHERE source_reference IS NOT NULL;
    CREATE FUNCTION core.guard_capture_source() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND ((NEW.source_namespace,NEW.source_reference) IS DISTINCT FROM (OLD.source_namespace,OLD.source_reference) OR OLD.confirmed_review IS NOT NULL AND NEW.confirmed_review IS DISTINCT FROM OLD.confirmed_review) THEN
        RAISE EXCEPTION 'Capture source and confirmed review are immutable' USING ERRCODE='55000';
      END IF;
      IF NEW.confirmed_review IS NOT NULL AND NEW.state<>'LINKED' THEN RAISE EXCEPTION 'Review requires linked draft' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER capture_source_integrity BEFORE INSERT OR UPDATE ON core.capture_drafts FOR EACH ROW EXECUTE FUNCTION core.guard_capture_source();
    CREATE FUNCTION core.guard_inbox_source_link() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE child core.capture_drafts%ROWTYPE; original harvest.evidence%ROWTYPE; ns text; ref text;
    BEGIN
      IF NEW.state='CONFIRMED' THEN
        ns=CASE WHEN NEW.locator IS NULL THEN 'inbox:manual' ELSE NEW.source END;
        ref=COALESCE(NEW.locator,NEW.id::text);
        IF NEW.capture_id IS NOT NULL THEN
          SELECT * INTO child FROM core.capture_drafts WHERE id=NEW.capture_id;
          IF (child.source_namespace,child.source_reference) IS DISTINCT FROM (ns,ref) OR (child.confirmed_review-'duplicate_reviewed') IS DISTINCT FROM ((NEW.reviewed->'reviewed')-'duplicate_reviewed') THEN RAISE EXCEPTION 'Inbox capture source identity mismatch' USING ERRCODE='23514'; END IF;
        ELSE
          SELECT e.* INTO original FROM harvest.evidence e JOIN harvest.depot_settlements s ON s.evidence_id=e.id WHERE s.id=NEW.harvest_id;
          IF (original.source,original.source_reference,original.raw_text) IS DISTINCT FROM (ns,ref,NEW.raw_text) THEN RAISE EXCEPTION 'Inbox Harvest source identity mismatch' USING ERRCODE='23514'; END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER inbox_source_link BEFORE INSERT OR UPDATE ON core.inbox_items FOR EACH ROW EXECUTE FUNCTION core.guard_inbox_source_link();
    """)

def downgrade():
    raise RuntimeError('Forward-only: verified backup and preservation/reconciliation of evidence and journals required')
