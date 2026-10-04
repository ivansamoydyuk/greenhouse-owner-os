"""Explicit owner-verified outstanding payable; never historical expense replay.

Forward-only recovery: restore verified backup separately and reconcile subsequent
immutable opening approvals, live journal and payments. No destructive downgrade.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '20261004_0008'
down_revision = '20261004_0007'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_entity_opening_policy', 'entities', schema='core')
    op.create_check_constraint('ck_entity_opening_policy', 'entities',
        "finance_opening_policy IN ('ZERO','EXPLICIT_AP')", schema='core')
    op.create_table('opening_payables',
        sa.Column('obligation_id', UUID(as_uuid=True), sa.ForeignKey('finance.obligations.id'), primary_key=True),
        sa.Column('entity_id', UUID(as_uuid=True), sa.ForeignKey('core.entities.id'), nullable=False),
        sa.Column('counterparty_id', UUID(as_uuid=True), sa.ForeignKey('core.counterparties.id'), nullable=False),
        sa.Column('amount', sa.Numeric(20, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('original_event_date', sa.Date(), nullable=False),
        sa.Column('opening_date', sa.Date(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('approval_reference', sa.Text(), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('verification_state', sa.String(20), nullable=False, server_default='OWNER_VERIFIED'),
        sa.Column('verified_by', UUID(as_uuid=True), sa.ForeignKey('core.actors.id'), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('entity_id', 'approval_reference', name='uq_opening_payable_approval'),
        sa.UniqueConstraint('entity_id', 'source', name='uq_opening_payable_source'),
        sa.CheckConstraint('amount > 0 AND original_event_date < opening_date', name='ck_opening_payable_dates_amount'),
        sa.CheckConstraint("verification_state = 'OWNER_VERIFIED'", name='ck_opening_payable_verified'),
        sa.CheckConstraint("request_hash ~ '^[a-f0-9]{64}$' AND btrim(source)<>'' AND btrim(description)<>'' AND btrim(approval_reference)<>''", name='ck_opening_payable_provenance'),
        schema='finance')
    op.execute("""
    CREATE OR REPLACE FUNCTION core.guard_cutover_config() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.finance_cutover_date <> DATE '2026-10-04' OR NEW.finance_opening_policy <> 'ZERO' THEN
          RAISE EXCEPTION 'entity starts with zero base; explicit opening approval required' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.finance_cutover_date IS DISTINCT FROM OLD.finance_cutover_date OR
            (NEW.finance_opening_policy IS DISTINCT FROM OLD.finance_opening_policy AND NOT
             (OLD.finance_opening_policy='ZERO' AND NEW.finance_opening_policy='EXPLICIT_AP' AND
              EXISTS (SELECT 1 FROM finance.opening_payables WHERE entity_id=NEW.id))) THEN
        RAISE EXCEPTION 'cut-over policy is immutable except verified payable recognition' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;

    CREATE OR REPLACE FUNCTION finance.guard_domain_cutover() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE cutoff date; approved boolean := false;
    BEGIN
      IF NEW.status IN ('posted','reversed') THEN
        SELECT finance_cutover_date INTO cutoff FROM core.entities WHERE id=NEW.entity_id;
        IF TG_TABLE_NAME='obligations' THEN
          SELECT EXISTS (SELECT 1 FROM finance.opening_payables a
            WHERE a.obligation_id=NEW.id AND a.entity_id=NEW.entity_id AND a.counterparty_id=NEW.counterparty_id
              AND a.currency=NEW.currency AND a.amount=NEW.original_amount
              AND a.original_event_date=NEW.event_date AND a.opening_date=cutoff AND NEW.direction='payable') INTO approved;
        END IF;
        IF cutoff IS NULL OR (NEW.event_date < cutoff AND NOT approved) THEN
          RAISE EXCEPTION 'pre-cut-over financial record cannot become live' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;

    CREATE FUNCTION finance.guard_opening_approval() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM core.actors WHERE id=NEW.verified_by AND actor_type='owner' AND active) THEN
        RAISE EXCEPTION 'opening requires active owner approval' USING ERRCODE='23514';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM core.entities e JOIN core.counterparties c ON c.entity_id=e.id
        WHERE e.id=NEW.entity_id AND e.finance_cutover_date=NEW.opening_date AND c.id=NEW.counterparty_id
          AND c.active AND c.counterparty_type IN ('supplier','both')) THEN
        RAISE EXCEPTION 'opening payable entity/cut-over/supplier mismatch' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_owner_guard BEFORE INSERT ON finance.opening_payables
      FOR EACH ROW EXECUTE FUNCTION finance.guard_opening_approval();
    CREATE TRIGGER immutable_opening_approval BEFORE UPDATE OR DELETE ON finance.opening_payables
      FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

    CREATE FUNCTION finance.assert_opening_payable() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE oid uuid; a finance.opening_payables%ROWTYPE; o finance.obligations%ROWTYPE;
      p finance.postings%ROWTYPE; offset_account finance.ledger_accounts%ROWTYPE;
      ap_id uuid; entry_count int; debit_total numeric; credit_total numeric;
    BEGIN
      IF TG_TABLE_NAME='opening_payables' THEN oid=NEW.obligation_id; ELSE oid=NEW.id; END IF;
      SELECT * INTO o FROM finance.obligations WHERE id=oid;
      SELECT * INTO a FROM finance.opening_payables WHERE obligation_id=oid;
      SELECT * INTO offset_account FROM finance.ledger_accounts WHERE id=o.offset_ledger_account_id;
      IF a.obligation_id IS NULL THEN
        IF offset_account.account_class='opening_equity' OR offset_account.system_code='OPENING_PAYABLE_OFFSET' THEN
          RAISE EXCEPTION 'opening offset requires explicit owner-verified approval' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
      END IF;
      IF o.id IS NULL OR o.status NOT IN ('posted','reversed') OR o.direction<>'payable'
        OR (o.entity_id,o.counterparty_id,o.currency,o.original_amount,o.event_date)
          IS DISTINCT FROM (a.entity_id,a.counterparty_id,a.currency,a.amount,a.original_event_date)
        OR o.created_by<>a.verified_by OR o.replacement_of_id IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM core.entities WHERE id=a.entity_id AND finance_opening_policy='EXPLICIT_AP'
                       AND finance_cutover_date=a.opening_date)
        OR (offset_account.entity_id,offset_account.currency,offset_account.account_class,offset_account.normal_side,offset_account.system_code)
          IS DISTINCT FROM (a.entity_id,a.currency,'opening_equity'::varchar,'debit'::varchar,'OPENING_PAYABLE_OFFSET'::varchar) THEN
        RAISE EXCEPTION 'invalid verified opening payable linkage' USING ERRCODE='23514';
      END IF;
      SELECT * INTO p FROM finance.postings WHERE id=o.posting_id;
      IF p.id IS NULL OR (p.entity_id,p.currency,p.event_date,p.source_type,p.source_id,p.posting_type,p.created_by)
        IS DISTINCT FROM (a.entity_id,a.currency,a.opening_date,'obligation'::varchar,o.id,'opening_payable'::varchar,a.verified_by)
        OR p.reversal_of_id IS NOT NULL THEN
        RAISE EXCEPTION 'opening journal must recognize approved payable at cut-over only' USING ERRCODE='23514';
      END IF;
      SELECT id INTO ap_id FROM finance.ledger_accounts WHERE entity_id=a.entity_id AND currency=a.currency
        AND system_code='AP_CONTROL' AND account_class='payable' AND normal_side='credit';
      SELECT count(*),coalesce(sum(amount) FILTER (WHERE ledger_account_id=o.offset_ledger_account_id AND side='debit'),0),
                     coalesce(sum(amount) FILTER (WHERE ledger_account_id=ap_id AND side='credit'),0)
        INTO entry_count,debit_total,credit_total FROM finance.posting_entries WHERE posting_id=p.id;
      IF entry_count<>2 OR debit_total<>a.amount OR credit_total<>a.amount THEN
        RAISE EXCEPTION 'opening journal must affect only opening offset and AP; no cash or P&L' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER opening_payable_effect AFTER INSERT ON finance.opening_payables
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_opening_payable();
    CREATE CONSTRAINT TRIGGER obligation_opening_effect AFTER INSERT OR UPDATE ON finance.obligations
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_opening_payable();

    CREATE FUNCTION finance.assert_reserved_opening_posting() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE p finance.postings%ROWTYPE; pid uuid;
    BEGIN
      IF TG_TABLE_NAME='postings' THEN pid=NEW.id; ELSE pid=NEW.posting_id; END IF;
      SELECT * INTO p FROM finance.postings WHERE id=pid;
      IF p.posting_type='opening_payable' OR EXISTS (
        SELECT 1 FROM finance.posting_entries pe JOIN finance.ledger_accounts l ON l.id=pe.ledger_account_id
        WHERE pe.posting_id=pid AND (l.account_class='opening_equity' OR l.system_code='OPENING_PAYABLE_OFFSET')) THEN
        IF NOT EXISTS (SELECT 1 FROM finance.opening_payables a JOIN finance.obligations o ON o.id=a.obligation_id
          WHERE a.obligation_id=p.source_id AND p.source_type='obligation'
            AND p.entity_id=a.entity_id AND p.currency=a.currency
            AND (o.posting_id=pid OR o.reversal_posting_id=pid)) THEN
          RAISE EXCEPTION 'reserved opening journal/account requires explicit approved obligation linkage' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER opening_posting_reserved AFTER INSERT ON finance.postings
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_reserved_opening_posting();
    CREATE CONSTRAINT TRIGGER opening_entry_reserved AFTER INSERT ON finance.posting_entries
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finance.assert_reserved_opening_posting();
    """)


def downgrade():
    raise RuntimeError('Forward-only recovery: separate backup restore + opening/journal/payment reconciliation')
