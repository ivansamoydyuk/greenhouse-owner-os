"""Fertilizer plans and bounded owner actual scope, forward-only."""
from alembic import op
revision='20261010_0029'
down_revision='20261010_0028'
branch_labels=depends_on=None

def upgrade():
    op.execute(r"""
CREATE TABLE inventory.fertilizer_reference_versions(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities(id),work_area_id uuid NOT NULL REFERENCES core.work_areas(id),
 area_da numeric CHECK(area_da>0 AND area_da<1e18 AND scale(area_da)<=12),default_worker_id uuid REFERENCES workers.people(id),
 canonical_key text,verification text NOT NULL CHECK(length(btrim(verification)) BETWEEN 1 AND 2000),effective_from date NOT NULL,
 replaces_id uuid UNIQUE REFERENCES inventory.fertilizer_reference_versions(id),created_by uuid NOT NULL REFERENCES core.actors(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 source_snapshot jsonb NOT NULL,CHECK(area_da IS NOT NULL OR default_worker_id IS NOT NULL));
CREATE TABLE inventory.fertilizer_plans(id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities(id),created_by uuid NOT NULL REFERENCES core.actors(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE inventory.fertilizer_revisions(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities(id),plan_id uuid NOT NULL REFERENCES inventory.fertilizer_plans(id),revision integer NOT NULL CHECK(revision>0),
 source_id uuid NOT NULL,input jsonb NOT NULL,preview jsonb NOT NULL,preview_digest text NOT NULL CHECK(length(preview_digest)=64),
 created_by uuid NOT NULL REFERENCES core.actors(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(plan_id,revision),UNIQUE(entity_id,source_id));
CREATE TABLE inventory.fertilizer_cards(
 revision_id uuid NOT NULL REFERENCES inventory.fertilizer_revisions(id),card_id uuid NOT NULL,entity_id uuid NOT NULL REFERENCES core.entities(id),plan_id uuid NOT NULL REFERENCES inventory.fertilizer_plans(id),
 work_area_id uuid NOT NULL REFERENCES core.work_areas(id),worker_id uuid REFERENCES workers.people(id),snapshot jsonb NOT NULL,PRIMARY KEY(revision_id,card_id));
CREATE TABLE inventory.fertilizer_dispatches(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities(id),plan_id uuid NOT NULL REFERENCES inventory.fertilizer_plans(id),revision_id uuid NOT NULL UNIQUE REFERENCES inventory.fertilizer_revisions(id),
 created_by uuid NOT NULL REFERENCES core.actors(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),source_snapshot jsonb NOT NULL);
CREATE TABLE inventory.fertilizer_reports(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities(id),plan_id uuid NOT NULL REFERENCES inventory.fertilizer_plans(id),revision_id uuid NOT NULL REFERENCES inventory.fertilizer_revisions(id),
 dispatch_id uuid NOT NULL REFERENCES inventory.fertilizer_dispatches(id),card_id uuid NOT NULL,worker_id uuid NOT NULL REFERENCES workers.people(id),actor_id uuid NOT NULL REFERENCES core.actors(id),
 response text NOT NULL CHECK(response IN ('DONE','PARTIAL','PROBLEM')),source_snapshot jsonb NOT NULL,received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(revision_id,card_id) REFERENCES inventory.fertilizer_cards(revision_id,card_id));
-- UNIQUE(plan_id) is a disclosed implementation restriction, not a lifetime
-- business policy. Partial remainder stays unresolved, never auto-completed.
CREATE TABLE inventory.fertilizer_actuals(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities(id),plan_id uuid NOT NULL UNIQUE REFERENCES inventory.fertilizer_plans(id),revision_id uuid NOT NULL REFERENCES inventory.fertilizer_revisions(id),
 source_id uuid NOT NULL,application_id uuid NOT NULL UNIQUE REFERENCES inventory.events(id),input jsonb NOT NULL,source_snapshot jsonb NOT NULL,preview_digest text NOT NULL CHECK(length(preview_digest)=64),
 created_by uuid NOT NULL REFERENCES core.actors(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(entity_id,source_id));
CREATE FUNCTION inventory.fertilizer_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Fertilizer originals and approvals are immutable' USING ERRCODE='55000'; END $$;
DO $$ DECLARE t text; BEGIN FOREACH t IN ARRAY ARRAY['reference_versions','plans','revisions','cards','dispatches','reports','actuals'] LOOP
 EXECUTE format('CREATE TRIGGER fertilizer_immutable BEFORE UPDATE OR DELETE ON inventory.fertilizer_%I FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_immutable()',t);
END LOOP; END $$;
CREATE FUNCTION inventory.fertilizer_version(p uuid) RETURNS integer LANGUAGE sql STABLE AS $$
 SELECT (SELECT count(*) FROM inventory.fertilizer_revisions WHERE plan_id=p)+(SELECT count(*) FROM inventory.fertilizer_dispatches WHERE plan_id=p)+(SELECT count(*) FROM inventory.fertilizer_reports WHERE plan_id=p)+(SELECT count(*) FROM inventory.fertilizer_actuals WHERE plan_id=p)
$$;
CREATE FUNCTION inventory.fertilizer_lock(e uuid,p uuid) RETURNS void LANGUAGE plpgsql AS $$ BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('inventory:'||e::text,0));
 IF p IS NOT NULL THEN PERFORM id FROM inventory.fertilizer_plans WHERE id=p AND entity_id=e FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Fertilizer plan scope mismatch' USING ERRCODE='23514'; END IF; END IF;
END $$;
CREATE FUNCTION inventory.fertilizer_numeric(v jsonb,allow_zero boolean DEFAULT false) RETURNS numeric LANGUAGE plpgsql IMMUTABLE AS $$ DECLARE n numeric; BEGIN
 IF jsonb_typeof(v) IS DISTINCT FROM 'string' OR v#>>'{}' !~ '^\d{1,18}([.,]\d{1,12})?$' THEN RAISE EXCEPTION 'Exact fertilizer decimal string required' USING ERRCODE='23514'; END IF;
 n=replace(v#>>'{}',',','.')::numeric;
 IF n>=1e18 OR n<0 OR (NOT allow_zero AND n=0) THEN RAISE EXCEPTION 'Invalid fertilizer quantity' USING ERRCODE='23514'; END IF; RETURN n;
END $$;
CREATE FUNCTION inventory.fertilizer_typed(p jsonb,keys text[],strings text[]) RETURNS void LANGUAGE plpgsql IMMUTABLE AS $$ DECLARE k text; BEGIN
 IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR NOT(p ?& keys) OR p-keys<>'{}'::jsonb THEN RAISE EXCEPTION 'Exact fertilizer source fields required' USING ERRCODE='23514'; END IF;
 FOREACH k IN ARRAY strings LOOP IF jsonb_typeof(p->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'Required fertilizer source string' USING ERRCODE='23514'; END IF; END LOOP;
END $$;
CREATE FUNCTION inventory.fertilizer_positive_int(p jsonb) RETURNS void LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN
 IF jsonb_typeof(p) IS DISTINCT FROM 'number' OR p#>>'{}' !~ '^[1-9][0-9]*$' THEN RAISE EXCEPTION 'Positive typed revision/version required' USING ERRCODE='23514'; END IF; END $$;
CREATE FUNCTION inventory.fertilizer_stamp(p jsonb) RETURNS void LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN
 IF jsonb_typeof(p) IS DISTINCT FROM 'string' OR p#>>'{}' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN RAISE EXCEPTION 'Aware source timestamp required' USING ERRCODE='23514'; END IF;
 PERFORM (p#>>'{}')::timestamptz;
END $$;
CREATE FUNCTION inventory.fertilizer_form(p jsonb,e uuid) RETURNS void LANGUAGE plpgsql STABLE AS $$ BEGIN
 PERFORM inventory.fertilizer_typed(p,ARRAY['form_id','captured_at','source_kind','origin','original_text','inbox_id'],ARRAY['form_id','captured_at','source_kind','origin']);
 PERFORM inventory.fertilizer_stamp(p->'captured_at'); PERFORM (p->>'form_id')::uuid;
 IF p->>'source_kind' IS DISTINCT FROM 'OWNER_FORM' OR p->>'origin' NOT IN ('owner','agronomist')
 OR (jsonb_typeof(p->'original_text') NOT IN ('null','string')) OR length(p->>'original_text')>10000
 OR (jsonb_typeof(p->'inbox_id') NOT IN ('null','string'))
 OR p->>'inbox_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.inbox_items WHERE id=(p->>'inbox_id')::uuid AND entity_id=e)
 THEN RAISE EXCEPTION 'Exact same-entity form original required' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION inventory.fertilizer_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
  IF NEW.revision<>COALESCE(r.revision,0)+1 OR EXISTS(SELECT 1 FROM inventory.fertilizer_actuals WHERE plan_id=NEW.plan_id) THEN RAISE EXCEPTION 'New current draft revision required' USING ERRCODE='23514'; END IF;
  p=NEW.input;
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
  PERFORM inventory.fertilizer_typed(p,ARRAY['schema_version','source_kind','client_response_id','card_id','expected_revision','dispatch_id','response','captured_at','button_label','original_language','comment','quantities'],ARRAY['source_kind','client_response_id','card_id','dispatch_id','response','captured_at','button_label','original_language']);
  PERFORM inventory.fertilizer_stamp(p->'captured_at');
  IF p->>'source_kind' IS DISTINCT FROM 'UI_ACTION' OR p->'schema_version' IS DISTINCT FROM '1'::jsonb OR jsonb_typeof(p->'comment') NOT IN ('null','string') OR length(p->>'comment')>10000 THEN RAISE EXCEPTION 'Typed UI_ACTION original required' USING ERRCODE='23514'; END IF;
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
DO $$ DECLARE t text; BEGIN FOREACH t IN ARRAY ARRAY['reference_versions','plans','revisions','dispatches','reports'] LOOP
 EXECUTE format('CREATE TRIGGER fertilizer_guard BEFORE INSERT ON inventory.fertilizer_%I FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_guard()',t);
END LOOP; END $$;

CREATE FUNCTION inventory.fertilizer_card_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r inventory.fertilizer_revisions; c jsonb; ln jsonb; original jsonb; basis inventory.fertilizer_reference_versions; q numeric; calc numeric;
BEGIN
 PERFORM inventory.fertilizer_lock(NEW.entity_id,NEW.plan_id);
 SELECT * INTO r FROM inventory.fertilizer_revisions WHERE id=NEW.revision_id;
 SELECT value INTO original FROM jsonb_array_elements(r.input->'cards') WHERE value->>'card_id'=NEW.card_id::text;
 c=NEW.snapshot;
 IF (r.entity_id,r.plan_id) IS DISTINCT FROM (NEW.entity_id,NEW.plan_id) OR original IS NULL
 OR c IS DISTINCT FROM (SELECT value FROM jsonb_array_elements(r.preview->'cards') WHERE value->>'card_id'=NEW.card_id::text)
 OR (c->>'work_area_id')::uuid IS DISTINCT FROM NEW.work_area_id OR (c->>'worker_id')::uuid IS DISTINCT FROM NEW.worker_id
 OR c-ARRAY['components','greenhouse_name','part_name','worker_name','area_da','area_provenance','prepared_instruction','issues'] IS DISTINCT FROM original-'components'
 OR NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=NEW.work_area_id AND entity_id=NEW.entity_id AND parent_id=(r.input->>'greenhouse_id')::uuid)
 THEN RAISE EXCEPTION 'Card must match exact scoped plan input' USING ERRCODE='23514'; END IF;
 IF c->>'area_reference_id' IS NOT NULL THEN
  SELECT * INTO basis FROM inventory.fertilizer_reference_versions WHERE id=(c->>'area_reference_id')::uuid;
  IF basis.id IS NULL OR basis.entity_id<>NEW.entity_id OR basis.work_area_id<>NEW.work_area_id OR basis.effective_from>(r.input->>'event_date')::date OR basis.area_da IS NULL
  OR (c->>'area_da')::numeric IS DISTINCT FROM basis.area_da OR (c->'area_provenance') - ARRAY['area_da','created_at'] IS DISTINCT FROM to_jsonb(basis) - ARRAY['area_da','created_at'] OR (c->'area_provenance'->>'area_da')::numeric IS DISTINCT FROM basis.area_da OR (c->'area_provenance'->>'created_at')::timestamptz IS DISTINCT FROM basis.created_at
  THEN RAISE EXCEPTION 'Exact immutable area reference required' USING ERRCODE='23514'; END IF;
 ELSIF c->>'area_da' IS NOT NULL THEN RAISE EXCEPTION 'Unknown area cannot be invented' USING ERRCODE='23514'; END IF;
 IF NEW.worker_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workers.people WHERE id=NEW.worker_id AND entity_id=NEW.entity_id AND active AND language='fa') THEN RAISE EXCEPTION 'Worker scope mismatch' USING ERRCODE='23514'; END IF;
 IF c->>'worker_reference_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.fertilizer_reference_versions WHERE id=(c->>'worker_reference_id')::uuid AND entity_id=NEW.entity_id AND work_area_id IN (NEW.work_area_id,(r.input->>'greenhouse_id')::uuid) AND default_worker_id=NEW.worker_id AND effective_from<=(r.input->>'event_date')::date) THEN RAISE EXCEPTION 'Worker reference mismatch' USING ERRCODE='23514'; END IF;
 IF (SELECT count(*) FROM jsonb_array_elements(c->'components'))<>(SELECT count(DISTINCT x->>'component_id') FROM jsonb_array_elements(c->'components') x) THEN RAISE EXCEPTION 'Distinct non-null card component identity required' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(c->'components') NOT BETWEEN 1 AND 30 OR jsonb_array_length(c->'components')<>jsonb_array_length(original->'components') THEN RAISE EXCEPTION 'Card component set mismatch' USING ERRCODE='23514'; END IF;
 FOR ln IN SELECT value FROM jsonb_array_elements(c->'components') LOOP
  PERFORM inventory.fertilizer_typed(ln,ARRAY['component_id','product_id','location_id','dose_per_da','unit','quantity_override','override_reason','photo_source_id','product_name','location_name','calculated_quantity','proposed_quantity','quantity_basis'],ARRAY['component_id','product_id','unit','product_name','quantity_basis']);
  PERFORM (ln->>'component_id')::uuid;
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(original->'components') x WHERE x=ln-ARRAY['product_name','location_name','calculated_quantity','proposed_quantity','quantity_basis'])
  OR NOT EXISTS(SELECT 1 FROM inventory.products WHERE id=(ln->>'product_id')::uuid AND entity_id=NEW.entity_id AND active AND base_unit=ln->>'unit' AND name=ln->>'product_name')
  OR ln->>'unit' NOT IN ('kg','L')
  OR ln->>'location_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.locations WHERE id=(ln->>'location_id')::uuid AND entity_id=NEW.entity_id AND name=ln->>'location_name')
  OR ln->>'photo_source_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.inbox_items WHERE id=(ln->>'photo_source_id')::uuid AND entity_id=NEW.entity_id AND file_bytes IS NOT NULL AND mime_type IN ('image/png','image/jpeg'))
  THEN RAISE EXCEPTION 'Exact product/location/photo source required' USING ERRCODE='23514'; END IF;
  calc=NULL;q=NULL;
  IF ln->>'dose_per_da' IS NOT NULL THEN calc=inventory.fertilizer_numeric(ln->'dose_per_da')*basis.area_da; END IF;
  IF calc>=1e18 OR scale(trim_scale(calc))>12 THEN calc=NULL; END IF;
  IF (ln->>'calculated_quantity')::numeric IS DISTINCT FROM calc THEN RAISE EXCEPTION 'Exact dose times area required' USING ERRCODE='23514'; END IF;
  IF ln->>'quantity_override' IS NOT NULL THEN
   q=inventory.fertilizer_numeric(ln->'quantity_override');
   IF length(btrim(COALESCE(ln->>'override_reason','')))=0 THEN RAISE EXCEPTION 'Override reason required' USING ERRCODE='23514'; END IF;
  ELSE q=calc; END IF;
  IF basis.area_da IS NULL THEN q=NULL; END IF;
  IF ln->>'quantity_basis' IS DISTINCT FROM (CASE WHEN q IS NULL THEN 'UNKNOWN' WHEN ln->>'quantity_override' IS NOT NULL THEN 'OWNER_OVERRIDE' ELSE 'DOSE_TIMES_AREA' END) THEN RAISE EXCEPTION 'Quantity provenance mismatch' USING ERRCODE='23514'; END IF;
  IF (ln->>'proposed_quantity')::numeric IS DISTINCT FROM q THEN RAISE EXCEPTION 'Proposed quantity mismatch' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER fertilizer_card_guard BEFORE INSERT ON inventory.fertilizer_cards FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_card_guard();
CREATE FUNCTION inventory.fertilizer_plan_finality() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM inventory.fertilizer_revisions WHERE plan_id=NEW.id AND revision=1) THEN RAISE EXCEPTION 'Plan requires initial immutable revision' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
CREATE CONSTRAINT TRIGGER fertilizer_plan_finality AFTER INSERT ON inventory.fertilizer_plans DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_plan_finality();
CREATE FUNCTION inventory.fertilizer_revision_finality() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE n integer; BEGIN
 SELECT count(*) INTO n FROM inventory.fertilizer_cards WHERE revision_id=NEW.id;
 IF n<>jsonb_array_length(NEW.input->'cards') OR n<>(SELECT count(DISTINCT value->>'card_id') FROM jsonb_array_elements(NEW.input->'cards'))
 OR (SELECT sum(jsonb_array_length(snapshot->'components')) FROM inventory.fertilizer_cards WHERE revision_id=NEW.id)>100
 THEN RAISE EXCEPTION 'Plan revision must commit all exact cards atomically' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE CONSTRAINT TRIGGER fertilizer_revision_finality AFTER INSERT ON inventory.fertilizer_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_revision_finality();

CREATE FUNCTION inventory.fertilizer_actual_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r inventory.fertilizer_revisions; c jsonb; ln jsonb; comp jsonb; rep inventory.fertilizer_reports; ev inventory.events; expected jsonb='[]'; inputline jsonb; q numeric; proposed numeric; cid text; cardrows integer; positives integer;
BEGIN
 PERFORM inventory.fertilizer_lock(NEW.entity_id,NEW.plan_id);
 SELECT * INTO r FROM inventory.fertilizer_revisions WHERE plan_id=NEW.plan_id ORDER BY revision DESC LIMIT 1;
 SELECT * INTO ev FROM inventory.events WHERE id=NEW.application_id;
 PERFORM inventory.fertilizer_typed(NEW.source_snapshot,ARRAY['input','actual_preview_digest','confirmed'],ARRAY['actual_preview_digest']);
 PERFORM inventory.fertilizer_typed(NEW.input,ARRAY['source','expected_version','revision','event_date','duplicate_reviewed','lines','completed_card_ids'],ARRAY['event_date']);
 PERFORM inventory.fertilizer_form(NEW.input->'source',NEW.entity_id);
 PERFORM inventory.fertilizer_positive_int(NEW.input->'expected_version'); PERFORM inventory.fertilizer_positive_int(NEW.input->'revision');
 IF jsonb_typeof(NEW.input->'duplicate_reviewed') IS DISTINCT FROM 'boolean' THEN RAISE EXCEPTION 'Duplicate review flag required' USING ERRCODE='23514'; END IF;
 IF NOT core.r2_owner(NEW.entity_id,NEW.created_by) OR r.id IS DISTINCT FROM NEW.revision_id
 OR NEW.source_snapshot->'input' IS DISTINCT FROM NEW.input OR NEW.source_snapshot->'confirmed' IS DISTINCT FROM 'true'::jsonb
 OR NEW.source_snapshot->>'actual_preview_digest' IS DISTINCT FROM NEW.preview_digest
 OR (NEW.input->>'expected_version')::int IS DISTINCT FROM inventory.fertilizer_version(NEW.plan_id) OR (NEW.input->>'revision')::int IS DISTINCT FROM r.revision
 OR NEW.input->'source'->>'source_kind' IS DISTINCT FROM 'OWNER_FORM' OR (NEW.input->'source'->>'form_id')::uuid IS DISTINCT FROM NEW.source_id
 OR (ev.entity_id,ev.kind,ev.status,ev.source,ev.locator,ev.event_date,ev.created_by,ev.posted_by) IS DISTINCT FROM (NEW.entity_id,'application'::text,'posted'::text,'fertilizer:actual'::text,NEW.source_id::text,(NEW.input->>'event_date')::date,NEW.created_by,NEW.created_by)
 OR ev.duplicate_reviewed IS DISTINCT FROM (NEW.input->>'duplicate_reviewed')::boolean
 OR ev.evidence->>'source_document' IS DISTINCT FROM 'fertilizer-plan:'||NEW.plan_id::text
 OR ev.replacement_of_id IS NOT NULL OR ev.payload->>'supplier_id' IS NOT NULL OR ev.payload->>'finance_obligation_id' IS NOT NULL
 OR ev.payload->>'method' IS NOT NULL OR ev.payload->>'notes' IS NOT NULL
 THEN RAISE EXCEPTION 'Exact current owner actual/application required' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(NEW.input->'lines') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.input->'lines') NOT BETWEEN 1 AND 100
 OR jsonb_array_length(NEW.input->'lines')<>(SELECT count(DISTINCT (x->>'card_id',x->>'component_id')) FROM jsonb_array_elements(NEW.input->'lines') x)
 THEN RAISE EXCEPTION 'Distinct selected actual scope required' USING ERRCODE='23514'; END IF;
 FOR ln IN SELECT value FROM jsonb_array_elements(NEW.input->'lines') LOOP
  PERFORM inventory.fertilizer_typed(ln,ARRAY['card_id','component_id','report_id','quantity','disposition','reason'],ARRAY['card_id','component_id','report_id','quantity','disposition']);
  SELECT snapshot INTO c FROM inventory.fertilizer_cards WHERE revision_id=r.id AND card_id=(ln->>'card_id')::uuid;
  SELECT value INTO comp FROM jsonb_array_elements(c->'components') WHERE value->>'component_id'=ln->>'component_id';
  SELECT * INTO rep FROM inventory.fertilizer_reports WHERE revision_id=r.id AND card_id=(ln->>'card_id')::uuid ORDER BY received_at DESC,id DESC LIMIT 1;
  IF c IS NULL OR comp IS NULL OR rep.id IS NULL OR rep.id IS DISTINCT FROM (ln->>'report_id')::uuid THEN RAISE EXCEPTION 'Actual requires current exact card/report/component' USING ERRCODE='23514'; END IF;
  IF jsonb_typeof(ln->'reason') NOT IN ('null','string') OR length(ln->>'reason')>2000 THEN RAISE EXCEPTION 'Exact optional owner reason required' USING ERRCODE='23514'; END IF;
  q=inventory.fertilizer_numeric(ln->'quantity',true);
  IF (q>0) IS DISTINCT FROM (ln->>'disposition'='APPLIED') OR ln->>'disposition' NOT IN ('APPLIED','NOT_APPLIED') THEN RAISE EXCEPTION 'Explicit zero differs from unknown' USING ERRCODE='23514'; END IF;
  proposed=NULL;
  SELECT replace(x->>'quantity',',','.')::numeric INTO proposed FROM jsonb_array_elements(rep.source_snapshot->'quantities') x WHERE x->>'component_id'=ln->>'component_id';
  IF rep.response='DONE' THEN proposed=(comp->>'proposed_quantity')::numeric; END IF;
  IF (q=0 OR rep.response<>'DONE' OR proposed IS NULL OR q IS DISTINCT FROM proposed) AND length(btrim(COALESCE(ln->>'reason','')))=0 THEN RAISE EXCEPTION 'Owner discrepancy/zero resolution required' USING ERRCODE='23514'; END IF;
  IF q>0 THEN
   expected=expected||jsonb_build_array(jsonb_build_object('product_id',comp->>'product_id','original_product_name',comp->>'product_name','quantity',q,'unit',comp->>'unit','location_id',comp->>'location_id','greenhouse_id',r.input->>'greenhouse_id','work_area_id',c->>'work_area_id'));
  END IF;
 END LOOP;
 IF jsonb_array_length(expected)=0 OR jsonb_array_length(expected)<>jsonb_array_length(ev.payload->'lines') THEN RAISE EXCEPTION 'Positive selected actual lines required; all-zero unsupported' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(ev.payload->'lines') x WHERE x->>'unit_cost' IS NOT NULL OR x->>'currency' IS NOT NULL OR x->>'destination_id' IS NOT NULL OR x->>'original_quantity' IS NOT NULL OR x->>'original_unit' IS NOT NULL) THEN RAISE EXCEPTION 'No undeclared actual valuation or destination facts' USING ERRCODE='23514'; END IF;
 IF (SELECT jsonb_agg(jsonb_build_object('product_id',x->>'product_id','original_product_name',x->>'original_product_name','quantity',(x->>'quantity')::numeric,'unit',x->>'unit','location_id',x->>'location_id','greenhouse_id',x->>'greenhouse_id','work_area_id',x->>'work_area_id') ORDER BY n) FROM jsonb_array_elements(ev.payload->'lines') WITH ORDINALITY z(x,n)) IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Application differs from selected owner actual' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(NEW.input->'completed_card_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.input->'completed_card_ids')<>(SELECT count(DISTINCT value) FROM jsonb_array_elements(NEW.input->'completed_card_ids')) THEN RAISE EXCEPTION 'Distinct completed card scope required' USING ERRCODE='23514'; END IF;
 FOR cid IN SELECT jsonb_array_elements_text(NEW.input->'completed_card_ids') LOOP
  SELECT jsonb_array_length(snapshot->'components') INTO cardrows FROM inventory.fertilizer_cards WHERE revision_id=r.id AND card_id=cid::uuid;
  SELECT count(*) INTO positives FROM jsonb_array_elements(NEW.input->'lines') x WHERE x->>'card_id'=cid AND x->>'disposition'='APPLIED';
  IF cardrows IS NULL OR positives<>cardrows THEN RAISE EXCEPTION 'Completed card requires every positive component' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER fertilizer_actual_guard BEFORE INSERT ON inventory.fertilizer_actuals FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_actual_guard();
CREATE FUNCTION inventory.fertilizer_application_finality() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE v inventory.events; BEGIN
 SELECT * INTO v FROM inventory.events WHERE id=NEW.id;
 IF v.source='fertilizer:actual' AND NOT EXISTS(WITH RECURSIVE lineage AS (SELECT id,replacement_of_id FROM inventory.events WHERE id=v.id UNION ALL SELECT e.id,e.replacement_of_id FROM inventory.events e JOIN lineage l ON e.id=l.replacement_of_id) SELECT 1 FROM inventory.fertilizer_actuals a JOIN lineage l ON l.id=a.application_id WHERE a.entity_id=v.entity_id AND a.source_id::text=v.locator AND core.r2_owner(v.entity_id,a.created_by))
 THEN RAISE EXCEPTION 'Fertilizer application requires its exact owner approval atomically' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE CONSTRAINT TRIGGER fertilizer_application_finality AFTER INSERT OR UPDATE ON inventory.events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inventory.fertilizer_application_finality();
""")

def downgrade():raise RuntimeError('0029 is forward-only: restore verified backup and reconcile immutable plan/report/application history')
