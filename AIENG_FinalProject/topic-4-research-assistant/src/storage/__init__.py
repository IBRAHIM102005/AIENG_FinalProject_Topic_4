"""src/storage — SQLite persistence layer for the Async Research Assistant.

Public surface
--------------
CacheStore         — low-level SQLite engine for ``(source, query)`` cache.
SessionRepository  — CRUD abstraction for ``ResearchSession`` records.
ResearchSession    — domain dataclass (passed between all layers).
RepositoryError    — exception raised by repository operations.
"""

from src.storage.cache_store import CacheStore
from src.storage.repository import (
    RepositoryError,
    ResearchSession,
    SessionRepository,
)

__all__ = [
    "CacheStore",
    "RepositoryError",
    "ResearchSession",
    "SessionRepository",
]
