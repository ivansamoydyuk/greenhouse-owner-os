"""R2 source/job/revision envelopes. Recovery preserves originals and effects.

Forward-only after use: restore a verified backup and reconcile subsequent
sources, proposals, owner decisions and existing domain journals before cutover.
"""
from alembic import op

revision = '20261008_0023'
down_revision = '20261006_0022'
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    DO $$ DECLARE c record; BEGIN
      FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='core.inbox_items'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%octet_length(file_bytes)%' LOOP
        EXECUTE format('ALTER TABLE core.inbox_items DROP CONSTRAINT %I',c.conname);
      END LOOP;
    END $$;
    ALTER TABLE core.inbox_items ADD CONSTRAINT inbox_binary_limit CHECK(octet_length(file_bytes)<=CASE WHEN metadata->>'contract'='OWNER_R2' THEN 20971520 ELSE 5242880 END);
    CREATE TABLE core.owner_source_states (
      inbox_id uuid PRIMARY KEY REFERENCES core.inbox_items(id),
      entity_id uuid NOT NULL REFERENCES core.entities(id),
      client_capture_id uuid NOT NULL, captured_at timestamptz NOT NULL,
      timezone text NOT NULL, transcript_only boolean NOT NULL DEFAULT false,
      state text NOT NULL DEFAULT 'SERVER_SAVED' CHECK(state IN ('SERVER_SAVED','PROCESSING','NEEDS_REVIEW','READY','FAILED')),
      UNIQUE(entity_id,client_capture_id));
    CREATE TABLE core.processing_jobs (
      id uuid PRIMARY KEY, inbox_id uuid NOT NULL REFERENCES core.inbox_items(id),
      entity_id uuid NOT NULL REFERENCES core.entities(id), stage text NOT NULL,
      engine_version text NOT NULL, state text NOT NULL DEFAULT 'QUEUED'
        CHECK(state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
      lease_token uuid, lease_until timestamptz, retry_at timestamptz,
      error_code text, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(inbox_id,stage,engine_version));
    CREATE INDEX processing_claim ON core.processing_jobs(state,retry_at,lease_until,created_at);
    CREATE TABLE core.extraction_runs (
      id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id),
      inbox_id uuid NOT NULL REFERENCES core.inbox_items(id), job_id uuid NOT NULL REFERENCES core.processing_jobs(id),
      attempt integer NOT NULL, provider text NOT NULL, engine_version text NOT NULL,
      input_hash text NOT NULL, raw_output jsonb NOT NULL, normalized jsonb NOT NULL,
      warnings jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(job_id,attempt));
    CREATE TABLE core.proposed_actions (
      id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id),
      inbox_id uuid NOT NULL REFERENCES core.inbox_items(id),
      slot text NOT NULL, action_type text NOT NULL CHECK(action_type IN
        ('PAYABLE','PAYMENT','DIRECT_EXPENSE','INVENTORY_RECEIPT','INVENTORY_APPLICATION','DEPOT_SETTLEMENT','PERSONAL_TASK','WORKER_TASK','INVOICE_MATCH','UNRESOLVED')),
      state text NOT NULL CHECK(state IN ('PREPARING','NEEDS_INPUT','READY','DEFERRED','POSTED','CONFIRMED_NONPOSTING','REJECTED','STALE','FAILED')),
      version integer NOT NULL DEFAULT 1 CHECK(version>0),
      priority integer NOT NULL CHECK(priority BETWEEN 1 AND 3),
      first_unresolved_at timestamptz NOT NULL DEFAULT now(),
      deferred_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(inbox_id,slot), UNIQUE(id,entity_id));
    CREATE INDEX owner_confirmation_queue ON core.proposed_actions(entity_id,priority,first_unresolved_at,id);
    CREATE TABLE core.proposal_revisions (
      proposal_id uuid NOT NULL REFERENCES core.proposed_actions(id), revision integer NOT NULL CHECK(revision>0),
      schema_version integer NOT NULL CHECK(schema_version=1),
      extraction_run_id uuid REFERENCES core.extraction_runs(id),
      payload jsonb NOT NULL, provenance jsonb NOT NULL,
      validation jsonb NOT NULL, basis jsonb NOT NULL, digest text NOT NULL,
      actor_id uuid REFERENCES core.actors(id), created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(proposal_id,revision));
    CREATE TABLE core.proposal_actions (
      id uuid PRIMARY KEY, proposal_id uuid NOT NULL REFERENCES core.proposed_actions(id),
      revision integer NOT NULL, actor_id uuid NOT NULL REFERENCES core.actors(id),
      action text NOT NULL CHECK(action IN ('EDIT','DEFER','REJECT','CONFIRM','STALE')),
      reason text, snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(proposal_id,revision) REFERENCES core.proposal_revisions(proposal_id,revision));
    CREATE UNIQUE INDEX one_owner_confirmation ON core.proposal_actions(proposal_id) WHERE action='CONFIRM';
    CREATE TABLE core.owner_personal_tasks (
      id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id),
      owner_id uuid NOT NULL REFERENCES core.actors(id), title text NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 2000),
      greenhouse_id uuid REFERENCES core.work_areas(id), planned_date date,
      note text, inbox_id uuid REFERENCES core.inbox_items(id),
      state text NOT NULL DEFAULT 'TO_DO' CHECK(state IN ('TO_DO','DONE')),
      version integer NOT NULL DEFAULT 1 CHECK(version>0), created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE core.owner_personal_task_actions (
      id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES core.owner_personal_tasks(id),
      actor_id uuid NOT NULL REFERENCES core.actors(id), revision integer NOT NULL,
      action text NOT NULL CHECK(action IN ('CREATE','DONE','REOPEN')),
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(task_id,revision));
    CREATE TABLE core.proposal_effect_links (
      proposal_id uuid PRIMARY KEY REFERENCES core.proposed_actions(id),
      entity_id uuid NOT NULL REFERENCES core.entities(id),
      obligation_id uuid UNIQUE REFERENCES finance.obligations(id),
      payment_id uuid UNIQUE REFERENCES finance.payments(id),
      inventory_id uuid UNIQUE REFERENCES inventory.events(id),
      settlement_id uuid UNIQUE REFERENCES harvest.depot_settlements(id),
      personal_task_id uuid UNIQUE REFERENCES core.owner_personal_tasks(id),
      worker_task_id uuid UNIQUE REFERENCES workers.tasks(id),
      FOREIGN KEY(proposal_id,entity_id) REFERENCES core.proposed_actions(id,entity_id),
      CHECK(num_nonnulls(obligation_id,payment_id,inventory_id,settlement_id,personal_task_id,worker_task_id)=1));
    CREATE TABLE core.owner_reminder_claims (
      proposal_id uuid NOT NULL REFERENCES core.proposed_actions(id), day date NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(proposal_id,day));
    CREATE FUNCTION core.guard_owner_envelope() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE scope uuid; actor uuid; target uuid; BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Owner source and review history cannot be deleted'; END IF;
      IF TG_TABLE_NAME='owner_source_states' THEN
        IF TG_OP='UPDATE' AND (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN RAISE EXCEPTION 'Source identity is immutable'; END IF;
        SELECT entity_id INTO scope FROM core.inbox_items WHERE id=NEW.inbox_id;
        IF scope IS DISTINCT FROM NEW.entity_id THEN RAISE EXCEPTION 'Source entity mismatch'; END IF;
      ELSIF TG_TABLE_NAME='proposed_actions' THEN
        SELECT entity_id INTO scope FROM core.inbox_items WHERE id=NEW.inbox_id;
        IF scope IS DISTINCT FROM NEW.entity_id THEN RAISE EXCEPTION 'Proposal source scope mismatch'; END IF;
        IF TG_OP='UPDATE' AND ((to_jsonb(NEW)-ARRAY['state','version','deferred_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','version','deferred_at']) OR NEW.version<>OLD.version+1 OR OLD.state IN ('POSTED','CONFIRMED_NONPOSTING','REJECTED')) THEN RAISE EXCEPTION 'Immutable proposal identity/final state'; END IF;
      ELSIF TG_TABLE_NAME='proposal_actions' THEN
        IF NOT EXISTS(SELECT 1 FROM core.actors WHERE id=NEW.actor_id AND active AND actor_type='owner') THEN RAISE EXCEPTION 'Only active owner may decide'; END IF;
      ELSIF TG_TABLE_NAME='proposal_effect_links' THEN
        IF NOT EXISTS(SELECT 1 FROM core.proposal_actions WHERE proposal_id=NEW.proposal_id AND action='CONFIRM') THEN RAISE EXCEPTION 'Effect requires owner confirmation'; END IF;
        target=coalesce(NEW.obligation_id,NEW.payment_id,NEW.inventory_id,NEW.settlement_id,NEW.personal_task_id,NEW.worker_task_id);
        IF NEW.obligation_id IS NOT NULL THEN SELECT entity_id INTO scope FROM finance.obligations WHERE id=target;
        ELSIF NEW.payment_id IS NOT NULL THEN SELECT entity_id INTO scope FROM finance.payments WHERE id=target;
        ELSIF NEW.inventory_id IS NOT NULL THEN SELECT entity_id INTO scope FROM inventory.events WHERE id=target;
        ELSIF NEW.settlement_id IS NOT NULL THEN SELECT entity_id INTO scope FROM harvest.depot_settlements WHERE id=target;
        ELSIF NEW.personal_task_id IS NOT NULL THEN SELECT entity_id INTO scope FROM core.owner_personal_tasks WHERE id=target;
        ELSE SELECT entity_id INTO scope FROM workers.tasks WHERE id=target; END IF;
        IF scope IS DISTINCT FROM NEW.entity_id THEN RAISE EXCEPTION 'Effect entity mismatch'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER owner_source_guard BEFORE INSERT OR UPDATE OR DELETE ON core.owner_source_states FOR EACH ROW EXECUTE FUNCTION core.guard_owner_envelope();
    CREATE TRIGGER proposal_guard BEFORE INSERT OR UPDATE OR DELETE ON core.proposed_actions FOR EACH ROW EXECUTE FUNCTION core.guard_owner_envelope();
    CREATE TRIGGER decision_guard BEFORE INSERT ON core.proposal_actions FOR EACH ROW EXECUTE FUNCTION core.guard_owner_envelope();
    CREATE TRIGGER effect_guard BEFORE INSERT ON core.proposal_effect_links FOR EACH ROW EXECUTE FUNCTION core.guard_owner_envelope();
    CREATE TRIGGER extraction_immutable BEFORE UPDATE OR DELETE ON core.extraction_runs FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    CREATE TRIGGER revision_immutable BEFORE UPDATE OR DELETE ON core.proposal_revisions FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    CREATE TRIGGER decision_immutable BEFORE UPDATE OR DELETE ON core.proposal_actions FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    CREATE TRIGGER effect_immutable BEFORE UPDATE OR DELETE ON core.proposal_effect_links FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    CREATE TRIGGER personal_action_immutable BEFORE UPDATE OR DELETE ON core.owner_personal_task_actions FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    CREATE TRIGGER reminder_immutable BEFORE UPDATE OR DELETE ON core.owner_reminder_claims FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    CREATE FUNCTION core.verify_owner_result() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE p core.proposed_actions%ROWTYPE; link core.proposal_effect_links%ROWTYPE; BEGIN
      SELECT * INTO p FROM core.proposed_actions WHERE id=NEW.id;
      IF NOT EXISTS(SELECT 1 FROM core.proposal_revisions WHERE proposal_id=p.id AND revision=p.version) THEN RAISE EXCEPTION 'Current proposal revision required'; END IF;
      SELECT * INTO link FROM core.proposal_effect_links WHERE proposal_id=p.id;
      IF p.state IN ('POSTED','CONFIRMED_NONPOSTING') THEN
        IF link.proposal_id IS NULL OR NOT EXISTS(SELECT 1 FROM core.proposal_actions WHERE proposal_id=p.id AND action='CONFIRM') THEN RAISE EXCEPTION 'Final effect requires owner snapshot and typed link'; END IF;
        IF NOT ((p.action_type='PAYABLE' AND link.obligation_id IS NOT NULL) OR (p.action_type='PAYMENT' AND link.payment_id IS NOT NULL) OR (p.action_type IN ('INVENTORY_RECEIPT','INVENTORY_APPLICATION') AND link.inventory_id IS NOT NULL) OR (p.action_type='DEPOT_SETTLEMENT' AND link.settlement_id IS NOT NULL) OR (p.action_type='PERSONAL_TASK' AND link.personal_task_id IS NOT NULL) OR (p.action_type='WORKER_TASK' AND link.worker_task_id IS NOT NULL)) THEN RAISE EXCEPTION 'Typed effect mismatch'; END IF;
      ELSIF link.proposal_id IS NOT NULL OR EXISTS(SELECT 1 FROM core.proposal_actions WHERE proposal_id=p.id AND action='CONFIRM') THEN RAISE EXCEPTION 'Unconfirmed proposal cannot have effect'; END IF;
      IF p.state='REJECTED' AND NOT EXISTS(SELECT 1 FROM core.proposal_actions WHERE proposal_id=p.id AND action='REJECT') THEN RAISE EXCEPTION 'Rejected proposal requires owner action'; END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER owner_result_integrity AFTER INSERT OR UPDATE ON core.proposed_actions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION core.verify_owner_result();
    """)


def downgrade():
    raise RuntimeError('Forward-only after use: verified backup and source/proposal/effect reconciliation required')
