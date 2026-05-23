"""
Async Research Assistant — AI Service Layer
AIENG Final Project / topic-4-research-assistant

This module performs:
    1. Wrapping raw AI client calls with retry, timeout, and structured logging
    2. Validating user input before any external call is made
    3. Returning structured SourceResult objects so the pipeline never fails silently
    4. Providing transparent multi-provider failover via FailoverAIClient (+3 bonus)

Key responsibilities:
    - Retry transient failures (network, timeout, OS errors) via tenacity
    - Apply per-call timeouts sourced from centralised Settings
    - Expose a clean async public API used by the pipeline orchestrator
    - Degrade gracefully: source failures produce a degraded SourceResult, not an exception
    - FailoverAIClient: if the primary AI provider fails, transparently try the secondary

Outputs / Side Effects:
    - Returns SourceResult (success or degraded) for each fetch operation
    - Returns raw synthesis result on success; raises AIServiceError on terminal failure
    - Writes WARNING-level logs on retries, degraded sources, and provider failovers

Bonuses implemented:
    - Multi-provider failover (+3): FailoverAIClient class

"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from src.config import get_settings

logger = logging.getLogger(__name__)

# Only network/OS-level errors warrant a retry.
# Logic bugs and validation errors must propagate immediately without retrying.
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


# ============================================================
# Exceptions
# ============================================================

class AIServiceError(Exception):
    """Raised when an AI operation fails definitively after all retries."""


class ValidationError(AIServiceError):
    """Raised when caller-supplied input fails validation."""


# ============================================================
# Result Model
# ============================================================

@dataclass(frozen=True)
class SourceResult:
    """
    Outcome of a single source-fetch attempt.

    Always returned — even on failure — so the pipeline can continue
    with partial results rather than aborting entirely.
    """

    source: str
    results: list[Any] = field(default_factory=list)
    success: bool = True
    error: str | None = None

    @classmethod
    def degraded(cls, source: str, reason: str) -> "SourceResult":
        logger.warning("Source '%s' degraded: %s", source, reason)
        return cls(source=source, success=False, error=reason)


# ============================================================
# AI Client Protocol (dependency injection boundary)
# ============================================================

class AIClient(Protocol):
    """Interface that every concrete AI client must satisfy."""

    async def fetch_wikipedia(self, question: str) -> list[Any]: ...
    async def fetch_arxiv(self, question: str) -> list[Any]: ...
    async def fetch_web(self, question: str) -> list[Any]: ...
    async def synthesize(self, question: str, sources: list[Any]) -> Any: ...


# ============================================================
# Retry Factory
# ============================================================

@lru_cache(maxsize=8)
def _build_retry(max_attempts: int, min_wait: float, max_wait: float):
    """
    Build and cache a tenacity retry decorator for the given parameters.

    Caching avoids reconstructing the decorator on every call while still
    supporting different retry policies for different settings profiles (e.g.
    fast retries in tests vs. slower retries in production).
    """
    return retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=False,
    )


def _retry():
    s = get_settings()
    return _build_retry(
        s.retry_max_attempts,
        s.retry_min_wait_seconds,
        s.retry_max_wait_seconds,
    )


# ============================================================
# Input Validation
# ============================================================

def _validate_question(question: str) -> None:
    if not question or not question.strip():
        raise ValidationError("Question must not be empty.")

    max_len = get_settings().max_question_length
    if len(question) > max_len:
        raise ValidationError(f"Question too long ({len(question)} > {max_len})")


# ============================================================
# AI Service
# ============================================================

class AIService:
    """
    High-level async wrapper around an AIClient.

    Adds retry, per-call timeout, structured logging, and input validation
    without coupling callers to tenacity internals or the concrete client.

   
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client

    # ── Public API ────────────────────────────────────────────────────────

    async def fetch_wikipedia(self, question: str) -> SourceResult:
        _validate_question(question)
        return await self._fetch("wikipedia", self._client.fetch_wikipedia, question)

    async def fetch_arxiv(self, question: str) -> SourceResult:
        _validate_question(question)
        return await self._fetch("arxiv", self._client.fetch_arxiv, question)

    async def fetch_web(self, question: str) -> SourceResult:
        _validate_question(question)
        return await self._fetch("web", self._client.fetch_web, question)

    async def synthesize(self, question: str, sources: list[Any]) -> Any:
        _validate_question(question)
        logger.info("Synthesizing answer (question_len=%d, sources=%d)", len(question), len(sources))

        # Synthesis involves more round-trips than a single fetch, so give it 3× the budget.
        timeout = get_settings().per_source_timeout_seconds * 3
        fn = _retry()(self._client.synthesize)

        try:
            return await asyncio.wait_for(
                _run_async(fn, question, sources),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise AIServiceError("Synthesis timed out")
        except RetryError as exc:
            raise AIServiceError(f"Synthesis failed after all retries: {exc}") from exc
        except Exception as exc:
            logger.exception("Unexpected synthesis error")
            raise AIServiceError(str(exc)) from exc

    # ── Internal ──────────────────────────────────────────────────────────

    async def _fetch(
        self,
        source: str,
        fn: Callable[[str], Awaitable[list[Any]]],
        question: str,
    ) -> SourceResult:
        timeout = get_settings().per_source_timeout_seconds
        fn = _retry()(fn)

        try:
            data = await asyncio.wait_for(
                _run_async(fn, question),
                timeout=timeout,
            )
            return SourceResult(source=source, results=data)
        except asyncio.TimeoutError:
            return SourceResult.degraded(source, f"timeout after {timeout}s")
        except RetryError as exc:
            return SourceResult.degraded(source, f"retries exhausted: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error from source '%s'", source)
            return SourceResult.degraded(source, str(exc))


# ============================================================
# FailoverAIClient
# ============================================================

class FailoverAIClient:
    """
    Wraps a prioritised list of AIClient instances with transparent failover.

    If the primary client raises any exception, the next client in the list
    is tried automatically. This continues until one succeeds or all are
    exhausted, at which point AIServiceError is raised.

    Chaos / bonus test
    ------------------
    To exercise failover in a test, configure the primary with a side_effect
    that raises ConnectionError on every call. The secondary should then
    handle the request normally.
    """

    def __init__(self, clients: list[AIClient]) -> None:
        if not clients:
            raise ValueError("FailoverAIClient requires at least one client.")
        self._clients = clients

    async def fetch_wikipedia(self, question: str) -> list[Any]:
        return await self._try_all("fetch_wikipedia", question)

    async def fetch_arxiv(self, question: str) -> list[Any]:
        return await self._try_all("fetch_arxiv", question)

    async def fetch_web(self, question: str) -> list[Any]:
        return await self._try_all("fetch_web", question)

    async def synthesize(self, question: str, sources: list[Any]) -> Any:
        return await self._try_all("synthesize", question, sources)

    async def _try_all(self, method: str, *args: Any) -> Any:
        """
        Iterate through clients in priority order, returning the first
        successful result. Logs a warning each time a provider fails.
        """
        last_exc: Exception | None = None

        for idx, client in enumerate(self._clients):
            try:
                return await getattr(client, method)(*args)
            except Exception as exc:
                provider_label = type(client).__name__
                logger.warning(
                    "Provider '%s' (index=%d) failed for '%s': %s — trying next",
                    provider_label, idx, method, exc,
                )
                last_exc = exc

        # All providers failed.
        raise AIServiceError(
            f"All {len(self._clients)} providers failed for '{method}'. "
            f"Last error: {last_exc}"
        ) from last_exc


# ============================================================
# Async Utility
# ============================================================

async def _run_async(fn: Callable[..., Any], *args: Any) -> Any:
    """
    Uniformly await both native coroutines and plain synchronous callables.

    Synchronous callables are offloaded to the default thread-pool executor so
    they do not block the event loop. This makes AIClient implementations that
    wrap blocking SDK calls work transparently.
    """
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)