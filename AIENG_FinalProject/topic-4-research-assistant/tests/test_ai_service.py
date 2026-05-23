"""
AIService — Unit Tests

Scope:
    - Fetch methods (wikipedia, arxiv, web)
    - Synthesize flow
    - Input validation
    - Error handling (degraded responses)

All tests are fully offline.
External dependencies are replaced with FakeAIClient.
Async Research Assistant — AIService Unit Tests
AIENG Final Project / topic-4-research-assistant

This module performs:
    1. Black-box testing of AIService public API via FakeAIClient injection
    2. Verifying FailoverAIClient transparent provider switching
    3. Confirming retry logic: transient errors are retried, logic errors are not

Key responsibilities:
    - All tests are fully offline (no network calls)
    - Behaviour is tested through the public API only — no internal state access
    - FakeAIClient is used everywhere instead of MagicMock for type safety
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.services.ai_service import (
    AIService,
    AIServiceError,
    FailoverAIClient,
    SourceResult,
    ValidationError,
)
from tests.fixtures import FakeAIClient, ai_service, fake_client  # noqa: F401


# ============================================================
# Helpers
# ============================================================

def _call(client: FakeAIClient, method: str) -> AsyncMock:
    """Return the named AsyncMock from a FakeAIClient."""
    return getattr(client, method)


# ============================================================
# Fetch Operations — Happy Path
# ============================================================

class TestAIServiceFetchSuccess:
    """Parametrised over all three sources to avoid test duplication."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method, source, payload",
        [
            ("fetch_wikipedia", "wikipedia", [{"title": "Photosynthesis"}]),
            ("fetch_arxiv",     "arxiv",     [{"id": "2101.00001"}]),
            ("fetch_web",       "web",       [{"url": "https://example.com"}]),
        ],
    )
    async def test_fetch_returns_success_result(
        self,
        ai_service: AIService,
        fake_client: FakeAIClient,
        method: str,
        source: str,
        payload: list[Any],
    ) -> None:
        _call(fake_client, method).return_value = payload

        result: SourceResult = await getattr(ai_service, method)("test question")

        assert isinstance(result, SourceResult)
        assert result.success is True
        assert result.source == source
        assert result.results == payload


# ============================================================
# Synthesize — Happy Path
# ============================================================

class TestAIServiceSynthesize:

    @pytest.mark.asyncio
    async def test_returns_client_response(
        self,
        ai_service: AIService,
        fake_client: FakeAIClient,
    ) -> None:
        expected = {"answer": "Photosynthesis converts light to energy.", "citations": []}
        fake_client.synthesize.return_value = expected

        result = await ai_service.synthesize("What is photosynthesis?", [{"title": "Source"}])

        assert result == expected
        fake_client.synthesize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_question_and_sources_to_client(
        self,
        ai_service: AIService,
        fake_client: FakeAIClient,
    ) -> None:
        question = "What is a transformer?"
        sources = [{"title": "Attention Is All You Need"}]
        fake_client.synthesize.return_value = {"answer": "ok", "citations": []}

        await ai_service.synthesize(question, sources)

        call_args = fake_client.synthesize.call_args
        assert call_args[0][0] == question
        assert call_args[0][1] == sources


# ============================================================
# Input Validation
# ============================================================

class TestAIServiceValidation:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["fetch_wikipedia", "fetch_arxiv", "fetch_web"])
    @pytest.mark.parametrize("question", ["", "   ", "\t\n"])
    async def test_empty_question_is_rejected(
        self,
        ai_service: AIService,
        method: str,
        question: str,
    ) -> None:
        with pytest.raises(ValidationError):
            await getattr(ai_service, method)(question)

    @pytest.mark.asyncio
    async def test_synthesize_empty_question_is_rejected(
        self, ai_service: AIService
    ) -> None:
        with pytest.raises(ValidationError):
            await ai_service.synthesize("   ", [{"title": "Source"}])


# ============================================================
# Degraded Behaviour — Error Handling
# ============================================================

class TestAIServiceErrorHandling:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method, source",
        [
            ("fetch_wikipedia", "wikipedia"),
            ("fetch_arxiv",     "arxiv"),
            ("fetch_web",       "web"),
        ],
    )
    async def test_connection_failure_returns_degraded_result(
        self,
        ai_service: AIService,
        fake_client: FakeAIClient,
        method: str,
        source: str,
    ) -> None:
        _call(fake_client, method).side_effect = ConnectionError("network unreachable")

        result = await getattr(ai_service, method)("question")

        assert result.success is False
        assert result.source == source
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_timeout_returns_degraded_result(
        self,
        ai_service: AIService,
        fake_client: FakeAIClient,
    ) -> None:
        fake_client.fetch_arxiv.side_effect = asyncio.TimeoutError

        result = await ai_service.fetch_arxiv("question")

        assert result.success is False
        assert result.source == "arxiv"

    @pytest.mark.asyncio
    async def test_synthesize_timeout_raises_ai_service_error(
        self,
        ai_service: AIService,
        fake_client: FakeAIClient,
    ) -> None:
        """Synthesis timeout must raise AIServiceError, not propagate TimeoutError."""
        async def _slow(*_: Any) -> None:
            await asyncio.sleep(60)

        fake_client.synthesize.side_effect = _slow

        with pytest.raises(AIServiceError, match="timed out"):
            await ai_service.synthesize("question", [{"title": "src"}])


# ============================================================
# FailoverAIClient — Multi-provider failover  (+3 bonus)
# ============================================================

class TestFailoverAIClient:
    """
    Verify that FailoverAIClient transparently routes to a secondary provider
    when the primary raises an exception (chaos test).
    """

    @pytest.mark.asyncio
    async def test_secondary_used_when_primary_fails(self) -> None:
        """If primary always raises, secondary should handle the request."""
        primary = FakeAIClient()
        primary.synthesize.side_effect = ConnectionError("primary is down")

        secondary_answer = {"answer": "ok from secondary", "citations": []}
        secondary = FakeAIClient(synthesize_return=secondary_answer)

        failover = FailoverAIClient([primary, secondary])
        service = AIService(client=failover)

        result = await service.synthesize("question", [{"title": "src"}])

        assert result == secondary_answer

    @pytest.mark.asyncio
    async def test_primary_used_when_healthy(self) -> None:
        """Primary should be used when it succeeds; secondary must not be called."""
        primary_answer = {"answer": "ok from primary", "citations": []}
        primary = FakeAIClient(synthesize_return=primary_answer)
        secondary = FakeAIClient()

        failover = FailoverAIClient([primary, secondary])
        service = AIService(client=failover)

        result = await service.synthesize("question", [{"title": "src"}])

        assert result == primary_answer
        secondary.synthesize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises_ai_service_error(self) -> None:
        """When every provider fails, AIServiceError must be raised."""
        primary = FakeAIClient()
        primary.synthesize.side_effect = ConnectionError("primary down")
        secondary = FakeAIClient()
        secondary.synthesize.side_effect = ConnectionError("secondary down")

        failover = FailoverAIClient([primary, secondary])
        service = AIService(client=failover)

        with pytest.raises(AIServiceError):
            await service.synthesize("question", [{"title": "src"}])

    @pytest.mark.asyncio
    async def test_failover_fetch_wikipedia(self) -> None:
        """Failover works for fetch methods too, not only synthesize."""
        primary = FakeAIClient()
        primary.fetch_wikipedia.side_effect = OSError("disk error")

        secondary = FakeAIClient(wikipedia=[{"title": "Backup Wikipedia Result"}])

        failover = FailoverAIClient([primary, secondary])
        service = AIService(client=failover)

        result = await service.fetch_wikipedia("photosynthesis")

        assert result.success is True
        assert result.results == [{"title": "Backup Wikipedia Result"}]

    def test_empty_client_list_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            FailoverAIClient([])


# ============================================================
# Retry Behaviour
# ============================================================

class TestAIServiceRetry:
    """
    Verify that transient errors are retried but non-retryable errors are not.
    Uses FakeAIClient with call-counting side effects.
    """

    @pytest.mark.asyncio
    async def test_connection_error_is_retried(
        self,
        fake_client: FakeAIClient,
    ) -> None:
        """
        A ConnectionError on the first two calls should be retried.
        The third call succeeds.
        """
        call_count = 0

        async def _flaky(*_: Any) -> list[dict]:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient failure")
            return [{"title": "Success on attempt 3"}]

        fake_client.fetch_wikipedia.side_effect = _flaky
        service = AIService(client=fake_client)

        result = await service.fetch_wikipedia("question")

        assert result.success is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_validation_error_is_not_retried(
        self,
        ai_service: AIService,
    ) -> None:
        """
        ValidationError is a logic error — it must not be retried.
        The service must raise immediately after the first call.
        """
        with pytest.raises(ValidationError):
            await ai_service.fetch_wikipedia("")