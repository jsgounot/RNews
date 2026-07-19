"""
Cron: recompute hot scores and write them to items.computed_score.

Intended to run hourly on Railway. Only items created within the last
SCORE_WINDOW_DAYS days are processed; older items have negligible scores
(e^(-0.3 × 90) ≈ 0.000002) and are left at their last computed value.

Must not import from app.main (no FastAPI side effects — see CLAUDE.md).
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Item
from app.scoring import compute_item_score_for_item

# Items older than this are skipped — at λ=0.3, score at 30 days ≈ 0.00012 (effectively zero)
SCORE_WINDOW_DAYS = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def run_cron() -> None:
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=SCORE_WINDOW_DAYS)
        items = db.query(Item).filter(Item.created_at >= cutoff).all()
        for item in items:
            item.computed_score = compute_item_score_for_item(item)
        db.commit()
        log.info(f"Recomputed scores for {len(items)} items (last {SCORE_WINDOW_DAYS} days).")
    finally:
        db.close()


if __name__ == "__main__":
    run_cron()
