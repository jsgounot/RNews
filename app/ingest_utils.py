"""
Shared utilities used by both main.py (bulk ingest) and cron_ingest.py (automated feed).
"""

import re as _re
from datetime import datetime, date as _date
from pathlib import Path
from typing import Optional

from slugify import slugify
from sqlalchemy.orm import Session

JOURNALS_FILE = Path(__file__).parent.parent / "journals.json"

# ---------------------------------------------------------------------------
# Journal index
# ---------------------------------------------------------------------------

def _build_journal_index() -> dict:
    """Return {lowercased_key: display_name} from journals.json."""
    import json
    try:
        entries = json.loads(JOURNALS_FILE.read_text())
    except Exception:
        return {}
    idx = {}
    for e in entries:
        display = e["name"]
        if e.get("abbrev"):
            idx[e["abbrev"].lower()] = display
        idx[e["pubmed"].lower()] = display
        idx[e["name"].lower()] = display
    return idx


JOURNAL_INDEX: dict = _build_journal_index()


# ---------------------------------------------------------------------------
# DOI normalisation
# ---------------------------------------------------------------------------

def normalize_doi_url(url: str) -> str:
    """Normalize any DOI form to https://doi.org/10.xxx."""
    if not url:
        return url
    stripped = url.strip()
    lower = stripped.lower()
    if lower.startswith("https://doi.org/10."):
        return stripped
    for prefix in ("http://dx.doi.org/", "https://dx.doi.org/",
                   "http://doi.org/", "https://doi.org/"):
        if lower.startswith(prefix):
            return f"https://doi.org/{stripped[len(prefix):]}"
    if lower.startswith("doi:"):
        return f"https://doi.org/{stripped[4:]}"
    if lower.startswith("10."):
        return f"https://doi.org/{stripped}"
    return stripped


# ---------------------------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------------------------

def get_or_create_tag(db: Session, name: str):
    from app.models import Tag
    name = name.strip().lower()
    slug = slugify(name)
    tag = db.query(Tag).filter(Tag.slug == slug).first()
    if not tag:
        tag = Tag(name=name, slug=slug)
        db.add(tag)
        db.commit()
        db.refresh(tag)
    return tag


def tag_vote_count(tag_dict: dict) -> int:
    """Derive vote_count from a tag dict that may carry numeric or string confidence.

    Numeric (cron pipeline, 1–10):  vote_count = 5 + confidence  →  6–15
    String  (bulk ingest):           high=15, medium=10, low=5
    Missing:                         10
    """
    c = tag_dict.get("confidence")
    if isinstance(c, (int, float)):
        return 5 + max(0, min(10, int(c)))
    if isinstance(c, str):
        return {"high": 15, "medium": 10, "low": 5}.get(c.lower(), 10)
    return 10


# ---------------------------------------------------------------------------
# Publication date parsing
# ---------------------------------------------------------------------------

def _parse_pub_date(raw: str) -> Optional[_date]:
    """Parse PubMed pub_date strings like '2026 Jun 9', '2026 Feb', '2026Jan'."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y %b %d", "%Y %b", "%Y%m%d", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    m = _re.match(r"^(\d{4})\s+([A-Za-z]+)", raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y %b").date()
        except ValueError:
            pass
    return None


def best_pub_date(paper: dict) -> Optional[_date]:
    """Return the best usable publication date, avoiding Dec-31 placeholders and future dates."""
    from datetime import timezone
    today = datetime.now(timezone.utc).date()

    def _usable(d: Optional[_date]) -> bool:
        return d is not None and d <= today and not (d.month == 12 and d.day == 31)

    dp = _parse_pub_date(paper.get("pub_date", ""))
    if _usable(dp):
        return dp
    dep = _parse_pub_date(paper.get("epub_date", ""))
    if _usable(dep):
        return dep
    return None
