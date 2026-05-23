"""
CacheEntry unit tests

Scope:
- Key generation logic (deterministic + case-insensitive behavior)
- TTL expiration rules (pure model logic, no I/O)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models import CacheEntry


# ============================================================
# Key generation
# ============================================================

class TestCacheEntryKeyGeneration:

    def test_key_is_deterministic(self) -> None:
        key1 = CacheEntry.make_key("wikipedia", "photosynthesis")
        key2 = CacheEntry.make_key("wikipedia", "photosynthesis")

        assert key1 == key2

    def test_key_is_case_insensitive(self) -> None:
        assert CacheEntry.make_key("wikipedia", "PHOTO") == \
               CacheEntry.make_key("wikipedia", "photo")

    def test_key_is_source_sensitive(self) -> None:
        assert CacheEntry.make_key("wikipedia", "topic") != \
               CacheEntry.make_key("arxiv", "topic")


# ============================================================
# TTL / expiration logic
# ============================================================

class TestCacheEntryExpiration:

    def test_new_entry_is_valid(self) -> None:
        entry = CacheEntry(key="k", value=[], ttl_seconds=3600)
        assert not entry.is_expired()

    def test_zero_ttl_never_expires(self) -> None:
        entry = CacheEntry(key="k", value=[], ttl_seconds=0)
        assert not entry.is_expired()

    def test_entry_expires_after_ttl(self) -> None:
        """
        Entry created more than one hour ago with TTL=1h should be expired.
        """
        entry = CacheEntry(
            key="k",
            value=[],
            ttl_seconds=3600,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        assert entry.is_expired()

    def test_entry_still_valid_within_ttl(self) -> None:
        """
        Entry created less than one hour ago with TTL=1h should still be valid.
        """
        entry = CacheEntry(
            key="k",
            value=[],
            ttl_seconds=3600,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )

        assert not entry.is_expired()
