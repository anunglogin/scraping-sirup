"""allow packages without a procurement type (jenis pengadaan)

SIRUP publishes some genuine packages without ``jenisPengadaan`` (e.g. rows
with ``idJenisPengadaan`` 6 and no name). See ADR TD-06.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE paket ALTER COLUMN id_jenis_pengadaan DROP NOT NULL")
    op.execute("ALTER TABLE paket ALTER COLUMN jenis_pengadaan DROP NOT NULL")


def downgrade() -> None:
    # Restoring NOT NULL is only possible when no such rows exist; refuse
    # with a clear message instead of deleting real data.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM paket
                       WHERE id_jenis_pengadaan IS NULL OR jenis_pengadaan IS NULL) THEN
                RAISE EXCEPTION 'cannot downgrade 0002: paket has rows without jenis '
                    'pengadaan; delete or fill them first';
            END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE paket ALTER COLUMN id_jenis_pengadaan SET NOT NULL")
    op.execute("ALTER TABLE paket ALTER COLUMN jenis_pengadaan SET NOT NULL")
