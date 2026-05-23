"""
src/storage/repository.py
=========================
Repository abstraction for ``ResearchSession`` persistence.

The repository is the only module that deals with SQL for session records.
``researcher.py``  receives and returns ``ResearchSession`` dataclass
instances; it never sees a cursor or a row.  This enforces clean architecture:
persistence is separated from business logic, and the repository can be
swapped for a PostgreSQL or in-memory implementation without touching any
other module.

"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.storage.cache_store import CacheStore, _utcnow_iso

logger = logging.getLogger(__name__)

# DDL — research_sessions table (lives alongside query_cache in the same file)

_DDL_SESSIONS = """\
CREATE TABLE IF NOT EXISTS research_sessions (
    id           TEXT    PRIMARY KEY,            -- UUID4 string
    question     TEXT    NOT NULL,               -- raw user question
    status       TEXT    NOT NULL DEFAULT 'pending',
                                                 -- pending | running | done | error
    answer       TEXT,                           -- synthesised answer (NULL until done)
    citations    TEXT    NOT NULL DEFAULT '[]',  -- JSON list of citation dicts
    sources_used TEXT    NOT NULL DEFAULT '[]',  -- JSON list of origin strings
    error_msg    TEXT,                           -- set when status='error'
    created_at   TEXT    NOT NULL,               -- ISO-8601 UTC
    updated_at   TEXT    NOT NULL                -- ISO-8601 UTC
);
"""

_DDL_SESSION_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_sessions_status"
    " ON research_sessions(status);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_created_at"
    " ON research_sessions(created_at);",
]

# Valid session status values — enforced at the Python layer before touching SQL.
_VALID_STATUSES: frozenset[str] = frozenset({"pending", "running", "done", "error"})


# Domain object — ResearchSession


@dataclass
class ResearchSession:
    """Represents one research request and its full lifecycle.

    This is the **domain object** passed between the storage, business-logic,
    and CLI layers.  It is intentionally a stdlib ``@dataclass`` (not Pydantic)
    so the storage layer has zero dependency on Pydantic.  Üzv A's
    ``src/models.py`` Pydantic model can be converted to/from this dataclass
    trivially.

    """

    question:     str
    id:           str                   = field(default_factory=lambda: str(uuid4()))
    status:       str                   = "pending"
    answer:       str | None            = None
    citations:    list[dict[str, Any]]  = field(default_factory=list)
    sources_used: list[str]             = field(default_factory=list)
    error_msg:    str | None            = None
    created_at:   str                   = field(default_factory=_utcnow_iso)
    updated_at:   str                   = field(default_factory=_utcnow_iso)

    # State-transition helpers

    def mark_running(self) -> None:
        """Transition the session to the ``running`` state.

        Call this immediately before the async fetching starts so the
        repository can track in-progress sessions.
        """
        self.status = "running"
        self.updated_at = _utcnow_iso()

    def mark_done(
        self,
        answer: str,
        citations: list[dict[str, Any]],
        sources_used: list[str],
    ) -> None:
        """Transition to ``done`` with the synthesised result.

        Parameters
        ----------
        answer:
            Synthesised answer text with inline ``[N]`` citation markers.
        citations:
            Flattened citation dicts from ``AnswerWithCitations.to_dict()``.
        sources_used:
            Origin strings of sources that contributed
            (e.g. ``["wikipedia", "arxiv", "web"]``).
        """
        self.status = "done"
        self.answer = answer
        self.citations = citations
        self.sources_used = sources_used
        self.updated_at = _utcnow_iso()

    def mark_error(self, message: str) -> None:
        """Transition to ``error`` with a descriptive message.

        Parameters
        ----------
        message:
            Human-readable failure description for CLI display.
        """
        self.status = "error"
        self.error_msg = message
        self.updated_at = _utcnow_iso()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for CLI rendering or API responses.

        Returns
        -------
        dict[str, Any]
            All nine session fields as JSON-serialisable Python primitives.
        """
        return {
            "id":           self.id,
            "question":     self.question,
            "status":       self.status,
            "answer":       self.answer,
            "citations":    self.citations,
            "sources_used": self.sources_used,
            "error_msg":    self.error_msg,
            "created_at":   self.created_at,
            "updated_at":   self.updated_at,
        }


# Custom exception


class RepositoryError(Exception):
    """Raised when a session database operation fails.

    Wraps ``sqlite3.Error`` so callers (``researcher.py``, ``orchestrator.py``,
    CLI) don't need to import ``sqlite3`` to handle persistence failures.
    This preserves the separation of concerns between the storage layer and the
    business logic.
    """


# SessionRepository


class SessionRepository:
    """CRUD interface for ``ResearchSession`` records stored in SQLite.

    Parameters
    ----------
    store:
        An **open** ``CacheStore``.  The repository re-uses the store's
        connection and transaction machinery — both cache entries and session
        records live in the same SQLite file.  The repository does **not**
        open or close the store; that lifecycle belongs to the caller.

    """

    def __init__(self, store: CacheStore) -> None:
        self._store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the sessions table and indexes if they don't exist."""
        conn = self._store._conn_or_raise()
        with self._store._transaction():
            conn.execute(_DDL_SESSIONS)
            for idx_sql in _DDL_SESSION_INDEXES:
                conn.execute(idx_sql)
        logger.debug("Sessions schema initialised.")

    # Write operations

    def create_session(self, question: str) -> ResearchSession:
        """Create and persist a new session in the ``pending`` state.

        Parameters
        ----------
        question:
            The raw user question.  Leading/trailing whitespace is stripped.
            Must be non-empty after stripping.

        Returns
        -------
        ResearchSession
            The newly created session with a fresh UUID.

        Raises
        ------
        ValueError
            If ``question`` is empty or whitespace-only.
        RepositoryError
            If the database insert fails (e.g. duplicate ID — astronomically
            unlikely with UUID4).
        """
        if not question.strip():
            raise ValueError("question must be non-empty")

        session = ResearchSession(question=question.strip())
        self._insert(session)
        logger.info(
            "Created session %s: %r", session.id, session.question[:80]
        )
        return session

    def update_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        answer: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        sources_used: list[str] | None = None,
        error_msg: str | None = None,
    ) -> ResearchSession:
        """Update one or more fields on an existing session.

        Only keyword arguments that are not ``None`` are written.
        ``updated_at`` is always refreshed.

        Parameters
        ----------
        session_id:
            UUID of the session to update.
        status:
            New status string.  Must be one of ``_VALID_STATUSES``.
        answer:
            Synthesised answer text.
        citations:
            List of citation dicts.
        sources_used:
            List of origin strings.
        error_msg:
            Failure description (used when transitioning to ``error``).

        Returns
        -------
        ResearchSession
            The freshly loaded, updated session.

        Raises
        ------
        ValueError
            If ``status`` is not in ``_VALID_STATUSES``.
        RepositoryError
            If the session doesn't exist or the SQL update fails.
        """
        if status is not None and status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}. "
                f"Must be one of {sorted(_VALID_STATUSES)}"
            )

        updates: dict[str, Any] = {"updated_at": _utcnow_iso()}
        if status is not None:
            updates["status"] = status
        if answer is not None:
            updates["answer"] = answer
        if citations is not None:
            updates["citations"] = json.dumps(citations, separators=(",", ":"))
        if sources_used is not None:
            updates["sources_used"] = json.dumps(
                sources_used, separators=(",", ":")
            )
        if error_msg is not None:
            updates["error_msg"] = error_msg

        self._apply_updates(session_id, updates)

        session = self.get_session(session_id)
        if session is None:  # pragma: no cover — update implies existence
            raise RepositoryError(
                f"Session {session_id!r} vanished after update."
            )
        logger.debug(
            "Updated session %s → status=%s", session_id, session.status
        )
        return session

    def delete_session(self, session_id: str) -> bool:
        """Delete a session by its primary key.

        Parameters
        ----------
        session_id:
            UUID of the session to delete.

        Returns
        -------
        bool
            ``True`` if a row was removed; ``False`` if the ID was not found.

        Raises
        ------
        RepositoryError
            If the delete operation fails unexpectedly.
        """
        try:
            conn = self._store._conn_or_raise()
            with self._store._transaction():
                cur = conn.execute(
                    "DELETE FROM research_sessions WHERE id = ?",
                    (session_id,),
                )
            deleted = cur.rowcount > 0
            if deleted:
                logger.info("Deleted session %s.", session_id)
            else:
                logger.debug("delete_session: ID %r not found.", session_id)
            return deleted
        except sqlite3.Error as exc:
            raise RepositoryError(
                f"Failed to delete session {session_id!r}: {exc}"
            ) from exc

    # Read operations

    def get_session(self, session_id: str) -> ResearchSession | None:
        """Fetch one session by primary key.

        Parameters
        ----------
        session_id:
            UUID of the session.

        Returns
        -------
        ResearchSession | None
            The session if found; ``None`` otherwise.

        Raises
        ------
        RepositoryError
            If the query or JSON deserialisation fails.
        """
        try:
            conn = self._store._conn_or_raise()
            with self._store._lock:
                row = conn.execute(
                    "SELECT * FROM research_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RepositoryError(
                f"Failed to fetch session {session_id!r}: {exc}"
            ) from exc

        if row is None:
            return None
        return self._row_to_session(row)

    def list_sessions(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ResearchSession]:
        """Return a page of sessions ordered by creation time (newest first).

        Parameters
        ----------
        status:
            Optional filter.  One of ``"pending"``, ``"running"``,
            ``"done"``, ``"error"``.  ``None`` returns all statuses.
        limit:
            Page size (default 100).
        offset:
            Row offset for pagination.

        Returns
        -------
        list[ResearchSession]
            Sessions ordered newest-first.

        Raises
        ------
        ValueError
            If ``status`` is provided but not in ``_VALID_STATUSES``.
        RepositoryError
            If the query fails.
        """
        if status is not None and status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status filter {status!r}")

        try:
            conn = self._store._conn_or_raise()
            if status is not None:
                with self._store._lock:
                    rows = conn.execute(
                        """
                        SELECT * FROM research_sessions
                        WHERE  status = ?
                        ORDER  BY created_at DESC
                        LIMIT  ? OFFSET ?
                        """,
                        (status, limit, offset),
                    ).fetchall()
            else:
                with self._store._lock:
                    rows = conn.execute(
                        """
                        SELECT * FROM research_sessions
                        ORDER  BY created_at DESC
                        LIMIT  ? OFFSET ?
                        """,
                        (limit, offset),
                    ).fetchall()
        except sqlite3.Error as exc:
            raise RepositoryError(f"Failed to list sessions: {exc}") from exc

        return [self._row_to_session(r) for r in rows]

    # Private helpers

    def _insert(self, session: ResearchSession) -> None:
        """Execute the INSERT for a new session row."""
        try:
            conn = self._store._conn_or_raise()
            with self._store._transaction():
                conn.execute(
                    """
                    INSERT INTO research_sessions
                        (id, question, status, answer, citations,
                         sources_used, error_msg, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.id,
                        session.question,
                        session.status,
                        session.answer,
                        json.dumps(session.citations, separators=(",", ":")),
                        json.dumps(session.sources_used, separators=(",", ":")),
                        session.error_msg,
                        session.created_at,
                        session.updated_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(
                f"Session {session.id!r} already exists (duplicate ID): {exc}"
            ) from exc
        except sqlite3.Error as exc:
            raise RepositoryError(
                f"Failed to insert session {session.id!r}: {exc}"
            ) from exc

    def _apply_updates(self, session_id: str, updates: dict[str, Any]) -> None:
        """Execute an UPDATE statement for the given column→value mapping."""
        if not updates:
            return
        # Build SET clause from column names (never from user input — safe).
        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = tuple(updates.values()) + (session_id,)
        try:
            conn = self._store._conn_or_raise()
            with self._store._transaction():
                cur = conn.execute(
                    f"UPDATE research_sessions SET {set_clause} WHERE id = ?",  # noqa: S608
                    values,
                )
            if cur.rowcount == 0:
                raise RepositoryError(
                    f"Session {session_id!r} not found — cannot update."
                )
        except sqlite3.Error as exc:
            raise RepositoryError(
                f"Failed to update session {session_id!r}: {exc}"
            ) from exc

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> ResearchSession:
        """Deserialise a SQLite row into a ``ResearchSession`` dataclass."""
        try:
            citations    = json.loads(row["citations"]    or "[]")
            sources_used = json.loads(row["sources_used"] or "[]")
        except (ValueError, TypeError) as exc:
            raise RepositoryError(
                f"Corrupt JSON in session {row['id']!r}: {exc}"
            ) from exc

        return ResearchSession(
            id=row["id"],
            question=row["question"],
            status=row["status"],
            answer=row["answer"],
            citations=citations,
            sources_used=sources_used,
            error_msg=row["error_msg"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
