"""Immutable Inbox source events and atomic reviewed draft handoff.

Recovery after use is forward-only: restore a verified backup separately and
preserve/reconcile all later originals, draft links, audits and journal history.
"""
from alembic import op
revision = '20261005_0013'
down_revision = '20261005_0012'
branch_labels = depends_on = None

def upgrade():
    op.execute("""
    CREATE TABLE core.inbox_items (
      id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id),
      source_type varchar(30) NOT NULL CHECK(source_type IN ('text','pdf','image','email','document')),
      source text NOT NULL CHECK(length(source) BETWEEN 1 AND 200), locator text CHECK(length(locator) BETWEEN 1 AND 2000),
      filename text, mime_type text, file_bytes bytea CHECK(octet_length(file_bytes)<=5242880),
      raw_text text CHECK(length(raw_text)<=10000), metadata jsonb NOT NULL,
      received_at timestamptz NOT NULL, fingerprint varchar(64) NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      content_hash varchar(64) NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
      identity_hash varchar(64) NOT NULL CHECK(identity_hash ~ '^[0-9a-f]{64}$'),
      conflict_of_id uuid REFERENCES core.inbox_items(id), duplicate_of_id uuid REFERENCES core.inbox_items(id),
      extractor_version text NOT NULL, extracted_text text, extraction jsonb NOT NULL,
      normalized jsonb NOT NULL, validation jsonb NOT NULL, route varchar(30) NOT NULL CHECK(route IN ('FINANCE','HARVEST_DEPOT','DOCUMENTS','UNRESOLVED')),
      confidence varchar(20) NOT NULL CHECK(confidence IN ('HIGH','LOW','UNAVAILABLE')),
      state varchar(30) NOT NULL CHECK(state IN ('READY','NEEDS_REVIEW','CONFLICT','CONFIRMED','REJECTED')),
      version integer NOT NULL CHECK(version>0), capture_id uuid UNIQUE REFERENCES core.capture_drafts(id),
      harvest_id uuid REFERENCES harvest.depot_settlements(id), reviewed jsonb,
      reviewed_by uuid REFERENCES core.actors(id), reviewed_at timestamptz,
      created_by uuid NOT NULL REFERENCES core.actors(id), created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(raw_text IS NOT NULL OR file_bytes IS NOT NULL),
      CHECK((state IN ('CONFIRMED','REJECTED'))=(reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed IS NOT NULL)),
      CHECK((state='CONFIRMED')=(capture_id IS NOT NULL OR harvest_id IS NOT NULL)),
      CHECK(NOT(capture_id IS NOT NULL AND harvest_id IS NOT NULL)),
      CHECK(conflict_of_id IS NULL OR conflict_of_id<>id), CHECK(duplicate_of_id IS NULL OR duplicate_of_id<>id),
      UNIQUE(entity_id,source,locator,content_hash));
    CREATE UNIQUE INDEX inbox_source_root ON core.inbox_items(entity_id,source,locator) WHERE locator IS NOT NULL AND conflict_of_id IS NULL;
    CREATE UNIQUE INDEX inbox_original_identity ON core.inbox_items(entity_id,source,locator,identity_hash) WHERE locator IS NOT NULL;
    CREATE INDEX inbox_queue ON core.inbox_items(entity_id,created_at DESC,id);
    CREATE INDEX inbox_content ON core.inbox_items(entity_id,fingerprint);
    CREATE FUNCTION core.guard_inbox() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE predecessor core.inbox_items%ROWTYPE; child core.capture_drafts%ROWTYPE; receipt harvest.depot_settlements%ROWTYPE; evidence harvest.evidence%ROWTYPE; domain_state text;
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Inbox originals cannot be deleted' USING ERRCODE='55000'; END IF;
      IF TG_OP='UPDATE' THEN
        IF OLD.state IN ('CONFIRMED','REJECTED') OR
          (to_jsonb(NEW)-ARRAY['state','version','capture_id','harvest_id','reviewed','reviewed_by','reviewed_at']) IS DISTINCT FROM
          (to_jsonb(OLD)-ARRAY['state','version','capture_id','harvest_id','reviewed','reviewed_by','reviewed_at']) OR
          NEW.version<>OLD.version+1 OR NEW.state NOT IN ('CONFIRMED','REJECTED') THEN
          RAISE EXCEPTION 'Inbox evidence and finalized interpretation are immutable' USING ERRCODE='55000';
        END IF;
      ELSE
        IF NEW.state NOT IN ('READY','NEEDS_REVIEW','CONFLICT') OR NEW.version<>1 THEN
          RAISE EXCEPTION 'Inbox must start unconfirmed' USING ERRCODE='23514'; END IF;
      END IF;
      IF NEW.conflict_of_id IS NOT NULL THEN
        SELECT * INTO predecessor FROM core.inbox_items WHERE id=NEW.conflict_of_id;
        IF NOT FOUND OR predecessor.conflict_of_id IS NOT NULL OR
          (predecessor.entity_id,predecessor.source,predecessor.locator) IS DISTINCT FROM (NEW.entity_id,NEW.source,NEW.locator) OR predecessor.identity_hash=NEW.identity_hash THEN
          RAISE EXCEPTION 'Invalid source conflict lineage' USING ERRCODE='23514'; END IF;
        IF NEW.state='CONFIRMED' THEN RAISE EXCEPTION 'Conflicting original requires a new corrected source event' USING ERRCODE='23514'; END IF;
      END IF;
      IF NEW.duplicate_of_id IS NOT NULL THEN
        SELECT * INTO predecessor FROM core.inbox_items WHERE id=NEW.duplicate_of_id;
        IF NOT FOUND OR predecessor.entity_id<>NEW.entity_id OR predecessor.fingerprint<>NEW.fingerprint THEN
          RAISE EXCEPTION 'Invalid content duplicate scope' USING ERRCODE='23514'; END IF;
      END IF;
      IF NEW.state='CONFIRMED' THEN
        IF NEW.capture_id IS NOT NULL THEN
          SELECT * INTO child FROM core.capture_drafts WHERE id=NEW.capture_id;
          IF NOT FOUND OR child.entity_id<>NEW.entity_id OR child.state<>'LINKED' OR child.route<>NEW.route OR child.original_text IS DISTINCT FROM NEW.extracted_text THEN
            RAISE EXCEPTION 'Inbox requires same-entity confirmed capture' USING ERRCODE='23514'; END IF;
          IF child.finance_kind='payment' THEN SELECT status INTO domain_state FROM finance.payments WHERE id=child.finance_id;
          ELSE SELECT status INTO domain_state FROM finance.obligations WHERE id=child.finance_id; END IF;
          IF domain_state IS DISTINCT FROM 'draft' THEN RAISE EXCEPTION 'Inbox handoff must be a non-posting draft' USING ERRCODE='23514'; END IF;
        ELSE
          SELECT * INTO receipt FROM harvest.depot_settlements WHERE id=NEW.harvest_id;
          IF NOT FOUND OR receipt.entity_id<>NEW.entity_id OR NEW.route<>'HARVEST_DEPOT' THEN
            RAISE EXCEPTION 'Inbox requires same-entity Harvest receipt' USING ERRCODE='23514'; END IF;
          SELECT * INTO evidence FROM harvest.evidence WHERE id=receipt.evidence_id;
          IF evidence.entity_id<>NEW.entity_id OR (NEW.file_bytes IS NOT NULL AND evidence.file_bytes IS DISTINCT FROM NEW.file_bytes) OR (NEW.file_bytes IS NULL AND evidence.raw_text IS DISTINCT FROM NEW.raw_text) THEN
            RAISE EXCEPTION 'Inbox receipt must retain the same original evidence' USING ERRCODE='23514'; END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER inbox_integrity BEFORE INSERT OR UPDATE OR DELETE ON core.inbox_items FOR EACH ROW EXECUTE FUNCTION core.guard_inbox();
    """)

def downgrade():
    raise RuntimeError('Forward-only: verified backup, preserved originals and draft/journal reconciliation required')
