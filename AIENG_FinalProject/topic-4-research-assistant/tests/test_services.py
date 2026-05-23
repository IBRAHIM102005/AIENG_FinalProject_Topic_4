"""Compatibility service tests for the Member A checklist.

The detailed service coverage lives in test_ai_service.py, test_cache_service.py,
and test_cache_entry.py. This file keeps the checklist's expected filename while
checking the public service entry points.
"""

from __future__ import annotations

from src.services.ai_service import AIService, FailoverAIClient, SourceResult
from src.services.cache import CacheService


def test_service_public_entry_points_are_importable() -> None:
    assert AIService is not None
    assert FailoverAIClient is not None
    assert SourceResult.degraded("web", "down").success is False


def test_cache_service_can_be_constructed(tmp_path) -> None:
    cache = CacheService(cache_dir=tmp_path, ttl_seconds=60)
    assert cache is not None
