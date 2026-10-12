"""Source-preserving daily crew facts and owner-confirmed Finance accrual.

Additive, no historical business data or worker scopes seeded. Recovery is a
verified separate backup plus preserved original/confirmation reconciliation.
"""
from alembic import op
revision='20261012_0034'
down_revision='20261012_0033'
branch_labels=depends_on=None


def upgrade():
    op.execute(SQL)


def downgrade():
    raise RuntimeError('DayWorkers is forward-only; preserve and reconcile sources and Finance history')


SQL=r'''
CREATE FUNCTION workers.day_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s jsonb;b jsonb;o jsonb;f jsonb;op text;fields text[];keys text[];BEGIN
 IF NEW.source NOT LIKE 'day-workers:%' THEN RETURN NEW;END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('day-workers:'||NEW.entity_id::text,0));
 s=NEW.snapshot;b=s->'command';o=b->'source';f=o->'fields';op=s->>'operation';
 IF jsonb_typeof(s) IS DISTINCT FROM 'object' OR jsonb_typeof(b) IS DISTINCT FROM 'object'
 OR jsonb_typeof(o) IS DISTINCT FROM 'object' OR jsonb_typeof(f) IS DISTINCT FROM 'object'
 OR coalesce(op,'') NOT IN ('scope','request','decision','clarification','actual','confirmation','accrual')
 OR NEW.source IS DISTINCT FROM 'day-workers:'||op OR NEW.event_type IS DISTINCT FROM 'day_'||op
 OR s->>'entity_id' IS DISTINCT FROM NEW.entity_id::text OR s->>'actor_id' IS NULL
 OR NOT s ? 'worker_id' OR o->'schema_version' IS DISTINCT FROM '1'::jsonb
 OR o->>'form_kind' IS DISTINCT FROM 'day_'||op OR o->>'form_id' IS DISTINCT FROM NEW.locator
 OR o->>'timezone' IS DISTINCT FROM 'Europe/Istanbul'
 OR coalesce(o->>'captured_at','') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
 OR length(s::text)>20000
 THEN RAISE EXCEPTION 'Typed original DayWorkers source required' USING ERRCODE='23514';END IF;
 PERFORM (o->>'form_id')::uuid;PERFORM (o->>'captured_at')::timestamptz;PERFORM (s->>'actor_id')::uuid;
 SELECT array_agg(key ORDER BY key) INTO keys FROM jsonb_object_keys(o) key;
 IF keys IS DISTINCT FROM ARRAY['captured_at','fields','form_id','form_kind','schema_version','timezone']::text[]
 THEN RAISE EXCEPTION 'Exact form envelope required' USING ERRCODE='23514';END IF;
 fields=CASE op
 WHEN 'request' THEN ARRAY['days','note','people','start_date','work']
 WHEN 'decision' THEN ARRAY['days','decision','note','people','reason','start_date','work']
 WHEN 'clarification' THEN ARRAY['text']
 WHEN 'actual' THEN ARRAY['allocations','event_date','full_day','note','people','work']
 WHEN 'confirmation' THEN ARRAY['note']
 WHEN 'accrual' THEN ARRAY['counterparty_id','currency','daily_rate','due_date','expense_account_id','rate_basis']
 WHEN 'scope' THEN ARRAY['action','greenhouse_id','reason','worker_id'] END;
 SELECT array_agg(key ORDER BY key) INTO keys FROM jsonb_object_keys(f) key;
 IF keys IS DISTINCT FROM fields OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string' OR length(value#>>'{}')>2000)
 THEN RAISE EXCEPTION 'Exact bounded lexical fields required' USING ERRCODE='23514';END IF;
 SELECT array_agg(key ORDER BY key) INTO keys FROM jsonb_object_keys(s) key;
 IF keys IS DISTINCT FROM ARRAY['actor_id','command','entity_id','operation','worker_id']::text[]
 THEN RAISE EXCEPTION 'Exact server-bound source envelope required' USING ERRCODE='23514';END IF;
 fields=CASE op WHEN 'scope' THEN ARRAY['source'] WHEN 'request' THEN ARRAY['greenhouse_id','source']
 WHEN 'decision' THEN ARRAY['expected_version','request_id','source'] WHEN 'clarification' THEN ARRAY['expected_version','request_id','source']
 WHEN 'actual' THEN ARRAY['greenhouse_id','replaces_id','request_id','source'] ELSE ARRAY['actual_id','confirmed','source'] END;
 SELECT array_agg(key ORDER BY key) INTO keys FROM jsonb_object_keys(b) key;
 IF keys IS DISTINCT FROM fields THEN RAISE EXCEPTION 'Exact command/source context required' USING ERRCODE='23514';END IF;
 IF op IN ('confirmation','accrual') AND b->'confirmed' IS DISTINCT FROM 'true'::jsonb
 THEN RAISE EXCEPTION 'Explicit owner confirmation required' USING ERRCODE='23514';END IF;
 IF op IN ('request','decision') AND f->>'start_date'<>'' AND f->>'start_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
 OR op='actual' AND f->>'event_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
 OR op='accrual' AND f->>'due_date'<>'' AND f->>'due_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
 THEN RAISE EXCEPTION 'Explicit ISO calendar date YYYY-MM-DD required' USING ERRCODE='23514';END IF;
 IF op IN ('request','decision') THEN PERFORM nullif(f->>'start_date','')::date;END IF;
 IF op IN ('request','actual') OR op='decision' AND f->>'decision' IN ('APPROVE','REVISE_APPROVE') THEN
  IF coalesce(f->>'people','') !~ '^[0-9]{1,6}$' OR length(btrim(f->>'work'))=0
  THEN RAISE EXCEPTION 'Explicit whole headcount and work required' USING ERRCODE='23514';END IF;
  IF (f->>'people')::int>100000 OR (f->>'people')::int<(CASE WHEN op='actual' THEN 0 ELSE 1 END)
  THEN RAISE EXCEPTION 'Invalid crew headcount' USING ERRCODE='23514';END IF;
  IF op<>'actual' THEN
   IF coalesce(f->>'days','') !~ '^[0-9]{1,6}$' THEN RAISE EXCEPTION 'Whole planned days required' USING ERRCODE='23514';END IF;
   IF (f->>'days')::int NOT BETWEEN 1 AND 100000 THEN RAISE EXCEPTION 'Invalid planned days' USING ERRCODE='23514';END IF;
   PERFORM nullif(f->>'start_date','')::date;
  END IF;
 END IF;
 IF op='actual' THEN
  PERFORM (f->>'event_date')::date;
  IF f->>'full_day' NOT IN ('true','false','unknown') OR jsonb_typeof((f->>'allocations')::jsonb) IS DISTINCT FROM 'array'
  THEN RAISE EXCEPTION 'Explicit day basis and allocation array required' USING ERRCODE='23514';END IF;
  IF jsonb_array_length((f->>'allocations')::jsonb)>100 OR EXISTS(SELECT 1 FROM jsonb_array_elements((f->>'allocations')::jsonb) x WHERE jsonb_typeof(x)<>'object' OR NOT (x ? 'greenhouse_id' AND x ? 'people') OR x-ARRAY['greenhouse_id','work_area_id','people']<>'{}'::jsonb OR jsonb_typeof(x->'people')<>'number' OR x->>'people' !~ '^[0-9]{1,6}$')
  THEN RAISE EXCEPTION 'Bounded typed allocation rows required' USING ERRCODE='23514';END IF;
 END IF;
 IF op='accrual' THEN
  IF coalesce(f->>'daily_rate','') !~ '^[0-9]{1,12}([.,][0-9]{1,2})?$' OR f->>'currency' NOT IN ('TRY','USD','EUR','UAH') OR length(btrim(f->>'rate_basis'))=0
  THEN RAISE EXCEPTION 'Explicit exact rate, currency and provenance required' USING ERRCODE='23514';END IF;
  IF replace(f->>'daily_rate',',','.')::numeric<=0 THEN RAISE EXCEPTION 'Positive daily rate required' USING ERRCODE='23514';END IF;
  PERFORM (f->>'counterparty_id')::uuid;PERFORM (f->>'expense_account_id')::uuid;PERFORM nullif(f->>'due_date','')::date;
 END IF;
 IF s->'worker_id'='null'::jsonb THEN
  IF NOT core.r2_owner(NEW.entity_id,(s->>'actor_id')::uuid) THEN RAISE EXCEPTION 'Owner source identity required' USING ERRCODE='23514';END IF;
 ELSE
  IF op NOT IN ('request','clarification','actual') OR NOT EXISTS(SELECT 1 FROM workers.people WHERE id=(s->>'worker_id')::uuid AND entity_id=NEW.entity_id AND actor_id=(s->>'actor_id')::uuid AND active)
  THEN RAISE EXCEPTION 'Active worker source identity required' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NEW;END $$;
CREATE TRIGGER day_source_guard BEFORE INSERT ON workers.sources FOR EACH ROW EXECUTE FUNCTION workers.day_source_guard();
CREATE TABLE workers.greenhouse_scope(
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities,
 worker_id uuid NOT NULL, greenhouse_id uuid NOT NULL REFERENCES core.work_areas,
 version int NOT NULL CHECK(version>0), action text NOT NULL CHECK(action IN ('GRANT','REVOKE')),
 reason text NOT NULL CHECK(length(btrim(reason))>0), source text NOT NULL, locator text NOT NULL,
 created_by uuid NOT NULL REFERENCES core.actors, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(entity_id,id),UNIQUE(entity_id,worker_id,greenhouse_id,version),UNIQUE(entity_id,source,locator),
 FOREIGN KEY(entity_id,worker_id) REFERENCES workers.people(entity_id,id),
 FOREIGN KEY(entity_id,source,locator) REFERENCES workers.sources(entity_id,source,locator));
CREATE FUNCTION workers.day_scope(e uuid,w uuid,g uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT coalesce((SELECT action='GRANT' FROM workers.greenhouse_scope WHERE entity_id=e AND worker_id=w AND greenhouse_id=g ORDER BY version DESC LIMIT 1),false)
 AND EXISTS(SELECT 1 FROM workers.people WHERE id=w AND entity_id=e AND active) $$;
CREATE TABLE workers.day_requests(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities,worker_id uuid,
 greenhouse_id uuid NOT NULL REFERENCES core.work_areas,people int NOT NULL CHECK(people>0),days int NOT NULL CHECK(days>0),
 work text NOT NULL CHECK(length(btrim(work))>0),start_date date,note text NOT NULL,
 source text NOT NULL,locator text NOT NULL,created_by uuid NOT NULL REFERENCES core.actors,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(entity_id,id),UNIQUE(entity_id,source,locator),
 FOREIGN KEY(entity_id,worker_id) REFERENCES workers.people(entity_id,id),
 FOREIGN KEY(entity_id,source,locator) REFERENCES workers.sources(entity_id,source,locator));
CREATE TABLE workers.day_decisions(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL,request_id uuid NOT NULL,version int NOT NULL CHECK(version>1),
 decision text NOT NULL CHECK(decision IN ('APPROVE','REVISE_APPROVE','REJECT','CLARIFY')),
 plan jsonb,reason text NOT NULL,source text NOT NULL,locator text NOT NULL,
 created_by uuid NOT NULL REFERENCES core.actors,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(request_id,version),UNIQUE(entity_id,source,locator),
 FOREIGN KEY(entity_id,request_id) REFERENCES workers.day_requests(entity_id,id),
 FOREIGN KEY(entity_id,source,locator) REFERENCES workers.sources(entity_id,source,locator));
CREATE TABLE workers.day_clarifications(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL,request_id uuid NOT NULL,version int NOT NULL CHECK(version>1),
 worker_id uuid NOT NULL,body text NOT NULL CHECK(length(btrim(body))>0),source text NOT NULL,locator text NOT NULL,
 created_by uuid NOT NULL REFERENCES core.actors,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(request_id,version),UNIQUE(entity_id,source,locator),
 FOREIGN KEY(entity_id,request_id) REFERENCES workers.day_requests(entity_id,id),
 FOREIGN KEY(entity_id,worker_id) REFERENCES workers.people(entity_id,id),
 FOREIGN KEY(entity_id,source,locator) REFERENCES workers.sources(entity_id,source,locator));
CREATE FUNCTION workers.day_request_version(r uuid) RETURNS int LANGUAGE sql STABLE AS $$
 SELECT 1+(SELECT count(*) FROM workers.day_decisions WHERE request_id=r)::int+(SELECT count(*) FROM workers.day_clarifications WHERE request_id=r)::int $$;
CREATE TABLE workers.day_actuals(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL REFERENCES core.entities,worker_id uuid,
 greenhouse_id uuid REFERENCES core.work_areas,request_id uuid,replaces_id uuid UNIQUE REFERENCES workers.day_actuals,
 event_date date NOT NULL,people int NOT NULL CHECK(people>=0),work text NOT NULL CHECK(length(btrim(work))>0),
 full_day text NOT NULL CHECK(full_day IN ('true','false','unknown')),note text NOT NULL,
 source text NOT NULL,locator text NOT NULL,created_by uuid NOT NULL REFERENCES core.actors,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(entity_id,id),UNIQUE(entity_id,source,locator),
 FOREIGN KEY(entity_id,request_id) REFERENCES workers.day_requests(entity_id,id),
 FOREIGN KEY(entity_id,worker_id) REFERENCES workers.people(entity_id,id),
 FOREIGN KEY(entity_id,source,locator) REFERENCES workers.sources(entity_id,source,locator));
CREATE TABLE workers.day_actual_allocations(
 actual_id uuid NOT NULL REFERENCES workers.day_actuals,ordinal int NOT NULL CHECK(ordinal>=0),
 greenhouse_id uuid NOT NULL REFERENCES core.work_areas,work_area_id uuid REFERENCES core.work_areas,
 people int NOT NULL CHECK(people>0),PRIMARY KEY(actual_id,ordinal));
CREATE TABLE workers.day_confirmations(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL,actual_id uuid NOT NULL UNIQUE,
 source text NOT NULL,locator text NOT NULL,created_by uuid NOT NULL REFERENCES core.actors,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(entity_id,source,locator),
 FOREIGN KEY(entity_id,actual_id) REFERENCES workers.day_actuals(entity_id,id),
 FOREIGN KEY(entity_id,source,locator) REFERENCES workers.sources(entity_id,source,locator));
CREATE TABLE workers.day_accruals(
 id uuid PRIMARY KEY,entity_id uuid NOT NULL,actual_id uuid NOT NULL UNIQUE,
 obligation_id uuid NOT NULL UNIQUE REFERENCES finance.obligations,counterparty_id uuid NOT NULL REFERENCES core.counterparties,
 daily_rate numeric(20,2) NOT NULL CHECK(daily_rate>0),currency text NOT NULL,
 expense_account_id uuid NOT NULL REFERENCES finance.ledger_accounts,rate_basis text NOT NULL CHECK(length(btrim(rate_basis))>0),
 source text NOT NULL,locator text NOT NULL,created_by uuid NOT NULL REFERENCES core.actors,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(entity_id,source,locator),
 FOREIGN KEY(entity_id,actual_id) REFERENCES workers.day_actuals(entity_id,id),
 FOREIGN KEY(entity_id,source,locator) REFERENCES workers.sources(entity_id,source,locator));
CREATE FUNCTION workers.day_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s jsonb; b jsonb; f jsonb; r workers.day_requests; old workers.day_actuals; v int; op text; g uuid; w uuid; actual workers.day_actuals;
BEGIN
 SELECT snapshot INTO s FROM workers.sources WHERE entity_id=NEW.entity_id AND source=NEW.source AND locator=NEW.locator;
 b=s->'command';f=b->'source'->'fields';op=s->>'operation';
 IF s IS NULL OR s->>'entity_id' IS DISTINCT FROM NEW.entity_id::text OR s->>'actor_id' IS DISTINCT FROM NEW.created_by::text
 OR b->'source'->>'form_id' IS DISTINCT FROM NEW.locator OR NEW.source IS DISTINCT FROM 'day-workers:'||op THEN RAISE EXCEPTION 'DayWorkers source binding mismatch' USING ERRCODE='23514';END IF;
 w=(s->>'worker_id')::uuid;
 IF w IS NULL THEN
  IF NOT core.r2_owner(NEW.entity_id,NEW.created_by) THEN RAISE EXCEPTION 'Confirmed entity owner required' USING ERRCODE='23514'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM workers.people WHERE entity_id=NEW.entity_id AND id=w AND actor_id=NEW.created_by AND active)
  THEN RAISE EXCEPTION 'Active scoped worker required' USING ERRCODE='23514';END IF;
 END IF;
 IF TG_TABLE_NAME='greenhouse_scope' THEN
  IF op IS DISTINCT FROM 'scope' OR w IS NOT NULL OR (NEW.worker_id,NEW.greenhouse_id,NEW.action,NEW.reason) IS DISTINCT FROM
  ((f->>'worker_id')::uuid,(f->>'greenhouse_id')::uuid,f->>'action',f->>'reason') THEN RAISE EXCEPTION 'Scope original mismatch' USING ERRCODE='23514';END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('day-scope:'||NEW.entity_id::text||NEW.worker_id::text||NEW.greenhouse_id::text,0));
  SELECT coalesce(max(version),0)+1 INTO v FROM workers.greenhouse_scope WHERE entity_id=NEW.entity_id AND worker_id=NEW.worker_id AND greenhouse_id=NEW.greenhouse_id;
  IF NEW.version<>v THEN RAISE EXCEPTION 'Scope version changed' USING ERRCODE='23514';END IF;g=NEW.greenhouse_id;
 ELSIF TG_TABLE_NAME='day_requests' THEN
  IF op IS DISTINCT FROM 'request' OR (NEW.worker_id,NEW.greenhouse_id,NEW.people,NEW.days,NEW.work,NEW.start_date,NEW.note) IS DISTINCT FROM
  (w,(b->>'greenhouse_id')::uuid,(f->>'people')::int,(f->>'days')::int,f->>'work',nullif(f->>'start_date','')::date,f->>'note')
  THEN RAISE EXCEPTION 'Request original mismatch' USING ERRCODE='23514';END IF;g=NEW.greenhouse_id;
 ELSIF TG_TABLE_NAME IN ('day_decisions','day_clarifications') THEN
  SELECT * INTO r FROM workers.day_requests WHERE id=NEW.request_id AND entity_id=NEW.entity_id FOR UPDATE;
  v=workers.day_request_version(r.id);
  IF r.id IS NULL OR (b->>'request_id')::uuid IS DISTINCT FROM r.id OR (b->>'expected_version')::int IS DISTINCT FROM v OR NEW.version<>v+1
  THEN RAISE EXCEPTION 'Request version/source changed' USING ERRCODE='23514';END IF;g=r.greenhouse_id;
  IF TG_TABLE_NAME='day_decisions' THEN
   IF op IS DISTINCT FROM 'decision' OR w IS NOT NULL OR (NEW.decision,NEW.reason) IS DISTINCT FROM(f->>'decision',f->>'reason')
   OR NEW.decision IN ('REJECT','CLARIFY') AND (length(btrim(NEW.reason))=0 OR NEW.plan IS NOT NULL)
   THEN RAISE EXCEPTION 'Owner decision original mismatch' USING ERRCODE='23514';END IF;
   IF NEW.decision IN ('APPROVE','REVISE_APPROVE') AND NEW.plan IS DISTINCT FROM jsonb_build_object('people',(f->>'people')::int,'days',(f->>'days')::int,'work',f->>'work','start_date',nullif(f->>'start_date',''),'note',f->>'note')
   THEN RAISE EXCEPTION 'Approved plan source mismatch' USING ERRCODE='23514';END IF;
   IF NEW.decision='APPROVE' AND NEW.plan IS DISTINCT FROM jsonb_build_object('people',r.people,'days',r.days,'work',r.work,'start_date',r.start_date,'note',r.note)
   THEN RAISE EXCEPTION 'Use explicit revised approval for changed plan' USING ERRCODE='23514';END IF;
  ELSE
   IF op IS DISTINCT FROM 'clarification' OR w IS NULL OR NEW.worker_id<>w OR r.worker_id IS DISTINCT FROM w OR NEW.body IS DISTINCT FROM f->>'text'
   OR NOT EXISTS(SELECT 1 FROM workers.day_decisions WHERE request_id=r.id AND version=v AND decision='CLARIFY')
   THEN RAISE EXCEPTION 'Current requested clarification required' USING ERRCODE='23514';END IF;
  END IF;
 ELSIF TG_TABLE_NAME='day_actuals' THEN
  IF op IS DISTINCT FROM 'actual' OR (NEW.worker_id,NEW.greenhouse_id,NEW.request_id,NEW.replaces_id,NEW.event_date,NEW.people,NEW.work,NEW.full_day,NEW.note) IS DISTINCT FROM
  (w,(b->>'greenhouse_id')::uuid,(b->>'request_id')::uuid,(b->>'replaces_id')::uuid,(f->>'event_date')::date,(f->>'people')::int,f->>'work',f->>'full_day',f->>'note')
  THEN RAISE EXCEPTION 'Actual original mismatch' USING ERRCODE='23514';END IF;g=NEW.greenhouse_id;
  IF NEW.request_id IS NOT NULL THEN
   SELECT * INTO r FROM workers.day_requests WHERE id=NEW.request_id AND entity_id=NEW.entity_id;
   IF r.id IS NULL OR w IS NOT NULL AND (r.worker_id IS DISTINCT FROM w OR NOT workers.day_scope(NEW.entity_id,w,r.greenhouse_id))
   THEN RAISE EXCEPTION 'Request report scope mismatch' USING ERRCODE='23514';END IF;
   IF g IS NULL THEN g=r.greenhouse_id;END IF;
  END IF;
  IF w IS NOT NULL AND g IS NULL THEN RAISE EXCEPTION 'Verified reporting context required' USING ERRCODE='23514';END IF;
  IF NEW.replaces_id IS NOT NULL THEN
   SELECT * INTO old FROM workers.day_actuals WHERE id=NEW.replaces_id FOR UPDATE;
   IF old.entity_id IS DISTINCT FROM NEW.entity_id OR w IS NOT NULL AND old.worker_id IS DISTINCT FROM w
    OR EXISTS(SELECT 1 FROM workers.day_accruals WHERE actual_id=old.id)
   THEN RAISE EXCEPTION 'Accrued correction requires separate owner review; original preserved' USING ERRCODE='23514';END IF;
   IF w IS NOT NULL AND (NOT workers.day_scope(NEW.entity_id,w,coalesce(old.greenhouse_id,(SELECT greenhouse_id FROM workers.day_requests WHERE id=old.request_id AND entity_id=NEW.entity_id)))
     OR EXISTS(SELECT 1 FROM workers.day_actual_allocations x WHERE x.actual_id=old.id AND NOT workers.day_scope(NEW.entity_id,w,x.greenhouse_id)))
   THEN RAISE EXCEPTION 'Original actual greenhouse scope is no longer authorized' USING ERRCODE='23514';END IF;

  END IF;
 ELSE
  SELECT * INTO actual FROM workers.day_actuals WHERE id=NEW.actual_id AND entity_id=NEW.entity_id FOR UPDATE;
  IF w IS NOT NULL OR actual.id IS NULL OR (b->>'actual_id')::uuid IS DISTINCT FROM actual.id
  OR EXISTS(SELECT 1 FROM workers.day_actuals WHERE replaces_id=actual.id) OR b->'confirmed' IS DISTINCT FROM 'true'::jsonb
  THEN RAISE EXCEPTION 'Current actual and owner confirmation required' USING ERRCODE='23514';END IF;
  IF TG_TABLE_NAME='day_confirmations' THEN
   IF coalesce(op,'') NOT IN ('confirmation','accrual') THEN RAISE EXCEPTION 'Fact confirmation source required' USING ERRCODE='23514';END IF;
  ELSE
   IF op IS DISTINCT FROM 'accrual' OR actual.people<=0 OR actual.full_day<>'true' OR (SELECT coalesce(sum(people),0) FROM workers.day_actual_allocations WHERE actual_id=actual.id)<>actual.people
   OR NOT EXISTS(SELECT 1 FROM workers.day_confirmations WHERE actual_id=actual.id)
   OR (NEW.counterparty_id,NEW.daily_rate,NEW.currency,NEW.expense_account_id,NEW.rate_basis) IS DISTINCT FROM
   ((f->>'counterparty_id')::uuid,replace(f->>'daily_rate',',','.')::numeric,f->>'currency',(f->>'expense_account_id')::uuid,f->>'rate_basis')
   THEN RAISE EXCEPTION 'Confirmed complete full-day accrual original required' USING ERRCODE='23514';END IF;
   IF NOT EXISTS(SELECT 1 FROM finance.obligations o JOIN finance.ledger_accounts l ON l.id=o.offset_ledger_account_id
    WHERE o.id=NEW.obligation_id AND o.entity_id=NEW.entity_id AND o.counterparty_id=NEW.counterparty_id
    AND o.direction='payable' AND o.status='posted' AND o.obligation_class='OPERATIONAL' AND o.economic_source='manual_other'
    AND o.original_amount=actual.people*NEW.daily_rate AND o.currency=NEW.currency AND o.event_date=actual.event_date
    AND o.due_date IS NOT DISTINCT FROM nullif(f->>'due_date','')::date
    AND o.offset_ledger_account_id=NEW.expense_account_id AND l.account_class='expense')
   THEN RAISE EXCEPTION 'Existing Finance accrual basis mismatch' USING ERRCODE='23514';END IF;
   IF EXISTS(SELECT 1 FROM (
     SELECT coalesce(work_area_id,greenhouse_id) area,sum(people)*NEW.daily_rate amount FROM workers.day_actual_allocations WHERE actual_id=actual.id GROUP BY 1
    ) d FULL JOIN (SELECT work_area_id area,amount FROM finance.obligation_attributions WHERE obligation_id=NEW.obligation_id) a USING(area)
    WHERE d.amount IS DISTINCT FROM a.amount)
   THEN RAISE EXCEPTION 'Finance actual allocation mismatch' USING ERRCODE='23514';END IF;
  END IF;
 END IF;
 IF g IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=g AND entity_id=NEW.entity_id AND kind='greenhouse' AND parent_id IS NULL)
 THEN RAISE EXCEPTION 'Verified greenhouse required' USING ERRCODE='23514';END IF;
 IF w IS NOT NULL AND g IS NOT NULL AND NOT workers.day_scope(NEW.entity_id,w,g) THEN RAISE EXCEPTION 'Worker greenhouse scope denied' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION workers.day_allocation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a workers.day_actuals;s jsonb;f jsonb;expected jsonb;BEGIN
 SELECT * INTO a FROM workers.day_actuals WHERE id=NEW.actual_id FOR UPDATE;
 SELECT snapshot INTO s FROM workers.sources WHERE entity_id=a.entity_id AND source=a.source AND locator=a.locator;
 f=s->'command'->'source'->'fields';expected=(f->>'allocations')::jsonb->NEW.ordinal;
 IF expected IS NULL OR (NEW.greenhouse_id,NEW.work_area_id,NEW.people) IS DISTINCT FROM
 ((expected->>'greenhouse_id')::uuid,(expected->>'work_area_id')::uuid,(expected->>'people')::int)
 OR NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=NEW.greenhouse_id AND entity_id=a.entity_id AND kind='greenhouse' AND parent_id IS NULL)
 OR NEW.work_area_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.work_areas WHERE id=NEW.work_area_id AND entity_id=a.entity_id AND (parent_id=NEW.greenhouse_id OR id=NEW.greenhouse_id))
 OR a.worker_id IS NOT NULL AND NOT workers.day_scope(a.entity_id,a.worker_id,NEW.greenhouse_id)
 OR EXISTS(SELECT 1 FROM workers.day_confirmations WHERE actual_id=a.id)
 OR EXISTS(SELECT 1 FROM workers.day_actuals WHERE replaces_id=a.id)
 THEN RAISE EXCEPTION 'Exact source-bound allocated greenhouse scope required' USING ERRCODE='23514';END IF;
 RETURN NEW;END $$;
CREATE TRIGGER day_allocation_guard BEFORE INSERT ON workers.day_actual_allocations FOR EACH ROW EXECUTE FUNCTION workers.day_allocation_guard();
CREATE FUNCTION workers.day_actual_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE f jsonb;BEGIN
 SELECT snapshot->'command'->'source'->'fields' INTO f FROM workers.sources WHERE entity_id=NEW.entity_id AND source=NEW.source AND locator=NEW.locator;
 IF (SELECT count(*) FROM workers.day_actual_allocations WHERE actual_id=NEW.id)<>jsonb_array_length((f->>'allocations')::jsonb)
 OR (SELECT coalesce(sum(people),0) FROM workers.day_actual_allocations WHERE actual_id=NEW.id)>NEW.people
 THEN RAISE EXCEPTION 'Actual allocations must preserve one crew total and all source rows' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE CONSTRAINT TRIGGER day_actual_complete AFTER INSERT ON workers.day_actuals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION workers.day_actual_complete();
DO $$ DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['greenhouse_scope','day_requests','day_decisions','day_clarifications','day_actuals','day_confirmations','day_accruals'] LOOP
 EXECUTE format('CREATE TRIGGER day_guard BEFORE INSERT ON workers.%I FOR EACH ROW EXECUTE FUNCTION workers.day_guard()',t);
 EXECUTE format('CREATE TRIGGER day_immutable BEFORE UPDATE OR DELETE ON workers.%I FOR EACH ROW EXECUTE FUNCTION workers.immutable()',t);
 END LOOP;END $$;
CREATE TRIGGER day_allocation_immutable BEFORE UPDATE OR DELETE ON workers.day_actual_allocations FOR EACH ROW EXECUTE FUNCTION workers.immutable();
CREATE FUNCTION workers.day_source_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t text;found boolean;BEGIN
 t=CASE NEW.snapshot->>'operation' WHEN 'scope' THEN 'greenhouse_scope' WHEN 'request' THEN 'day_requests' WHEN 'decision' THEN 'day_decisions' WHEN 'clarification' THEN 'day_clarifications' WHEN 'actual' THEN 'day_actuals' WHEN 'confirmation' THEN 'day_confirmations' WHEN 'accrual' THEN 'day_accruals' END;
 IF t IS NULL THEN RAISE EXCEPTION 'Typed source child required' USING ERRCODE='23514';END IF;
 EXECUTE format('SELECT EXISTS(SELECT 1 FROM workers.%I WHERE entity_id=$1 AND source=$2 AND locator=$3)',t) INTO found USING NEW.entity_id,NEW.source,NEW.locator;
 IF NOT found THEN RAISE EXCEPTION 'Source requires its atomic DayWorkers action' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE CONSTRAINT TRIGGER day_source_complete AFTER INSERT ON workers.sources DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.source LIKE 'day-workers:%') EXECUTE FUNCTION workers.day_source_complete();
CREATE INDEX day_actual_period ON workers.day_actuals(entity_id,event_date);
CREATE INDEX day_request_worker ON workers.day_requests(entity_id,worker_id);
'''
