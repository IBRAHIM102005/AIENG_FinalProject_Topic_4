"""Click command line interface for the research assistant."""

from __future__ import annotations

import asyncio
import logging
import os

import click
import httpx
from dotenv import load_dotenv

# 🔥 CRITICAL: .env load (demo ilə eyni davranış)
load_dotenv()

from ai import AnswerWithCitations

from src.config import get_settings
from src.core.researcher import ResearchResult, research_question
from src.offline import OfflineLLM, offline_fetchers
from src.services.cache import CacheService


# ✅ Wikipedia 403 fix
_WIKI_USER_AGENT = (
    "ResearchAssistant/1.0 (AIENG-110 student project; contact: student@aiacademy.az)"
)


def _split_sources(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def render_answer(result: ResearchResult) -> str:
    """Render an answer and references for terminal output."""

    answer: AnswerWithCitations = result.answer
    lines = [f"Q: {answer.question}", "", f"A: {answer.answer}", ""]

    if result.failures:
        lines.append("Source notes:")
        for failure in result.failures:
            lines.append(f"  - {failure.source} failed: {failure.error}")
        lines.append("")

    if answer.citations:
        lines.append("References:")
        for citation in answer.citations:
            src = citation.source
            lines.append(f"  [{citation.index}] ({src.origin}) {src.title}")
            lines.append(f"      {src.url}")

    return "\n".join(lines)


@click.group()
def cli() -> None:
    """Async research assistant."""

    settings = get_settings()

    # 🔥 env sync (AI lib üçün vacibdir)
    try:
        settings.export_to_environ()
    except Exception:
        pass

    logging.basicConfig(level=settings.log_level)

    # debug
    logging.info(f"LLM_PROVIDER={os.getenv('LLM_PROVIDER')}")
    logging.info(f"OPENAI_API_KEY={'SET' if os.getenv('OPENAI_API_KEY') else 'MISSING'}")
    logging.info(f"AZURE_OPENAI_ENDPOINT={os.getenv('AZURE_OPENAI_ENDPOINT')}")


@cli.command()
@click.argument("question")
@click.option("--sources", help="Comma-separated list: wiki,arxiv,web.")
@click.option("--limit", type=int, default=lambda: get_settings().max_sources_per_query)
@click.option("--timeout", type=float, default=lambda: get_settings().per_source_timeout_seconds)
@click.option("--concurrency", type=int, default=lambda: get_settings().concurrency_semaphore_limit)
@click.option("--no-cache", is_flag=True)
@click.option("--offline", is_flag=True)
def ask(
    question: str,
    sources: str | None,
    limit: int,
    timeout: float,
    concurrency: int,
    no_cache: bool,
    offline: bool,
) -> None:
    """Answer QUESTION with cited research sources."""

    settings = get_settings()

    cache = None if no_cache else CacheService(
        cache_dir=settings.cache_dir,
        ttl_seconds=settings.cache_ttl_seconds,
    )

    async def _run():
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": _WIKI_USER_AGENT},  # 🔥 ƏSAS FIX
        ) as client:

            return await research_question(
                question,
                sources=_split_sources(sources),
                max_results=limit,
                per_source_timeout=timeout,
                semaphore_limit=concurrency,
                fetchers=offline_fetchers() if offline else None,
                llm=OfflineLLM() if offline else None,
                cache=cache,
                client=client,  # 🔥 BURDA ötürülür
            )

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(render_answer(result))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()