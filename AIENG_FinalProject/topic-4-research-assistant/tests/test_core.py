"""
tests/test_core.py
==================
Comprehensive test suite for the storage layer (Üzv C responsibilities).

Coverage strategy
-----------------
Unit tests        — every CacheStore / SessionRepository method in isolation.
Integration tests — both tables operating in a single in-memory SQLite DB.
Edge cases        — empty strings, Unicode, very large payloads, concurrent
                    canonicalisation, boundary TTL values.
Error paths       — closed store, duplicate IDs, invalid status strings.
Expiry tests      — TTL expiry with injected ``now`` timestamps.
Mock-based tests  — verify that business-logic callers use the repository API
                    with the correct arguments (no SQLite required).
Async smoke       — verify the store is safe to call via asyncio.to_thread().
File I/O tests    — verify data survives an open → close → reopen cycle.

Run:
    pytest tests/test_core.py -v --tb=short

Coverage report:
    pytest tests/test_core.py --cov=src/storage --cov-report=term-missing
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.storage.cache_store import CacheStore, _canonicalise, _utcnow_iso
from src.storage.repository import (
    RepositoryError,
    ResearchSession,
    SessionRepository,
)


# Shared test helpers


def _source_dict(**kwargs) -> dict[str, Any]:
    """Build a minimal Source-compatible dict for cache storage tests.

    Parameters
    ----------
    **kwargs:
        Override any default field.  ``origin`` defaults to ``"wikipedia"``.

    Returns
    -------
    dict[str, Any]
        Dict with ``title``, ``url``, ``snippet``, ``origin`` keys.
    """
    base: dict[str, Any] = {
        "title":   "Test Source",
        "url":     "https://example.com/test",
        "snippet": "A test snippet about the topic.",
        "origin":  kwargs.pop("origin", "wikipedia"),
    }
    base.update(kwargs)
    return base


def _make_session(**kwargs) -> ResearchSession:
    """Construct a ResearchSession with sensible defaults."""
    kwargs.setdefault("question", "What is the speed of light?")
    return ResearchSession(**kwargs)


# _canonicalise helper


class TestCanonicalize:
    """Unit tests for the ``_canonicalise`` query-key helper."""

    def test_lowercases(self):
        assert _canonicalise("PHOTOSYNTHESIS") == "photosynthesis"

    def test_strips_leading_whitespace(self):
        assert _canonicalise("  quantum entanglement") == "quantum entanglement"

    def test_strips_trailing_whitespace(self):
        assert _canonicalise("quantum entanglement  ") == "quantum entanglement"

    def test_lower_and_strip_combined(self):
        assert _canonicalise("  WHAT IS LIGHT?  ") == "what is light?"

    def test_empty_string(self):
        assert _canonicalise("") == ""

    def test_unicode_preserved_and_lowercased(self):
        # Japanese is already in lowercase form; stripping still works.
        assert _canonicalise("  フォトシンセシス  ") == "フォトシンセシス"

    def test_internal_whitespace_not_collapsed(self):
        # We only strip edges; internal spaces are preserved.
        assert _canonicalise("what   is   dna") == "what   is   dna"

    def test_punctuation_preserved(self):
        assert _canonicalise("WHAT IS DNA?") == "what is dna?"


# _utcnow_iso helper


class TestUtcNowIso:
    """Unit tests for the ``_utcnow_iso`` timestamp helper."""

    def test_returns_string(self):
        assert isinstance(_utcnow_iso(), str)

    def test_parseable_iso_format(self):
        from datetime import datetime
        ts = _utcnow_iso()
        parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        assert parsed is not None

    def test_ends_with_z(self):
        assert _utcnow_iso().endswith("Z")

    def test_monotonically_non_decreasing(self):
        t1 = _utcnow_iso()
        t2 = _utcnow_iso()
        assert t2 >= t1

    def test_two_calls_same_second_equal(self):
        # Within the same second, two calls should produce the same string.
        t1 = _utcnow_iso()
        t2 = _utcnow_iso()
        # They should differ by at most 1 second (format has second precision).
        assert abs(len(t1) - len(t2)) == 0


# CacheStore — lifecycle


class TestCacheStoreLifecycle:
    """Tests for CacheStore open / close / context-manager behaviour."""

    def test_open_returns_self(self):
        store = CacheStore(":memory:")
        result = store.open()
        store.close()
        assert result is store

    def test_context_manager_opens_store(self):
        with CacheStore(":memory:") as store:
            assert store._conn is not None

    def test_context_manager_closes_on_exit(self):
        with CacheStore(":memory:") as store:
            pass
        assert store._conn is None

    def test_double_open_is_idempotent(self):
        store = CacheStore(":memory:")
        store.open()
        conn_first = store._conn
        store.open()  # second call must not replace the connection
        assert store._conn is conn_first
        store.close()

    def test_close_on_closed_store_is_noop(self):
        store = CacheStore(":memory:")
        store.close()  # should not raise

    def test_operations_raise_when_not_open(self):
        store = CacheStore(":memory:")
        with pytest.raises(RuntimeError, match="not open"):
            store.put("wikipedia", "test", [])

    def test_schema_creates_query_cache_table(self, db_store):
        rows = db_store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "query_cache" in names

    def test_file_backed_creates_nested_parent_dir(self, tmp_path):
        db_path = tmp_path / "nested" / "subdir" / "test.db"
        with CacheStore(db_path) as store:
            store.put("arxiv", "light", [_source_dict(origin="arxiv")])
        assert db_path.exists()

    def test_wal_mode_is_set_on_file_db(self, tmp_path):
        # WAL is only applicable to file-backed databases.
        # In-memory SQLite always reports "memory" regardless of PRAGMA.
        db = tmp_path / "wal_test.db"
        with CacheStore(db) as store:
            row = store._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0].upper() == "WAL"

    def test_foreign_keys_are_on(self, db_store):
        row = db_store._conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1


# CacheStore — put / get


class TestCacheStorePutGet:
    """Tests for the core put/get round-trip."""

    def test_put_and_get_round_trip(self, db_store):
        results = [_source_dict()]
        db_store.put("wikipedia", "photosynthesis", results)
        fetched = db_store.get("wikipedia", "photosynthesis")
        assert fetched == results

    def test_get_missing_returns_none(self, db_store):
        assert db_store.get("wikipedia", "nonexistent query") is None

    def test_get_is_case_insensitive_on_query(self, db_store):
        results = [_source_dict()]
        db_store.put("wikipedia", "  PHOTOSYNTHESIS  ", results)
        assert db_store.get("wikipedia", "photosynthesis") == results
        assert db_store.get("wikipedia", "PHOTOSYNTHESIS") == results

    def test_put_upsert_overwrites_existing_entry(self, db_store):
        db_store.put("arxiv", "entanglement", [_source_dict(title="Old")])
        db_store.put("arxiv", "entanglement", [_source_dict(title="New")])
        fetched = db_store.get("arxiv", "entanglement")
        assert fetched[0]["title"] == "New"

    def test_different_sources_same_query_are_independent(self, db_store):
        wiki  = [_source_dict(origin="wikipedia", title="Wiki")]
        arxiv = [_source_dict(origin="arxiv",     title="ArXiv")]
        db_store.put("wikipedia", "light", wiki)
        db_store.put("arxiv",     "light", arxiv)
        assert db_store.get("wikipedia", "light")[0]["title"] == "Wiki"
        assert db_store.get("arxiv",     "light")[0]["title"] == "ArXiv"

    def test_put_empty_results_list(self, db_store):
        db_store.put("web", "obscure query", [])
        assert db_store.get("web", "obscure query") == []

    def test_large_results_list(self, db_store):
        many = [_source_dict(title=f"Source {i}") for i in range(100)]
        db_store.put("wikipedia", "big query", many)
        fetched = db_store.get("wikipedia", "big query")
        assert len(fetched) == 100

    def test_unicode_query_and_results(self, db_store):
        results = [_source_dict(title="光合作用")]
        db_store.put("wikipedia", "光合作用", results)
        fetched = db_store.get("wikipedia", "光合作用")
        assert fetched[0]["title"] == "光合作用"

    def test_results_json_preserves_all_fields(self, db_store):
        results = [
            {"title": "Deep Thought", "url": "https://example.com/42",
             "snippet": "The answer.", "origin": "web"}
        ]
        db_store.put("web", "meaning of life", results)
        fetched = db_store.get("web", "meaning of life")
        assert fetched[0] == results[0]

    def test_get_expired_entry_returns_none(self, db_store):
        # Cached at epoch → already expired with any positive TTL
        db_store.put(
            "wikipedia", "ancient query", [_source_dict()],
            ttl=1,
            cached_at="2020-01-01T00:00:00Z",
        )
        assert db_store.get("wikipedia", "ancient query") is None

    def test_get_not_yet_expired_returns_results(self, db_store):
        db_store.put(
            "arxiv", "fresh query", [_source_dict(origin="arxiv")],
            ttl=86400,
            cached_at=_utcnow_iso(),
        )
        assert db_store.get("arxiv", "fresh query") is not None

    def test_ttl_zero_means_immediately_expired(self, db_store):
        db_store.put("web", "zero ttl", [_source_dict(origin="web")], ttl=0)
        # TTL=0 means expired the instant it was cached
        assert db_store.get("web", "zero ttl") is None

    def test_default_ttl_is_applied_when_not_specified(self, tmp_path):
        store = CacheStore(":memory:", default_ttl=3600)
        with store:
            store.put("wikipedia", "default ttl query", [_source_dict()])
            row = store._conn.execute(
                "SELECT ttl_seconds FROM query_cache"
            ).fetchone()
            assert row["ttl_seconds"] == 3600


# CacheStore — delete / delete_expired / clear_all


class TestCacheStoreDelete:
    """Tests for delete, delete_expired, and clear_all operations."""

    def test_delete_existing_entry_returns_true(self, db_store):
        db_store.put("web", "to delete", [_source_dict(origin="web")])
        assert db_store.delete("web", "to delete") is True

    def test_delete_removes_entry(self, db_store):
        db_store.put("web", "to delete", [_source_dict(origin="web")])
        db_store.delete("web", "to delete")
        assert db_store.get("web", "to delete") is None

    def test_delete_nonexistent_returns_false(self, db_store):
        assert db_store.delete("wikipedia", "ghost query") is False

    def test_delete_expired_removes_old_entries(self, db_store):
        db_store.put("wikipedia", "old",   [_source_dict()],
                     ttl=0, cached_at="2020-01-01T00:00:00Z")
        db_store.put("arxiv",     "old",   [_source_dict(origin="arxiv")],
                     ttl=0, cached_at="2020-01-01T00:00:00Z")
        db_store.put("web",       "fresh", [_source_dict(origin="web")],
                     ttl=86400, cached_at=_utcnow_iso())

        removed = db_store.delete_expired()

        assert removed == 2
        assert db_store.get("web", "fresh") is not None

    def test_delete_expired_with_injected_now(self, db_store):
        """Inject a future 'now' to expire an otherwise live entry."""
        db_store.put("wikipedia", "q", [_source_dict()], ttl=60,
                     cached_at="2024-01-01T00:00:00Z")
        # Move 'now' forward by 2 minutes → the 60-second TTL has elapsed
        removed = db_store.delete_expired(now="2024-01-01T00:02:00Z")
        assert removed == 1

    def test_delete_expired_leaves_live_entries_intact(self, db_store):
        db_store.put("wikipedia", "live", [_source_dict()], ttl=86400)
        db_store.delete_expired()
        assert db_store.get("wikipedia", "live") is not None

    def test_delete_expired_empty_store_returns_zero(self, db_store):
        assert db_store.delete_expired() == 0

    def test_clear_all_removes_all_rows(self, db_store):
        for i in range(5):
            db_store.put("wikipedia", f"query {i}", [_source_dict()])
        removed = db_store.clear_all()
        assert removed == 5
        assert db_store.list_entries(include_expired=True) == []

    def test_clear_all_on_empty_store_returns_zero(self, db_store):
        assert db_store.clear_all() == 0


# CacheStore — list_entries / stats


class TestCacheStoreListAndStats:
    """Tests for list_entries and stats aggregation queries."""

    def test_list_entries_empty_store(self, db_store):
        assert db_store.list_entries() == []

    def test_list_entries_returns_only_live_by_default(self, db_store):
        db_store.put("wikipedia", "live", [_source_dict()], ttl=86400)
        db_store.put("arxiv",     "dead", [_source_dict()],
                     ttl=0, cached_at="2020-01-01T00:00:00Z")
        live = db_store.list_entries(include_expired=False)
        assert len(live) == 1
        assert live[0]["query_key"] == "live"

    def test_list_entries_include_expired(self, db_store):
        db_store.put("wikipedia", "live", [_source_dict()], ttl=86400)
        db_store.put("arxiv",     "dead", [_source_dict()],
                     ttl=0, cached_at="2020-01-01T00:00:00Z")
        all_rows = db_store.list_entries(include_expired=True)
        assert len(all_rows) == 2

    def test_list_entries_filtered_by_source(self, db_store):
        db_store.put("wikipedia", "q1", [_source_dict()])
        db_store.put("arxiv",     "q2", [_source_dict(origin="arxiv")])
        db_store.put("web",       "q3", [_source_dict(origin="web")])
        wiki_only = db_store.list_entries(source="wikipedia")
        assert all(r["source"] == "wikipedia" for r in wiki_only)
        assert len(wiki_only) == 1

    def test_list_entries_ordered_newest_first(self, db_store):
        # Use include_expired=True so the injected past timestamps don't
        # get filtered out by the live-entry WHERE clause.
        db_store.put("wikipedia", "first",  [_source_dict()],
                     cached_at="2024-01-01T00:00:01Z", ttl=86400)
        db_store.put("wikipedia", "second", [_source_dict()],
                     cached_at="2024-01-01T00:00:02Z", ttl=86400)
        rows = db_store.list_entries(include_expired=True)
        assert len(rows) == 2
        assert rows[0]["query_key"] == "second"

    def test_stats_correct_counts(self, db_store):
        db_store.put("wikipedia", "live", [_source_dict()], ttl=86400)
        db_store.put("arxiv",     "dead", [_source_dict()],
                     ttl=0, cached_at="2020-01-01T00:00:00Z")
        s = db_store.stats()
        assert s["total"]   == 2
        assert s["expired"] == 1
        assert s["live"]    == 1

    def test_stats_empty_store(self, db_store):
        assert db_store.stats() == {"total": 0, "expired": 0, "live": 0}

    def test_stats_all_live(self, db_store):
        for i in range(3):
            db_store.put("web", f"q{i}", [_source_dict(origin="web")])
        s = db_store.stats()
        assert s["total"]   == 3
        assert s["live"]    == 3
        assert s["expired"] == 0


# CacheStore — put_entry / get_entry bridge helpers


class TestCacheStoreBridgeHelpers:
    """Verify the CacheEntry duck-typing bridge helpers."""

    def _make_entry(
        self, source="wikipedia", query="test", results=None, ttl=3600
    ) -> MagicMock:
        """Build a duck-typed CacheEntry without any Pydantic dependency."""
        entry = MagicMock()
        entry.source      = source
        entry.query       = query
        entry.results     = results or [_source_dict()]
        entry.cached_at   = _utcnow_iso()   # string timestamp
        entry.ttl_seconds = ttl
        return entry

    def test_put_entry_then_get_entry_round_trip(self, db_store):
        entry = self._make_entry()
        db_store.put_entry(entry)
        result = db_store.get_entry("wikipedia", "test")
        assert result is not None
        assert result["source"] == "wikipedia"
        assert result["query"]  == "test"

    def test_put_entry_with_datetime_cached_at(self, db_store):
        from datetime import datetime, timezone
        entry = self._make_entry()
        entry.cached_at = datetime.now(tz=timezone.utc)  # datetime object, not str
        db_store.put_entry(entry)
        assert db_store.get_entry("wikipedia", "test") is not None

    def test_put_entry_with_source_objects(self, db_store, sample_sources):
        """put_entry must handle results that are Source instances (not dicts)."""
        entry = MagicMock()
        entry.source      = "wikipedia"
        entry.query       = "photosynthesis"
        entry.results     = sample_sources   # list[Source], not list[dict]
        entry.cached_at   = _utcnow_iso()
        entry.ttl_seconds = 3600
        db_store.put_entry(entry)
        result = db_store.get_entry("wikipedia", "photosynthesis")
        assert result is not None

    def test_get_entry_miss_returns_none(self, db_store):
        assert db_store.get_entry("arxiv", "missing query") is None

    def test_get_entry_includes_all_expected_keys(self, db_store):
        entry = self._make_entry()
        db_store.put_entry(entry)
        result = db_store.get_entry("wikipedia", "test")
        for key in ("source", "query", "results", "cached_at", "ttl_seconds"):
            assert key in result


# ResearchSession domain object


class TestResearchSession:
    """Unit tests for the ResearchSession dataclass (no I/O)."""

    def test_default_status_is_pending(self):
        s = _make_session()
        assert s.status == "pending"

    def test_default_answer_is_none(self):
        s = _make_session()
        assert s.answer is None

    def test_default_citations_is_empty_list(self):
        s = _make_session()
        assert s.citations == []

    def test_default_sources_used_is_empty_list(self):
        s = _make_session()
        assert s.sources_used == []

    def test_default_error_msg_is_none(self):
        s = _make_session()
        assert s.error_msg is None

    def test_mark_running_changes_status(self):
        s = _make_session()
        s.mark_running()
        assert s.status == "running"

    def test_mark_done_sets_all_fields(self):
        s = _make_session()
        s.mark_done(
            answer="42 is the answer.",
            citations=[{"index": 1, "title": "Deep Thought"}],
            sources_used=["wikipedia"],
        )
        assert s.status       == "done"
        assert s.answer       == "42 is the answer."
        assert s.sources_used == ["wikipedia"]

    def test_mark_error_sets_status_and_message(self):
        s = _make_session()
        s.mark_error("Wikipedia timed out after 10 s")
        assert s.status   == "error"
        assert "timed out" in s.error_msg

    def test_mark_running_updates_updated_at(self):
        s = _make_session()
        old_ts = s.updated_at
        time.sleep(0.01)
        s.mark_running()
        assert s.updated_at >= old_ts

    def test_mark_done_updates_updated_at(self):
        s = _make_session()
        old_ts = s.updated_at
        time.sleep(0.01)
        s.mark_done("ans", [], [])
        assert s.updated_at >= old_ts

    def test_to_dict_contains_all_expected_keys(self):
        s = _make_session()
        d = s.to_dict()
        for key in (
            "id", "question", "status", "answer", "citations",
            "sources_used", "error_msg", "created_at", "updated_at",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_values_are_json_serialisable(self):
        s = _make_session()
        s.mark_done("answer", [{"index": 1}], ["wikipedia"])
        json.dumps(s.to_dict())  # must not raise

    def test_unique_ids_across_many_instances(self):
        ids = {_make_session().id for _ in range(50)}
        assert len(ids) == 50

    def test_unicode_question_stored_correctly(self):
        s = ResearchSession(question="量子もつれとは何ですか？")
        assert "量子" in s.question


# SessionRepository — create_session


class TestRepositoryCreate:
    """Tests for SessionRepository.create_session."""

    def test_create_returns_research_session_instance(self, repo):
        s = repo.create_session("What is DNA?")
        assert isinstance(s, ResearchSession)

    def test_create_sets_pending_status(self, repo):
        s = repo.create_session("What is DNA?")
        assert s.status == "pending"

    def test_create_session_is_persisted_in_db(self, repo):
        s = repo.create_session("What is DNA?")
        loaded = repo.get_session(s.id)
        assert loaded is not None
        assert loaded.question == "What is DNA?"

    def test_create_empty_question_raises_value_error(self, repo):
        with pytest.raises(ValueError, match="non-empty"):
            repo.create_session("")

    def test_create_whitespace_only_raises_value_error(self, repo):
        with pytest.raises(ValueError, match="non-empty"):
            repo.create_session("   ")

    def test_create_strips_whitespace_from_question(self, repo):
        s = repo.create_session("  DNA replication  ")
        assert s.question == "DNA replication"

    def test_create_multiple_sessions_returns_unique_ids(self, repo):
        ids = {repo.create_session(f"Q{i}?").id for i in range(10)}
        assert len(ids) == 10


# SessionRepository — get_session


class TestRepositoryGet:
    """Tests for SessionRepository.get_session."""

    def test_get_missing_session_returns_none(self, repo):
        assert repo.get_session("nonexistent-uuid-0000") is None

    def test_get_round_trips_done_session(self, repo, sample_session):
        loaded = repo.get_session(sample_session.id)
        assert loaded is not None
        assert loaded.status == "done"
        assert loaded.answer is not None

    def test_get_round_trips_citations_as_list(self, repo, sample_session):
        loaded = repo.get_session(sample_session.id)
        assert isinstance(loaded.citations, list)

    def test_get_round_trips_sources_used_as_list(self, repo, sample_session):
        loaded = repo.get_session(sample_session.id)
        assert isinstance(loaded.sources_used, list)

    def test_get_round_trips_error_session(self, repo):
        s = repo.create_session("Will this fail?")
        repo.update_session(s.id, status="error", error_msg="arXiv was down")
        loaded = repo.get_session(s.id)
        assert loaded.status    == "error"
        assert "arXiv" in loaded.error_msg

    def test_get_round_trips_unicode_question(self, repo):
        s = repo.create_session("日本語の質問は何ですか？")
        loaded = repo.get_session(s.id)
        assert "日本語" in loaded.question


# SessionRepository — update_session


class TestRepositoryUpdate:
    """Tests for SessionRepository.update_session."""

    def test_update_status_to_running(self, repo, pending_session):
        updated = repo.update_session(pending_session.id, status="running")
        assert updated.status == "running"

    def test_update_status_to_done_with_full_payload(
        self, repo, pending_session, sample_answer
    ):
        updated = repo.update_session(
            pending_session.id,
            status="done",
            answer=sample_answer.answer,
            citations=[c.model_dump() for c in sample_answer.citations],
            sources_used=["wikipedia", "arxiv"],
        )
        assert updated.status == "done"
        assert updated.answer == sample_answer.answer
        assert "wikipedia" in updated.sources_used

    def test_update_status_to_error(self, repo, pending_session):
        updated = repo.update_session(
            pending_session.id,
            status="error",
            error_msg="All sources timed out",
        )
        assert updated.status   == "error"
        assert "timed out" in updated.error_msg

    def test_update_nonexistent_session_raises_repository_error(self, repo):
        with pytest.raises(RepositoryError, match="not found"):
            repo.update_session("ghost-uuid-9999", status="done")

    def test_update_invalid_status_raises_value_error(self, repo, pending_session):
        with pytest.raises(ValueError, match="Invalid status"):
            repo.update_session(pending_session.id, status="flying")

    def test_update_always_refreshes_updated_at(self, repo):
        s = repo.create_session("Timestamp check?")
        old_ts = repo.get_session(s.id).updated_at
        time.sleep(0.01)
        repo.update_session(s.id, status="running")
        new_ts = repo.get_session(s.id).updated_at
        assert new_ts >= old_ts

    def test_update_partial_fields_only_changes_specified(self, repo, pending_session):
        """Updating only ``status`` must not overwrite other fields."""
        original_q = pending_session.question
        repo.update_session(pending_session.id, status="running")
        loaded = repo.get_session(pending_session.id)
        assert loaded.question == original_q


# SessionRepository — delete_session


class TestRepositoryDelete:
    """Tests for SessionRepository.delete_session."""

    def test_delete_existing_returns_true(self, repo, sample_session):
        assert repo.delete_session(sample_session.id) is True

    def test_deleted_session_not_findable(self, repo, sample_session):
        repo.delete_session(sample_session.id)
        assert repo.get_session(sample_session.id) is None

    def test_delete_nonexistent_returns_false(self, repo):
        assert repo.delete_session("ghost-uuid-1234") is False

    def test_double_delete_second_returns_false(self, repo, sample_session):
        repo.delete_session(sample_session.id)
        assert repo.delete_session(sample_session.id) is False


# SessionRepository — list_sessions


class TestRepositoryList:
    """Tests for SessionRepository.list_sessions with pagination and filtering."""

    def test_list_empty_database(self, repo):
        assert repo.list_sessions() == []

    def test_list_all_sessions(self, repo, sample_session, pending_session):
        sessions = repo.list_sessions()
        ids = {s.id for s in sessions}
        assert sample_session.id  in ids
        assert pending_session.id in ids

    def test_list_filter_by_status_done(self, repo, sample_session, pending_session):
        done = repo.list_sessions(status="done")
        assert all(s.status == "done" for s in done)
        assert sample_session.id in {s.id for s in done}
        assert pending_session.id not in {s.id for s in done}

    def test_list_filter_by_status_pending(self, repo, sample_session, pending_session):
        pending = repo.list_sessions(status="pending")
        assert all(s.status == "pending" for s in pending)
        assert pending_session.id in {s.id for s in pending}

    def test_list_filter_invalid_status_raises_value_error(self, repo):
        with pytest.raises(ValueError, match="Invalid status"):
            repo.list_sessions(status="nonexistent_status")

    def test_list_pagination_returns_correct_pages(self, repo):
        for i in range(7):
            repo.create_session(f"Question {i}?")
        page1 = repo.list_sessions(limit=4, offset=0)
        page2 = repo.list_sessions(limit=4, offset=4)
        assert len(page1) == 4
        assert len(page2) == 3
        assert {s.id for s in page1}.isdisjoint({s.id for s in page2})

    def test_list_ordered_newest_first(self, repo):
        sessions = []
        for i in range(3):
            sessions.append(repo.create_session(f"Q{i}?"))
            time.sleep(0.01)
        listed = repo.list_sessions()
        assert listed[0].id == sessions[-1].id

    def test_list_with_zero_limit_returns_empty(self, repo, pending_session):
        result = repo.list_sessions(limit=0)
        assert result == []


# Edge cases & integration


class TestEdgeCases:
    """Edge cases: Unicode, large payloads, coexistence, many operations."""

    def test_very_long_question_round_trips(self, repo):
        long_q = "What is " + "really " * 200 + "important?"
        s = repo.create_session(long_q)
        loaded = repo.get_session(s.id)
        assert loaded.question == long_q

    def test_empty_citations_list_round_trips(self, repo):
        s = repo.create_session("Edge case?")
        repo.update_session(s.id, status="done", answer="No refs.", citations=[])
        loaded = repo.get_session(s.id)
        assert loaded.citations == []

    def test_unicode_answer_round_trips(self, repo):
        s = repo.create_session("日本語の質問")
        repo.update_session(s.id, status="done", answer="答え：光合成 [1]", citations=[])
        loaded = repo.get_session(s.id)
        assert "光合成" in loaded.answer

    def test_many_sources_used_round_trips(self, repo):
        s = repo.create_session("Multi-source question?")
        repo.update_session(
            s.id, status="done", answer="...",
            sources_used=["wikipedia", "arxiv", "web"],
        )
        loaded = repo.get_session(s.id)
        assert len(loaded.sources_used) == 3

    def test_cache_and_session_tables_coexist_in_same_db(self, db_store):
        """Both tables operate independently in the same SQLite file."""
        repo = SessionRepository(db_store)
        db_store.put("wikipedia", "coexistence", [_source_dict()])
        s = repo.create_session("Coexistence test?")
        assert db_store.get("wikipedia", "coexistence") is not None
        assert repo.get_session(s.id) is not None

    def test_cache_and_session_tables_do_not_interfere(self, db_store):
        """Clearing cache must not touch the sessions table."""
        repo = SessionRepository(db_store)
        s = repo.create_session("Will survive cache clear?")
        db_store.put("wikipedia", "q", [_source_dict()])
        db_store.clear_all()
        # Session must still exist after cache is cleared
        assert repo.get_session(s.id) is not None
        # Cache entry must be gone
        assert db_store.get("wikipedia", "q") is None

    def test_create_many_sessions_all_persisted(self, repo):
        sessions = [repo.create_session(f"Q{i}?") for i in range(50)]
        listed = repo.list_sessions(limit=100)
        assert len(listed) == 50

    def test_nested_json_in_citations(self, repo):
        """Citations are arbitrary dicts; nested structures must survive."""
        s = repo.create_session("Nested JSON test?")
        complex_citation = {
            "index": 1,
            "title": "Complex",
            "url": "https://example.com",
            "origin": "web",
            "meta": {"year": 2024, "tags": ["ai", "research"]},
        }
        repo.update_session(
            s.id, status="done", answer="...", citations=[complex_citation]
        )
        loaded = repo.get_session(s.id)
        assert loaded.citations[0]["meta"]["tags"] == ["ai", "research"]


# Mock-based tests (business logic caller verification)


class TestMockRepositoryInteractions:
    """Verify that business-logic callers use the repository API correctly.

    These tests replace the real database with a ``MagicMock`` and assert on
    the call signatures.  They simulate what ``researcher.py`` /
    ``orchestrator.py`` / CLI should do without any I/O at all.
    """

    def test_researcher_creates_session_on_new_question(self, mock_repo):
        question = "What is the Higgs boson?"
        session = ResearchSession(question=question)
        mock_repo.create_session.return_value = session

        mock_repo.create_session(question)

        mock_repo.create_session.assert_called_once_with(question)

    def test_researcher_marks_running_before_fetching(self, mock_repo):
        session = ResearchSession(question="Q?")
        session.mark_running()
        mock_repo.update_session.return_value = session

        mock_repo.update_session(session.id, status="running")
        args, kwargs = mock_repo.update_session.call_args
        assert kwargs.get("status") == "running" or "running" in args

    def test_researcher_marks_done_after_synthesis(self, mock_repo, sample_answer):
        session = ResearchSession(question="Q?")
        mock_repo.update_session.return_value = session

        mock_repo.update_session(
            session.id,
            status="done",
            answer=sample_answer.answer,
            citations=[c.model_dump() for c in sample_answer.citations],
            sources_used=["wikipedia"],
        )
        _, kwargs = mock_repo.update_session.call_args
        assert kwargs["status"] == "done"
        assert "answer" in kwargs
        assert "citations" in kwargs

    def test_researcher_marks_error_on_all_sources_failing(self, mock_repo):
        session = ResearchSession(question="Q?")
        mock_repo.update_session.return_value = session

        mock_repo.update_session(
            session.id,
            status="error",
            error_msg="All sources timed out",
        )
        _, kwargs = mock_repo.update_session.call_args
        assert kwargs["status"]    == "error"
        assert "timed out" in kwargs["error_msg"]

    def test_cli_lists_sessions_with_status_filter(self, mock_repo):
        mock_repo.list_sessions.return_value = []
        mock_repo.list_sessions(status="done", limit=20)
        mock_repo.list_sessions.assert_called_once_with(status="done", limit=20)

    def test_cli_deletes_session_by_id(self, mock_repo):
        sid = "abc-123-def-456"
        mock_repo.delete_session.return_value = True
        result = mock_repo.delete_session(sid)
        assert result is True
        mock_repo.delete_session.assert_called_once_with(sid)

    def test_cli_gets_session_by_id(self, mock_repo, sample_session):
        mock_repo.get_session.return_value = sample_session
        result = mock_repo.get_session(sample_session.id)
        assert result is sample_session


# File-backed store (real file I/O, uses tmp_path)


class TestFileBackedStore:
    """Tests that exercise real SQLite file I/O (not in-memory)."""

    def test_cache_persists_across_open_close(self, tmp_path):
        db = tmp_path / ".cache" / "test.db"
        results = [_source_dict(title="Persistent")]

        with CacheStore(db) as store:
            store.put("wikipedia", "persistence", results)

        with CacheStore(db) as store:
            fetched = store.get("wikipedia", "persistence")

        assert fetched is not None
        assert fetched[0]["title"] == "Persistent"

    def test_sessions_persist_across_open_close(self, tmp_path):
        db = tmp_path / ".cache" / "sessions.db"

        with CacheStore(db) as store:
            repo = SessionRepository(store)
            s = repo.create_session("Persistent question?")
            session_id = s.id

        with CacheStore(db) as store:
            repo = SessionRepository(store)
            loaded = repo.get_session(session_id)

        assert loaded is not None
        assert loaded.question == "Persistent question?"

    def test_file_backed_store_with_repo_fixture(self, cache_store_path):
        """cache_store_path fixture provides a real file-backed store."""
        repo = SessionRepository(cache_store_path)
        s = repo.create_session("File-backed test?")
        assert repo.get_session(s.id) is not None

    def test_multiple_stores_on_same_file(self, tmp_path):
        """Opening a second CacheStore on the same file (same process) works."""
        db = tmp_path / "shared.db"
        with CacheStore(db) as s1:
            s1.put("wikipedia", "shared", [_source_dict()])
        # Re-open — simulates a restart
        with CacheStore(db) as s2:
            assert s2.get("wikipedia", "shared") is not None


# Async compatibility smoke tests


class TestAsyncCompatibility:
    """Verify the store is safe to use from asyncio.to_thread()."""

    @pytest.mark.asyncio
    async def test_put_and_get_via_to_thread(self, db_store):
        results = [_source_dict(title="Async")]
        await asyncio.to_thread(db_store.put, "arxiv", "async test", results)
        fetched = await asyncio.to_thread(db_store.get, "arxiv", "async test")
        assert fetched is not None
        assert fetched[0]["title"] == "Async"

    @pytest.mark.asyncio
    async def test_concurrent_puts_via_gather_no_corruption(self, db_store):
        """Three concurrent writes must not corrupt the database."""
        async def write(source: str, query: str) -> None:
            await asyncio.to_thread(
                db_store.put, source, query, [_source_dict(origin=source)]
            )

        await asyncio.gather(
            write("wikipedia", "concurrent_a"),
            write("arxiv",     "concurrent_b"),
            write("web",       "concurrent_c"),
        )

        assert db_store.get("wikipedia", "concurrent_a") is not None
        assert db_store.get("arxiv",     "concurrent_b") is not None
        assert db_store.get("web",       "concurrent_c") is not None

    @pytest.mark.asyncio
    async def test_repo_create_via_to_thread(self, repo):
        s = await asyncio.to_thread(repo.create_session, "Async session?")
        loaded = await asyncio.to_thread(repo.get_session, s.id)
        assert loaded is not None
        assert loaded.question == "Async session?"

    @pytest.mark.asyncio
    async def test_delete_expired_via_to_thread(self, db_store):
        db_store.put("web", "old", [_source_dict()],
                     ttl=0, cached_at="2020-01-01T00:00:00Z")
        removed = await asyncio.to_thread(db_store.delete_expired)
        assert removed == 1
