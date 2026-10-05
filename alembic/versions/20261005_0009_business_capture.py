"""Business basis and global non-posting capture.

Forward-only after use: restore a verified backup separately, retain/reconcile
subsequent immutable journals, attributions and original capture evidence.
No owner transactions/references are seeded by migrations.
"""
from alembic import op
revision='20261005_0009'
down_revision='20261004_0008'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""
    ALTER TABLE finance.obligations ADD COLUMN obligation_class varchar(20) NOT NULL DEFAULT 'OPERATIONAL',
      ADD COLUMN economic_source varchar(30) NOT NULL DEFAULT 'manual_other', ADD COLUMN description text,
      ADD CONSTRAINT obligation_business_class CHECK (obligation_class IN ('OPERATIONAL','STRATEGIC') AND (obligation_class<>'STRATEGIC' OR direction='payable')),
      ADD CONSTRAINT obligation_economic_source CHECK (economic_source IN ('manual_other','harvest_depot'));
    CREATE TABLE core.work_areas (
      id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id),
      name text NOT NULL CHECK (btrim(name)<>''), parent_id uuid REFERENCES core.work_areas(id),
      kind varchar(20) NOT NULL CHECK (kind IN ('greenhouse','work_area')),
      source text NOT NULL CHECK (btrim(source)<>''), UNIQUE(entity_id,name),
      CHECK ((kind='greenhouse' AND parent_id IS NULL) OR (kind='work_area' AND parent_id IS NOT NULL)));
    CREATE FUNCTION core.guard_work_area() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.parent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM core.work_areas WHERE id=NEW.parent_id AND entity_id=NEW.entity_id AND kind='greenhouse') THEN
        RAISE EXCEPTION 'work area requires greenhouse parent in same entity' USING ERRCODE='23514';
      END IF; RETURN NEW; END $$;
    CREATE TRIGGER work_area_scope BEFORE INSERT ON core.work_areas FOR EACH ROW EXECUTE FUNCTION core.guard_work_area();
    CREATE TRIGGER work_area_immutable BEFORE UPDATE OR DELETE ON core.work_areas FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    CREATE TABLE finance.obligation_attributions (
      obligation_id uuid NOT NULL REFERENCES finance.obligations(id),
      work_area_id uuid NOT NULL REFERENCES core.work_areas(id), amount numeric(20,2) NOT NULL CHECK(amount>0),
      source text NOT NULL CHECK(btrim(source)<>''), created_by uuid NOT NULL REFERENCES core.actors(id),
      PRIMARY KEY(obligation_id,work_area_id));
    CREATE FUNCTION finance.guard_attribution() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE o finance.obligations%ROWTYPE; total numeric; BEGIN
      SELECT * INTO o FROM finance.obligations WHERE id=NEW.obligation_id FOR UPDATE;
      IF NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=NEW.work_area_id AND entity_id=o.entity_id) THEN
        RAISE EXCEPTION 'attribution work area scope mismatch' USING ERRCODE='23514'; END IF;
      IF o.status NOT IN ('draft','review') AND NOT (o.status='posted' AND EXISTS(SELECT 1 FROM finance.opening_payables WHERE obligation_id=o.id) AND NEW.source LIKE 'owner:opening-attribution:%') THEN
        RAISE EXCEPTION 'posted attribution is immutable; only explicit opening annotation permitted' USING ERRCODE='55000'; END IF;
      SELECT coalesce(sum(amount),0) INTO total FROM finance.obligation_attributions WHERE obligation_id=o.id;
      IF total+NEW.amount>o.original_amount THEN RAISE EXCEPTION 'attribution exceeds economic amount' USING ERRCODE='23514'; END IF;
      RETURN NEW; END $$;
    CREATE TRIGGER attribution_scope BEFORE INSERT ON finance.obligation_attributions FOR EACH ROW EXECUTE FUNCTION finance.guard_attribution();
    CREATE TRIGGER attribution_immutable BEFORE UPDATE OR DELETE ON finance.obligation_attributions FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    CREATE FUNCTION finance.guard_business_basis() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE cls text; BEGIN
      IF NEW.direction='receivable' AND (TG_OP='INSERT' OR (NEW.status='posted' AND OLD.status IN ('draft','review'))) AND
         (EXISTS(SELECT 1 FROM core.counterparties WHERE id=NEW.counterparty_id AND counterparty_type='depot') OR coalesce(NEW.description,'') ~* '(банан|banana)') THEN
        RAISE EXCEPTION 'banana sale requires authoritative harvest/depot settlement adapter' USING ERRCODE='23514'; END IF;
      IF NEW.economic_source='harvest_depot' THEN
        RAISE EXCEPTION 'banana sale requires authoritative harvest/depot settlement adapter' USING ERRCODE='23514'; END IF;
      IF NEW.obligation_class='STRATEGIC' THEN
        SELECT account_class INTO cls FROM finance.ledger_accounts WHERE id=NEW.offset_ledger_account_id;
        IF cls NOT IN ('cash','financing_principal') THEN RAISE EXCEPTION 'strategic principal cannot be expense/revenue' USING ERRCODE='23514'; END IF;
      END IF; RETURN NEW; END $$;
    CREATE TRIGGER obligation_business_basis BEFORE INSERT OR UPDATE ON finance.obligations FOR EACH ROW EXECUTE FUNCTION finance.guard_business_basis();
    CREATE TABLE core.capture_drafts (
      id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id), original_text text NOT NULL CHECK(length(original_text) BETWEEN 1 AND 10000),
      source varchar(20) NOT NULL CHECK(source IN ('text','voice_transcript')), fingerprint varchar(64) NOT NULL,
      route varchar(30) NOT NULL CHECK(route IN ('FINANCE','HARVEST_DEPOT','DOCUMENTS','INVENTORY','OPERATIONS','UNRESOLVED')),
      extracted jsonb NOT NULL, gaps jsonb NOT NULL, state varchar(20) NOT NULL CHECK(state IN ('REVIEW','READY','LINKED')),
      version integer NOT NULL DEFAULT 1 CHECK(version>0), finance_kind varchar(30), finance_id uuid,
      created_by uuid NOT NULL REFERENCES core.actors(id), created_at timestamptz NOT NULL DEFAULT now(),
      CHECK ((state='LINKED')=(finance_id IS NOT NULL AND finance_kind IS NOT NULL)),
      UNIQUE(finance_kind,finance_id));
    CREATE INDEX capture_attention ON core.capture_drafts(entity_id,state,created_at);
    CREATE FUNCTION core.guard_capture() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE e uuid; BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'capture evidence is immutable' USING ERRCODE='55000'; END IF;
      IF TG_OP='UPDATE' THEN
        IF (NEW.id,NEW.entity_id,NEW.original_text,NEW.source,NEW.fingerprint,NEW.route,NEW.extracted,NEW.gaps,NEW.created_by,NEW.created_at) IS DISTINCT FROM
           (OLD.id,OLD.entity_id,OLD.original_text,OLD.source,OLD.fingerprint,OLD.route,OLD.extracted,OLD.gaps,OLD.created_by,OLD.created_at) OR OLD.state='LINKED' OR NEW.version<>OLD.version+1 OR NEW.state<>'LINKED' THEN
          RAISE EXCEPTION 'capture evidence is immutable; only one confirmed draft linkage allowed' USING ERRCODE='55000'; END IF;
      END IF;
      IF NEW.state='LINKED' THEN
        IF NEW.route<>'FINANCE' OR NEW.finance_kind NOT IN ('obligation','payment') THEN RAISE EXCEPTION 'unsupported capture routing' USING ERRCODE='23514'; END IF;
        IF NEW.finance_kind='obligation' THEN SELECT entity_id INTO e FROM finance.obligations WHERE id=NEW.finance_id;
        ELSE SELECT entity_id INTO e FROM finance.payments WHERE id=NEW.finance_id; END IF;
        IF e IS DISTINCT FROM NEW.entity_id THEN RAISE EXCEPTION 'capture linkage scope mismatch' USING ERRCODE='23514'; END IF;
      END IF; RETURN NEW; END $$;
    CREATE TRIGGER capture_evidence BEFORE INSERT OR UPDATE OR DELETE ON core.capture_drafts FOR EACH ROW EXECUTE FUNCTION core.guard_capture();
    """)

def downgrade():
    raise RuntimeError('Forward-only: restore verified backup separately and reconcile retained history')
