"""Independent R1 review: aggregate team lifecycle, review claims and races.
Forward recovery: restore a verified backup separately and reconcile retained
immutable instructions, assignments, reports and actions. Preflight refuses
incompatible history; never relabels or deletes historical completion evidence.
"""
from alembic import op
revision='20261006_0021'
down_revision='20261006_0020'
branch_labels=depends_on=None

def upgrade():
    op.execute(r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM workers.tasks t WHERE t.status='completed' AND
   (NOT EXISTS(SELECT 1 FROM workers.assignments a WHERE a.task_id=t.id) OR
    EXISTS(SELECT 1 FROM workers.assignments a WHERE a.task_id=t.id AND NOT EXISTS(
      SELECT 1 FROM workers.reports r WHERE r.assignment_id=a.id AND r.cycle=t.cycle AND r.state='submitted' AND r.claim='done'))))
 THEN RAISE EXCEPTION 'Workers completed history lacks all-assignee done evidence; reconcile explicitly before upgrade'; END IF;
END $$;
ALTER TABLE workers.assignments ADD CHECK(translation_source IN ('original','owner_reviewed_translation')),
 ADD CHECK(length(trim(translation_version))>0), ADD CHECK(length(trim(rendered_text))>0);
ALTER TABLE workers.actions DROP CONSTRAINT actions_kind_check;
ALTER TABLE workers.actions ADD CHECK(kind IN ('assign','acknowledge','start','submit_report','complete','reopen','cancel','review_report'));
CREATE UNIQUE INDEX workers_review_once ON workers.actions(report_id) WHERE kind='review_report';
CREATE OR REPLACE FUNCTION workers.apply_action() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t workers.tasks%ROWTYPE; a workers.assignments%ROWTYPE; r workers.reports%ROWTYPE; s text;
BEGIN
 SELECT * INTO t FROM workers.tasks WHERE id=NEW.task_id FOR UPDATE;
 IF NOT FOUND OR t.entity_id<>NEW.entity_id OR t.cycle<>NEW.cycle THEN RAISE EXCEPTION 'Task/cycle mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.assignment_id IS NOT NULL THEN
  SELECT * INTO a FROM workers.assignments WHERE id=NEW.assignment_id;
  IF NOT FOUND OR (a.entity_id,a.task_id,a.cycle) IS DISTINCT FROM (NEW.entity_id,NEW.task_id,NEW.cycle) THEN RAISE EXCEPTION 'Assignment scope mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.kind IN ('acknowledge','start','submit_report') THEN
  IF NEW.worker_id IS NULL OR a.worker_id IS DISTINCT FROM NEW.worker_id OR NOT EXISTS(SELECT 1 FROM workers.people WHERE id=NEW.worker_id AND active) THEN RAISE EXCEPTION 'Worker identity mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.actor_id IS NULL OR NOT EXISTS(SELECT 1 FROM core.actors WHERE id=NEW.actor_id AND actor_type='owner' AND active) THEN RAISE EXCEPTION 'Owner action required' USING ERRCODE='23514'; END IF;
 END IF;
 s=t.status;
 CASE NEW.kind
 WHEN 'assign' THEN
  IF s NOT IN ('draft','assigned','reopened') OR a.id IS NULL THEN RAISE EXCEPTION 'Task not assignable' USING ERRCODE='23514'; END IF;s='assigned';
 WHEN 'acknowledge' THEN
  IF s IN ('draft','completed','cancelled') OR a.acknowledged_at IS NOT NULL THEN RAISE EXCEPTION 'Invalid acknowledgement' USING ERRCODE='23514'; END IF;
  UPDATE workers.assignments SET acknowledged_at=NEW.received_at WHERE id=a.id;
  IF s IN ('assigned','reopened') AND NOT EXISTS(SELECT 1 FROM workers.assignments WHERE task_id=t.id AND acknowledged_at IS NULL) THEN s='acknowledged'; END IF;
 WHEN 'start' THEN
  IF s IN ('draft','completed','cancelled') OR a.acknowledged_at IS NULL OR a.started_at IS NOT NULL THEN RAISE EXCEPTION 'Acknowledge before start' USING ERRCODE='23514'; END IF;
  UPDATE workers.assignments SET started_at=NEW.received_at WHERE id=a.id;
  IF s<>'reported' THEN s='in_progress'; END IF;
 WHEN 'submit_report' THEN
  SELECT * INTO r FROM workers.reports WHERE id=NEW.report_id FOR UPDATE;
  IF NOT FOUND OR (r.entity_id,r.task_id,r.assignment_id,r.worker_id,r.cycle) IS DISTINCT FROM (NEW.entity_id,NEW.task_id,NEW.assignment_id,NEW.worker_id,NEW.cycle) OR r.state<>'draft' THEN RAISE EXCEPTION 'Invalid report submission' USING ERRCODE='23514'; END IF;
  IF r.claim='done' AND s NOT IN ('completed','cancelled') AND (a.acknowledged_at IS NULL OR a.started_at IS NULL) THEN RAISE EXCEPTION 'Подтвердите получение и начало работы перед отчётом о выполнении' USING ERRCODE='23514'; END IF;
  UPDATE workers.reports SET state='submitted',submitted_at=NEW.received_at WHERE id=r.id;
  IF s NOT IN ('completed','cancelled') THEN
   IF r.claim='issue' THEN s='reported';
   ELSIF r.claim='done' AND NOT EXISTS(SELECT 1 FROM workers.assignments aa WHERE aa.task_id=t.id AND NOT EXISTS(SELECT 1 FROM workers.reports rr WHERE rr.assignment_id=aa.id AND rr.cycle=t.cycle AND rr.state='submitted' AND rr.claim='done')) THEN s='reported'; END IF;
  END IF;
 WHEN 'complete' THEN
  IF s<>'reported' OR length(trim(NEW.reason))=0 OR
   NOT EXISTS(SELECT 1 FROM workers.assignments WHERE task_id=t.id) OR
   EXISTS(SELECT 1 FROM workers.assignments aa WHERE aa.task_id=t.id AND NOT EXISTS(SELECT 1 FROM workers.reports rr WHERE rr.assignment_id=aa.id AND rr.cycle=t.cycle AND rr.state='submitted' AND rr.claim='done')) OR
   EXISTS(SELECT 1 FROM workers.reports rr WHERE rr.task_id=t.id AND rr.cycle=t.cycle AND rr.state='submitted' AND rr.claim='issue' AND NOT EXISTS(SELECT 1 FROM workers.actions x WHERE x.report_id=rr.id AND x.kind='review_report'))
  THEN RAISE EXCEPTION 'Нужны отчёты о выполнении от всех работников и проверка замечаний' USING ERRCODE='23514'; END IF;s='completed';
 WHEN 'reopen' THEN
  IF s NOT IN ('reported','completed') OR length(trim(NEW.reason))=0 THEN RAISE EXCEPTION 'Explicit reopen reason required' USING ERRCODE='23514'; END IF;s='reopened';
  UPDATE workers.assignments SET cycle=t.cycle+1,acknowledged_at=NULL,started_at=NULL WHERE task_id=t.id;
 WHEN 'cancel' THEN
  IF s IN ('completed','cancelled') OR length(trim(NEW.reason))=0 THEN RAISE EXCEPTION 'Terminal task cannot cancel' USING ERRCODE='23514'; END IF;s='cancelled';
 WHEN 'review_report' THEN
  IF length(trim(NEW.reason))=0 OR NOT EXISTS(SELECT 1 FROM workers.reports WHERE id=NEW.report_id AND task_id=t.id AND entity_id=t.entity_id) THEN RAISE EXCEPTION 'Explicit same-task report review required' USING ERRCODE='23514'; END IF;
 END CASE;
 UPDATE workers.tasks SET status=s,version=version+1,cycle=cycle+CASE WHEN NEW.kind='reopen' THEN 1 ELSE 0 END,
  completed_at=CASE WHEN s='completed' THEN COALESCE(completed_at,NEW.received_at) ELSE NULL END,
  cancelled_at=CASE WHEN s='cancelled' THEN COALESCE(cancelled_at,NEW.received_at) ELSE NULL END WHERE id=t.id;
 RETURN NEW;
END $$;
DO $$ DECLARE s text; BEGIN
 s=pg_get_functiondef('workers.guard_task()'::regprocedure);
 s=replace(s,'IF NEW.rule_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workers.rules WHERE',
  'IF TG_OP=''INSERT'' AND NEW.template_id IS NOT NULL THEN PERFORM pg_advisory_xact_lock(hashtextextended(''workers-rule:''||NEW.template_id::text,0)); END IF; IF NEW.rule_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workers.rules WHERE');
 EXECUTE s;
END $$;
""")

def downgrade():
    raise RuntimeError('Forward-only: verified backup restore and immutable evidence reconciliation')
