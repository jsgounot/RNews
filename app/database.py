import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# LOCAL default is SQLite; on Railway/Render set DATABASE_URL to the Postgres connection string.
# Railway provides postgres:// but SQLAlchemy 2.0 requires postgresql://.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./rnews.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite needs check_same_thread=False; Postgres does not accept that kwarg.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

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
    # SQLite-only: patch columns added after the original schema was created.
    # On Postgres (fresh database) create_all already includes every column.
    if DATABASE_URL.startswith("sqlite"):
        _migrate_sqlite()


def _migrate_sqlite():
    """Apply any missing columns to an existing SQLite database."""
    migrations = [
        "ALTER TABLE items ADD COLUMN is_team_only INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE items ADD COLUMN follow_up_of INTEGER REFERENCES items(id)",
        "ALTER TABLE users ADD COLUMN is_superadmin INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE items ADD COLUMN last_edited_by INTEGER REFERENCES users(id)",
        "ALTER TABLE items ADD COLUMN last_edited_at TEXT",
    ]
    import sqlalchemy
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(sqlalchemy.text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists
