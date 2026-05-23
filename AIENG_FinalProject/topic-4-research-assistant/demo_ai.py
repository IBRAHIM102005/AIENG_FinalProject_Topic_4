"""Demo harness for the Topic 4 AI module.

Two modes:
  python demo_ai.py             # real LLM + live sources (parallel)
  python demo_ai.py --offline   # fake LLM + canned sources (no network)
  python demo_ai.py --bench     # sequential vs concurrent wall-clock comparison

Wikipedia fix: a shared httpx.AsyncClient with the correct bot User-Agent is
passed to ai.fetch_wikipedia — the ai/ module uses it for BOTH the opensearch
call AND every per-article summary call, so no ai/ files are modified.

"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx

from ai import (
    Source, AnswerWithCitations,
    fetch_wikipedia, fetch_arxiv, fetch_web,
    synthesize,
)
from ai.providers.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)

# ============================================================
# Shared HTTP client configuration
# ============================================================
#
# Wikipedia REST API returns HTTP 403 for requests without a descriptive
# User-Agent (httpx's default is blocked).  By creating ONE AsyncClient with
# the correct headers and passing it to ai.fetch_wikipedia, the ai/sources.py
# module uses our client for both the opensearch call AND every per-article
_WIKI_USER_AGENT = (
    "ResearchAssistant/1.0 (AIENG-110 student project; "
    "contact: student@aiacademy.az)"
)

_CLIENT_KWARGS: dict = {
    "timeout": 20.0,
    "follow_redirects": True,
    "headers": {"User-Agent": _WIKI_USER_AGENT},
}


# ============================================================
# Wikipedia query extraction
# ============================================================

# Content words likely to appear as Wikipedia article titles.
# Verbs, question words, articles, and prepositions are stripped.
_STOP_WORDS = frozenset({
    "what", "is", "are", "how", "does", "do", "why", "when", "where",
    "which", "who", "the", "a", "an", "of", "in", "at", "to", "and",
    "its", "their", "were", "was", "has", "have", "had", "be", "been",
    "main", "current", "state", "work", "works", "handle", "level",
    "molecular", "give", "explain", "describe", "tell", "me", "us",
    "between", "difference", "differences", "role", "roles", "use",
    "used", "using", "effect", "effects", "impact", "cause", "causes",
    "can", "could", "would", "should", "will", "some", "any", "all",
    "this", "that", "these", "those", "with", "without", "from", "for",
    "on", "by", "as", "or", "but", "not", "also", "about", "into",
    "during", "after", "before", "between", "through", "more", "most",
})

# Known multi-word Wikipedia titles — keep them as a single token.
# Patterns are written to handle plurals and common variants:
#   "black hole" | "black holes" | "Black Holes"
#   "neural network" | "neural networks"
#   "CRISPR" | "CRISPR-Cas9" | "applications of CRISPR"
_COMPOUND_TITLES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bCRISPR\b", re.I),                          "CRISPR"),           # check CRISPR first (greedy)
    (re.compile(r"\bquantum computing\b", re.I),               "quantum computing"),
    (re.compile(r"\bquantum entanglement\b", re.I),            "quantum entanglement"),
    (re.compile(r"\bmachine learning\b", re.I),                "machine learning"),
    (re.compile(r"\bneural networks?\b", re.I),                "neural network"),   # singular + plural
    (re.compile(r"\bdeep learning\b", re.I),                   "deep learning"),
    (re.compile(r"\bblack holes?\b", re.I),                    "black hole"),       # singular + plural
    (re.compile(r"\bclimate change\b", re.I),                  "climate change"),
    (re.compile(r"\bnatural language processing\b", re.I),     "natural language processing"),
    (re.compile(r"\bartificial intelligence\b", re.I),         "artificial intelligence"),
    (re.compile(r"\bglobal warming\b", re.I),                  "global warming"),
    (re.compile(r"\bDNA replication\b", re.I),                 "DNA replication"),
    (re.compile(r"\bcell division\b", re.I),                   "cell division"),
    (re.compile(r"\bnatural selection\b", re.I),               "natural selection"),
]


def _wiki_query(question: str) -> str:
    """
    Extract the single most important keyword (or short compound) from a
    research question to use as the Wikipedia opensearch query.

    Strategy:
        1. Detect known multi-word Wikipedia titles first (e.g. "machine learning").
        2. Otherwise strip stop-words and punctuation, keep content words.
        3. Return only the FIRST content word — Wikipedia's opensearch is a
           title prefix search; a single strong noun hits more articles than a
           multi-word phrase that rarely appears verbatim in a title.

    Examples:
        "What is photosynthesis and what are its main stages?"  → "photosynthesis"
        "How does machine learning work?"                        → "machine learning"
        "Explain the effects of climate change on ecosystems"   → "climate change"
        "What are the main applications of CRISPR?"             → "CRISPR"
        "How does DNA replication work?"                        → "DNA"
    """
    # Step 1 — check for known compound titles (order matters: longest first)
    for pattern, compound in _COMPOUND_TITLES:
        if pattern.search(question):
            logger.debug("wiki_query compound_match: %r → %r", question, compound)
            return compound

    # Step 2 — strip punctuation, lowercase, tokenize
    clean = re.sub(r"[^\w\s]", " ", question).lower()
    words = clean.split()

    # Step 3 — keep content words; prefer longer words (more specific nouns)
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 2]

    if not keywords:
        # Fallback: original question truncated — better than empty string
        return question[:60]

    # Step 4 — return only the SINGLE most prominent keyword.
    # "photosynthesis" hits the article directly; "photosynthesis stages" may not.
    best = max(keywords, key=len)
    logger.debug("wiki_query: %r → %r  (all_keywords=%r)", question, best, keywords)
    return best


# ============================================================
# Offline fakes
# ============================================================

class _OfflineLLM(LLMProvider):
    """Returns a deterministic cited answer without any API call."""

    def complete(self, prompt: str, *, json_schema=None, max_tokens: int = 1024) -> str:
        indices = re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE)
        n = len(indices)
        if n == 0:
            return "I cannot answer from the available sources."
        cited = ", ".join(f"[{i}]" for i in range(1, min(n, 3) + 1))
        return (
            f"Based on the available sources, here is a synthesized answer "
            f"drawing from {cited}. The sources broadly agree on the main points; "
            f"differences in emphasis are noted in each reference [1]."
        )


class _OfflineSources:
    """Canned Source lists keyed by keyword — zero network traffic."""

    _DB: dict[str, list[Source]] = {
        "photosynthesis": [
            Source(
                title="Photosynthesis",
                url="https://en.wikipedia.org/wiki/Photosynthesis",
                snippet="Photosynthesis converts light energy into chemical energy stored in glucose.",
                origin="wikipedia",
            ),
            Source(
                title="Light-Dependent Reactions of Photosynthesis",
                url="https://arxiv.org/abs/1706.03762",
                snippet="A review of light-dependent reactions and the oxygen evolution complex.",
                origin="arxiv",
            ),
            Source(
                title="How Plants Make Food",
                url="https://example.com/plants",
                snippet="Plants use chlorophyll to produce glucose from CO₂ and water.",
                origin="web",
            ),
        ],
        "transformer": [
            Source(
                title="Transformer (machine learning model)",
                url="https://en.wikipedia.org/wiki/Transformer_(machine_learning)",
                snippet="A transformer adopts the mechanism of self-attention.",
                origin="wikipedia",
            ),
            Source(
                title="Attention Is All You Need",
                url="https://arxiv.org/abs/1706.03762",
                snippet="We propose the Transformer, a model based solely on attention mechanisms.",
                origin="arxiv",
            ),
            Source(
                title="The Illustrated Transformer",
                url="https://example.com/illustrated-transformer",
                snippet="A visual walkthrough of the Transformer architecture.",
                origin="web",
            ),
        ],
        "quantum": [
            Source(
                title="Quantum computing",
                url="https://en.wikipedia.org/wiki/Quantum_computing",
                snippet="Quantum computing uses quantum-mechanical phenomena to perform computation.",
                origin="wikipedia",
            ),
            Source(
                title="Quantum advantage in machine learning",
                url="https://arxiv.org/abs/2001.00030",
                snippet="Recent results on quantum advantage for specific computational tasks.",
                origin="arxiv",
            ),
        ],
    }

    @classmethod
    def fetch(cls, query: str) -> list[Source]:
        q = query.lower()
        for keyword, sources in cls._DB.items():
            if keyword in q:
                return sources
        return [
            Source(
                title=f"Overview: {query[:60]}",
                url="https://example.com/generic",
                snippet=f"A general overview of the topic: {query}",
                origin="web",
            )
        ]


# ============================================================
# Live source fetching
# ============================================================

async def fetch_all_sources_live(
    question: str,
    *,
    sources_filter: set[str] | None = None,
) -> tuple[list[Source], dict[str, float]]:
    """
    Fetch Wikipedia, arXiv, and web results in parallel.

    Key design decisions:
      - Wikipedia and arXiv receive a SHORT KEYWORD (via _wiki_query), not the
        full question sentence.  Both are title/keyword search APIs — sending a
        full question sentence returns zero results.
      - Web search (Tavily/Serper) receives the FULL question because it handles
        natural language queries well.
      - A hard 5-second timeout per source prevents arXiv rate-limit hangs.
      - The shared AsyncClient is passed to ai.fetch_wikipedia so it applies
        the correct User-Agent for both opensearch and summary calls.

    Returns:
        sources: deduplicated list[Source]
        timings: per-source wall-clock seconds {source: elapsed}
    """
    enabled = sources_filter or {"wikipedia", "arxiv", "web"}
    per_source_times: dict[str, float] = {}

    # Extract the single best keyword for title-based APIs
    kw = _wiki_query(question)
    logger.debug("wiki/arxiv keyword: %r → %r", question, kw)

    async def _timed(label: str, coro) -> list[Source]:
        t0 = time.monotonic()
        try:
            # 5-second hard cap — arXiv can hang 15-18s before returning 429
            result = await asyncio.wait_for(coro, timeout=5.0)
            per_source_times[label] = round(time.monotonic() - t0, 3)
            return result
        except asyncio.TimeoutError:
            per_source_times[label] = round(time.monotonic() - t0, 3)
            logger.warning("source_timeout label=%s (>5s) — skipping", label)
            return []
        except Exception as exc:
            per_source_times[label] = round(time.monotonic() - t0, 3)
            logger.warning("source_failed label=%s error=%s", label, str(exc)[:120])
            return []

    async with httpx.AsyncClient(**_CLIENT_KWARGS) as client:
        tasks = []

        if "wikipedia" in enabled:
            tasks.append(_timed(
                "wikipedia",
                # Pass keyword query + shared client (fixes User-Agent 403)
                fetch_wikipedia(kw, max_results=2, client=client),
            ))
        if "arxiv" in enabled:
            tasks.append(_timed(
                "arxiv",
                # arXiv title search also works better with keywords
                fetch_arxiv(kw, max_results=2, client=client),
            ))
        if "web" in enabled:
            tasks.append(_timed(
                "web",
                # Web search handles natural language — use full question
                fetch_web(question, max_results=3, client=client),
            ))

        batches = await asyncio.gather(*tasks, return_exceptions=False)

    # Deduplicate by URL while preserving insertion order
    seen: set[str] = set()
    sources: list[Source] = []
    for batch in batches:
        for s in batch:
            if s.url not in seen:
                seen.add(s.url)
                sources.append(s)

    return sources, per_source_times


async def fetch_all_sources_sequential(
    question: str,
    *,
    sources_filter: set[str] | None = None,
) -> tuple[list[Source], dict[str, float]]:
    """
    Fetch sources one-by-one (for benchmark comparison only).

    Uses the same shared client and keyword extraction as the parallel version
    so timing differences reflect concurrency, not setup overhead.
    """
    enabled = sources_filter or {"wikipedia", "arxiv", "web"}
    per_source_times: dict[str, float] = {}
    sources: list[Source] = []
    seen: set[str] = set()

    kw = _wiki_query(question)
    async with httpx.AsyncClient(**_CLIENT_KWARGS) as client:
        fetch_map = {
            "wikipedia": lambda: fetch_wikipedia(kw, max_results=2, client=client),
            "arxiv":     lambda: fetch_arxiv(kw, max_results=2, client=client),
            "web":       lambda: fetch_web(question, max_results=3, client=client),
        }
        for label, coro_fn in fetch_map.items():
            if label not in enabled:
                continue
            t0 = time.monotonic()
            try:
                batch = await coro_fn()
                per_source_times[label] = round(time.monotonic() - t0, 3)
                for s in batch:
                    if s.url not in seen:
                        seen.add(s.url)
                        sources.append(s)
            except Exception as exc:
                per_source_times[label] = round(time.monotonic() - t0, 3)
                logger.warning("sequential_source_failed label=%s error=%s", label, exc)

    return sources, per_source_times


async def fetch_all_sources_offline(question: str) -> list[Source]:
    """Return canned sources — no network, no API keys required."""

    async def _canned(label: str) -> list[Source]:
        batch = _OfflineSources.fetch(question)
        return [s for s in batch if s.origin == label] or batch[:1]

    results = await asyncio.gather(
        _canned("wikipedia"),
        _canned("arxiv"),
        _canned("web"),
    )
    seen: set[str] = set()
    out: list[Source] = []
    for batch in results:
        for s in batch:
            if s.url not in seen:
                seen.add(s.url)
                out.append(s)
    return out


# ============================================================
# Render helpers
# ============================================================

_SOURCE_ICON = {
    "wikipedia": "📖",
    "arxiv":     "📄",
    "web":       "🌐",
}


def render(answer: AnswerWithCitations) -> str:
    """Format a cited answer for clean, human-readable terminal output."""
    import textwrap

    W = 72  # box width

    lines: list[str] = []

    # ── Question banner ───────────────────────────────────────────────────────
    lines.append("┌" + "─" * (W - 2) + "┐")
    q_words = answer.question.split()
    q_line, q_chunks = "", []
    for word in q_words:
        if len(q_line) + len(word) + 1 > W - 8:
            q_chunks.append(q_line.strip())
            q_line = word
        else:
            q_line += (" " if q_line else "") + word
    if q_line:
        q_chunks.append(q_line.strip())
    for i, chunk in enumerate(q_chunks):
        prefix = "❓  " if i == 0 else "    "
        lines.append(f"│  {prefix}{chunk:<{W - 8}}│")
    lines.append("└" + "─" * (W - 2) + "┘")
    lines.append("")

    # ── Answer body ───────────────────────────────────────────────────────────
    lines.append("  💡 Answer")
    lines.append("  " + "─" * (W - 4))
    for para in answer.answer.split("\n"):
        wrapped = textwrap.fill(
            para.strip(), width=W - 4,
            initial_indent="  ", subsequent_indent="  ",
        )
        lines.append(wrapped)
    lines.append("")

    # ── References ────────────────────────────────────────────────────────────
    if answer.citations:
        lines.append("  📚 References")
        lines.append("  " + "─" * (W - 4))
        for c in answer.citations:
            icon = _SOURCE_ICON.get(c.source.origin, "🔗")
            title = c.source.title
            if len(title) > W - 14:
                title = title[: W - 17] + "…"
            lines.append(f"  [{c.index}] {icon}  {title}")
            lines.append(f"       {c.source.url}")
        lines.append("")

    return "\n".join(lines)

# ============================================================
# Single-question runner
# ============================================================

async def run_one(
    question: str,
    offline: bool,
    llm: LLMProvider | None,
    sources_filter: set[str] | None = None,
) -> None:
    W = 72
    mode_tag = " [OFFLINE]" if offline else ""
    print()
    print("━" * W)
    print(f"  🔍 Researching{mode_tag}")
    print("━" * W)
    print()

    if offline:
        sources = await fetch_all_sources_offline(question)
        timings: dict[str, float] = {}
    else:
        sources, timings = await fetch_all_sources_live(
            question, sources_filter=sources_filter
        )

    if not sources:
        print("  ⚠️  No sources retrieved — cannot synthesize.")
        print()
        return

    wiki_n  = sum(s.origin == "wikipedia" for s in sources)
    arxiv_n = sum(s.origin == "arxiv"     for s in sources)
    web_n   = sum(s.origin == "web"       for s in sources)

    # Source summary line
    parts = []
    if wiki_n:  parts.append(f"📖 {wiki_n} Wikipedia")
    if arxiv_n: parts.append(f"📄 {arxiv_n} arXiv")
    if web_n:   parts.append(f"🌐 {web_n} Web")
    print(f"  Sources fetched: {len(sources)} total  ·  {'  '.join(parts)}")

    if timings:
        timing_str = "  ".join(f"{k}: {v}s" for k, v in timings.items())
        print(f"  Timing: {timing_str}")
    print()

    try:
        answer = synthesize(question, sources, llm=llm)
    except (ProviderError, ValueError) as exc:
        logger.error("synthesis_failed error=%s", exc)
        print(f"  ❌  Synthesis failed: {exc}", file=sys.stderr)
        return

    print(render(answer))

# ============================================================
# Benchmark runner
# ============================================================

async def run_bench(question: str) -> None:
    """Compare parallel vs sequential fetching — prints a README-ready table."""
    W = 72
    print()
    print("━" * W)
    print("  ⚡ Benchmark: parallel vs sequential")
    print("━" * W)
    import textwrap
    q_wrapped = textwrap.fill(question, width=W - 4, initial_indent="  Q: ", subsequent_indent="     ")
    print(q_wrapped)
    print()

    t0 = time.monotonic()
    _, par_timings = await fetch_all_sources_live(question)
    par_total = round(time.monotonic() - t0, 3)

    t0 = time.monotonic()
    _, seq_timings = await fetch_all_sources_sequential(question)
    seq_total = round(time.monotonic() - t0, 3)

    speedup = round(seq_total / par_total, 2) if par_total > 0 else 0

    # Table
    def _row(label: str, t: dict[str, float], total: float) -> str:
        wiki  = f"{t.get('wikipedia', 0):.3f}s"
        arxiv = f"{t.get('arxiv', 0):.3f}s"
        web   = f"{t.get('web', 0):.3f}s"
        return f"  {label:<12} │ {wiki:<10} │ {arxiv:<8} │ {web:<8} │ {total:.3f}s"

    print(f"  {'Mode':<12} │ {'Wikipedia':<10} │ {'arXiv':<8} │ {'Web':<8} │ Total")
    print("  " + "─" * 13 + "┼" + "─" * 11 + "┼" + "─" * 9 + "┼" + "─" * 9 + "┼" + "─" * 8)
    print(_row("🚀 parallel",  par_timings, par_total))
    print(_row("🐢 sequential", seq_timings, seq_total))
    print()
    print(f"  Speed-up: {speedup}×  ({seq_total:.3f}s sequential ÷ {par_total:.3f}s parallel)")
    print()
    print("  ▸ Reproduce:  python demo_ai.py --bench --limit 1")
    print()

# ============================================================
# Main entry point
# ============================================================

async def run_demo(
    offline: bool,
    limit: int,
    bench: bool,
    sources_filter: set[str] | None,
) -> None:
    here = Path(__file__).parent
    qfile = here / "data" / "research_questions.json"
    if not qfile.exists():
        logger.error("Missing questions file: %s", qfile)
        sys.exit(2)

    questions: list[dict] = json.loads(qfile.read_text())["questions"][:limit]
    llm: LLMProvider | None = _OfflineLLM() if offline else None

    # arXiv public API enforces ~1 req/sec per IP.
    # 3-second pause between questions keeps us well within that limit.
    # Skipped in offline mode (no network calls).
    ARXIV_POLITE_DELAY = 0.0 if offline else 3.0

    for i, q in enumerate(questions):
        if i > 0:
            await asyncio.sleep(ARXIV_POLITE_DELAY)
        if bench:
            await run_bench(q["text"])
        else:
            await run_one(q["text"], offline=offline, llm=llm, sources_filter=sources_filter)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="Use fake LLM and canned sources (no network, no API keys).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=2,
        metavar="N",
        help="How many questions to run (default: 2).",
    )
    p.add_argument(
        "--bench",
        action="store_true",
        help="Run parallel-vs-sequential benchmark instead of normal demo.",
    )
    p.add_argument(
        "--sources",
        default="wikipedia,arxiv,web",
        metavar="SOURCES",
        help="Comma-separated sources to query: wikipedia,arxiv,web (default: all).",
    )
    args = p.parse_args()

    sources_filter: set[str] | None = None
    if args.sources != "wikipedia,arxiv,web":
        sources_filter = {s.strip().lower() for s in args.sources.split(",")}

    asyncio.run(run_demo(
        offline=args.offline,
        limit=args.limit,
        bench=args.bench,
        sources_filter=sources_filter,
    ))


if __name__ == "__main__":
    main()