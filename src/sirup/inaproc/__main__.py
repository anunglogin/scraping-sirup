"""CLI: ``python -m sirup.inaproc``. Exit codes: 0 ok, 1 error, 2 refused by the site."""

from __future__ import annotations

import logging
import signal
import sys

from sirup.inaproc.client import InaprocClient, InaprocRefusedError, epoch_millis
from sirup.inaproc.config import InaprocSettings
from sirup.inaproc.runner import run_collect

log = logging.getLogger("sirup.inaproc")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    settings = InaprocSettings()
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
            result = run_collect(settings, client)
    except InaprocRefusedError as exc:
        log.error("%s. Stopping; see README 'data.inaproc.id'.", exc)
        return 2
    except Exception:
        log.exception("inaproc collect failed")
        return 1
    log.info("done: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
