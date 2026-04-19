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
FROM node:20-slim AS frontend-builder

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

# Create non-root user for security
RUN groupadd -r zensynora && useradd -r -g zensynora -m -d /home/zensynora zensynora

# Set up data directory (mount this as a volume for persistence)
RUN mkdir -p /data/.myclaw && chown -R zensynora:zensynora /data
ENV HOME=/data
ENV MYCLAW_CONFIG_DIR=/data/.myclaw

# Set working directory
WORKDIR /app

# Copy package metadata FIRST for proper editable install
COPY --chown=zensynora:zensynora pyproject.toml ./
COPY --chown=zensynora:zensynora requirements.txt ./

# Copy application code
COPY --chown=zensynora:zensynora myclaw/ ./myclaw/
COPY --chown=zensynora:zensynora cli.py onboard.py deploy.py ./

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

# Default command: show help
CMD ["zensynora", "--help"]

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
RUN pip install --no-cache-dir \
    pytest>=7.0 \
    pytest-asyncio>=0.21.0 \
    pytest-cov>=4.0 \
    ruff>=0.4.0 \
    black>=24.0 \
    isort>=5.13 \
    mypy>=1.9

USER zensynora

# Default dev command
CMD ["bash"]
