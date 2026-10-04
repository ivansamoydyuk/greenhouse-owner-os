"""Finance integrity/corrections.

Recovery: forward-only once used. Restore a verified pre-upgrade backup into a
separate database and reconcile/export subsequent journal history before cutover.
Never discard compensating history by dropping reversal metadata.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261004_0003"
down_revision = "20261004_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("postings", sa.Column("reason", sa.Text()), schema="finance")
    for table in ("payments", "obligations"):
        for name, typ in (("reversal_posting_id", postgresql.UUID(as_uuid=True)),
                          ("reversed_by", postgresql.UUID(as_uuid=True)),
                          ("reversal_reason", sa.Text()),
                          ("replacement_of_id", postgresql.UUID(as_uuid=True))):
            op.add_column(table, sa.Column(name, typ), schema="finance")
        for name, target in (("reversal_posting_id", "finance.postings.id"),
                             ("reversed_by", "core.actors.id"),
                             ("replacement_of_id", f"finance.{table}.id")):
            schema, target_table, column = target.split(".")
            op.create_foreign_key(f"fk_{table}_{name}", table, target_table, [name], [column], source_schema="finance", referent_schema=schema)
        for name in ("reversal_posting_id", "replacement_of_id"):
            op.create_unique_constraint(f"uq_{table}_{name}", table, [name], schema="finance")
        op.create_check_constraint(f"ck_{table}_reversal_metadata", table,
            "(status='reversed' AND reversal_posting_id IS NOT NULL AND reversed_by IS NOT NULL AND reversed_at IS NOT NULL AND reversal_reason IS NOT NULL AND length(btrim(reversal_reason)) > 0) OR "
            "(status<>'reversed' AND reversal_posting_id IS NULL AND reversed_by IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL)", schema="finance")

    op.execute("""
    CREATE OR REPLACE FUNCTION finance.protect_integrity_record() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE original jsonb;
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.status NOT IN ('draft','review') OR NEW.posting_id IS NOT NULL OR NEW.posted_at IS NOT NULL THEN
          RAISE EXCEPTION 'insert a draft before posting' USING ERRCODE='23514';
        END IF;
      ELSE
        IF OLD.status IN ('posted','reversed') THEN
          IF OLD.status='posted' AND NEW.status='reversed' THEN
            IF NEW.version <> OLD.version+1 OR
              (to_jsonb(NEW) - ARRAY['status','version','reversal_posting_id','reversed_at','reversed_by','reversal_reason']) IS DISTINCT FROM
              (to_jsonb(OLD) - ARRAY['status','version','reversal_posting_id','reversed_at','reversed_by','reversal_reason']) THEN
              RAISE EXCEPTION 'only reversal lifecycle metadata may change' USING ERRCODE='55000';
            END IF;
          ELSIF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
            RAISE EXCEPTION 'posted history is immutable' USING ERRCODE='55000';
          END IF;
        END IF;
        IF OLD.replacement_of_id IS DISTINCT FROM NEW.replacement_of_id THEN
          RAISE EXCEPTION 'replacement link is immutable' USING ERRCODE='55000';
        END IF;
      END IF;
      IF NEW.replacement_of_id IS NOT NULL THEN
        EXECUTE format('SELECT to_jsonb(t) FROM finance.%I t WHERE id=$1 FOR UPDATE', TG_TABLE_NAME)
          INTO original USING NEW.replacement_of_id;
        IF original IS NULL OR original->>'status'<>'reversed' OR NEW.id=NEW.replacement_of_id OR
          original->>'entity_id' <> NEW.entity_id::text OR original->>'currency' <> NEW.currency OR
          original->>'counterparty_id' <> NEW.counterparty_id::text OR original->>'direction' <> NEW.direction THEN
          RAISE EXCEPTION 'replacement requires reversed original in same scope/currency/direction' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER trg_payment_integrity BEFORE INSERT OR UPDATE ON finance.payments
      FOR EACH ROW EXECUTE FUNCTION finance.protect_integrity_record();
    CREATE TRIGGER trg_obligation_integrity BEFORE INSERT OR UPDATE ON finance.obligations
      FOR EACH ROW EXECUTE FUNCTION finance.protect_integrity_record();

    CREATE OR REPLACE FUNCTION finance.protect_payment_allocation() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE v_status text; v_id uuid;
    BEGIN
      -- Check BOTH parents during a move, and lock against concurrent posting.
      FOR v_id IN SELECT DISTINCT x FROM unnest(ARRAY[
        CASE WHEN TG_OP<>'INSERT' THEN OLD.payment_id END,
        CASE WHEN TG_OP<>'DELETE' THEN NEW.payment_id END]) x WHERE x IS NOT NULL ORDER BY x LOOP
        SELECT status INTO v_status FROM finance.payments WHERE id=v_id FOR UPDATE;
        IF v_status IN ('posted','reversed') THEN
          RAISE EXCEPTION 'allocations of posted/reversed payment are immutable' USING ERRCODE='55000';
        END IF;
      END LOOP;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END; $$;

    CREATE OR REPLACE FUNCTION finance.guard_reversal_transition() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE p finance.postings%ROWTYPE;
    BEGIN
      IF NEW.status='reversed' AND OLD.status='posted' THEN
        SELECT * INTO p FROM finance.postings WHERE id=NEW.reversal_posting_id;
        IF p.id IS NULL OR p.reversal_of_id IS DISTINCT FROM OLD.posting_id OR
          p.entity_id<>NEW.entity_id OR p.currency<>NEW.currency OR p.source_id<>NEW.id OR
          p.source_type<>TG_ARGV[0] OR p.created_by<>NEW.reversed_by OR p.reason IS DISTINCT FROM NEW.reversal_reason THEN
          RAISE EXCEPTION 'reversal requires matching compensating posting and actor/reason' USING ERRCODE='23514';
        END IF;
        IF TG_ARGV[0]='obligation' AND EXISTS (
          SELECT 1 FROM finance.payment_allocations a JOIN finance.payments pay ON pay.id=a.payment_id
          WHERE a.obligation_id=NEW.id AND pay.status='posted') THEN
          RAISE EXCEPTION 'reverse active allocated payments first' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER trg_payment_reversal BEFORE UPDATE ON finance.payments
      FOR EACH ROW EXECUTE FUNCTION finance.guard_reversal_transition('payment');
    CREATE TRIGGER trg_obligation_reversal BEFORE UPDATE ON finance.obligations
      FOR EACH ROW EXECUTE FUNCTION finance.guard_reversal_transition('obligation');

    CREATE OR REPLACE FUNCTION finance.assert_compensating_posting() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE p finance.postings%ROWTYPE; original finance.postings%ROWTYPE; lifecycle jsonb;
    BEGIN
      SELECT * INTO p FROM finance.postings WHERE id=NEW.id;
      IF p.reversal_of_id IS NULL THEN RETURN NULL; END IF;
      SELECT * INTO original FROM finance.postings WHERE id=p.reversal_of_id;
      IF original.reversal_of_id IS NOT NULL OR original.entity_id<>p.entity_id OR original.currency<>p.currency
        OR p.event_date<original.event_date OR p.source_id<>original.source_id OR p.source_type<>original.source_type
        OR p.source_type NOT IN ('payment','obligation') OR length(btrim(p.reason)) IS NULL OR length(btrim(p.reason))=0 THEN
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
      EXECUTE format('SELECT to_jsonb(t) FROM finance.%I t WHERE id=$1', CASE p.source_type WHEN 'payment' THEN 'payments' ELSE 'obligations' END)
        INTO lifecycle USING p.source_id;
      IF lifecycle IS NULL OR lifecycle->>'status'<>'reversed' OR lifecycle->>'reversal_posting_id'<>p.id::text THEN
        RAISE EXCEPTION 'compensating posting must be atomic with domain reversal' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END; $$;
    CREATE CONSTRAINT TRIGGER trg_posting_compensation AFTER INSERT ON finance.postings
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_compensating_posting();

    CREATE OR REPLACE FUNCTION finance.protect_entry_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM finance.postings WHERE id=NEW.posting_id
        AND xmin::text::bigint = txid_current() % 4294967296) THEN
        RAISE EXCEPTION 'cannot append entries to committed posting' USING ERRCODE='55000';
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER trg_entry_insert BEFORE INSERT ON finance.posting_entries
      FOR EACH ROW EXECUTE FUNCTION finance.protect_entry_insert();

    CREATE OR REPLACE FUNCTION finance.protect_advance() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'advance history cannot be deleted' USING ERRCODE='55000'; END IF;
      IF TG_OP='UPDATE' AND (OLD.status<>'open' OR NEW.status<>'reversed' OR
        (to_jsonb(NEW)-'status') IS DISTINCT FROM (to_jsonb(OLD)-'status')) THEN
        RAISE EXCEPTION 'advance history immutable except reversal lifecycle' USING ERRCODE='55000';
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER trg_advance_protect BEFORE UPDATE OR DELETE ON finance.advances
      FOR EACH ROW EXECUTE FUNCTION finance.protect_advance();

    CREATE OR REPLACE FUNCTION finance.assert_advance_consistency() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE pay finance.payments%ROWTYPE; adv finance.advances%ROWTYPE; total numeric;
    BEGIN
      IF TG_TABLE_NAME='advances' THEN
        SELECT * INTO pay FROM finance.payments WHERE id=NEW.source_payment_id;
      ELSE SELECT * INTO pay FROM finance.payments WHERE id=NEW.id; END IF;
      SELECT * INTO adv FROM finance.advances WHERE source_payment_id=pay.id;
      SELECT coalesce(sum(amount),0) INTO total FROM finance.payment_allocations WHERE payment_id=pay.id;
      IF pay.status IN ('posted','reversed') THEN
        IF pay.amount>total AND (adv.id IS NULL OR adv.original_amount<>pay.amount-total OR
          adv.entity_id<>pay.entity_id OR adv.counterparty_id<>pay.counterparty_id OR adv.currency<>pay.currency OR
          adv.direction<>CASE pay.direction WHEN 'out' THEN 'paid' ELSE 'received' END OR
          adv.status<>CASE pay.status WHEN 'posted' THEN 'open' ELSE 'reversed' END) THEN
          RAISE EXCEPTION 'payment remainder/advance inconsistent' USING ERRCODE='23514';
        END IF;
        IF pay.amount=total AND adv.id IS NOT NULL THEN
          RAISE EXCEPTION 'fully allocated payment cannot have advance' USING ERRCODE='23514';
        END IF;
      ELSIF adv.id IS NOT NULL THEN
        RAISE EXCEPTION 'draft payment cannot have advance effect' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END; $$;
    CREATE CONSTRAINT TRIGGER trg_payment_advance_consistency AFTER INSERT OR UPDATE ON finance.payments
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_advance_consistency();
    CREATE CONSTRAINT TRIGGER trg_advance_consistency AFTER INSERT OR UPDATE ON finance.advances
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_advance_consistency();

    CREATE OR REPLACE FUNCTION finance.protect_account_scope() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (NEW.entity_id,NEW.currency) IS DISTINCT FROM (OLD.entity_id,OLD.currency) OR
        (TG_TABLE_NAME='money_accounts' AND to_jsonb(NEW)->>'ledger_account_id' IS DISTINCT FROM to_jsonb(OLD)->>'ledger_account_id') THEN
        RAISE EXCEPTION 'account entity/currency/ledger identity is immutable' USING ERRCODE='55000';
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER trg_ledger_scope BEFORE UPDATE ON finance.ledger_accounts
      FOR EACH ROW EXECUTE FUNCTION finance.protect_account_scope();
    CREATE TRIGGER trg_money_scope BEFORE UPDATE ON finance.money_accounts
      FOR EACH ROW EXECUTE FUNCTION finance.protect_account_scope();

    CREATE OR REPLACE VIEW finance.obligation_balances AS
    SELECT o.id AS obligation_id, o.entity_id, o.counterparty_id, o.direction, o.currency, o.original_amount,
      COALESCE(SUM(pa.amount) FILTER (WHERE p.status='posted'),0)::numeric(20,2) AS allocated_amount,
      (CASE WHEN o.status='reversed' THEN 0 ELSE o.original_amount-COALESCE(SUM(pa.amount) FILTER (WHERE p.status='posted'),0) END)::numeric(20,2) AS remaining_amount,
      CASE WHEN o.status='reversed' THEN 'reversed' WHEN o.status<>'posted' THEN o.status
        WHEN o.original_amount-COALESCE(SUM(pa.amount) FILTER (WHERE p.status='posted'),0)=0 THEN 'settled'
        WHEN COALESCE(SUM(pa.amount) FILTER (WHERE p.status='posted'),0)>0 THEN 'partial' ELSE 'open' END AS settlement_status
    FROM finance.obligations o LEFT JOIN finance.payment_allocations pa ON pa.obligation_id=o.id
    LEFT JOIN finance.payments p ON p.id=pa.payment_id GROUP BY o.id;
    """)


def downgrade():
    raise RuntimeError("0003 is forward-only: preserve financial history; restore verified pre-upgrade backup into a separate database and reconcile subsequent postings before cutover")
