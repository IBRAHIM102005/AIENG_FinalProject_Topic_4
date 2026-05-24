from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import Source, fetch_arxiv, fetch_web, fetch_wikipedia
from src.concurrency.orchestrator import fetch_selected_sources


async def _fake_fetch(origin: str, delay: float, query: str, *, max_results: int = 3, client=None) -> list[Source]:
    await asyncio.sleep(delay)
    return [
        Source(
            title=f"{origin.title()} result",
            url=f"https://example.com/{origin}/{query.replace(' ', '-').lower()}",
            snippet=f"Offline benchmark result for {query}",
            origin="wikipedia" if origin == "wikipedia" else origin,
        )
    ][:max_results]

def offline_fetchers() -> dict[str, Callable[..., Awaitable[list[Source]]]]:
    return {
        "wikipedia": lambda query, **kw: _fake_fetch("wikipedia", 0.45, query, **kw),
        "arxiv": lambda query, **kw: _fake_fetch("arxiv", 0.65, query, **kw),
        "web": lambda query, **kw: _fake_fetch("web", 0.55, query, **kw),
    }


async def sequential(question: str, fetchers, max_results: int) -> float:
    started = time.perf_counter()
    for name in ("wikipedia", "arxiv", "web"):
        await fetchers[name](question, max_results=max_results, client=None)
    return time.perf_counter() - started


async def parallel(question: str, fetchers, max_results: int) -> float:
    started = time.perf_counter()
    await fetch_selected_sources(question, max_results=max_results, fetchers=fetchers)
    return time.perf_counter() - started


async def run(args: argparse.Namespace) -> None:
    fetchers = {
        "wikipedia": fetch_wikipedia,
        "arxiv": fetch_arxiv,
        "web": fetch_web,
    } if args.live else offline_fetchers()

    seq = await sequential(args.question, fetchers, args.limit)
    par = await parallel(args.question, fetchers, args.limit)

    print("Mode        Time (s)")
    print(f"Sequential  {seq:.3f}")
    print(f"Parallel    {par:.3f}")
    print(f"Speedup     {seq / par:.2f}x")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default="What is photosynthesis?")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--live", action="store_true", help="use real providers instead of offline fakes")
    args = parser.parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())