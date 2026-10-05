"""Conservative binary protection includes synthetic manual Inbox identities.

If historical manual duplicates exist, unique-index creation fails atomically;
no original, settlement or financial history is deleted or repaired.
"""
from alembic import op
revision='20261005_0015'
down_revision='20261005_0014'
branch_labels=depends_on=None

def upgrade():
    op.execute("""
    DROP INDEX harvest.evidence_binary_sha;
    CREATE UNIQUE INDEX evidence_binary_sha ON harvest.evidence(entity_id,fingerprint)
      WHERE file_bytes IS NOT NULL AND (source_reference IS NULL OR source IN ('owner_mobile:manual','inbox:manual'));
    CREATE INDEX capture_content_review ON core.capture_drafts(entity_id,fingerprint);
    """)

def downgrade():
    raise RuntimeError('Forward-only: verified backup and preserved evidence/journal reconciliation required')
