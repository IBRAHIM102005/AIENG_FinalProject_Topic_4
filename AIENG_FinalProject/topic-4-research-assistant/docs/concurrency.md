# Concurrency, CLI, and Benchmark Notes

This section documents the work owned by Member B: async orchestration, the
research workflow, the command line interface, and benchmark/demo scripts.

## Async Orchestration

`src/concurrency/orchestrator.py` fetches selected sources concurrently with
`asyncio.gather`. Each source call is wrapped with:

- a semaphore limit, so the number of active source fetches is bounded;
- a per-source timeout, so one slow provider does not block the whole answer;
- graceful degradation, so failed providers are returned as `SourceFailure`
  values while successful providers still contribute sources.

Duplicate URLs are removed after all fetches complete. Source aliases such as
`wiki`, `wikipedia`, `arxiv`, and `web` are normalized before dispatch.

## Research Workflow

`src/core/researcher.py` validates the question, runs the orchestrator, and
passes retrieved sources into the synthesizer. If every provider fails, the
workflow raises a clear error that includes source failure details.

## CLI

The CLI entry point is `src/cli.py`.

Run an offline deterministic query:

```powershell
python -m src.cli ask "What is photosynthesis?" --offline --limit 2
```

Useful options:

- `--sources wiki,arxiv,web` chooses source providers;
- `--limit 2` limits results per source;
- `--timeout 5` controls per-source timeout seconds;
- `--concurrency 3` controls the semaphore limit;
- `--offline` uses fake providers and fake LLM output for repeatable demos.

## Benchmark

`scripts/bench.py` compares sequential fetching against parallel fetching.
The default mode is offline and deterministic, so it is suitable for tests and
presentations.

```powershell
python scripts\bench.py
```

Example result:

```text
Mode        Time (s)
Sequential  1.684
Parallel    0.660
Speedup     2.55x
```

## Demo

`scripts/demo.py` runs the research workflow for sample questions from
`data/research_questions.json`.

```powershell
python scripts\demo.py --limit 5
```

## Verification

Member B tests can be run with:

```powershell
pytest tests\test_concurrency.py tests\test_end_to_end.py -q
```