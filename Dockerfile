# =============================================================================
# ZenSynora — Production-Ready Multi-stage Dockerfile
# =============================================================================
# Build:
#   docker build -t zensynora:latest .
#
# Run (standalone):
#   docker run -it --rm -p 8000:8000 -v zensynora-data:/data zensynora:latest
#
# Run (with compose):
#   docker compose up --build
#
# For orchestration details, see docker-compose.yml
# =============================================================================

# ── Stage 1: Python Dependency Builder ───────────────────────────────────────
FROM python:3.12-slim AS python-builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Frontend Builder ────────────────────────────────────────────────
# Vite 8 needs Node >= 20.19; Node 20 is end-of-life, so build on 22 LTS.
FROM node:22-slim AS frontend-builder

WORKDIR /frontend

# Install dependencies
COPY webui/package*.json ./
RUN npm ci

# Build the frontend
COPY webui/ ./
RUN npm run build

# ── Stage 3: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="Adrian Petrescu <adrianx26@protonmail.com>"
LABEL description="ZenSynora (MyClaw) — Personal AI Agent"
LABEL org.opencontainers.image.source="https://github.com/adrianx26/zensynora"
LABEL org.opencontainers.image.licenses="AGPL-3.0"

# Runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=python-builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user for security.
# Changed 2026-10-13 (v0.7.0): the user now has a fixed UID (default 1000, the
# usual first Linux user). With a random system UID the container could not
# write to the ./data folder bind-mounted by docker-compose.yml.
# Build with --build-arg ZENSYNORA_UID=$(id -u) to match your own user.
ARG ZENSYNORA_UID=1000
ARG ZENSYNORA_GID=1000
RUN groupadd -g "${ZENSYNORA_GID}" zensynora \
    && useradd -u "${ZENSYNORA_UID}" -g zensynora -m -d /home/zensynora zensynora

# Set up data directory (mount this as a volume for persistence).
# ZenSynora keeps everything in ~/.myclaw, so HOME=/data puts it in /data/.myclaw.
RUN mkdir -p /data/.myclaw/logs && chown -R zensynora:zensynora /data
ENV HOME=/data

# Set working directory
WORKDIR /app

# Copy package metadata FIRST for proper editable install
COPY --chown=zensynora:zensynora pyproject.toml ./
COPY --chown=zensynora:zensynora requirements.txt ./
# pyproject.toml declares readme = "README.md"; hatchling refuses to build
# the package metadata without it (this broke `pip install -e .` below).
COPY --chown=zensynora:zensynora README.md LICENSE ./

# Copy application code
COPY --chown=zensynora:zensynora myclaw/ ./myclaw/
# updater/ holds the data migrations (doctor checks them; stdlib only).
COPY --chown=zensynora:zensynora updater/ ./updater/
COPY --chown=zensynora:zensynora cli.py onboard.py ./

# Copy built frontend from frontend-builder stage
COPY --from=frontend-builder --chown=zensynora:zensynora /frontend/dist ./webui/dist

# Install the package in editable mode (provides CLI entry points)
RUN pip install --no-cache-dir -e .

# Switch to non-root user
USER zensynora

# Health check with progressive backoff
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:8000/health >/dev/null || exit 1

# Expose ports
# 8000 — WebUI / FastAPI
# 8080 — WhatsApp webhook (optional)
EXPOSE 8000 8080

# Default command: the web UI (the HEALTHCHECK above expects it).
# Changed 2026-10-13 (v0.7.0): was `--help`, so `docker run` exited at once.
CMD ["zensynora", "webui", "--host", "0.0.0.0", "--port", "8000"]

# ── Stage 4: Development (optional target) ───────────────────────────────────
FROM runtime AS development

USER root

# Install development tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim \
    nano \
    htop \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install dev dependencies
# (quoted: unquoted `pytest>=7.0` is a shell redirect, not a version spec)
RUN pip install --no-cache-dir -e ".[test]" \
    "ruff>=0.4.0,<1" \
    "mypy>=1.9,<2"

USER zensynora

# Default dev command
CMD ["bash"]
