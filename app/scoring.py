"""
RNews item scoring — the "hot" sort.

Formula
-------
    score = (A + B) × e^(−λ × t)

    A  =  journal_score / max_js          (source quality, always in [0, 1])
          For auto-ingested items, journal_score comes from journals.json.
          For user submissions, journal_score is treated as
          USER_SUBMISSION_JS_FRACTION × max_js (default 0.8), reflecting the
          assumption that manually submitted papers are of high quality.

    B  =  log(1 + votes) / log(1 + vote_scale)   (community engagement)
          Logarithmic so early votes matter more than later ones.
          B reaches exactly 1.0 when votes == vote_scale (default 1000),
          meaning 1 000 upvotes equals the contribution of a max-score journal.

    t  =  age of the item in fractional days.

    λ  =  exponential decay rate per day (default 0.3).
          Half-life ≈ ln(2) / λ ≈ 2.3 days.
          At t = 3 days : ~40 % of score remaining.
          At t = 7 days : ~12 % of score remaining.

Re-tuning guide
---------------
Change only the constants below (LAMBDA, VOTE_SCALE,
USER_SUBMISSION_JS_FRACTION) — never touch the formula itself.

    LAMBDA         lower  → slower decay  (0.1  → half-life ≈ 7 days)
                   higher → faster decay  (0.5  → half-life ≈ 1.4 days)
    VOTE_SCALE     lower  → community votes matter more relative to journal score
                   higher → community votes matter less
    USER_SUBMISSION_JS_FRACTION
                   0–1 fraction of max_js given to manually submitted items.
                   0.8 means user submissions start at 80 % of the top journal score.

Source quality values
---------------------
Stored as "journal_score" in journals.json. Values are the OpenAlex 2-year
mean citedness (summary_stats.2yr_mean_citedness) — an open equivalent of
the Journal Impact Factor, refreshed annually via private_local/fetch_ifs.py.
Journals with no usable OpenAlex data (e.g. BMJ) retain their previous value
and should be updated manually when better data is available.
max_js is derived at runtime from the current file — no constant to update
when journals are added or removed.

This module must not import from app.main (no FastAPI/static-file side
effects — safe to use in cron scripts).
"""

import json
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Hyperparameters — adjust here to re-tune, never inside the formula
# ---------------------------------------------------------------------------
LAMBDA: float = 0.3
VOTE_SCALE: float = 1000.0
USER_SUBMISSION_JS_FRACTION: float = 0.8

_JOURNALS_PATH = Path(__file__).parent.parent / "journals.json"


@lru_cache(maxsize=1)
def _load_js_data() -> tuple[dict[str, float], float]:
    """Return (name_lower → journal_score, max_js). Cached for the process lifetime."""
    journals = json.loads(_JOURNALS_PATH.read_text())
    js_map: dict[str, float] = {}
    max_js = 0.0
    for j in journals:
        js = float(j.get("journal_score", 0.0))
        js_map[j["name"].lower().strip()] = js
        if js > max_js:
            max_js = js
    return js_map, max_js


def get_journal_score(journal_name: str) -> float:
    """Return the journal_score for a display name, or 0.0 if not in journals.json."""
    js_map, _ = _load_js_data()
    return js_map.get(journal_name.lower().strip(), 0.0)


def get_max_js() -> float:
    """Return the maximum journal_score across all journals in journals.json."""
    _, max_js = _load_js_data()
    return max_js


def compute_item_score(
    js: float,
    votes: int,
    days_since_pub: float,
    max_js: float,
    vote_scale: float = VOTE_SCALE,
    lambda_: float = LAMBDA,
) -> float:
    """
    Core scoring function — operates on plain values, not ORM objects.

    Kept parameter-explicit so it can be unit-tested and reasoned about
    independently of the database layer. Use compute_item_score_for_item()
    when you have an ORM Item instance.

    Parameters
    ----------
    js : float
        Journal score for this item. For user submissions pass
        USER_SUBMISSION_JS_FRACTION * max_js.
    votes : int
        Number of upvotes.
    days_since_pub : float
        Fractional days since the item was created.
    max_js : float
        Maximum journal_score across all journals (for normalisation).
    vote_scale : float
        Votes at which engagement contribution equals 1.0 (default 1 000).
    lambda_ : float
        Decay rate per day (default 0.3).
    """
    a = js / max_js if max_js > 0 else 0.0
    b = math.log1p(votes) / math.log1p(vote_scale)
    decay = math.exp(-lambda_ * max(days_since_pub, 0.0))
    return (a + b) * decay


def compute_item_score_for_item(item) -> float:
    """
    Compute the hot score for an ORM Item instance.

    Looks up journal_score by item.journal (the display name stored at ingest
    time, matching the "name" field in journals.json). Auto-ingested items
    use the journal score; user submissions receive USER_SUBMISSION_JS_FRACTION
    × max_js. Returns 0.0 if journals.json contains no score data.
    """
    js_map, max_js = _load_js_data()
    if max_js == 0.0:
        return 0.0

    if item.auto_ingested and item.journal:
        js = js_map.get(item.journal.lower().strip(), 0.0)
    else:
        js = USER_SUBMISSION_JS_FRACTION * max_js

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    days = max((now - item.created_at).total_seconds() / 86400.0, 0.0)

    return compute_item_score(
        js=js,
        votes=len(item.votes),
        days_since_pub=days,
        max_js=max_js,
    )
