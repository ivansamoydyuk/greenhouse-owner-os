"""Bounded owner-selected task audience recovery; preserve applied migrations.

Only the established source-linked recovery family gains typed task targets.
No original or confirmed task is rewritten, assigned or dispatched. Forward-only.
"""
from alembic import op

revision = '20261012_0035'
down_revision = '20261012_0034'
branch_labels = depends_on = None


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _extend(signature, replacements):
    sql = f"DO $task_recovery$ DECLARE definition text; BEGIN SELECT pg_get_functiondef({_literal(signature)}::regprocedure) INTO definition;"
    for before, after in replacements:
        old, new = _literal(before), _literal(after)
        sql += f" IF (length(definition)-length(replace(definition,{old},'')))/length({old})<>1 THEN RAISE EXCEPTION 'Unexpected prior recovery guard; migration refused'; END IF; definition:=replace(definition,{old},{new});"
    sql += ' EXECUTE definition; END $task_recovery$;'
    op.execute(sql)


def upgrade():
    _extend('core.r2_recovery_write_guard()', [(
        "(NEW.slot NOT LIKE 'manual:direct-expense:%' AND NEW.slot NOT LIKE 'manual:money:%')",
        "(NEW.slot NOT LIKE 'manual:direct-expense:%' AND NEW.slot NOT LIKE 'manual:money:%' AND NEW.slot NOT LIKE 'manual:task:%')",
    )])
    _extend('core.r2_recovery_link_guard()', [(
        "OR target.action_type NOT IN ('DIRECT_EXPENSE','PAYABLE','PAYMENT')",
        "OR target.action_type NOT IN ('DIRECT_EXPENSE','PAYABLE','PAYMENT','PERSONAL_TASK','WORKER_TASK')",
    ), (
        "OR NOT ((target.action_type='DIRECT_EXPENSE' AND target.slot='manual:direct-expense:'||coalesce(NEW.origin_proposal_id,NEW.inbox_id)::text) OR (NEW.origin_proposal_id IS NOT NULL AND target.slot='manual:money:'||NEW.origin_proposal_id::text AND target.action_type IN ('DIRECT_EXPENSE','PAYABLE','PAYMENT')))",
        "OR NOT ((target.action_type='DIRECT_EXPENSE' AND target.slot='manual:direct-expense:'||coalesce(NEW.origin_proposal_id,NEW.inbox_id)::text) OR (NEW.origin_proposal_id IS NOT NULL AND target.slot='manual:money:'||NEW.origin_proposal_id::text AND target.action_type IN ('DIRECT_EXPENSE','PAYABLE','PAYMENT')) OR (NEW.origin_proposal_id IS NOT NULL AND target.slot='manual:task:'||NEW.origin_proposal_id::text AND target.action_type IN ('PERSONAL_TASK','WORKER_TASK')))",
    ), (
        "OR origin.action_type IS DISTINCT FROM 'UNRESOLVED'",
        "OR NOT (origin.action_type='UNRESOLVED' OR (target.slot LIKE 'manual:task:%' AND origin.action_type IN ('PERSONAL_TASK','WORKER_TASK')))",
    ), (
        "THEN RAISE EXCEPTION 'Selected origin is not eligible for recovery' USING ERRCODE='23514'; END IF;",
        """THEN RAISE EXCEPTION 'Selected origin is not eligible for recovery' USING ERRCODE='23514'; END IF;
  IF target.slot NOT LIKE 'manual:task:%' AND EXISTS (
   SELECT 1 FROM core.proposal_revisions r WHERE r.proposal_id=origin.id AND r.version=1
   AND jsonb_typeof(r.payload->'task_candidates')='object')
  THEN RAISE EXCEPTION 'Prepared task cannot recover into an economic action' USING ERRCODE='23514'; END IF;
  IF target.slot LIKE 'manual:task:%' AND NOT EXISTS (
   SELECT 1 FROM core.proposal_revisions r WHERE r.proposal_id=origin.id AND r.version=1
   AND ((origin.action_type='UNRESOLVED' AND jsonb_typeof(r.payload->'task_candidates')='object'
      AND length(coalesce(r.payload->'task_candidates'->>'original_instruction',''))>0)
    OR (origin.action_type IN ('PERSONAL_TASK','WORKER_TASK') AND length(coalesce(r.payload->>'original_instruction',r.payload->>'title',''))>0)))
  THEN RAISE EXCEPTION 'Task recovery requires an immutable prepared task' USING ERRCODE='23514'; END IF;""",
    )])
    _extend('core.r2_recovery_consistency()', [(
        "(p.slot LIKE 'manual:direct-expense:%' OR p.slot LIKE 'manual:money:%')",
        "(p.slot LIKE 'manual:direct-expense:%' OR p.slot LIKE 'manual:money:%' OR p.slot LIKE 'manual:task:%')",
    )])


def downgrade():
    raise RuntimeError('0035 is forward-only: verified backup plus preserved-history reconciliation required')
