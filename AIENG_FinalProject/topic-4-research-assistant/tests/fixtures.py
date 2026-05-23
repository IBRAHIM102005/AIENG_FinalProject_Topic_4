"""
Shared test fixtures for Async Research Assistant.

Design goals:
- Fully deterministic tests (no real network / IO side effects)
- Lightweight fake client instead of MagicMock
- Centralized service initialization
"""


from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.config import Settings
from src.services.ai_service import AIService
from src.services.cache import CacheService


# FakeAIClient — deterministic stub (no MagicMock)

class FakeAIClient:
    """
    Typed test double for the AIClient protocol.

    Attributes are plain AsyncMocks so each test can set
    return_value / side_effect without touching internals.
    """

    def __init__(
        self,
        *,
        wikipedia: list[Any] | None = None,
        arxiv: list[Any] | None = None,
        web: list[Any] | None = None,
        synthesize_return: Any = None,
    ) -> None:
        self.fetch_wikipedia: AsyncMock = AsyncMock(return_value=wikipedia or [])
        self.fetch_arxiv: AsyncMock = AsyncMock(return_value=arxiv or [])
        self.fetch_web: AsyncMock = AsyncMock(return_value=web or [])
        self.synthesize: AsyncMock = AsyncMock(return_value=synthesize_return)


# Settings factory

def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        llm_provider="anthropic",
        llm_model="claude-sonnet-4-6",
        anthropic_api_key="test-key",
        cache_dir=tmp_path / "cache",
        cache_ttl_seconds=3600,
        retry_max_attempts=3,
        retry_min_wait_seconds=0.01,
        retry_max_wait_seconds=0.05,
        per_source_timeout_seconds=5.0,
    )


# Pytest fixtures (importable via conftest or direct import)

@pytest.fixture
def fake_client() -> FakeAIClient:
    return FakeAIClient()


@pytest.fixture
def ai_service(fake_client: FakeAIClient) -> AIService:
    return AIService(client=fake_client)


@pytest.fixture
def file_cache(tmp_path: Path) -> CacheService:
    return CacheService(cache_dir=tmp_path / "cache", ttl_seconds=3600)


@pytest.fixture
def memory_cache(tmp_path: Path) -> CacheService:
    """In-memory-style cache backed by a temp dir; TTL large enough not to expire mid-test."""
    return CacheService(cache_dir=tmp_path / "mem_cache", ttl_seconds=3600)