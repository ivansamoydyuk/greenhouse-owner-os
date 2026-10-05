"""Inventory lifecycle hardening; no historical cost assignment.

Atomic preflight refuses conflicting source roots, with IDs for reconciliation.
Recovery: retain/restore a verified pre-upgrade backup separately and explicitly
reconcile originals, currency establishments, movements and adapter links made
since it. Never delete/merge events to force upgrade; forward-only after use.
"""
from alembic import op
revision='20261006_0018'
down_revision='20261005_0017'
branch_labels=depends_on=None

def upgrade():
    op.execute(r'''
LOCK TABLE inventory.events IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE conflicts text; BEGIN
 SELECT string_agg(ids, '; ') INTO conflicts FROM (
  SELECT string_agg(id::text, ', ' ORDER BY id) ids FROM inventory.events
  WHERE locator IS NOT NULL AND replacement_of_id IS NULL
  GROUP BY entity_id,source,locator HAVING count(*)>1 LIMIT 10
 ) c;
 IF conflicts IS NOT NULL THEN
  RAISE EXCEPTION 'Inventory source reconciliation required before 0018; conflicting root IDs: %', conflicts USING ERRCODE='23514';
 END IF;
END $$;
DROP INDEX inventory.event_source_root;
CREATE UNIQUE INDEX event_source_root ON inventory.events(entity_id,source,locator)
 WHERE locator IS NOT NULL AND replacement_of_id IS NULL;
CREATE TABLE inventory.currency_establishments (
 product_id uuid PRIMARY KEY REFERENCES inventory.products(id),
 currency text NOT NULL CHECK(currency IN ('TRY','USD','EUR','UAH')),
 verification text NOT NULL CHECK(length(btrim(verification)) BETWEEN 1 AND 2000),
 established_by uuid NOT NULL REFERENCES core.actors(id),
 established_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION inventory.guard_currency_establishment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p inventory.products%ROWTYPE; BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Основание валюты оценки неизменно' USING ERRCODE='55000'; END IF;
 -- Same lock order as physical commands, then product row lock.
 SELECT * INTO p FROM inventory.products WHERE id=NEW.product_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Неизвестный товар' USING ERRCODE='23503'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('inventory:'||p.entity_id::text,0));
 SELECT * INTO p FROM inventory.products WHERE id=NEW.product_id FOR UPDATE;
 IF p.currency IS NOT NULL THEN RAISE EXCEPTION 'Валюта оценки уже установлена и не может быть изменена' USING ERRCODE='55000'; END IF;
 NEW.established_at=clock_timestamp(); RETURN NEW;
END $$;
CREATE TRIGGER currency_establishment_guard BEFORE INSERT OR UPDATE OR DELETE ON inventory.currency_establishments
 FOR EACH ROW EXECUTE FUNCTION inventory.guard_currency_establishment();
CREATE FUNCTION inventory.apply_currency_establishment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 UPDATE inventory.products SET currency=NEW.currency WHERE id=NEW.product_id;
 RETURN NEW;
END $$;
CREATE TRIGGER currency_establishment_apply AFTER INSERT ON inventory.currency_establishments
 FOR EACH ROW EXECUTE FUNCTION inventory.apply_currency_establishment();
CREATE OR REPLACE FUNCTION inventory.guard_master() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN
  IF TG_TABLE_NAME='products' AND TG_OP='UPDATE' THEN
   IF to_jsonb(NEW)-ARRAY['active','updated_at']=to_jsonb(OLD)-ARRAY['active','updated_at'] THEN NEW.updated_at=clock_timestamp(); RETURN NEW; END IF;
   IF OLD.currency IS NULL AND NEW.currency IS NOT NULL AND pg_trigger_depth()>1
      AND to_jsonb(NEW)-ARRAY['currency','updated_at']=to_jsonb(OLD)-ARRAY['currency','updated_at']
      AND EXISTS(SELECT 1 FROM inventory.currency_establishments WHERE product_id=OLD.id AND currency=NEW.currency) THEN
      NEW.updated_at=clock_timestamp(); RETURN NEW;
   END IF;
   RAISE EXCEPTION 'Валюта оценки устанавливается один раз отдельной командой; изменение запрещено' USING ERRCODE='55000';
  END IF;
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
    ''' )

def downgrade():
    raise RuntimeError('Forward-only: restore verified backup and reconcile preserved originals, currency establishments and movements')
