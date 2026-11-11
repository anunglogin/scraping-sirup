"""CLI for the data.inaproc.id collector.

``python -m sirup.inaproc``              collect pages (files, plus DB when configured)
``python -m sirup.inaproc load [FILE]``  upsert an existing JSONL file into the DB

Exit codes: 0 ok, 1 error, 2 refused by the site.
"""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

from sirup.inaproc.client import InaprocClient, InaprocRefusedError, epoch_millis
from sirup.inaproc.config import InaprocSettings
from sirup.inaproc.runner import run_collect
from sirup.inaproc.store import InaprocStore, load_file

log = logging.getLogger("sirup.inaproc")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    settings = InaprocSettings()
    db_url = settings.database_url.get_secret_value() if settings.database_url else None
    if len(sys.argv) > 1 and sys.argv[1] == "load":
        return load(settings, db_url, sys.argv[2] if len(sys.argv) > 2 else None)
    fixed_time = settings.request_time
    client = InaprocClient(
        table_url=settings.table_url,
        cursor_param=settings.cursor_param,
        time_header=settings.time_header,
        user_agent=settings.user_agent,
        timeout_seconds=settings.timeout_seconds,
        retry_attempts=settings.retry_attempts,
        x_secret=settings.x_secret.get_secret_value() if settings.x_secret else None,
        clock=(lambda: fixed_time) if fixed_time else epoch_millis,
    )
    try:
        with client:
            if db_url:
                with InaprocStore(db_url) as store:
                    result = run_collect(settings, client, store=store)
            else:
                result = run_collect(settings, client)
    except InaprocRefusedError as exc:
        log.error("%s. Stopping; see README 'data.inaproc.id'.", exc)
        return 2
    except Exception:
        log.exception("inaproc collect failed")
        return 1
    log.info("done: %s", result)
    return 0


def load(settings: InaprocSettings, db_url: str | None, file_arg: str | None) -> int:
    if not db_url:
        log.error("INAPROC_DATABASE_URL is not set; nothing to load into")
        return 1
    default = settings.output_dir / f"rup_{settings.resolved_year()}.jsonl"
    path = Path(file_arg) if file_arg else default
    if not path.is_file():
        log.error("file not found: %s", path)
        return 1
    try:
        with InaprocStore(db_url) as store:
            written = load_file(store, path)
            total = store.count()
    except Exception:
        log.exception("inaproc load failed")
        return 1
    log.info("loaded %d rows from %s; inaproc_rup now has %d rows", written, path.name, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
