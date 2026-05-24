"""FastAPI demo layer for the Async Research Assistant."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.config import get_settings
from src.core.researcher import research_question
from src.offline import OfflineLLM, offline_fetchers
from src.services.cache import CacheService

load_dotenv()

logger = logging.getLogger(__name__)
settings = get_settings()
settings.export_to_environ()
logging.basicConfig(level=settings.log_level)

WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(
    title="Async Research Assistant",
    version="1.0.0",
    description="Demo API for concurrent research with citations.",
)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class ResearchRequest(BaseModel):
    """Browser/API request body for a research query."""

    question: str = Field(min_length=1, max_length=settings.max_question_length)
    sources: list[Literal["wiki", "wikipedia", "arxiv", "web"]] = Field(
        default_factory=lambda: ["wiki", "arxiv", "web"]
    )
    limit: int = Field(default=3, ge=1, le=5)
    timeout: float = Field(default=settings.per_source_timeout_seconds, gt=0, le=30)
    concurrency: int = Field(default=settings.concurrency_semaphore_limit, ge=1, le=10)
    offline: bool = False
    no_cache: bool = False


@app.get("/")
async def index() -> FileResponse:
    """Serve the browser demo."""

    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Small health check for Docker/server demos."""

    return {"status": "ok"}


@app.post("/api/research")
async def research(payload: ResearchRequest) -> dict:
    """Run the research pipeline and return structured JSON."""

    cache = None if payload.no_cache else CacheService(
        cache_dir=settings.cache_dir,
        ttl_seconds=settings.cache_ttl_seconds,
    )

    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "ResearchAssistant/1.0 demo"},
        ) as client:
            result = await research_question(
                payload.question,
                sources=payload.sources,
                max_results=payload.limit,
                per_source_timeout=payload.timeout,
                semaphore_limit=payload.concurrency,
                fetchers=offline_fetchers() if payload.offline else None,
                llm=OfflineLLM() if payload.offline else None,
                cache=cache,
                client=client,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("api_research_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "question": result.answer.question,
        "answer": result.answer.answer,
        "elapsed_seconds": result.elapsed_seconds,
        "timings": result.timings,
        "failures": [
            {
                "source": failure.source,
                "error": failure.error,
                "elapsed_seconds": failure.elapsed_seconds,
            }
            for failure in result.failures
        ],
        "citations": [
            {
                "index": citation.index,
                "origin": citation.source.origin,
                "title": citation.source.title,
                "url": citation.source.url,
                "snippet": citation.source.snippet,
            }
            for citation in result.answer.citations
        ],
    }