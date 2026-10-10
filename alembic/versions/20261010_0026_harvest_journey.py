"""Append-only owner link/evidence/estimated allocation history; no ledger change."""
from alembic import op
revision='20261010_0026'
down_revision='20261010_0025'
branch_labels=depends_on=None

def upgrade():op.execute(SQL)
def downgrade():raise RuntimeError('Forward-only evidence: restore separately and reconcile subsequent history')

SQL=r'''
CREATE TABLE harvest.journey_revisions (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id),
 settlement_id uuid NOT NULL REFERENCES harvest.depot_settlements(id),
 version integer NOT NULL CHECK(version>0), kind text NOT NULL CHECK(kind IN ('LINKS','ESTIMATE','EVIDENCE')),
 reason text NOT NULL CHECK(length(trim(reason))>0), created_by uuid NOT NULL REFERENCES core.actors(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), created_tx bigint NOT NULL DEFAULT txid_current(),
 link_revision_id uuid REFERENCES harvest.journey_revisions(id),
 total_basis_count numeric(18,3), basis_description text, algorithm text,
 UNIQUE(settlement_id,version),
 CHECK((kind='ESTIMATE' AND total_basis_count IS NOT NULL AND basis_description IS NOT NULL AND algorithm IS NOT NULL AND total_basis_count>0 AND length(trim(basis_description))>0 AND algorithm='largest-remainder-per-line-v1') OR
       (kind<>'ESTIMATE' AND total_basis_count IS NULL AND basis_description IS NULL AND algorithm IS NULL AND link_revision_id IS NULL))
);
CREATE TABLE harvest.journey_links (
 revision_id uuid NOT NULL REFERENCES harvest.journey_revisions(id),
 batch_id uuid NOT NULL REFERENCES harvest.batches(id), PRIMARY KEY(revision_id,batch_id)
);
CREATE TABLE harvest.estimate_basis (
 revision_id uuid NOT NULL REFERENCES harvest.journey_revisions(id),
 work_area_id uuid NOT NULL REFERENCES core.work_areas(id),
 basis_count numeric(18,3) NOT NULL CHECK(basis_count>0), PRIMARY KEY(revision_id,work_area_id)
);
CREATE TABLE harvest.estimate_lines (
 id uuid PRIMARY KEY, revision_id uuid NOT NULL REFERENCES harvest.journey_revisions(id),
 line_id uuid NOT NULL REFERENCES harvest.settlement_lines(id),
 work_area_id uuid REFERENCES core.work_areas(id),
 net_weight_kg numeric(20,3) NOT NULL CHECK(net_weight_kg>=0),
 amount numeric(20,2) NOT NULL CHECK(amount>=0),
 UNIQUE NULLS NOT DISTINCT(revision_id,line_id,work_area_id)
);
CREATE TABLE harvest.journey_evidence (
 revision_id uuid PRIMARY KEY REFERENCES harvest.journey_revisions(id),
 evidence_id uuid NOT NULL REFERENCES harvest.evidence(id)
);
CREATE FUNCTION harvest.current_batch_links(sid uuid) RETURNS TABLE(batch_id uuid)
 LANGUAGE sql STABLE AS $$
 WITH latest AS (SELECT id FROM harvest.journey_revisions WHERE settlement_id=sid AND kind='LINKS' ORDER BY version DESC LIMIT 1)
 SELECT j.batch_id FROM harvest.journey_links j WHERE j.revision_id IN (SELECT id FROM latest)
 UNION ALL SELECT b.batch_id FROM harvest.settlement_batches b WHERE b.settlement_id=sid AND NOT EXISTS(SELECT 1 FROM latest)
 $$;
CREATE FUNCTION harvest.journey_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s harvest.depot_settlements; latest uuid; BEGIN
 NEW.created_tx:=txid_current();
 SELECT * INTO s FROM harvest.depot_settlements WHERE id=NEW.settlement_id FOR UPDATE;
 IF s.entity_id IS DISTINCT FROM NEW.entity_id OR s.status='reversed' OR NOT core.r2_owner(NEW.entity_id,NEW.created_by)
 THEN RAISE EXCEPTION 'Owner and active receipt scope required' USING ERRCODE='23514'; END IF;
 IF NEW.version<>(SELECT coalesce(max(version),0)+1 FROM harvest.journey_revisions WHERE settlement_id=s.id)
 THEN RAISE EXCEPTION 'Stale journey revision' USING ERRCODE='23514'; END IF;
 IF NEW.kind='ESTIMATE' THEN
   IF s.status<>'posted' THEN RAISE EXCEPTION 'Estimate requires immutable posted receipt lines' USING ERRCODE='23514'; END IF;
   SELECT id INTO latest FROM harvest.journey_revisions WHERE settlement_id=s.id AND kind='LINKS' ORDER BY version DESC LIMIT 1;
   IF NEW.link_revision_id IS DISTINCT FROM latest THEN RAISE EXCEPTION 'Estimate link basis changed' USING ERRCODE='23514'; END IF;
   IF EXISTS(SELECT 1 FROM harvest.line_allocations a JOIN harvest.settlement_lines l ON l.id=a.line_id WHERE l.settlement_id=s.id)
   THEN RAISE EXCEPTION 'Exact attribution needs existing guarded correction, not estimate overlay' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER journey_revision_guard BEFORE INSERT ON harvest.journey_revisions FOR EACH ROW EXECUTE FUNCTION harvest.journey_revision_guard();
CREATE TRIGGER journey_immutable BEFORE UPDATE OR DELETE ON harvest.journey_revisions FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TRIGGER journey_immutable BEFORE UPDATE OR DELETE ON harvest.journey_links FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TRIGGER journey_immutable BEFORE UPDATE OR DELETE ON harvest.estimate_basis FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TRIGGER journey_immutable BEFORE UPDATE OR DELETE ON harvest.estimate_lines FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TRIGGER journey_immutable BEFORE UPDATE OR DELETE ON harvest.journey_evidence FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE FUNCTION harvest.journey_child_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r harvest.journey_revisions; e uuid; parent uuid; BEGIN
 SELECT * INTO r FROM harvest.journey_revisions WHERE id=NEW.revision_id;
 IF r.created_tx IS DISTINCT FROM txid_current() THEN RAISE EXCEPTION 'Completed revision children immutable' USING ERRCODE='23514'; END IF;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Journey revision required' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='journey_links' THEN
   IF r.kind<>'LINKS' THEN RAISE EXCEPTION 'Link revision required' USING ERRCODE='23514'; END IF;
   SELECT entity_id INTO e FROM harvest.batches WHERE id=NEW.batch_id FOR UPDATE;
   IF e IS DISTINCT FROM r.entity_id THEN RAISE EXCEPTION 'Batch entity mismatch' USING ERRCODE='23514'; END IF;
   IF EXISTS(SELECT 1 FROM harvest.depot_settlements s CROSS JOIN LATERAL harvest.current_batch_links(s.id) l
     WHERE l.batch_id=NEW.batch_id AND s.id<>r.settlement_id AND s.status<>'reversed')
   THEN RAISE EXCEPTION 'Batch already has active receipt' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='journey_evidence' THEN
   IF r.kind<>'EVIDENCE' OR EXISTS(SELECT 1 FROM harvest.worker_reports WHERE evidence_id=NEW.evidence_id) OR NOT EXISTS(SELECT 1 FROM harvest.evidence WHERE id=NEW.evidence_id AND entity_id=r.entity_id AND source<>'owner_form:v1:field_harvest')
   THEN RAISE EXCEPTION 'Independent depot evidence required' USING ERRCODE='23514'; END IF;
 ELSE
   IF r.kind<>'ESTIMATE' THEN RAISE EXCEPTION 'Estimate revision required' USING ERRCODE='23514'; END IF;
   IF NEW.work_area_id IS NOT NULL THEN
     SELECT entity_id,parent_id INTO e,parent FROM core.work_areas WHERE id=NEW.work_area_id;
     IF e IS DISTINCT FROM r.entity_id THEN RAISE EXCEPTION 'Estimate area scope mismatch' USING ERRCODE='23514'; END IF;
   END IF;
   IF TG_TABLE_NAME='estimate_basis' AND EXISTS(SELECT 1 FROM harvest.estimate_basis b JOIN core.work_areas a ON a.id=b.work_area_id
     WHERE b.revision_id=r.id AND (a.parent_id=NEW.work_area_id OR a.id=parent))
   THEN RAISE EXCEPTION 'Overlapping area basis' USING ERRCODE='23514'; END IF;
   IF TG_TABLE_NAME='estimate_lines' THEN
     IF NOT EXISTS(SELECT 1 FROM harvest.settlement_lines WHERE id=NEW.line_id AND settlement_id=r.settlement_id)
     THEN RAISE EXCEPTION 'Estimate line scope mismatch' USING ERRCODE='23514'; END IF;
   END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER journey_child BEFORE INSERT ON harvest.journey_links FOR EACH ROW EXECUTE FUNCTION harvest.journey_child_guard();
CREATE TRIGGER journey_child BEFORE INSERT ON harvest.journey_evidence FOR EACH ROW EXECUTE FUNCTION harvest.journey_child_guard();
CREATE TRIGGER journey_child BEFORE INSERT ON harvest.estimate_basis FOR EACH ROW EXECUTE FUNCTION harvest.journey_child_guard();
CREATE TRIGGER journey_child BEFORE INSERT ON harvest.estimate_lines FOR EACH ROW EXECUTE FUNCTION harvest.journey_child_guard();
CREATE FUNCTION harvest.expected_estimate(rid uuid) RETURNS TABLE(line_id uuid,work_area_id uuid,net_weight_kg numeric,amount numeric)
 LANGUAGE sql STABLE AS $$
 WITH revision AS (SELECT * FROM harvest.journey_revisions WHERE id=rid),
 weights AS (SELECT b.work_area_id,b.basis_count FROM harvest.estimate_basis b WHERE b.revision_id=rid
 UNION ALL SELECT NULL::uuid,r.total_basis_count-(SELECT coalesce(sum(basis_count),0) FROM harvest.estimate_basis WHERE revision_id=rid)
 FROM revision r WHERE r.total_basis_count>(SELECT coalesce(sum(basis_count),0) FROM harvest.estimate_basis WHERE revision_id=rid)),
 raw AS (SELECT l.id line_id,w.work_area_id,l.net_weight_kg*1000 kg_units,l.amount*100 money_units,
 l.net_weight_kg*1000*w.basis_count*1000 kg_numerator,l.amount*100*w.basis_count*1000 money_numerator,r.total_basis_count*1000 denominator
 FROM revision r JOIN harvest.settlement_lines l ON l.settlement_id=r.settlement_id CROSS JOIN weights w),
 ranked AS (SELECT *,div(kg_numerator,denominator) kg_floor,div(money_numerator,denominator) money_floor,
 row_number() OVER(PARTITION BY line_id ORDER BY mod(kg_numerator,denominator) DESC,work_area_id NULLS LAST) kg_rank,
 row_number() OVER(PARTITION BY line_id ORDER BY mod(money_numerator,denominator) DESC,work_area_id NULLS LAST) money_rank,
 sum(div(kg_numerator,denominator)) OVER(PARTITION BY line_id) kg_sum,sum(div(money_numerator,denominator)) OVER(PARTITION BY line_id) money_sum FROM raw)
 SELECT line_id,work_area_id,(kg_floor+CASE WHEN kg_rank<=kg_units-kg_sum THEN 1 ELSE 0 END)/1000,
 (money_floor+CASE WHEN money_rank<=money_units-money_sum THEN 1 ELSE 0 END)/100 FROM ranked
 $$;
CREATE FUNCTION harvest.journey_complete_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r harvest.journey_revisions; n int; basis numeric; BEGIN
 SELECT * INTO r FROM harvest.journey_revisions WHERE id=NEW.id;
 IF r.kind='ESTIMATE' THEN
   IF r.total_basis_count IS NULL OR r.total_basis_count<=0 OR r.algorithm IS DISTINCT FROM 'largest-remainder-per-line-v1' OR r.basis_description IS NULL OR length(trim(r.basis_description))=0 THEN RAISE EXCEPTION 'Explicit estimate basis required' USING ERRCODE='23514'; END IF;
   SELECT count(*),coalesce(sum(basis_count),0) INTO n,basis FROM harvest.estimate_basis WHERE revision_id=r.id;
   IF n=0 OR basis>r.total_basis_count THEN RAISE EXCEPTION 'Incomplete estimate basis' USING ERRCODE='23514'; END IF;
   IF EXISTS(SELECT 1 FROM harvest.settlement_lines l WHERE l.settlement_id=r.settlement_id AND (
      (SELECT count(*) FROM harvest.estimate_lines x WHERE x.revision_id=r.id AND x.line_id=l.id)<>n+CASE WHEN basis<r.total_basis_count THEN 1 ELSE 0 END OR
      (SELECT coalesce(sum(net_weight_kg),0) FROM harvest.estimate_lines x WHERE x.revision_id=r.id AND x.line_id=l.id)<>l.net_weight_kg OR
      (SELECT coalesce(sum(amount),0) FROM harvest.estimate_lines x WHERE x.revision_id=r.id AND x.line_id=l.id)<>l.amount))
   OR EXISTS(SELECT 1 FROM harvest.estimate_lines x WHERE x.revision_id=r.id AND x.work_area_id IS NOT NULL AND NOT EXISTS(
     SELECT 1 FROM harvest.estimate_basis b WHERE b.revision_id=r.id AND b.work_area_id=x.work_area_id))
   THEN RAISE EXCEPTION 'Estimate must conserve each original line and explicit area basis' USING ERRCODE='23514'; END IF;
   IF EXISTS(SELECT 1 FROM harvest.expected_estimate(r.id) expected WHERE NOT EXISTS(SELECT 1 FROM harvest.estimate_lines actual WHERE actual.revision_id=r.id AND actual.line_id=expected.line_id AND actual.work_area_id IS NOT DISTINCT FROM expected.work_area_id AND actual.net_weight_kg=expected.net_weight_kg AND actual.amount=expected.amount)) THEN RAISE EXCEPTION 'Estimate differs from deterministic per-line largest remainder' USING ERRCODE='23514'; END IF;
 ELSIF r.kind='EVIDENCE' AND NOT EXISTS(SELECT 1 FROM harvest.journey_evidence WHERE revision_id=r.id)
 THEN RAISE EXCEPTION 'Evidence attachment required' USING ERRCODE='23514'; END IF;
 RETURN NULL; END $$;
CREATE CONSTRAINT TRIGGER journey_complete AFTER INSERT ON harvest.journey_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION harvest.journey_complete_guard();
-- The existing legacy/Capture creation path must also honor late-link ownership.
CREATE FUNCTION harvest.journey_original_link_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 PERFORM id FROM harvest.batches WHERE id=NEW.batch_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM harvest.depot_settlements s CROSS JOIN LATERAL harvest.current_batch_links(s.id) l
   WHERE s.id<>NEW.settlement_id AND s.status<>'reversed' AND l.batch_id=NEW.batch_id)
 THEN RAISE EXCEPTION 'Batch already has active receipt' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER journey_original_link BEFORE INSERT ON harvest.settlement_batches FOR EACH ROW EXECUTE FUNCTION harvest.journey_original_link_guard();
CREATE OR REPLACE FUNCTION harvest.guard_child() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s harvest.depot_settlements%ROWTYPE; l harvest.settlement_lines%ROWTYPE; BEGIN
  IF TG_TABLE_NAME='settlement_batches' THEN
    SELECT * INTO s FROM harvest.depot_settlements WHERE id=NEW.settlement_id FOR UPDATE;
    PERFORM id FROM harvest.batches WHERE id=NEW.batch_id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM harvest.depot_settlements ds CROSS JOIN LATERAL harvest.current_batch_links(ds.id) sb WHERE sb.batch_id=NEW.batch_id AND ds.id<>NEW.settlement_id AND ds.status<>'reversed') THEN RAISE EXCEPTION 'batch already has active receipt'; END IF;
    IF NOT EXISTS(SELECT 1 FROM harvest.batches WHERE id=NEW.batch_id AND entity_id=s.entity_id) OR s.link_evidence IS NULL THEN RAISE EXCEPTION 'explicit batch evidence required'; END IF;
  ELSIF TG_TABLE_NAME='settlement_lines' THEN SELECT * INTO s FROM harvest.depot_settlements WHERE id=NEW.settlement_id FOR UPDATE;
  ELSE
    SELECT * INTO l FROM harvest.settlement_lines WHERE id=NEW.line_id;
    SELECT * INTO s FROM harvest.depot_settlements WHERE id=l.settlement_id FOR UPDATE;
    IF NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=NEW.work_area_id AND entity_id=s.entity_id) THEN RAISE EXCEPTION 'allocation scope mismatch'; END IF;
    IF (SELECT coalesce(sum(amount),0) FROM harvest.line_allocations WHERE line_id=l.id)+NEW.amount>l.amount OR (SELECT coalesce(sum(net_weight_kg),0) FROM harvest.line_allocations WHERE line_id=l.id)+NEW.net_weight_kg>l.net_weight_kg OR (l.count IS NOT NULL AND (SELECT coalesce(sum(confirmed_count),0) FROM harvest.line_allocations WHERE line_id=l.id)+coalesce(NEW.confirmed_count,0)>l.count) THEN RAISE EXCEPTION 'allocation exceeds source'; END IF;
  END IF;
  IF s.status<>'draft' THEN RAISE EXCEPTION 'posted receipt children immutable'; END IF;
  RETURN NEW; END $$;

CREATE FUNCTION harvest.journey_receipt_snapshot_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE f jsonb; s harvest.depot_settlements; BEGIN
 IF NEW.settlement_id IS NOT NULL THEN
   SELECT payload INTO f FROM core.proposal_revisions WHERE proposal_id=NEW.proposal_id AND version=NEW.version;
   SELECT * INTO s FROM harvest.depot_settlements WHERE id=NEW.settlement_id;
   IF s.due_date IS DISTINCT FROM (f->>'due_date')::date OR s.document_number IS DISTINCT FROM f->>'document_number'
   THEN RAISE EXCEPTION 'Depot due date/document identity differs from confirmed snapshot' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL; END $$;
CREATE CONSTRAINT TRIGGER journey_receipt_snapshot AFTER INSERT ON core.confirmation_effects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION harvest.journey_receipt_snapshot_guard();
'''
