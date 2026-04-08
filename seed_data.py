"""
Seed demo data for RNews.
Run via:  python run.py --seed
Or:       python seed_data.py
"""
from datetime import datetime, timedelta, timezone
import random


def seed():
    from app.database import SessionLocal, init_db
    from app.models import Comment, CommentVote, Item, Tag, Vote
    from app.auth import create_user, get_user_by_email

    init_db()
    db = SessionLocal()

    # ── Users ────────────────────────────────────────────────────────────────
    users_data = [
        ("alice@example.com", "alice", "password123"),
        ("bob@example.com", "bob", "password123"),
        ("carol@example.com", "carol", "password123"),
        ("dave@example.com", "dave", "password123"),
    ]
    users = []
    for email, username, password in users_data:
        u = get_user_by_email(db, email)
        if not u:
            u = create_user(db, email=email, username=username, password=password)
        users.append(u)

    # alice is superadmin
    alice = next(u for u in users if u.username == "alice")
    if not alice.is_superadmin:
        alice.is_superadmin = True
        db.commit()

    print(f"  Users: {[u.username for u in users]}")

    # ── Tags ─────────────────────────────────────────────────────────────────
    from slugify import slugify

    def get_or_create_tag(name):
        from app.models import Tag
        slug = slugify(name.lower())
        tag = db.query(Tag).filter(Tag.slug == slug).first()
        if not tag:
            tag = Tag(name=name.lower(), slug=slug)
            db.add(tag)
            db.commit()
            db.refresh(tag)
        return tag

    tag_names = [
        "machine-learning", "genomics", "neuroscience", "climate",
        "bioinformatics", "single-cell", "protein-structure",
        "deep-learning", "CRISPR", "epidemiology", "open-science",
        "statistics", "tools", "review", "preprint",
    ]
    tags = {n: get_or_create_tag(n) for n in tag_names}

    # ── Items ─────────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    papers = [
        {
            "url": "https://doi.org/10.1038/s41586-021-03819-2",
            "title": "Highly accurate protein structure prediction with AlphaFold",
            "item_type": "paper",
            "journal": "Nature",
            "first_author": "John Jumper",
            "last_author": "Demis Hassabis",
            "publication_date": "2021-07-15",
            "tags": ["protein-structure", "deep-learning", "bioinformatics"],
            "days_ago": 1,
        },
        {
            "url": "https://doi.org/10.1126/science.abn7293",
            "title": "Transcriptional landscape of the human brain across development",
            "item_type": "paper",
            "journal": "Science",
            "first_author": "Nikolas Jorstad",
            "last_author": "Ed Lein",
            "publication_date": "2023-05-12",
            "tags": ["neuroscience", "single-cell", "genomics"],
            "days_ago": 2,
        },
        {
            "url": "https://arxiv.org/abs/2303.08774",
            "title": "GPT-4 Technical Report",
            "item_type": "paper",
            "journal": "arXiv",
            "first_author": "OpenAI",
            "last_author": "OpenAI",
            "publication_date": "2023-03-27",
            "tags": ["machine-learning", "deep-learning"],
            "days_ago": 3,
        },
        {
            "url": "https://doi.org/10.1016/j.cell.2022.09.027",
            "title": "A molecular cell atlas of the human lung",
            "item_type": "paper",
            "journal": "Cell",
            "first_author": "Kyle Travaglini",
            "last_author": "Mark Krasnow",
            "publication_date": "2022-10-13",
            "tags": ["single-cell", "genomics", "bioinformatics"],
            "days_ago": 2,
        },
        {
            "url": "https://doi.org/10.1038/s41586-023-05881-4",
            "title": "Whole-genome sequencing of 500,000 UK Biobank participants",
            "item_type": "paper",
            "journal": "Nature",
            "first_author": "Bjarni Halldorsson",
            "last_author": "Peter Donnelly",
            "publication_date": "2023-02-22",
            "tags": ["genomics", "epidemiology", "statistics"],
            "days_ago": 4,
        },
        {
            "url": "https://doi.org/10.1038/s41586-020-2649-2",
            "title": "Array programming with NumPy",
            "item_type": "paper",
            "journal": "Nature",
            "first_author": "Charles Harris",
            "last_author": "Stéfan van der Walt",
            "publication_date": "2020-09-16",
            "tags": ["tools", "statistics", "open-science"],
            "days_ago": 5,
        },
        {
            "url": "https://doi.org/10.1038/s41586-023-06221-2",
            "title": "A CRISPR screen identifies genes essential for T cell exhaustion",
            "item_type": "paper",
            "journal": "Nature",
            "first_author": "Julia Carnevale",
            "last_author": "Alexander Marson",
            "publication_date": "2023-06-07",
            "tags": ["CRISPR", "genomics"],
            "days_ago": 1,
        },
        {
            "url": "https://doi.org/10.1016/j.cell.2023.05.004",
            "title": "Spatiotemporal transcriptomic atlas of mouse organogenesis",
            "item_type": "paper",
            "journal": "Cell",
            "first_author": "Chen Qian",
            "last_author": "Guoji Guo",
            "publication_date": "2023-05-25",
            "tags": ["single-cell", "genomics", "bioinformatics"],
            "days_ago": 3,
        },
    ]

    links = [
        {
            "url": "https://github.com/fastapi/fastapi",
            "title": "FastAPI — modern Python web framework",
            "item_type": "link",
            "tags": ["tools", "open-science"],
            "days_ago": 0,
        },
        {
            "url": "https://www.nature.com/articles/d41586-023-01527-1",
            "title": "How AI image generators could help spot signs of disease",
            "item_type": "link",
            "tags": ["machine-learning", "epidemiology"],
            "days_ago": 1,
        },
        {
            "url": "",
            "title": "Interesting preprint on long COVID mechanisms — worth discussing",
            "item_type": "link",
            "tags": ["epidemiology", "review"],
            "days_ago": 2,
        },
    ]

    items_data = papers + links
    created_items = []

    for d in items_data:
        existing = None
        if d["url"]:
            from app.models import Item
            existing = db.query(Item).filter(Item.url == d["url"]).first()
        if existing:
            created_items.append(existing)
            continue

        submitter = random.choice(users)
        days_ago = d.pop("days_ago", 0)
        tag_names_list = d.pop("tags", [])
        d["url"] = d["url"] or None

        item = Item(
            **{k: v for k, v in d.items() if k not in ("tags",)},
            submitter_id=submitter.id,
            created_at=now - timedelta(days=days_ago, hours=random.randint(0, 20)),
        )
        for tn in tag_names_list:
            if tn in tags:
                item.tags.append(tags[tn])
        db.add(item)
        db.commit()
        db.refresh(item)
        created_items.append(item)

    print(f"  Items: {len(created_items)}")

    # ── Votes ─────────────────────────────────────────────────────────────────
    vote_count = 0
    for item in created_items:
        voters = random.sample(users, k=random.randint(1, len(users)))
        for voter in voters:
            existing = db.query(Vote).filter(Vote.user_id == voter.id, Vote.item_id == item.id).first()
            if not existing:
                db.add(Vote(user_id=voter.id, item_id=item.id))
                vote_count += 1
    db.commit()
    print(f"  Votes: {vote_count}")

    # ── Comments ──────────────────────────────────────────────────────────────
    comment_texts = [
        "This is a fascinating result! The methodology seems solid.",
        "I wonder how this compares to the previous work by Smith et al.",
        "Great paper but the sample size is a bit small for these conclusions.",
        "The supplementary figures are where the real data is — worth a read.",
        "Has anyone tried replicating this? Would love to see independent validation.",
        "The code is available on GitHub which is great for reproducibility.",
        "Thanks for sharing! Added to my reading list.",
        "The discussion section doesn't fully address the limitations IMO.",
        "Impressive technical contribution, though the biological interpretation is speculative.",
        "This opens up so many follow-up questions. Exciting times!",
    ]

    comment_count = 0
    for item in created_items[:5]:
        # Add 2-4 top-level comments
        for _ in range(random.randint(2, 4)):
            author = random.choice(users)
            comment = Comment(
                item_id=item.id,
                user_id=author.id,
                content=random.choice(comment_texts),
                created_at=now - timedelta(hours=random.randint(0, 48)),
            )
            db.add(comment)
            db.commit()
            db.refresh(comment)
            comment_count += 1

            # Add 0-2 replies
            for _ in range(random.randint(0, 2)):
                replier = random.choice(users)
                reply = Comment(
                    item_id=item.id,
                    user_id=replier.id,
                    parent_id=comment.id,
                    content=random.choice(comment_texts),
                    created_at=now - timedelta(hours=random.randint(0, 24)),
                )
                db.add(reply)
                db.commit()
                comment_count += 1

    print(f"  Comments: {comment_count}")
    print("\nDemo accounts:")
    for email, username, password in users_data:
        print(f"  {username} / {password}  ({email})")

    db.close()
    print("\nSeed complete!\n")


if __name__ == "__main__":
    seed()
