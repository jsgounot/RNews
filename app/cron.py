"""
Combined cron entry point — runs all periodic jobs in a single Railway cron service.

Jobs run in order; each is wrapped independently so a failure in one does not
prevent the others from executing. Exit code is non-zero if any job failed.

Railway cron command:
    python -m app.cron
"""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _run_job(name: str, fn) -> bool:
    """Call fn(), return True on success, log and return False on exception."""
    log.info("=== %s starting ===", name)
    try:
        fn()
        log.info("=== %s finished ===", name)
        return True
    except Exception:
        log.exception("=== %s FAILED ===", name)
        return False


def main() -> None:
    from app.cron_score import run_cron as score
    from app.cron_resolve import run_cron as resolve

    results = [
        _run_job("cron_score", score),
        _run_job("cron_resolve", resolve),
    ]

    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
