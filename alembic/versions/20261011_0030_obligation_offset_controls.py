"""Keep ordinary obligations out of reserved subledger-control offsets.

Existing rows are not repaired or relabelled. Posted originals remain immutable
and may follow their existing reversal path. Forward-only recovery requires a
verified backup and reconciliation of the preserved history.
"""
from alembic import op

revision = '20261011_0030'
down_revision = '20261010_0029'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(r"""
CREATE FUNCTION finance.guard_obligation_control_offset() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE offset_code text;
BEGIN
 -- Do not rewrite historical posted evidence or obstruct its existing reversal.
 -- Existing immutable-history guards still govern every such transition.
 IF TG_OP='UPDATE' AND OLD.status IN ('posted','reversed') THEN RETURN NEW; END IF;
 SELECT system_code INTO offset_code FROM finance.ledger_accounts
  WHERE id=NEW.offset_ledger_account_id;
 IF offset_code IN ('AP_CONTROL','AR_CONTROL','SUPPLIER_ADVANCE','CUSTOMER_ADVANCE') THEN
  RAISE EXCEPTION 'subledger control account cannot be an ordinary obligation offset'
   USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER obligation_control_offset_guard BEFORE INSERT OR UPDATE ON finance.obligations
 FOR EACH ROW EXECUTE FUNCTION finance.guard_obligation_control_offset();
""")


def downgrade():
    raise RuntimeError('0030 is forward-only: verified backup plus preserved-history reconciliation required')
