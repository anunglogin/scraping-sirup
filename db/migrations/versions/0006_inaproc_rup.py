"""add inaproc_rup for rows collected from data.inaproc.id

Separate from paket on purpose: paket is filled by the nightly SIRUP scraper
and serves the API; inaproc_rup is filled by the optional, manual inaproc
collector. Keyed by kode_rup so re-collecting a row updates it instead of
duplicating it. The full source row is kept in raw. See ADR TD-09.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE inaproc_rup (
            kode_rup             BIGINT PRIMARY KEY,
            tahun_anggaran       SMALLINT NOT NULL,
            nama_paket           TEXT NOT NULL DEFAULT '',
            nama_instansi        TEXT NOT NULL DEFAULT '',
            nama_satker          TEXT NOT NULL DEFAULT '',
            total_nilai          NUMERIC(20, 2),
            cara_pengadaan       TEXT,
            cara_pengadaan_label TEXT,
            jenis_pengadaan      TEXT,
            metode_pengadaan     TEXT,
            sumber_dana          TEXT,
            produk_dalam_negeri  TEXT,
            status_umk           TEXT,
            raw                  JSONB NOT NULL,
            first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_inaproc_rup_tahun ON inaproc_rup (tahun_anggaran)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inaproc_rup")
