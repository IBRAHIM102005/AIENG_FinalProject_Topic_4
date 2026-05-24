# Contribution Statement

**Team:** zerobias
**Topic:** Topic 4 – Async Research Assistant
**Repository:** https://github.com/IBRAHIM102005/AIENG_FinalProject_Topic_4
**Final tag:** `v1.0-final`
**Submission date:** 2026-05-23

---

## Member A — Ibrahim Mammadov 

**Owned (sole author):**
- `src/config.py`
- `src/services/ai_service.py` — retry logic, failover, provider abstraction
- `src/services/cache.py`
- CI workflow files (`.github/workflows/`)

**Co-owned (paired or substantially edited):**
- `src/api.py` (with Fidan Allahverdiyeva) — FastAPI route definitions and error handling
- `tests/test_services.py` (with Fatma Mammadova) — service-layer unit tests

**Reviewed (PRs reviewed and merged):**
- Concurrency pipeline PRs (orchestrator, gather degradation)
- Storage and Dockerfile PRs

**Approximate share of commits:** ~34%

---

## Member B — Fidan Allahverdiyeva

**Owned (sole author):**
- `src/concurrency/orchestrator.py` — asyncio.gather pipeline, semaphore, degraded-source handling
- `src/core/researcher.py` — core research logic
- `src/cli.py` — CLI entry point and argument parsing
- `src/api.py` — FastAPI application
- `src/web/` — browser UI
- `scripts/` — bench.py, demo.py, and supporting scripts
- `tests/test_concurrency.py`
- `tests/test_end_to_end.py`
- `tests/test_api.py`

**Co-owned (paired or substantially edited):**
- `src/api.py` — shared route-level error middleware with Ibrahim

**Reviewed (PRs reviewed and merged):**
- Storage layer PRs (cache_store, repository)
- Config and CI PRs

**Approximate share of commits:** ~33%

---

## Member C — Fatma Mammadova

**Owned (sole author):**
- `src/storage/cache_store.py`
- `src/storage/repository.py`
- `Dockerfile`
- `README.md`
- Full test suite structure and coordination (184 tests total, 78% coverage)
- `tests/test_cache_entry.py` — caught naive-datetime timezone bug

**Co-owned (paired or substantially edited):**
- `tests/test_services.py` (with Ibrahim) — service-layer assertions

**Reviewed (PRs reviewed and merged):**
- CLI and API PRs
- Concurrency pipeline PRs

**Approximate share of commits:** ~33%

---

## AI Tool Disclosure

| Module / file | Assistant | What we did with it |
|---|---|---|
| `src/services/ai_service.py` (retry logic) | Claude | Claude scaffolded the initial retry logic; team rewrote all backoff parameters, jitter, and retry-on-429 branch after observing rate-limit behaviour in dev. |
| `src/storage/` (cache_store + repository DDL) | Claude | Claude provided initial SQL DDL for storage schema; team reviewed, adapted queries, added timezone-aware datetime handling after catching a naive-datetime bug in tests. |

We affirm that we **can defend every line of code** in this repository during the oral defense. "The AI wrote it" is not an answer we will use.

---

## Signatures

By signing below, we affirm that:
- The contributions described above are accurate.
- The commit percentages reflect actual work, not artificially split commits.
- Every line of code in the repository can be defended by at least one team member.
- AI assistant usage has been disclosed as described above.

| Member | Signature | Date |
|---|---|---|
| Ibrahim Mammadov | __________________________ | __________ |
| Fidan Allahverdiyeva | __________________________ | __________ |
| Fatma Mammadova | __________________________ | __________ |