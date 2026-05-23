"""
CacheService integration tests

Focus:
- Public API correctness (get / set / invalidate / clear)
- Safe failure handling (missing keys, corruption, disabled cache)
- Same contract across file + memory backends
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.cache import CacheService
from tests.fixtures import file_cache, memory_cache  # noqa: F401


# ============================================================
# File-based cache tests
# ============================================================

class TestFileCacheService:

    @pytest.mark.asyncio
    async def test_missing_key_returns_none(self, file_cache: CacheService) -> None:
        result = await file_cache.get("wikipedia", "unknown")
        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "source, query, payload",
        [
            ("arxiv", "quantum physics",
             [{"title": "Atom", "url": "https://arxiv.org/abs/0001"}]),

            ("wikipedia", "PHOTOSYNTHESIS",
             [{"title": "Photosynthesis"}]),

            ("web", "latest ai news",
             [{"url": "https://example.com"}]),
        ],
    )
    async def test_set_get_returns_value(
        self,
        file_cache: CacheService,
        source: str,
        query: str,
        payload: list[Any],
    ) -> None:
        await file_cache.set(source, query, payload)

        result = await file_cache.get(source, query.lower())
        assert result == payload

    @pytest.mark.asyncio
    async def test_invalidate_removes_entry(self, file_cache: CacheService) -> None:
        await file_cache.set("web", "photosynthesis", [{"x": 1}])

        removed = await file_cache.invalidate("web", "photosynthesis")

        assert removed is True
        assert await file_cache.get("web", "photosynthesis") is None

    @pytest.mark.asyncio
    async def test_clear_removes_all_entries(self, file_cache: CacheService) -> None:
        await file_cache.set("wikipedia", "q1", [{"a": 1}])
        await file_cache.set("arxiv", "q2", [{"b": 2}])

        await file_cache.clear()

        assert await file_cache.get("wikipedia", "q1") is None
        assert await file_cache.get("arxiv", "q2") is None

    @pytest.mark.asyncio
    async def test_corrupt_file_returns_none(self, file_cache: CacheService) -> None:
        """
        Cache should handle corrupted data gracefully and return None.
        """
        await file_cache.set("web", "test", [{"x": 1}])

        # simplified corruption simulation at read level
        with pytest.MonkeyPatch().context() as mp:
            def fake_read(*args, **kwargs):
                raise ValueError("invalid json")

            mp.setattr(Path, "read_text", fake_read)

            result = await file_cache.get("web", "test")

        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_cache_returns_none(self, tmp_path: Path) -> None:
        cache = CacheService(cache_dir=tmp_path / "disabled", ttl_seconds=0)

        await cache.set("wikipedia", "question", [{"data": True}])

        result = await cache.get("wikipedia", "question")
        assert result is None


# ============================================================
# Memory cache tests
# ============================================================

class TestMemoryCacheService:

    @pytest.mark.asyncio
    async def test_set_get_returns_value(self, memory_cache: CacheService) -> None:
        await memory_cache.set("wikipedia", "memory test", [{"title": "data"}])

        result = await memory_cache.get("wikipedia", "memory test")
        assert result == [{"title": "data"}]

    @pytest.mark.asyncio
    async def test_invalidate_removes_entry(self, memory_cache: CacheService) -> None:
        await memory_cache.set("arxiv", "topic", [{"id": "001"}])

        removed = await memory_cache.invalidate("arxiv", "topic")

        assert removed is True
        assert await memory_cache.get("arxiv", "topic") is None
