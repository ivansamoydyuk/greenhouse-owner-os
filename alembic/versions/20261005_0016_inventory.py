"""Authoritative physical ledger; managerial valuation, never Finance postings.

Forward-only after use: restore a verified backup separately, preserve/reconcile
subsequent evidence, movements and domain links. No factual opening stock seeded.
"""
from alembic import op
revision='20261005_0016'
down_revision='20261005_0015'
branch_labels=depends_on=None

def upgrade():
    op.execute(r'''
CREATE SCHEMA inventory;
CREATE TABLE inventory.products (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id),
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 200), aliases jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(aliases)='array'),
 category text NOT NULL CHECK(category IN ('fertilizer','biostimulant','biological','crop_protection','other')),
 base_unit text NOT NULL CHECK(base_unit IN ('kg','L','piece')), active boolean NOT NULL DEFAULT true,
 supplier_reference text, currency text CHECK(currency IN ('TRY','USD','EUR','UAH')),
 source text NOT NULL CHECK(length(btrim(source)) BETWEEN 1 AND 2000), created_by uuid NOT NULL REFERENCES core.actors(id),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(entity_id,name));
CREATE TABLE inventory.conversions (
 id uuid PRIMARY KEY, product_id uuid NOT NULL REFERENCES inventory.products(id), unit text NOT NULL CHECK(unit IN ('bag','container','package','kg','L','piece')),
 factor numeric NOT NULL CHECK(factor>0 AND factor<1e18 AND scale(factor)<=12),
 verification text NOT NULL CHECK(length(btrim(verification)) BETWEEN 1 AND 2000), created_by uuid NOT NULL REFERENCES core.actors(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(product_id,unit));
CREATE TABLE inventory.locations (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id), name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 200),
 work_area_id uuid REFERENCES core.work_areas(id), source text NOT NULL CHECK(length(btrim(source)) BETWEEN 1 AND 2000),
 created_by uuid NOT NULL REFERENCES core.actors(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(entity_id,name));
CREATE TABLE inventory.events (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id), kind text NOT NULL CHECK(kind IN ('receipt','application','transfer','writeoff','plan')),
 event_date date, payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object'), source text NOT NULL CHECK(length(source) BETWEEN 1 AND 200),
 locator text CHECK(length(locator) BETWEEN 1 AND 2000), replacement_of_id uuid UNIQUE REFERENCES inventory.events(id),
 duplicate_reviewed boolean NOT NULL DEFAULT false, status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted','reversed')), version integer NOT NULL DEFAULT 1 CHECK(version>0),
 created_by uuid NOT NULL REFERENCES core.actors(id), created_at timestamptz NOT NULL DEFAULT now(),
 posted_by uuid REFERENCES core.actors(id), posted_at timestamptz, reversed_by uuid REFERENCES core.actors(id), reversed_at timestamptz, reversal_event_date date, reversal_reason text,
 CHECK((status<>'draft')=(posted_by IS NOT NULL AND posted_at IS NOT NULL)),
 CHECK((status='reversed')=(reversed_by IS NOT NULL AND reversed_at IS NOT NULL AND length(btrim(reversal_reason))>0)));
CREATE UNIQUE INDEX event_source_root ON inventory.events(entity_id,source,locator,kind) WHERE locator IS NOT NULL AND replacement_of_id IS NULL;
CREATE TABLE inventory.movements (
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, event_id uuid NOT NULL REFERENCES inventory.events(id),
 entity_id uuid NOT NULL REFERENCES core.entities(id), product_id uuid NOT NULL REFERENCES inventory.products(id), location_id uuid NOT NULL REFERENCES inventory.locations(id),
 line_no integer NOT NULL CHECK(line_no>=0), role text NOT NULL CHECK(role IN ('receipt','application','writeoff','transfer_out','transfer_in','reversal')),
 quantity numeric NOT NULL CHECK(quantity<>0 AND abs(quantity)<1e18 AND scale(quantity)<=12),
 uncosted_quantity numeric NOT NULL CHECK(abs(uncosted_quantity)<=abs(quantity) AND quantity*uncosted_quantity>=0),
 known_cost numeric NOT NULL CHECK(quantity*known_cost>=0),
 greenhouse_id uuid REFERENCES core.work_areas(id), work_area_id uuid REFERENCES core.work_areas(id),
 reversal_of_seq bigint UNIQUE REFERENCES inventory.movements(seq), UNIQUE(event_id,line_no,role,location_id));
CREATE VIEW inventory.stock AS SELECT entity_id,product_id,location_id,sum(quantity) quantity,sum(uncosted_quantity) uncosted_quantity,sum(known_cost) known_cost FROM inventory.movements GROUP BY entity_id,product_id,location_id;
CREATE FUNCTION inventory.guard_master() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN
  IF TG_TABLE_NAME='products' AND TG_OP='UPDATE' AND to_jsonb(NEW)-ARRAY['active','updated_at']=to_jsonb(OLD)-ARRAY['active','updated_at'] THEN NEW.updated_at=clock_timestamp(); RETURN NEW; END IF;
  RAISE EXCEPTION 'Inventory references are immutable; only active state may change' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='locations' THEN
 IF NEW.work_area_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=NEW.work_area_id AND entity_id=NEW.entity_id) THEN RAISE EXCEPTION 'Location scope mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='products' THEN
 PERFORM pg_advisory_xact_lock(hashtextextended('inventory:'||NEW.entity_id::text,0));
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.aliases) a WHERE jsonb_typeof(a)<>'string' OR length(btrim(a#>>'{}')) NOT BETWEEN 1 AND 200) OR EXISTS(SELECT 1 FROM inventory.products p WHERE p.entity_id=NEW.entity_id AND (lower(p.name)=lower(NEW.name) OR p.aliases @> jsonb_build_array(NEW.name) OR NEW.aliases @> jsonb_build_array(p.name) OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p.aliases) a JOIN jsonb_array_elements_text(NEW.aliases) b ON lower(a)=lower(b)))) THEN RAISE EXCEPTION 'Product alias collision' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='conversions' THEN
  IF NEW.unit=(SELECT base_unit FROM inventory.products WHERE id=NEW.product_id) OR (NEW.unit IN ('kg','L') AND (SELECT base_unit FROM inventory.products WHERE id=NEW.product_id) NOT IN ('kg','L')) THEN RAISE EXCEPTION 'Invalid conversion' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER master BEFORE INSERT OR UPDATE OR DELETE ON inventory.products FOR EACH ROW EXECUTE FUNCTION inventory.guard_master();
CREATE TRIGGER master BEFORE INSERT OR UPDATE OR DELETE ON inventory.locations FOR EACH ROW EXECUTE FUNCTION inventory.guard_master();
CREATE TRIGGER master BEFORE INSERT OR UPDATE OR DELETE ON inventory.conversions FOR EACH ROW EXECUTE FUNCTION inventory.guard_master();
CREATE FUNCTION inventory.guard_movement() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ev inventory.events%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' OR pg_trigger_depth()<2 THEN RAISE EXCEPTION 'Movements are generated only by event lifecycle and cannot be edited' USING ERRCODE='55000'; END IF;
 SELECT * INTO ev FROM inventory.events WHERE id=NEW.event_id;
 IF ev.entity_id<>NEW.entity_id OR NOT EXISTS(SELECT 1 FROM inventory.products WHERE id=NEW.product_id AND entity_id=NEW.entity_id) OR NOT EXISTS(SELECT 1 FROM inventory.locations WHERE id=NEW.location_id AND entity_id=NEW.entity_id) THEN RAISE EXCEPTION 'Movement scope mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER movement_guard BEFORE INSERT OR UPDATE OR DELETE ON inventory.movements FOR EACH ROW EXECUTE FUNCTION inventory.guard_movement();
CREATE FUNCTION inventory.guard_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ln jsonb; idx integer; p inventory.products%ROWTYPE; q numeric; factor numeric; loc uuid; dest uuid; gid uuid; aid uuid;
 qty numeric; unc numeric; cost numeric; uc numeric; val numeric; sign integer; m inventory.movements%ROWTYPE; prior inventory.events%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('inventory:'||COALESCE(NEW.entity_id,OLD.entity_id)::text,0));
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Inventory history is immutable' USING ERRCODE='55000'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.source IS DISTINCT FROM NEW.evidence->>'source' OR NEW.locator IS DISTINCT FROM NEW.evidence->>'locator' THEN RAISE EXCEPTION 'Source identity mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.locator IS NOT NULL AND EXISTS(SELECT 1 FROM inventory.events ev WHERE ev.entity_id=NEW.entity_id AND ev.source=NEW.source AND ev.locator=NEW.locator AND ev.evidence IS DISTINCT FROM NEW.evidence) THEN RAISE EXCEPTION 'Stable source original changed' USING ERRCODE='23514'; END IF;
  IF jsonb_typeof(NEW.payload->'lines') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.payload->'lines') NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'Product lines required' USING ERRCODE='23514'; END IF;
  FOR ln IN SELECT value FROM jsonb_array_elements(NEW.payload->'lines') LOOP
   IF ln->>'product_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.products WHERE id=(ln->>'product_id')::uuid AND entity_id=NEW.entity_id) OR ln->>'location_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.locations WHERE id=(ln->>'location_id')::uuid AND entity_id=NEW.entity_id) OR ln->>'destination_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM inventory.locations WHERE id=(ln->>'destination_id')::uuid AND entity_id=NEW.entity_id) OR ln->>'greenhouse_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=(ln->>'greenhouse_id')::uuid AND entity_id=NEW.entity_id AND kind='greenhouse') OR ln->>'work_area_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=(ln->>'work_area_id')::uuid AND entity_id=NEW.entity_id AND parent_id=(ln->>'greenhouse_id')::uuid) THEN RAISE EXCEPTION 'Inventory draft reference scope mismatch' USING ERRCODE='23514'; END IF;
  END LOOP;
  IF NEW.payload->>'supplier_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.counterparties WHERE id=(NEW.payload->>'supplier_id')::uuid AND entity_id=NEW.entity_id) OR NEW.payload->>'finance_obligation_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM finance.obligations WHERE id=(NEW.payload->>'finance_obligation_id')::uuid AND entity_id=NEW.entity_id AND direction='payable') OR NEW.evidence->>'import_batch_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.import_batches WHERE id=(NEW.evidence->>'import_batch_id')::uuid AND entity_id=NEW.entity_id) THEN RAISE EXCEPTION 'Inventory provenance/link scope mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.status<>'draft' OR NEW.version<>1 OR NEW.duplicate_reviewed THEN RAISE EXCEPTION 'Start with a draft' USING ERRCODE='23514'; END IF;
  IF NEW.replacement_of_id IS NOT NULL THEN
   SELECT * INTO prior FROM inventory.events WHERE id=NEW.replacement_of_id;
   IF NOT FOUND OR prior.entity_id<>NEW.entity_id OR prior.kind<>NEW.kind OR prior.status NOT IN ('draft','reversed') OR (prior.source,prior.locator,prior.evidence) IS DISTINCT FROM (NEW.source,NEW.locator,NEW.evidence) THEN RAISE EXCEPTION 'Explicit replacement requires draft/reversed original and same evidence' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF to_jsonb(NEW)-ARRAY['status','version','posted_by','posted_at','reversed_by','reversed_at','reversal_reason','reversal_event_date','duplicate_reviewed'] IS DISTINCT FROM to_jsonb(OLD)-ARRAY['status','version','posted_by','posted_at','reversed_by','reversed_at','reversal_reason','reversal_event_date','duplicate_reviewed'] OR NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'Original event cannot be rewritten' USING ERRCODE='55000'; END IF;
 IF OLD.status='posted' AND NEW.status='reversed' THEN
  IF (NEW.posted_by,NEW.posted_at,NEW.duplicate_reviewed) IS DISTINCT FROM (OLD.posted_by,OLD.posted_at,OLD.duplicate_reviewed) OR NEW.reversed_by IS NULL OR NEW.reversed_at IS NULL OR NEW.reversal_event_date IS NULL OR NEW.reversal_event_date<NEW.event_date OR length(btrim(coalesce(NEW.reversal_reason,'')))=0 THEN RAISE EXCEPTION 'Reversal requires actor/reason' USING ERRCODE='23514'; END IF;
  -- Strict R1 dependency rule: unwind later active movements on every affected
  -- product/location before reversing. No replay/revaluation of later history.
  IF EXISTS(SELECT 1 FROM inventory.movements a JOIN inventory.movements b ON (a.entity_id,a.product_id,a.location_id)=(b.entity_id,b.product_id,b.location_id) JOIN inventory.events e ON e.id=b.event_id WHERE a.event_id=OLD.id AND b.seq>a.seq AND b.event_id<>OLD.id AND e.status='posted') THEN RAISE EXCEPTION 'Сначала отмените последующие движения этого товара на складе' USING ERRCODE='23514'; END IF;
  FOR m IN SELECT * FROM inventory.movements WHERE event_id=OLD.id ORDER BY seq LOOP
   INSERT INTO inventory.movements(event_id,entity_id,product_id,location_id,line_no,role,quantity,uncosted_quantity,known_cost,greenhouse_id,work_area_id,reversal_of_seq)
   VALUES(NEW.id,NEW.entity_id,m.product_id,m.location_id,m.line_no,'reversal',-m.quantity,-m.uncosted_quantity,-m.known_cost,m.greenhouse_id,m.work_area_id,m.seq);
  END LOOP;
  RETURN NEW;
 END IF;
 IF EXISTS(SELECT 1 FROM inventory.events WHERE replacement_of_id=NEW.id) THEN RAISE EXCEPTION 'Черновик заменён: откройте исправленную версию' USING ERRCODE='23514'; END IF;
 IF OLD.status<>'draft' OR NEW.status<>'posted' OR NEW.kind='plan' OR NEW.posted_by IS NULL OR NEW.posted_at IS NULL OR NEW.event_date IS NULL THEN RAISE EXCEPTION 'Invalid inventory lifecycle; plans have no physical effect' USING ERRCODE='23514'; END IF;
 IF NEW.replacement_of_id IS NULL AND NOT NEW.duplicate_reviewed AND EXISTS(SELECT 1 FROM inventory.events ev WHERE ev.id<>NEW.id AND ev.entity_id=NEW.entity_id AND ev.kind=NEW.kind AND (ev.event_date IS NOT DISTINCT FROM NEW.event_date AND ev.payload=NEW.payload OR NEW.evidence->>'raw_text' IS NOT NULL AND NEW.evidence->>'raw_text'=ev.evidence->>'raw_text' OR NEW.evidence->>'file_base64' IS NOT NULL AND NEW.evidence->>'file_base64'=ev.evidence->>'file_base64')) THEN RAISE EXCEPTION 'Проверьте возможный дубль и явно подтвердите отдельное событие' USING ERRCODE='23514'; END IF;
 IF NEW.evidence->>'verification'='HISTORY' THEN RAISE EXCEPTION 'Historical evidence is non-posting' USING ERRCODE='23514'; END IF;
 IF NEW.event_date < (SELECT finance_cutover_date FROM core.entities WHERE id=NEW.entity_id) THEN RAISE EXCEPTION 'Historical inventory requires explicit verified opening/import workflow' USING ERRCODE='23514'; END IF;
 IF NEW.payload->>'supplier_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.counterparties WHERE id=(NEW.payload->>'supplier_id')::uuid AND entity_id=NEW.entity_id) THEN RAISE EXCEPTION 'Supplier scope mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.payload->>'finance_obligation_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM finance.obligations WHERE id=(NEW.payload->>'finance_obligation_id')::uuid AND entity_id=NEW.entity_id AND direction='payable') THEN RAISE EXCEPTION 'Finance linkage mismatch' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(NEW.payload->'lines') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.payload->'lines') NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'Product lines required' USING ERRCODE='23514'; END IF;
 idx=0;
 FOR ln IN SELECT value FROM jsonb_array_elements(NEW.payload->'lines') LOOP
  IF length(btrim(coalesce(ln->>'original_product_name',''))) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'Original product name required' USING ERRCODE='23514'; END IF;
  SELECT * INTO p FROM inventory.products WHERE id=(ln->>'product_id')::uuid AND entity_id=NEW.entity_id AND active;
  IF NOT FOUND THEN RAISE EXCEPTION 'Выберите известный активный товар' USING ERRCODE='23514'; END IF;
  IF jsonb_typeof(ln->'quantity') IS DISTINCT FROM 'string' OR (ln->>'quantity') !~ '^[0-9]{1,18}(\.[0-9]{1,12})?$' THEN RAISE EXCEPTION 'Exact quantity string required' USING ERRCODE='23514'; END IF;
  q=(ln->>'quantity')::numeric;factor=1;
  IF ln->>'unit' IS DISTINCT FROM p.base_unit THEN
   SELECT c.factor INTO factor FROM inventory.conversions c WHERE c.product_id=p.id AND c.unit=ln->>'unit';
   IF NOT FOUND THEN RAISE EXCEPTION 'Нет проверенного пересчёта упаковки или плотности' USING ERRCODE='23514'; END IF;
  END IF;
  q=q*factor;
  IF q<=0 OR q>=1e18 OR scale(q)>12 THEN RAISE EXCEPTION 'Invalid base quantity precision' USING ERRCODE='23514'; END IF;
  loc=(ln->>'location_id')::uuid;dest=(ln->>'destination_id')::uuid;
  IF loc IS NULL OR NOT EXISTS(SELECT 1 FROM inventory.locations WHERE id=loc AND entity_id=NEW.entity_id) THEN RAISE EXCEPTION 'Выберите склад' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM inventory.movements later JOIN inventory.events ev ON ev.id=later.event_id WHERE later.entity_id=NEW.entity_id AND later.product_id=p.id AND later.location_id IN (loc,dest) AND ev.status='posted' AND ev.event_date>NEW.event_date) THEN RAISE EXCEPTION 'Поздняя операция требует явной отмены последующих движений и пересчёта' USING ERRCODE='23514'; END IF;
  gid=(ln->>'greenhouse_id')::uuid;aid=(ln->>'work_area_id')::uuid;
  IF gid IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=gid AND entity_id=NEW.entity_id AND kind='greenhouse') THEN RAISE EXCEPTION 'Greenhouse scope mismatch' USING ERRCODE='23514'; END IF;
  IF aid IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=aid AND entity_id=NEW.entity_id AND parent_id=gid) THEN RAISE EXCEPTION 'Work area requires verified greenhouse parent' USING ERRCODE='23514'; END IF;
  IF NEW.kind='application' AND gid IS NULL THEN RAISE EXCEPTION 'Выберите теплицу внесения' USING ERRCODE='23514'; END IF;
  IF NEW.kind='transfer' AND (dest IS NULL OR dest=loc OR NOT EXISTS(SELECT 1 FROM inventory.locations WHERE id=dest AND entity_id=NEW.entity_id)) THEN RAISE EXCEPTION 'Выберите другой склад назначения' USING ERRCODE='23514'; END IF;
  IF NEW.kind='receipt' THEN
   IF ln->>'unit_cost' IS NULL THEN unc=q;cost=0;
   ELSE
    IF jsonb_typeof(ln->'unit_cost')<>'string' OR (ln->>'unit_cost') !~ '^[0-9]{1,18}(\.[0-9]{1,12})?$' OR p.currency IS NULL OR ln->>'currency' IS DISTINCT FROM p.currency THEN RAISE EXCEPTION 'Known cost requires exact base-unit cost and product valuation currency' USING ERRCODE='23514'; END IF;
    unc=0;cost=q*(ln->>'unit_cost')::numeric;
   END IF;
   sign=1;
  ELSE
   SELECT coalesce(sum(quantity),0),coalesce(sum(uncosted_quantity),0),coalesce(sum(known_cost),0) INTO qty,uc,val FROM inventory.movements WHERE entity_id=NEW.entity_id AND product_id=p.id AND location_id=loc;
   IF qty<q THEN RAISE EXCEPTION 'Недостаточно товара на выбранном складе' USING ERRCODE='23514'; END IF;
   -- Rounded quantities have a 12-decimal contract; consume the final residue
   -- exactly when exhausting a pool. Cost uses numeric, never float.
   unc=CASE WHEN q=qty THEN uc ELSE round(q*uc/qty,12) END;
   cost=CASE WHEN q=qty THEN val ELSE round(q*val/qty,12) END;
   sign=-1;
  END IF;
  INSERT INTO inventory.movements(event_id,entity_id,product_id,location_id,line_no,role,quantity,uncosted_quantity,known_cost,greenhouse_id,work_area_id)
  VALUES(NEW.id,NEW.entity_id,p.id,loc,idx,CASE WHEN NEW.kind='transfer' THEN 'transfer_out' ELSE NEW.kind END,sign*q,sign*unc,sign*cost,gid,aid);
  IF NEW.kind='transfer' THEN
   INSERT INTO inventory.movements(event_id,entity_id,product_id,location_id,line_no,role,quantity,uncosted_quantity,known_cost)
   VALUES(NEW.id,NEW.entity_id,p.id,dest,idx,'transfer_in',q,unc,cost);
  END IF;
  idx=idx+1;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER event_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON inventory.events FOR EACH ROW EXECUTE FUNCTION inventory.guard_event();
''')

def downgrade():
    raise RuntimeError('Forward-only: verified backup plus preserved evidence/movement reconciliation required')
