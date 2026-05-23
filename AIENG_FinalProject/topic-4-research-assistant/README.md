# Async Research Assistant

Topic 4 implementation for the AI Engineering Software Engineering final project.
The project wraps the provided `ai/` package with configuration, retry-safe
services, async orchestration, persistent storage, CLI tools, tests, Docker, and
demo artefacts.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

The offline demo and tests do not require API keys. Live provider calls use
values from `.env`.

## CLI

Run a deterministic offline question:

```powershell
python -m researcher ask "What is photosynthesis?" --offline --limit 2
```

The implementation also works through the package module:

```powershell
python -m src.cli ask "What is photosynthesis?" --offline --limit 2
```

Useful options:

- `--sources wiki,arxiv,web`
- `--limit 3`
- `--timeout 10`
- `--concurrency 5`
- `--no-cache`
- `--offline`

## Web Demo

Start the API and browser UI:

```powershell
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000
```

The same research pipeline is available through JSON:

```powershell
curl -X POST http://localhost:8000/api/research `
  -H "Content-Type: application/json" `
  -d "{\"question\":\"What is quantum computing?\",\"sources\":[\"wiki\",\"arxiv\"],\"limit\":2,\"offline\":true}"
```

Use `offline=true` for deterministic demos without API keys, or leave it false
to use the live providers configured in `.env`.

## Benchmark And Demo

Compare sequential and parallel source fetching:

```powershell
python scripts\bench.py
```

Current measured result on the submission machine:

```text
Mode        Time (s)
Sequential  1.680
Parallel    0.653
Speedup     2.57x
```

Run the sample questions:

```powershell
python scripts\demo.py --limit 5
```

## Tests

```powershell
pytest -q
```

Current verified result:

```text
196 passed
```

Coverage command:

```powershell
pytest --cov=src --cov-report=term-missing -q
```

Current verified coverage:

```text
TOTAL 864 statements, 187 missed, 78% coverage
```

## Architecture

- `src/config.py`: Pydantic settings and environment validation.
- `src/models.py`: cache/session domain models.
- `src/services/ai_service.py`: retry, timeout, logging, graceful degradation.
- `src/services/cache.py`: TTL source cache.
- `src/concurrency/orchestrator.py`: async source fetching with semaphore and timeout.
- `src/core/researcher.py`: research workflow and synthesis.
- `src/cli.py`: Click command line interface.
- `src/api.py`: FastAPI backend for the browser demo.
- `src/web/`: static browser UI for interactive demonstrations.
- `src/storage/cache_store.py`: SQLite cache storage.
- `src/storage/repository.py`: persisted research sessions.

## Contribution Split

- Member Ibrahim: configuration, models, AI service wrapper, cache service, service tests.
- Member Fidan: async orchestration, researcher workflow, CLI, benchmark/demo, concurrency and E2E tests.
- Member Fatma: SQLite storage, repository, Docker, requirements, shared fixtures, core tests, README and artefacts.

## Docker

```powershell
docker build -t async-research-assistant .
docker run --rm async-research-assistant
```

Use an env file for live provider calls:

```powershell
docker run --rm --env-file .env async-research-assistant
```

Run the web demo in Docker:

```powershell
docker run --rm --env-file .env -p 8000:8000 async-research-assistant uvicorn src.api:app --host 0.0.0.0 --port 8000
```
