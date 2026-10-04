"""architecture foundation: core, audit, balanced postings

Revision ID: 20261004_0001
Revises:
Create Date: 2026-10-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261004_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    op.execute("CREATE SCHEMA IF NOT EXISTS finance")
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")

    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("default_currency", sa.String(3), nullable=False, server_default="TRY"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="core",
    )
    op.create_table(
        "actors",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("external_ref", sa.String(255)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="core",
    )
    op.create_table(
        "counterparties",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.entities.id"), nullable=False),
        sa.Column("name", sa.String(250), nullable=False),
        sa.Column("counterparty_type", sa.String(30), nullable=False, server_default="company"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="core",
    )
    op.create_table(
        "ledger_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.entities.id"), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("account_class", sa.String(40), nullable=False),
        sa.Column("normal_side", sa.String(6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("system_code", sa.String(80)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint("normal_side in ('debit','credit')", name="ck_ledger_account_normal_side"),
        sa.UniqueConstraint("entity_id", "currency", "system_code", name="uq_ledger_account_system_code"),
        schema="finance",
    )
    op.create_table(
        "money_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.entities.id"), nullable=False),
        sa.Column("ledger_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.ledger_accounts.id"), nullable=False),
        sa.Column("account_type", sa.String(10), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint("account_type in ('cash','bank')", name="ck_money_account_type"),
        sa.UniqueConstraint("ledger_account_id", name="uq_money_account_ledger"),
        schema="finance",
    )
    op.create_table(
        "postings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.entities.id"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("posting_type", sa.String(80), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reversal_of_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.postings.id")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.actors.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="finance",
    )
    op.create_index("uq_posting_reversal_once", "postings", ["reversal_of_id"], unique=True, schema="finance", postgresql_where=sa.text("reversal_of_id IS NOT NULL"))
    op.create_table(
        "posting_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("posting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.postings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ledger_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("finance.ledger_accounts.id"), nullable=False),
        sa.Column("side", sa.String(6), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("memo", sa.Text()),
        sa.CheckConstraint("side in ('debit','credit')", name="ck_posting_entry_side"),
        sa.CheckConstraint("amount > 0", name="ck_posting_entry_amount_positive"),
        schema="finance",
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.actors.id"), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("response_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("actor_id", "operation", "idempotency_key", name="uq_idempotency_actor_operation_key"),
        schema="audit",
    )
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core.actors.id"), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("reason", sa.Text()),
        sa.Column("before_json", postgresql.JSONB()),
        sa.Column("after_json", postgresql.JSONB()),
        schema="audit",
    )

    # Immutable posting and audit rows.
    op.execute("""
    CREATE OR REPLACE FUNCTION audit.reject_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'immutable table %.% does not allow %', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = '55000';
    END;
    $$;
    """)
    for schema, table in [("finance", "postings"), ("finance", "posting_entries"), ("audit", "events")]:
        op.execute(f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {schema}.{table} FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()")

    # Deferred database-level validation: every posting is >=2 lines, balanced, and all accounts match entity/currency.
    op.execute("""
    CREATE OR REPLACE FUNCTION finance.assert_posting_balanced() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
      v_posting_id uuid;
      v_entity_id uuid;
      v_currency text;
      v_count integer;
      v_debit numeric(20,2);
      v_credit numeric(20,2);
      v_scope_errors integer;
    BEGIN
      v_posting_id := COALESCE(NEW.posting_id, NEW.id, OLD.posting_id, OLD.id);
      SELECT entity_id, currency INTO v_entity_id, v_currency FROM finance.postings WHERE id = v_posting_id;
      IF v_entity_id IS NULL THEN RETURN NULL; END IF;
      SELECT count(*),
             COALESCE(sum(CASE WHEN e.side='debit' THEN e.amount ELSE 0 END),0),
             COALESCE(sum(CASE WHEN e.side='credit' THEN e.amount ELSE 0 END),0)
        INTO v_count, v_debit, v_credit
        FROM finance.posting_entries e WHERE e.posting_id = v_posting_id;
      SELECT count(*) INTO v_scope_errors
        FROM finance.posting_entries e
        JOIN finance.ledger_accounts a ON a.id=e.ledger_account_id
       WHERE e.posting_id=v_posting_id AND (a.entity_id<>v_entity_id OR a.currency<>v_currency);
      IF v_count < 2 OR v_debit <> v_credit OR v_scope_errors <> 0 THEN
        RAISE EXCEPTION 'invalid posting %, lines %, debit %, credit %, scope_errors %', v_posting_id, v_count, v_debit, v_credit, v_scope_errors
          USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END;
    $$;
    CREATE CONSTRAINT TRIGGER trg_posting_balance_header
      AFTER INSERT ON finance.postings DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION finance.assert_posting_balanced();
    CREATE CONSTRAINT TRIGGER trg_posting_balance_entry
      AFTER INSERT ON finance.posting_entries DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION finance.assert_posting_balanced();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_posting_balance_entry ON finance.posting_entries")
    op.execute("DROP TRIGGER IF EXISTS trg_posting_balance_header ON finance.postings")
    op.execute("DROP FUNCTION IF EXISTS finance.assert_posting_balanced()")
    op.drop_table("events", schema="audit")
    op.drop_table("idempotency_keys", schema="audit")
    op.drop_table("posting_entries", schema="finance")
    op.drop_index("uq_posting_reversal_once", table_name="postings", schema="finance")
    op.drop_table("postings", schema="finance")
    op.drop_table("money_accounts", schema="finance")
    op.drop_table("ledger_accounts", schema="finance")
    op.drop_table("counterparties", schema="core")
    op.drop_table("actors", schema="core")
    op.drop_table("entities", schema="core")
    op.execute("DROP FUNCTION IF EXISTS audit.reject_mutation()")
    op.execute("DROP SCHEMA IF EXISTS audit")
    op.execute("DROP SCHEMA IF EXISTS finance")
    op.execute("DROP SCHEMA IF EXISTS core")
