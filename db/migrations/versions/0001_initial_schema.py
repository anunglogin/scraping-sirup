"""initial schema: paket + scrape_runs

Revision ID: 0001
Revises:
Create Date: 2026-11-10
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE TABLE paket (
            id                 BIGINT PRIMARY KEY,
            tahun_anggaran     SMALLINT     NOT NULL,
            nama_paket         TEXT         NOT NULL,
            pagu               BIGINT       NOT NULL,
            id_jenis_pengadaan SMALLINT     NOT NULL,
            jenis_pengadaan    TEXT         NOT NULL,
            id_metode          SMALLINT,
            metode             TEXT,
            id_bulan           SMALLINT,
            pemilihan          TEXT,
            id_kldi            TEXT,
            kldi               TEXT,
            id_satker          BIGINT,
            satuan_kerja       TEXT,
            ids_lokasi         TEXT,
            lokasi             TEXT,
            sumber_dana        TEXT,
            is_pdn             BOOLEAN,
            is_umk             BOOLEAN,
            pds                BOOLEAN,
            id_referensi       INTEGER,
            raw                JSONB        NOT NULL,
            is_active          BOOLEAN      NOT NULL DEFAULT true,
            first_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            last_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_paket_nama_trgm ON paket USING gin (nama_paket gin_trgm_ops)")
    op.execute("CREATE INDEX ix_paket_satker_trgm ON paket USING gin (satuan_kerja gin_trgm_ops)")
    op.execute("CREATE INDEX ix_paket_bulan ON paket (tahun_anggaran, id_bulan)")
    op.execute("CREATE INDEX ix_paket_pagu ON paket (tahun_anggaran, pagu)")
    op.execute("CREATE INDEX ix_paket_first_seen ON paket (first_seen_at DESC)")
    op.execute(
        """
        CREATE TABLE scrape_runs (
            id               BIGSERIAL PRIMARY KEY,
            tahun_anggaran   SMALLINT    NOT NULL,
            status           TEXT        NOT NULL
                             CHECK (status IN ('running', 'success', 'failed')),
            started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at      TIMESTAMPTZ,
            records_total    INTEGER,
            rows_received    INTEGER     NOT NULL DEFAULT 0,
            rows_inserted    INTEGER     NOT NULL DEFAULT 0,
            rows_updated     INTEGER     NOT NULL DEFAULT 0,
            rows_invalid     INTEGER     NOT NULL DEFAULT 0,
            rows_deactivated INTEGER     NOT NULL DEFAULT 0,
            checkpoint_start INTEGER     NOT NULL DEFAULT 0,
            error            TEXT,
            note             TEXT
        )
        """
    )
    op.execute("CREATE INDEX ix_scrape_runs_year ON scrape_runs (tahun_anggaran, id DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scrape_runs")
    op.execute("DROP TABLE IF EXISTS paket")
    # pg_trgm is left installed on purpose: other objects may depend on it and
    # keeping an extension is harmless.
