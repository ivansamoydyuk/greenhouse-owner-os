"""Independent R2 review: bind provenance and the complete confirmed effect.

Forward-only. Recover from a verified backup in a separate database and reconcile
all later originals, revisions and effects. Never discard confirmed audit history.
"""
from alembic import op

revision = '20261008_0024'
down_revision = '20261008_0023'
branch_labels = depends_on = None

def upgrade():
    op.execute(SQL)

def downgrade():
    raise RuntimeError('Forward-only evidence migration; restore separately and reconcile later evidence')

SQL = r"""
-- No ownership-transfer/grant workflow exists in this bounded release. Refuse
-- ambiguous prior self-grants for explicit reconciliation, never silently keep
-- or delete access or historical evidence during an upgrade.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM core.owner_entity_access a WHERE NOT EXISTS(
 SELECT 1 FROM core.reference_records r WHERE r.entity_id=a.entity_id AND r.kind='entity' AND r.created_by=a.actor_id))
 THEN RAISE EXCEPTION 'Owner access provenance requires explicit reconciliation before upgrade'; END IF;
END $$;
CREATE FUNCTION core.r2_access_provenance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM core.reference_records r WHERE r.entity_id=NEW.entity_id
 AND r.kind='entity' AND r.created_by=NEW.actor_id)
 THEN RAISE EXCEPTION 'Owner access requires original entity authority' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER owner_access_provenance BEFORE INSERT OR UPDATE ON core.owner_entity_access
 FOR EACH ROW EXECUTE FUNCTION core.r2_access_provenance();
ALTER TABLE core.extraction_runs ADD COLUMN lease_token uuid;
CREATE FUNCTION core.r2_job_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (NEW.id,NEW.entity_id,NEW.inbox_id,NEW.engine_version,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.entity_id,OLD.inbox_id,OLD.engine_version,OLD.created_at)
 THEN RAISE EXCEPTION 'Preparation source identity is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER job_identity BEFORE UPDATE OR DELETE ON core.processing_jobs
 FOR EACH ROW EXECUTE FUNCTION core.r2_job_identity();
CREATE FUNCTION core.r2_extraction_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM core.processing_jobs j JOIN core.inbox_items s ON s.id=j.inbox_id
 WHERE j.id=NEW.job_id AND j.entity_id=NEW.entity_id AND j.inbox_id=NEW.inbox_id
 AND s.fingerprint=NEW.input_hash AND j.state='RUNNING' AND j.attempts=NEW.attempt
 AND j.lease_token=NEW.lease_token AND NEW.lease_token IS NOT NULL)
 THEN RAISE EXCEPTION 'Extraction source/job/attempt lease mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER extraction_source BEFORE INSERT ON core.extraction_runs
 FOR EACH ROW EXECUTE FUNCTION core.r2_extraction_source();
CREATE FUNCTION core.r2_revision_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.extraction_run_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM core.proposed_actions p JOIN core.extraction_runs r ON r.inbox_id=p.inbox_id AND r.entity_id=p.entity_id
 WHERE p.id=NEW.proposal_id AND p.entity_id=NEW.entity_id AND r.id=NEW.extraction_run_id)
 THEN RAISE EXCEPTION 'Proposal extraction belongs to another original' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER revision_source BEFORE INSERT ON core.proposal_revisions
 FOR EACH ROW EXECUTE FUNCTION core.r2_revision_source();

CREATE FUNCTION core.r2_complete_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE f jsonb; k text; original uuid; s harvest.depot_settlements; expected jsonb; actual jsonb;
BEGIN
 SELECT r.payload,p.action_type,p.inbox_id INTO f,k,original
 FROM core.proposal_revisions r JOIN core.proposed_actions p ON p.id=r.proposal_id
 WHERE r.proposal_id=NEW.proposal_id AND r.version=NEW.version;
 IF num_nonnulls(NEW.obligation_id,NEW.payment_id,NEW.inventory_id,NEW.settlement_id,
 NEW.owner_task_id,NEW.worker_task_id,NEW.direct_expense_id,NEW.invoice_match_id)<>1
 OR NOT EXISTS(SELECT 1 FROM core.proposal_actions a WHERE a.proposal_id=NEW.proposal_id
 AND a.version=NEW.version AND a.actor_id=NEW.actor_id AND a.action='CONFIRM'
 AND a.snapshot->>'preview_digest'=NEW.preview_digest AND a.snapshot->'payload'=f)
 THEN RAISE EXCEPTION 'Exactly one effect and exact authority snapshot required' USING ERRCODE='23514'; END IF;
 IF k='PAYABLE' THEN
  IF NOT EXISTS(SELECT 1 FROM finance.obligations o WHERE o.id=NEW.obligation_id
   AND o.due_date IS NOT DISTINCT FROM (f->>'due_date')::date
   AND o.description IS NOT DISTINCT FROM f->>'description')
  THEN RAISE EXCEPTION 'Payable detail snapshot mismatch' USING ERRCODE='23514'; END IF;
  SELECT coalesce(jsonb_agg(jsonb_build_array(x->>'work_area_id',(x->>'amount')::numeric) ORDER BY x->>'work_area_id'),'[]') INTO expected
   FROM jsonb_array_elements(coalesce(f->'attributions','[]')) x;
  SELECT coalesce(jsonb_agg(jsonb_build_array(work_area_id::text,amount) ORDER BY work_area_id::text),'[]') INTO actual
   FROM finance.obligation_attributions WHERE obligation_id=NEW.obligation_id;
  IF expected IS DISTINCT FROM actual THEN RAISE EXCEPTION 'Payable attribution snapshot mismatch' USING ERRCODE='23514'; END IF;
 ELSIF k IN ('STOCK_RECEIPT','STOCK_APPLICATION') THEN
  IF NOT EXISTS(SELECT 1 FROM inventory.events v WHERE v.id=NEW.inventory_id
  AND v.source='owner:r2-action' AND v.locator=NEW.proposal_id::text
  AND v.evidence->>'source_document'=original::text
  AND v.duplicate_reviewed=coalesce((f->>'separate_event_reviewed')::boolean,false))
  THEN RAISE EXCEPTION 'Stock original/duplicate-review snapshot mismatch' USING ERRCODE='23514'; END IF;
 ELSIF k='DEPOT_RECEIPT' THEN
  SELECT * INTO s FROM harvest.depot_settlements WHERE id=NEW.settlement_id;
  IF s.original_greenhouse IS DISTINCT FROM f->>'original_greenhouse'
  OR s.link_evidence IS DISTINCT FROM f->>'link_evidence'
  OR s.payment_id IS NOT NULL OR s.application_ids<>'[]'::jsonb
  OR s.total_amount IS DISTINCT FROM (SELECT sum(coalesce((x->>'confirmed_amount')::numeric,
       round((x->>'net_weight_kg')::numeric*(x->>'unit_price')::numeric,2))) FROM jsonb_array_elements(f->'lines') x)
  OR NOT EXISTS(SELECT 1 FROM harvest.evidence e JOIN core.inbox_items i ON i.id=original
      WHERE e.id=s.evidence_id AND e.entity_id=i.entity_id AND e.fingerprint=i.fingerprint
      AND e.source='owner:r2-action' AND e.source_reference=NEW.proposal_id::text)
  THEN RAISE EXCEPTION 'Depot original/amount/effect snapshot mismatch' USING ERRCODE='23514'; END IF;
  SELECT coalesce(jsonb_agg(x ORDER BY x),'[]') INTO expected FROM jsonb_array_elements(coalesce(f->'batch_ids','[]')) x;
  SELECT coalesce(jsonb_agg(to_jsonb(batch_id::text) ORDER BY batch_id::text),'[]') INTO actual
   FROM harvest.settlement_batches WHERE settlement_id=s.id;
  IF expected IS DISTINCT FROM actual THEN RAISE EXCEPTION 'Depot batch snapshot mismatch' USING ERRCODE='23514'; END IF;
  -- Include each line's complete allocation signature in the multiset: swapping
  -- allocations between otherwise different lines cannot preserve authority.
  WITH signatures AS (
   SELECT jsonb_build_array(x.l->>'original_quality_name',(x.l->>'net_weight_kg')::numeric,
    (x.l->>'unit_price')::numeric,(x.l->>'stated_amount')::numeric,(x.l->>'count')::int,x.l->>'source_line',
    f->'qualities'->>(x.n::int-1),round((x.l->>'net_weight_kg')::numeric*(x.l->>'unit_price')::numeric,2),
    coalesce((x.l->>'confirmed_amount')::numeric,round((x.l->>'net_weight_kg')::numeric*(x.l->>'unit_price')::numeric,2)),
    (SELECT coalesce(jsonb_agg(jsonb_build_array(a->>'work_area_id',(a->>'net_weight_kg')::numeric,
      (a->>'amount')::numeric,(a->>'confirmed_count')::int) ORDER BY a->>'work_area_id'),'[]')
      FROM jsonb_array_elements(coalesce(x.l->'allocations','[]')) a)) sig
   FROM jsonb_array_elements(f->'lines') WITH ORDINALITY x(l,n))
  SELECT jsonb_agg(sig ORDER BY sig) INTO expected FROM signatures;
  WITH signatures AS (
   SELECT jsonb_build_array(l.original_quality_name,l.net_weight_kg,l.unit_price,l.stated_amount,l.count,l.source_line,
    l.normalized_quality,l.calculated_amount,l.amount,
    (SELECT coalesce(jsonb_agg(jsonb_build_array(a.work_area_id::text,a.net_weight_kg,a.amount,a.confirmed_count)
     ORDER BY a.work_area_id::text),'[]') FROM harvest.line_allocations a WHERE a.line_id=l.id)) sig
   FROM harvest.settlement_lines l WHERE l.settlement_id=s.id)
  SELECT jsonb_agg(sig ORDER BY sig) INTO actual FROM signatures;
  IF expected IS DISTINCT FROM actual THEN RAISE EXCEPTION 'Depot line/allocation snapshot mismatch' USING ERRCODE='23514'; END IF;
 ELSIF k='INVOICE_MATCH' AND f->>'finance_obligation_id' IS NOT NULL THEN
  IF NOT EXISTS(SELECT 1 FROM finance.obligations o WHERE o.id=(f->>'finance_obligation_id')::uuid
  AND o.entity_id=NEW.entity_id AND o.status='posted' AND o.direction='payable')
  THEN RAISE EXCEPTION 'Invoice requires a posted payable' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER complete_confirmation_snapshot AFTER INSERT ON core.confirmation_effects
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_complete_snapshot();

CREATE FUNCTION harvest.r2_review_authority() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE f jsonb; c core.confirmation_effects;
BEGIN
 SELECT * INTO c FROM core.confirmation_effects WHERE proposal_id=NEW.proposal_id;
 SELECT payload INTO f FROM core.proposal_revisions WHERE proposal_id=c.proposal_id AND version=c.version;
 IF c.settlement_id IS NULL OR c.entity_id IS DISTINCT FROM NEW.entity_id
 THEN RAISE EXCEPTION 'Quality review requires committed receipt authority' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='receipt_quality_reviews' THEN
  IF NEW.settlement_id IS DISTINCT FROM c.settlement_id OR NEW.reviewed_by IS DISTINCT FROM c.actor_id
  OR NEW.quality IS DISTINCT FROM f->'qualities'->>NEW.line_no
  THEN RAISE EXCEPTION 'Receipt quality review snapshot mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.accepted_by IS DISTINCT FROM c.actor_id OR NEW.depot_id::text IS DISTINCT FROM f->>'depot_id'
  OR f->>'remember_quality_mapping' IS DISTINCT FROM 'true'
  OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(f->'lines') WITH ORDINALITY x(l,n)
     WHERE replace(NEW.original_label,'i̇','i')=replace(lower(x.l->>'original_quality_name'),'i̇','i')
     AND NEW.quality=f->'qualities'->>(x.n::int-1))
  THEN RAISE EXCEPTION 'Depot default requires exact owner-approved mapping' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER reviewed_quality_authority AFTER INSERT ON harvest.receipt_quality_reviews
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION harvest.r2_review_authority();
CREATE CONSTRAINT TRIGGER reviewed_mapping_authority AFTER INSERT ON harvest.depot_quality_mappings
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION harvest.r2_review_authority();

-- Distinct individually approved semantic slots may share their parent's full
-- raw text. Keep the legacy duplicate guard for every other event pair.
DO $$ DECLARE f text; needle text; replacement text; BEGIN
 SELECT pg_get_functiondef('inventory.guard_event()'::regprocedure) INTO f;
 needle='ev.kind=NEW.kind AND (ev.event_date';
 replacement='ev.kind=NEW.kind AND NOT (NEW.source=''owner:r2-action'' AND ev.source=''owner:r2-action'' AND EXISTS(SELECT 1 FROM core.proposed_actions a JOIN core.proposed_actions b ON b.inbox_id=a.inbox_id AND b.entity_id=a.entity_id WHERE a.id::text=NEW.locator AND b.id::text=ev.locator AND a.id<>b.id AND a.action_type=b.action_type)) AND (ev.event_date';
 IF position(needle IN f)=0 THEN RAISE EXCEPTION 'Unexpected Inventory guard baseline'; END IF;
 EXECUTE replace(f,needle,replacement);
END $$;
CREATE OR REPLACE FUNCTION inventory.r2_source_reservation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v inventory.events;
BEGIN
 SELECT * INTO v FROM inventory.events WHERE id=NEW.id;
 IF v.status<>'posted' THEN RETURN NEW; END IF;
 IF EXISTS(SELECT 1 FROM core.inbox_items i WHERE i.id::text=v.evidence->>'source_document' AND i.source='owner:r2')
 AND v.source<>'owner:r2-action'
 THEN RAISE EXCEPTION 'R2 original requires its individual action adapter' USING ERRCODE='23514'; END IF;
 IF v.source='owner:r2-action' AND NOT EXISTS(
 SELECT 1 FROM core.confirmation_effects c JOIN core.proposed_actions p ON p.id=c.proposal_id
 WHERE c.inventory_id=v.id AND p.id::text=v.locator AND p.inbox_id::text=v.evidence->>'source_document'
 AND p.entity_id=v.entity_id AND core.r2_owner(v.entity_id,c.actor_id))
 THEN RAISE EXCEPTION 'Stock source requires owner approval' USING ERRCODE='23514'; END IF;
 IF v.kind='receipt' AND EXISTS(
 SELECT 1 FROM core.inbox_items incoming JOIN core.inbox_items original ON original.entity_id=incoming.entity_id AND original.fingerprint=incoming.fingerprint
 JOIN inventory.events other ON other.entity_id=original.entity_id AND other.evidence->>'source_document'=original.id::text
 WHERE incoming.entity_id=v.entity_id AND incoming.id::text=v.evidence->>'source_document'
 AND other.id<>v.id AND other.kind='receipt' AND other.status='posted'
 AND ((incoming.file_bytes IS NOT NULL AND original.file_bytes IS NOT NULL AND incoming.id<>original.id)
 OR (incoming.id=original.id AND (v.source<>'owner:r2-action' OR other.source<>'owner:r2-action'))))
 THEN RAISE EXCEPTION 'Original stock source already received' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
"""
