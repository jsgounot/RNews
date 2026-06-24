from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey,
    Boolean, UniqueConstraint, Table
)
from sqlalchemy.orm import relationship
from app.database import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Many-to-many: items <-> tags
item_tags = Table(
    "item_tags",
    Base.metadata,
    Column("item_id", Integer, ForeignKey("items.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(64), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow)
    is_active = Column(Boolean, default=True)
    is_superadmin = Column(Boolean, default=False, nullable=False)
    auto_upvote_on_favorite = Column(Boolean, default=True, nullable=False)

    items = relationship("Item", foreign_keys="[Item.submitter_id]", back_populates="submitter")
    votes = relationship("Vote", back_populates="user")
    comments = relationship("Comment", back_populates="author")
    comment_votes = relationship("CommentVote", back_populates="user")
    saved_tags = relationship("SavedTag", back_populates="user", cascade="all, delete-orphan")
    favorite_items = relationship("FavoriteItem", back_populates="user", cascade="all, delete-orphan")
    team_memberships = relationship("TeamMember", back_populates="user", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), unique=True, index=True, nullable=False)
    slug = Column(String(64), unique=True, index=True, nullable=False)

    items = relationship("Item", secondary=item_tags, back_populates="tags")
    saved_by = relationship("SavedTag", back_populates="tag", cascade="all, delete-orphan")


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(Text, nullable=True)
    title = Column(String(512), nullable=False)
    item_type = Column(String(16), nullable=False, default="link")  # "paper" or "link"

    # Paper-specific fields
    journal = Column(String(256), nullable=True)
    first_author = Column(String(256), nullable=True)
    last_author = Column(String(256), nullable=True)
    publication_date = Column(String(32), nullable=True)
    doi = Column(String(256), nullable=True)

    # Resolved endpoint URL for display (populated at submit time for DOI URLs)
    display_url = Column(Text, nullable=True)

    # If True, this item was submitted directly to a team and won't appear on main feed
    is_team_only = Column(Boolean, default=False, nullable=False)

    # If True, this item was ingested automatically (robot icon displayed)
    auto_ingested = Column(Boolean, default=False, nullable=False)

    # Optional: this item is a follow-up of another item
    follow_up_of = Column(Integer, ForeignKey("items.id"), nullable=True)

    submitter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow, index=True)

    # Edit tracking
    last_edited_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_edited_at = Column(DateTime, nullable=True)

    submitter = relationship("User", foreign_keys=[submitter_id], back_populates="items")
    editor = relationship("User", foreign_keys=[last_edited_by])
    tags = relationship("Tag", secondary=item_tags, back_populates="items")
    votes = relationship("Vote", back_populates="item", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="item", cascade="all, delete-orphan")
    team_items = relationship("TeamItem", back_populates="item", cascade="all, delete-orphan")
    favorited_by = relationship("FavoriteItem", back_populates="item", cascade="all, delete-orphan")
    # Self-referential: follow-ups of this item
    follow_ups = relationship(
        "Item",
        foreign_keys="[Item.follow_up_of]",
        back_populates="parent_item",
    )
    parent_item = relationship(
        "Item",
        foreign_keys="[Item.follow_up_of]",
        back_populates="follow_ups",
        remote_side="Item.id",
    )

    @property
    def score(self):
        return len(self.votes)

    @property
    def comment_count(self):
        return len(self.comments)

    @property
    def domain(self):
        source = self.display_url or self.url
        if not source:
            return None
        from urllib.parse import urlparse
        try:
            parsed = urlparse(source)
            return parsed.netloc.replace("www.", "")
        except Exception:
            return None


class Vote(Base):
    __tablename__ = "votes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "item_id"),)

    user = relationship("User", back_populates="votes")
    item = relationship("Item", back_populates="votes")


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("comments.id"), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    item = relationship("Item", back_populates="comments")
    author = relationship("User", back_populates="comments")
    parent = relationship("Comment", remote_side="Comment.id", back_populates="children")
    children = relationship("Comment", back_populates="parent")
    votes = relationship("CommentVote", back_populates="comment", cascade="all, delete-orphan")

    @property
    def score(self):
        return len(self.votes)


class CommentVote(Base):
    __tablename__ = "comment_votes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "comment_id"),)

    user = relationship("User", back_populates="comment_votes")
    comment = relationship("Comment", back_populates="votes")


# ── Favorite items ────────────────────────────────────────────────────────────

class FavoriteItem(Base):
    __tablename__ = "favorite_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    saved_at = Column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "item_id"),)

    user = relationship("User", back_populates="favorite_items")
    item = relationship("Item", back_populates="favorited_by")


# ── Saved tags ────────────────────────────────────────────────────────────────

class SavedTag(Base):
    __tablename__ = "saved_tags"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False)
    saved_at = Column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "tag_id"),)

    user = relationship("User", back_populates="saved_tags")
    tag = relationship("Tag", back_populates="saved_by")


# ── Teams ─────────────────────────────────────────────────────────────────────

class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    slug = Column(String(160), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    is_public = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    creator = relationship("User", foreign_keys=[created_by])
    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    items = relationship("TeamItem", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(16), nullable=False, default="contributor")  # admin/contributor/viewer
    joined_at = Column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("team_id", "user_id"),)

    team = relationship("Team", back_populates="members")
    user = relationship("User", back_populates="team_memberships")


class TeamItem(Base):
    __tablename__ = "team_items"

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    added_at = Column(DateTime, default=utcnow)
    source = Column(String(16), nullable=False, default="submitted")  # "submitted" or "shared"

    __table_args__ = (UniqueConstraint("team_id", "item_id"),)

    team = relationship("Team", back_populates="items")
    item = relationship("Item", back_populates="team_items")
    adder = relationship("User", foreign_keys=[added_by])
