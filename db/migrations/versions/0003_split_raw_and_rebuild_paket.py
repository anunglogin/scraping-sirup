"""move raw jsonb to paket_raw and rebuild paket for faster reads

The original SIRUP row (``raw``, ~600 of ~900 bytes per row) made the hot
``paket`` heap 3.3 GB for 3.6M rows, so every heap visit (bitmap rechecks,
pagu-ordered scans, deep pages) read ~3x more pages than needed. The table is
rebuilt in one transaction (readers keep using the old table until commit),
physically ordered by (tahun_anggaran, pagu) so pagu-ordered scans touch
neighbouring pages, and indexes get an ``id`` tie-break matching the API's
ORDER BY. See ADR TD-06.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_COLUMNS = """
    id, tahun_anggaran, nama_paket, pagu, id_jenis_pengadaan, jenis_pengadaan,
    id_metode, metode, id_bulan, pemilihan, id_kldi, kldi, id_satker, satuan_kerja,
    ids_lokasi, lokasi, sumber_dana, is_pdn, is_umk, pds, id_referensi,
    is_active, first_seen_at, last_seen_at, updated_at
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE paket_raw (
            id  BIGINT PRIMARY KEY,
            raw JSONB  NOT NULL
        )
        """
    )
    op.execute("INSERT INTO paket_raw (id, raw) SELECT id, raw FROM paket ORDER BY id")

    op.execute(
        """
        CREATE TABLE paket_new (
            id                 BIGINT       NOT NULL,
            tahun_anggaran     SMALLINT     NOT NULL,
            nama_paket         TEXT         NOT NULL,
            pagu               BIGINT       NOT NULL,
            id_jenis_pengadaan SMALLINT,
            jenis_pengadaan    TEXT,
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
            is_active          BOOLEAN      NOT NULL DEFAULT true,
            first_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            last_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"INSERT INTO paket_new ({_COLUMNS}) SELECT {_COLUMNS} FROM paket "  # noqa: S608
        "ORDER BY tahun_anggaran, pagu, id"
    )
    op.execute("DROP TABLE paket")
    op.execute("ALTER TABLE paket_new RENAME TO paket")
    op.execute("ALTER TABLE paket ADD CONSTRAINT paket_pkey PRIMARY KEY (id)")
    op.execute("CREATE INDEX ix_paket_nama_trgm ON paket USING gin (nama_paket gin_trgm_ops)")
    op.execute("CREATE INDEX ix_paket_satker_trgm ON paket USING gin (satuan_kerja gin_trgm_ops)")
    op.execute("CREATE INDEX ix_paket_bulan ON paket (tahun_anggaran, id_bulan)")
    op.execute("CREATE INDEX ix_paket_pagu ON paket (tahun_anggaran, pagu, id)")
    op.execute("CREATE INDEX ix_paket_first_seen ON paket (first_seen_at DESC, id DESC)")
    op.execute("ANALYZE paket")
    op.execute("ANALYZE paket_raw")


def downgrade() -> None:
    op.execute("ALTER TABLE paket ADD COLUMN raw JSONB")
    op.execute("UPDATE paket p SET raw = r.raw FROM paket_raw r WHERE r.id = p.id")
    op.execute("UPDATE paket SET raw = jsonb_build_object('id', id) WHERE raw IS NULL")
    op.execute("ALTER TABLE paket ALTER COLUMN raw SET NOT NULL")
    op.execute("DROP TABLE paket_raw")
    op.execute("DROP INDEX ix_paket_pagu")
    op.execute("CREATE INDEX ix_paket_pagu ON paket (tahun_anggaran, pagu)")
    op.execute("DROP INDEX ix_paket_first_seen")
    op.execute("CREATE INDEX ix_paket_first_seen ON paket (first_seen_at DESC)")
