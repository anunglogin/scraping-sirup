"""add paket.sumber: which sources list the package (sirup, inaproc)

The owner decided (11 Nov 2026) that /paket also serves packages found on
data.inaproc.id, with one row per kode RUP and a list of sources. Existing rows
all came from SIRUP. SIRUP stays the source of truth for the row's content;
INAPROC only adds its tag, or the whole row when SIRUP does not list it.
See ADR TD-10.

The constant default makes ADD COLUMN a catalog-only change (no table rewrite).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE paket ADD COLUMN sumber TEXT[] NOT NULL DEFAULT '{sirup}'")
    op.execute(
        "ALTER TABLE paket ADD CONSTRAINT ck_paket_sumber CHECK ("
        "cardinality(sumber) >= 1 AND sumber <@ ARRAY['sirup', 'inaproc']::text[])"
    )
    op.execute("CREATE INDEX ix_paket_sumber ON paket USING gin (sumber)")


def downgrade() -> None:
    # Rows known only from INAPROC have no SIRUP origin; drop them with the column.
    op.execute("DELETE FROM paket WHERE NOT ('sirup' = ANY(sumber))")
    op.execute("DROP INDEX IF EXISTS ix_paket_sumber")
    op.execute("ALTER TABLE paket DROP CONSTRAINT IF EXISTS ck_paket_sumber")
    op.execute("ALTER TABLE paket DROP COLUMN sumber")
