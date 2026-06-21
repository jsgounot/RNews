import os

import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# LOCAL default is SQLite; on Railway/Render set DATABASE_URL to the Postgres connection string.
# Railway provides postgres:// but SQLAlchemy 2.0 requires postgresql://.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./rnews.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# SQLite needs check_same_thread=False; Postgres does not accept that kwarg.
connect_args = {"check_same_thread": False} if IS_SQLITE else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """Apply any missing columns to an existing database.

    Each entry is a tuple of (sqlite_sql, postgres_sql). If both dialects use
    the same syntax, a plain string is accepted too.
    The try/except swallows 'column already exists' errors on both engines.
    """
    migrations = [
        "ALTER TABLE items ADD COLUMN is_team_only INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE items ADD COLUMN follow_up_of INTEGER REFERENCES items(id)",
        "ALTER TABLE users ADD COLUMN is_superadmin INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE items ADD COLUMN last_edited_by INTEGER REFERENCES users(id)",
        "ALTER TABLE items ADD COLUMN last_edited_at TEXT",
        (
            "ALTER TABLE users ADD COLUMN auto_upvote_on_favorite INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE users ADD COLUMN auto_upvote_on_favorite BOOLEAN NOT NULL DEFAULT TRUE",
        ),
        "ALTER TABLE items ADD COLUMN display_url TEXT",
    ]

    with engine.connect() as conn:
        for entry in migrations:
            if isinstance(entry, tuple):
                sql = entry[0] if IS_SQLITE else entry[1]
            else:
                sql = entry
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()
                pass  # column already exists — safe to ignore
