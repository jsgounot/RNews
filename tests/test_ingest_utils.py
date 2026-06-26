"""Tests for pure functions in app/ingest_utils.py."""

import pytest
from datetime import date

from app.ingest_utils import normalize_doi_url, tag_vote_count, best_pub_date


# ── normalize_doi_url ─────────────────────────────────────────────────────────

class TestNormalizeDoiUrl:
    def test_already_canonical(self):
        url = "https://doi.org/10.1234/test"
        assert normalize_doi_url(url) == url

    def test_http_doi_org(self):
        assert normalize_doi_url("http://doi.org/10.1234/test") == "https://doi.org/10.1234/test"

    def test_dx_doi_org(self):
        assert normalize_doi_url("https://dx.doi.org/10.1234/test") == "https://doi.org/10.1234/test"

    def test_doi_colon_prefix(self):
        assert normalize_doi_url("doi:10.1234/test") == "https://doi.org/10.1234/test"

    def test_bare_doi(self):
        assert normalize_doi_url("10.1234/test") == "https://doi.org/10.1234/test"

    def test_non_doi_url_unchanged(self):
        url = "https://www.nature.com/articles/s41586-024-001"
        assert normalize_doi_url(url) == url

    def test_empty_string(self):
        assert normalize_doi_url("") == ""

    def test_whitespace_stripped(self):
        assert normalize_doi_url("  10.1234/test  ") == "https://doi.org/10.1234/test"


# ── tag_vote_count ────────────────────────────────────────────────────────────

class TestTagVoteCount:
    def test_numeric_mid(self):
        assert tag_vote_count({"confidence": 5}) == 10

    def test_numeric_zero(self):
        assert tag_vote_count({"confidence": 0}) == 5

    def test_numeric_ten(self):
        assert tag_vote_count({"confidence": 10}) == 15

    def test_numeric_clamped_above(self):
        assert tag_vote_count({"confidence": 99}) == 15

    def test_numeric_clamped_below(self):
        assert tag_vote_count({"confidence": -5}) == 5

    def test_string_high(self):
        assert tag_vote_count({"confidence": "high"}) == 15

    def test_string_medium(self):
        assert tag_vote_count({"confidence": "medium"}) == 10

    def test_string_low(self):
        assert tag_vote_count({"confidence": "low"}) == 5

    def test_string_case_insensitive(self):
        assert tag_vote_count({"confidence": "HIGH"}) == 15

    def test_string_unknown(self):
        assert tag_vote_count({"confidence": "unknown"}) == 10

    def test_missing_confidence(self):
        assert tag_vote_count({}) == 10

    def test_none_confidence(self):
        assert tag_vote_count({"confidence": None}) == 10


# ── best_pub_date ─────────────────────────────────────────────────────────────

class TestBestPubDate:
    def test_full_date(self):
        assert best_pub_date({"pub_date": "2024 Jun 9"}) == date(2024, 6, 9)

    def test_year_month(self):
        assert best_pub_date({"pub_date": "2024 Feb"}) == date(2024, 2, 1)

    def test_falls_back_to_epub(self):
        result = best_pub_date({"pub_date": "", "epub_date": "20240615"})
        assert result == date(2024, 6, 15)

    def test_rejects_dec31_placeholder(self):
        # Dec 31 is a common PubMed placeholder — should be rejected
        result = best_pub_date({"pub_date": "2024 Dec 31", "epub_date": "20240601"})
        assert result == date(2024, 6, 1)

    def test_rejects_future_date(self):
        result = best_pub_date({"pub_date": "2099 Jan 1"})
        assert result is None

    def test_empty(self):
        assert best_pub_date({}) is None

    def test_iso_format(self):
        assert best_pub_date({"pub_date": "2024-03-15"}) == date(2024, 3, 15)
