"""
Async Research Assistant — Cache Service

This module provides a simple, reliable async cache layer that:
- Stores results on disk (JSON files)
- Uses TTL (time-to-live) for automatic expiration
- Prevents race conditions with per-key locks
- Keeps cache size under control using LRU eviction

Why this exists:
Instead of repeatedly calling external APIs (slow + expensive),
we cache results locally and reuse them when possible.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Generic, TypeVar, cast

from src.config import get_settings
from src.models import CacheEntry

logger = logging.getLogger(__name__)

T = TypeVar("T")
_DEFAULT_MAX_ENTRIES = 1000


class CacheService(Generic[T]):
    """
    Async disk-based cache for (source, query) pairs.

    Key ideas:
    - Each cache entry = one JSON file
    - File name is derived from a hashed key
    - Expired entries are auto-deleted
    - Safe for concurrent usage (locks per key)
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_seconds: int | None = None,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        settings = get_settings()

        # Where cache files live
        self._dir: Path = cache_dir or settings.cache_dir

        # Default expiration time
        self._ttl: int = ttl_seconds if ttl_seconds is not None else settings.cache_ttl_seconds

        # Max number of cache files allowed
        self._max_entries: int = max_entries

        # Prevent concurrent writes to same key
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # =========================================================
    # Public API
    # =========================================================

    async def get(self, source: str, query: str) -> T | None:
        """
        Get cached value.

        Returns:
            - Cached data if exists and valid
            - None if not found / expired / corrupted
        """
        if self._is_disabled():
            return None

        if not await asyncio.to_thread(self._dir.exists):
            return None

        key = CacheEntry.make_key(source, query)
        path = self._entry_path(key)

        if not await asyncio.to_thread(path.exists):
            logger.debug("cache_miss", extra={"key": key[:16]})
            return None

        try:
            raw = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))

            entry = CacheEntry.model_validate(raw)

        except Exception as e:
            # File is broken → remove it
            logger.warning("Invalid cache file removed", extra={"error": str(e)})
            await asyncio.to_thread(path.unlink, True)
            return None

        if entry.is_expired():
            logger.debug("cache_expired", extra={"key": key[:16]})
            await asyncio.to_thread(path.unlink, True)
            return None

        logger.debug("cache_hit", extra={"key": key[:16]})
        return cast(T, entry.value)

    async def set(self, source: str, query: str, value: T) -> None:
        """
        Save data into cache.

        Guarantees:
        - Atomic writes (no corruption)
        - Safe under concurrency
        - Auto cleanup if cache too big
        """
        if self._is_disabled():
            return

        await asyncio.to_thread(self._dir.mkdir, 0o755, True, True)

        key = CacheEntry.make_key(source, query)

        async with self._locks[key]:
            entry = CacheEntry(key=key, value=value, ttl_seconds=self._ttl)

            path = self._entry_path(key)
            tmp_path = path.with_suffix(".tmp")

            try:
                # Write temp file first
                await asyncio.to_thread(tmp_path.write_text, entry.model_dump_json(), encoding="utf-8")

                # Atomic replace
                await asyncio.to_thread(os.replace, tmp_path, path)

                logger.debug("cache_saved", extra={"key": key[:16]})

            except OSError as e:
                logger.warning("Cache write failed", extra={"error": str(e)})
                await asyncio.to_thread(tmp_path.unlink, True)
                return

        await self._evict_if_needed()

    async def invalidate(self, source: str, query: str) -> bool:
        """
        Remove a specific cache entry.
        """
        key = CacheEntry.make_key(source, query)
        path = self._entry_path(key)

        if await asyncio.to_thread(path.exists):
            await asyncio.to_thread(path.unlink, True)
            logger.debug("cache_deleted", extra={"key": key[:16]})
            return True

        return False

    async def clear(self) -> int:
        """
        Delete ALL cache files.

        Returns:
            Number of deleted files
        """
        if not await asyncio.to_thread(self._dir.exists):
            return 0

        files = await asyncio.to_thread(lambda: list(self._dir.glob("cache_*.json")))

        for f in files:
            await asyncio.to_thread(f.unlink, True)

        logger.info("cache_cleared", extra={"count": len(files)})
        return len(files)

    # =========================================================
    # Internal helpers
    # =========================================================

    def _is_disabled(self) -> bool:
        """Cache disabled if TTL <= 0"""
        return self._ttl <= 0

    def _entry_path(self, key: str) -> Path:
        """
        Convert key → safe filename
        """
        safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", key)
        return self._dir / f"cache_{safe_key}.json"

    async def _evict_if_needed(self) -> None:
        """
        Remove oldest files if cache exceeds limit.
        """
        files = await asyncio.to_thread(lambda: list(self._dir.glob("cache_*.json")))

        if len(files) <= self._max_entries:
            return

        # Sort by last modified (oldest first)
        files_sorted = await asyncio.to_thread(
            lambda: sorted(files, key=lambda f: f.stat().st_mtime)
        )

        to_delete = files_sorted[: len(files) - self._max_entries]

        for f in to_delete:
            await asyncio.to_thread(f.unlink, True)

        logger.info("cache_evicted", extra={"removed": len(to_delete)})
