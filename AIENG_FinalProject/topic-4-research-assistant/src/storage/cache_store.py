"""
src/storage/cache_store.py

SQLite-backed persistent cache store for the Async Research Assistant.

Both the cache entries and the session records live in the same SQLite file
and share the same WAL journal for consistent ACID guarantees.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

# DDL â€” query_cache table

_DDL_CACHE = """\
CREATE TABLE IF NOT EXISTS query_cache (
    -- Composite primary key: a cache entry is uniquely identified by the
    -- combination of source name and the canonicalised query string.
    source       TEXT     NOT NULL,
    query_key    TEXT     NOT NULL,        -- lower-stripped canonical query
    results_json TEXT     NOT NULL,        -- JSON-encoded list[Source.model_dump()]
    cached_at    TEXT     NOT NULL,        -- ISO-8601 UTC  e.g. 2024-01-15T12:00:00Z
    ttl_seconds  INTEGER  NOT NULL DEFAULT 86400,
    PRIMARY KEY (source, query_key)
);
"""

_DDL_INDEX_CACHED_AT = (
    "CREATE INDEX IF NOT EXISTS idx_cache_cached_at"
    " ON query_cache(cached_at);"
)


# CacheStore


class CacheStore:
    """Low-level SQLite persistence for ``(source, query) â†’ results`` cache.

    Parameters
    ----------
    db_path:
        Path to the SQLite file.  Use ``":memory:"`` for tests â€” the database
        is discarded when the connection closes.
    default_ttl:
        Default TTL in seconds applied to new entries when no per-entry TTL is
        provided.  Mirrors ``CACHE_TTL_SECONDS`` from ``src/config.py``.
    """

    def __init__(
        self,
        db_path: str | Path = ".cache/researcher.db",
        *,
        default_ttl: int = 86_400,
    ) -> None:
        self._db_path = Path(db_path)
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None


    # Lifecycle


    def open(self) -> "CacheStore":
        """Open (or create) the database, configure pragmas, initialise schema.

        Idempotent â€” calling twice returns the same instance without
        reopening the connection.

        Returns
        -------
        CacheStore
            ``self``, so callers can chain: ``store = CacheStore(...).open()``.

        Raises
        ------
        sqlite3.Error
            If the database file cannot be opened or created.
        """
        if self._conn is not None:
            return self  # already open

        # Ensure the parent directory exists (important for CI / Docker).
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

        logger.debug("Opening cache database at %r", str(self._db_path))
        try:
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,  # guarded by self._lock
                isolation_level=None,     # autocommit; we manage transactions
            )
            self._conn.row_factory = sqlite3.Row
            self._apply_pragmas()
            self._init_schema()
        except sqlite3.Error as exc:
            logger.error("Failed to open cache database: %s", exc)
            raise
        return self

    def close(self) -> None:
        """Close the connection and release file locks.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._conn is not None:
            try:
                self._conn.close()
                logger.debug("Cache database closed.")
            except sqlite3.Error:  # pragma: no cover â€” close rarely fails
                pass
            finally:
                self._conn = None

    # Context-manager protocol

    def __enter__(self) -> "CacheStore":
        return self.open()

    def __exit__(self, *_: Any) -> None:
        self.close()


    # Internal helpers


    def _apply_pragmas(self) -> None:
        """Configure connection pragmas for performance and safety."""
        assert self._conn is not None
        for stmt in [
            "PRAGMA journal_mode = WAL;",        # concurrent reads during writes
            "PRAGMA synchronous  = NORMAL;",     # durable enough without full sync
            "PRAGMA busy_timeout = 5000;",       # wait 5 s before SQLITE_BUSY
            "PRAGMA foreign_keys = ON;",
        ]:
            self._conn.execute(stmt)

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't already exist."""
        assert self._conn is not None
        with self._transaction():
            self._conn.execute(_DDL_CACHE)
            self._conn.execute(_DDL_INDEX_CACHED_AT)
        logger.debug("Cache schema initialised.")

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        """Yield an explicit transaction; COMMIT on success, ROLLBACK on error.

        All writes must go through this context manager to stay atomic.
        The lock prevents two threads from interleaving ``BEGIN`` / ``COMMIT``.
        """
        assert self._conn is not None
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _conn_or_raise(self) -> sqlite3.Connection:
        """Return the active connection or raise if the store is not open."""
        if self._conn is None:
            raise RuntimeError(
                "CacheStore is not open. "
                "Call .open() or use it as a context manager."
            )
        return self._conn


    # Public API â€” write


    def put(
        self,
        source: str,
        query: str,
        results: list[dict[str, Any]],
        *,
        ttl: int | None = None,
        cached_at: str | None = None,
    ) -> None:
        """Insert or replace a cache entry (upsert).

        Parameters
        ----------
        source:
            Fetcher name: ``"wikipedia"``, ``"arxiv"``, or ``"web"``.
        query:
            The research query string.  Will be canonicalised (lower + strip).
        results:
            List of ``Source.model_dump()`` dicts to cache.
        ttl:
            Time-to-live in seconds.  Defaults to ``self._default_ttl``.
        cached_at:
            ISO-8601 UTC timestamp.  Defaults to now.  Mainly for testing.

        Raises
        ------
        RuntimeError
            If the store has not been opened.
        sqlite3.Error
            On unexpected database failure.
        """
        conn = self._conn_or_raise()
        key = _canonicalise(query)
        effective_ttl = ttl if ttl is not None else self._default_ttl
        ts = cached_at or _utcnow_iso()

        with self._transaction():
            conn.execute(
                """
                INSERT INTO query_cache
                    (source, query_key, results_json, cached_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, query_key) DO UPDATE SET
                    results_json = excluded.results_json,
                    cached_at    = excluded.cached_at,
                    ttl_seconds  = excluded.ttl_seconds
                """,
                (source, key, json.dumps(results, separators=(",", ":")), ts, effective_ttl),
            )
        logger.debug(
            "Cache put: source=%r query_key=%r ttl=%ds", source, key, effective_ttl
        )

    def delete(self, source: str, query: str) -> bool:
        """Delete one cache entry by source + query.

        Parameters
        ----------
        source:
            Fetcher name (``"wikipedia"``, ``"arxiv"``, ``"web"``).
        query:
            Query string (will be canonicalised before lookup).

        Returns
        -------
        bool
            ``True`` if a row was deleted; ``False`` if it did not exist.

        Raises
        ------
        RuntimeError
            If the store has not been opened.
        """
        conn = self._conn_or_raise()
        key = _canonicalise(query)
        with self._transaction():
            cur = conn.execute(
                "DELETE FROM query_cache WHERE source = ? AND query_key = ?",
                (source, key),
            )
        deleted = cur.rowcount > 0
        if deleted:
            logger.debug("Cache delete: source=%r query_key=%r", source, key)
        return deleted

    def delete_expired(self, *, now: str | None = None) -> int:
        """Purge all cache entries whose TTL has elapsed.

        Parameters
        ----------
        now:
            Reference ISO-8601 UTC timestamp.  Defaults to the actual current
            time; injectable for deterministic tests without mocking.

        Returns
        -------
        int
            Number of rows deleted.

        Raises
        ------
        RuntimeError
            If the store has not been opened.
        """
        conn = self._conn_or_raise()
        reference = now or _utcnow_iso()
        with self._transaction():
            cur = conn.execute(
                """
                DELETE FROM query_cache
                WHERE datetime(cached_at, '+' || ttl_seconds || ' seconds')
                      <= datetime(?)
                """,
                (reference,),
            )
        count = cur.rowcount
        if count:
            logger.info("Cache GC: removed %d expired entries.", count)
        return count

    def clear_all(self) -> int:
        """Truncate the entire cache table.

        Useful for ``--no-cache`` mode or test teardown.

        Returns
        -------
        int
            Number of rows deleted.

        Raises
        ------
        RuntimeError
            If the store has not been opened.
        """
        conn = self._conn_or_raise()
        with self._transaction():
            cur = conn.execute("DELETE FROM query_cache")
        count = cur.rowcount
        logger.debug("Cache cleared: %d rows removed.", count)
        return count


    # Public API â€” read


    def get(
        self,
        source: str,
        query: str,
    ) -> list[dict[str, Any]] | None:
        """Fetch a live (non-expired) cache entry.

        Parameters
        ----------
        source:
            Fetcher name (``"wikipedia"``, ``"arxiv"``, ``"web"``).
        query:
            Query string (will be canonicalised before lookup).

        Returns
        -------
        list[dict] | None
            The cached ``Source.model_dump()`` dicts, or ``None`` if there is
            no entry or the entry has expired.

        Raises
        ------
        RuntimeError
            If the store has not been opened.
        """
        conn = self._conn_or_raise()
        key = _canonicalise(query)
        now = _utcnow_iso()

        with self._lock:
            row = conn.execute(
                """
                SELECT results_json, cached_at, ttl_seconds
                FROM   query_cache
                WHERE  source = ? AND query_key = ?
                  AND  datetime(cached_at, '+' || ttl_seconds || ' seconds')
                       > datetime(?)
                """,
                (source, key, now),
            ).fetchone()

        if row is None:
            logger.debug("Cache miss: source=%r query_key=%r", source, key)
            return None

        logger.debug("Cache hit:  source=%r query_key=%r", source, key)
        return json.loads(row["results_json"])

    def list_entries(
        self,
        *,
        source: str | None = None,
        include_expired: bool = False,
    ) -> list[sqlite3.Row]:
        """Return cache rows, optionally filtered by source and live/expired.

        Used by ``cache.py``'s diagnostics and the CLI ``cache stats`` command.

        Parameters
        ----------
        source:
            Filter to one fetcher.  ``None`` returns all sources.
        include_expired:
            If ``False`` (default), only live entries are returned.

        Returns
        -------
        list[sqlite3.Row]
            Rows ordered by ``cached_at`` descending (newest first).

        Raises
        ------
        RuntimeError
            If the store has not been opened.
        """
        conn = self._conn_or_raise()
        now = _utcnow_iso()
        clauses: list[str] = []
        params: list[Any] = []

        if source is not None:
            clauses.append("source = ?")
            params.append(source)

        if not include_expired:
            clauses.append(
                "datetime(cached_at, '+' || ttl_seconds || ' seconds') > datetime(?)"
            )
            params.append(now)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        # The f-string is safe here: clauses contain only literals, never user data.
        sql = f"SELECT * FROM query_cache {where} ORDER BY cached_at DESC"  # noqa: S608

        with self._lock:
            return conn.execute(sql, params).fetchall()

    def stats(self) -> dict[str, int]:
        """Return a summary dict: total, expired, and live row counts.

        Intended for health-check endpoints and the ``bench.py`` script.

        Returns
        -------
        dict[str, int]
            Keys: ``total``, ``expired``, ``live``.

        Raises
        ------
        RuntimeError
            If the store has not been opened.
        """
        conn = self._conn_or_raise()
        now = _utcnow_iso()
        with self._lock:
            total: int = conn.execute(
                "SELECT COUNT(*) FROM query_cache"
            ).fetchone()[0]
            expired: int = conn.execute(
                """
                SELECT COUNT(*) FROM query_cache
                WHERE datetime(cached_at, '+' || ttl_seconds || ' seconds')
                      <= datetime(?)
                """,
                (now,),
            ).fetchone()[0]
        return {"total": total, "expired": expired, "live": total - expired}


    # CacheEntry bridge helpers


    def put_entry(self, entry: Any) -> None:
        """Persist a ``CacheEntry`` model instance (Ãœzv A's models.py).

        Accepts any object that has ``source``, ``query``, ``results``,
        ``cached_at`` (datetime or str), and ``ttl_seconds`` attributes.
        This duck-typing approach keeps the storage layer decoupled from the
        exact ``CacheEntry`` class definition until the two branches are merged.

        Parameters
        ----------
        entry:
            Duck-typed CacheEntry â€” any object with the five required attributes.

        Raises
        ------
        RuntimeError
            If the store has not been opened.
        """
        cached_at = entry.cached_at
        if isinstance(cached_at, datetime):
            cached_at = cached_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        # ``entry.results`` may be a list[Source] or list[dict]
        results_dicts: list[dict[str, Any]]
        if entry.results and hasattr(entry.results[0], "model_dump"):
            results_dicts = [s.model_dump() for s in entry.results]
        else:
            results_dicts = list(entry.results)

        self.put(
            source=entry.source,
            query=entry.query,
            results=results_dicts,
            ttl=entry.ttl_seconds,
            cached_at=cached_at,
        )

    def get_entry(self, source: str, query: str) -> dict[str, Any] | None:
        """Return a raw dict suitable for constructing a ``CacheEntry``.

        Returns ``None`` on cache miss or expired entry.  The caller
        (``cache.py``) wraps this in ``CacheEntry(**result)``.

        Parameters
        ----------
        source:
            Fetcher name (``"wikipedia"``, ``"arxiv"``, ``"web"``).
        query:
            Query string (will be canonicalised before lookup).

        Returns
        -------
        dict[str, Any] | None
            Keys: ``source``, ``query``, ``results``, ``cached_at``,
            ``ttl_seconds``.  ``None`` on miss.
        """
        results = self.get(source, query)
        if results is None:
            return None
        return {
            "source":      source,
            "query":       query,
            "results":     results,
            "cached_at":   _utcnow_iso(),
            "ttl_seconds": self._default_ttl,
        }


# Module-level helpers (also imported by repository.py)


def _canonicalise(query: str) -> str:
    """Normalise a query to a consistent cache key.

    ``"WHAT IS PHOTOSYNTHESIS?"`` and ``"what is photosynthesis?"``
    map to the same key: ``"what is photosynthesis?"``.

    Parameters
    ----------
    query:
        Raw query string (any case, any leading/trailing whitespace).

    Returns
    -------
    str
        Lower-cased and stripped query.
    """
    return query.lower().strip()


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string compatible with SQLite.

    Returns
    -------
    str
        Format: ``YYYY-MM-DDTHH:MM:SSZ``  e.g. ``"2024-01-15T12:00:00Z"``.
    """
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
