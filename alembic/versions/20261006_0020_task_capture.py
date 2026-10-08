"""Extend existing immutable source handoff guards for operational drafts.
Prior migrations unchanged. Forward recovery via verified backup/history reconciliation.
"""
from alembic import op
revision='20261006_0020'
down_revision='20261006_0019'
branch_labels=depends_on=None

def upgrade():
    op.execute(r"""
ALTER TABLE core.capture_drafts ADD COLUMN task_id uuid REFERENCES workers.tasks;
ALTER TABLE core.inbox_items ADD COLUMN task_id uuid REFERENCES workers.tasks, ADD COLUMN task_report_id uuid REFERENCES workers.reports;
DO $$ DECLARE r record; s text; BEGIN
 FOR r IN SELECT conname FROM pg_constraint WHERE conrelid='core.capture_drafts'::regclass AND contype='c' AND (pg_get_constraintdef(oid) LIKE '%num_nonnulls%' OR pg_get_constraintdef(oid) LIKE '%route%') LOOP EXECUTE format('ALTER TABLE core.capture_drafts DROP CONSTRAINT %I',r.conname); END LOOP;
 FOR r IN SELECT conname FROM pg_constraint WHERE conrelid='core.inbox_items'::regclass AND contype='c' AND (pg_get_constraintdef(oid) LIKE '%route%' OR pg_get_constraintdef(oid) LIKE '%num_nonnulls%') LOOP EXECUTE format('ALTER TABLE core.inbox_items DROP CONSTRAINT %I',r.conname); END LOOP;
 s=pg_get_functiondef('core.guard_capture()'::regprocedure);
 s=replace(s,'IF NEW.route=''INVENTORY'' THEN', $f$IF NEW.route='TASKS' THEN
 SELECT entity_id INTO e FROM workers.tasks WHERE id=NEW.task_id;
 IF NOT EXISTS(SELECT 1 FROM workers.tasks t WHERE t.id=NEW.task_id AND t.source=COALESCE(NEW.source_namespace,'capture:'||NEW.source) AND t.locator=COALESCE(NEW.source_reference,NEW.id::text) AND t.original_instruction=NEW.original_text AND t.interpretation @> (NEW.confirmed_review-ARRAY['duplicate_reviewed','suggested_worker_ids','recurrence_suggestion','kind'])) THEN RAISE EXCEPTION 'Task capture source/review mismatch' USING ERRCODE='23514'; END IF;
 ELSIF NEW.route='INVENTORY' THEN$f$);EXECUTE s;
 s=pg_get_functiondef('core.guard_inbox()'::regprocedure);
 s=replace(s,'''inventory_id'']', '''inventory_id'',''task_id'',''task_report_id'']');
 s=replace(s,'IF NEW.inventory_id IS NOT NULL THEN', $f$IF NEW.task_id IS NOT NULL THEN
 IF NEW.route<>'TASKS' OR NOT EXISTS(SELECT 1 FROM workers.tasks t WHERE t.id=NEW.task_id AND t.entity_id=NEW.entity_id AND t.source=CASE WHEN NEW.locator IS NULL THEN 'inbox:manual' ELSE NEW.source END AND t.locator=COALESCE(NEW.locator,NEW.id::text) AND t.original_instruction=NEW.raw_text AND t.interpretation @> ((NEW.reviewed->'reviewed')-ARRAY['duplicate_reviewed','suggested_worker_ids','recurrence_suggestion','kind'])) THEN RAISE EXCEPTION 'Inbox Task source/review mismatch' USING ERRCODE='23514'; END IF;
 ELSIF NEW.task_report_id IS NOT NULL THEN
 IF NEW.route<>'WORKER_REPORTS' OR NOT EXISTS(SELECT 1 FROM workers.reports r WHERE r.id=NEW.task_report_id AND r.entity_id=NEW.entity_id AND r.state='draft' AND r.source=CASE WHEN NEW.locator IS NULL THEN 'inbox:manual' ELSE NEW.source END AND r.locator=COALESCE(NEW.locator,NEW.id::text) AND r.original_text=NEW.raw_text) THEN RAISE EXCEPTION 'Inbox report source mismatch' USING ERRCODE='23514'; END IF;
 ELSIF NEW.inventory_id IS NOT NULL THEN$f$); EXECUTE s;
 s=pg_get_functiondef('core.guard_inbox_source_link()'::regprocedure);
 s=replace(s,'IF NEW.inventory_id IS NOT NULL THEN RETURN NEW;', 'IF NEW.inventory_id IS NOT NULL OR NEW.task_id IS NOT NULL OR NEW.task_report_id IS NOT NULL THEN RETURN NEW;');EXECUTE s;
END $$;
ALTER TABLE core.capture_drafts ADD CHECK(route IN ('FINANCE','HARVEST_DEPOT','INVENTORY','TASKS','OPERATIONS','WORKERS','DOCUMENTS','UNRESOLVED')), ADD CHECK((state='LINKED')=(num_nonnulls(finance_id,harvest_id,inventory_id,task_id)=1)), ADD CHECK(num_nonnulls(finance_id,harvest_id,inventory_id,task_id)<=1);
ALTER TABLE core.inbox_items ADD CHECK(route IN ('FINANCE','HARVEST_DEPOT','DOCUMENTS','UNRESOLVED','INVENTORY','TASKS','WORKER_REPORTS')), ADD CHECK((state='CONFIRMED')=(num_nonnulls(capture_id,harvest_id,inventory_id,task_id,task_report_id)=1)), ADD CHECK(num_nonnulls(capture_id,harvest_id,inventory_id,task_id,task_report_id)<=1);
""")

def downgrade():raise RuntimeError('Forward-only: backup restore and evidence reconciliation')
