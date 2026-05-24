## What this PR changes

Merges the complete Topic 4 – Async Research Assistant implementation into `main`
for final submission. Adds `src/`, `tests/`, `scripts/`, `src/web/`, `Dockerfile`,
`requirements.txt`, CI workflows, and all supporting artefacts. The provided
`ai/` package is untouched.

## Why

Final project submission for AI-ENG-110 – Software Engineering – Spring 2026.
Implements all 10 build requirements from `SOFTWARE_PROJECT.pdf §1.1`.

Closes #1 — project tracking issue

## How We tested it

- [x] `pytest` passes locally — **196 passed, 0 failed**
- [x] `pytest tests/test_ai_smoke.py` (provided smoke tests) passes
- [x] `pytest --cov=src --cov-report=term-missing` — **78% coverage** (864 statements, 187 missed)
- [x] `docker build .` succeeds from a clean clone
- [x] `docker run --env-file .env <image>` runs the offline demo end-to-end
- [x] `python scripts/bench.py` (offline, no API keys) confirms concurrent is faster than sequential

**Benchmark result (AMD Ryzen 7, Python 3.12.9, Windows 11):**

| Mode       | Time (s) | Speedup |
|------------|----------|---------|
| Sequential | 1.680    | 1.0×    |
| Parallel   | 0.653    | 2.57×   |

**Manual steps to reproduce offline:**
```bash
python -m researcher ask "What is photosynthesis?" --offline --limit 2
python scripts/demo.py --limit 5
python scripts/bench.py
```

## What this PR does NOT do

- Does not add ETag-based cache staleness checks for arXiv (TTL expiry only) — tracked as a known limitation in the report
- Does not add OpenTelemetry spans — identified as next-step in the slides
- Does not add token-per-minute rate limiting — TPM ceiling noted as a known limitation
- Does not support multi-worker deployment (SQLite is single-writer; PostgreSQL + asyncpg is the documented upgrade path)

## Checklist

- [x] No `.env`, secrets, or API keys in the diff — `.env.example` committed, real `.env` in `.gitignore`
- [x] No `TODO` / `FIXME` comments left in the changed code
- [x] Type hints on every new public function / method
- [x] No `except Exception: pass` or bare `except:`
- [x] No `print()` for runtime diagnostics — structured `logging` used throughout (`source_fetch_ok`, `source_fetch_failed`, `source_fetch_cache_hit`)
- [x] The provided `ai/` package's public interface is unchanged (`ai/__init__.py`, `ai/schemas.py`, `ai/sources.py`, `ai/synthesizer.py`, `ai/providers/`)
- [x] All timeouts and limits sourced from `src/config.py` `Settings` — no hardcoded magic numbers
- [x] Source failures returned as `SourceFailure` / degraded `OrchestrationResult` — pipeline never raises on a single bad provider
- [x] `*.db` and `*.sqlite3` in `.gitignore` — no database file in the diff
- [x] If using an AI assistant: every team member can explain every line of code in this repo

## AI assistant disclosure

Claude scaffolded the initial retry logic in `src/services/ai_service.py` and the SQL DDL in `src/storage/cache_store.py` and `src/storage/repository.py`. The team rewrote all backoff parameters, the retry-on-429 branch, and the timezone-aware datetime handling after catching a naive-datetime bug in `tests/test_cache_entry.py`. All business logic, test assertions, and architectural decisions were written and validated by the team.

## Contribution split

| Member | Primary ownership | Commits |
|---|---|---|
| Ibrahim Mammadov | `src/config.py`, `src/services/ai_service.py`, `src/services/cache.py`, CI workflows | ~34% |
| Fidan Allahverdiyeva | `src/concurrency/orchestrator.py`, `src/core/researcher.py`, `src/cli.py`, `src/api.py`, `src/web/`, `scripts/`, concurrency + E2E + API tests | ~33% |
| Fatma Mammadova | `src/storage/cache_store.py`, `src/storage/repository.py`, `Dockerfile`, `README.md`, test suite (196 tests, 78% coverage) | ~33% |

Full details in `CONTRIBUTION_STATEMENT.md`.

## Submission artefacts

- `REPORT.pdf` — written report
- `CONTRIBUTION_STATEMENT.md` — signed by all three members
- `artefacts/demo_offline.txt` — sample offline demo output
- Tag: `v1.0-final`
- Repository: https://github.com/IBRAHIM102005/AIENG_FinalProject_Topic_4


## Screenshots (if UI-affecting)

![UI](images/image1.png)