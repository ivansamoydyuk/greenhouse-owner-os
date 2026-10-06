"""Operational tasks; append-only lifecycle and separately reviewed reports.
Forward recovery: restore verified backup separately, preserve/reconcile all
subsequent original instructions, reports, actions and recurrence occurrences.
No worker/task bootstrap or effects in Finance, Harvest or Inventory.
"""
from alembic import op
revision='20261006_0019'
down_revision='20261006_0018'
branch_labels=depends_on=None

def upgrade():
    op.execute(r"""
CREATE SCHEMA workers;
CREATE TABLE workers.people(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entity_id uuid NOT NULL REFERENCES core.entities,
 actor_id uuid NOT NULL UNIQUE REFERENCES core.actors, display_name text NOT NULL CHECK(length(trim(display_name)) BETWEEN 1 AND 200), native_name text,
 language text NOT NULL CHECK(language IN ('ru','tr','fa','uz')), active boolean NOT NULL DEFAULT true,
 contact text, telegram_ref text, notes text, source text NOT NULL, created_by uuid NOT NULL REFERENCES core.actors,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(entity_id,id), UNIQUE(entity_id,telegram_ref)
);
CREATE TABLE workers.rules(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entity_id uuid NOT NULL REFERENCES core.entities,
 template_id uuid NOT NULL, version integer NOT NULL CHECK(version>0), replaces_id uuid UNIQUE REFERENCES workers.rules,
 definition jsonb NOT NULL CHECK(jsonb_typeof(definition)='object'),
 frequency text NOT NULL CHECK(frequency IN ('daily','weekly')), weekday integer CHECK(weekday BETWEEN 0 AND 6),
 effective_from date NOT NULL, effective_to date, active boolean NOT NULL DEFAULT true,
 created_by uuid NOT NULL REFERENCES core.actors, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((frequency='weekly')=(weekday IS NOT NULL)), CHECK(effective_to IS NULL OR effective_to>=effective_from),
 UNIQUE(entity_id,template_id,version)
);
CREATE TABLE workers.tasks(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entity_id uuid NOT NULL REFERENCES core.entities,
 owner_language text NOT NULL CHECK(owner_language IN ('ru','tr','fa','uz')), title text NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200), original_instruction text NOT NULL CHECK(length(trim(original_instruction)) BETWEEN 1 AND 10000),
 description text NOT NULL, category text NOT NULL, greenhouse_id uuid REFERENCES core.work_areas,
 work_area_id uuid REFERENCES core.work_areas, priority text NOT NULL CHECK(priority IN ('normal','high','urgent')),
 planned_date date, due_at timestamptz, status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','assigned','acknowledged','in_progress','reported','completed','reopened','cancelled')),
 cycle integer NOT NULL DEFAULT 1 CHECK(cycle>0), version integer NOT NULL DEFAULT 1 CHECK(version>0),
 source text NOT NULL, locator text NOT NULL, source_timestamp timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
 interpretation jsonb NOT NULL, gaps jsonb NOT NULL DEFAULT '[]',
 rule_id uuid REFERENCES workers.rules, template_id uuid, occurrence_date date,
 created_by uuid NOT NULL REFERENCES core.actors, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz, cancelled_at timestamptz,
 UNIQUE(entity_id,id), UNIQUE(entity_id,source,locator), UNIQUE(entity_id,template_id,occurrence_date),
 CHECK((rule_id IS NULL)=(template_id IS NULL) AND (template_id IS NULL)=(occurrence_date IS NULL)),
 CHECK((status='completed')=(completed_at IS NOT NULL)), CHECK((status='cancelled')=(cancelled_at IS NOT NULL))
);
CREATE TABLE workers.assignments(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entity_id uuid NOT NULL, task_id uuid NOT NULL, worker_id uuid NOT NULL,
 assigned_at timestamptz NOT NULL DEFAULT now(), assigned_by uuid NOT NULL REFERENCES core.actors,
 language text NOT NULL CHECK(language IN ('ru','tr','fa','uz')), rendered_text text NOT NULL,
 translation_source text NOT NULL, translation_version text NOT NULL, rendering_reviewed boolean NOT NULL,
 acknowledged_at timestamptz, started_at timestamptz, cycle integer NOT NULL DEFAULT 1,
 UNIQUE(task_id,worker_id), UNIQUE(entity_id,id),
 FOREIGN KEY(entity_id,task_id) REFERENCES workers.tasks(entity_id,id), FOREIGN KEY(entity_id,worker_id) REFERENCES workers.people(entity_id,id)
);
CREATE TABLE workers.reports(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entity_id uuid NOT NULL, task_id uuid NOT NULL, assignment_id uuid NOT NULL,
 worker_id uuid NOT NULL, cycle integer NOT NULL, original_text text NOT NULL CHECK(length(trim(original_text)) BETWEEN 1 AND 10000),
 original_language text NOT NULL CHECK(original_language IN ('ru','tr','fa','uz')), normalized jsonb NOT NULL DEFAULT '{}',
 issues jsonb NOT NULL DEFAULT '[]', attachments jsonb NOT NULL DEFAULT '[]', claim text NOT NULL CHECK(claim IN ('done','issue','note')),
 source text NOT NULL, locator text NOT NULL, source_timestamp timestamptz, received_at timestamptz NOT NULL DEFAULT now(), event_at timestamptz,
 state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','submitted')), submitted_at timestamptz,
 UNIQUE(entity_id,source,locator), UNIQUE(entity_id,id),
 FOREIGN KEY(entity_id,task_id) REFERENCES workers.tasks(entity_id,id), FOREIGN KEY(entity_id,assignment_id) REFERENCES workers.assignments(entity_id,id),
 FOREIGN KEY(entity_id,worker_id) REFERENCES workers.people(entity_id,id), CHECK((state='submitted')=(submitted_at IS NOT NULL))
);
CREATE TABLE workers.actions(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entity_id uuid NOT NULL, task_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('assign','acknowledge','start','submit_report','complete','reopen','cancel')),
 assignment_id uuid REFERENCES workers.assignments, report_id uuid REFERENCES workers.reports,
 actor_id uuid REFERENCES core.actors, worker_id uuid REFERENCES workers.people,
 cycle integer NOT NULL, reason text NOT NULL DEFAULT '', source text NOT NULL, locator text NOT NULL,
 source_timestamp timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(entity_id,source,locator), FOREIGN KEY(entity_id,task_id) REFERENCES workers.tasks(entity_id,id),
 CHECK((actor_id IS NULL)<>(worker_id IS NULL))
);
CREATE UNIQUE INDEX workers_ack_once ON workers.actions(assignment_id,cycle,kind) WHERE kind IN ('acknowledge','start');
CREATE UNIQUE INDEX workers_submit_once ON workers.actions(report_id) WHERE kind='submit_report';
CREATE UNIQUE INDEX workers_assign_once ON workers.actions(assignment_id) WHERE kind='assign';
CREATE TABLE workers.links(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entity_id uuid NOT NULL, task_id uuid NOT NULL,
 domain text NOT NULL CHECK(domain IN ('inventory','harvest','finance','inbox')), target_id uuid NOT NULL,
 created_by uuid NOT NULL REFERENCES core.actors, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(task_id,domain,target_id), FOREIGN KEY(entity_id,task_id) REFERENCES workers.tasks(entity_id,id)
);
CREATE FUNCTION workers.immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'Operational history is immutable' USING ERRCODE='55000'; END $$;
CREATE TRIGGER actions_immutable BEFORE UPDATE OR DELETE ON workers.actions FOR EACH ROW EXECUTE FUNCTION workers.immutable();
CREATE TRIGGER rules_immutable BEFORE UPDATE OR DELETE ON workers.rules FOR EACH ROW EXECUTE FUNCTION workers.immutable();
CREATE TRIGGER links_immutable BEFORE UPDATE OR DELETE ON workers.links FOR EACH ROW EXECUTE FUNCTION workers.immutable();
CREATE FUNCTION workers.require_owner() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM core.actors WHERE id=NEW.created_by AND actor_type='owner' AND active) THEN RAISE EXCEPTION 'Owner creator required' USING ERRCODE='23514'; END IF;RETURN NEW; END $$;
CREATE TRIGGER task_owner BEFORE INSERT ON workers.tasks FOR EACH ROW EXECUTE FUNCTION workers.require_owner();
CREATE TRIGGER rule_owner BEFORE INSERT ON workers.rules FOR EACH ROW EXECUTE FUNCTION workers.require_owner();
CREATE TRIGGER person_owner BEFORE INSERT ON workers.people FOR EACH ROW EXECUTE FUNCTION workers.require_owner();
CREATE TRIGGER link_owner BEFORE INSERT ON workers.links FOR EACH ROW EXECUTE FUNCTION workers.require_owner();
CREATE FUNCTION workers.guard_task() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE a core.work_areas%ROWTYPE; BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Task history cannot be deleted' USING ERRCODE='55000'; END IF;
 IF TG_OP='UPDATE' THEN
  IF pg_trigger_depth()<2 OR (to_jsonb(NEW)-ARRAY['status','cycle','version','completed_at','cancelled_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','cycle','version','completed_at','cancelled_at']) THEN RAISE EXCEPTION 'Use explicit lifecycle action; instruction is immutable' USING ERRCODE='55000'; END IF;
 ELSE
  IF NEW.status<>'draft' OR NEW.cycle<>1 OR NEW.version<>1 THEN RAISE EXCEPTION 'Task must start as draft' USING ERRCODE='23514'; END IF;
 END IF;
 IF (NEW.original_instruction,NEW.title,NEW.description,NEW.category,NEW.source,NEW.locator,NEW.owner_language) IS DISTINCT FROM (NEW.interpretation->>'original_instruction',NEW.interpretation->>'title',NEW.interpretation->>'description',NEW.interpretation->>'category',NEW.interpretation->>'source',NEW.interpretation->>'locator',NEW.interpretation->>'owner_language') THEN RAISE EXCEPTION 'Instruction source snapshot mismatch' USING ERRCODE='23514'; END IF;
 IF (NEW.greenhouse_id,NEW.work_area_id,NEW.priority,NEW.planned_date,NEW.due_at,NEW.source_timestamp) IS DISTINCT FROM ((NEW.interpretation->>'greenhouse_id')::uuid,(NEW.interpretation->>'work_area_id')::uuid,NEW.interpretation->>'priority',(NEW.interpretation->>'planned_date')::date,(NEW.interpretation->>'due_at')::timestamptz,(NEW.interpretation->>'source_timestamp')::timestamptz) THEN RAISE EXCEPTION 'Structured instruction snapshot mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.greenhouse_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=NEW.greenhouse_id AND entity_id=NEW.entity_id AND kind='greenhouse') THEN RAISE EXCEPTION 'Invalid greenhouse scope' USING ERRCODE='23514'; END IF;
 IF NEW.work_area_id IS NOT NULL THEN SELECT * INTO a FROM core.work_areas WHERE id=NEW.work_area_id;
  IF NOT FOUND OR a.entity_id<>NEW.entity_id OR a.parent_id IS DISTINCT FROM NEW.greenhouse_id THEN RAISE EXCEPTION 'Invalid work area scope' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.rule_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workers.rules WHERE id=NEW.rule_id AND entity_id=NEW.entity_id AND template_id=NEW.template_id) THEN RAISE EXCEPTION 'Invalid recurrence scope' USING ERRCODE='23514'; END IF;
 IF NEW.rule_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workers.rules r WHERE r.id=NEW.rule_id AND r.active AND NEW.occurrence_date>=r.effective_from AND (r.effective_to IS NULL OR NEW.occurrence_date<=r.effective_to) AND (r.frequency='daily' OR (extract(isodow FROM NEW.occurrence_date)::integer-1)=r.weekday) AND NOT EXISTS(SELECT 1 FROM workers.rules n WHERE n.template_id=r.template_id AND n.entity_id=r.entity_id AND n.version>r.version AND n.effective_from<=NEW.occurrence_date)) THEN RAISE EXCEPTION 'Invalid recurrence occurrence' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER task_guard BEFORE INSERT OR UPDATE OR DELETE ON workers.tasks FOR EACH ROW EXECUTE FUNCTION workers.guard_task();
CREATE FUNCTION workers.guard_assignment() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE t workers.tasks%ROWTYPE; w workers.people%ROWTYPE; BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Assignment is immutable' USING ERRCODE='55000'; END IF;
 IF TG_OP='UPDATE' THEN
  IF pg_trigger_depth()<2 OR (to_jsonb(NEW)-ARRAY['acknowledged_at','started_at','cycle']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['acknowledged_at','started_at','cycle']) THEN RAISE EXCEPTION 'Use assignment lifecycle actions' USING ERRCODE='55000'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM core.actors WHERE id=NEW.assigned_by AND actor_type='owner' AND active) THEN RAISE EXCEPTION 'Owner assignment required' USING ERRCODE='23514'; END IF;
  SELECT * INTO t FROM workers.tasks WHERE id=NEW.task_id FOR UPDATE;
  SELECT * INTO w FROM workers.people WHERE id=NEW.worker_id;
  IF t.status NOT IN ('draft','assigned','reopened') OR NOT w.active OR t.entity_id<>NEW.entity_id OR w.entity_id<>NEW.entity_id OR NEW.cycle<>t.cycle OR NEW.acknowledged_at IS NOT NULL OR NEW.started_at IS NOT NULL OR NOT NEW.rendering_reviewed THEN RAISE EXCEPTION 'Invalid assignment/review' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.language IS DISTINCT FROM (CASE WHEN NEW.translation_source='original' THEN (SELECT owner_language FROM workers.tasks WHERE id=NEW.task_id) ELSE (SELECT language FROM workers.people WHERE id=NEW.worker_id) END) THEN RAISE EXCEPTION 'Rendering language mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.translation_source='original' AND NEW.rendered_text IS DISTINCT FROM (SELECT original_instruction FROM workers.tasks WHERE id=NEW.task_id) THEN RAISE EXCEPTION 'Original rendering changed' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER assignment_guard BEFORE INSERT OR UPDATE OR DELETE ON workers.assignments FOR EACH ROW EXECUTE FUNCTION workers.guard_assignment();
CREATE FUNCTION workers.guard_report() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE a workers.assignments%ROWTYPE; t workers.tasks%ROWTYPE; BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Report original is immutable' USING ERRCODE='55000'; END IF;
 IF TG_OP='UPDATE' THEN
  IF pg_trigger_depth()<2 OR OLD.state<>'draft' OR NEW.state<>'submitted' OR (to_jsonb(NEW)-ARRAY['state','submitted_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','submitted_at']) THEN RAISE EXCEPTION 'Report original is immutable' USING ERRCODE='55000'; END IF;
 ELSE
  SELECT * INTO a FROM workers.assignments WHERE id=NEW.assignment_id;
  SELECT * INTO t FROM workers.tasks WHERE id=NEW.task_id FOR UPDATE;
  IF NOT FOUND OR (a.entity_id,a.task_id,a.worker_id) IS DISTINCT FROM (NEW.entity_id,NEW.task_id,NEW.worker_id) OR NEW.cycle<>t.cycle OR NEW.state<>'draft' THEN RAISE EXCEPTION 'Report assignment mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.normalized ? 'greenhouse_id' AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=(NEW.normalized->>'greenhouse_id')::uuid AND entity_id=NEW.entity_id AND kind='greenhouse') THEN RAISE EXCEPTION 'Report greenhouse scope mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.normalized ? 'work_area_id' AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=(NEW.normalized->>'work_area_id')::uuid AND entity_id=NEW.entity_id AND parent_id=COALESCE((NEW.normalized->>'greenhouse_id')::uuid,t.greenhouse_id)) THEN RAISE EXCEPTION 'Report work area scope mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER report_guard BEFORE INSERT OR UPDATE OR DELETE ON workers.reports FOR EACH ROW EXECUTE FUNCTION workers.guard_report();
CREATE FUNCTION workers.apply_action() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE t workers.tasks%ROWTYPE; a workers.assignments%ROWTYPE; r workers.reports%ROWTYPE; s text; BEGIN
 SELECT * INTO t FROM workers.tasks WHERE id=NEW.task_id FOR UPDATE;
 IF NOT FOUND OR t.entity_id<>NEW.entity_id OR t.cycle<>NEW.cycle THEN RAISE EXCEPTION 'Task/cycle mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.assignment_id IS NOT NULL THEN SELECT * INTO a FROM workers.assignments WHERE id=NEW.assignment_id;
  IF NOT FOUND OR (a.entity_id,a.task_id) IS DISTINCT FROM (NEW.entity_id,NEW.task_id) THEN RAISE EXCEPTION 'Assignment scope mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.kind IN ('acknowledge','start','submit_report') THEN
  IF NEW.worker_id IS NULL OR a.worker_id IS DISTINCT FROM NEW.worker_id OR NOT EXISTS(SELECT 1 FROM workers.people WHERE id=NEW.worker_id AND active) THEN RAISE EXCEPTION 'Worker identity mismatch' USING ERRCODE='23514'; END IF;
 ELSE IF NEW.actor_id IS NULL OR NOT EXISTS(SELECT 1 FROM core.actors WHERE id=NEW.actor_id AND actor_type='owner' AND active) THEN RAISE EXCEPTION 'Owner action required' USING ERRCODE='23514'; END IF; END IF;
 s=t.status;
 CASE NEW.kind
 WHEN 'assign' THEN
  IF s NOT IN ('draft','assigned','reopened') OR a.id IS NULL THEN RAISE EXCEPTION 'Task not assignable' USING ERRCODE='23514'; END IF;s='assigned';
 WHEN 'acknowledge' THEN
  IF s IN ('draft','completed','cancelled') OR a.acknowledged_at IS NOT NULL THEN RAISE EXCEPTION 'Invalid acknowledgement' USING ERRCODE='23514'; END IF;
  UPDATE workers.assignments SET acknowledged_at=NEW.received_at WHERE id=a.id;
  IF s IN ('assigned','reopened') THEN s='acknowledged'; END IF;
 WHEN 'start' THEN
  IF s IN ('draft','completed','cancelled') OR a.acknowledged_at IS NULL OR a.started_at IS NOT NULL THEN RAISE EXCEPTION 'Acknowledge before start' USING ERRCODE='23514'; END IF;
  UPDATE workers.assignments SET started_at=NEW.received_at WHERE id=a.id; IF s<>'reported' THEN s='in_progress'; END IF;
 WHEN 'submit_report' THEN
  SELECT * INTO r FROM workers.reports WHERE id=NEW.report_id FOR UPDATE;
  IF NOT FOUND OR (r.entity_id,r.task_id,r.assignment_id,r.worker_id,r.cycle) IS DISTINCT FROM (NEW.entity_id,NEW.task_id,NEW.assignment_id,NEW.worker_id,NEW.cycle) OR r.state<>'draft' THEN RAISE EXCEPTION 'Invalid report submission' USING ERRCODE='23514'; END IF;
  UPDATE workers.reports SET state='submitted',submitted_at=NEW.received_at WHERE id=r.id;
  IF s NOT IN ('completed','cancelled') THEN s='reported'; END IF;
 WHEN 'complete' THEN
  IF s<>'reported' OR NOT EXISTS(SELECT 1 FROM workers.reports WHERE task_id=t.id AND cycle=t.cycle AND state='submitted') OR length(trim(NEW.reason))=0 THEN RAISE EXCEPTION 'Owner must review a submitted report' USING ERRCODE='23514'; END IF;s='completed';
 WHEN 'reopen' THEN
  IF s NOT IN ('reported','completed') OR length(trim(NEW.reason))=0 THEN RAISE EXCEPTION 'Explicit reopen reason required' USING ERRCODE='23514'; END IF;s='reopened';
  UPDATE workers.assignments SET cycle=t.cycle+1,acknowledged_at=NULL,started_at=NULL WHERE task_id=t.id;
 WHEN 'cancel' THEN
  IF s IN ('completed','cancelled') OR length(trim(NEW.reason))=0 THEN RAISE EXCEPTION 'Terminal task cannot cancel' USING ERRCODE='23514'; END IF;s='cancelled';
 END CASE;
 UPDATE workers.tasks SET status=s,version=version+1,cycle=cycle+CASE WHEN NEW.kind='reopen' THEN 1 ELSE 0 END,
 completed_at=CASE WHEN s='completed' THEN COALESCE(completed_at,NEW.received_at) ELSE NULL END,
 cancelled_at=CASE WHEN s='cancelled' THEN COALESCE(cancelled_at,NEW.received_at) ELSE NULL END WHERE id=t.id;
 RETURN NEW; END $$;
CREATE TRIGGER action_effect AFTER INSERT ON workers.actions FOR EACH ROW EXECUTE FUNCTION workers.apply_action();
CREATE FUNCTION workers.guard_rule() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE p workers.rules%ROWTYPE; BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('workers-rule:'||NEW.template_id::text,0));
 IF NEW.replaces_id IS NULL THEN
  IF NEW.version<>1 OR EXISTS(SELECT 1 FROM workers.rules WHERE template_id=NEW.template_id AND entity_id=NEW.entity_id) THEN RAISE EXCEPTION 'Rule lineage required' USING ERRCODE='23514'; END IF;
 ELSE SELECT * INTO p FROM workers.rules WHERE id=NEW.replaces_id;
  IF NOT FOUND OR (p.entity_id,p.template_id) IS DISTINCT FROM (NEW.entity_id,NEW.template_id) OR NEW.version<>p.version+1 OR NEW.effective_from<=p.effective_from OR EXISTS(SELECT 1 FROM workers.tasks WHERE template_id=NEW.template_id AND occurrence_date>=NEW.effective_from) THEN RAISE EXCEPTION 'Recurrence change must preserve existing occurrences' USING ERRCODE='23514'; END IF;
 END IF;RETURN NEW; END $$;
CREATE TRIGGER rule_guard BEFORE INSERT ON workers.rules FOR EACH ROW EXECUTE FUNCTION workers.guard_rule();
CREATE FUNCTION workers.guard_link() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE e uuid; k text; BEGIN
 CASE NEW.domain
 WHEN 'inventory' THEN SELECT entity_id,kind INTO e,k FROM inventory.events WHERE id=NEW.target_id AND status='posted'; IF k<>'application' THEN e=NULL; END IF;
 WHEN 'harvest' THEN SELECT entity_id INTO e FROM harvest.worker_reports WHERE id=NEW.target_id;
 WHEN 'finance' THEN SELECT entity_id INTO e FROM finance.payments WHERE id=NEW.target_id AND status='posted'; IF e IS NULL THEN SELECT entity_id INTO e FROM finance.obligations WHERE id=NEW.target_id AND status='posted'; END IF;
 WHEN 'inbox' THEN SELECT entity_id INTO e FROM core.inbox_items WHERE id=NEW.target_id;
 END CASE;
 IF e IS DISTINCT FROM NEW.entity_id THEN RAISE EXCEPTION 'Link requires an existing same-entity authoritative event' USING ERRCODE='23514'; END IF;RETURN NEW; END $$;
CREATE TRIGGER link_guard BEFORE INSERT ON workers.links FOR EACH ROW EXECUTE FUNCTION workers.guard_link();
CREATE FUNCTION workers.guard_person() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'active') IS DISTINCT FROM (to_jsonb(OLD)-'active') THEN RAISE EXCEPTION 'Worker reference is immutable; use explicit replacement' USING ERRCODE='55000'; END IF;RETURN NEW; END $$;
CREATE TRIGGER person_guard BEFORE UPDATE OR DELETE ON workers.people FOR EACH ROW EXECUTE FUNCTION workers.guard_person();

CREATE TABLE workers.sources(
 entity_id uuid NOT NULL REFERENCES core.entities, source text NOT NULL, locator text NOT NULL,
 event_type text NOT NULL, snapshot jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(entity_id,source,locator)
);
CREATE TRIGGER sources_immutable BEFORE UPDATE OR DELETE ON workers.sources FOR EACH ROW EXECUTE FUNCTION workers.immutable();
CREATE FUNCTION workers.guard_source() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE p workers.sources%ROWTYPE; snapshot jsonb; BEGIN
 snapshot=CASE TG_TABLE_NAME WHEN 'tasks' THEN to_jsonb(NEW)->'interpretation' ELSE to_jsonb(NEW)-ARRAY['id','received_at','state','submitted_at','cycle'] END;
 INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(NEW.entity_id,NEW.source,NEW.locator,TG_TABLE_NAME,snapshot) ON CONFLICT DO NOTHING;
 SELECT * INTO p FROM workers.sources WHERE entity_id=NEW.entity_id AND source=NEW.source AND locator=NEW.locator;
 IF p.event_type<>TG_TABLE_NAME OR p.snapshot<>snapshot THEN RAISE EXCEPTION 'Source identity reused with changed meaning/kind' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER source_tasks BEFORE INSERT ON workers.tasks FOR EACH ROW EXECUTE FUNCTION workers.guard_source();
CREATE TRIGGER source_reports BEFORE INSERT ON workers.reports FOR EACH ROW EXECUTE FUNCTION workers.guard_source();
CREATE TRIGGER source_actions BEFORE INSERT ON workers.actions FOR EACH ROW EXECUTE FUNCTION workers.guard_source();

CREATE INDEX workers_tasks_attention ON workers.tasks(entity_id,status,due_at);
CREATE INDEX workers_assigned_to ON workers.assignments(worker_id,task_id);
""")

def downgrade():
    raise RuntimeError('Forward-only: restore verified backup and reconcile retained operational history')
