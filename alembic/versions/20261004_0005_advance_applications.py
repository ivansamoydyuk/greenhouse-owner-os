"""Non-cash advance applications and reconciliation support.
Forward-only: restore verified pre-upgrade backup separately, retain/reconcile
subsequent immutable journal and application history before any cutover.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '20261004_0005'
down_revision = '20261004_0004'
branch_labels = None
depends_on = None


def upgrade():
    table = 'advance_applications'
    cols = [
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('entity_id', UUID(as_uuid=True), sa.ForeignKey('core.entities.id'), nullable=False),
        sa.Column('amount', sa.Numeric(20,2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('event_date', sa.Date(), nullable=False),
        sa.Column('source', sa.Text()), sa.Column('reason', sa.Text()),
        sa.Column('status', sa.String(10), nullable=False, server_default='draft'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('core.actors.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('posting_id', UUID(as_uuid=True), sa.ForeignKey('finance.postings.id'), unique=True),
        sa.Column('posted_at', sa.DateTime(timezone=True)),
        sa.Column('reversal_posting_id', UUID(as_uuid=True), sa.ForeignKey('finance.postings.id'), unique=True),
        sa.Column('reversed_at', sa.DateTime(timezone=True)),
        sa.Column('reversed_by', UUID(as_uuid=True), sa.ForeignKey('core.actors.id')),
        sa.Column('reversal_reason', sa.Text()),
        sa.Column('replacement_of_id', UUID(as_uuid=True)),
        sa.CheckConstraint('replacement_of_id IS NULL', name='ck_'+table+'_replacement_reserved'),
        sa.CheckConstraint('amount > 0', name='ck_'+table+'_amount'),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_'+table+'_currency'),
        sa.CheckConstraint("status IN ('draft','posted','reversed')", name='ck_'+table+'_status'),
        sa.CheckConstraint("(status='draft' AND version=1 AND posting_id IS NULL AND posted_at IS NULL) OR (((status='posted' AND version=2) OR (status='reversed' AND version=3)) AND posting_id IS NOT NULL AND posted_at IS NOT NULL)", name='ck_'+table+'_posting'),
        sa.CheckConstraint("(status='reversed' AND reversal_posting_id IS NOT NULL AND reversed_at IS NOT NULL AND reversed_by IS NOT NULL AND length(btrim(reversal_reason))>0) OR (status<>'reversed' AND reversal_posting_id IS NULL AND reversed_at IS NULL AND reversed_by IS NULL AND reversal_reason IS NULL)", name='ck_'+table+'_reversal'),
    ]
    cols += [
        sa.Column('advance_id', UUID(as_uuid=True), sa.ForeignKey('finance.advances.id'), nullable=False),
        sa.Column('obligation_id', UUID(as_uuid=True), sa.ForeignKey('finance.obligations.id'), nullable=False),
        sa.Column('counterparty_id', UUID(as_uuid=True), sa.ForeignKey('core.counterparties.id'), nullable=False),
        sa.Column('direction', sa.String(10), nullable=False),
        sa.CheckConstraint("direction IN ('paid','received')", name='ck_application_direction'),
    ]
    op.create_table(table, *cols, schema='finance')
    for col in ('advance_id','obligation_id','counterparty_id'):
        op.create_index('ix_application_'+col, table, [col], schema='finance')
    op.execute("CREATE TRIGGER trg_application_integrity BEFORE INSERT OR UPDATE ON finance.advance_applications FOR EACH ROW EXECUTE FUNCTION finance.protect_integrity_record()")
    op.execute("CREATE TRIGGER trg_application_delete BEFORE DELETE ON finance.advance_applications FOR EACH ROW EXECUTE FUNCTION finance.protect_financial_delete()")
    op.execute("CREATE TRIGGER trg_application_reversal BEFORE UPDATE ON finance.advance_applications FOR EACH ROW EXECUTE FUNCTION finance.guard_reversal_transition('advance_application')")
    op.execute(SQL)


def downgrade():
    raise RuntimeError('0005 is forward-only: restore verified pre-upgrade backup separately and reconcile subsequent immutable history')


SQL = r"""
CREATE OR REPLACE VIEW finance.obligation_balances AS
SELECT o.id AS obligation_id, o.entity_id, o.counterparty_id, o.direction, o.currency, o.original_amount,
 (p.amount+s.amount+a.amount)::numeric(20,2) AS allocated_amount,
 (CASE WHEN o.status='reversed' THEN 0 ELSE o.original_amount-p.amount-s.amount-a.amount END)::numeric(20,2) AS remaining_amount,
 CASE WHEN o.status='reversed' THEN 'reversed' WHEN o.status<>'posted' THEN o.status
 WHEN o.original_amount-p.amount-s.amount-a.amount=0 THEN 'settled' WHEN p.amount+s.amount+a.amount>0 THEN 'partial' ELSE 'open' END AS settlement_status
FROM finance.obligations o
CROSS JOIN LATERAL (SELECT coalesce(sum(a.amount) FILTER (WHERE pay.status='posted'),0) AS amount
 FROM finance.payment_allocations a JOIN finance.payments pay ON pay.id=a.payment_id WHERE a.obligation_id=o.id) p
CROSS JOIN LATERAL (SELECT coalesce(sum(d.amount),0) AS amount FROM finance.direct_settlements d
 WHERE d.status='posted' AND o.id IN (d.receivable_id,d.payable_id)) s
CROSS JOIN LATERAL (SELECT coalesce(sum(x.amount),0) AS amount FROM finance.advance_applications x
 WHERE x.status='posted' AND x.obligation_id=o.id) a;


CREATE VIEW finance.advance_balances AS
SELECT a.id AS advance_id,a.entity_id,a.counterparty_id,a.direction,a.currency,a.original_amount,
 x.amount::numeric(20,2) AS applied_amount,
 (CASE WHEN a.status='reversed' THEN 0 ELSE a.original_amount-x.amount END)::numeric(20,2) AS remaining_amount
FROM finance.advances a
CROSS JOIN LATERAL (SELECT coalesce(sum(amount),0) AS amount FROM finance.advance_applications
 WHERE advance_id=a.id AND status='posted') x;

CREATE FUNCTION finance.guard_application() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE adv finance.advances%ROWTYPE; pay finance.payments%ROWTYPE; ob finance.obligations%ROWTYPE;
 remaining numeric; pid uuid;
BEGIN
 IF NEW.status='posted' AND OLD.status='draft' OR NEW.status='reversed' AND OLD.status='posted' THEN
   SELECT source_payment_id INTO pid FROM finance.advances WHERE id=NEW.advance_id;
   SELECT * INTO pay FROM finance.payments WHERE id=pid FOR UPDATE;
   SELECT * INTO adv FROM finance.advances WHERE id=NEW.advance_id FOR UPDATE;
   SELECT * INTO ob FROM finance.obligations WHERE id=NEW.obligation_id FOR UPDATE;
   IF pay.status IS DISTINCT FROM 'posted' OR adv.status IS DISTINCT FROM 'open' OR ob.status IS DISTINCT FROM 'posted'
     OR (adv.entity_id,adv.counterparty_id,adv.currency,adv.direction) IS DISTINCT FROM (NEW.entity_id,NEW.counterparty_id,NEW.currency,NEW.direction)
     OR (ob.entity_id,ob.counterparty_id,ob.currency,ob.direction) IS DISTINCT FROM
        (NEW.entity_id,NEW.counterparty_id,NEW.currency,CASE NEW.direction WHEN 'paid' THEN 'payable' ELSE 'receivable' END)
     OR NEW.event_date<adv.event_date OR NEW.event_date<ob.event_date
     OR NOT EXISTS(SELECT 1 FROM core.counterparties WHERE id=NEW.counterparty_id AND entity_id=NEW.entity_id) THEN
     RAISE EXCEPTION 'invalid application source/scope/lifecycle' USING ERRCODE='23514';
   END IF;
   IF NEW.status='posted' THEN
     SELECT remaining_amount INTO remaining FROM finance.advance_balances WHERE advance_id=adv.id;
     IF NEW.amount>remaining THEN RAISE EXCEPTION 'application exceeds advance remaining' USING ERRCODE='23514'; END IF;
     SELECT remaining_amount INTO remaining FROM finance.obligation_balances WHERE obligation_id=ob.id;
     IF NEW.amount>remaining THEN RAISE EXCEPTION 'application exceeds obligation remaining' USING ERRCODE='23514'; END IF;
   END IF;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER trg_application_sources BEFORE UPDATE ON finance.advance_applications FOR EACH ROW EXECUTE FUNCTION finance.guard_application();

CREATE FUNCTION finance.guard_applied_source_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status='reversed' AND OLD.status<>'reversed' AND EXISTS(
   SELECT 1 FROM finance.advance_applications x JOIN finance.advances a ON a.id=x.advance_id
   WHERE x.status='posted' AND
    ((TG_TABLE_NAME='payments' AND a.source_payment_id=OLD.id) OR
     (TG_TABLE_NAME='advances' AND a.id=OLD.id) OR
     (TG_TABLE_NAME='obligations' AND x.obligation_id=OLD.id))) THEN
   RAISE EXCEPTION 'reverse active advance applications first' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER trg_payment_applied_reversal BEFORE UPDATE ON finance.payments FOR EACH ROW EXECUTE FUNCTION finance.guard_applied_source_reversal();
CREATE TRIGGER trg_advance_applied_reversal BEFORE UPDATE ON finance.advances FOR EACH ROW EXECUTE FUNCTION finance.guard_applied_source_reversal();
CREATE TRIGGER trg_obligation_applied_reversal BEFORE UPDATE ON finance.obligations FOR EACH ROW EXECUTE FUNCTION finance.guard_applied_source_reversal();

CREATE FUNCTION finance.assert_application_posting() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r finance.advance_applications%ROWTYPE; p finance.postings%ROWTYPE; expected jsonb; d uuid; c uuid;
BEGIN
 SELECT * INTO r FROM finance.advance_applications WHERE id=NEW.id;
 IF r.status='draft' THEN RETURN NULL; END IF;
 SELECT * INTO p FROM finance.postings WHERE id=r.posting_id;
 IF p.id IS NULL OR (p.source_type,p.source_id,p.entity_id,p.currency,p.event_date,p.reversal_of_id)
   IS DISTINCT FROM ('advance_application'::text,r.id,r.entity_id,r.currency,r.event_date,NULL::uuid) THEN
   RAISE EXCEPTION 'application posting header mismatch' USING ERRCODE='23514';
 END IF;
 SELECT id INTO d FROM finance.ledger_accounts WHERE entity_id=r.entity_id AND currency=r.currency
  AND system_code=CASE r.direction WHEN 'paid' THEN 'AP_CONTROL' ELSE 'CUSTOMER_ADVANCE' END;
 SELECT id INTO c FROM finance.ledger_accounts WHERE entity_id=r.entity_id AND currency=r.currency
  AND system_code=CASE r.direction WHEN 'paid' THEN 'SUPPLIER_ADVANCE' ELSE 'AR_CONTROL' END;
 IF d IS NULL OR c IS NULL THEN RAISE EXCEPTION 'application control accounts missing' USING ERRCODE='23514'; END IF;
 expected=jsonb_build_array(jsonb_build_object('account',d,'side','debit','amount',r.amount),jsonb_build_object('account',c,'side','credit','amount',r.amount));
 IF EXISTS(
  (SELECT ledger_account_id::text,side,sum(amount) FROM finance.posting_entries WHERE posting_id=p.id GROUP BY ledger_account_id,side
   EXCEPT SELECT x->>'account',x->>'side',sum((x->>'amount')::numeric) FROM jsonb_array_elements(expected) x GROUP BY x->>'account',x->>'side')
  UNION ALL
  (SELECT x->>'account',x->>'side',sum((x->>'amount')::numeric) FROM jsonb_array_elements(expected) x GROUP BY x->>'account',x->>'side'
   EXCEPT SELECT ledger_account_id::text,side,sum(amount) FROM finance.posting_entries WHERE posting_id=p.id GROUP BY ledger_account_id,side)
 ) THEN RAISE EXCEPTION 'application journal effect mismatch' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER trg_application_posting AFTER INSERT OR UPDATE ON finance.advance_applications
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_application_posting();
    CREATE OR REPLACE FUNCTION finance.assert_compensating_posting() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE p finance.postings%ROWTYPE; original finance.postings%ROWTYPE; lifecycle jsonb;
    BEGIN
      SELECT * INTO p FROM finance.postings WHERE id=NEW.id;
      IF p.reversal_of_id IS NULL THEN RETURN NULL; END IF;
      SELECT * INTO original FROM finance.postings WHERE id=p.reversal_of_id;
      IF original.reversal_of_id IS NOT NULL OR original.entity_id<>p.entity_id OR original.currency<>p.currency
        OR p.event_date<original.event_date OR p.source_id<>original.source_id OR p.source_type<>original.source_type
        OR p.source_type NOT IN ('payment','obligation','transfer','owner_funding','direct_settlement','advance_application') OR length(btrim(p.reason)) IS NULL OR length(btrim(p.reason))=0 THEN
        RAISE EXCEPTION 'invalid reversal header' USING ERRCODE='23514';
      END IF;
      IF EXISTS (
        (SELECT ledger_account_id, side, sum(amount) FROM finance.posting_entries WHERE posting_id=p.id GROUP BY ledger_account_id,side
         EXCEPT
         SELECT ledger_account_id, CASE side WHEN 'debit' THEN 'credit' ELSE 'debit' END, sum(amount)
           FROM finance.posting_entries WHERE posting_id=original.id GROUP BY ledger_account_id,side)
        UNION ALL
        (SELECT ledger_account_id, CASE side WHEN 'debit' THEN 'credit' ELSE 'debit' END, sum(amount)
           FROM finance.posting_entries WHERE posting_id=original.id GROUP BY ledger_account_id,side
         EXCEPT
         SELECT ledger_account_id, side, sum(amount) FROM finance.posting_entries WHERE posting_id=p.id GROUP BY ledger_account_id,side)
      ) THEN RAISE EXCEPTION 'reversal must exactly compensate original entries' USING ERRCODE='23514'; END IF;
      EXECUTE format('SELECT to_jsonb(t) FROM finance.%I t WHERE id=$1', CASE p.source_type WHEN 'payment' THEN 'payments' WHEN 'obligation' THEN 'obligations' WHEN 'transfer' THEN 'own_account_transfers' WHEN 'owner_funding' THEN 'owner_fundings' WHEN 'direct_settlement' THEN 'direct_settlements' WHEN 'advance_application' THEN 'advance_applications' END)
        INTO lifecycle USING p.source_id;
      IF lifecycle IS NULL OR lifecycle->>'status'<>'reversed' OR lifecycle->>'reversal_posting_id'<>p.id::text THEN
        RAISE EXCEPTION 'compensating posting must be atomic with domain reversal' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END; $$;

"""
