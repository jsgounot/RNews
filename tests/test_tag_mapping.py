"""Tests for tag mapping seeding and applying to existing DB tags."""

import pytest
from app.ingest_utils import get_or_create_tag
from app.models import Item, ItemTag, ItemTagVote, Tag, TagMapping


def _consume(gen):
    """Exhaust a generator, return concatenated output."""
    return "".join(gen)


def _make_item(db, user):
    item = Item(title="Test paper", submitter_id=user.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ── _seed_tag_mappings ────────────────────────────────────────────────────────

class TestSeedTagMappings:
    def test_inserts_new_entries(self, db):
        from app.main import _seed_tag_mappings

        mapping = {"antivirals": "antiviral", "biofilms": "biofilm"}
        _consume(_seed_tag_mappings(mapping, db))

        rows = db.query(TagMapping).all()
        assert {r.raw_tag for r in rows} == {"antivirals", "biofilms"}
        assert db.query(TagMapping).filter_by(raw_tag="antivirals").one().clean_tag == "antiviral"

    def test_updates_existing_entry(self, db):
        from app.main import _seed_tag_mappings

        db.add(TagMapping(raw_tag="amr", clean_tag="old_value"))
        db.commit()

        _consume(_seed_tag_mappings({"amr": "antimicrobial-resistance"}, db))

        row = db.query(TagMapping).filter_by(raw_tag="amr").one()
        assert row.clean_tag == "antimicrobial-resistance"

    def test_accepts_null_clean_tag(self, db):
        from app.main import _seed_tag_mappings

        _consume(_seed_tag_mappings({"junk": None}, db))

        row = db.query(TagMapping).filter_by(raw_tag="junk").one()
        assert row.clean_tag is None

    def test_skips_empty_key(self, db):
        from app.main import _seed_tag_mappings

        _consume(_seed_tag_mappings({"": "something", "valid": "valid"}, db))

        rows = db.query(TagMapping).all()
        assert len(rows) == 1
        assert rows[0].raw_tag == "valid"


# ── _apply_mapping_to_existing ────────────────────────────────────────────────

class TestApplyMappingToExisting:
    def test_identity_mapping_is_noop(self, db, regular_user):
        from app.main import _apply_mapping_to_existing

        tag = get_or_create_tag(db, "microbiome")
        item = _make_item(db, regular_user)
        db.add(ItemTag(item_id=item.id, tag_id=tag.id, vote_count=10))
        db.commit()

        out = _consume(_apply_mapping_to_existing({"microbiome": "microbiome"}, db))
        assert "nothing to apply" in out

        # Tag and ItemTag untouched
        assert db.query(Tag).filter_by(name="microbiome").count() == 1
        assert db.query(ItemTag).filter_by(tag_id=tag.id).count() == 1

    def test_remaps_item_tag(self, db, regular_user):
        from app.main import _apply_mapping_to_existing

        old_tag = get_or_create_tag(db, "antivirals")
        item = _make_item(db, regular_user)
        db.add(ItemTag(item_id=item.id, tag_id=old_tag.id, vote_count=10))
        db.commit()

        _consume(_apply_mapping_to_existing({"antivirals": "antiviral"}, db))

        # Old tag removed, new tag created
        assert db.query(Tag).filter_by(name="antivirals").count() == 0
        new_tag = db.query(Tag).filter_by(name="antiviral").first()
        assert new_tag is not None
        assert db.query(ItemTag).filter_by(tag_id=new_tag.id, item_id=item.id).count() == 1

    def test_merge_keeps_max_vote_count(self, db, regular_user):
        from app.main import _apply_mapping_to_existing

        old_tag = get_or_create_tag(db, "antivirals")
        new_tag = get_or_create_tag(db, "antiviral")
        item = _make_item(db, regular_user)
        db.add(ItemTag(item_id=item.id, tag_id=old_tag.id, vote_count=8))
        db.add(ItemTag(item_id=item.id, tag_id=new_tag.id, vote_count=12))
        db.commit()

        _consume(_apply_mapping_to_existing({"antivirals": "antiviral"}, db))

        it = db.query(ItemTag).filter_by(tag_id=new_tag.id, item_id=item.id).one()
        assert it.vote_count == 12  # max(8, 12)

    def test_discard_removes_item_tag_and_tag(self, db, regular_user):
        from app.main import _apply_mapping_to_existing

        tag = get_or_create_tag(db, "junkterm")
        item = _make_item(db, regular_user)
        db.add(ItemTag(item_id=item.id, tag_id=tag.id, vote_count=10))
        db.commit()

        _consume(_apply_mapping_to_existing({"junkterm": None}, db))

        assert db.query(Tag).filter_by(name="junkterm").count() == 0
        assert db.query(ItemTag).count() == 0

    def test_missing_tag_in_db_is_skipped(self, db):
        from app.main import _apply_mapping_to_existing

        # Mapping references a tag that was never inserted — should not crash
        out = _consume(_apply_mapping_to_existing({"nonexistent": "other"}, db))
        assert "done" in out.lower() or "apply" in out.lower()

    def test_deletes_item_tag_votes_on_remap(self, db, regular_user):
        from app.main import _apply_mapping_to_existing

        old_tag = get_or_create_tag(db, "antivirals")
        item = _make_item(db, regular_user)
        db.add(ItemTag(item_id=item.id, tag_id=old_tag.id, vote_count=10))
        db.add(ItemTagVote(
            user_id=regular_user.id, item_id=item.id, tag_id=old_tag.id, direction=1
        ))
        db.commit()

        _consume(_apply_mapping_to_existing({"antivirals": "antiviral"}, db))

        # Stale votes for old tag are removed
        assert db.query(ItemTagVote).filter_by(tag_id=old_tag.id).count() == 0
