"""add pemilihan_tahun (year of the procurement month) for the month+year filter

The owner decided (11 Nov 2026) that the API filters pemilihan by month AND
year: tahun anggaran 2026 contains e.g. "December 2025" (72k) and
"December 2026" (6k) packages. SIRUP only gives the year inside the text
("April 2026"), so it is parsed into a STORED generated column: the scraper
cannot drift from the backfill, and adding the column rewrites the table
compactly in one statement. NULL when the text is not "<English month> YYYY".
See ADR TD-07.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_MONTH_YEAR = (
    r"^\s*(january|february|march|april|may|june|july|august|september|october"
    r"|november|december)\s+[0-9]{4}\s*$"
)


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE paket ADD COLUMN pemilihan_tahun SMALLINT GENERATED ALWAYS AS (
            CASE WHEN pemilihan ~* '{_MONTH_YEAR}'
                 THEN substring(pemilihan from '([0-9]{{4}})\\s*$')::smallint
            END
        ) STORED
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_paket_bulan")
    op.execute(
        "CREATE INDEX ix_paket_pemilihan ON paket (tahun_anggaran, pemilihan_tahun, id_bulan)"
    )
    op.execute("ANALYZE paket")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_paket_pemilihan")
    op.execute("ALTER TABLE paket DROP COLUMN pemilihan_tahun")
    op.execute("CREATE INDEX ix_paket_bulan ON paket (tahun_anggaran, id_bulan)")
