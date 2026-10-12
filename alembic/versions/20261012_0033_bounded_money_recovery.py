"""Bounded nominal-money recovery reuses the existing immutable family.

Proposal types are immutable and 0025 permits only DirectExpense recovery.
Extend only that family's exact slot/type guards for owner-selected payable,
paid expense, or existing-debt payment. No domain effect, source rewrite, new
accounting aggregate or null-origin general-money recovery is introduced.

Forward-only: retain original source/revisions/links/effects and restore only
from a separately verified backup with subsequent audit reconciliation.
"""
from alembic import op

revision='20261012_0033'
down_revision='20261011_0032'
branch_labels=depends_on=None


def _literal(value):
    return "'"+value.replace("'","''")+"'"


def _extend(signature,replacements):
    # The existing function body contains all source locks, lineage and
    # finality guards. Fail closed if its expected exact guard is absent or
    # duplicated; replace no unrelated logic or applied migration bytes.
    sql=f"DO $money_recovery$ DECLARE definition text; BEGIN SELECT pg_get_functiondef({_literal(signature)}::regprocedure) INTO definition;"
    for before,after in replacements:
        old,new=_literal(before),_literal(after)
        sql+=f" IF (length(definition)-length(replace(definition,{old},'')))/length({old})<>1 THEN RAISE EXCEPTION 'Unexpected prior recovery guard; migration refused'; END IF; definition:=replace(definition,{old},{new});"
    sql+=" EXECUTE definition; END $money_recovery$;"
    op.execute(sql)


def upgrade():
    _extend('core.r2_recovery_write_guard()',[(
        "NEW.slot NOT LIKE 'manual:direct-expense:%'",
        "(NEW.slot NOT LIKE 'manual:direct-expense:%' AND NEW.slot NOT LIKE 'manual:money:%')",
    )])
    _extend('core.r2_recovery_link_guard()',[
        ("OR target.action_type IS DISTINCT FROM 'DIRECT_EXPENSE'",
         "OR target.action_type NOT IN ('DIRECT_EXPENSE','PAYABLE','PAYMENT')"),
        ("OR target.slot IS DISTINCT FROM 'manual:direct-expense:'||coalesce(NEW.origin_proposal_id,NEW.inbox_id)::text",
         "OR NOT ((target.action_type='DIRECT_EXPENSE' AND target.slot='manual:direct-expense:'||coalesce(NEW.origin_proposal_id,NEW.inbox_id)::text) OR (NEW.origin_proposal_id IS NOT NULL AND target.slot='manual:money:'||NEW.origin_proposal_id::text AND target.action_type IN ('DIRECT_EXPENSE','PAYABLE','PAYMENT')))"),
    ])
    # Relational source eligibility is enforced at SQL boundary too. Semantic
    # nominal grammar is still independently recomputed by the trusted handler.
    _extend('core.r2_recovery_link_guard()',[(
        "IF NEW.origin_proposal_id IS NOT NULL THEN",
        "IF target.slot LIKE 'manual:money:%' AND (original.file_bytes IS NOT NULL OR coalesce(original.metadata->>'capture_context','') NOT IN ('','quick_expense') OR (SELECT count(*) FROM core.proposed_actions WHERE inbox_id=NEW.inbox_id AND id<>target.id)<>1 OR NOT EXISTS(SELECT 1 FROM core.proposal_revisions WHERE proposal_id=NEW.origin_proposal_id AND version=1 AND payload->>'note'=original.raw_text)) THEN RAISE EXCEPTION 'Money recovery requires one immutable text original' USING ERRCODE='23514'; END IF; IF NEW.origin_proposal_id IS NOT NULL THEN",
    )])
    _extend('core.r2_recovery_consistency()',[(
        "p.slot LIKE 'manual:direct-expense:%'",
        "(p.slot LIKE 'manual:direct-expense:%' OR p.slot LIKE 'manual:money:%')",
    )])


def downgrade():
    raise RuntimeError('0033 is forward-only: verified backup plus preserved-history reconciliation required')
