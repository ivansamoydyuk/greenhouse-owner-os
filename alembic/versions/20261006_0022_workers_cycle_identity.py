"""Preserve explicit report cycles, ingress identity and immutable review scope.
Forward-only recovery uses a separate verified backup and retained source/action
reconciliation. Existing migrations are unchanged; no history is rewritten.
"""
from alembic import op
revision='20261006_0022'
down_revision='20261006_0021'
branch_labels=depends_on=None

def upgrade():
    op.execute(r"""
DO $$ DECLARE s text; BEGIN
 s=pg_get_functiondef('workers.guard_report()'::regprocedure);
 s=replace(s,'NEW.cycle<>t.cycle OR NEW.state<>''draft''', 'NEW.cycle<1 OR NEW.cycle>t.cycle OR NEW.state<>''draft''');
 s=replace(s,'IF TG_OP=''DELETE'' THEN', 'SELECT * INTO t FROM workers.tasks WHERE id=NEW.task_id FOR UPDATE; IF TG_OP=''DELETE'' THEN');
 s=replace(s,'RETURN NEW; END', 'IF TG_OP=''INSERT'' THEN NEW.received_at=clock_timestamp(); END IF; RETURN NEW; END');
 EXECUTE s;
END $$;
CREATE FUNCTION workers.guard_occurrence_identity() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE r workers.rules%ROWTYPE; p jsonb; BEGIN
 IF NEW.rule_id IS NULL THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('workers-rule:'||NEW.template_id::text,0));
 SELECT * INTO r FROM workers.rules WHERE id=NEW.rule_id;
 p=r.definition || jsonb_build_object('source','recurrence','locator',NEW.template_id::text||':'||NEW.occurrence_date::text,'planned_date',NEW.occurrence_date::text,'due_at',NULL);
 IF NEW.interpretation IS DISTINCT FROM p THEN RAISE EXCEPTION 'Recurrence instruction must match immutable rule definition' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM workers.rules n WHERE n.entity_id=r.entity_id AND n.template_id=r.template_id AND n.version>r.version AND n.effective_from<=NEW.occurrence_date) THEN RAISE EXCEPTION 'Recurrence rule superseded for occurrence date' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER occurrence_identity BEFORE INSERT ON workers.tasks FOR EACH ROW EXECUTE FUNCTION workers.guard_occurrence_identity();
CREATE FUNCTION workers.action_received() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 PERFORM 1 FROM workers.tasks WHERE id=NEW.task_id FOR UPDATE; NEW.received_at=clock_timestamp(); RETURN NEW; END $$;
CREATE TRIGGER action_received BEFORE INSERT ON workers.actions FOR EACH ROW EXECUTE FUNCTION workers.action_received();
CREATE FUNCTION workers.inbox_report_review() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE r workers.reports%ROWTYPE; p jsonb; BEGIN
 IF NEW.task_report_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO r FROM workers.reports WHERE id=NEW.task_report_id; p=NEW.reviewed->'reviewed';
 IF (r.task_id,r.assignment_id,r.worker_id,r.cycle,r.original_language,r.claim) IS DISTINCT FROM ((p->>'task_id')::uuid,(p->>'assignment_id')::uuid,(p->>'worker_id')::uuid,(p->>'cycle')::integer,p->>'original_language',COALESCE(p->>'claim','done'))
 OR r.normalized IS DISTINCT FROM COALESCE(p->'normalized','{}'::jsonb) OR r.issues IS DISTINCT FROM COALESCE(p->'issues','[]'::jsonb) OR r.attachments IS DISTINCT FROM COALESCE(p->'attachments','[]'::jsonb)
 OR r.source_timestamp IS DISTINCT FROM COALESCE((NEW.metadata->>'source_timestamp')::timestamptz,(p->>'source_timestamp')::timestamptz) OR r.event_at IS DISTINCT FROM (p->>'event_at')::timestamptz THEN RAISE EXCEPTION 'Inbox worker report review/assignment snapshot mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER workers_inbox_review BEFORE INSERT OR UPDATE ON core.inbox_items FOR EACH ROW EXECUTE FUNCTION workers.inbox_report_review();
CREATE INDEX workers_current_reports ON workers.reports(assignment_id,cycle,submitted_at DESC,received_at DESC,id DESC) WHERE state='submitted';
ALTER TABLE workers.assignments ADD CHECK(length(rendered_text)<=10000), ADD CHECK(length(translation_version)<=200);
ALTER TABLE workers.reports ADD CHECK(jsonb_typeof(normalized)='object'), ADD CHECK(jsonb_typeof(issues)='array'), ADD CHECK(jsonb_typeof(attachments)='array'), ADD CHECK(NOT(normalized ? 'water_volume'));
""")

def downgrade():
    raise RuntimeError('Forward-only: verified backup and retained operational history reconciliation')
