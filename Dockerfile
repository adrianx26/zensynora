# =============================================================================
# ZenSynora — Multi-stage Dockerfile
# =============================================================================
# Build:  docker build -t zensynora:latest .
# Run:    docker run -it --rm -p 8000:8000 -v zensynora-data:/data zensynora:latest
#
# For full orchestration with compose, see docker-compose.yml
# =============================================================================

# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
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
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user for security
RUN groupadd -r zensynora && useradd -r -g zensynora -m -d /home/zensynora zensynora

# Set up data directory (mount this as a volume for persistence)
RUN mkdir -p /data/.myclaw && chown -R zensynora:zensynora /data
ENV HOME=/data
ENV MYCLAW_CONFIG_DIR=/data/.myclaw

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=zensynora:zensynora myclaw/ ./myclaw/
COPY --chown=zensynora:zensynora cli.py onboard.py deploy.py ./
COPY --chown=zensynora:zensynora webui/dist/ ./webui/dist/
COPY --chown=zensynora:zensynora requirements.txt ./
COPY --chown=zensynora:zensynora install.sh uninstall.sh cleanup.sh ./
COPY --chown=zensynora:zensynora CHANGELOG.md LICENSE README.md ./

# Install the package in editable mode (for CLI entry points)
RUN pip install --no-cache-dir -e .

# Switch to non-root user
USER zensynora

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose ports
# 8000 — WebUI / FastAPI
# 8080 — WhatsApp webhook (optional)
EXPOSE 8000 8080

# Default command: show help
CMD ["zensynora", "--help"]
