"""CLI: ``python -m sirup.scraper once|schedule``."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from sirup.scraper.config import ScraperSettings
from sirup.scraper.runner import make_client, run_scrape
from sirup.scraper.store import ScrapeAlreadyRunningError
from sirup.timeutil import next_run_at, now_jakarta, parse_hhmm, seconds_until

log = logging.getLogger("sirup.scraper")


def _once(settings: ScraperSettings) -> int:
    try:
        with make_client(settings) as client:
            result = run_scrape(settings, client)
    except ScrapeAlreadyRunningError as exc:
        log.error("%s", exc)
        return 2
    except Exception:
        # Log the cause here too: failures before the run row exists (DB down,
        # lock/connect errors) would otherwise vanish (review F-10).
        log.exception("scrape run failed")
        return 1
    log.info("done: %s", result)
    return 0


def _schedule(settings: ScraperSettings) -> int:
    at = parse_hhmm(settings.scrape_time)
    while True:
        wait = seconds_until(now_jakarta(), at)
        log.info("next run at %s (WIB)", next_run_at(now_jakarta(), at).isoformat())
        time.sleep(wait)
        for attempt in range(settings.schedule_retry_count + 1):
            code = _once(settings)
            if code != 1:
                break
            if attempt < settings.schedule_retry_count:
                log.warning(
                    "run failed; retrying (resume) in %.0f minutes",
                    settings.schedule_retry_wait_minutes,
                )
                time.sleep(settings.schedule_retry_wait_minutes * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sirup.scraper")
    parser.add_argument("mode", choices=["once", "schedule"])
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # `docker stop` sends SIGTERM: exit through SystemExit so connections close
    # cleanly. An interrupted run stays 'running' and is resumed next time.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    settings = ScraperSettings()  # type: ignore[call-arg]
    if args.mode == "once":
        return _once(settings)
    return _schedule(settings)


if __name__ == "__main__":
    sys.exit(main())
