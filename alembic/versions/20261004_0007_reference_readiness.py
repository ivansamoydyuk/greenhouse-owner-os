"""Reference provenance and immutable environment separation.

Forward-only recovery: restore a verified backup separately and reconcile the
subsequent immutable journal and reference evidence; never remove guards live.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '20261004_0007'
down_revision = '20261004_0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('entities', sa.Column('reference_class', sa.String(20), nullable=False,
                  server_default='OWNER_VERIFIED'), schema='core')
    op.create_check_constraint('ck_entity_reference_class', 'entities',
        "reference_class IN ('OWNER_VERIFIED','TEST_PREVIEW')", schema='core')
    op.create_table('reference_records',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('entity_id', UUID(as_uuid=True), sa.ForeignKey('core.entities.id'), nullable=False),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('canonical_key', sa.Text(), nullable=False),
        sa.Column('reference_class', sa.String(20), nullable=False),
        sa.Column('original_name', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('definition', JSONB(), nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('core.actors.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('entity_id','kind','canonical_key', name='uq_reference_identity'),
        sa.CheckConstraint("kind IN ('entity','money_account','counterparty','ledger_account')", name='ck_reference_kind'),
        sa.CheckConstraint("reference_class IN ('SYSTEM','OWNER_VERIFIED','TEST_PREVIEW')", name='ck_reference_class'),
        sa.CheckConstraint("btrim(original_name) <> '' AND btrim(source) <> '' AND btrim(canonical_key) <> ''", name='ck_reference_text'),
        schema='core')
    op.execute("""
    CREATE FUNCTION core.guard_reference_class() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE classification text; found_id uuid;
    BEGIN
      IF TG_TABLE_NAME = 'entities' THEN
        IF NEW.reference_class='TEST_PREVIEW' AND current_database()<>'greenhouse_owner_os_test' THEN
          RAISE EXCEPTION 'preview references require disposable test database' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND NEW.reference_class IS DISTINCT FROM OLD.reference_class THEN
          RAISE EXCEPTION 'entity reference class is immutable' USING ERRCODE='23514';
        END IF;
      ELSE
        SELECT reference_class INTO classification FROM core.entities WHERE id=NEW.entity_id;
        IF NEW.reference_class <> 'SYSTEM' AND NEW.reference_class IS DISTINCT FROM classification THEN
          RAISE EXCEPTION 'reference environment mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.reference_class='SYSTEM' AND NEW.kind <> 'ledger_account' THEN
          RAISE EXCEPTION 'system references are technical ledger mappings only' USING ERRCODE='23514';
        END IF;
        CASE NEW.kind
          WHEN 'entity' THEN SELECT id INTO found_id FROM core.entities WHERE id=NEW.id AND id=NEW.entity_id;
          WHEN 'counterparty' THEN SELECT id INTO found_id FROM core.counterparties WHERE id=NEW.id AND entity_id=NEW.entity_id;
          WHEN 'money_account' THEN SELECT id INTO found_id FROM finance.money_accounts WHERE id=NEW.id AND entity_id=NEW.entity_id;
          WHEN 'ledger_account' THEN SELECT id INTO found_id FROM finance.ledger_accounts WHERE id=NEW.id AND entity_id=NEW.entity_id;
        END CASE;
        IF found_id IS NULL THEN
          RAISE EXCEPTION 'orphan reference record' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER entity_reference_class_guard BEFORE INSERT OR UPDATE ON core.entities
      FOR EACH ROW EXECUTE FUNCTION core.guard_reference_class();
    CREATE TRIGGER reference_scope_guard BEFORE INSERT ON core.reference_records
      FOR EACH ROW EXECUTE FUNCTION core.guard_reference_class();
    CREATE TRIGGER immutable_reference_evidence BEFORE UPDATE OR DELETE ON core.reference_records
      FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
    """)


def downgrade():
    raise RuntimeError('Forward-only recovery: separate backup restore and reference/journal reconciliation required')
