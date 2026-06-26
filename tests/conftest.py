"""
Shared fixtures for the RNews test suite.

Run from the project root so StaticFiles can find app/static:
    conda run -n rnews python -m pytest
"""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure working directory is the project root (needed for StaticFiles + journals.json)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, BOT_EMAIL, BOT_USERNAME  # noqa: E402


@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def db(db_engine):
    Session = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(scope="function")
def bot_user(db):
    import bcrypt
    from app.models import User

    hashed = bcrypt.hashpw(b"", bcrypt.gensalt()).decode()
    user = User(email=BOT_EMAIL, username=BOT_USERNAME, hashed_password=hashed)
    db.add(user)
    db.commit()
    return user


@pytest.fixture(scope="function")
def regular_user(db):
    import bcrypt
    from app.models import User

    hashed = bcrypt.hashpw(b"password", bcrypt.gensalt()).decode()
    user = User(email="alice@example.com", username="alice", hashed_password=hashed)
    db.add(user)
    db.commit()
    return user
