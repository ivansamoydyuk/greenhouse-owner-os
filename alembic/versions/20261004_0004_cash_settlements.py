"""Cash & settlements. Forward-only recovery after use: restore verified pre-upgrade
backup into a separate database, retain/export/reconcile subsequent journal history
before cutover. Never drop posted business or compensating history in place.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '20261004_0004'
down_revision = '20261004_0003'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('source', 'reason'):
        op.add_column('payments', sa.Column(name, sa.Text()), schema='finance')
    op.execute(CASH_FUNCTIONS)
    for kind, table in [('transfer','own_account_transfers'), ('owner_funding','owner_fundings'), ('direct_settlement','direct_settlements')]:
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
        if kind == 'transfer':
            cols += [sa.Column('source_account_id', UUID(as_uuid=True), sa.ForeignKey('finance.money_accounts.id'), nullable=False),
                sa.Column('destination_account_id', UUID(as_uuid=True), sa.ForeignKey('finance.money_accounts.id'), nullable=False),
                sa.Column('fee', sa.Numeric(20,2), nullable=False, server_default='0'),
                sa.Column('fee_account_id', UUID(as_uuid=True), sa.ForeignKey('finance.ledger_accounts.id')),
                sa.CheckConstraint('source_account_id <> destination_account_id', name='ck_transfer_accounts'),
                sa.CheckConstraint('(fee=0 AND fee_account_id IS NULL) OR (fee>0 AND fee_account_id IS NOT NULL)', name='ck_transfer_fee')]
        elif kind == 'owner_funding':
            cols += [sa.Column('money_account_id', UUID(as_uuid=True), sa.ForeignKey('finance.money_accounts.id'), nullable=False)]
        else:
            cols += [sa.Column('receivable_id', UUID(as_uuid=True), sa.ForeignKey('finance.obligations.id'), nullable=False),
                sa.Column('payable_id', UUID(as_uuid=True), sa.ForeignKey('finance.obligations.id'), nullable=False),
                sa.CheckConstraint('receivable_id <> payable_id', name='ck_settlement_pair')]
        op.create_table(table, *cols, schema='finance')
        op.create_index('ix_'+table+'_entity_status', table, ['entity_id','status'], schema='finance')
        if kind == 'direct_settlement':
            for col in ('receivable_id','payable_id'):
                op.create_index('ix_settlement_'+col, table, [col], schema='finance')
        op.execute(f"CREATE TRIGGER trg_{table}_integrity BEFORE INSERT OR UPDATE ON finance.{table} FOR EACH ROW EXECUTE FUNCTION finance.protect_integrity_record()")
        op.execute(f"CREATE TRIGGER trg_{table}_delete BEFORE DELETE ON finance.{table} FOR EACH ROW EXECUTE FUNCTION finance.protect_financial_delete()")
        op.execute(f"CREATE TRIGGER trg_{table}_reversal BEFORE UPDATE ON finance.{table} FOR EACH ROW EXECUTE FUNCTION finance.guard_reversal_transition('{kind}')")
        op.execute(f"CREATE CONSTRAINT TRIGGER trg_{table}_posting AFTER INSERT OR UPDATE ON finance.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_cash_posting('{kind}')")
    op.execute("""
    CREATE OR REPLACE FUNCTION finance.assert_compensating_posting() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE p finance.postings%ROWTYPE; original finance.postings%ROWTYPE; lifecycle jsonb;
    BEGIN
      SELECT * INTO p FROM finance.postings WHERE id=NEW.id;
      IF p.reversal_of_id IS NULL THEN RETURN NULL; END IF;
      SELECT * INTO original FROM finance.postings WHERE id=p.reversal_of_id;
      IF original.reversal_of_id IS NOT NULL OR original.entity_id<>p.entity_id OR original.currency<>p.currency
        OR p.event_date<original.event_date OR p.source_id<>original.source_id OR p.source_type<>original.source_type
        OR p.source_type NOT IN ('payment','obligation','transfer','owner_funding','direct_settlement') OR length(btrim(p.reason)) IS NULL OR length(btrim(p.reason))=0 THEN
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
      EXECUTE format('SELECT to_jsonb(t) FROM finance.%I t WHERE id=$1', CASE p.source_type WHEN 'payment' THEN 'payments' WHEN 'obligation' THEN 'obligations' WHEN 'transfer' THEN 'own_account_transfers' WHEN 'owner_funding' THEN 'owner_fundings' WHEN 'direct_settlement' THEN 'direct_settlements' END)
        INTO lifecycle USING p.source_id;
      IF lifecycle IS NULL OR lifecycle->>'status'<>'reversed' OR lifecycle->>'reversal_posting_id'<>p.id::text THEN
        RAISE EXCEPTION 'compensating posting must be atomic with domain reversal' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END; $$;

CREATE OR REPLACE VIEW finance.obligation_balances AS
SELECT o.id AS obligation_id, o.entity_id, o.counterparty_id, o.direction, o.currency, o.original_amount,
 (p.amount+s.amount)::numeric(20,2) AS allocated_amount,
 (CASE WHEN o.status='reversed' THEN 0 ELSE o.original_amount-p.amount-s.amount END)::numeric(20,2) AS remaining_amount,
 CASE WHEN o.status='reversed' THEN 'reversed' WHEN o.status<>'posted' THEN o.status
 WHEN o.original_amount-p.amount-s.amount=0 THEN 'settled' WHEN p.amount+s.amount>0 THEN 'partial' ELSE 'open' END AS settlement_status
FROM finance.obligations o
CROSS JOIN LATERAL (SELECT coalesce(sum(a.amount) FILTER (WHERE pay.status='posted'),0) AS amount
 FROM finance.payment_allocations a JOIN finance.payments pay ON pay.id=a.payment_id WHERE a.obligation_id=o.id) p
CROSS JOIN LATERAL (SELECT coalesce(sum(d.amount),0) AS amount FROM finance.direct_settlements d
 WHERE d.status='posted' AND o.id IN (d.receivable_id,d.payable_id)) s;

CREATE OR REPLACE FUNCTION finance.assert_settlement_postable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ob finance.obligations%ROWTYPE; oid uuid; remaining numeric;
BEGIN
 IF NEW.status='posted' AND OLD.status<>'posted' THEN
   FOR oid IN SELECT x FROM unnest(ARRAY[NEW.receivable_id,NEW.payable_id]) x ORDER BY x LOOP
     SELECT * INTO ob FROM finance.obligations WHERE id=oid FOR UPDATE;
     SELECT remaining_amount INTO remaining FROM finance.obligation_balances WHERE obligation_id=oid;
     IF ob.status<>'posted' OR ob.entity_id<>NEW.entity_id OR ob.currency<>NEW.currency OR NEW.event_date<ob.event_date
       OR ob.direction<>(CASE WHEN oid=NEW.receivable_id THEN 'receivable' ELSE 'payable' END)
       OR NOT EXISTS(SELECT 1 FROM core.counterparties WHERE id=ob.counterparty_id AND entity_id=NEW.entity_id)
       OR NEW.amount>remaining THEN
       RAISE EXCEPTION 'invalid/over settlement' USING ERRCODE='23514';
     END IF;
   END LOOP;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER trg_settlement_postable BEFORE UPDATE OF status ON finance.direct_settlements
 FOR EACH ROW EXECUTE FUNCTION finance.assert_settlement_postable();

CREATE OR REPLACE FUNCTION finance.assert_payment_postable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a record; ob finance.obligations%ROWTYPE; remaining numeric; total numeric;
BEGIN
 IF NEW.status='posted' AND OLD.status<>'posted' THEN
   SELECT coalesce(sum(amount),0) INTO total FROM finance.payment_allocations WHERE payment_id=NEW.id;
   IF total>NEW.amount THEN RAISE EXCEPTION 'allocation exceeds payment' USING ERRCODE='23514'; END IF;
   FOR a IN SELECT obligation_id, sum(amount) AS amount FROM finance.payment_allocations WHERE payment_id=NEW.id GROUP BY obligation_id ORDER BY obligation_id LOOP
     SELECT * INTO ob FROM finance.obligations WHERE id=a.obligation_id FOR UPDATE;
     SELECT remaining_amount INTO remaining FROM finance.obligation_balances WHERE obligation_id=ob.id;
     IF ob.status<>'posted' OR ob.entity_id<>NEW.entity_id OR ob.currency<>NEW.currency OR ob.counterparty_id<>NEW.counterparty_id
       OR ob.direction<>(CASE NEW.direction WHEN 'out' THEN 'payable' ELSE 'receivable' END) OR a.amount>remaining THEN
       RAISE EXCEPTION 'invalid/over allocation' USING ERRCODE='23514';
     END IF;
   END LOOP;
 END IF;
 RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION finance.guard_settled_obligation_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status='reversed' AND OLD.status='posted' AND EXISTS(SELECT 1 FROM finance.direct_settlements
 WHERE status='posted' AND OLD.id IN (receivable_id,payable_id)) THEN
   RAISE EXCEPTION 'reverse active direct settlements first' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER trg_obligation_settlement_reversal BEFORE UPDATE ON finance.obligations
 FOR EACH ROW EXECUTE FUNCTION finance.guard_settled_obligation_reversal();
    """)

def downgrade():
    raise RuntimeError("0004 is forward-only: restore verified pre-upgrade backup separately and reconcile subsequent immutable journal history")

CASH_FUNCTIONS = """

CREATE OR REPLACE FUNCTION finance.assert_cash_posting() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb; p finance.postings%ROWTYPE; expected jsonb; source_ledger uuid; destination_ledger uuid; control uuid;
BEGIN
 EXECUTE format('SELECT to_jsonb(t) FROM finance.%I t WHERE id=$1', TG_TABLE_NAME) INTO r USING NEW.id;
 IF r->>'status'='draft' THEN RETURN NULL; END IF;
 SELECT * INTO p FROM finance.postings WHERE id=(r->>'posting_id')::uuid;
 IF p.id IS NULL OR p.source_type<>TG_ARGV[0] OR p.source_id<>(r->>'id')::uuid OR p.entity_id<>(r->>'entity_id')::uuid
   OR p.currency<>r->>'currency' OR p.event_date<>(r->>'event_date')::date OR p.reversal_of_id IS NOT NULL THEN
   RAISE EXCEPTION 'cash domain/posting mismatch' USING ERRCODE='23514';
 END IF;
 IF TG_ARGV[0]='transfer' THEN
   SELECT ledger_account_id INTO source_ledger FROM finance.money_accounts WHERE id=(r->>'source_account_id')::uuid AND entity_id=p.entity_id AND currency=p.currency AND active;
   SELECT ledger_account_id INTO destination_ledger FROM finance.money_accounts WHERE id=(r->>'destination_account_id')::uuid AND entity_id=p.entity_id AND currency=p.currency AND active;
   IF source_ledger IS NULL OR destination_ledger IS NULL OR source_ledger=destination_ledger THEN RAISE EXCEPTION 'invalid transfer accounts' USING ERRCODE='23514'; END IF;
   expected=jsonb_build_array(jsonb_build_object('account',source_ledger,'side','credit','amount',(r->>'amount')::numeric), jsonb_build_object('account',destination_ledger,'side','debit','amount',(r->>'amount')::numeric));
   IF (r->>'fee')::numeric>0 THEN
     IF NOT EXISTS(SELECT 1 FROM finance.ledger_accounts WHERE id=(r->>'fee_account_id')::uuid AND entity_id=p.entity_id AND currency=p.currency AND account_class='expense' AND active) THEN RAISE EXCEPTION 'fee needs expense account' USING ERRCODE='23514'; END IF;
     expected=expected||jsonb_build_array(jsonb_build_object('account',source_ledger,'side','credit','amount',(r->>'fee')::numeric),jsonb_build_object('account',r->>'fee_account_id','side','debit','amount',(r->>'fee')::numeric));
   END IF;
 ELSIF TG_ARGV[0]='owner_funding' THEN
   SELECT ledger_account_id INTO destination_ledger FROM finance.money_accounts WHERE id=(r->>'money_account_id')::uuid AND entity_id=p.entity_id AND currency=p.currency AND active;
   SELECT id INTO control FROM finance.ledger_accounts WHERE entity_id=p.entity_id AND currency=p.currency AND system_code='OWNER_FUNDING' AND account_class='owner_funding' AND active;
   IF destination_ledger IS NULL OR control IS NULL THEN RAISE EXCEPTION 'invalid funding accounts' USING ERRCODE='23514'; END IF;
   expected=jsonb_build_array(jsonb_build_object('account',destination_ledger,'side','debit','amount',(r->>'amount')::numeric),jsonb_build_object('account',control,'side','credit','amount',(r->>'amount')::numeric));
 ELSE
   SELECT id INTO source_ledger FROM finance.ledger_accounts WHERE entity_id=p.entity_id AND currency=p.currency AND system_code='AR_CONTROL' AND active;
   SELECT id INTO destination_ledger FROM finance.ledger_accounts WHERE entity_id=p.entity_id AND currency=p.currency AND system_code='AP_CONTROL' AND active;
   IF source_ledger IS NULL OR destination_ledger IS NULL THEN RAISE EXCEPTION 'settlement control missing' USING ERRCODE='23514'; END IF;
   expected=jsonb_build_array(jsonb_build_object('account',source_ledger,'side','credit','amount',(r->>'amount')::numeric),jsonb_build_object('account',destination_ledger,'side','debit','amount',(r->>'amount')::numeric));
 END IF;
 IF EXISTS(
   (SELECT ledger_account_id::text,side,sum(amount) FROM finance.posting_entries WHERE posting_id=p.id GROUP BY ledger_account_id,side
    EXCEPT SELECT x->>'account',x->>'side',sum((x->>'amount')::numeric) FROM jsonb_array_elements(expected) x GROUP BY x->>'account',x->>'side')
   UNION ALL
   (SELECT x->>'account',x->>'side',sum((x->>'amount')::numeric) FROM jsonb_array_elements(expected) x GROUP BY x->>'account',x->>'side'
    EXCEPT SELECT ledger_account_id::text,side,sum(amount) FROM finance.posting_entries WHERE posting_id=p.id GROUP BY ledger_account_id,side)
 ) THEN RAISE EXCEPTION 'cash posting does not match domain effects' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END; $$;
"""
