"""Tests for DB-level operations: tag creation, dedup, item properties."""

import pytest
from app.ingest_utils import get_or_create_tag
from app.models import Item, ItemTag, Tag


# ── get_or_create_tag ─────────────────────────────────────────────────────────

class TestGetOrCreateTag:
    def test_creates_new_tag(self, db):
        tag = get_or_create_tag(db, "microbiome")
        assert tag.id is not None
        assert tag.name == "microbiome"
        assert tag.slug == "microbiome"

    def test_deduplicates(self, db):
        t1 = get_or_create_tag(db, "microbiome")
        t2 = get_or_create_tag(db, "microbiome")
        assert t1.id == t2.id

    def test_normalizes_case(self, db):
        t1 = get_or_create_tag(db, "Microbiome")
        t2 = get_or_create_tag(db, "microbiome")
        assert t1.id == t2.id

    def test_strips_whitespace(self, db):
        t1 = get_or_create_tag(db, "  amr  ")
        t2 = get_or_create_tag(db, "amr")
        assert t1.id == t2.id

    def test_slug_for_multi_word(self, db):
        tag = get_or_create_tag(db, "clinical trial")
        assert tag.slug == "clinical-trial"


# ── Item.display_tags ─────────────────────────────────────────────────────────

class TestDisplayTags:
    def _make_item(self, db, regular_user):
        item = Item(title="Test paper", submitter_id=regular_user.id)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def test_shows_tags_above_threshold(self, db, regular_user):
        item = self._make_item(db, regular_user)
        tag = get_or_create_tag(db, "microbiome")
        db.add(ItemTag(item_id=item.id, tag_id=tag.id, vote_count=10))
        db.commit()
        db.refresh(item)
        assert any(t.name == "microbiome" for t in item.display_tags)

    def test_hides_tags_below_threshold(self, db, regular_user):
        item = self._make_item(db, regular_user)
        tag = get_or_create_tag(db, "lowvote")
        db.add(ItemTag(item_id=item.id, tag_id=tag.id, vote_count=4))
        db.commit()
        db.refresh(item)
        assert not any(t.name == "lowvote" for t in item.display_tags)

    def test_threshold_boundary(self, db, regular_user):
        item = self._make_item(db, regular_user)
        tag = get_or_create_tag(db, "boundary")
        db.add(ItemTag(item_id=item.id, tag_id=tag.id, vote_count=5))
        db.commit()
        db.refresh(item)
        assert any(t.name == "boundary" for t in item.display_tags)

    def test_capped_at_five(self, db, regular_user):
        item = self._make_item(db, regular_user)
        for name in ["a", "b", "c", "d", "e", "f"]:
            tag = get_or_create_tag(db, name)
            db.add(ItemTag(item_id=item.id, tag_id=tag.id, vote_count=10))
        db.commit()
        db.refresh(item)
        assert len(item.display_tags) == 5

    def test_sorted_by_vote_count(self, db, regular_user):
        item = self._make_item(db, regular_user)
        for name, vc in [("low", 6), ("high", 14), ("mid", 10)]:
            tag = get_or_create_tag(db, name)
            db.add(ItemTag(item_id=item.id, tag_id=tag.id, vote_count=vc))
        db.commit()
        db.refresh(item)
        names = [t.name for t in item.display_tags]
        assert names == ["high", "mid", "low"]


# ── Item.domain ───────────────────────────────────────────────────────────────

class TestItemDomain:
    def test_from_display_url(self, db, regular_user):
        item = Item(
            title="Test",
            submitter_id=regular_user.id,
            url="https://doi.org/10.1234/x",
            display_url="https://www.nature.com/articles/x",
        )
        db.add(item)
        db.commit()
        assert item.domain == "nature.com"

    def test_falls_back_to_url(self, db, regular_user):
        item = Item(
            title="Test",
            submitter_id=regular_user.id,
            url="https://www.science.org/doi/x",
        )
        db.add(item)
        db.commit()
        assert item.domain == "science.org"

    def test_strips_www(self, db, regular_user):
        item = Item(
            title="Test",
            submitter_id=regular_user.id,
            url="https://www.cell.com/article/x",
        )
        db.add(item)
        db.commit()
        assert item.domain == "cell.com"
