from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_research_endpoint_offline_returns_citations() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/research",
        json={
            "question": "What is photosynthesis?",
            "sources": ["wiki", "arxiv"],
            "limit": 2,
            "offline": True,
            "no_cache": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["question"] == "What is photosynthesis?"
    assert payload["answer"]
    assert len(payload["citations"]) == 2
    assert {item["origin"] for item in payload["citations"]} == {"wikipedia", "arxiv"}