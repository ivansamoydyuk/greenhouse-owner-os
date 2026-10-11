"""Guard new purchase links without rewriting historical sources or valuations.

Forward-only: restore a verified backup separately and reconcile subsequent
immutable source, finance and physical movement history before cutover.
"""
from alembic import op
revision='20261010_0027'
down_revision='20261010_0026'
branch_labels=None
depends_on=None


def upgrade():
    op.execute(r"""
CREATE FUNCTION core.guard_purchase_invoice_link() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE receipt inventory.events; obligation finance.obligations;
BEGIN
 -- Lock order: physical entity, receipt, then Finance obligation. Finance
 -- reversal takes the obligation lock, and never waits on inventory.
 PERFORM pg_advisory_xact_lock(hashtextextended('inventory:'||NEW.entity_id::text,0));
 SELECT * INTO receipt FROM inventory.events WHERE id=NEW.receipt_id FOR SHARE;
 IF receipt.id IS NULL OR receipt.entity_id<>NEW.entity_id OR receipt.kind<>'receipt' OR receipt.status<>'posted'
 THEN RAISE EXCEPTION 'Invoice receipt scope/state mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.finance_obligation_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO obligation FROM finance.obligations WHERE id=NEW.finance_obligation_id FOR SHARE;
 IF obligation.id IS NULL OR obligation.entity_id<>NEW.entity_id OR obligation.direction<>'payable' OR obligation.status<>'posted'
 OR receipt.id IS NULL OR receipt.entity_id<>NEW.entity_id OR receipt.kind<>'receipt' OR receipt.status<>'posted'
 OR (receipt.payload->>'supplier_id' IS NOT NULL AND (receipt.payload->>'supplier_id')::uuid<>obligation.counterparty_id)
 THEN RAISE EXCEPTION 'Invoice payable or supplier mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER purchase_invoice_link_guard BEFORE INSERT ON core.invoice_receipt_links
 FOR EACH ROW EXECUTE FUNCTION core.guard_purchase_invoice_link();

CREATE FUNCTION inventory.guard_purchase_finance_link() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE obligation finance.obligations;
BEGIN
 -- Reversal of old posted evidence remains possible. Never repair an old link.
 IF TG_OP='UPDATE' AND OLD.status IN ('posted','reversed') THEN RETURN NEW; END IF;
 IF NEW.kind<>'receipt' OR NEW.payload->>'finance_obligation_id' IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO obligation FROM finance.obligations WHERE id=(NEW.payload->>'finance_obligation_id')::uuid FOR SHARE;
 IF obligation.id IS NULL OR obligation.entity_id<>NEW.entity_id OR obligation.direction<>'payable'
 OR (NEW.payload->>'supplier_id' IS NOT NULL AND (NEW.payload->>'supplier_id')::uuid<>obligation.counterparty_id)
 THEN RAISE EXCEPTION 'Physical receipt Finance supplier mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER purchase_finance_link_guard BEFORE INSERT OR UPDATE ON inventory.events
 FOR EACH ROW EXECUTE FUNCTION inventory.guard_purchase_finance_link();
""")


def downgrade():
    raise RuntimeError('0027 is forward-only: verified backup plus preserved-history reconciliation required')
