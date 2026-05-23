# Dockerfile for the AI Engineering Software Engineering Final
# Topic 4 — Async Research Assistant (Storage + CLI layer)


# ---- Stage 1: Builder -----------------------------------------------
# Compile wheels and install dependencies into an isolated venv.
# Only the venv is copied to the runtime stage, keeping the final
# image small (~280 MB instead of ~1 GB with build tools included).
FROM python:3.12.9-slim AS builder

# ---- Set sensible defaults for Python in a container ----------------
# PYTHONDONTWRITEBYTECODE: don't write .pyc files in the container.
# PYTHONUNBUFFERED:        stdout/stderr appear immediately (good for logs).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# ---- System dependencies --------------------------------------------
# gcc and libffi-dev are required to compile certain Python wheels
# (e.g. cryptography). Removed from the runtime stage for a lean image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# ---- Working directory ----------------------------------------------
WORKDIR /build

# ---- Install Python dependencies BEFORE copying the code ------------
# This lets Docker cache the dependency layer between builds when only
# your source code changes. Big speed-up for iterative development.
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt


# ---- Stage 2: Runtime -----------------------------------------------
# Lean final image — only the venv and application code, no build tools.
FROM python:3.12.9-slim AS runtime

# ---- Build-time labels (optional but professional) ------------------
LABEL org.opencontainers.image.title="Async Research Assistant"
LABEL org.opencontainers.image.description="AIENG Final Project — Topic 4 SE layer"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.authors="Team C"

# ---- Runtime environment --------------------------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    LOG_LEVEL=INFO \
    CACHE_DIR=/app/.cache \
    CACHE_TTL_SECONDS=86400 \
    PER_SOURCE_TIMEOUT_SECONDS=10 \
    MAX_SOURCES_PER_QUERY=3

# ---- Copy the compiled venv from the builder stage ------------------
COPY --from=builder /opt/venv /opt/venv

# ---- Non-root user (security best practice) -------------------------
# Containers should not run as root. Create an unprivileged user and
# switch to them before ENTRYPOINT.
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup \
               --create-home --shell /bin/bash \
               --no-log-init appuser

# ---- Working directory ----------------------------------------------
WORKDIR /app

# ---- Copy the rest of the project -----------------------------------
# .dockerignore excludes .git, .venv, __pycache__, *.pyc, .env,
# local databases, logs, and other files not needed at runtime.
COPY --chown=appuser:appgroup . .

RUN mkdir -p /app/.cache /app/logs \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

# ---- Healthcheck ----------------------------------------------------
# Validates the storage layer is importable and SQLite is functional.
HEALTHCHECK --interval=60s --timeout=10s --start-period=5s --retries=2 \
    CMD python -c \
        "from src.storage.cache_store import CacheStore; CacheStore(':memory:').open().close(); print('ok')" \
    || exit 1

# ---- Default command ------------------------------------------------
# For a CLI-driven project (Topic 4):
CMD ["python", "-m", "src.cli", "--help"]

