"""Parallel source orchestration for the research pipeline."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ai import Source, fetch_arxiv, fetch_web, fetch_wikipedia

from src.services.cache import CacheService

logger = logging.getLogger(__name__)

SourceName = str
Fetcher = Callable[..., Awaitable[list[Source]]]

SOURCE_ALIASES: dict[str, SourceName] = {
    "wiki": "wikipedia",
    "wikipedia": "wikipedia",
    "arxiv": "arxiv",
    "web": "web",
}

DEFAULT_FETCHERS: dict[SourceName, Fetcher] = {
    "wikipedia": fetch_wikipedia,
    "arxiv": fetch_arxiv,
    "web": fetch_web,
}


@dataclass(frozen=True)
class SourceFailure:
    """A non-fatal source failure captured during parallel fetching."""

    source: SourceName
    error: str
    elapsed_seconds: float


@dataclass(frozen=True)
class OrchestrationResult:
    """Combined output from all selected source fetchers."""

    sources: list[Source]
    failures: list[SourceFailure] = field(default_factory=list)
    timings: dict[SourceName, float] = field(default_factory=dict)


def normalize_sources(selected: Iterable[str] | None) -> tuple[SourceName, ...]:
    """Normalize CLI/source aliases and reject unknown source names."""

    if selected is None:
        return ("wikipedia", "arxiv", "web")

    normalized: list[SourceName] = []
    for raw in selected:
        key = raw.strip().lower()
        if not key:
            continue
        if key not in SOURCE_ALIASES:
            allowed = ", ".join(sorted(SOURCE_ALIASES))
            raise ValueError(f"unknown source {raw!r}; expected one of: {allowed}")
        name = SOURCE_ALIASES[key]
        if name not in normalized:
            normalized.append(name)

    if not normalized:
        raise ValueError("at least one source must be selected")
    return tuple(normalized)


async def fetch_selected_sources(
    question: str,
    *,
    sources: Iterable[str] | None = None,
    max_results: int = 3,
    per_source_timeout: float = 10.0,
    semaphore_limit: int = 3,
    fetchers: Mapping[SourceName, Fetcher] | None = None,
    client: Any = None,
    cache: CacheService[list[dict[str, Any]]] | None = None,
) -> OrchestrationResult:
    """Fetch selected sources concurrently with per-source timeouts.

    Failures are returned as data instead of raising, so one broken provider
    does not prevent the research flow from using the remaining sources.
    """

    if not question.strip():
        raise ValueError("question must be non-empty")
    if max_results < 1:
        raise ValueError("max_results must be at least 1")
    if per_source_timeout <= 0:
        raise ValueError("per_source_timeout must be positive")
    if semaphore_limit < 1:
        raise ValueError("semaphore_limit must be at least 1")

    selected = normalize_sources(sources)
    active_fetchers = dict(fetchers or DEFAULT_FETCHERS)
    missing = [name for name in selected if name not in active_fetchers]
    if missing:
        raise ValueError(f"missing fetcher(s): {', '.join(missing)}")

    semaphore = asyncio.Semaphore(semaphore_limit)

    async def run_one(name: SourceName) -> tuple[SourceName, list[Source] | Exception, float]:
        started = time.perf_counter()
        try:
            if cache is not None:
                cached = await cache.get(name, question)
                if cached is not None:
                    result = [Source.model_validate(item) for item in cached]
                    elapsed = time.perf_counter() - started
                    logger.info("source_fetch_cache_hit source=%s count=%s elapsed=%.3f", name, len(result), elapsed)
                    return name, result, elapsed

            async with semaphore:
                result = await asyncio.wait_for(
                    active_fetchers[name](
                        question,
                        max_results=max_results,
                        client=client,
                    ),
                    timeout=per_source_timeout,
                )
            if cache is not None:
                await cache.set(name, question, [source.model_dump() for source in result])
            elapsed = time.perf_counter() - started
            logger.info("source_fetch_ok source=%s count=%s elapsed=%.3f", name, len(result), elapsed)
            return name, result, elapsed
        except Exception as exc:
            elapsed = time.perf_counter() - started
            logger.warning("source_fetch_failed source=%s elapsed=%.3f error=%s", name, elapsed, exc)
            return name, exc, elapsed

    results = await asyncio.gather(*(run_one(name) for name in selected), return_exceptions=True)

    combined: list[Source] = []
    failures: list[SourceFailure] = []
    timings: dict[SourceName, float] = {}
    seen_urls: set[str] = set()

    for item in results:
        if isinstance(item, Exception):
            failures.append(SourceFailure(source="orchestrator", error=str(item), elapsed_seconds=0.0))
            continue

        name, payload, elapsed = item
        timings[name] = elapsed
        if isinstance(payload, Exception):
            failures.append(SourceFailure(source=name, error=str(payload), elapsed_seconds=elapsed))
            continue
        for source in payload:
            if source.url in seen_urls:
                continue
            seen_urls.add(source.url)
            combined.append(source)

    return OrchestrationResult(sources=combined, failures=failures, timings=timings)