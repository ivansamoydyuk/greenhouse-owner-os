"""finance vertical: obligation payment allocation advance

Revision ID: 20261004_0002
Revises: 20261004_0001
Create Date: 2026-10-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261004_0002"
down_revision = "20261004_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "obligations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.entities.id"), nullable=False),
        sa.Column("counterparty_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.counterparties.id"), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("original_amount", sa.Numeric(20,2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date()),
        sa.Column("offset_ledger_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.ledger_accounts.id"), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("posting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.postings.id")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.actors.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("direction in ('payable','receivable')", name="ck_obligation_direction"),
        sa.CheckConstraint("status in ('draft','review','posted','reversed')", name="ck_obligation_status"),
        sa.CheckConstraint("original_amount > 0", name="ck_obligation_amount_positive"),
        schema="finance",
    )
    op.create_index("ix_obligation_counterparty_status", "obligations", ["counterparty_id","status"], schema="finance")

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.entities.id"), nullable=False),
        sa.Column("money_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.money_accounts.id"), nullable=False),
        sa.Column("counterparty_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.counterparties.id"), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(20,2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("posting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.postings.id")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.actors.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("direction in ('in','out')", name="ck_payment_direction"),
        sa.CheckConstraint("status in ('draft','review','posted','reversed')", name="ck_payment_status"),
        sa.CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        schema="finance",
    )
    op.create_index("ix_payment_counterparty_status", "payments", ["counterparty_id","status"], schema="finance")

    op.create_table(
        "payment_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.obligations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(20,2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("amount > 0", name="ck_payment_allocation_amount_positive"),
        schema="finance",
    )
    op.create_index("ix_payment_allocation_obligation", "payment_allocations", ["obligation_id"], schema="finance")

    op.create_table(
        "advances",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.entities.id"), nullable=False),
        sa.Column("counterparty_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.counterparties.id"), nullable=False),
        sa.Column("source_payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.payments.id"), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("original_amount", sa.Numeric(20,2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("direction in ('paid','received')", name="ck_advance_direction"),
        sa.CheckConstraint("status in ('open','closed','reversed')", name="ck_advance_status"),
        sa.CheckConstraint("original_amount > 0", name="ck_advance_amount_positive"),
        sa.UniqueConstraint("source_payment_id", name="uq_advance_source_payment"),
        schema="finance",
    )

    op.execute("""
    CREATE VIEW finance.obligation_balances AS
    SELECT
      o.id AS obligation_id,
      o.entity_id,
      o.counterparty_id,
      o.direction,
      o.currency,
      o.original_amount,
      COALESCE(SUM(pa.amount) FILTER (WHERE p.status='posted'), 0)::numeric(20,2) AS allocated_amount,
      (o.original_amount - COALESCE(SUM(pa.amount) FILTER (WHERE p.status='posted'), 0))::numeric(20,2) AS remaining_amount,
      CASE
        WHEN o.status='reversed' THEN 'reversed'
        WHEN o.status<>'posted' THEN o.status
        WHEN o.original_amount - COALESCE(SUM(pa.amount) FILTER (WHERE p.status='posted'), 0) = 0 THEN 'settled'
        WHEN COALESCE(SUM(pa.amount) FILTER (WHERE p.status='posted'), 0) > 0 THEN 'partial'
        ELSE 'open'
      END AS settlement_status
    FROM finance.obligations o
    LEFT JOIN finance.payment_allocations pa ON pa.obligation_id=o.id
    LEFT JOIN finance.payments p ON p.id=pa.payment_id
    GROUP BY o.id;

    CREATE VIEW finance.payment_balances AS
    SELECT
      p.id AS payment_id,
      p.amount,
      p.currency,
      COALESCE(SUM(pa.amount),0)::numeric(20,2) AS allocation_total,
      (p.amount - COALESCE(SUM(pa.amount),0))::numeric(20,2) AS unallocated_amount,
      p.status
    FROM finance.payments p
    LEFT JOIN finance.payment_allocations pa ON pa.payment_id=p.id
    GROUP BY p.id;

    CREATE VIEW finance.money_account_balances AS
    SELECT
      ma.id AS money_account_id,
      ma.entity_id,
      ma.currency,
      COALESCE(SUM(CASE WHEN pe.side='debit' THEN pe.amount ELSE -pe.amount END),0)::numeric(20,2) AS balance
    FROM finance.money_accounts ma
    LEFT JOIN finance.posting_entries pe ON pe.ledger_account_id=ma.ledger_account_id
    GROUP BY ma.id;
    """)

    # Posted financial business rows: business fields are frozen; only posted->reversed lifecycle metadata may change later.
    op.execute("""
    CREATE OR REPLACE FUNCTION finance.protect_posted_obligation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status IN ('posted','reversed') AND (
        NEW.entity_id IS DISTINCT FROM OLD.entity_id OR
        NEW.counterparty_id IS DISTINCT FROM OLD.counterparty_id OR
        NEW.direction IS DISTINCT FROM OLD.direction OR
        NEW.original_amount IS DISTINCT FROM OLD.original_amount OR
        NEW.currency IS DISTINCT FROM OLD.currency OR
        NEW.event_date IS DISTINCT FROM OLD.event_date OR
        NEW.due_date IS DISTINCT FROM OLD.due_date OR
        NEW.offset_ledger_account_id IS DISTINCT FROM OLD.offset_ledger_account_id OR
        NEW.posting_id IS DISTINCT FROM OLD.posting_id
      ) THEN
        RAISE EXCEPTION 'posted obligation business fields are immutable' USING ERRCODE='55000';
      END IF;
      RETURN NEW;
    END;
    $$;
    CREATE TRIGGER trg_obligation_protect_posted BEFORE UPDATE ON finance.obligations
      FOR EACH ROW EXECUTE FUNCTION finance.protect_posted_obligation();

    CREATE OR REPLACE FUNCTION finance.protect_payment_allocation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE v_status text;
    BEGIN
      SELECT status INTO v_status FROM finance.payments WHERE id=COALESCE(OLD.payment_id, NEW.payment_id);
      IF v_status IN ('posted','reversed') THEN
        RAISE EXCEPTION 'allocations of posted/reversed payment are immutable' USING ERRCODE='55000';
      END IF;
      RETURN COALESCE(NEW, OLD);
    END;
    $$;
    CREATE TRIGGER trg_payment_allocation_protect BEFORE UPDATE OR DELETE ON finance.payment_allocations
      FOR EACH ROW EXECUTE FUNCTION finance.protect_payment_allocation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_payment_allocation_protect ON finance.payment_allocations")
    op.execute("DROP FUNCTION IF EXISTS finance.protect_payment_allocation()")
    op.execute("DROP TRIGGER IF EXISTS trg_obligation_protect_posted ON finance.obligations")
    op.execute("DROP FUNCTION IF EXISTS finance.protect_posted_obligation()")
    op.execute("DROP VIEW IF EXISTS finance.money_account_balances")
    op.execute("DROP VIEW IF EXISTS finance.payment_balances")
    op.execute("DROP VIEW IF EXISTS finance.obligation_balances")
    op.drop_table("advances", schema="finance")
    op.drop_index("ix_payment_allocation_obligation", table_name="payment_allocations", schema="finance")
    op.drop_table("payment_allocations", schema="finance")
    op.drop_index("ix_payment_counterparty_status", table_name="payments", schema="finance")
    op.drop_table("payments", schema="finance")
    op.drop_index("ix_obligation_counterparty_status", table_name="obligations", schema="finance")
    op.drop_table("obligations", schema="finance")
