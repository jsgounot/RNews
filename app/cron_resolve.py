"""
Cron job: resolve display_url for items that have none.

Follows HTTP redirects for every item where display_url IS NULL.
After a successful request (any HTTP status), display_url is set to the
final URL after redirects — which may equal the original URL if there is
no redirect, which is fine: it marks the item as resolved and stops retries.
Items that fail due to network errors / timeouts keep display_url=NULL and
will be retried on the next run.

Run via:
    python -m app.cron_resolve

Expected env vars:
    DATABASE_URL — same as the web app (Railway injects this automatically)
"""

import asyncio
import logging
from typing import Optional

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BATCH_SIZE = 200
CONCURRENCY = 20
TIMEOUT = 10.0
USER_AGENT = "Mozilla/5.0 (RNews resolver)"


async def _resolve(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str
) -> Optional[str]:
    async with sem:
        try:
            resp = await client.head(
                url, headers={"User-Agent": USER_AGENT}
            )
            return str(resp.url)
        except Exception:
            return None


async def _run(db) -> dict:
    from app.models import Item

    items = (
        db.query(Item)
        .filter(Item.display_url.is_(None))
        .limit(BATCH_SIZE)
        .all()
    )

    if not items:
        log.info("No items with missing display_url — nothing to do.")
        return {"total": 0, "redirected": 0, "same": 0, "failed": 0}

    log.info("Resolving %d items (concurrency=%d)…", len(items), CONCURRENCY)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            *[_resolve(client, sem, item.url) for item in items]
        )

    redirected = same = failed = 0
    for item, resolved in zip(items, results):
        if resolved is None:
            failed += 1
        else:
            item.display_url = resolved
            if resolved != item.url:
                redirected += 1
            else:
                same += 1

    db.commit()
    log.info(
        "Done. Redirected: %d  Same URL: %d  Failed (will retry): %d",
        redirected,
        same,
        failed,
    )
    return {"total": len(items), "redirected": redirected, "same": same, "failed": failed}


def run_cron():
    from app.database import init_db, SessionLocal

    init_db()
    db = SessionLocal()
    try:
        log.info("=== cron_resolve starting ===")
        asyncio.run(_run(db))
        log.info("=== cron_resolve finished ===")
    except Exception:
        log.exception("cron_resolve failed")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_cron()
