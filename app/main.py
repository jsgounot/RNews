import math
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from fastapi import (
    FastAPI, Request, Form, Depends, HTTPException, Query, UploadFile
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slugify import slugify
from sqlalchemy.orm import Session

from app.auth import (
    authenticate_user, create_user,
    get_user_by_email, get_user_by_id, get_user_by_username,
)
from app.database import get_db, init_db
from app.models import (
    Comment, CommentVote, Item, Tag, User, Vote,
    SavedTag, Team, TeamMember, TeamItem, FavoriteItem,
)
from app.services.metadata import extract_metadata, is_scientific_url

app = FastAPI(title="RNews")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup():
    init_db()


# ── Session helpers ───────────────────────────────────────────────────────────

from starlette.middleware.sessions import SessionMiddleware
import secrets

_SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=_SECRET_KEY, max_age=86400 * 30)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(db, user_id)


# ── Jinja2 globals ────────────────────────────────────────────────────────────

def tag_color(tag_name: str) -> str:
    colors = [
        "#4a90d9", "#7bc67e", "#e07b54", "#c47ed4",
        "#e6b84a", "#5bbcbe", "#d4726b", "#9eb86b",
        "#7a8fe0", "#c97bb8",
    ]
    idx = sum(ord(c) for c in tag_name) % len(colors)
    return colors[idx]


def time_ago(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    return f"{months}mo ago"


templates.env.globals["tag_color"] = tag_color
templates.env.globals["time_ago"] = time_ago
templates.env.globals["now"] = lambda: datetime.now(timezone.utc)

# Cache-busting token for static assets.
# On Railway: uses the git commit SHA (changes only on deploy).
# Locally: falls back to an hourly timestamp.
_STATIC_VERSION = (
    os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:8]
    or os.environ.get("RENDER_GIT_COMMIT", "")[:8]
    or datetime.now().strftime("%Y%m%d%H")
)
templates.env.globals["static_v"] = _STATIC_VERSION


# ── Helper functions ──────────────────────────────────────────────────────────

def get_or_create_tag(db: Session, name: str) -> Tag:
    name = name.strip().lower()
    slug = slugify(name)
    tag = db.query(Tag).filter(Tag.slug == slug).first()
    if not tag:
        tag = Tag(name=name, slug=slug)
        db.add(tag)
        db.commit()
        db.refresh(tag)
    return tag


def _edit_distance(a: str, b: str) -> int:
    """Standard dynamic-programming Levenshtein distance."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def normalize_doi_url(url: str) -> str:
    """Normalize any DOI form to https://doi.org/10.xxx.

    Accepts:
      - bare DOI:           10.1234/something
      - http/https doi.org: http://doi.org/10.xxx  or  https://doi.org/10.xxx
      - dx.doi.org variant: https://dx.doi.org/10.xxx
    Returns the input unchanged if it doesn't look like a DOI.
    """
    if not url:
        return url
    stripped = url.strip()
    lower = stripped.lower()
    # Already canonical
    if lower.startswith("https://doi.org/10."):
        return stripped
    # http → https, dx.doi.org → doi.org
    for prefix in ("http://dx.doi.org/", "https://dx.doi.org/",
                   "http://doi.org/", "https://doi.org/"):
        if lower.startswith(prefix):
            path = stripped[len(prefix):]
            return f"https://doi.org/{path}"
    # Bare DOI: starts with "10."
    if lower.startswith("10."):
        return f"https://doi.org/{stripped}"
    return stripped


async def _resolve_display_url(url: str) -> Optional[str]:
    """If url is a DOI link, follow redirects and return the final endpoint URL for display.
    Returns None on failure or if the URL is not a DOI."""
    if not url or "doi.org/" not in url.lower():
        return None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
            resp = await client.head(url, headers={"User-Agent": "Mozilla/5.0 (RNews)"})
            resolved = str(resp.url)
            return resolved if resolved != url else None
    except Exception:
        return None


def find_exact_url(db: Session, url: str, exclude_team_only: bool = False) -> Optional[Item]:
    """Return an existing item matching this URL, or None.

    Checks both Item.url and Item.display_url so that a DOI URL and its
    resolved publisher endpoint are treated as the same paper.
    If exclude_team_only=True, items submitted only to a team are ignored.
    """
    if not url:
        return None

    def _q(col):
        q = db.query(Item).filter(col == url)
        if exclude_team_only:
            q = q.filter(Item.is_team_only == False)  # noqa: E712
        return q.first()

    return _q(Item.url) or _q(Item.display_url)


def find_close_titles(db: Session, title: str, max_distance: int = 3,
                      exclude_url: Optional[str] = None) -> list:
    """Return items whose title is within *max_distance* edits of *title*."""
    needle = title.lower().strip()
    close = []
    for item in db.query(Item).all():
        haystack = item.title.lower().strip()
        if haystack == needle:
            if exclude_url and item.url == exclude_url:
                continue  # already caught by exact-URL check
            close.append((item, 0))
            continue
        # Only compute full distance when lengths are close enough to matter
        if abs(len(haystack) - len(needle)) <= max_distance:
            d = _edit_distance(needle, haystack)
            if d <= max_distance:
                close.append((item, d))
    close.sort(key=lambda x: x[1])
    return [item for item, _ in close]


def build_comment_tree(comments: list) -> list:
    by_id = {c.id: {"comment": c, "children": []} for c in comments}
    roots = []
    for node in by_id.values():
        pid = node["comment"].parent_id
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)

    def sort_key(n):
        return (-n["comment"].score, n["comment"].created_at)

    def sort_tree(nodes):
        nodes.sort(key=sort_key)
        for n in nodes:
            sort_tree(n["children"])

    sort_tree(roots)
    return roots


def get_items_for_period(
    db: Session,
    start: datetime,
    end: datetime,
    tag_slug: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    sort: str = "score",
):
    q = db.query(Item).filter(
        Item.created_at >= start, Item.created_at < end, Item.is_team_only == False  # noqa: E712
    )
    if tag_slug:
        q = q.join(Item.tags).filter(Tag.slug == tag_slug)
    items = q.all()
    if sort == "score":
        items.sort(key=lambda i: (-i.score, i.created_at))
    else:
        items.sort(key=lambda i: i.created_at, reverse=True)
    total = len(items)
    start_idx = (page - 1) * per_page
    return items[start_idx: start_idx + per_page], total


def _sort_items(items: list, sort: str) -> list:
    if sort == "time":
        items.sort(key=lambda i: i.created_at, reverse=True)
    else:
        items.sort(key=lambda i: (-i.score, i.created_at))
    return items


def get_top_items(
    db: Session,
    days: int = 7,
    limit: int = 20,
    tag_slug: Optional[str] = None,
    sort: str = "score",
):
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    q = db.query(Item).filter(Item.created_at >= cutoff, Item.is_team_only == False)  # noqa: E712
    if tag_slug:
        q = q.join(Item.tags).filter(Tag.slug == tag_slug)
    items = q.all()
    return _sort_items(items, sort)[:limit]


def get_user_feed_items(
    db: Session,
    profile: User,
    tag_slug: Optional[str] = None,
    sort: str = "score",
) -> list:
    """Items from saved tags (all users) UNION starred items, never lost even if tags change."""
    saved_tag_ids = [st.tag_id for st in profile.saved_tags]
    favorite_item_ids = {fi.item_id for fi in profile.favorite_items}

    if tag_slug:
        # Filtered view: only items with that tag
        items = (
            db.query(Item)
            .filter(Item.is_team_only == False)  # noqa: E712
            .join(Item.tags).filter(Tag.slug == tag_slug)
            .all()
        )
    else:
        # Union: saved-tag items + starred items
        seen: set = set()
        items: list = []

        if saved_tag_ids:
            for item in (
                db.query(Item)
                .filter(Item.is_team_only == False,  # noqa: E712
                        Item.tags.any(Tag.id.in_(saved_tag_ids)))
                .all()
            ):
                if item.id not in seen:
                    seen.add(item.id)
                    items.append(item)

        if favorite_item_ids:
            missing = favorite_item_ids - seen
            if missing:
                for item in (
                    db.query(Item)
                    .filter(Item.id.in_(missing), Item.is_team_only == False)  # noqa: E712
                    .all()
                ):
                    seen.add(item.id)
                    items.append(item)

    if not items and not favorite_item_ids and not saved_tag_ids:
        return []

    return _sort_items(items, sort)


def user_voted_items(db: Session, user: Optional[User], items: list) -> set:
    if not user:
        return set()
    item_ids = {i.id for i in items}
    votes = db.query(Vote).filter(Vote.user_id == user.id, Vote.item_id.in_(item_ids)).all()
    return {v.item_id for v in votes}


def user_voted_comments(db: Session, user: Optional[User], comments: list) -> set:
    if not user:
        return set()
    cids = {c.id for c in comments}
    votes = db.query(CommentVote).filter(
        CommentVote.user_id == user.id, CommentVote.comment_id.in_(cids)
    ).all()
    return {v.comment_id for v in votes}


def user_favorited_items(db: Session, user: Optional[User], items: list) -> set:
    if not user or not items:
        return set()
    item_ids = {i.id for i in items}
    favs = db.query(FavoriteItem).filter(
        FavoriteItem.user_id == user.id, FavoriteItem.item_id.in_(item_ids)
    ).all()
    return {f.item_id for f in favs}


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, email, password)
    if not user:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid email or password."},
            status_code=401,
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"error": ""})


@app.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if get_user_by_email(db, email):
        return templates.TemplateResponse(
            request, "register.html",
            {"error": "Email already registered."},
            status_code=400,
        )
    if get_user_by_username(db, username):
        return templates.TemplateResponse(
            request, "register.html",
            {"error": "Username already taken."},
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            request, "register.html",
            {"error": "Password must be at least 6 characters."},
            status_code=400,
        )
    user = create_user(db, email=email, username=username, password=password)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)


# ── Main pages ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def homepage(
    request: Request,
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(20, ge=5, le=100),
    sort: str = Query("score", pattern="^(score|time)$"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    items = get_top_items(db, days=days, limit=limit, sort=sort)
    voted = user_voted_items(db, user, items)
    favorited = user_favorited_items(db, user, items)

    today = datetime.now(timezone.utc).date()
    week_days = [today - timedelta(days=i) for i in range(6, -1, -1)]

    return templates.TemplateResponse(request, "index.html", {
        "items": items,
        "voted": voted,
        "favorited": favorited,
        "user": user,
        "days": days,
        "limit": limit,
        "sort": sort,
        "week_days": week_days,
        "page_title": "Top Stories",
        "tag": None,
        "tag_saved": False,
    })


@app.get("/day/{date_str}", response_class=HTMLResponse)
def day_view(
    request: Request,
    date_str: str,
    page: int = Query(1, ge=1),
    sort: str = Query("score", pattern="^(score|time)$"),
    tag_slug: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid date")

    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end = start + timedelta(days=1)
    per_page = 50

    items, total = get_items_for_period(
        db, start, end, tag_slug=tag_slug, page=page, per_page=per_page, sort=sort
    )
    voted = user_voted_items(db, user, items)
    favorited = user_favorited_items(db, user, items)
    total_pages = max(1, math.ceil(total / per_page))
    tag = db.query(Tag).filter(Tag.slug == tag_slug).first() if tag_slug else None

    return templates.TemplateResponse(request, "day.html", {
        "items": items,
        "voted": voted,
        "favorited": favorited,
        "user": user,
        "date": day,
        "date_str": date_str,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "sort": sort,
        "tag": tag,
        "tag_slug": tag_slug,
    })


@app.get("/tag/{tag_slug}", response_class=HTMLResponse)
def tag_page(
    request: Request,
    tag_slug: str,
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(20, ge=5, le=100),
    sort: str = Query("score", pattern="^(score|time)$"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    tag = db.query(Tag).filter(Tag.slug == tag_slug).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    items = get_top_items(db, days=days, limit=limit, tag_slug=tag_slug, sort=sort)
    voted = user_voted_items(db, user, items)
    favorited = user_favorited_items(db, user, items)

    today = datetime.now(timezone.utc).date()
    week_days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    saved = user and any(st.tag.slug == tag_slug for st in user.saved_tags)

    return templates.TemplateResponse(request, "index.html", {
        "items": items,
        "voted": voted,
        "favorited": favorited,
        "user": user,
        "days": days,
        "limit": limit,
        "sort": sort,
        "week_days": week_days,
        "page_title": f"#{tag.name}",
        "tag": tag,
        "tag_saved": saved,
    })


@app.get("/item/{item_id}", response_class=HTMLResponse)
def item_page(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    comments = db.query(Comment).filter(Comment.item_id == item_id).all()
    tree = build_comment_tree(comments)
    voted_comments = user_voted_comments(db, user, comments)
    item_voted = item_id in user_voted_items(db, user, [item]) if user else False

    # Item is not shareable if it was submitted to a private team
    can_share = not (
        item.is_team_only
        and any(not ti.team.is_public for ti in item.team_items)
    )

    can_edit = bool(user and (user.is_superadmin or user.id == item.submitter_id))

    return templates.TemplateResponse(request, "item.html", {
        "item": item,
        "tree": tree,
        "user": user,
        "item_voted": item_voted,
        "voted_comments": voted_comments,
        "can_share": can_share,
        "can_edit": can_edit,
    })


def _search_filter_items(raw_items: list, user: Optional[User]) -> tuple:
    """Filter items based on team visibility and build source-team map.

    Returns (visible_items, item_source_team) where item_source_team maps
    item.id -> Team object when the item lives in a team channel.
    """
    user_team_ids: set = set()
    if user:
        for tm in user.team_memberships:
            user_team_ids.add(tm.team_id)

    visible: list = []
    item_source_team: dict = {}

    for item in raw_items:
        if not item.is_team_only:
            visible.append(item)
            continue
        # Team-only item: visible only if team is public or user is a member
        for ti in item.team_items:
            if ti.team.is_public or ti.team_id in user_team_ids:
                visible.append(item)
                item_source_team[item.id] = ti.team
                break

    return visible, item_source_team


@app.get("/tags", response_class=HTMLResponse)
def multi_tag_page(
    request: Request,
    q: str = Query(""),
    mode: str = Query("intersection", pattern="^(union|intersection)$"),
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(20, ge=5, le=100),
    sort: str = Query("score", pattern="^(score|time)$"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    tags_found = []
    if q:
        for s in [slugify(t.strip()) for t in q.split(",") if t.strip()]:
            tag = db.query(Tag).filter(Tag.slug == s).first()
            if tag:
                tags_found.append(tag)

    items = []
    if tags_found:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        base_q = db.query(Item).filter(
            Item.is_team_only == False,  # noqa: E712
            Item.created_at >= cutoff,
        )
        if mode == "union":
            tag_ids = [t.id for t in tags_found]
            base_q = base_q.filter(Item.tags.any(Tag.id.in_(tag_ids)))
        else:
            for tag in tags_found:
                base_q = base_q.filter(Item.tags.any(Tag.id == tag.id))
        items = _sort_items(base_q.all(), sort)[:limit]

    voted = user_voted_items(db, user, items)
    favorited = user_favorited_items(db, user, items)
    tag_slugs = ",".join(t.slug for t in tags_found)

    return templates.TemplateResponse(request, "tags.html", {
        "tags_found": tags_found,
        "tag_slugs": tag_slugs,
        "q": q,
        "mode": mode,
        "days": days,
        "limit": limit,
        "sort": sort,
        "items": items,
        "voted": voted,
        "favorited": favorited,
        "user": user,
    })


@app.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = Query(""),
    tag_mode: bool = Query(False),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    raw_items = []
    tags_found = []
    if q:
        if tag_mode:
            slugs = [slugify(t.strip()) for t in q.split(",") if t.strip()]
            for s in slugs:
                tag = db.query(Tag).filter(Tag.slug == s).first()
                if tag:
                    tags_found.append(tag)
            if tags_found:
                q_items = db.query(Item)
                for tag in tags_found:
                    q_items = q_items.filter(Item.tags.any(Tag.id == tag.id))
                raw_items = q_items.all()
                raw_items.sort(key=lambda i: (-i.score, i.created_at))
        else:
            search_term = f"%{q}%"
            raw_items = db.query(Item).filter(Item.title.ilike(search_term)).all()
            raw_items.sort(key=lambda i: (-i.score, i.created_at))

    items, item_source_team = _search_filter_items(raw_items, user)

    voted = user_voted_items(db, user, items)
    favorited = user_favorited_items(db, user, items)

    return templates.TemplateResponse(request, "search.html", {
        "q": q,
        "tag_mode": tag_mode,
        "items": items,
        "item_source_team": item_source_team,
        "voted": voted,
        "favorited": favorited,
        "tags_found": tags_found,
        "user": user,
    })


# ── Submit ────────────────────────────────────────────────────────────────────

@app.get("/submit", response_class=HTMLResponse)
def submit_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "submit.html", {
        "user": user, "error": "", "prefill": {}
    })


@app.post("/submit")
async def submit_item(
    request: Request,
    url: str = Form(""),
    title: str = Form(""),
    item_type: str = Form("link"),
    journal: str = Form(""),
    first_author: str = Form(""),
    last_author: str = Form(""),
    publication_date: str = Form(""),
    tags_input: str = Form(""),
    follow_up_of_id: str = Form(""),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)

    url = normalize_doi_url(url.strip())
    title = title.strip()
    errors = []

    if item_type == "paper":
        if not url:
            errors.append("URL is required for papers.")
        if not title:
            errors.append("Title is required.")
        if not journal:
            errors.append("Journal is required.")
        if not first_author:
            errors.append("First author is required.")
    else:
        if not title:
            errors.append("Title is required.")

    tag_names = [t.strip().lower() for t in tags_input.split(",") if t.strip()]
    if not tag_names:
        errors.append("At least one tag is required.")
    elif len(tag_names) > 5:
        errors.append("Maximum 5 tags allowed.")

    prefill = {
        "url": url, "title": title, "item_type": item_type,
        "journal": journal, "first_author": first_author,
        "last_author": last_author, "publication_date": publication_date,
        "tags_input": tags_input,
        "follow_up_of_id": follow_up_of_id,
    }

    if errors:
        return templates.TemplateResponse(request, "submit.html", {
            "user": user, "error": " ".join(errors), "prefill": prefill,
        })

    # ── Stage 1: hard block on exact URL duplicate ────────────────────────────
    # Exclude team-only items: a URL posted in a team should not block main-page submissions
    exact = find_exact_url(db, url, exclude_team_only=True)
    if exact:
        return templates.TemplateResponse(request, "submit.html", {
            "user": user, "error": "", "prefill": prefill,
            "exact_duplicate": exact,
        })

    # ── Stage 2: soft warning on close title (unless user confirmed) ──────────
    confirmed = request.query_params.get("confirmed") == "1"
    # follow_up_of comes from query param (set by warning form JS) or form body field
    raw_fup = request.query_params.get("follow_up_of") or follow_up_of_id or None
    follow_up_of_id = None
    if raw_fup:
        try:
            follow_up_of_id = int(raw_fup)
        except (ValueError, TypeError):
            pass

    if not confirmed:
        close = find_close_titles(db, title, max_distance=3, exclude_url=url)
        if close:
            return templates.TemplateResponse(request, "submit.html", {
                "user": user, "error": "", "prefill": prefill,
                "close_titles": close,
            })

    # ── Create item ───────────────────────────────────────────────────────────
    # Validate follow_up_of points to a real item
    if follow_up_of_id:
        parent = db.query(Item).filter(Item.id == follow_up_of_id).first()
        if not parent:
            follow_up_of_id = None

    display_url = await _resolve_display_url(url)

    item = Item(
        url=url or None,
        title=title,
        item_type=item_type,
        journal=journal.strip() or None,
        first_author=first_author.strip() or None,
        last_author=last_author.strip() or None,
        publication_date=publication_date.strip() or None,
        submitter_id=user.id,
        follow_up_of=follow_up_of_id,
        display_url=display_url,
    )

    for name in tag_names:
        tag = get_or_create_tag(db, name)
        if tag not in item.tags:
            item.tags.append(tag)

    db.add(item)
    db.commit()
    db.refresh(item)

    db.add(Vote(user_id=user.id, item_id=item.id))
    db.commit()

    return RedirectResponse(f"/item/{item.id}", status_code=302)


# ── API: metadata fetch ───────────────────────────────────────────────────────

@app.get("/api/metadata")
async def api_metadata(url: str = Query(...)):
    meta = await extract_metadata(url)
    meta["is_scientific"] = is_scientific_url(url)
    return JSONResponse(meta)


# ── API: tag autocomplete ─────────────────────────────────────────────────────

@app.get("/api/tags/suggest")
def api_tags_suggest(q: str = Query(""), db: Session = Depends(get_db)):
    q = q.strip().lower()
    if not q:
        return JSONResponse([])
    tags = db.query(Tag).filter(Tag.name.ilike(f"{q}%")).limit(10).all()
    return JSONResponse([{"name": t.name, "slug": t.slug} for t in tags])


# ── API: item search (follow-up autocomplete) ────────────────────────────────

@app.get("/api/items/search")
def api_items_search(q: str = Query(""), db: Session = Depends(get_db)):
    q = q.strip()
    if len(q) < 3:
        return JSONResponse([])
    items = (
        db.query(Item)
        .filter(Item.title.ilike(f"%{q}%"))
        .order_by(Item.created_at.desc())
        .limit(10)
        .all()
    )
    return JSONResponse([{"id": it.id, "title": it.title} for it in items])


# ── API: vote item ────────────────────────────────────────────────────────────

@app.post("/api/vote/{item_id}")
def vote_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        return JSONResponse({"error": "Not found"}, status_code=404)

    existing = db.query(Vote).filter(Vote.user_id == user.id, Vote.item_id == item_id).first()
    if existing:
        db.delete(existing)
        db.commit()
        voted = False
    else:
        db.add(Vote(user_id=user.id, item_id=item_id))
        db.commit()
        voted = True

    db.refresh(item)
    return JSONResponse({"score": item.score, "voted": voted})


# ── API: vote comment ─────────────────────────────────────────────────────────

@app.post("/api/vote_comment/{comment_id}")
def vote_comment(
    comment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        return JSONResponse({"error": "Not found"}, status_code=404)

    existing = db.query(CommentVote).filter(
        CommentVote.user_id == user.id, CommentVote.comment_id == comment_id
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        voted = False
    else:
        db.add(CommentVote(user_id=user.id, comment_id=comment_id))
        db.commit()
        voted = True

    db.refresh(comment)
    return JSONResponse({"score": comment.score, "voted": voted})


# ── API: add comment ──────────────────────────────────────────────────────────

@app.post("/api/comment/{item_id}")
def add_comment(
    item_id: int,
    request: Request,
    content: str = Form(...),
    parent_id: str = Form(""),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404)

    content = content.strip()
    if not content:
        return RedirectResponse(f"/item/{item_id}", status_code=302)

    parsed_parent_id = int(parent_id) if parent_id.strip() else None

    comment = Comment(
        item_id=item_id,
        user_id=user.id,
        parent_id=parsed_parent_id,
        content=content,
    )
    db.add(comment)
    db.commit()
    return RedirectResponse(f"/item/{item_id}#comments", status_code=302)


# ── Item edit / delete (superadmin or submitter) ──────────────────────────────

def _assert_can_edit(user: Optional[User], item: Item):
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    if not (user.is_superadmin or user.id == item.submitter_id):
        raise HTTPException(status_code=403, detail="Not allowed")


@app.post("/api/item/{item_id}/edit")
async def edit_item(
    request: Request,
    item_id: int,
    title: str = Form(""),
    journal: str = Form(""),
    first_author: str = Form(""),
    last_author: str = Form(""),
    publication_date: str = Form(""),
    tags_input: str = Form(""),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    _assert_can_edit(user, item)

    title = title.strip()
    if not title:
        return RedirectResponse(f"/item/{item_id}?edit_error=Title+required", status_code=302)

    tag_names = [t.strip().lower() for t in tags_input.split(",") if t.strip()]
    if not tag_names:
        return RedirectResponse(f"/item/{item_id}?edit_error=At+least+one+tag+required", status_code=302)
    if len(tag_names) > 5:
        return RedirectResponse(f"/item/{item_id}?edit_error=Maximum+5+tags", status_code=302)

    item.title = title
    if item.item_type == "paper":
        item.journal = journal.strip() or item.journal
        item.first_author = first_author.strip() or item.first_author
        item.last_author = last_author.strip() or None
        item.publication_date = publication_date.strip() or item.publication_date

    # Replace tags
    item.tags.clear()
    for name in tag_names:
        tag = get_or_create_tag(db, name)
        if tag not in item.tags:
            item.tags.append(tag)

    item.last_edited_by = user.id
    item.last_edited_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    return RedirectResponse(f"/item/{item_id}?edit_success=1", status_code=302)


@app.post("/api/item/{item_id}/delete")
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    _assert_can_edit(user, item)

    db.delete(item)
    db.commit()
    return RedirectResponse("/", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════
# NEW FEATURES — About, User page, Settings, Teams
# ═══════════════════════════════════════════════════════════════════════════

import random
import string
from app.auth import hash_password, verify_password


def _random_suffix(n=6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _unique_team_slug(db: Session, base: str) -> str:
    slug = slugify(base)[:80]
    if not db.query(Team).filter(Team.slug == slug).first():
        return slug
    for _ in range(20):
        candidate = f"{slug}-{_random_suffix()}"
        if not db.query(Team).filter(Team.slug == candidate).first():
            return candidate
    raise ValueError("Could not generate a unique team slug")


def _get_team_role(db: Session, team: Team, user: Optional[User]) -> Optional[str]:
    """Return the user's role in the team, or None if not a member."""
    if not user:
        return None
    m = db.query(TeamMember).filter(
        TeamMember.team_id == team.id, TeamMember.user_id == user.id
    ).first()
    return m.role if m else None


def _assert_team_access(team: Team, role: Optional[str]):
    """Raise 403 if user cannot view this team."""
    if not team.is_public and role is None:
        raise HTTPException(status_code=403, detail="This team is private.")


def _team_items_query(db: Session, team: Team, tag_slug: Optional[str] = None):
    q = (
        db.query(Item)
        .join(TeamItem, TeamItem.item_id == Item.id)
        .filter(TeamItem.team_id == team.id)
    )
    if tag_slug:
        q = q.join(Item.tags).filter(Tag.slug == tag_slug)
    return q


# ── About ─────────────────────────────────────────────────────────────────────

@app.get("/about", response_class=HTMLResponse)
def about_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
):
    return templates.TemplateResponse(request, "about.html", {"user": user})


# ── User page ─────────────────────────────────────────────────────────────────

@app.get("/user/{username}", response_class=HTMLResponse)
def user_page(
    request: Request,
    username: str,
    tag: Optional[str] = Query(None),
    sort: str = Query("score", pattern="^(score|time)$"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    profile = get_user_by_username(db, username)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")

    # Saved tags for this user
    saved_tags = [st.tag for st in profile.saved_tags]

    # Items: feed from saved tags (all users), or empty if none saved
    tag_slug = slugify(tag) if tag else None
    items = get_user_feed_items(db, profile, tag_slug=tag_slug, sort=sort)

    voted = user_voted_items(db, current_user, items)
    favorited = user_favorited_items(db, current_user, items)

    # Tags available for filtering (union of saved tags + all tags on those items)
    filter_tags: dict = {}
    for st in profile.saved_tags:
        filter_tags[st.tag.slug] = st.tag
    for item in items:
        for t in item.tags:
            filter_tags[t.slug] = t

    # Teams visible to the viewer (public, or private ones viewer is a member of)
    viewer_team_ids = set()
    if current_user:
        viewer_team_ids = {
            tm.team_id for tm in db.query(TeamMember)
            .filter(TeamMember.user_id == current_user.id).all()
        }
    profile_teams = []
    for tm in profile.team_memberships:
        team = tm.team
        if team.is_public or team.id in viewer_team_ids:
            profile_teams.append(team)

    return templates.TemplateResponse(request, "user.html", {
        "profile": profile,
        "items": items,
        "voted": voted,
        "favorited": favorited,
        "current_user": current_user,
        "saved_tags": saved_tags,
        "filter_tags": list(filter_tags.values()),
        "active_tag": tag_slug,
        "sort": sort,
        "profile_teams": profile_teams,
        "user": current_user,
    })


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    tab: str = Query("profile"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)

    teams = (
        db.query(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(TeamMember.user_id == user.id)
        .all()
    )
    team_roles = {
        tm.team_id: tm.role
        for tm in db.query(TeamMember).filter(TeamMember.user_id == user.id).all()
    }

    return templates.TemplateResponse(request, "settings.html", {
        "user": user,
        "tab": tab,
        "teams": teams,
        "team_roles": team_roles,
        "error": "",
        "success": "",
    })


@app.post("/settings/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)

    def _respond(error="", success=""):
        teams = (
            db.query(Team).join(TeamMember).filter(TeamMember.user_id == user.id).all()
        )
        team_roles = {
            tm.team_id: tm.role
            for tm in db.query(TeamMember).filter(TeamMember.user_id == user.id).all()
        }
        return templates.TemplateResponse(request, "settings.html", {
            "user": user, "tab": "profile",
            "teams": teams, "team_roles": team_roles,
            "error": error, "success": success,
        })

    if not verify_password(current_password, user.hashed_password):
        return _respond(error="Current password is incorrect.")
    if len(new_password) < 6:
        return _respond(error="New password must be at least 6 characters.")
    if new_password != confirm_password:
        return _respond(error="Passwords do not match.")

    user.hashed_password = hash_password(new_password)
    db.commit()
    return _respond(success="Password updated successfully.")


@app.post("/settings/email")
def change_email(
    request: Request,
    new_email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)

    def _respond(error="", success=""):
        teams = (
            db.query(Team).join(TeamMember).filter(TeamMember.user_id == user.id).all()
        )
        team_roles = {
            tm.team_id: tm.role
            for tm in db.query(TeamMember).filter(TeamMember.user_id == user.id).all()
        }
        return templates.TemplateResponse(request, "settings.html", {
            "user": user, "tab": "profile",
            "teams": teams, "team_roles": team_roles,
            "error": error, "success": success,
        })

    if not verify_password(password, user.hashed_password):
        return _respond(error="Password is incorrect.")
    existing = get_user_by_email(db, new_email)
    if existing and existing.id != user.id:
        return _respond(error="Email already in use by another account.")

    user.email = new_email
    db.commit()
    return _respond(success="Email updated successfully.")


# ── Settings: preferences ────────────────────────────────────────────────────

@app.post("/settings/preferences")
async def save_preferences(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    form_data = await request.form()
    user.auto_upvote_on_favorite = form_data.get("auto_upvote_on_favorite", "") in ("true", "on", "1", "yes")
    db.commit()
    return RedirectResponse("/settings?tab=profile&success=Preferences+saved", status_code=302)


# ── Team creation ─────────────────────────────────────────────────────────────

@app.post("/teams/create")
async def create_team(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)

    # HTML checkboxes only submit when checked; use raw form data to detect absence
    form_data = await request.form()
    is_public = form_data.get("is_public", "") in ("true", "on", "1", "yes")

    name = name.strip()
    if not name:
        return RedirectResponse("/settings?tab=teams&error=Name+required", status_code=302)

    try:
        slug = _unique_team_slug(db, name)
    except ValueError:
        return RedirectResponse("/settings?tab=teams&error=Could+not+create+team", status_code=302)

    team = Team(
        name=name,
        slug=slug,
        description=description.strip() or None,
        is_public=is_public,
        created_by=user.id,
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    # Creator becomes admin
    db.add(TeamMember(team_id=team.id, user_id=user.id, role="admin"))
    db.commit()

    return RedirectResponse(f"/teams/{team.slug}", status_code=302)


# ── Team landing page ─────────────────────────────────────────────────────────

@app.get("/teams/{team_slug}", response_class=HTMLResponse)
def team_page(
    request: Request,
    team_slug: str,
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(20, ge=5, le=100),
    sort: str = Query("time", pattern="^(score|time)$"),  # default: newest first
    tag: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    role = _get_team_role(db, team, user)
    _assert_team_access(team, role)

    tag_slug = slugify(tag) if tag else None
    items = _team_items_query(db, team, tag_slug=tag_slug).all()

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    items = [i for i in items if i.created_at >= cutoff]
    items = _sort_items(items, sort)[:limit]

    voted = user_voted_items(db, user, items)
    favorited = user_favorited_items(db, user, items)

    # Collect all tags used in this team for the filter
    team_tags: dict = {}
    for item in _team_items_query(db, team).all():
        for t in item.tags:
            team_tags[t.slug] = t

    today = datetime.now(timezone.utc).date()
    week_days = [today - timedelta(days=i) for i in range(6, -1, -1)]

    return templates.TemplateResponse(request, "team.html", {
        "team": team,
        "role": role,
        "items": items,
        "voted": voted,
        "favorited": favorited,
        "user": user,
        "days": days,
        "limit": limit,
        "sort": sort,
        "week_days": week_days,
        "team_tags": list(team_tags.values()),
        "active_tag": tag,
        "members": team.members,
    })


# ── Team day view ─────────────────────────────────────────────────────────────

@app.get("/teams/{team_slug}/day/{date_str}", response_class=HTMLResponse)
def team_day_view(
    request: Request,
    team_slug: str,
    date_str: str,
    page: int = Query(1, ge=1),
    sort: str = Query("score", pattern="^(score|time)$"),
    tag: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    role = _get_team_role(db, team, user)
    _assert_team_access(team, role)

    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid date")

    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end = start + timedelta(days=1)
    tag_slug = slugify(tag) if tag else None

    items = _team_items_query(db, team, tag_slug=tag_slug).filter(
        Item.created_at >= start, Item.created_at < end
    ).all()

    if sort == "score":
        items.sort(key=lambda i: (-i.score, i.created_at))
    else:
        items.sort(key=lambda i: i.created_at, reverse=True)

    total = len(items)
    per_page = 50
    total_pages = max(1, math.ceil(total / per_page))
    items = items[(page - 1) * per_page: page * per_page]
    voted = user_voted_items(db, user, items)

    return templates.TemplateResponse(request, "day.html", {
        "items": items, "voted": voted, "user": user,
        "date": day, "date_str": date_str,
        "page": page, "total_pages": total_pages, "total": total,
        "sort": sort, "tag": None, "tag_slug": None,
        "team": team,
    })


# ── Team submit ───────────────────────────────────────────────────────────────

@app.get("/teams/{team_slug}/submit", response_class=HTMLResponse)
def team_submit_page(
    request: Request,
    team_slug: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    role = _get_team_role(db, team, user)
    if role not in ("admin", "contributor"):
        raise HTTPException(status_code=403, detail="Not allowed")
    return templates.TemplateResponse(request, "team_submit.html", {
        "user": user, "team": team, "error": "", "prefill": {}
    })


@app.post("/teams/{team_slug}/submit")
async def team_submit_item(
    request: Request,
    team_slug: str,
    url: str = Form(""),
    title: str = Form(""),
    item_type: str = Form("link"),
    journal: str = Form(""),
    first_author: str = Form(""),
    last_author: str = Form(""),
    publication_date: str = Form(""),
    tags_input: str = Form(""),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    role = _get_team_role(db, team, user)
    if role not in ("admin", "contributor"):
        raise HTTPException(status_code=403, detail="Not allowed")

    url = normalize_doi_url(url.strip())
    title = title.strip()
    errors = []

    if item_type == "paper":
        if not url:
            errors.append("URL is required for papers.")
        if not title:
            errors.append("Title is required.")
        if not journal:
            errors.append("Journal is required.")
        if not first_author:
            errors.append("First author is required.")
    else:
        if not title:
            errors.append("Title is required.")

    tag_names = [t.strip().lower() for t in tags_input.split(",") if t.strip()]
    if not tag_names:
        errors.append("At least one tag is required.")
    elif len(tag_names) > 5:
        errors.append("Maximum 5 tags allowed.")

    if errors:
        return templates.TemplateResponse(request, "team_submit.html", {
            "user": user, "team": team,
            "error": " ".join(errors),
            "prefill": {
                "url": url, "title": title, "item_type": item_type,
                "journal": journal, "first_author": first_author,
                "last_author": last_author, "publication_date": publication_date,
                "tags_input": tags_input,
            },
        })

    # Check for duplicate URL within this team
    if url:
        existing_item = db.query(Item).filter(Item.url == url).first()
        if existing_item:
            already_in_team = db.query(TeamItem).filter(
                TeamItem.team_id == team.id, TeamItem.item_id == existing_item.id
            ).first()
            if already_in_team:
                return RedirectResponse(f"/item/{existing_item.id}?duplicate=1", status_code=302)

    display_url = await _resolve_display_url(url)

    item = Item(
        url=url or None,
        title=title,
        item_type=item_type,
        journal=journal.strip() or None,
        first_author=first_author.strip() or None,
        last_author=last_author.strip() or None,
        publication_date=publication_date.strip() or None,
        submitter_id=user.id,
        is_team_only=True,
        display_url=display_url,
    )
    for name in tag_names:
        tag_obj = get_or_create_tag(db, name)
        if tag_obj not in item.tags:
            item.tags.append(tag_obj)

    db.add(item)
    db.commit()
    db.refresh(item)

    db.add(Vote(user_id=user.id, item_id=item.id))
    db.add(TeamItem(team_id=team.id, item_id=item.id, added_by=user.id, source="submitted"))
    db.commit()

    return RedirectResponse(f"/teams/{team_slug}", status_code=302)


# ── API: share item to team ───────────────────────────────────────────────────

@app.post("/api/item/{item_id}/share")
def share_item_to_team(
    item_id: int,
    request: Request,
    team_slug: str = Form(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        return JSONResponse({"error": "Item not found"}, status_code=404)

    # Items submitted directly to a private team cannot be shared
    if item.is_team_only:
        for ti in item.team_items:
            if not ti.team.is_public:
                return JSONResponse(
                    {"error": "This item belongs to a private team and cannot be shared."},
                    status_code=403,
                )

    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        return JSONResponse({"error": "Team not found"}, status_code=404)

    role = _get_team_role(db, team, user)
    if role not in ("admin", "contributor"):
        return JSONResponse({"error": "Not a contributor in this team"}, status_code=403)

    existing = db.query(TeamItem).filter(
        TeamItem.team_id == team.id, TeamItem.item_id == item_id
    ).first()
    if existing:
        return JSONResponse({"status": "already_shared", "team": team.name})

    db.add(TeamItem(team_id=team.id, item_id=item_id, added_by=user.id, source="shared"))
    db.commit()
    return JSONResponse({"status": "shared", "team": team.name})


# ── API: save/unsave tag ──────────────────────────────────────────────────────

@app.post("/api/tag/{tag_slug}/save")
def toggle_saved_tag(
    tag_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    tag = db.query(Tag).filter(Tag.slug == tag_slug).first()
    if not tag:
        return JSONResponse({"error": "Tag not found"}, status_code=404)

    existing = db.query(SavedTag).filter(
        SavedTag.user_id == user.id, SavedTag.tag_id == tag.id
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        return JSONResponse({"saved": False})
    else:
        db.add(SavedTag(user_id=user.id, tag_id=tag.id))
        db.commit()
        return JSONResponse({"saved": True})


# ── API: team member management ───────────────────────────────────────────────

@app.post("/api/teams/{team_slug}/add-member")
def add_team_member(
    team_slug: str,
    request: Request,
    email: str = Form(...),
    role: str = Form("contributor"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        raise HTTPException(status_code=404)

    my_role = _get_team_role(db, team, user)
    if my_role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can add members")

    target = get_user_by_email(db, email.strip())
    if not target:
        return RedirectResponse(
            f"/teams/{team_slug}?error=User+not+found", status_code=302
        )

    existing = db.query(TeamMember).filter(
        TeamMember.team_id == team.id, TeamMember.user_id == target.id
    ).first()
    if existing:
        existing.role = role
        db.commit()
    else:
        db.add(TeamMember(team_id=team.id, user_id=target.id, role=role))
        db.commit()

    return RedirectResponse(f"/teams/{team_slug}?success=Member+added", status_code=302)


@app.post("/api/teams/{team_slug}/remove-member/{target_user_id}")
def remove_team_member(
    team_slug: str,
    target_user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        raise HTTPException(status_code=404)

    my_role = _get_team_role(db, team, user)
    if my_role != "admin" and user.id != target_user_id:
        raise HTTPException(status_code=403, detail="Not allowed")

    member = db.query(TeamMember).filter(
        TeamMember.team_id == team.id, TeamMember.user_id == target_user_id
    ).first()
    if member:
        db.delete(member)
        db.commit()

    if user.id == target_user_id:
        return RedirectResponse("/settings?tab=teams", status_code=302)
    return RedirectResponse(f"/teams/{team_slug}", status_code=302)


@app.post("/api/teams/{team_slug}/delete")
def delete_team(
    team_slug: str,
    request: Request,
    confirm: str = Form(""),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        raise HTTPException(status_code=404)

    my_role = _get_team_role(db, team, user)
    if my_role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete teams")

    if confirm.strip().lower() != "delete":
        return RedirectResponse(
            f"/teams/{team_slug}?error=Type+%22delete%22+to+confirm", status_code=302
        )

    # Remove team-only items belonging to this team
    for ti in team.items:
        if ti.item.is_team_only:
            db.delete(ti.item)
    db.delete(team)
    db.commit()

    return RedirectResponse("/settings?tab=teams&success=Team+deleted", status_code=302)


# ── API: remove team item (admin only) ───────────────────────────────────────

@app.post("/api/teams/{team_slug}/remove-item/{item_id}")
def remove_team_item(
    team_slug: str,
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        return JSONResponse({"error": "Team not found"}, status_code=404)

    my_role = _get_team_role(db, team, user)
    if my_role != "admin":
        return JSONResponse({"error": "Admins only"}, status_code=403)

    ti = db.query(TeamItem).filter(
        TeamItem.team_id == team.id, TeamItem.item_id == item_id
    ).first()
    if ti:
        if ti.item.is_team_only:
            db.delete(ti.item)
        else:
            db.delete(ti)
        db.commit()

    return JSONResponse({"status": "removed"})


# ── API: toggle team visibility (admin only) ─────────────────────────────────

@app.post("/api/teams/{team_slug}/set-visibility")
def set_team_visibility(
    team_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    team = db.query(Team).filter(Team.slug == team_slug).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    role = _get_team_role(db, team, user)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    team.is_public = not team.is_public
    db.commit()

    label = "public" if team.is_public else "private"
    return RedirectResponse(
        f"/teams/{team_slug}?success=Team+is+now+{label}", status_code=302
    )


# ── API: favorite / unfavorite item ──────────────────────────────────────────

@app.post("/api/item/{item_id}/favorite")
def toggle_favorite(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        return JSONResponse({"error": "Not found"}, status_code=404)

    existing = db.query(FavoriteItem).filter(
        FavoriteItem.user_id == user.id, FavoriteItem.item_id == item_id
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        db.refresh(item)
        return JSONResponse({"favorited": False, "score": item.score, "auto_voted": False})

    db.add(FavoriteItem(user_id=user.id, item_id=item_id))

    # Auto-upvote if enabled and not already voted
    auto_voted = False
    if user.auto_upvote_on_favorite:
        already_voted = db.query(Vote).filter(
            Vote.user_id == user.id, Vote.item_id == item_id
        ).first()
        if not already_voted:
            db.add(Vote(user_id=user.id, item_id=item_id))
            auto_voted = True

    db.commit()
    db.refresh(item)
    return JSONResponse({"favorited": True, "score": item.score, "auto_voted": auto_voted})


# ── Forgot password ───────────────────────────────────────────────────────────

import secrets as _secrets
import string as _string
from app.services.mailer import send_new_password as _send_new_password


def _gen_password(length: int = 12) -> str:
    alphabet = _string.ascii_letters + _string.digits + "!@#$%^&*"
    return "".join(_secrets.choice(alphabet) for _ in range(length))


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {
        "sent": False, "error": ""
    })


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_user_by_email(db, email.strip().lower())

    # Always show the same success message to avoid email enumeration
    if user:
        new_pw = _gen_password()
        user.hashed_password = hash_password(new_pw)
        db.commit()
        ok = _send_new_password(user.email, user.username, new_pw)
        if not ok:
            # Email delivery failed — surface the error so the admin can
            # configure SMTP; don't leak the password to the browser
            return templates.TemplateResponse(request, "forgot_password.html", {
                "sent": False,
                "error": (
                    "Could not send the email (SMTP not configured?). "
                    "Please contact the site administrator."
                ),
            })

    return templates.TemplateResponse(request, "forgot_password.html", {
        "sent": True, "error": ""
    })


# ── Admin: bulk ingest ────────────────────────────────────────────────────────

import json as _json
import re as _re
from datetime import date as _date
from app.database import get_or_create_bot_user

# Build lookup: abbrev (lower) -> display name, and pubmed name (lower) -> display name
def _build_journal_index() -> dict:
    import json as _j
    from pathlib import Path as _P
    path = _P(__file__).parent.parent / "journals.json"
    try:
        entries = _j.loads(path.read_text())
    except Exception:
        return {}
    idx = {}
    for e in entries:
        display = e["name"]
        if e.get("abbrev"):
            idx[e["abbrev"].lower()] = display
        idx[e["pubmed"].lower()] = display
    return idx

_JOURNAL_INDEX: dict = _build_journal_index()


def _parse_pub_date(raw: str) -> Optional[_date]:
    """Parse PubMed pub_date strings like '2026 Jun 9', '2026 Feb', '2026 Jan'."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y %b %d", "%Y %b", "%Y%m%d", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    # Try stripping a trailing day if month-only parse fails after removing suffix
    m = _re.match(r"^(\d{4})\s+([A-Za-z]+)", raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y %b").date()
        except ValueError:
            pass
    return None


def _best_pub_date(paper: dict) -> Optional[_date]:
    """Return the best available publication date for a paper.

    Prefers pub_date unless it is a Dec 31 placeholder or in the future,
    in which case falls back to epub_date, then gives up (caller uses utcnow).
    """
    today = datetime.now(timezone.utc).date()

    def _is_usable(d: Optional[_date]) -> bool:
        return d is not None and d <= today and not (d.month == 12 and d.day == 31)

    dp = _parse_pub_date(paper.get("pub_date", ""))
    if _is_usable(dp):
        return dp

    dep = _parse_pub_date(paper.get("epub_date", ""))
    if _is_usable(dep):
        return dep

    return None


REQUIRED_INGEST_FIELDS = ("doi", "title", "journal", "pub_date", "authors", "tags")


def _validate_paper(paper: dict) -> Optional[str]:
    """Return an error string if the paper should be skipped, else None."""
    if paper.get("tag_error"):
        return f"tag_error: {paper['tag_error']}"
    for field in REQUIRED_INGEST_FIELDS:
        val = paper.get(field)
        if not val or (isinstance(val, list) and len(val) == 0):
            return f"missing field: {field}"
    journal_key = paper["journal"].lower()
    if journal_key not in _JOURNAL_INDEX:
        return f"unknown journal: {paper['journal']!r}"
    return None


def _ingest_papers(papers: list, db: Session, update_existing: bool = False):
    """Generator: yields log lines while inserting (or updating) papers in the DB."""
    bot_id = get_or_create_bot_user()
    existing_urls = {row[0] for row in db.query(Item.url).filter(Item.url.isnot(None)).all()}
    existing_display = {row[0] for row in db.query(Item.display_url).filter(Item.display_url.isnot(None)).all()}

    skipped_error = skipped_dup = skipped_missing = inserted = updated = 0

    for i, paper in enumerate(papers, 1):
        # Validation
        err = _validate_paper(paper)
        if err:
            if "tag_error" in err:
                skipped_error += 1
            else:
                skipped_missing += 1
            yield f"[{i}/{len(papers)}] SKIP  {paper.get('title', '?')[:60]} — {err}\n"
            continue

        # Normalize DOI → canonical URL
        doi_url = normalize_doi_url(paper["doi"])
        display_url = paper.get("display_url") or None

        # Duplicate detection (fast in-memory check)
        is_dup = (
            doi_url in existing_urls or doi_url in existing_display
            or (display_url and (display_url in existing_urls or display_url in existing_display))
        )

        # Shared field derivation (needed for both insert and update)
        pub_date_obj = _best_pub_date(paper)
        if pub_date_obj:
            created_at = datetime(pub_date_obj.year, pub_date_obj.month, pub_date_obj.day)
            pub_date_str = pub_date_obj.strftime("%Y-%m-%d")
        else:
            created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            pub_date_str = paper.get("pub_date", "")

        authors = paper["authors"]
        first_author = authors[0] if authors else None
        last_author = authors[-1] if len(authors) > 1 else None
        journal_display = _JOURNAL_INDEX[paper["journal"].lower()]
        tag_names = [t["tag"].strip().lower() for t in paper["tags"] if t.get("tag")][:5]

        if is_dup:
            if not update_existing:
                skipped_dup += 1
                yield f"[{i}/{len(papers)}] DUP   {paper['title'][:60]}\n"
                continue

            # Find the existing item in the DB
            existing = (
                db.query(Item).filter(Item.url == doi_url).first()
                or db.query(Item).filter(Item.display_url == doi_url).first()
                or (display_url and db.query(Item).filter(Item.url == display_url).first())
                or (display_url and db.query(Item).filter(Item.display_url == display_url).first())
            )
            if not existing:
                skipped_dup += 1
                yield f"[{i}/{len(papers)}] DUP?  {paper['title'][:60]} — matched in index but not found in DB\n"
                continue

            existing.title = paper["title"]
            existing.journal = journal_display
            existing.first_author = first_author
            existing.last_author = last_author
            existing.publication_date = pub_date_str
            existing.created_at = created_at
            existing.auto_ingested = True
            if display_url:
                existing.display_url = display_url
            existing.tags.clear()
            for name in tag_names:
                existing.tags.append(get_or_create_tag(db, name))
            db.commit()

            updated += 1
            yield f"[{i}/{len(papers)}] UPD   {paper['title'][:60]}\n"
            continue

        # Insert new item
        item = Item(
            url=doi_url,
            display_url=display_url,
            title=paper["title"],
            item_type="paper",
            journal=journal_display,
            first_author=first_author,
            last_author=last_author,
            publication_date=pub_date_str,
            doi=doi_url,
            submitter_id=bot_id,
            created_at=created_at,
            auto_ingested=True,
        )
        for name in tag_names:
            item.tags.append(get_or_create_tag(db, name))

        db.add(item)
        db.commit()
        db.refresh(item)

        existing_urls.add(doi_url)
        if display_url:
            existing_display.add(display_url)

        inserted += 1
        yield f"[{i}/{len(papers)}] OK    {paper['title'][:60]}\n"

    yield (
        f"\n--- Done ---\n"
        f"Inserted:        {inserted}\n"
        f"Updated:         {updated}\n"
        f"Skipped (dup):   {skipped_dup}\n"
        f"Skipped (error): {skipped_error}\n"
        f"Skipped (other): {skipped_missing}\n"
    )


@app.get("/admin/ingest", response_class=HTMLResponse)
def admin_ingest_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
):
    if not user or not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin only")
    return templates.TemplateResponse(request, "admin_ingest.html", {"user": user})


@app.post("/admin/ingest")
async def admin_ingest(
    request: Request,
    file: Optional[UploadFile] = None,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    if not user or not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin only")

    form = await request.form()
    file = form.get("file")
    update_existing = form.get("update_existing", "") in ("1", "on", "true", "yes")

    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")

    raw = await file.read()
    try:
        papers = _json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(papers, list):
        raise HTTPException(status_code=400, detail="Expected a JSON array")

    return StreamingResponse(
        _ingest_papers(papers, db, update_existing=update_existing),
        media_type="text/plain; charset=utf-8",
    )
