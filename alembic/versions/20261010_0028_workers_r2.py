"""Versioned R2 worker responses, preserving every R1 source and lifecycle.

Forward-only: restore a verified backup separately and reconcile immutable
instructions, response sources, assignments, reports and owner decisions.
"""
from alembic import op
revision='20261010_0028'
down_revision='20261010_0027'
branch_labels=depends_on=None


def upgrade():
    op.execute(r"""
ALTER TABLE workers.tasks ADD COLUMN execution_contract text NOT NULL DEFAULT 'R1'
 CHECK(execution_contract IN ('R1','R2_RECEIVED_REPORT'));
CREATE FUNCTION workers.execution_contract_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.execution_contract IS DISTINCT FROM COALESCE(NEW.interpretation->>'execution_contract','R1')
 THEN RAISE EXCEPTION 'Execution contract must match immutable instruction' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER execution_contract_guard BEFORE INSERT OR UPDATE ON workers.tasks
 FOR EACH ROW EXECUTE FUNCTION workers.execution_contract_guard();
CREATE FUNCTION workers.approved_execution_contract() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.worker_task_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM workers.tasks t JOIN core.proposal_revisions r ON r.proposal_id=NEW.proposal_id AND r.version=NEW.version AND r.entity_id=NEW.entity_id
  WHERE t.id=NEW.worker_task_id AND t.entity_id=NEW.entity_id AND t.execution_contract=COALESCE(r.payload->>'execution_contract','R1'))
 THEN RAISE EXCEPTION 'Worker execution contract differs from approved revision' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE CONSTRAINT TRIGGER approved_worker_contract AFTER INSERT ON core.confirmation_effects
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION workers.approved_execution_contract();

-- Only the R1 started requirement changes. All-team done, issue review,
-- owner expected-version service gate, reopen and immutable history remain.
DO $$ DECLARE s text; needle text; BEGIN
 s=pg_get_functiondef('workers.apply_action()'::regprocedure);
 needle='a.acknowledged_at IS NULL OR a.started_at IS NULL';
 IF position(needle IN s)=0 THEN RAISE EXCEPTION 'Expected R1 eligibility function missing'; END IF;
 s=replace(s,needle,'a.acknowledged_at IS NULL OR (t.execution_contract=''R1'' AND a.started_at IS NULL)');
 EXECUTE s;
END $$;

CREATE FUNCTION workers.r2_assignment_language() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM workers.tasks WHERE id=NEW.task_id AND execution_contract='R2_RECEIVED_REPORT')
 AND NEW.language IS DISTINCT FROM (SELECT language FROM workers.people WHERE id=NEW.worker_id)
 THEN RAISE EXCEPTION 'R2 requires reviewed worker-language rendering' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER r2_assignment_language BEFORE INSERT ON workers.assignments
 FOR EACH ROW EXECUTE FUNCTION workers.r2_assignment_language();

CREATE FUNCTION workers.r2_ui_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb; t workers.tasks; a workers.assignments; w workers.people; k text;
 keys text[]=ARRAY['schema_version','source_kind','entity_id','task_id','worker_id','actor_id','execution_contract','client_response_id','assignment_id','expected_cycle','response','captured_at','button_label','original_language','comment'];
BEGIN
 IF NEW.source NOT IN ('worker:r2-ui','worker:r2-ui:report','worker:r2-ui:action') AND NEW.event_type<>'r2_ui_response' THEN RETURN NEW; END IF;
 IF NEW.source<>'worker:r2-ui' THEN
  IF NEW.event_type IS DISTINCT FROM (CASE NEW.source WHEN 'worker:r2-ui:report' THEN 'reports' WHEN 'worker:r2-ui:action' THEN 'actions' ELSE NULL END)
  OR NOT EXISTS(SELECT 1 FROM workers.sources WHERE entity_id=NEW.entity_id AND source='worker:r2-ui' AND locator=NEW.locator AND event_type='r2_ui_response')
  THEN RAISE EXCEPTION 'R2 child requires typed parent source' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 p=NEW.snapshot;
 IF NEW.event_type<>'r2_ui_response' OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR NOT(p ?& keys) OR p-keys<>'{}'::jsonb
 OR p->'schema_version' IS DISTINCT FROM '1'::jsonb OR p->>'source_kind' IS DISTINCT FROM 'UI_ACTION'
 OR p->>'execution_contract' IS DISTINCT FROM 'R2_RECEIVED_REPORT'
 THEN RAISE EXCEPTION 'Strict R2 UI_ACTION snapshot required' USING ERRCODE='23514'; END IF;
 FOREACH k IN ARRAY ARRAY['entity_id','task_id','worker_id','actor_id','execution_contract','client_response_id','assignment_id','response','captured_at','button_label','original_language'] LOOP
  IF jsonb_typeof(p->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'R2 source field must be text' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF jsonb_typeof(p->'expected_cycle') IS DISTINCT FROM 'number' OR p->>'expected_cycle' !~ '^[1-9][0-9]*$'
 OR p->>'response' NOT IN ('RECEIVED','DONE','PROBLEM') OR p->>'original_language' NOT IN ('ru','tr','fa','uz')
 OR length(btrim(p->>'button_label')) NOT BETWEEN 1 AND 200
 OR p->>'captured_at' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
 OR (p->'comment'<>'null'::jsonb AND (jsonb_typeof(p->'comment') IS DISTINCT FROM 'string' OR length(p->>'comment')>10000))
 OR (p->>'client_response_id')::uuid::text IS DISTINCT FROM NEW.locator OR (p->>'entity_id')::uuid<>NEW.entity_id
 THEN RAISE EXCEPTION 'Invalid R2 source values' USING ERRCODE='23514'; END IF;
 PERFORM (p->>'captured_at')::timestamptz;
 -- Same entity advisory -> task order as the trusted Workers service.
 PERFORM pg_advisory_xact_lock(hashtextextended('workers:'||NEW.entity_id::text,0));
 SELECT * INTO t FROM workers.tasks WHERE id=(p->>'task_id')::uuid FOR UPDATE;
 SELECT * INTO a FROM workers.assignments WHERE id=(p->>'assignment_id')::uuid;
 SELECT * INTO w FROM workers.people WHERE id=(p->>'worker_id')::uuid;
 IF t.id IS NULL OR t.entity_id<>NEW.entity_id OR t.execution_contract<>'R2_RECEIVED_REPORT'
 OR a.id IS NULL OR (a.entity_id,a.task_id,a.worker_id,a.cycle) IS DISTINCT FROM (NEW.entity_id,t.id,w.id,(p->>'expected_cycle')::int)
 OR t.cycle<>(p->>'expected_cycle')::int OR w.id IS NULL OR NOT w.active OR w.entity_id<>NEW.entity_id OR w.actor_id IS DISTINCT FROM (p->>'actor_id')::uuid
 THEN RAISE EXCEPTION 'R2 source assignment/actor/cycle mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r2_ui_source_guard BEFORE INSERT ON workers.sources FOR EACH ROW EXECUTE FUNCTION workers.r2_ui_source_guard();

CREATE FUNCTION workers.r2_action_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t workers.tasks; p jsonb; a workers.assignments;
BEGIN
 SELECT * INTO t FROM workers.tasks WHERE id=NEW.task_id FOR UPDATE;
 IF t.execution_contract<>'R2_RECEIVED_REPORT' THEN
  IF NEW.source='worker:r2-ui:action' THEN RAISE EXCEPTION 'R2 response cannot act on R1' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.kind='start' THEN RAISE EXCEPTION 'R2 has no START action' USING ERRCODE='23514'; END IF;
 IF NEW.kind NOT IN ('acknowledge','submit_report') THEN RETURN NEW; END IF;
 SELECT snapshot INTO p FROM workers.sources WHERE entity_id=NEW.entity_id AND source='worker:r2-ui' AND locator=NEW.locator AND event_type='r2_ui_response';
 SELECT * INTO a FROM workers.assignments WHERE id=NEW.assignment_id;
 IF NEW.source<>'worker:r2-ui:action' OR p IS NULL
 OR (NEW.task_id,NEW.assignment_id,NEW.worker_id,NEW.cycle) IS DISTINCT FROM ((p->>'task_id')::uuid,(p->>'assignment_id')::uuid,(p->>'worker_id')::uuid,(p->>'expected_cycle')::int)
 OR NEW.actor_id IS NOT NULL OR NEW.source_timestamp IS DISTINCT FROM (p->>'captured_at')::timestamptz
 OR (NEW.kind='acknowledge') IS DISTINCT FROM (p->>'response'='RECEIVED')
 OR (p->>'response'='DONE' AND a.acknowledged_at IS NULL)
 THEN RAISE EXCEPTION 'R2 action requires current typed worker response' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r2_action_guard BEFORE INSERT ON workers.actions FOR EACH ROW EXECUTE FUNCTION workers.r2_action_guard();

CREATE FUNCTION workers.r2_response_finality() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb; a workers.actions; r workers.reports; expected jsonb;
BEGIN
 IF NEW.source NOT IN ('worker:r2-ui','worker:r2-ui:report','worker:r2-ui:action') THEN RETURN NEW; END IF;
 SELECT snapshot INTO p FROM workers.sources WHERE entity_id=NEW.entity_id AND source='worker:r2-ui' AND locator=NEW.locator AND event_type='r2_ui_response';
 SELECT * INTO a FROM workers.actions WHERE entity_id=NEW.entity_id AND source='worker:r2-ui:action' AND locator=NEW.locator;
 IF p IS NULL OR a.id IS NULL OR a.task_id IS DISTINCT FROM (p->>'task_id')::uuid OR a.assignment_id IS DISTINCT FROM (p->>'assignment_id')::uuid
 OR a.worker_id IS DISTINCT FROM (p->>'worker_id')::uuid OR a.cycle IS DISTINCT FROM (p->>'expected_cycle')::int OR a.source_timestamp IS DISTINCT FROM (p->>'captured_at')::timestamptz
 THEN RAISE EXCEPTION 'R2 response must commit matching action atomically' USING ERRCODE='23514'; END IF;
 IF p->>'response'='RECEIVED' THEN
  IF a.kind<>'acknowledge' OR a.report_id IS NOT NULL OR a.reason IS DISTINCT FROM p->>'button_label'
  OR EXISTS(SELECT 1 FROM workers.reports WHERE entity_id=NEW.entity_id AND source='worker:r2-ui:report' AND locator=NEW.locator)
  THEN RAISE EXCEPTION 'Received response cannot invent report/start' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO r FROM workers.reports WHERE entity_id=NEW.entity_id AND source='worker:r2-ui:report' AND locator=NEW.locator;
  expected=jsonb_build_object('source_kind','UI_ACTION','response',p->>'response','button_label',p->>'button_label','comment_present',p->'comment'<>'null'::jsonb);
  IF r.id IS NULL OR a.kind<>'submit_report' OR a.report_id IS DISTINCT FROM r.id OR a.reason<>''
  OR (r.task_id,r.assignment_id,r.worker_id,r.cycle) IS DISTINCT FROM (a.task_id,a.assignment_id,a.worker_id,a.cycle)
  OR r.state<>'submitted' OR r.claim IS DISTINCT FROM (CASE p->>'response' WHEN 'DONE' THEN 'done' ELSE 'issue' END)
  OR r.original_text IS DISTINCT FROM COALESCE(NULLIF(p->>'comment',''),p->>'button_label') OR r.original_language IS DISTINCT FROM p->>'original_language'
  OR r.normalized IS DISTINCT FROM expected OR r.issues<>'[]'::jsonb OR r.attachments<>'[]'::jsonb
  OR r.source_timestamp IS DISTINCT FROM (p->>'captured_at')::timestamptz OR r.event_at IS DISTINCT FROM (p->>'captured_at')::timestamptz
  THEN RAISE EXCEPTION 'R2 report must match exact UI evidence and submission' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER r2_response_finality AFTER INSERT ON workers.sources
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION workers.r2_response_finality();
""")


def downgrade():
    raise RuntimeError('0028 is forward-only: verified backup and immutable worker evidence reconciliation')
