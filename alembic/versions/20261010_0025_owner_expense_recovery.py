"""Source-linked DirectExpense recovery, without rewriting existing evidence.

Forward-only: restore a verified backup separately, then reconcile and retain
later originals, revisions, recovery links, audit and effects together.
"""
from alembic import op

revision = '20261010_0025'
down_revision = '20261008_0024'
branch_labels = depends_on = None


def upgrade():
    op.execute(SQL)


def downgrade():
    raise RuntimeError('Forward-only recovery evidence; restore separately and reconcile later evidence')


SQL = r"""
CREATE TABLE core.owner_recovery_links (
 entity_id uuid NOT NULL, inbox_id uuid NOT NULL,
 origin_proposal_id uuid, origin_version int,
 recovery_proposal_id uuid PRIMARY KEY, failed_job_id uuid,
 created_by uuid NOT NULL REFERENCES core.actors(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK ((origin_proposal_id IS NULL) = (origin_version IS NULL)),
 CHECK ((origin_proposal_id IS NULL) = (failed_job_id IS NOT NULL)),
 CHECK (origin_proposal_id IS DISTINCT FROM recovery_proposal_id),
 FOREIGN KEY(entity_id,inbox_id) REFERENCES core.inbox_items(entity_id,id),
 FOREIGN KEY(entity_id,origin_proposal_id) REFERENCES core.proposed_actions(entity_id,id),
 FOREIGN KEY(origin_proposal_id,origin_version) REFERENCES core.proposal_revisions(proposal_id,version),
 FOREIGN KEY(entity_id,recovery_proposal_id) REFERENCES core.proposed_actions(entity_id,id),
 FOREIGN KEY(entity_id,failed_job_id) REFERENCES core.processing_jobs(entity_id,id));
CREATE UNIQUE INDEX owner_recovery_origin ON core.owner_recovery_links(entity_id,origin_proposal_id)
 WHERE origin_proposal_id IS NOT NULL;
CREATE UNIQUE INDEX owner_recovery_failed_source ON core.owner_recovery_links(entity_id,inbox_id)
 WHERE origin_proposal_id IS NULL;
CREATE INDEX owner_recovery_source ON core.owner_recovery_links(entity_id,inbox_id);
CREATE TRIGGER recovery_link_immutable BEFORE UPDATE OR DELETE ON core.owner_recovery_links
 FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();

-- Ordinary callers acquire this key BEFORE row locks. Arbitrary SQL may already
-- hold a tuple lock; never wait in its trigger and invert the application order.
-- VOLATILE fresh reads plus READ COMMITTED are required: an advisory lock cannot
-- refresh a pre-existing REPEATABLE READ snapshot.
CREATE FUNCTION core.r2_recovery_write_lock(e uuid,s uuid) RETURNS void
 LANGUAGE plpgsql VOLATILE AS $$
BEGIN
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'R2 writes require READ COMMITTED' USING ERRCODE='23514';
 END IF;
 IF NOT pg_try_advisory_xact_lock(hashtextextended('owner-review-source:'||e::text||':'||s::text,0)) THEN
  RAISE EXCEPTION 'R2 source is busy; retry the transaction' USING ERRCODE='40001';
 END IF;
END $$;

CREATE FUNCTION core.r2_recovery_write_guard() RETURNS trigger
 LANGUAGE plpgsql VOLATILE AS $$
DECLARE e uuid; s uuid; link core.owner_recovery_links;
BEGIN
 e := NEW.entity_id;
 IF TG_TABLE_NAME IN ('proposed_actions','processing_jobs','extraction_runs','owner_recovery_links') THEN
  s := NEW.inbox_id;
 ELSE
  SELECT inbox_id INTO s FROM core.proposed_actions WHERE id=NEW.proposal_id AND entity_id=e;
 END IF;
 IF s IS NULL THEN RAISE EXCEPTION 'R2 source identity required' USING ERRCODE='23514'; END IF;
 PERFORM core.r2_recovery_write_lock(e,s);
 IF TG_TABLE_NAME='processing_jobs' AND EXISTS(SELECT 1 FROM core.owner_recovery_links WHERE entity_id=e AND inbox_id=s) THEN
  RAISE EXCEPTION 'Preparation reserved for manual recovery' USING ERRCODE='23514';
 ELSIF TG_TABLE_NAME='extraction_runs' AND EXISTS(SELECT 1 FROM core.owner_recovery_links WHERE entity_id=e AND inbox_id=s) THEN
  RAISE EXCEPTION 'Late extraction after manual recovery' USING ERRCODE='23514';
 ELSIF TG_TABLE_NAME='proposed_actions' THEN
  IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM core.owner_recovery_links WHERE entity_id=e AND inbox_id=s
   AND (origin_proposal_id IS NULL OR NEW.slot NOT LIKE 'manual:direct-expense:%')) THEN
   RAISE EXCEPTION 'Preparation cannot add proposals after recovery reservation' USING ERRCODE='23514';
  END IF;
  SELECT * INTO link FROM core.owner_recovery_links WHERE origin_proposal_id=NEW.id;
  IF link.recovery_proposal_id IS NOT NULL AND TG_OP='UPDATE' AND NOT (
   OLD.version=link.origin_version AND NEW.version=link.origin_version+1 AND NEW.state='REJECTED'
   AND EXISTS(SELECT 1 FROM core.confirmation_effects WHERE proposal_id=link.recovery_proposal_id)) THEN
   RAISE EXCEPTION 'Recovery origin is reserved and cannot reopen' USING ERRCODE='23514';
  END IF;
 ELSIF TG_TABLE_NAME='confirmation_effects' THEN
  IF EXISTS(SELECT 1 FROM core.owner_recovery_links WHERE origin_proposal_id=NEW.proposal_id) THEN
   RAISE EXCEPTION 'Recovery origin cannot create an effect' USING ERRCODE='23514';
  END IF;
 ELSIF TG_TABLE_NAME IN ('proposal_revisions','proposal_actions') THEN
  SELECT * INTO link FROM core.owner_recovery_links WHERE origin_proposal_id=NEW.proposal_id;
  IF link.recovery_proposal_id IS NOT NULL THEN
   IF NEW.version<>link.origin_version+1 OR NOT EXISTS(
    SELECT 1 FROM core.confirmation_effects WHERE proposal_id=link.recovery_proposal_id)
   THEN RAISE EXCEPTION 'Reserved origin history only changes during target confirmation' USING ERRCODE='23514'; END IF;
   IF TG_TABLE_NAME='proposal_revisions' THEN
    IF NEW.reason<>'REJECT' THEN RAISE EXCEPTION 'Origin retirement revision required' USING ERRCODE='23514'; END IF;
   ELSIF NEW.action<>'REJECT' THEN
    RAISE EXCEPTION 'Origin retirement action required' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER recovery_write_guard BEFORE INSERT OR UPDATE ON core.proposed_actions
 FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_write_guard();
CREATE TRIGGER recovery_write_guard BEFORE INSERT OR UPDATE ON core.processing_jobs
 FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_write_guard();
CREATE TRIGGER recovery_write_guard BEFORE INSERT ON core.extraction_runs
 FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_write_guard();
CREATE TRIGGER recovery_write_guard BEFORE INSERT ON core.proposal_revisions
 FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_write_guard();
CREATE TRIGGER recovery_write_guard BEFORE INSERT ON core.proposal_actions
 FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_write_guard();
CREATE TRIGGER recovery_write_guard BEFORE INSERT ON core.confirmation_effects
 FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_write_guard();
CREATE TRIGGER recovery_write_guard BEFORE INSERT OR UPDATE ON finance.direct_expenses
 FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_write_guard();

CREATE FUNCTION core.r2_recovery_link_guard() RETURNS trigger
 LANGUAGE plpgsql VOLATILE AS $$
DECLARE original core.inbox_items; origin core.proposed_actions; target core.proposed_actions;
BEGIN
 PERFORM core.r2_recovery_write_lock(NEW.entity_id,NEW.inbox_id);
 SELECT * INTO original FROM core.inbox_items WHERE id=NEW.inbox_id AND entity_id=NEW.entity_id;
 SELECT * INTO target FROM core.proposed_actions WHERE id=NEW.recovery_proposal_id AND entity_id=NEW.entity_id;
 IF original.source IS DISTINCT FROM 'owner:r2' OR original.created_by IS DISTINCT FROM NEW.created_by
 OR NOT core.r2_owner(NEW.entity_id,NEW.created_by)
 OR target.inbox_id IS DISTINCT FROM NEW.inbox_id OR target.action_type IS DISTINCT FROM 'DIRECT_EXPENSE'
 OR target.created_by IS DISTINCT FROM NEW.created_by OR target.state IN ('POSTED','CONFIRMED_NONPOSTING','REJECTED')
 OR target.slot IS DISTINCT FROM 'manual:direct-expense:'||coalesce(NEW.origin_proposal_id,NEW.inbox_id)::text
 OR EXISTS(SELECT 1 FROM core.confirmation_effects WHERE proposal_id=NEW.recovery_proposal_id)
 THEN RAISE EXCEPTION 'Recovery source/target/owner mismatch or consumed target' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM core.processing_jobs WHERE inbox_id=NEW.inbox_id AND
  (state IN ('QUEUED','RUNNING') OR state='FAILED' AND attempts<3 OR lease_token IS NOT NULL OR lease_until IS NOT NULL))
 THEN RAISE EXCEPTION 'Preparation still active or retryable' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM core.owner_recovery_links WHERE
  recovery_proposal_id=NEW.origin_proposal_id OR origin_proposal_id=NEW.recovery_proposal_id)
 THEN RAISE EXCEPTION 'Recovery chains are unsupported' USING ERRCODE='23514'; END IF;
 IF NEW.origin_proposal_id IS NOT NULL THEN
  SELECT * INTO origin FROM core.proposed_actions WHERE id=NEW.origin_proposal_id AND entity_id=NEW.entity_id;
  IF origin.inbox_id IS DISTINCT FROM NEW.inbox_id OR origin.action_type IS DISTINCT FROM 'UNRESOLVED'
  OR origin.created_by IS DISTINCT FROM NEW.created_by OR origin.version IS DISTINCT FROM NEW.origin_version
  OR origin.state NOT IN ('NEEDS_INPUT','READY','DEFERRED','STALE')
  OR EXISTS(SELECT 1 FROM core.confirmation_effects WHERE proposal_id=NEW.origin_proposal_id)
  OR EXISTS(SELECT 1 FROM core.owner_recovery_links WHERE inbox_id=NEW.inbox_id AND origin_proposal_id IS NULL)
  THEN RAISE EXCEPTION 'Selected origin is not eligible for recovery' USING ERRCODE='23514'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM core.processing_jobs WHERE id=NEW.failed_job_id AND entity_id=NEW.entity_id
   AND inbox_id=NEW.inbox_id AND state='FAILED' AND attempts=3 AND lease_token IS NULL AND lease_until IS NULL)
  OR EXISTS(SELECT 1 FROM core.proposed_actions WHERE inbox_id=NEW.inbox_id AND id<>NEW.recovery_proposal_id)
  OR EXISTS(SELECT 1 FROM core.owner_recovery_links WHERE inbox_id=NEW.inbox_id)
  THEN RAISE EXCEPTION 'Exhausted source without proposals required' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER recovery_link_guard BEFORE INSERT ON core.owner_recovery_links
 FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_link_guard();

-- Final checks run from BOTH directions. In particular, effect-before-link and
-- link-before-effect in one transaction cannot bypass family finality.
CREATE FUNCTION core.r2_recovery_consistency() RETURNS trigger
 LANGUAGE plpgsql VOLATILE AS $$
DECLARE e uuid; s uuid; link core.owner_recovery_links; origin core.proposed_actions;
 effect core.confirmation_effects; original_revision core.proposal_revisions; retired core.proposal_revisions;
BEGIN
 e := NEW.entity_id;
 IF TG_TABLE_NAME IN ('proposed_actions','processing_jobs','owner_recovery_links') THEN s := NEW.inbox_id;
 ELSE SELECT inbox_id INTO s FROM core.proposed_actions WHERE id=NEW.proposal_id AND entity_id=e; END IF;
 PERFORM core.r2_recovery_write_lock(e,s);
 IF EXISTS(SELECT 1 FROM core.proposed_actions p WHERE p.entity_id=e AND p.inbox_id=s
  AND p.slot LIKE 'manual:direct-expense:%' AND NOT EXISTS(
   SELECT 1 FROM core.owner_recovery_links l WHERE l.recovery_proposal_id=p.id))
 THEN RAISE EXCEPTION 'Manual recovery target requires immutable linkage' USING ERRCODE='23514'; END IF;
 FOR link IN SELECT * FROM core.owner_recovery_links WHERE entity_id=e AND inbox_id=s LOOP
  IF EXISTS(SELECT 1 FROM core.processing_jobs WHERE inbox_id=s AND
   (state IN ('QUEUED','RUNNING') OR state='FAILED' AND attempts<3 OR lease_token IS NOT NULL OR lease_until IS NOT NULL))
  OR EXISTS(SELECT 1 FROM core.owner_recovery_links x WHERE x.recovery_proposal_id=link.origin_proposal_id
   OR x.origin_proposal_id=link.recovery_proposal_id)
  THEN RAISE EXCEPTION 'Recovery preparation fence or chain violated' USING ERRCODE='23514'; END IF;
  IF link.origin_proposal_id IS NULL THEN
   IF (SELECT count(*) FROM core.proposed_actions WHERE inbox_id=s)<>1
   OR NOT EXISTS(SELECT 1 FROM core.processing_jobs WHERE id=link.failed_job_id AND state='FAILED'
    AND attempts=3 AND lease_token IS NULL AND lease_until IS NULL)
   THEN RAISE EXCEPTION 'Exhausted recovery source cardinality violated' USING ERRCODE='23514'; END IF;
  ELSE
   SELECT * INTO origin FROM core.proposed_actions WHERE id=link.origin_proposal_id;
   SELECT * INTO effect FROM core.confirmation_effects WHERE proposal_id=link.recovery_proposal_id;
   IF EXISTS(SELECT 1 FROM core.confirmation_effects WHERE proposal_id=origin.id)
   THEN RAISE EXCEPTION 'Recovery origin already has an effect' USING ERRCODE='23514'; END IF;
   IF effect.proposal_id IS NULL THEN
    IF origin.version<>link.origin_version OR origin.state NOT IN ('NEEDS_INPUT','READY','DEFERRED','STALE')
    THEN RAISE EXCEPTION 'Reserved origin changed before confirmation' USING ERRCODE='23514'; END IF;
   ELSE
    SELECT * INTO original_revision FROM core.proposal_revisions WHERE proposal_id=origin.id AND version=link.origin_version;
    SELECT * INTO retired FROM core.proposal_revisions WHERE proposal_id=origin.id AND version=origin.version;
    IF origin.state<>'REJECTED' OR origin.version<>link.origin_version+1 OR retired.reason<>'REJECT'
    OR retired.payload IS DISTINCT FROM original_revision.payload
    OR retired.provenance IS DISTINCT FROM original_revision.provenance
    OR retired.actor_id IS DISTINCT FROM effect.actor_id
    OR NOT EXISTS(SELECT 1 FROM core.proposal_actions WHERE proposal_id=origin.id AND version=origin.version
     AND action='REJECT' AND actor_id=effect.actor_id AND snapshot->>'recovery_proposal_id'=link.recovery_proposal_id::text)
    THEN RAISE EXCEPTION 'Confirmed recovery requires exact origin retirement audit' USING ERRCODE='23514'; END IF;
   END IF;
  END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER recovery_consistency AFTER INSERT ON core.owner_recovery_links
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_consistency();
CREATE CONSTRAINT TRIGGER recovery_consistency AFTER INSERT OR UPDATE ON core.proposed_actions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_consistency();
CREATE CONSTRAINT TRIGGER recovery_consistency AFTER INSERT OR UPDATE ON core.processing_jobs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_consistency();
CREATE CONSTRAINT TRIGGER recovery_consistency AFTER INSERT ON core.confirmation_effects
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_consistency();
CREATE CONSTRAINT TRIGGER recovery_consistency AFTER INSERT ON core.proposal_revisions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_consistency();
CREATE CONSTRAINT TRIGGER recovery_consistency AFTER INSERT ON core.proposal_actions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_recovery_consistency();

-- Only new confirmations are checked. Historical expenses and 0023/0024 bytes
-- remain unchanged; this closes the observed exact-purpose snapshot omission.
CREATE FUNCTION core.r2_expense_purpose_snapshot() RETURNS trigger
 LANGUAGE plpgsql VOLATILE AS $$
DECLARE fields jsonb;
BEGIN
 IF NEW.direct_expense_id IS NOT NULL THEN
  SELECT payload INTO fields FROM core.proposal_revisions WHERE proposal_id=NEW.proposal_id AND version=NEW.version;
  IF fields->>'payment_purpose' NOT IN ('expense') OR fields->>'obligation_id' IS NOT NULL
  OR fields->>'due_date' IS NOT NULL OR coalesce(fields->'attributions','[]'::jsonb)<>'[]'::jsonb THEN
   RAISE EXCEPTION 'DirectExpense does not support debt, advance, due date or attribution' USING ERRCODE='23514';
  END IF;
 END IF;
 IF NEW.direct_expense_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM finance.direct_expenses d JOIN core.proposal_revisions r
  ON r.proposal_id=NEW.proposal_id AND r.version=NEW.version
  WHERE d.id=NEW.direct_expense_id AND d.description IS NOT DISTINCT FROM r.payload->>'description')
 THEN RAISE EXCEPTION 'Expense purpose approval snapshot mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER expense_purpose_snapshot AFTER INSERT ON core.confirmation_effects
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_expense_purpose_snapshot();
"""
