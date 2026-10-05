"""Separate immutable source identity from content fingerprint.

Forward-only: restore a verified backup separately and reconcile subsequent
evidence/journals. Equal text with distinct locators cannot fit the old index.
"""
from alembic import op

revision = '20261005_0011'
down_revision = '20261005_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE harvest.evidence DROP CONSTRAINT evidence_entity_id_fingerprint_key')
    op.execute('CREATE UNIQUE INDEX evidence_stable_source ON harvest.evidence(entity_id,source,source_reference) WHERE source_reference IS NOT NULL')
    op.execute('CREATE UNIQUE INDEX evidence_binary_sha ON harvest.evidence(entity_id,fingerprint) WHERE file_bytes IS NOT NULL')
    op.execute('CREATE UNIQUE INDEX evidence_unlocated_text ON harvest.evidence(entity_id,fingerprint) WHERE file_bytes IS NULL AND source_reference IS NULL')


def downgrade():
    raise RuntimeError('Forward-only: verified backup and preserved evidence/journal reconciliation required')
