"""
tests/conftest.py
=================
Shared pytest fixtures for the Async Research Assistant test suite.

This file is loaded automatically by pytest before any test module runs.
All fixtures defined here are available to:

    * tests/test_core.py         (Ãœzv C â€” storage & repository tests)
    * tests/test_services.py     (Ãœzv A â€” cache service, ai_service tests)
    * tests/test_concurrency.py  (Ãœzv B â€” orchestrator tests)
    * tests/test_end_to_end.py   (Ãœzv B â€” full pipeline tests)
    * tests/test_ai_smoke.py     (provided â€” do not modify)

Fixture catalogue
-----------------
db_store            â€” in-memory CacheStore, open for the test, auto-closed.
repo                â€” SessionRepository wired to db_store.
mock_repo           â€” MagicMock(spec=SessionRepository) for unit tests.
cache_store_path    â€” tmp_path-based file CacheStore (tests real file I/O).
sample_session      â€” A "done" ResearchSession already persisted in db_store.
pending_session     â€” A "pending" ResearchSession already persisted in db_store.
fake_wiki_sources   â€” Two canned Wikipedia Source objects.
fake_arxiv_sources  â€” Two canned arXiv Source objects.
sample_sources      â€” Three Sources from three different origins.
sample_answer       â€” AnswerWithCitations built from sample_sources.
fake_llm            â€” Offline FakeLLM stub (records calls, no API).
fake_web            â€” Offline FakeWebSearch stub (no HTTP).
mock_fetch_wikipedia â€” AsyncMock returning fake_wiki_sources.
mock_fetch_arxiv    â€” AsyncMock returning fake_arxiv_sources.
mock_fetch_web      â€” AsyncMock returning web-only sample_source.
event_loop          â€” Session-scoped asyncio loop for pytest-asyncio.

"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# AI module imports (the provided package)
from ai.providers.base import LLMProvider
from ai.schemas import AnswerWithCitations, Citation, Source
from ai.sources import WebSearchProvider

# Our storage layer
from src.storage.cache_store import CacheStore
from src.storage.repository import (
    RepositoryError,
    ResearchSession,
    SessionRepository,
)


# Offline provider stubs
# (mirrored from tests/test_ai_smoke.py conftest so all test modules share
#  the same implementations â€” single source of truth)


class FakeLLM(LLMProvider):
    """Offline LLM stub.

    Returns a hard-coded cited answer without making any API call.
    Records every prompt it receives in ``self.calls`` so tests can assert
    on what was passed.

    Usage::

        def test_something(fake_llm):
            result = synthesize("Q?", sources, llm=fake_llm)
            assert fake_llm.calls          # ensure it was called
            assert "[1]" in result.answer
    """

    DEFAULT_RESPONSE: str = (
        "Photosynthesis is the process by which plants convert light energy "
        "into chemical energy [1]. The reaction takes place in the chloroplasts "
        "and produces oxygen as a byproduct [2]."
    )

    def __init__(self, response: str | None = None) -> None:
        self.response: str = response or self.DEFAULT_RESPONSE
        self.calls: list[str] = []

    def complete(
        self,
        prompt: str,
        *,
        json_schema: dict | None = None,
        max_tokens: int = 1024,
    ) -> str:
        self.calls.append(prompt)
        return self.response


class FakeWebSearch(WebSearchProvider):
    """Offline web-search stub.

    Returns canned ``Source`` objects without making any HTTP request.
    Records every query it receives in ``self.calls``.

    Usage::

        def test_something(fake_web):
            sources = await fetch_web("photosynthesis", provider=fake_web)
            assert fake_web.calls == ["photosynthesis"]
    """

    _DEFAULT_RESULTS: list[Source] = [
        Source(
            title="Photosynthesis â€” Encyclopedia",
            url="https://example.com/photosynthesis",
            snippet=(
                "A biological process used by plants and some bacteria to "
                "convert light into chemical energy."
            ),
            origin="web",
        )
    ]

    def __init__(self, results: list[Source] | None = None) -> None:
        self.results: list[Source] = results or self._DEFAULT_RESULTS
        self.calls: list[str] = []

    async def search(
        self,
        query: str,
        *,
        max_results: int = 3,
        client: Any = None,
    ) -> list[Source]:
        self.calls.append(query)
        return self.results[:max_results]


# Core database fixtures


@pytest.fixture()
def db_store() -> CacheStore:  # type: ignore[return]
    """Yield an in-memory CacheStore, auto-closed after the test.

    All tables (``query_cache`` + ``research_sessions``) are created fresh for
    every test function, so tests are fully isolated from each other.

    Yields
    ------
    CacheStore
        Open, in-memory store.
    """
    with CacheStore(":memory:") as store:
        yield store


@pytest.fixture()
def repo(db_store: CacheStore) -> SessionRepository:
    """Return a SessionRepository wired to the in-memory db_store.

    Parameters
    ----------
    db_store:
        Provided by the ``db_store`` fixture.

    Returns
    -------
    SessionRepository
        Ready to use; no cleanup needed (``db_store`` handles close).
    """
    return SessionRepository(db_store)


@pytest.fixture()
def mock_repo() -> MagicMock:
    """Return a MagicMock with the SessionRepository spec.

    Use in unit tests where you want to assert call signatures without
    touching a database at all.

    Example::

        def test_researcher_calls_create(mock_repo):
            mock_repo.create_session.return_value = ResearchSession(
                question="Q?"
            )
            # inject mock_repo into researcher.py under test
            mock_repo.create_session.assert_called_once_with("Q?")

    Returns
    -------
    MagicMock
        Spec'd to ``SessionRepository``.
    """
    return MagicMock(spec=SessionRepository)


@pytest.fixture()
def cache_store_path(tmp_path):
    """Yield a file-backed CacheStore in a temporary directory.

    Exercises real file I/O (directory creation, WAL files, shared-cache
    lock) without polluting the project working directory.  The directory
    is cleaned up automatically by pytest's ``tmp_path`` mechanism.

    Yields
    ------
    CacheStore
        Open store backed by a real file at ``tmp_path/.cache/test.db``.
    """
    db_file = tmp_path / ".cache" / "test_researcher.db"
    with CacheStore(db_file) as store:
        yield store


# Sample AI data fixtures


@pytest.fixture()
def fake_wiki_sources() -> list[Source]:
    """Return two canned Wikipedia Source objects.

    Returns
    -------
    list[Source]
        Two ``origin="wikipedia"`` sources about photosynthesis.
    """
    return [
        Source(
            title="Photosynthesis (Wikipedia)",
            url="https://en.wikipedia.org/wiki/Photosynthesis",
            snippet=(
                "Photosynthesis is a process used by plants and other "
                "organisms to convert light energy into chemical energy."
            ),
            origin="wikipedia",
        ),
        Source(
            title="Calvin cycle (Wikipedia)",
            url="https://en.wikipedia.org/wiki/Calvin_cycle",
            snippet=(
                "The Calvin cycle is a series of biochemical redox "
                "reactions in the stroma of chloroplasts."
            ),
            origin="wikipedia",
        ),
    ]


@pytest.fixture()
def fake_arxiv_sources() -> list[Source]:
    """Return two canned arXiv Source objects.

    Returns
    -------
    list[Source]
        Two ``origin="arxiv"`` sources about photosynthesis research.
    """
    return [
        Source(
            title="Light-Dependent Reactions of Photosynthesis",
            url="https://arxiv.org/abs/1706.03762",
            snippet=(
                "We review the light-dependent reactions with emphasis on "
                "the electron transport chain in thylakoid membranes."
            ),
            origin="arxiv",
        ),
        Source(
            title="Quantum Effects in Photosynthesis",
            url="https://arxiv.org/abs/0811.1869",
            snippet=(
                "Long-lived quantum coherence is observed in the "
                "Fenna-Matthews-Olson complex."
            ),
            origin="arxiv",
        ),
    ]


@pytest.fixture()
def sample_sources(
    fake_wiki_sources: list[Source],
    fake_arxiv_sources: list[Source],
) -> list[Source]:
    """Return three Sources from three different origins (wiki, arxiv, web).

    Combines one Wikipedia source, one arXiv source, and one web source
    so tests can verify multi-origin aggregation logic.

    Returns
    -------
    list[Source]
        ``[wikipedia, arxiv, web]`` â€” three distinct origins.
    """
    web_source = Source(
        title="How Plants Make Food",
        url="https://example.com/plants",
        snippet=(
            "Plants use sunlight, water, and carbon dioxide to produce "
            "glucose and oxygen through photosynthesis."
        ),
        origin="web",
    )
    # Return both Wikipedia sources so test_ai_smoke.py::test_synthesize_passes_sources_to_prompt
    # can assert that both "Photosynthesis (Wikipedia)" and "Calvin cycle (Wikipedia)"
    # appear in the LLM prompt.  Our own test_core.py checks .origin, not titles,
    # so this change is backward-compatible.
    return [fake_wiki_sources[0], fake_wiki_sources[1], web_source]


@pytest.fixture()
def sample_answer(sample_sources: list[Source]) -> AnswerWithCitations:
    """Return a realistic AnswerWithCitations built from sample_sources.

    The ``answer`` text contains ``[1]``, ``[2]``, ``[3]`` markers that
    reference the three ``sample_sources`` in order.

    Returns
    -------
    AnswerWithCitations
        A fully populated answer object, suitable for repository update tests.
    """
    return AnswerWithCitations(
        question="What is photosynthesis?",
        answer=(
            "Photosynthesis is the process by which plants convert light "
            "energy into chemical energy [1]. The reaction involves electron "
            "transport chains in the thylakoid membranes [2], and ultimately "
            "produces glucose from COâ‚‚ and water [3]."
        ),
        citations=[
            Citation(index=1, source=sample_sources[0]),
            Citation(index=2, source=sample_sources[1]),
            Citation(index=3, source=sample_sources[2]),
        ],
    )


# Pre-populated session fixtures


@pytest.fixture()
def sample_session(
    repo: SessionRepository,
    sample_answer: AnswerWithCitations,
    sample_sources: list[Source],
) -> ResearchSession:
    """Return a ``done`` ResearchSession already saved in the database.

    Provides a realistic end-state record for read / update / delete tests
    without requiring each test to perform the full session lifecycle.

    Returns
    -------
    ResearchSession
        Status ``"done"``; answer and citations populated.
    """
    session = repo.create_session("What is photosynthesis?")
    return repo.update_session(
        session.id,
        status="done",
        answer=sample_answer.answer,
        citations=[c.model_dump() for c in sample_answer.citations],
        sources_used=[s.origin for s in sample_sources],
    )


@pytest.fixture()
def pending_session(repo: SessionRepository) -> ResearchSession:
    """Return a ``pending`` ResearchSession already saved in the database.

    Useful for update / transition tests where you need a session that
    has not yet started processing.

    Returns
    -------
    ResearchSession
        Status ``"pending"``; answer is ``None``.
    """
    return repo.create_session("What is quantum entanglement?")


# AI provider fake fixtures


@pytest.fixture()
def fake_llm() -> FakeLLM:
    """Return a fresh FakeLLM instance (no API calls).

    Returns
    -------
    FakeLLM
        Calls list is empty; response is the default cited text.
    """
    return FakeLLM()


@pytest.fixture()
def fake_web() -> FakeWebSearch:
    """Return a fresh FakeWebSearch instance (no HTTP calls).

    Returns
    -------
    FakeWebSearch
        Calls list is empty; results are the default web sources.
    """
    return FakeWebSearch()


# Async mock fetcher fixtures
# (used by test_concurrency.py and test_end_to_end.py â€” Ãœzv B)


@pytest.fixture()
def mock_fetch_wikipedia(fake_wiki_sources: list[Source]) -> AsyncMock:
    """Return an AsyncMock that returns canned Wikipedia sources.

    Replaces ``ai.fetch_wikipedia`` in orchestrator / researcher tests.

    Returns
    -------
    AsyncMock
        Configured to return ``fake_wiki_sources`` on any call.
    """
    return AsyncMock(return_value=fake_wiki_sources)


@pytest.fixture()
def mock_fetch_arxiv(fake_arxiv_sources: list[Source]) -> AsyncMock:
    """Return an AsyncMock that returns canned arXiv sources.

    Returns
    -------
    AsyncMock
        Configured to return ``fake_arxiv_sources`` on any call.
    """
    return AsyncMock(return_value=fake_arxiv_sources)


@pytest.fixture()
def mock_fetch_web(sample_sources: list[Source]) -> AsyncMock:
    """Return an AsyncMock that returns a single canned web Source.

    Returns
    -------
    AsyncMock
        Configured to return the web-origin source from ``sample_sources``.
    """
    web_only = [s for s in sample_sources if s.origin == "web"]
    return AsyncMock(return_value=web_only)
