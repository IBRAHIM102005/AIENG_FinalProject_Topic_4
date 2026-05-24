"""Business workflow for answering a research question."""

from __future__ import annotations

import logging
import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from ai import AnswerWithCitations, Source, synthesize
from ai.providers.base import LLMProvider

from src.config import get_settings
from src.concurrency.orchestrator import (
    Fetcher,
    OrchestrationResult,
    SourceFailure,
    fetch_selected_sources,
)
from src.services.cache import CacheService

logger = logging.getLogger(__name__)

Synthesizer = Callable[..., AnswerWithCitations]
Orchestrator = Callable[..., Awaitable[OrchestrationResult]]


@dataclass(frozen=True)
class ResearchResult:
    """Final answer plus operational details needed by CLI/reporting."""

    answer: AnswerWithCitations
    sources: list[Source]
    failures: list[SourceFailure] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    elapsed_seconds: float = 0.0


def validate_question(question: str) -> str:
    """Validate and canonicalize user-provided questions."""

    cleaned = " ".join(question.split())
    if not cleaned:
        raise ValueError("question must be non-empty")
    max_question_length = get_settings().max_question_length
    if len(cleaned) > max_question_length:
        raise ValueError(f"question must be at most {max_question_length} characters")
    return cleaned


def sanitize_answer_text(answer: AnswerWithCitations) -> AnswerWithCitations:
    """Trim accidental surrounding whitespace from model output."""

    answer.answer = answer.answer.strip()
    return answer


async def research_question(
    question: str,
    *,
    sources: Iterable[str] | None = None,
    max_results: int = 3,
    per_source_timeout: float = 10.0,
    semaphore_limit: int = 3,
    llm: LLMProvider | None = None,
    fetchers: Mapping[str, Fetcher] | None = None,
    client: Any = None,
    cache: CacheService[list[dict[str, Any]]] | None = None,
    orchestrator: Orchestrator = fetch_selected_sources,
    synthesizer: Synthesizer = synthesize,
) -> ResearchResult:
    """Fetch sources in parallel and synthesize a cited answer."""

    cleaned_question = validate_question(question)
    started = time.perf_counter()

    orchestration = await orchestrator(
        cleaned_question,
        sources=sources,
        max_results=max_results,
        per_source_timeout=per_source_timeout,
        semaphore_limit=semaphore_limit,
        fetchers=fetchers,
        client=client,
        cache=cache,
    )
    if not orchestration.sources:
        failed = ", ".join(f"{f.source}: {f.error}" for f in orchestration.failures)
        detail = f" Source failures: {failed}" if failed else ""
        raise RuntimeError(f"no sources were retrieved.{detail}")

    for failure in orchestration.failures:
        logger.warning(
            "research_source_failure source=%s elapsed=%.3f error=%s",
            failure.source,
            failure.elapsed_seconds,
            failure.error,
        )

    loop = asyncio.get_running_loop()
    answer = await loop.run_in_executor(
        None,
        partial(synthesizer, cleaned_question, orchestration.sources, llm=llm),
    )
    answer = sanitize_answer_text(answer)
    elapsed = time.perf_counter() - started

    logger.info(
        "research_complete source_count=%s failure_count=%s elapsed=%.3f",
        len(orchestration.sources),
        len(orchestration.failures),
        elapsed,
    )

    return ResearchResult(
        answer=answer,
        sources=orchestration.sources,
        failures=orchestration.failures,
        timings=orchestration.timings,
        elapsed_seconds=elapsed,
    )