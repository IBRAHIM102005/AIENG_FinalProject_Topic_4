from __future__ import annotations

import pytest

from ai import Source
from src.core.researcher import research_question, validate_question


async def _wiki(query: str, *, max_results: int = 3, client=None) -> list[Source]:
    return [
        Source(
            title="Photosynthesis",
            url="https://en.wikipedia.org/wiki/Photosynthesis",
            snippet="Plants convert light energy into chemical energy.",
            origin="wikipedia",
        )
    ]


async def _arxiv(query: str, *, max_results: int = 3, client=None) -> list[Source]:
    return [
        Source(
            title="Light reactions",
            url="https://arxiv.org/abs/1234.5678",
            snippet="Light reactions support oxygen evolution.",
            origin="arxiv",
        )
    ]


async def _web_down(query: str, *, max_results: int = 3, client=None) -> list[Source]:
    raise RuntimeError("web quota exceeded")


def test_validate_question_canonicalizes_whitespace():
    assert validate_question("  what   is   photosynthesis? ") == "what is photosynthesis?"


@pytest.mark.asyncio
async def test_research_question_returns_answer_with_degraded_sources(fake_llm):
    result = await research_question(
        "What is photosynthesis?",
        fetchers={"wikipedia": _wiki, "arxiv": _arxiv, "web": _web_down},
        llm=fake_llm,
    )

    assert result.answer.question == "What is photosynthesis?"
    assert len(result.sources) == 2
    assert result.failures[0].source == "web"
    assert {citation.index for citation in result.answer.citations} == {1, 2}