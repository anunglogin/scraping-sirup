"""FastAPI application: read-only filter API over the collected SIRUP packages.

Run with: ``uvicorn sirup.api.main:create_app --factory``
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import AfterValidator, StringConstraints

from sirup import __version__
from sirup.api.config import ApiSettings
from sirup.api.queries import (
    DETAIL_QUERY,
    RUNS_QUERY,
    PaketFilter,
    SortKey,
    Sumber,
    count_query,
    list_query,
)
from sirup.api.schemas import ErrorMessage, Health, Paket, PaketDetail, PaketPage, ScrapeRun
from sirup.timeutil import current_year

log = logging.getLogger("sirup.api")

MAX_RESULT_WINDOW = 10_000
MAX_PEMILIHAN_VALUES = 24

DESCRIPTION = """
API internal untuk **mencari paket pengadaan pemerintah** yang dikumpulkan setiap malam
dari [SIRUP](https://sirup.inaproc.id).

### Cara memakai
1. Minta **kunci API** ke admin.
2. Klik tombol **Authorize** (gembok) di kanan atas, tempel kuncinya, lalu klik *Authorize*.
3. Buka endpoint `GET /v1/paket`, klik **Try it out**, isi filter, lalu **Execute**.

### Filter utama (bisa dipakai sendiri atau digabung)
| Filter | Arti |
|---|---|
| `paket` | Nama paket mengandung kata ini |
| `satuan_kerja` | Nama satuan kerja mengandung kata ini |
| `pemilihan` | Bulan + tahun pemilihan, format `YYYY-MM` (mis. `2026-03`), boleh diulang |
| `pagu_min` / `pagu_max` | Rentang nilai pagu dalam rupiah |
| `sumber` | Hanya paket dari sumber ini: `sirup` atau `inaproc` |

Semua kata kunci **tidak peka huruf besar/kecil** dan harus memuat minimal **3 huruf/angka
berurutan** (mis. `laptop`, `100%`). Tanda `%` dan `_` dicari apa adanya.

### Kode error
- **401** kunci API tidak ada atau salah
- **404** paket tidak ditemukan
- **422** isian filter tidak valid (pesannya menjelaskan kolom mana)
- **429** terlalu banyak pencarian bersamaan; coba lagi sesuai header `Retry-After`
- **503** database sedang tidak bisa dihubungi
"""

TAGS = [
    {"name": "Paket", "description": "Cari dan lihat paket pengadaan dari SIRUP dan INAPROC."},
    {"name": "Riwayat pengambilan", "description": "Catatan setiap kali data diambil dari SIRUP."},
    {"name": "Sistem", "description": "Cek kesehatan layanan (tanpa kunci API)."},
]

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    scheme_name="KunciAPI",
    description="Kunci API dari admin. Kirim di header `X-API-Key`.",
)

ERR_401 = {"model": ErrorMessage, "description": "Kunci API tidak ada atau salah."}
ERR_404 = {"model": ErrorMessage, "description": "Paket tidak ditemukan."}
ERR_422 = {"description": "Isian tidak valid. `detail` berisi daftar kolom yang salah."}
ERR_429 = {
    "model": ErrorMessage,
    "description": "Terlalu banyak pencarian bersamaan. Coba lagi setelah beberapa detik "
    "(lihat header `Retry-After`).",
}
ERR_503 = {
    "model": ErrorMessage,
    "description": "Database sedang tidak bisa dihubungi, sedang sibuk, "
    "atau pencarian terlalu lama.",
}

RETRY_AFTER_SECONDS = 2
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# pg_trgm can only use its index when the keyword has >= 3 consecutive letters
# or digits; without that every row is a candidate and the query times out.
_TRIGRAM_RUN = re.compile(r"[^\W_]{3}")


def _check_search_text(value: str) -> str:
    if _CONTROL_CHARS.search(value):
        raise ValueError("Kata kunci tidak boleh berisi karakter kontrol.")
    if not _TRIGRAM_RUN.search(value):
        raise ValueError(
            "Kata kunci harus mengandung minimal 3 huruf/angka berurutan, mis. 'laptop'."
        )
    return value


_YEAR_MONTH = re.compile(r"[0-9]{4}-[0-9]{2}")


def _check_year_month(value: str) -> str:
    if not _YEAR_MONTH.fullmatch(value):
        raise ValueError("Format harus YYYY-MM, mis. 2026-03.")
    year, month = int(value[:4]), int(value[5:])
    if not 1 <= month <= 12:
        raise ValueError("Bulan harus 01 sampai 12, format YYYY-MM, mis. 2026-03.")
    if not 2000 <= year <= 2100:
        raise ValueError("Tahun harus 2000 sampai 2100, format YYYY-MM, mis. 2026-03.")
    return value


YearMonth = Annotated[
    str,
    StringConstraints(strip_whitespace=True),
    AfterValidator(_check_year_month),
]

SearchText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=100),
    AfterValidator(_check_search_text),
]


def require_api_key(
    request: Request, key: Annotated[str | None, Security(api_key_header)] = None
) -> None:
    keys: list[bytes] = request.app.state.api_keys
    ok = False
    if key:
        given = key.encode("utf-8")
        # Check every key without short-circuiting to keep timing uniform.
        for candidate in keys:
            ok |= secrets.compare_digest(candidate, given)
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Kunci API tidak ada atau salah. Kirim header X-API-Key.",
            headers={"WWW-Authenticate": "APIKey"},
        )


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]


def limit_search(request: Request) -> Iterator[None]:
    """Cap concurrent searches below the pool size (review F-01).

    Without it, a handful of slow searches holds every pool connection and even
    /health fails. Extra searches wait briefly, then get 429 + Retry-After.
    """
    slots: threading.BoundedSemaphore = request.app.state.search_slots
    if not slots.acquire(timeout=request.app.state.search_queue_wait):
        raise HTTPException(
            status_code=429,
            detail="Terlalu banyak pencarian bersamaan. Coba lagi beberapa detik lagi.",
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
    try:
        yield
    finally:
        slots.release()


def _validation_error(field: str, message: str, value: Any) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("query", field), "msg": message, "input": value}]
    )


router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(require_api_key)],
    responses={401: ERR_401, 503: ERR_503},
)


@router.get(
    "/paket",
    tags=["Paket"],
    summary="Cari paket",
    description="Cari paket dengan filter. Semua filter opsional dan digabung dengan logika "
    "**DAN**. Hasil dibagi per halaman; maksimal 10.000 hasil pertama bisa dibuka "
    "(page × size ≤ 10.000) — persempit filter bila perlu lebih.",
    response_model=PaketPage,
    responses={422: ERR_422, 429: ERR_429},
    dependencies=[Depends(limit_search)],
)
def search_paket(
    conn: Conn,
    paket: Annotated[
        SearchText | None,
        Query(
            description="Kata kunci **nama paket** (3–100 karakter, minimal 3 huruf/angka "
            "berurutan). Cocok bila nama paket "
            "mengandung kata ini, tidak peka huruf besar/kecil.",
            examples=["laptop"],
        ),
    ] = None,
    satuan_kerja: Annotated[
        SearchText | None,
        Query(
            description="Kata kunci **nama satuan kerja** (3–100 karakter, minimal 3 huruf/angka "
            "berurutan), tidak peka huruf "
            "besar/kecil.",
            examples=["puskesmas"],
        ),
    ] = None,
    pemilihan: Annotated[
        list[YearMonth] | None,
        Query(
            description="Bulan dan tahun **pemilihan penyedia**, format `YYYY-MM` "
            "(mis. `2026-03` = Maret 2026). Boleh diulang, maksimal 24: "
            "`pemilihan=2026-03&pemilihan=2026-04`. Catatan: tahun anggaran 2026 juga "
            "berisi paket dengan pemilihan Desember 2025.",
            examples=[["2026-03", "2026-04"]],
        ),
    ] = None,
    pagu_min: Annotated[
        int | None,
        Query(ge=0, description="Pagu **minimal** dalam rupiah.", examples=[100_000_000]),
    ] = None,
    pagu_max: Annotated[
        int | None,
        Query(
            ge=0,
            description="Pagu **maksimal** dalam rupiah. Harus ≥ `pagu_min`.",
            examples=[1_000_000_000],
        ),
    ] = None,
    sumber: Annotated[
        Sumber | None,
        Query(
            description="Hanya paket yang dicantumkan sumber ini: `sirup` atau `inaproc`. "
            "Kosongkan untuk semua sumber. Paket yang ada di keduanya ikut di dua-duanya. "
            "Catatan: paket yang hanya ada di INAPROC tidak punya bulan pemilihan, "
            "jadi tidak ikut bila filter `pemilihan` dipakai.",
            examples=["inaproc"],
        ),
    ] = None,
    tahun: Annotated[
        int | None,
        Query(
            ge=2000,
            le=2100,
            description="Tahun anggaran. Kosongkan untuk tahun berjalan (WIB).",
            examples=[2026],
        ),
    ] = None,
    aktif: Annotated[
        bool,
        Query(
            description="`true` (bawaan) = sembunyikan paket yang sudah hilang dari SIRUP. "
            "`false` = tampilkan semua, termasuk yang hilang.",
            examples=[True],
        ),
    ] = True,
    sort: Annotated[
        SortKey,
        Query(
            description="Urutan: `pagu_desc` pagu terbesar dulu (bawaan), `pagu_asc` pagu "
            "terkecil dulu, `terbaru` paket yang paling baru kami temukan dulu.",
            examples=["pagu_desc"],
        ),
    ] = "pagu_desc",
    page: Annotated[int, Query(ge=1, description="Nomor halaman, mulai dari 1.", examples=[1])] = 1,
    size: Annotated[
        int,
        Query(ge=1, le=100, description="Jumlah paket per halaman (1–100).", examples=[20]),
    ] = 20,
) -> PaketPage:
    if pagu_min is not None and pagu_max is not None and pagu_min > pagu_max:
        raise _validation_error(
            "pagu_min", "pagu_min tidak boleh lebih besar dari pagu_max", pagu_min
        )
    if pemilihan and len(pemilihan) > MAX_PEMILIHAN_VALUES:
        raise _validation_error(
            "pemilihan", f"Maksimal {MAX_PEMILIHAN_VALUES} nilai pemilihan.", len(pemilihan)
        )
    if page * size > MAX_RESULT_WINDOW:
        raise _validation_error(
            "page",
            f"Hanya {MAX_RESULT_WINDOW} hasil pertama yang bisa dibuka (page x size). "
            "Persempit filter agar hasilnya lebih sedikit.",
            page,
        )
    f = PaketFilter(
        tahun=tahun or current_year(),
        aktif=aktif,
        paket=paket or None,
        satuan_kerja=satuan_kerja or None,
        pemilihan=tuple(sorted({(int(v[:4]), int(v[5:])) for v in pemilihan or []})),
        pagu_min=pagu_min,
        pagu_max=pagu_max,
        sumber=sumber,
    )
    with conn.cursor(row_factory=dict_row) as cur:
        q, params = count_query(f, MAX_RESULT_WINDOW + 1)
        cur.execute(q, params)
        counted = cur.fetchone()["count"]  # type: ignore[index]
        capped = counted > MAX_RESULT_WINDOW
        total = MAX_RESULT_WINDOW if capped else counted
        rows: list[dict[str, Any]] = []
        if total and (page - 1) * size < total:
            q, params = list_query(f, sort, size, (page - 1) * size)
            cur.execute(q, params)
            rows = cur.fetchall()
    return PaketPage(
        total=total,
        total_terbatas=capped,
        page=page,
        size=size,
        data=[Paket(**r) for r in rows],
    )


@router.get(
    "/paket/{id}",
    tags=["Paket"],
    summary="Detail satu paket",
    description="Ambil satu paket berdasarkan ID SIRUP, termasuk data aslinya (`raw`).",
    response_model=PaketDetail,
    responses={404: ERR_404, 422: ERR_422},
)
def get_paket(
    conn: Conn,
    id: Annotated[
        int,
        Path(
            ge=1, le=9_223_372_036_854_775_807, description="ID paket SIRUP.", examples=[60522827]
        ),
    ],
) -> PaketDetail:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(DETAIL_QUERY, (id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")
    return PaketDetail(**row)


@router.get(
    "/scrape-runs",
    tags=["Riwayat pengambilan"],
    summary="Riwayat pengambilan data",
    description="Daftar run pengambilan data terbaru: waktu mulai/selesai, jumlah paket, "
    "dan error (bila ada).",
    response_model=list[ScrapeRun],
    responses={422: ERR_422},
)
def list_scrape_runs(
    conn: Conn,
    limit: Annotated[
        int,
        Query(
            ge=1, le=100, description="Jumlah run terbaru yang ditampilkan (1–100).", examples=[20]
        ),
    ] = 20,
) -> list[ScrapeRun]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(RUNS_QUERY, (limit,))
        return [ScrapeRun(**r) for r in cur.fetchall()]


def health(request: Request) -> JSONResponse:
    pool: ConnectionPool = request.app.state.pool
    try:
        with pool.connection(timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception:
        log.warning("health check: database unreachable", exc_info=True)
        return JSONResponse(status_code=503, content={"detail": "Database tidak bisa dihubungi."})
    return JSONResponse(content={"status": "ok", "database": "ok"})


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()  # type: ignore[call-arg]

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        pool = ConnectionPool(
            settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            open=False,
            timeout=5,
            kwargs={
                "autocommit": True,
                "application_name": "sirup-api",
                "options": "-c default_transaction_read_only=on "
                f"-c statement_timeout={settings.db_statement_timeout_ms}",
            },
        )
        # Non-blocking: the API starts even if the DB is still booting;
        # /health reports 503 until it is reachable.
        pool.open(wait=False)
        app.state.pool = pool
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(
        title="SIRUP — API Pencarian Paket",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    app.state.api_keys = [k.encode("utf-8") for k in settings.key_list()]
    app.state.search_slots = threading.BoundedSemaphore(settings.search_slots())
    app.state.search_queue_wait = settings.search_queue_wait_seconds
    app.include_router(router)
    app.add_api_route(
        "/health",
        health,
        methods=["GET"],
        tags=["Sistem"],
        summary="Cek kesehatan layanan",
        description="Mengecek koneksi ke database. Tidak memerlukan kunci API.",
        response_model=Health,
        responses={503: ERR_503},
    )

    @app.exception_handler(PoolTimeout)
    async def _pool_busy(_: Request, exc: Exception) -> JSONResponse:
        # The database is fine; every pooled connection is just in use.
        log.warning("connection pool exhausted")
        return JSONResponse(
            status_code=503,
            content={"detail": "Server sedang sibuk. Coba lagi beberapa detik lagi."},
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    @app.exception_handler(psycopg.OperationalError)
    async def _db_unavailable(_: Request, exc: Exception) -> JSONResponse:
        log.error("database unavailable: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503, content={"detail": "Database sedang tidak bisa dihubungi."}
        )

    @app.exception_handler(psycopg.errors.QueryCanceled)
    async def _db_timeout(_: Request, exc: Exception) -> JSONResponse:
        log.warning("query cancelled (statement_timeout)")
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            content={"detail": "Pencarian terlalu lama. Persempit filter lalu coba lagi."},
        )

    @app.exception_handler(psycopg.Error)
    async def _db_error(_: Request, exc: Exception) -> JSONResponse:
        log.exception("database error", exc_info=exc)
        return JSONResponse(status_code=503, content={"detail": "Terjadi gangguan database."})

    return app
