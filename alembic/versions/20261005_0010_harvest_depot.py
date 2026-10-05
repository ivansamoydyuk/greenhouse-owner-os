"""Harvest R1. Forward-only after evidence/posting: restore verified backup in a
separate environment, preserve/reconcile subsequent original evidence and journal.
No business history or references seeded. Prior migrations remain unchanged."""
from alembic import op
revision='20261005_0010'
down_revision='20261005_0009'
branch_labels=None
depends_on=None

def upgrade():
    op.execute(r'''CREATE SCHEMA harvest;

CREATE TABLE harvest.evidence (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	entity_id UUID NOT NULL,
	source TEXT NOT NULL,
	source_reference TEXT,
	raw_text TEXT,
	filename TEXT,
	mime_type TEXT,
	file_bytes BYTEA,
	fingerprint VARCHAR(64) NOT NULL,
	created_by UUID NOT NULL,
	received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(entity_id) REFERENCES core.entities (id),
	FOREIGN KEY(created_by) REFERENCES core.actors (id)
)

;

CREATE TABLE harvest.batches (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	entity_id UUID NOT NULL,
	event_date DATE NOT NULL,
	label TEXT NOT NULL,
	original_greenhouse TEXT,
	greenhouse_id UUID,
	link_evidence TEXT NOT NULL,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(entity_id) REFERENCES core.entities (id),
	FOREIGN KEY(greenhouse_id) REFERENCES core.work_areas (id),
	FOREIGN KEY(created_by) REFERENCES core.actors (id)
)

;

CREATE TABLE harvest.worker_reports (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	entity_id UUID NOT NULL,
	event_date DATE NOT NULL,
	original_greenhouse TEXT NOT NULL,
	greenhouse_id UUID,
	work_area_id UUID,
	cut_count INTEGER,
	field_weight_kg NUMERIC(20, 3),
	reporter TEXT NOT NULL,
	notes TEXT,
	evidence_id UUID NOT NULL,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(entity_id) REFERENCES core.entities (id),
	FOREIGN KEY(greenhouse_id) REFERENCES core.work_areas (id),
	FOREIGN KEY(work_area_id) REFERENCES core.work_areas (id),
	FOREIGN KEY(evidence_id) REFERENCES harvest.evidence (id),
	FOREIGN KEY(created_by) REFERENCES core.actors (id)
)

;

CREATE TABLE harvest.batch_reports (
	batch_id UUID NOT NULL,
	report_id UUID NOT NULL,
	PRIMARY KEY (batch_id, report_id),
	FOREIGN KEY(batch_id) REFERENCES harvest.batches (id),
	FOREIGN KEY(report_id) REFERENCES harvest.worker_reports (id)
)

;

CREATE TABLE harvest.depot_settlements (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	entity_id UUID NOT NULL,
	depot_id UUID NOT NULL,
	event_date DATE NOT NULL,
	due_date DATE,
	expected_payment BOOLEAN NOT NULL,
	currency VARCHAR(3) NOT NULL,
	original_greenhouse TEXT,
	greenhouse_id UUID,
	document_number TEXT,
	mixed BOOLEAN NOT NULL,
	stated_total NUMERIC(20, 2),
	alacak_value NUMERIC(20, 2),
	total_amount NUMERIC(20, 2) NOT NULL,
	evidence_id UUID NOT NULL,
	link_evidence TEXT,
	status VARCHAR(10) NOT NULL,
	version INTEGER NOT NULL,
	obligation_id UUID,
	payment_id UUID,
	application_ids JSONB NOT NULL,
	reviewed_issues JSONB NOT NULL,
	review_reason TEXT,
	posted_at TIMESTAMP WITH TIME ZONE,
	reversed_at TIMESTAMP WITH TIME ZONE,
	reversal_reason TEXT,
	replacement_of_id UUID,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(entity_id) REFERENCES core.entities (id),
	FOREIGN KEY(depot_id) REFERENCES core.counterparties (id),
	FOREIGN KEY(greenhouse_id) REFERENCES core.work_areas (id),
	FOREIGN KEY(evidence_id) REFERENCES harvest.evidence (id),
	FOREIGN KEY(obligation_id) REFERENCES finance.obligations (id) DEFERRABLE INITIALLY DEFERRED,
	FOREIGN KEY(payment_id) REFERENCES finance.payments (id),
	FOREIGN KEY(replacement_of_id) REFERENCES harvest.depot_settlements (id),
	FOREIGN KEY(created_by) REFERENCES core.actors (id)
)

;

CREATE TABLE harvest.settlement_batches (
	settlement_id UUID NOT NULL,
	batch_id UUID NOT NULL,
	PRIMARY KEY (settlement_id, batch_id),
	FOREIGN KEY(settlement_id) REFERENCES harvest.depot_settlements (id),
	FOREIGN KEY(batch_id) REFERENCES harvest.batches (id)
)

;

CREATE TABLE harvest.settlement_lines (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	settlement_id UUID NOT NULL,
	original_quality_name TEXT NOT NULL,
	normalized_quality VARCHAR(10) NOT NULL,
	net_weight_kg NUMERIC(20, 3) NOT NULL,
	unit_price NUMERIC(20, 2) NOT NULL,
	calculated_amount NUMERIC(20, 2) NOT NULL,
	stated_amount NUMERIC(20, 2),
	amount NUMERIC(20, 2) NOT NULL,
	count INTEGER,
	source_line TEXT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(settlement_id) REFERENCES harvest.depot_settlements (id)
)

;

CREATE TABLE harvest.line_allocations (
	line_id UUID NOT NULL,
	work_area_id UUID NOT NULL,
	net_weight_kg NUMERIC(20, 3) NOT NULL,
	amount NUMERIC(20, 2) NOT NULL,
	confirmed_count INTEGER,
	PRIMARY KEY (line_id, work_area_id),
	FOREIGN KEY(line_id) REFERENCES harvest.settlement_lines (id),
	FOREIGN KEY(work_area_id) REFERENCES core.work_areas (id)
)

;

ALTER TABLE harvest.evidence ADD UNIQUE(entity_id,fingerprint), ADD CHECK(length(fingerprint)=64), ADD CHECK(source_reference IS NOT NULL OR raw_text IS NOT NULL OR file_bytes IS NOT NULL), ADD CHECK(octet_length(file_bytes)<=5242880);
ALTER TABLE harvest.worker_reports ADD UNIQUE(evidence_id), ADD CHECK(cut_count>0), ADD CHECK(field_weight_kg>0);
ALTER TABLE harvest.batch_reports ADD UNIQUE(report_id);
ALTER TABLE harvest.depot_settlements ADD UNIQUE(obligation_id), ADD UNIQUE(payment_id), ADD UNIQUE(replacement_of_id), ADD CHECK(status IN ('draft','posted','reversed')), ADD CHECK(version>0), ADD CHECK(total_amount>0), ADD CHECK(currency IN ('TRY','USD','EUR','UAH')), ADD CHECK(due_date IS NULL OR due_date>=event_date), ADD CHECK(status='draft' OR obligation_id IS NOT NULL), ADD CHECK(stated_total>=0), ADD CHECK(alacak_value>=0);
CREATE UNIQUE INDEX receipt_active_source ON harvest.depot_settlements(entity_id,evidence_id) WHERE status<>'reversed';
CREATE UNIQUE INDEX receipt_active_number ON harvest.depot_settlements(entity_id,depot_id,document_number) WHERE status<>'reversed' AND document_number IS NOT NULL;

ALTER TABLE harvest.settlement_lines ADD CHECK(net_weight_kg>0), ADD CHECK(unit_price>=0), ADD CHECK(amount>0), ADD CHECK(count>0), ADD CHECK(calculated_amount=round(net_weight_kg*unit_price,2)), ADD CHECK(amount=calculated_amount OR amount=stated_amount), ADD CHECK(normalized_quality IN ('FIRST','YERLI','SECOND','VERIFY'));
ALTER TABLE harvest.line_allocations ADD CHECK(net_weight_kg>0), ADD CHECK(amount>=0), ADD CHECK(confirmed_count>0);
CREATE FUNCTION harvest.guard_evidence_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e uuid; g uuid; w uuid; sid uuid; BEGIN
  IF TG_TABLE_NAME='worker_reports' THEN
    IF NOT EXISTS(SELECT 1 FROM harvest.evidence WHERE id=NEW.evidence_id AND entity_id=NEW.entity_id) THEN RAISE EXCEPTION 'report evidence scope mismatch'; END IF;
    g=NEW.greenhouse_id; w=NEW.work_area_id; e=NEW.entity_id;
  ELSIF TG_TABLE_NAME='batches' THEN g=NEW.greenhouse_id; e=NEW.entity_id;
  ELSIF TG_TABLE_NAME='depot_settlements' THEN
    g=NEW.greenhouse_id; e=NEW.entity_id;
    IF NOT EXISTS(SELECT 1 FROM core.counterparties WHERE id=NEW.depot_id AND entity_id=e AND counterparty_type IN ('depot','customer','both')) OR NOT EXISTS(SELECT 1 FROM harvest.evidence WHERE id=NEW.evidence_id AND entity_id=e) THEN RAISE EXCEPTION 'depot or evidence scope mismatch'; END IF;
  ELSIF TG_TABLE_NAME='batch_reports' THEN
    IF NOT EXISTS(SELECT 1 FROM harvest.batches b JOIN harvest.worker_reports r ON r.entity_id=b.entity_id WHERE b.id=NEW.batch_id AND r.id=NEW.report_id) THEN RAISE EXCEPTION 'report batch scope mismatch'; END IF;

  END IF;
  IF g IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=g AND entity_id=e AND kind='greenhouse') THEN RAISE EXCEPTION 'greenhouse scope mismatch'; END IF;
  IF w IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=w AND entity_id=e AND (id=g OR parent_id=g)) THEN RAISE EXCEPTION 'work area scope mismatch'; END IF;
  RETURN NEW; END $$;
CREATE FUNCTION harvest.guard_child() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s harvest.depot_settlements%ROWTYPE; l harvest.settlement_lines%ROWTYPE; BEGIN
  IF TG_TABLE_NAME='settlement_batches' THEN
    SELECT * INTO s FROM harvest.depot_settlements WHERE id=NEW.settlement_id FOR UPDATE;
    PERFORM id FROM harvest.batches WHERE id=NEW.batch_id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM harvest.settlement_batches sb JOIN harvest.depot_settlements ds ON ds.id=sb.settlement_id WHERE sb.batch_id=NEW.batch_id AND ds.status<>'reversed') THEN RAISE EXCEPTION 'batch already has active receipt'; END IF;
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
CREATE FUNCTION harvest.guard_settlement() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'receipt history immutable'; END IF;
 IF TG_OP='INSERT' AND NEW.status<>'draft' THEN RAISE EXCEPTION 'receipt begins as draft'; END IF;
 IF TG_OP='UPDATE' THEN
   IF OLD.status='draft' AND NEW.status='draft' AND OLD.obligation_id IS NULL AND NEW.obligation_id IS NOT NULL AND (to_jsonb(NEW)-'obligation_id')=(to_jsonb(OLD)-'obligation_id') THEN RETURN NEW; END IF;
   IF (to_jsonb(NEW)-ARRAY['status','version','obligation_id','payment_id','application_ids','reviewed_issues','review_reason','posted_at','reversed_at','reversal_reason']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','version','obligation_id','payment_id','application_ids','reviewed_issues','review_reason','posted_at','reversed_at','reversal_reason']) OR NEW.version<>OLD.version+1 OR NOT ((OLD.status='draft' AND NEW.status='posted') OR (OLD.status='posted' AND NEW.status='reversed')) THEN RAISE EXCEPTION 'immutable receipt lifecycle'; END IF;
   IF OLD.status='posted' AND (NEW.obligation_id,NEW.payment_id,NEW.application_ids,NEW.reviewed_issues,NEW.review_reason,NEW.posted_at) IS DISTINCT FROM (OLD.obligation_id,OLD.payment_id,OLD.application_ids,OLD.reviewed_issues,OLD.review_reason,OLD.posted_at) THEN RAISE EXCEPTION 'posted receipt linkage immutable'; END IF;
 END IF;
 IF NEW.replacement_of_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM harvest.depot_settlements WHERE id=NEW.replacement_of_id AND entity_id=NEW.entity_id AND depot_id=NEW.depot_id AND currency=NEW.currency AND status='reversed') THEN RAISE EXCEPTION 'replacement requires reversed original'; END IF;
 RETURN NEW; END $$;
CREATE FUNCTION harvest.verify_sale_link() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s harvest.depot_settlements%ROWTYPE; o finance.obligations%ROWTYPE; BEGIN
 IF TG_TABLE_NAME='depot_settlements' THEN SELECT * INTO s FROM harvest.depot_settlements WHERE id=NEW.id;
 ELSE SELECT * INTO s FROM harvest.depot_settlements WHERE obligation_id=NEW.id; END IF;
 IF s.status='draft' AND s.obligation_id IS NOT NULL THEN RAISE EXCEPTION 'draft reservation cannot commit'; END IF;
 IF s.obligation_id IS NOT NULL THEN
   SELECT * INTO o FROM finance.obligations WHERE id=s.obligation_id;
   IF o.id IS NULL OR (o.entity_id,o.counterparty_id,o.currency,o.original_amount,o.event_date,o.due_date,o.direction,o.economic_source,o.status) IS DISTINCT FROM (s.entity_id,s.depot_id,s.currency,s.total_amount,s.event_date,s.due_date,'receivable'::varchar,'harvest_depot'::varchar,s.status) OR NOT EXISTS(SELECT 1 FROM finance.ledger_accounts WHERE id=o.offset_ledger_account_id AND account_class='revenue') THEN RAISE EXCEPTION 'receipt finance authority mismatch'; END IF;
   IF (SELECT coalesce(sum(amount),0) FROM harvest.settlement_lines WHERE settlement_id=s.id)<>s.total_amount THEN RAISE EXCEPTION 'receipt total mismatch'; END IF;
   IF EXISTS(SELECT 1 FROM
      (SELECT work_area_id,sum(a.amount) amount FROM harvest.line_allocations a JOIN harvest.settlement_lines l ON l.id=a.line_id WHERE l.settlement_id=s.id GROUP BY work_area_id) a
      FULL JOIN (SELECT work_area_id,sum(amount) amount FROM finance.obligation_attributions WHERE obligation_id=o.id GROUP BY work_area_id) b USING(work_area_id)
      WHERE coalesce(a.amount,0)<>coalesce(b.amount,0)) THEN RAISE EXCEPTION 'sale attribution differs from explicit receipt allocation'; END IF;
   IF s.payment_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM finance.payments p JOIN finance.payment_allocations a ON a.payment_id=p.id WHERE p.id=s.payment_id AND p.entity_id=s.entity_id AND p.counterparty_id=s.depot_id AND p.currency=s.currency AND p.direction='in' AND a.obligation_id=o.id AND a.amount=p.amount) THEN RAISE EXCEPTION 'receipt cash payment linkage mismatch'; END IF;
   IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(s.application_ids) x WHERE NOT EXISTS(SELECT 1 FROM finance.advance_applications WHERE id=x::uuid AND obligation_id=o.id AND entity_id=s.entity_id AND counterparty_id=s.depot_id AND currency=s.currency AND direction='received')) THEN RAISE EXCEPTION 'receipt advance linkage mismatch'; END IF;

 ELSIF TG_TABLE_NAME='obligations' THEN IF NEW.economic_source='harvest_depot' THEN RAISE EXCEPTION 'orphan banana sale'; END IF; END IF;
 RETURN NULL; END $$;
CREATE CONSTRAINT TRIGGER sale_receipt_consistency AFTER INSERT OR UPDATE ON harvest.depot_settlements DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION harvest.verify_sale_link();
CREATE CONSTRAINT TRIGGER sale_obligation_consistency AFTER INSERT OR UPDATE ON finance.obligations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION harvest.verify_sale_link();
CREATE TRIGGER receipt_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON harvest.depot_settlements FOR EACH ROW EXECUTE FUNCTION harvest.guard_settlement();
CREATE OR REPLACE FUNCTION finance.guard_business_basis() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cls text; BEGIN
 IF NEW.economic_source='harvest_depot' THEN
   IF NOT EXISTS(SELECT 1 FROM harvest.depot_settlements WHERE obligation_id=NEW.id AND entity_id=NEW.entity_id AND depot_id=NEW.counterparty_id AND currency=NEW.currency AND total_amount=NEW.original_amount) THEN RAISE EXCEPTION 'banana sale requires authoritative harvest/depot settlement adapter' USING ERRCODE='23514'; END IF;
 ELSIF NEW.direction='receivable' AND (TG_OP='INSERT' OR (NEW.status='posted' AND OLD.status IN ('draft','review'))) AND (EXISTS(SELECT 1 FROM core.counterparties WHERE id=NEW.counterparty_id AND counterparty_type='depot') OR coalesce(NEW.description,'') ~* '(банан|banana)') THEN RAISE EXCEPTION 'banana sale requires authoritative harvest/depot settlement adapter' USING ERRCODE='23514'; END IF;
 IF NEW.obligation_class='STRATEGIC' THEN SELECT account_class INTO cls FROM finance.ledger_accounts WHERE id=NEW.offset_ledger_account_id; IF cls NOT IN ('cash','financing_principal') THEN RAISE EXCEPTION 'strategic principal cannot be expense/revenue' USING ERRCODE='23514'; END IF; END IF;
 RETURN NEW; END $$;
ALTER TABLE core.capture_drafts ADD COLUMN harvest_kind varchar(30), ADD COLUMN harvest_id uuid;
ALTER TABLE core.capture_drafts DROP CONSTRAINT capture_drafts_check;
ALTER TABLE core.capture_drafts ADD CHECK ((state='LINKED')=((finance_id IS NOT NULL AND finance_kind IS NOT NULL) OR (harvest_id IS NOT NULL AND harvest_kind IS NOT NULL))), ADD UNIQUE(harvest_kind,harvest_id), ADD CHECK (finance_id IS NULL OR harvest_id IS NULL);
CREATE OR REPLACE FUNCTION core.guard_capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e uuid; BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'capture evidence is immutable' USING ERRCODE='55000'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.id,NEW.entity_id,NEW.original_text,NEW.source,NEW.fingerprint,NEW.route,NEW.extracted,NEW.gaps,NEW.created_by,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.entity_id,OLD.original_text,OLD.source,OLD.fingerprint,OLD.route,OLD.extracted,OLD.gaps,OLD.created_by,OLD.created_at) OR OLD.state='LINKED' OR NEW.version<>OLD.version+1 OR NEW.state<>'LINKED') THEN RAISE EXCEPTION 'capture evidence is immutable; only one confirmed draft linkage allowed' USING ERRCODE='55000'; END IF;
 IF NEW.state='LINKED' THEN
   IF NEW.route='HARVEST_DEPOT' THEN
     IF NEW.harvest_kind='report' THEN SELECT entity_id INTO e FROM harvest.worker_reports WHERE id=NEW.harvest_id;
     ELSIF NEW.harvest_kind='batch' THEN SELECT entity_id INTO e FROM harvest.batches WHERE id=NEW.harvest_id;
     ELSIF NEW.harvest_kind='settlement' THEN SELECT entity_id INTO e FROM harvest.depot_settlements WHERE id=NEW.harvest_id;
     ELSE RAISE EXCEPTION 'unsupported harvest route'; END IF;
   ELSE
     IF NEW.route<>'FINANCE' OR NEW.finance_kind NOT IN ('obligation','payment') THEN RAISE EXCEPTION 'unsupported capture routing' USING ERRCODE='23514'; END IF;
     IF NEW.finance_kind='obligation' THEN SELECT entity_id INTO e FROM finance.obligations WHERE id=NEW.finance_id; ELSE SELECT entity_id INTO e FROM finance.payments WHERE id=NEW.finance_id; END IF;
   END IF;
   IF e IS DISTINCT FROM NEW.entity_id THEN RAISE EXCEPTION 'capture linkage scope mismatch' USING ERRCODE='23514'; END IF;
 END IF;RETURN NEW; END $$;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON harvest.evidence FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON harvest.worker_reports FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON harvest.batches FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON harvest.batch_reports FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON harvest.settlement_batches FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON harvest.settlement_lines FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON harvest.line_allocations FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER scope BEFORE INSERT ON harvest.worker_reports FOR EACH ROW EXECUTE FUNCTION harvest.guard_evidence_scope();
CREATE TRIGGER scope BEFORE INSERT ON harvest.batches FOR EACH ROW EXECUTE FUNCTION harvest.guard_evidence_scope();
CREATE TRIGGER scope BEFORE INSERT ON harvest.batch_reports FOR EACH ROW EXECUTE FUNCTION harvest.guard_evidence_scope();
CREATE TRIGGER scope BEFORE INSERT ON harvest.depot_settlements FOR EACH ROW EXECUTE FUNCTION harvest.guard_evidence_scope();
CREATE TRIGGER child_scope BEFORE INSERT ON harvest.settlement_lines FOR EACH ROW EXECUTE FUNCTION harvest.guard_child();
CREATE TRIGGER child_scope BEFORE INSERT ON harvest.settlement_batches FOR EACH ROW EXECUTE FUNCTION harvest.guard_child();
CREATE TRIGGER child_scope BEFORE INSERT ON harvest.line_allocations FOR EACH ROW EXECUTE FUNCTION harvest.guard_child();
''')

def downgrade():
    raise RuntimeError('Forward-only: verified backup and preserved journal/evidence reconciliation required')
