"""Offline fakes for local CLI demos and deterministic scripts."""

from __future__ import annotations

from typing import Any

from ai import Source
from ai.providers.base import LLMProvider


class OfflineLLM(LLMProvider):
    """Small deterministic LLM replacement for local demos."""

    def complete(self, prompt: str, *, json_schema: dict | None = None, max_tokens: int = 1024) -> str:
        import re

        indices = re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE)
        if not indices:
            return "I cannot answer from the available sources."
        cited = ", ".join(f"[{i}]" for i in indices[:3])
        return (
            "The available sources describe the topic from complementary angles "
            f"and support a concise research answer {cited}. Key details should "
            "be compared across the retrieved references before drawing a final conclusion [1]."
        )


async def fetch_offline_wikipedia(query: str, *, max_results: int = 3, client: Any = None) -> list[Source]:
    return [
        Source(
            title=f"Wikipedia overview: {query}",
            url=f"https://example.com/wiki/{_slug(query)}",
            snippet=f"An encyclopedia-style overview of {query}.",
            origin="wikipedia",
        )
    ][:max_results]


async def fetch_offline_arxiv(query: str, *, max_results: int = 3, client: Any = None) -> list[Source]:
    return [
        Source(
            title=f"Research paper about {query}",
            url=f"https://example.com/arxiv/{_slug(query)}",
            snippet=f"A research abstract discussing technical aspects of {query}.",
            origin="arxiv",
        )
    ][:max_results]


async def fetch_offline_web(query: str, *, max_results: int = 3, client: Any = None) -> list[Source]:
    return [
        Source(
            title=f"Web article: {query}",
            url=f"https://example.com/web/{_slug(query)}",
            snippet=f"A public web article with practical context about {query}.",
            origin="web",
        )
    ][:max_results]


def offline_fetchers():
    return {
        "wikipedia": fetch_offline_wikipedia,
        "arxiv": fetch_offline_arxiv,
        "web": fetch_offline_web,
    }


def _slug(value: str) -> str:
    return "-".join(value.lower().split())[:80]

