# artefacts/

This directory holds demo outputs, benchmark results, coverage screenshots,
and Docker logs produced during development and submission.

All files in this directory are **git-ignored** (see `.gitignore`) except this
`README.md`.  Generate them locally before submission using the commands below.

---

## Catalogue

| File | Description | How to generate |
|---|---|---|
| `demo_offline.log` | Full output of `python demo_ai.py --offline` — proves the AI module and offline stubs work | `python demo_ai.py --offline 2>&1 \| tee artefacts/demo_offline.log` |
| `demo_live.log` | Live run with real API keys (requires `.env`) | `python demo_ai.py 2>&1 \| tee artefacts/demo_live.log` |
| `pytest_output.txt` | Full pytest output for all tests | `pytest tests/ -v 2>&1 \| tee artefacts/pytest_output.txt` |
| `pytest_core_output.txt` | Storage-layer tests only | `pytest tests/test_core.py -v 2>&1 \| tee artefacts/pytest_core_output.txt` |
| `coverage_report.txt` | Terminal coverage for `src/storage/` | `pytest tests/test_core.py --cov=src/storage --cov-report=term-missing 2>&1 \| tee artefacts/coverage_report.txt` |
| `coverage_html/` | Full HTML coverage report (browser-viewable) | `pytest tests/ --cov=src --cov-report=html:artefacts/coverage_html` |
| `bench_parallel_vs_sequential.json` | Timing comparison from `scripts/bench.py` | `python scripts/bench.py 2>&1 \| tee artefacts/bench_parallel_vs_sequential.json` |
| `docker_build.log` | Multi-stage Docker build output | `docker build --progress=plain -t researcher:latest . 2>&1 \| tee artefacts/docker_build.log` |
| `docker_run_help.log` | Default CLI help from inside the container | `docker run --rm researcher:latest 2>&1 \| tee artefacts/docker_run_help.log` |
| `docker_run_tests.log` | Test suite run inside the container | `docker run --rm researcher:latest pytest tests/ -v 2>&1 \| tee artefacts/docker_run_tests.log` |
| `docker_images.txt` | Image size verification (`~280 MB`) | `docker images researcher 2>&1 \| tee artefacts/docker_images.txt` |
| `cli_ask_demo.log` | CLI `ask` command output (requires keys unless `--offline` is used) | `python -m researcher ask "What is CRISPR?" --offline 2>&1 \| tee artefacts/cli_ask_demo.log` |

---

## Screenshot guide

For the submission report, include screenshots of:

1. **pytest coverage** — Run:
   ```bash
   pytest tests/test_core.py -v --cov=src/storage --cov-report=term-missing
   ```
   Screenshot the terminal showing ≥ 95% coverage for `src/storage/`.

2. **Docker build** — Run:
   ```bash
   docker build -t researcher:latest .
   ```
   Screenshot the terminal showing `Stage 1 builder` and `Stage 2 runtime`
   layers completing successfully.

3. **Docker test run** — Run:
   ```bash
   docker run --rm researcher:latest pytest tests/ -v
   ```
   Screenshot showing all tests passing inside the container.

4. **CLI demo** — Run:
   ```bash
   python -m researcher ask "What is quantum computing?" --sources wiki,arxiv
   ```
   Screenshot showing the formatted answer with numbered citations.

5. **Benchmark** — Run:
   ```bash
   python scripts/bench.py
   ```
   Screenshot showing the parallel vs. sequential timing table (Üzv B output).

---

## One-shot artefact generation

```bash
# Generate all artefacts in one go (no API keys required for most):
python demo_ai.py --offline 2>&1 | tee artefacts/demo_offline.log
pytest tests/ -v \
    --cov=src \
    --cov-report=term-missing \
    --cov-report=html:artefacts/coverage_html \
    2>&1 | tee artefacts/pytest_output.txt
docker build --progress=plain -t researcher:latest . 2>&1 | tee artefacts/docker_build.log
docker run --rm researcher:latest pytest tests/ -v 2>&1 | tee artefacts/docker_run_tests.log
docker images researcher 2>&1 | tee artefacts/docker_images.txt
```
