"""Append-only fertilizer physical execution scope; no historical posting."""
from alembic import op
revision="20261011_0031"
down_revision="20261011_0030"
branch_labels=depends_on=None
def upgrade():
    op.execute(r"""
ALTER TABLE inventory.fertilizer_actuals DROP CONSTRAINT fertilizer_actuals_plan_id_key;
ALTER TABLE inventory.fertilizer_actuals ALTER COLUMN application_id DROP NOT NULL;
CREATE TABLE inventory.fertilizer_actual_lines(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), actual_id uuid NOT NULL REFERENCES inventory.fertilizer_actuals(id),
 entity_id uuid NOT NULL REFERENCES core.entities(id),plan_id uuid NOT NULL REFERENCES inventory.fertilizer_plans(id),
 revision_id uuid NOT NULL REFERENCES inventory.fertilizer_revisions(id),physical_execution_id uuid NOT NULL,
 card_id uuid NOT NULL,component_id uuid NOT NULL,report_id uuid REFERENCES inventory.fertilizer_reports(id),
 product_id uuid NOT NULL REFERENCES inventory.products(id),location_id uuid NOT NULL REFERENCES inventory.locations(id),
 work_area_id uuid NOT NULL REFERENCES core.work_areas(id),unit text NOT NULL CHECK(unit IN ('kg','L')),
 quantity numeric NOT NULL CHECK(quantity>=0 AND quantity<1e18 AND scale(quantity)<=12),
 remainder_decision text NOT NULL CHECK(remainder_decision IN ('KEEP_OPEN','CLOSE')),
 inventory_line_index integer CHECK(inventory_line_index>=0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(plan_id,physical_execution_id,card_id,component_id),UNIQUE(plan_id,report_id,card_id,component_id),
 FOREIGN KEY(revision_id,card_id) REFERENCES inventory.fertilizer_cards(revision_id,card_id));
-- Populate identity/projection links only. No original, receipt or movement changes.
INSERT INTO inventory.fertilizer_actual_lines(actual_id,entity_id,plan_id,revision_id,physical_execution_id,card_id,component_id,report_id,product_id,location_id,work_area_id,unit,quantity,remainder_decision,inventory_line_index,created_at)
 SELECT a.id,a.entity_id,a.plan_id,a.revision_id,(ln->>'report_id')::uuid,(ln->>'card_id')::uuid,(ln->>'component_id')::uuid,(ln->>'report_id')::uuid,
 (comp->>'product_id')::uuid,(comp->>'location_id')::uuid,c.work_area_id,comp->>'unit',replace(ln->>'quantity',',','.')::numeric,'KEEP_OPEN',
 CASE WHEN replace(ln->>'quantity',',','.')::numeric>0 THEN (SELECT count(*)::int FROM jsonb_array_elements(a.input->'lines') WITH ORDINALITY q(v,n) WHERE n<z.n AND replace(v->>'quantity',',','.')::numeric>0) ELSE NULL END,a.created_at
 FROM inventory.fertilizer_actuals a CROSS JOIN LATERAL jsonb_array_elements(a.input->'lines') WITH ORDINALITY z(ln,n)
 JOIN inventory.fertilizer_cards c ON c.revision_id=a.revision_id AND c.card_id=(ln->>'card_id')::uuid
 CROSS JOIN LATERAL jsonb_array_elements(c.snapshot->'components') comp WHERE comp->>'component_id'=ln->>'component_id';
CREATE TABLE inventory.fertilizer_resolutions(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities(id),plan_id uuid NOT NULL REFERENCES inventory.fertilizer_plans(id),
 source_id uuid NOT NULL,kind text NOT NULL CHECK(kind IN ('EVIDENCE_ONLY','CONFLICT','UNRESOLVED','CORRECTION','REOPEN_SCOPE')),
 report_id uuid REFERENCES inventory.fertilizer_reports(id),actual_id uuid REFERENCES inventory.fertilizer_actuals(id),
 card_id uuid,component_id uuid,source_snapshot jsonb NOT NULL,created_by uuid NOT NULL REFERENCES core.actors(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(entity_id,source_id));
CREATE TRIGGER fertilizer_immutable BEFORE UPDATE OR DELETE ON inventory.fertilizer_actual_lines FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_immutable();
CREATE TRIGGER fertilizer_immutable BEFORE UPDATE OR DELETE ON inventory.fertilizer_resolutions FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_immutable();
CREATE OR REPLACE FUNCTION inventory.fertilizer_version(p uuid) RETURNS integer LANGUAGE sql STABLE AS $$
 SELECT (SELECT count(*) FROM inventory.fertilizer_revisions WHERE plan_id=p)+(SELECT count(*) FROM inventory.fertilizer_dispatches WHERE plan_id=p)+(SELECT count(*) FROM inventory.fertilizer_reports WHERE plan_id=p)+(SELECT count(*) FROM inventory.fertilizer_actuals WHERE plan_id=p)+(SELECT count(*) FROM inventory.fertilizer_resolutions WHERE plan_id=p)
$$;
CREATE OR REPLACE FUNCTION inventory.fertilizer_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r inventory.fertilizer_revisions; p jsonb; c jsonb; ln jsonb; basis inventory.fertilizer_reference_versions; wa core.work_areas; w workers.people; q numeric; candidate numeric; prev inventory.fertilizer_reference_versions; cid uuid; raw jsonb;
BEGIN
 IF TG_TABLE_NAME='fertilizer_reports' THEN
  PERFORM inventory.fertilizer_lock(NEW.entity_id,NEW.plan_id);
 ELSE
  IF NOT core.r2_owner(NEW.entity_id,NEW.created_by) THEN RAISE EXCEPTION 'Confirmed entity owner required' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME NOT IN ('fertilizer_plans','fertilizer_reference_versions') THEN PERFORM inventory.fertilizer_lock(NEW.entity_id,NEW.plan_id); ELSE PERFORM inventory.fertilizer_lock(NEW.entity_id,NULL); END IF;
 END IF;
 IF TG_TABLE_NAME='fertilizer_plans' THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='fertilizer_reference_versions' THEN
  p=NEW.source_snapshot;
  PERFORM inventory.fertilizer_typed(p,ARRAY['client_reference_id','captured_at','work_area_id','area_da','default_worker_id','canonical_key','verification','effective_from','replaces_id','confirmed'],ARRAY['client_reference_id','captured_at','work_area_id','verification','effective_from']);
  PERFORM inventory.fertilizer_stamp(p->'captured_at');
  SELECT * INTO wa FROM core.work_areas WHERE id=NEW.work_area_id AND entity_id=NEW.entity_id;
  IF wa.id IS NULL OR p->'confirmed' IS DISTINCT FROM 'true'::jsonb OR (p->>'client_reference_id')::uuid IS DISTINCT FROM NEW.id
  OR (p->>'work_area_id')::uuid IS DISTINCT FROM NEW.work_area_id OR (p->>'default_worker_id')::uuid IS DISTINCT FROM NEW.default_worker_id
  OR p->>'verification' IS DISTINCT FROM NEW.verification OR p->>'canonical_key' IS DISTINCT FROM NEW.canonical_key
  OR (p->>'effective_from')::date IS DISTINCT FROM NEW.effective_from OR (p->>'replaces_id')::uuid IS DISTINCT FROM NEW.replaces_id
  THEN RAISE EXCEPTION 'Verified area identity/source mismatch' USING ERRCODE='23514'; END IF;
  PERFORM (p->>'captured_at')::timestamptz;
  IF NEW.area_da IS NOT NULL AND inventory.fertilizer_numeric(p->'area_da') IS DISTINCT FROM NEW.area_da OR NEW.area_da IS NULL AND p->'area_da'<>'null'::jsonb THEN RAISE EXCEPTION 'Area source mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.default_worker_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workers.people WHERE id=NEW.default_worker_id AND entity_id=NEW.entity_id AND active AND language='fa') THEN RAISE EXCEPTION 'Worker reference scope mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.canonical_key IS NOT NULL THEN
   candidate=CASE NEW.canonical_key WHEN 'Yeşilöz:Part 1' THEN 3 WHEN 'Yeşilöz:Part 2-1' THEN 5 WHEN 'Yeşilöz:Part 2-2' THEN 5 WHEN 'Yeşilöz:Part 3-1' THEN 3.5 WHEN 'Yeşilöz:Part 3-2' THEN 3.5
    WHEN 'İmamlı:Part 1' THEN 5 WHEN 'İmamlı:Part 2' THEN 5 WHEN 'İmamlı:Part 3' THEN 5 WHEN 'İmamlı:Part 4' THEN 5 WHEN 'Özvadi:Gazipaşa 1' THEN 7 WHEN 'Özvadi:Gazipaşa 2' THEN 7 WHEN 'Özvadi:Alanya 1 / upper' THEN 6 WHEN 'Özvadi:Alanya 2 / middle' THEN 6 WHEN 'Özvadi:Alanya 3 / lower' THEN 4 END;
   IF candidate IS NOT NULL AND NEW.area_da IS DISTINCT FROM candidate OR candidate IS NULL AND (NEW.canonical_key NOT IN ('Yeşilöz:worker','İmamlı:worker','Özvadi:worker') OR NEW.default_worker_id IS NULL) THEN RAISE EXCEPTION 'Exact canonical candidate required' USING ERRCODE='23514'; END IF;
  END IF;
  IF NEW.replaces_id IS NOT NULL THEN SELECT * INTO prev FROM inventory.fertilizer_reference_versions WHERE id=NEW.replaces_id;
   IF (prev.entity_id,prev.work_area_id) IS DISTINCT FROM (NEW.entity_id,NEW.work_area_id) OR NEW.effective_from<=prev.effective_from THEN RAISE EXCEPTION 'Reference revision scope/date mismatch' USING ERRCODE='23514'; END IF;
  ELSIF EXISTS(SELECT 1 FROM inventory.fertilizer_reference_versions WHERE entity_id=NEW.entity_id AND work_area_id=NEW.work_area_id) THEN RAISE EXCEPTION 'Reference replacement requires predecessor' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO r FROM inventory.fertilizer_revisions WHERE plan_id=NEW.plan_id ORDER BY revision DESC LIMIT 1;
 IF TG_TABLE_NAME='fertilizer_revisions' THEN
  IF NEW.revision<>COALESCE(r.revision,0)+1  THEN RAISE EXCEPTION 'New current draft revision required' USING ERRCODE='23514'; END IF;
  p=NEW.input;
  IF EXISTS(SELECT 1 FROM inventory.fertilizer_actuals WHERE plan_id=NEW.plan_id) AND
   ((r.input->>'greenhouse_id') IS DISTINCT FROM p->>'greenhouse_id' OR
    (SELECT jsonb_agg(jsonb_build_object('card',x->>'card_id','part',x->>'work_area_id','application',x->>'application_id','components',(SELECT jsonb_agg(jsonb_build_object('id',item->>'component_id','product',item->>'product_id','unit',item->>'unit') ORDER BY item->>'component_id') FROM jsonb_array_elements(x->'components') AS pieces(item))) ORDER BY x->>'card_id') FROM jsonb_array_elements(r.input->'cards') x)
    IS DISTINCT FROM
    (SELECT jsonb_agg(jsonb_build_object('card',x->>'card_id','part',x->>'work_area_id','application',x->>'application_id','components',(SELECT jsonb_agg(jsonb_build_object('id',item->>'component_id','product',item->>'product_id','unit',item->>'unit') ORDER BY item->>'component_id') FROM jsonb_array_elements(x->'components') AS pieces(item))) ORDER BY x->>'card_id') FROM jsonb_array_elements(p->'cards') x))
   THEN RAISE EXCEPTION 'Posted plan identities remain stable across revisions' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(p->'cards') AS steps(item) WHERE item->>'application_id' IS NOT NULL GROUP BY item->>'application_id',item->>'work_area_id' HAVING count(*)>1)
   THEN RAISE EXCEPTION 'Duplicate application part' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(p->'cards') AS steps(item) WHERE item->>'application_id' IS NOT NULL GROUP BY item->>'application_id' HAVING count(DISTINCT item->>'application_order')<>1)
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(p->'cards') AS steps(item) WHERE item->>'application_id' IS NOT NULL GROUP BY item->>'application_order' HAVING count(DISTINCT item->>'application_id')>1)
  THEN RAISE EXCEPTION 'Consistent distinct application order required' USING ERRCODE='23514'; END IF;
  FOR c IN SELECT value FROM jsonb_array_elements(p->'cards') LOOP
   IF (c ? 'application_order' OR c ? 'mixing_intent') AND NOT(c ? 'application_id') OR c ? 'application_id' AND NOT(c ? 'application_order') THEN RAISE EXCEPTION 'Explicit application/order required' USING ERRCODE='23514'; END IF;
   IF c ? 'application_id' THEN IF jsonb_typeof(c->'application_id') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'Typed application identity required' USING ERRCODE='23514'; END IF; PERFORM (c->>'application_id')::uuid; END IF;
   IF c ? 'application_order' THEN PERFORM inventory.fertilizer_positive_int(c->'application_order'); IF (c->>'application_order')::int>50 THEN RAISE EXCEPTION 'Bounded application order required' USING ERRCODE='23514'; END IF; END IF;
   IF c ? 'mixing_intent' AND (jsonb_typeof(c->'mixing_intent') IS DISTINCT FROM 'string' OR c->>'mixing_intent' NOT IN ('SINGLE','TOGETHER','SEPARATE','UNRESOLVED')) THEN RAISE EXCEPTION 'Explicit mixing intent required' USING ERRCODE='23514'; END IF;
  END LOOP;
  IF NEW.preview->'cards' IS DISTINCT FROM (SELECT jsonb_agg(item ORDER BY COALESCE((item->>'application_order')::int,1),n) FROM jsonb_array_elements(NEW.preview->'cards') WITH ORDINALITY ordered(item,n)) THEN RAISE EXCEPTION 'Prepared instruction must preserve explicit application order' USING ERRCODE='23514'; END IF;
  PERFORM inventory.fertilizer_typed(p,ARRAY['source','greenhouse_id','event_date','intended_send_at','copied_from_plan_id','copied_from_revision','copy_reason','cards'],ARRAY['greenhouse_id','event_date']);
  PERFORM inventory.fertilizer_form(p->'source',NEW.entity_id);
  IF p->'intended_send_at'<>'null'::jsonb THEN PERFORM inventory.fertilizer_stamp(p->'intended_send_at'); END IF;
  IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR p->'source'->>'source_kind' IS DISTINCT FROM 'OWNER_FORM' OR (p->'source'->>'form_id')::uuid IS DISTINCT FROM NEW.source_id
  OR NEW.preview->'input' IS DISTINCT FROM p OR NEW.preview->>'preview_digest' IS DISTINCT FROM NEW.preview_digest
  OR jsonb_typeof(p->'cards') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'cards') NOT BETWEEN 1 AND 50
  OR NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=(p->>'greenhouse_id')::uuid AND entity_id=NEW.entity_id AND kind='greenhouse' AND parent_id IS NULL)
  THEN RAISE EXCEPTION 'Typed plan source/scope required' USING ERRCODE='23514'; END IF;
  PERFORM (p->>'event_date')::date; PERFORM (p->'source'->>'captured_at')::timestamptz;
  IF p->'source'->>'inbox_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.inbox_items WHERE id=(p->'source'->>'inbox_id')::uuid AND entity_id=NEW.entity_id) THEN RAISE EXCEPTION 'Plan original scope mismatch' USING ERRCODE='23514'; END IF;
  IF p->>'copied_from_plan_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.fertilizer_revisions WHERE plan_id=(p->>'copied_from_plan_id')::uuid AND entity_id=NEW.entity_id AND revision=(p->>'copied_from_revision')::int) THEN RAISE EXCEPTION 'Copy lineage mismatch' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='fertilizer_reports' AND NEW.source_snapshot->'late_evidence'='true'::jsonb THEN SELECT * INTO r FROM inventory.fertilizer_revisions WHERE id=NEW.revision_id AND plan_id=NEW.plan_id; END IF;
 IF NEW.revision_id IS DISTINCT FROM r.id THEN RAISE EXCEPTION 'Current fertilizer revision required' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='fertilizer_dispatches' THEN
  p=NEW.source_snapshot;
  PERFORM inventory.fertilizer_typed(p,ARRAY['client_approval_id','captured_at','expected_version','revision','preview_digest','confirmed'],ARRAY['client_approval_id','captured_at','preview_digest']);
  PERFORM inventory.fertilizer_stamp(p->'captured_at');
  PERFORM inventory.fertilizer_positive_int(p->'expected_version'); PERFORM inventory.fertilizer_positive_int(p->'revision');
  IF (p->>'client_approval_id')::uuid IS DISTINCT FROM NEW.id OR p->'confirmed' IS DISTINCT FROM 'true'::jsonb
  OR (p->>'expected_version')::int IS DISTINCT FROM inventory.fertilizer_version(NEW.plan_id) OR (p->>'revision')::int IS DISTINCT FROM r.revision
  OR p->>'preview_digest' IS DISTINCT FROM r.preview_digest OR r.input->>'intended_send_at' IS NULL
  OR (SELECT count(*) FROM inventory.fertilizer_cards WHERE revision_id=r.id)<>jsonb_array_length(r.input->'cards')
  THEN RAISE EXCEPTION 'Exact current dispatch approval required' USING ERRCODE='23514'; END IF;
  FOR c IN SELECT snapshot FROM inventory.fertilizer_cards WHERE revision_id=r.id LOOP
   IF c->>'mixing_intent'='UNRESOLVED' OR c->>'application_id' IS NOT NULL AND jsonb_array_length(c->'components')>1 AND COALESCE(c->>'mixing_intent','') <>'TOGETHER' THEN RAISE EXCEPTION 'Owner composition intent unresolved' USING ERRCODE='23514'; END IF;
   IF c->>'area_da' IS NULL OR c->>'worker_id' IS NULL OR c->'rendering_reviewed' IS DISTINCT FROM 'true'::jsonb OR length(btrim(COALESCE(c->>'rendered_text','')))=0
   OR NOT EXISTS(SELECT 1 FROM workers.people WHERE id=(c->>'worker_id')::uuid AND entity_id=NEW.entity_id AND active AND language='fa')
   THEN RAISE EXCEPTION 'Reviewed area/recipient/Dari required' USING ERRCODE='23514'; END IF;
   FOR ln IN SELECT value FROM jsonb_array_elements(c->'components') LOOP
    IF ln->>'dose_per_da' IS NULL OR ln->>'proposed_quantity' IS NULL OR ln->>'location_id' IS NULL THEN RAISE EXCEPTION 'Complete card quantities/location required' USING ERRCODE='23514'; END IF;
   END LOOP;
  END LOOP;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='fertilizer_reports' THEN
  p=NEW.source_snapshot;
  PERFORM inventory.fertilizer_typed(p,ARRAY['schema_version','source_kind','client_response_id','card_id','expected_revision','dispatch_id','response','captured_at','button_label','original_language','comment','quantities']||(SELECT COALESCE(array_agg(k),ARRAY[]::text[]) FROM unnest(ARRAY['physical_execution_id','quantity_mode','late_evidence']) k WHERE p ? k),ARRAY['source_kind','client_response_id','card_id','dispatch_id','response','captured_at','button_label','original_language']);
  PERFORM inventory.fertilizer_stamp(p->'captured_at');
  IF p->>'source_kind' IS DISTINCT FROM 'UI_ACTION' OR p->'schema_version' IS DISTINCT FROM '1'::jsonb OR jsonb_typeof(p->'comment') NOT IN ('null','string') OR length(p->>'comment')>10000 THEN RAISE EXCEPTION 'Typed UI_ACTION original required' USING ERRCODE='23514'; END IF;
  IF p ? 'physical_execution_id' THEN IF jsonb_typeof(p->'physical_execution_id') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'Typed execution identity required' USING ERRCODE='23514'; END IF; PERFORM (p->>'physical_execution_id')::uuid; END IF;
  IF p ? 'quantity_mode' AND (jsonb_typeof(p->'quantity_mode') IS DISTINCT FROM 'string' OR p->>'quantity_mode' NOT IN ('INCREMENTAL','CUMULATIVE','UNSPECIFIED')) THEN RAISE EXCEPTION 'Quantity mode required' USING ERRCODE='23514'; END IF;
  IF p ? 'late_evidence' AND jsonb_typeof(p->'late_evidence')<>'boolean' THEN RAISE EXCEPTION 'Typed late evidence flag required' USING ERRCODE='23514'; END IF;
  PERFORM inventory.fertilizer_positive_int(p->'expected_revision');
  SELECT * INTO w FROM workers.people WHERE id=NEW.worker_id;
  IF w.entity_id<>NEW.entity_id OR NOT w.active OR w.actor_id IS DISTINCT FROM NEW.actor_id
  OR NOT EXISTS(SELECT 1 FROM inventory.fertilizer_dispatches WHERE id=NEW.dispatch_id AND plan_id=NEW.plan_id AND revision_id=NEW.revision_id AND entity_id=NEW.entity_id)
  OR NOT EXISTS(SELECT 1 FROM inventory.fertilizer_cards WHERE revision_id=NEW.revision_id AND card_id=NEW.card_id AND worker_id=NEW.worker_id)
  OR (p->>'client_response_id')::uuid IS DISTINCT FROM NEW.id OR (p->>'card_id')::uuid IS DISTINCT FROM NEW.card_id OR (p->>'dispatch_id')::uuid IS DISTINCT FROM NEW.dispatch_id OR (p->>'expected_revision')::int IS DISTINCT FROM r.revision
  OR p->>'response' IS DISTINCT FROM NEW.response OR p->>'original_language' NOT IN ('ru','tr','fa','uz') OR length(btrim(COALESCE(p->>'button_label',''))) NOT BETWEEN 1 AND 200
  OR jsonb_typeof(p->'quantities') IS DISTINCT FROM 'array' OR (NEW.response='DONE' AND jsonb_array_length(p->'quantities')<>0)
  THEN RAISE EXCEPTION 'Typed assigned fertilizer response required' USING ERRCODE='23514'; END IF;
  PERFORM (p->>'captured_at')::timestamptz;
  SELECT snapshot INTO c FROM inventory.fertilizer_cards WHERE revision_id=r.id AND card_id=NEW.card_id;
  IF (SELECT count(*) FROM jsonb_array_elements(p->'quantities'))<>(SELECT count(DISTINCT value->>'component_id') FROM jsonb_array_elements(p->'quantities')) THEN RAISE EXCEPTION 'Duplicate response quantity' USING ERRCODE='23514'; END IF;
  FOR ln IN SELECT value FROM jsonb_array_elements(p->'quantities') LOOP
   PERFORM inventory.fertilizer_typed(ln,ARRAY['component_id','quantity'],ARRAY['component_id']);
   IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(c->'components') x WHERE x->>'component_id'=ln->>'component_id') THEN RAISE EXCEPTION 'Response component mismatch' USING ERRCODE='23514'; END IF;
   IF ln->'quantity'<>'null'::jsonb THEN PERFORM inventory.fertilizer_numeric(ln->'quantity',true); END IF;
  END LOOP;
  RETURN NEW;
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION inventory.fertilizer_actual_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r inventory.fertilizer_revisions; c jsonb; ln jsonb; comp jsonb; rep inventory.fertilizer_reports; ev inventory.events; expected jsonb='[]'; q numeric; proposed numeric; execution uuid; cid text; cardrows integer; positives integer;
BEGIN
 PERFORM inventory.fertilizer_lock(NEW.entity_id,NEW.plan_id);
 SELECT * INTO r FROM inventory.fertilizer_revisions WHERE plan_id=NEW.plan_id ORDER BY revision DESC LIMIT 1;
 SELECT * INTO ev FROM inventory.events WHERE id=NEW.application_id;
 PERFORM inventory.fertilizer_typed(NEW.source_snapshot,ARRAY['input','actual_preview_digest','confirmed'],ARRAY['actual_preview_digest']);
 PERFORM inventory.fertilizer_typed(NEW.input,ARRAY['source','expected_version','revision','event_date','duplicate_reviewed','lines','completed_card_ids']||CASE WHEN NEW.input ? 'execution_kind' THEN ARRAY['execution_kind'] ELSE ARRAY[]::text[] END,ARRAY['event_date']);
 PERFORM inventory.fertilizer_form(NEW.input->'source',NEW.entity_id);
 IF NEW.input->>'event_date' !~ '^\d{4}-\d{2}-\d{2}$' THEN RAISE EXCEPTION 'Exact ISO event date required' USING ERRCODE='23514'; END IF;
 PERFORM (NEW.input->>'event_date')::date;
 PERFORM inventory.fertilizer_positive_int(NEW.input->'expected_version'); PERFORM inventory.fertilizer_positive_int(NEW.input->'revision');
 IF jsonb_typeof(NEW.input->'duplicate_reviewed') IS DISTINCT FROM 'boolean' THEN RAISE EXCEPTION 'Duplicate review flag required' USING ERRCODE='23514'; END IF;
 IF NOT core.r2_owner(NEW.entity_id,NEW.created_by) OR r.id IS DISTINCT FROM NEW.revision_id
 OR NEW.source_snapshot->'input' IS DISTINCT FROM NEW.input OR NEW.source_snapshot->'confirmed' IS DISTINCT FROM 'true'::jsonb
 OR NEW.source_snapshot->>'actual_preview_digest' IS DISTINCT FROM NEW.preview_digest
 OR (NEW.input->>'expected_version')::int IS DISTINCT FROM inventory.fertilizer_version(NEW.plan_id) OR (NEW.input->>'revision')::int IS DISTINCT FROM r.revision
 OR (NEW.input->'source'->>'form_id')::uuid IS DISTINCT FROM NEW.source_id
 OR NOT EXISTS(SELECT 1 FROM inventory.fertilizer_dispatches WHERE revision_id=r.id)
 THEN RAISE EXCEPTION 'Exact current owner actual required' USING ERRCODE='23514'; END IF;
 IF NEW.input ? 'execution_kind' AND (jsonb_typeof(NEW.input->'execution_kind') IS DISTINCT FROM 'string' OR NEW.input->>'execution_kind' NOT IN ('FIRST','ADDITIONAL')) THEN RAISE EXCEPTION 'Execution kind required' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM inventory.fertilizer_actuals WHERE plan_id=NEW.plan_id) AND NEW.input->>'execution_kind' IS DISTINCT FROM 'ADDITIONAL' THEN RAISE EXCEPTION 'Explicit new physical execution required' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(NEW.input->'lines') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.input->'lines') NOT BETWEEN 1 AND 100
 OR jsonb_array_length(NEW.input->'lines')<>(SELECT count(DISTINCT (x->>'card_id',x->>'component_id')) FROM jsonb_array_elements(NEW.input->'lines') x)
 THEN RAISE EXCEPTION 'Distinct selected actual scope required' USING ERRCODE='23514'; END IF;
 FOR ln IN SELECT value FROM jsonb_array_elements(NEW.input->'lines') LOOP
  PERFORM inventory.fertilizer_typed(ln,ARRAY['card_id','component_id','report_id','quantity','disposition','reason']||(SELECT COALESCE(array_agg(k),ARRAY[]::text[]) FROM unnest(ARRAY['physical_execution_id','remainder_decision']) k WHERE ln ? k),ARRAY['card_id','component_id','quantity','disposition']);
  SELECT snapshot INTO c FROM inventory.fertilizer_cards WHERE revision_id=r.id AND card_id=(ln->>'card_id')::uuid;
  SELECT value INTO comp FROM jsonb_array_elements(c->'components') WHERE value->>'component_id'=ln->>'component_id';
  SELECT * INTO rep FROM inventory.fertilizer_reports WHERE id=(ln->>'report_id')::uuid;
  IF c IS NULL OR comp IS NULL OR ln->>'report_id' IS NOT NULL AND (rep.id IS NULL OR rep.revision_id<>r.id OR rep.card_id<>(ln->>'card_id')::uuid OR rep.plan_id<>NEW.plan_id) THEN RAISE EXCEPTION 'Exact scoped report/component required' USING ERRCODE='23514'; END IF;
  IF rep.id IS NOT NULL AND rep.id IS DISTINCT FROM (SELECT id FROM inventory.fertilizer_reports WHERE revision_id=r.id AND card_id=rep.card_id ORDER BY received_at DESC,id DESC LIMIT 1) THEN RAISE EXCEPTION 'Newer evidence requires owner review' USING ERRCODE='23514'; END IF;
  IF ln ? 'physical_execution_id' AND jsonb_typeof(ln->'physical_execution_id') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'Typed execution identity required' USING ERRCODE='23514'; END IF;
  execution=COALESCE((ln->>'physical_execution_id')::uuid,(rep.source_snapshot->>'physical_execution_id')::uuid,rep.id);
  IF execution IS NULL OR rep.id IS NOT NULL AND execution IS DISTINCT FROM COALESCE((rep.source_snapshot->>'physical_execution_id')::uuid,rep.id) THEN RAISE EXCEPTION 'Stable physical execution identity required' USING ERRCODE='23514'; END IF;
  IF rep.source_snapshot->>'quantity_mode'='CUMULATIVE' THEN RAISE EXCEPTION 'Cumulative report is not incremental actual' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM inventory.fertilizer_actual_lines f WHERE f.plan_id=NEW.plan_id AND f.card_id=(ln->>'card_id')::uuid AND f.component_id=(ln->>'component_id')::uuid AND (f.physical_execution_id=execution OR f.report_id=rep.id)) THEN RAISE EXCEPTION 'Physical scope already confirmed' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM inventory.fertilizer_actual_lines f WHERE f.plan_id=NEW.plan_id AND f.card_id=(ln->>'card_id')::uuid AND f.component_id=(ln->>'component_id')::uuid AND f.remainder_decision='CLOSE' AND NOT EXISTS(SELECT 1 FROM inventory.fertilizer_resolutions rs WHERE rs.plan_id=f.plan_id AND rs.card_id=f.card_id AND rs.component_id=f.component_id AND rs.kind='REOPEN_SCOPE' AND rs.created_at>f.created_at)) THEN RAISE EXCEPTION 'Explicit reopen required' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM inventory.fertilizer_actual_lines f JOIN inventory.fertilizer_actuals a ON a.id=f.actual_id WHERE f.plan_id=NEW.plan_id AND f.card_id=(ln->>'card_id')::uuid AND f.component_id=(ln->>'component_id')::uuid AND EXISTS(SELECT 1 FROM inventory.events child WHERE child.replacement_of_id=a.application_id) AND (SELECT count(*) FROM inventory.fertilizer_actual_lines peer WHERE peer.actual_id=f.actual_id AND peer.quantity>0 AND (peer.product_id,peer.location_id,peer.work_area_id,peer.unit)=(f.product_id,f.location_id,f.work_area_id,f.unit))>1) THEN RAISE EXCEPTION 'Ambiguous replacement requires explicit scope resolution' USING ERRCODE='23514'; END IF;
  IF jsonb_typeof(ln->'reason') NOT IN ('null','string') OR length(ln->>'reason')>2000 THEN RAISE EXCEPTION 'Exact owner reason required' USING ERRCODE='23514'; END IF;
  q=inventory.fertilizer_numeric(ln->'quantity',true);
  IF (q>0) IS DISTINCT FROM (ln->>'disposition'='APPLIED') OR ln->>'disposition' NOT IN ('APPLIED','NOT_APPLIED') THEN RAISE EXCEPTION 'Explicit zero differs from unknown' USING ERRCODE='23514'; END IF;
  IF ln ? 'remainder_decision' AND (jsonb_typeof(ln->'remainder_decision') IS DISTINCT FROM 'string' OR ln->>'remainder_decision' NOT IN ('KEEP_OPEN','CLOSE')) THEN RAISE EXCEPTION 'Explicit remainder decision required' USING ERRCODE='23514'; END IF;
  proposed=NULL;
  SELECT replace(x->>'quantity',',','.')::numeric INTO proposed FROM jsonb_array_elements(rep.source_snapshot->'quantities') x WHERE x->>'component_id'=ln->>'component_id';
  IF rep.response='DONE' THEN proposed=(comp->>'proposed_quantity')::numeric; END IF;
  IF (q=0 OR rep.id IS NULL OR rep.response<>'DONE' OR rep.source_snapshot->>'quantity_mode'='UNSPECIFIED' OR proposed IS NULL OR q IS DISTINCT FROM proposed OR ln->>'remainder_decision'='CLOSE') AND length(btrim(COALESCE(ln->>'reason','')))=0 THEN RAISE EXCEPTION 'Owner discrepancy/zero resolution required' USING ERRCODE='23514'; END IF;
  IF q>0 THEN expected=expected||jsonb_build_array(jsonb_build_object('product_id',comp->>'product_id','original_product_name',comp->>'product_name','quantity',q,'unit',comp->>'unit','location_id',comp->>'location_id','greenhouse_id',r.input->>'greenhouse_id','work_area_id',c->>'work_area_id')); END IF;
 END LOOP;
 IF jsonb_array_length(expected)=0 THEN
  IF NEW.application_id IS NOT NULL THEN RAISE EXCEPTION 'Zero fact has no inventory event' USING ERRCODE='23514'; END IF;
 ELSE
  IF ev.id IS NULL OR (ev.entity_id,ev.kind,ev.status,ev.source,ev.locator,ev.event_date,ev.created_by,ev.posted_by) IS DISTINCT FROM (NEW.entity_id,'application'::text,'posted'::text,'fertilizer:actual'::text,NEW.source_id::text,(NEW.input->>'event_date')::date,NEW.created_by,NEW.created_by)
  OR ev.duplicate_reviewed IS DISTINCT FROM (NEW.input->>'duplicate_reviewed')::boolean OR ev.evidence->>'source_document' IS DISTINCT FROM 'fertilizer-plan:'||NEW.plan_id::text
  OR ev.replacement_of_id IS NOT NULL OR ev.payload->>'supplier_id' IS NOT NULL OR ev.payload->>'finance_obligation_id' IS NOT NULL OR ev.payload->>'method' IS NOT NULL OR ev.payload->>'notes' IS NOT NULL
  THEN RAISE EXCEPTION 'Exact owner inventory event required' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(ev.payload->'lines') x WHERE x->>'unit_cost' IS NOT NULL OR x->>'currency' IS NOT NULL OR x->>'destination_id' IS NOT NULL OR x->>'original_quantity' IS NOT NULL OR x->>'original_unit' IS NOT NULL) THEN RAISE EXCEPTION 'No undeclared actual valuation or destination facts' USING ERRCODE='23514'; END IF;
  IF (SELECT jsonb_agg(jsonb_build_object('product_id',x->>'product_id','original_product_name',x->>'original_product_name','quantity',(x->>'quantity')::numeric,'unit',x->>'unit','location_id',x->>'location_id','greenhouse_id',x->>'greenhouse_id','work_area_id',x->>'work_area_id') ORDER BY n) FROM jsonb_array_elements(ev.payload->'lines') WITH ORDINALITY z(x,n)) IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Application differs from selected owner actual' USING ERRCODE='23514'; END IF;
 END IF;
 IF jsonb_typeof(NEW.input->'completed_card_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.input->'completed_card_ids')<>(SELECT count(DISTINCT value) FROM jsonb_array_elements(NEW.input->'completed_card_ids')) THEN RAISE EXCEPTION 'Distinct completed scope required' USING ERRCODE='23514'; END IF;
 -- Completion remains a claim that each component has a positive accepted fact.
 FOR cid IN SELECT jsonb_array_elements_text(NEW.input->'completed_card_ids') LOOP
  SELECT jsonb_array_length(snapshot->'components') INTO cardrows FROM inventory.fertilizer_cards WHERE revision_id=r.id AND card_id=cid::uuid;
  SELECT count(DISTINCT component) INTO positives FROM (
   SELECT x->>'component_id' component FROM jsonb_array_elements(NEW.input->'lines') x WHERE x->>'card_id'=cid AND x->>'disposition'='APPLIED'
   UNION SELECT component_id::text FROM inventory.fertilizer_actual_lines WHERE plan_id=NEW.plan_id AND card_id=cid::uuid AND quantity>0) known;
  IF cardrows IS NULL OR positives<>cardrows THEN RAISE EXCEPTION 'Completed card requires positive components' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE FUNCTION inventory.fertilizer_actual_lines_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ln jsonb;c jsonb;comp jsonb;rep inventory.fertilizer_reports;idx integer=0;q numeric;
BEGIN
 FOR ln IN SELECT value FROM jsonb_array_elements(NEW.input->'lines') LOOP
  SELECT snapshot INTO c FROM inventory.fertilizer_cards WHERE revision_id=NEW.revision_id AND card_id=(ln->>'card_id')::uuid;
  SELECT value INTO comp FROM jsonb_array_elements(c->'components') WHERE value->>'component_id'=ln->>'component_id';
  SELECT * INTO rep FROM inventory.fertilizer_reports WHERE id=(ln->>'report_id')::uuid;
  q=inventory.fertilizer_numeric(ln->'quantity',true);
  INSERT INTO inventory.fertilizer_actual_lines(actual_id,entity_id,plan_id,revision_id,physical_execution_id,card_id,component_id,report_id,product_id,location_id,work_area_id,unit,quantity,remainder_decision,inventory_line_index)
  VALUES(NEW.id,NEW.entity_id,NEW.plan_id,NEW.revision_id,COALESCE((ln->>'physical_execution_id')::uuid,(rep.source_snapshot->>'physical_execution_id')::uuid,rep.id),(ln->>'card_id')::uuid,(ln->>'component_id')::uuid,rep.id,(comp->>'product_id')::uuid,(comp->>'location_id')::uuid,(c->>'work_area_id')::uuid,comp->>'unit',q,COALESCE(ln->>'remainder_decision','KEEP_OPEN'),CASE WHEN q>0 THEN idx ELSE NULL END);
  IF q>0 THEN idx=idx+1; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER fertilizer_actual_lines_insert AFTER INSERT ON inventory.fertilizer_actuals FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_actual_lines_insert();
CREATE FUNCTION inventory.fertilizer_actual_line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a inventory.fertilizer_actuals; ln jsonb;c jsonb;comp jsonb;rep inventory.fertilizer_reports;expected_index integer;
BEGIN
 SELECT * INTO a FROM inventory.fertilizer_actuals WHERE id=NEW.actual_id;
 PERFORM inventory.fertilizer_lock(NEW.entity_id,NEW.plan_id);
 SELECT value INTO ln FROM jsonb_array_elements(a.input->'lines') WHERE value->>'card_id'=NEW.card_id::text AND value->>'component_id'=NEW.component_id::text;
 SELECT snapshot INTO c FROM inventory.fertilizer_cards WHERE revision_id=a.revision_id AND card_id=NEW.card_id;
 SELECT value INTO comp FROM jsonb_array_elements(c->'components') WHERE value->>'component_id'=NEW.component_id::text;
 SELECT * INTO rep FROM inventory.fertilizer_reports WHERE id=(ln->>'report_id')::uuid;
 SELECT count(*)::int INTO expected_index FROM jsonb_array_elements(a.input->'lines') WITH ORDINALITY z(v,n) WHERE replace(v->>'quantity',',','.')::numeric>0 AND n<(SELECT n FROM jsonb_array_elements(a.input->'lines') WITH ORDINALITY t(v,n) WHERE v=ln);
 IF ln IS NULL OR (a.entity_id,a.plan_id,a.revision_id) IS DISTINCT FROM (NEW.entity_id,NEW.plan_id,NEW.revision_id)
 OR NEW.physical_execution_id IS DISTINCT FROM COALESCE((ln->>'physical_execution_id')::uuid,(rep.source_snapshot->>'physical_execution_id')::uuid,rep.id)
 OR NEW.report_id IS DISTINCT FROM (ln->>'report_id')::uuid OR NEW.quantity IS DISTINCT FROM replace(ln->>'quantity',',','.')::numeric
 OR (NEW.product_id,NEW.location_id,NEW.work_area_id,NEW.unit) IS DISTINCT FROM ((comp->>'product_id')::uuid,(comp->>'location_id')::uuid,(c->>'work_area_id')::uuid,comp->>'unit')
 OR NEW.remainder_decision IS DISTINCT FROM COALESCE(ln->>'remainder_decision','KEEP_OPEN')
 OR NEW.inventory_line_index IS DISTINCT FROM (CASE WHEN NEW.quantity>0 THEN expected_index ELSE NULL END)
 THEN RAISE EXCEPTION 'Exact immutable normalized actual line required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER fertilizer_actual_line_guard BEFORE INSERT ON inventory.fertilizer_actual_lines FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_actual_line_guard();
CREATE FUNCTION inventory.fertilizer_actual_lines_finality() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (SELECT count(*) FROM inventory.fertilizer_actual_lines WHERE actual_id=NEW.id)<>jsonb_array_length(NEW.input->'lines') THEN RAISE EXCEPTION 'All actual scope identities required atomically' USING ERRCODE='23514'; END IF;RETURN NEW;END $$;
CREATE CONSTRAINT TRIGGER fertilizer_actual_lines_finality AFTER INSERT ON inventory.fertilizer_actuals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_actual_lines_finality();
CREATE FUNCTION inventory.fertilizer_resolution_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb;r inventory.fertilizer_revisions;
BEGIN
 PERFORM inventory.fertilizer_lock(NEW.entity_id,NEW.plan_id);p=NEW.source_snapshot;
 SELECT * INTO r FROM inventory.fertilizer_revisions WHERE plan_id=NEW.plan_id ORDER BY revision DESC LIMIT 1;
 PERFORM inventory.fertilizer_typed(p,ARRAY['source','expected_version','revision','kind','report_id','actual_id','card_id','component_id','reason','confirmed'],ARRAY['kind','reason']);
 PERFORM inventory.fertilizer_form(p->'source',NEW.entity_id);
 PERFORM inventory.fertilizer_positive_int(p->'expected_version'); PERFORM inventory.fertilizer_positive_int(p->'revision');
 IF NOT core.r2_owner(NEW.entity_id,NEW.created_by) OR (p->>'expected_version')::int IS DISTINCT FROM inventory.fertilizer_version(NEW.plan_id) OR (p->>'revision')::int IS DISTINCT FROM r.revision
 OR (p->'source'->>'form_id')::uuid IS DISTINCT FROM NEW.source_id OR p->'confirmed' IS DISTINCT FROM 'true'::jsonb OR length(btrim(p->>'reason')) NOT BETWEEN 1 AND 2000
 OR (p->>'kind',(p->>'report_id')::uuid,(p->>'actual_id')::uuid,(p->>'card_id')::uuid,(p->>'component_id')::uuid) IS DISTINCT FROM (NEW.kind,NEW.report_id,NEW.actual_id,NEW.card_id,NEW.component_id)
 OR NEW.report_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.fertilizer_reports WHERE id=NEW.report_id AND plan_id=NEW.plan_id AND entity_id=NEW.entity_id)
 OR NEW.actual_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.fertilizer_actuals WHERE id=NEW.actual_id AND plan_id=NEW.plan_id AND entity_id=NEW.entity_id)
 OR NEW.kind<>'REOPEN_SCOPE' AND NEW.report_id IS NULL THEN RAISE EXCEPTION 'Exact scoped owner resolution required' USING ERRCODE='23514'; END IF;
 IF NEW.actual_id IS NOT NULL AND NEW.report_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.fertilizer_actuals a JOIN inventory.fertilizer_reports rep ON rep.id=NEW.report_id CROSS JOIN LATERAL jsonb_array_elements(a.input->'lines') ln WHERE a.id=NEW.actual_id AND ln->>'card_id'=rep.card_id::text) THEN RAISE EXCEPTION 'Resolution actual/report application-part mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.kind='REOPEN_SCOPE' AND NOT EXISTS(SELECT 1 FROM inventory.fertilizer_actual_lines f WHERE f.plan_id=NEW.plan_id AND f.card_id=NEW.card_id AND f.component_id=NEW.component_id AND f.remainder_decision='CLOSE' AND NOT EXISTS(SELECT 1 FROM inventory.fertilizer_resolutions x WHERE x.plan_id=f.plan_id AND x.card_id=f.card_id AND x.component_id=f.component_id AND x.kind='REOPEN_SCOPE' AND x.created_at>f.created_at)) THEN RAISE EXCEPTION 'Closed exact scope required' USING ERRCODE='23514'; END IF;
 IF NEW.kind='CORRECTION' AND NOT EXISTS(WITH RECURSIVE chain AS (SELECT ev.id,ev.status,0 depth FROM inventory.fertilizer_actuals a JOIN inventory.events ev ON ev.id=a.application_id WHERE a.id=NEW.actual_id UNION ALL SELECT ev.id,ev.status,c.depth+1 FROM inventory.events ev JOIN chain c ON ev.replacement_of_id=c.id) SELECT 1 FROM (SELECT * FROM chain ORDER BY depth DESC LIMIT 1) tail WHERE tail.status IN ('posted','reversed') AND (tail.depth>0 OR tail.status='reversed')) THEN RAISE EXCEPTION 'Existing audited inventory correction required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER fertilizer_resolution_guard BEFORE INSERT ON inventory.fertilizer_resolutions FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_resolution_guard();
""")
def downgrade():raise RuntimeError("0031 is forward-only; restore verified backup and reconcile immutable history")
