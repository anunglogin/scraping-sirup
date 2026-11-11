"""deactivate only after two consecutive complete runs miss a package

Adds paket.missing_runs (reset to 0 whenever the package is seen) and
scrape_runs.rows_missing. Already-inactive packages start at 2. See ADR TD-07
(review F-05).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Constant default: metadata-only in PostgreSQL 11+, no table rewrite.
    op.execute("ALTER TABLE paket ADD COLUMN missing_runs SMALLINT NOT NULL DEFAULT 0")
    op.execute("UPDATE paket SET missing_runs = 2 WHERE NOT is_active")
    op.execute("ALTER TABLE scrape_runs ADD COLUMN rows_missing INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE scrape_runs DROP COLUMN rows_missing")
    op.execute("ALTER TABLE paket DROP COLUMN missing_runs")
