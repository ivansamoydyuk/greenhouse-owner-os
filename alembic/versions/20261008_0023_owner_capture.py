"""Source-first R2 envelope; no source or business records seeded.

Recovery: restore a verified pre-upgrade backup to a separate database, then
preserve/reconcile subsequent sources, review revisions and domain effects.
Dropping these tables after owner confirmations would destroy audit evidence.
"""
from alembic import op
revision = '20261008_0023'
down_revision = '20261006_0022'
branch_labels = depends_on = None

def upgrade():
    op.execute(SQL)
    op.execute(EXTRA)
    op.execute(HARDEN)

def downgrade():
    raise RuntimeError('Forward-only evidence migration: use documented separate-backup recovery')

SQL = r"""
CREATE TABLE core.owner_entity_access (
 entity_id uuid REFERENCES core.entities(id), actor_id uuid REFERENCES core.actors(id),
 PRIMARY KEY(entity_id,actor_id));
INSERT INTO core.owner_entity_access SELECT DISTINCT r.entity_id,r.created_by
 FROM core.reference_records r JOIN core.actors a ON a.id=r.created_by
 WHERE r.kind='entity' AND a.actor_type='owner' AND a.active;
CREATE FUNCTION core.r2_owner(e uuid,a uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT 1 FROM core.owner_entity_access x JOIN core.actors p ON p.id=x.actor_id
 WHERE x.entity_id=e AND x.actor_id=a AND p.active AND p.actor_type='owner') $$;
ALTER TABLE core.inbox_items ADD UNIQUE(entity_id,id);
CREATE TABLE core.processing_jobs (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL, inbox_id uuid NOT NULL,
 engine_version text NOT NULL, state text NOT NULL DEFAULT 'QUEUED'
 CHECK(state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
 attempts int NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3), lease_token uuid,
 lease_until timestamptz, retry_at timestamptz, error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(inbox_id,engine_version), UNIQUE(entity_id,id),
 FOREIGN KEY(entity_id,inbox_id) REFERENCES core.inbox_items(entity_id,id));
CREATE INDEX processing_claim ON core.processing_jobs(state,retry_at,lease_until);
CREATE TABLE core.extraction_runs (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL, inbox_id uuid NOT NULL, job_id uuid NOT NULL,
 attempt int NOT NULL, provider text NOT NULL, engine_version text NOT NULL,
 input_hash text NOT NULL, raw_output jsonb NOT NULL, normalized jsonb NOT NULL,
 warnings jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(job_id,attempt), UNIQUE(entity_id,id),
 FOREIGN KEY(entity_id,inbox_id) REFERENCES core.inbox_items(entity_id,id),
 FOREIGN KEY(entity_id,job_id) REFERENCES core.processing_jobs(entity_id,id));
CREATE TABLE core.proposed_actions (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL, inbox_id uuid NOT NULL, slot text NOT NULL,
 action_type text NOT NULL CHECK(action_type IN ('PAYABLE','PAYMENT','DIRECT_EXPENSE','STOCK_RECEIPT','STOCK_APPLICATION','DEPOT_RECEIPT','PERSONAL_TASK','WORKER_TASK','INVOICE_MATCH','UNRESOLVED')),
 state text NOT NULL CHECK(state IN ('PREPARING','NEEDS_INPUT','READY','DEFERRED','POSTED','CONFIRMED_NONPOSTING','REJECTED','STALE','FAILED')),
 version int NOT NULL CHECK(version>0), priority int NOT NULL,
 first_unresolved_at timestamptz NOT NULL DEFAULT clock_timestamp(), deferred_at timestamptz,
 created_by uuid NOT NULL REFERENCES core.actors(id),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(inbox_id,slot), UNIQUE(entity_id,id),
 FOREIGN KEY(entity_id,inbox_id) REFERENCES core.inbox_items(entity_id,id));
CREATE INDEX confirmation_queue ON core.proposed_actions(entity_id,priority,first_unresolved_at,id);
CREATE TABLE core.proposal_revisions (
 proposal_id uuid NOT NULL, entity_id uuid NOT NULL, version int NOT NULL,
 schema_version int NOT NULL CHECK(schema_version=1), extraction_run_id uuid,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 provenance jsonb NOT NULL, missing jsonb NOT NULL, basis jsonb NOT NULL,
 preview_digest text NOT NULL CHECK(length(preview_digest)=64),
 actor_id uuid REFERENCES core.actors(id), reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(proposal_id,version),
 FOREIGN KEY(entity_id,proposal_id) REFERENCES core.proposed_actions(entity_id,id),
 FOREIGN KEY(entity_id,extraction_run_id) REFERENCES core.extraction_runs(entity_id,id));
CREATE TABLE core.proposal_actions (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL, proposal_id uuid NOT NULL, version int NOT NULL,
 actor_id uuid NOT NULL REFERENCES core.actors(id), action text NOT NULL CHECK(action IN ('PREPARE','EDIT','DEFER','REJECT','REOPEN','STALE','CONFIRM')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), snapshot jsonb NOT NULL,
 FOREIGN KEY(entity_id,proposal_id) REFERENCES core.proposed_actions(entity_id,id),
 FOREIGN KEY(proposal_id,version) REFERENCES core.proposal_revisions(proposal_id,version));
CREATE TABLE core.owner_tasks (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id), owner_id uuid NOT NULL REFERENCES core.actors(id),
 proposal_id uuid UNIQUE, title text NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 200),
 greenhouse_id uuid REFERENCES core.work_areas(id), planned_date date, note text,
 state text NOT NULL CHECK(state IN ('TO_DO','DONE')), version int NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(entity_id,id), FOREIGN KEY(entity_id,proposal_id) REFERENCES core.proposed_actions(entity_id,id));
CREATE TABLE core.owner_task_actions (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL, task_id uuid NOT NULL, version int NOT NULL,
 actor_id uuid NOT NULL REFERENCES core.actors(id), state text NOT NULL CHECK(state IN ('TO_DO','DONE')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(task_id,version),
 FOREIGN KEY(entity_id,task_id) REFERENCES core.owner_tasks(entity_id,id));
CREATE TABLE core.confirmation_effects (
 proposal_id uuid PRIMARY KEY, entity_id uuid NOT NULL, version int NOT NULL,
 actor_id uuid NOT NULL REFERENCES core.actors(id), preview_digest text NOT NULL,
 obligation_id uuid UNIQUE REFERENCES finance.obligations(id), payment_id uuid UNIQUE REFERENCES finance.payments(id),
 inventory_id uuid UNIQUE REFERENCES inventory.events(id), settlement_id uuid UNIQUE REFERENCES harvest.depot_settlements(id),
 owner_task_id uuid UNIQUE REFERENCES core.owner_tasks(id), worker_task_id uuid UNIQUE REFERENCES workers.tasks(id),
 response jsonb NOT NULL, confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(entity_id,proposal_id) REFERENCES core.proposed_actions(entity_id,id),
 FOREIGN KEY(proposal_id,version) REFERENCES core.proposal_revisions(proposal_id,version),
 CHECK(num_nonnulls(obligation_id,payment_id,inventory_id,settlement_id,owner_task_id,worker_task_id)<=1));
CREATE TABLE core.owner_reminders (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL, actor_id uuid NOT NULL REFERENCES core.actors(id),
 reminder_day date NOT NULL, reason text NOT NULL CHECK(reason='UNCONFIRMED'),
 state text NOT NULL CHECK(state IN ('ELIGIBLE','CANCELLED','ATTEMPTED','DELIVERED')),
 proposal_ids jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(entity_id,actor_id,reason,reminder_day));
CREATE FUNCTION core.r2_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'R2 evidence is append-only' USING ERRCODE='23514'; END $$;
CREATE TRIGGER extraction_immutable BEFORE UPDATE OR DELETE ON core.extraction_runs FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TRIGGER revision_immutable BEFORE UPDATE OR DELETE ON core.proposal_revisions FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TRIGGER proposal_action_immutable BEFORE UPDATE OR DELETE ON core.proposal_actions FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TRIGGER confirmation_immutable BEFORE UPDATE OR DELETE ON core.confirmation_effects FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TRIGGER owner_task_action_immutable BEFORE UPDATE OR DELETE ON core.owner_task_actions FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE FUNCTION core.r2_effect_guard() RETURNS trigger LANGUAGE plpgsql AS $$
 DECLARE p core.proposed_actions; r core.proposal_revisions; target_entity uuid;
 BEGIN
 SELECT * INTO p FROM core.proposed_actions WHERE id=NEW.proposal_id;
 SELECT * INTO r FROM core.proposal_revisions WHERE proposal_id=p.id AND version=NEW.version;
 IF NOT core.r2_owner(NEW.entity_id,NEW.actor_id) OR p.version<>NEW.version OR p.entity_id<>NEW.entity_id
 OR r.preview_digest<>NEW.preview_digest OR p.state NOT IN ('POSTED','CONFIRMED_NONPOSTING')
 OR NOT EXISTS(SELECT 1 FROM core.proposal_actions WHERE proposal_id=p.id AND version=NEW.version AND action='CONFIRM' AND actor_id=NEW.actor_id)
 THEN RAISE EXCEPTION 'Invalid owner confirmation' USING ERRCODE='23514'; END IF;
 IF NEW.obligation_id IS NOT NULL THEN SELECT entity_id INTO target_entity FROM finance.obligations WHERE id=NEW.obligation_id;
 ELSIF NEW.payment_id IS NOT NULL THEN SELECT entity_id INTO target_entity FROM finance.payments WHERE id=NEW.payment_id;
 ELSIF NEW.inventory_id IS NOT NULL THEN SELECT entity_id INTO target_entity FROM inventory.events WHERE id=NEW.inventory_id;
 ELSIF NEW.settlement_id IS NOT NULL THEN SELECT entity_id INTO target_entity FROM harvest.depot_settlements WHERE id=NEW.settlement_id;
 ELSIF NEW.owner_task_id IS NOT NULL THEN SELECT entity_id INTO target_entity FROM core.owner_tasks WHERE id=NEW.owner_task_id;
 ELSIF NEW.worker_task_id IS NOT NULL THEN SELECT entity_id INTO target_entity FROM workers.tasks WHERE id=NEW.worker_task_id;
 END IF;
 IF target_entity IS DISTINCT FROM NEW.entity_id THEN RAISE EXCEPTION 'Confirmation effect scope mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
 END $$;
CREATE CONSTRAINT TRIGGER r2_effect_valid AFTER INSERT ON core.confirmation_effects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_effect_guard();
CREATE FUNCTION core.r2_proposal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Proposal retained' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.id,NEW.entity_id,NEW.inbox_id,NEW.slot,NEW.action_type,NEW.first_unresolved_at,NEW.created_by)
 IS DISTINCT FROM (OLD.id,OLD.entity_id,OLD.inbox_id,OLD.slot,OLD.action_type,OLD.first_unresolved_at,OLD.created_by)
 OR OLD.state IN ('POSTED','CONFIRMED_NONPOSTING') OR NEW.version NOT BETWEEN OLD.version AND OLD.version+1)
 THEN RAISE EXCEPTION 'Invalid proposal mutation' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER r2_proposal_identity BEFORE UPDATE OR DELETE ON core.proposed_actions FOR EACH ROW EXECUTE FUNCTION core.r2_proposal_guard();
"""

EXTRA = r"""
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='core.inbox_items'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%octet_length(file_bytes)%' LOOP
 EXECUTE format('ALTER TABLE core.inbox_items DROP CONSTRAINT %I',c.conname); END LOOP;
 END $$;
ALTER TABLE core.inbox_items ADD CHECK(octet_length(file_bytes)<=20971520);
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='harvest.settlement_lines'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%normalized_quality%' LOOP
 EXECUTE format('ALTER TABLE harvest.settlement_lines DROP CONSTRAINT %I',c.conname); END LOOP;
 END $$;
ALTER TABLE harvest.settlement_lines ADD CHECK(normalized_quality IN ('HIGHEST','FIRST','SECOND','YERLI','VERIFY'));
CREATE TABLE harvest.receipt_quality_reviews (
 settlement_id uuid NOT NULL REFERENCES harvest.depot_settlements(id), line_no int NOT NULL CHECK(line_no>=0),
 entity_id uuid NOT NULL REFERENCES core.entities(id), proposal_id uuid NOT NULL REFERENCES core.proposed_actions(id),
 quality text NOT NULL CHECK(quality IN ('HIGHEST','FIRST','SECOND')), reviewed_by uuid NOT NULL REFERENCES core.actors(id),
 reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(settlement_id,line_no));
CREATE TRIGGER quality_review_immutable BEFORE UPDATE OR DELETE ON harvest.receipt_quality_reviews FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TABLE core.invoice_receipt_links (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id), inbox_id uuid NOT NULL REFERENCES core.inbox_items(id),
 proposal_id uuid NOT NULL UNIQUE REFERENCES core.proposed_actions(id), receipt_id uuid NOT NULL REFERENCES inventory.events(id),
 finance_obligation_id uuid REFERENCES finance.obligations(id), reviewed_by uuid NOT NULL REFERENCES core.actors(id),
 note text, valuation_state text NOT NULL DEFAULT 'UNKNOWN_UNCHANGED' CHECK(valuation_state='UNKNOWN_UNCHANGED'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(inbox_id,receipt_id));
CREATE TRIGGER invoice_link_immutable BEFORE UPDATE OR DELETE ON core.invoice_receipt_links FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE TABLE finance.direct_expenses (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id), proposal_id uuid NOT NULL UNIQUE REFERENCES core.proposed_actions(id),
 amount numeric(20,2) NOT NULL CHECK(amount>0), currency varchar(3) NOT NULL,
 event_date date NOT NULL, money_account_id uuid NOT NULL REFERENCES finance.money_accounts(id),
 counterparty_id uuid REFERENCES core.counterparties(id), description text,
 status text NOT NULL CHECK(status IN ('posted','reversed')), version int NOT NULL DEFAULT 1,
 posting_id uuid NOT NULL UNIQUE REFERENCES finance.postings(id) DEFERRABLE INITIALLY DEFERRED,
 reversal_posting_id uuid UNIQUE REFERENCES finance.postings(id) DEFERRABLE INITIALLY DEFERRED,
 created_by uuid NOT NULL REFERENCES core.actors(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 reversed_by uuid REFERENCES core.actors(id), reversal_reason text);
ALTER TABLE core.confirmation_effects ADD direct_expense_id uuid UNIQUE REFERENCES finance.direct_expenses(id),
 ADD invoice_match_id uuid UNIQUE REFERENCES core.invoice_receipt_links(id);
CREATE FUNCTION finance.r2_expense_guard() RETURNS trigger LANGUAGE plpgsql AS $$
 DECLARE p finance.postings; cash uuid; expense uuid; scope uuid; cur text; proposal core.proposed_actions;
 BEGIN
 SELECT * INTO proposal FROM core.proposed_actions WHERE id=NEW.proposal_id;
 SELECT * INTO p FROM finance.postings WHERE id=NEW.posting_id;
 SELECT m.entity_id,m.currency,m.ledger_account_id INTO scope,cur,cash FROM finance.money_accounts m WHERE m.id=NEW.money_account_id;
 SELECT id INTO expense FROM finance.ledger_accounts WHERE entity_id=NEW.entity_id AND currency=NEW.currency AND system_code='GENERAL_EXPENSE';
 IF NOT core.r2_owner(NEW.entity_id,NEW.created_by) OR scope<>NEW.entity_id OR cur<>NEW.currency
 OR proposal.entity_id<>NEW.entity_id OR proposal.action_type<>'DIRECT_EXPENSE'
 OR p.id IS NULL OR (p.entity_id,p.currency,p.event_date,p.source_type,p.source_id,p.created_by)
 IS DISTINCT FROM (NEW.entity_id,NEW.currency,NEW.event_date,'direct_expense'::varchar,NEW.id,NEW.created_by)
 OR (NEW.counterparty_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.counterparties WHERE id=NEW.counterparty_id AND entity_id=NEW.entity_id))
 OR NOT EXISTS(SELECT 1 FROM core.confirmation_effects WHERE proposal_id=NEW.proposal_id AND direct_expense_id=NEW.id)
 THEN RAISE EXCEPTION 'Direct expense scope/authority mismatch' USING ERRCODE='23514'; END IF;
 IF (SELECT count(*) FROM finance.posting_entries WHERE posting_id=p.id)<>2
 OR NOT EXISTS(SELECT 1 FROM finance.posting_entries WHERE posting_id=p.id AND ledger_account_id=cash AND side='credit' AND amount=NEW.amount)
 OR NOT EXISTS(SELECT 1 FROM finance.posting_entries WHERE posting_id=p.id AND ledger_account_id=expense AND side='debit' AND amount=NEW.amount)
 THEN RAISE EXCEPTION 'Direct expense journal mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE CONSTRAINT TRIGGER direct_expense_valid AFTER INSERT OR UPDATE ON finance.direct_expenses DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.r2_expense_guard();
DO $$ DECLARE f text; BEGIN
 SELECT pg_get_functiondef('finance.assert_compensating_posting()'::regprocedure) INTO f;
 IF position('WHEN ''advance_application'' THEN ''advance_applications''' IN f)=0 THEN RAISE EXCEPTION 'Unexpected reversal function baseline'; END IF;
 f=replace(f,'''direct_settlement'',''advance_application'')','''direct_settlement'',''advance_application'',''direct_expense'')');
 f=replace(f,'WHEN ''advance_application'' THEN ''advance_applications'' END','WHEN ''advance_application'' THEN ''advance_applications'' WHEN ''direct_expense'' THEN ''direct_expenses'' END');
 EXECUTE f; END $$;
CREATE FUNCTION core.r2_extended_effect_scope() RETURNS trigger LANGUAGE plpgsql AS $$
 DECLARE scope uuid;
 BEGIN
 IF NEW.direct_expense_id IS NOT NULL THEN SELECT entity_id INTO scope FROM finance.direct_expenses WHERE id=NEW.direct_expense_id;
 ELSIF NEW.invoice_match_id IS NOT NULL THEN SELECT entity_id INTO scope FROM core.invoice_receipt_links WHERE id=NEW.invoice_match_id;
 ELSE RETURN NEW; END IF;
 IF scope IS DISTINCT FROM NEW.entity_id OR num_nonnulls(NEW.obligation_id,NEW.payment_id,NEW.inventory_id,NEW.settlement_id,NEW.owner_task_id,NEW.worker_task_id,NEW.direct_expense_id,NEW.invoice_match_id)<>1
 THEN RAISE EXCEPTION 'Typed effect mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE CONSTRAINT TRIGGER extended_effect_valid AFTER INSERT ON core.confirmation_effects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_extended_effect_scope();
-- Extend the existing confirmation scope function for the two typed additions.
DO $$ DECLARE f text; BEGIN
 SELECT pg_get_functiondef('core.r2_effect_guard()'::regprocedure) INTO f;
 f=replace(f,'IF target_entity IS DISTINCT FROM NEW.entity_id','IF NEW.direct_expense_id IS NOT NULL THEN SELECT entity_id INTO target_entity FROM finance.direct_expenses WHERE id=NEW.direct_expense_id; ELSIF NEW.invoice_match_id IS NOT NULL THEN SELECT entity_id INTO target_entity FROM core.invoice_receipt_links WHERE id=NEW.invoice_match_id; END IF; IF target_entity IS DISTINCT FROM NEW.entity_id');
 EXECUTE f; END $$;
CREATE FUNCTION core.r2_task_scope() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN
 IF NOT core.r2_owner(NEW.entity_id,NEW.owner_id) OR NEW.greenhouse_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=NEW.greenhouse_id AND entity_id=NEW.entity_id)
 THEN RAISE EXCEPTION 'Owner task scope mismatch' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.id,NEW.entity_id,NEW.owner_id,NEW.proposal_id,NEW.title,NEW.greenhouse_id,NEW.planned_date,NEW.note) IS DISTINCT FROM (OLD.id,OLD.entity_id,OLD.owner_id,OLD.proposal_id,OLD.title,OLD.greenhouse_id,OLD.planned_date,OLD.note) OR NEW.version<>OLD.version+1)
 THEN RAISE EXCEPTION 'Owner task identity immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER owner_task_scope BEFORE INSERT OR UPDATE ON core.owner_tasks FOR EACH ROW EXECUTE FUNCTION core.r2_task_scope();
"""

HARDEN = r"""
CREATE FUNCTION finance.r2_expense_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Expense evidence retained' USING ERRCODE='23514'; END IF;
 IF (NEW.id,NEW.entity_id,NEW.proposal_id,NEW.amount,NEW.currency,NEW.event_date,NEW.money_account_id,NEW.counterparty_id,NEW.description,NEW.posting_id,NEW.created_by,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.entity_id,OLD.proposal_id,OLD.amount,OLD.currency,OLD.event_date,OLD.money_account_id,OLD.counterparty_id,OLD.description,OLD.posting_id,OLD.created_by,OLD.created_at)
 OR OLD.status<>'posted' OR NEW.status<>'reversed' OR NEW.version<>OLD.version+1
 OR NEW.reversal_posting_id IS NULL OR NOT core.r2_owner(NEW.entity_id,NEW.reversed_by) OR length(btrim(NEW.reversal_reason))=0
 THEN RAISE EXCEPTION 'Expense original immutable / invalid reversal' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER expense_identity BEFORE UPDATE OR DELETE ON finance.direct_expenses FOR EACH ROW EXECUTE FUNCTION finance.r2_expense_identity();
CREATE TABLE harvest.depot_quality_mappings (
 entity_id uuid NOT NULL REFERENCES core.entities(id), depot_id uuid NOT NULL REFERENCES core.counterparties(id),
 original_label text NOT NULL CHECK(length(btrim(original_label)) BETWEEN 1 AND 100), version int NOT NULL CHECK(version>0),
 quality text NOT NULL CHECK(quality IN ('HIGHEST','FIRST','SECOND')), proposal_id uuid NOT NULL REFERENCES core.proposed_actions(id),
 accepted_by uuid NOT NULL REFERENCES core.actors(id), accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(depot_id,original_label,version));
CREATE TRIGGER depot_mapping_immutable BEFORE UPDATE OR DELETE ON harvest.depot_quality_mappings FOR EACH ROW EXECUTE FUNCTION core.r2_immutable();
CREATE FUNCTION harvest.r2_mapping_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT core.r2_owner(NEW.entity_id,NEW.accepted_by)
 OR NOT EXISTS(SELECT 1 FROM core.counterparties WHERE id=NEW.depot_id AND entity_id=NEW.entity_id AND counterparty_type IN ('depot','customer','both'))
 OR NOT EXISTS(SELECT 1 FROM core.proposed_actions WHERE id=NEW.proposal_id AND entity_id=NEW.entity_id AND action_type='DEPOT_RECEIPT')
 THEN RAISE EXCEPTION 'Depot mapping scope/authority mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER depot_mapping_scope BEFORE INSERT ON harvest.depot_quality_mappings FOR EACH ROW EXECUTE FUNCTION harvest.r2_mapping_scope();
CREATE FUNCTION core.r2_finality() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p core.proposed_actions; effect core.confirmation_effects;
BEGIN
 SELECT * INTO p FROM core.proposed_actions WHERE id=NEW.id;
 IF NOT EXISTS(SELECT 1 FROM core.proposal_revisions WHERE proposal_id=p.id AND version=p.version)
 THEN RAISE EXCEPTION 'Current revision required' USING ERRCODE='23514'; END IF;
 IF p.state IN ('POSTED','CONFIRMED_NONPOSTING') THEN
 SELECT * INTO effect FROM core.confirmation_effects WHERE proposal_id=p.id;
 IF effect.proposal_id IS NULL OR effect.version<>p.version OR NOT CASE p.action_type
 WHEN 'PAYABLE' THEN effect.obligation_id IS NOT NULL WHEN 'PAYMENT' THEN effect.payment_id IS NOT NULL
 WHEN 'DIRECT_EXPENSE' THEN effect.direct_expense_id IS NOT NULL
 WHEN 'STOCK_RECEIPT' THEN effect.inventory_id IS NOT NULL WHEN 'STOCK_APPLICATION' THEN effect.inventory_id IS NOT NULL
 WHEN 'DEPOT_RECEIPT' THEN effect.settlement_id IS NOT NULL WHEN 'PERSONAL_TASK' THEN effect.owner_task_id IS NOT NULL
 WHEN 'WORKER_TASK' THEN effect.worker_task_id IS NOT NULL WHEN 'INVOICE_MATCH' THEN effect.invoice_match_id IS NOT NULL ELSE false END
 THEN RAISE EXCEPTION 'Typed owner effect required' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE CONSTRAINT TRIGGER proposal_finality AFTER INSERT OR UPDATE ON core.proposed_actions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.r2_finality();
CREATE FUNCTION core.r2_link_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT core.r2_owner(NEW.entity_id,NEW.reviewed_by)
 OR NOT EXISTS(SELECT 1 FROM core.proposed_actions p WHERE p.id=NEW.proposal_id AND p.entity_id=NEW.entity_id)
 THEN RAISE EXCEPTION 'Review link authority/scope mismatch' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='invoice_receipt_links' THEN
 IF NOT EXISTS(SELECT 1 FROM core.inbox_items WHERE id=NEW.inbox_id AND entity_id=NEW.entity_id)
 OR NOT EXISTS(SELECT 1 FROM inventory.events WHERE id=NEW.receipt_id AND entity_id=NEW.entity_id AND kind='receipt' AND status='posted')
 OR NEW.finance_obligation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM finance.obligations WHERE id=NEW.finance_obligation_id AND entity_id=NEW.entity_id AND status='posted')
 THEN RAISE EXCEPTION 'Invoice receipt scope/state mismatch' USING ERRCODE='23514'; END IF;
 ELSE
 IF NOT EXISTS(SELECT 1 FROM harvest.depot_settlements WHERE id=NEW.settlement_id AND entity_id=NEW.entity_id)
 THEN RAISE EXCEPTION 'Quality receipt scope mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER invoice_link_scope BEFORE INSERT ON core.invoice_receipt_links FOR EACH ROW EXECUTE FUNCTION core.r2_link_scope();
CREATE TRIGGER quality_link_scope BEFORE INSERT ON harvest.receipt_quality_reviews FOR EACH ROW EXECUTE FUNCTION core.r2_link_scope();
"""
