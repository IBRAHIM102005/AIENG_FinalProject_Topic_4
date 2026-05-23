"""
Async Research Assistant — Domain Models
Core domain layer for research pipeline state, caching, and citations.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing import Generic, TypeVar


# ============================================================
# Types
# ============================================================

SourceName = Literal["wikipedia", "arxiv", "web"]


# ============================================================
# Citation
# ============================================================

class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: SourceName
    url: str
    snippet: str
    title: str = ""


# ============================================================
# Cache Entry
# ============================================================
T = TypeVar("T")

class CacheEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    value: T

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: int = 86_400

    @property
    def created_at_ts(self) -> float:
        return self.created_at.timestamp()

    def is_expired(self) -> bool:
        return self.ttl_seconds > 0 and (
            (datetime.now(timezone.utc).timestamp() - self.created_at_ts)
            > self.ttl_seconds
        )

    @classmethod
    def make_key(cls, source: SourceName, query: str) -> str:
        norm = f"{source.strip().lower()}:{query.strip().lower()}"
        digest = hashlib.sha256(norm.encode()).hexdigest()
        return f"{source.lower()}:{digest}"


# ============================================================
# Research Session
# ============================================================

class ResearchSession(BaseModel):
    model_config = ConfigDict()

    session_id: UUID = Field(default_factory=uuid4)
    question: str

    sources_used: list[SourceName] = Field(default_factory=list)
    answer: str = ""

    citations: list[Citation] = Field(default_factory=list)
    source_timings: dict[SourceName, float] = Field(default_factory=dict)

    total_time_s: float = 0.0
    cache_hit: bool = False
    error: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def created_at_ts(self) -> float:
        return self.created_at.timestamp()

    # -------------------------
    # State updates
    # -------------------------

    def record_source_result(
        self,
        source: SourceName,
        elapsed_s: float,
        from_cache: bool = False,
    ) -> None:
        if source not in self.sources_used:
            self.sources_used.append(source)

        self.source_timings[source] = elapsed_s

        if from_cache:
            self.cache_hit = True

    def record_answer(self, answer: str, citations: list[Citation]) -> None:
        self.answer = answer
        self.citations = citations

    def record_completion(self, total_time_s: float) -> None:
        self.total_time_s = total_time_s

    def record_error(self, message: str, total_time_s: float | None = None) -> None:
        self.error = message
        if total_time_s is not None:
            self.total_time_s = total_time_s

    # -------------------------
    # Helpers
    # -------------------------

    def was_successful(self) -> bool:
        return self.error is None and bool(self.answer)

    def summary(self) -> str:
        sources = ",".join(sorted(self.sources_used)) or "none"
        preview = (self.question[:60] + "…") if len(self.question) > 60 else self.question

        return (
            f"[{'OK' if self.was_successful() else 'ERR'}] "
            f"{self.total_time_s:.2f}s cache={self.cache_hit} "
            f"sources={sources} '{preview}'"
        )