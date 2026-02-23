# ── NEXUS — multi-stage Dockerfile ────────────────────────────────────────────
#
# Stage 1: builder  — install Python deps into a virtual env
# Stage 2: runtime  — copy only the venv + app code; no build tools in prod image
#
# Azure Speech SDK requires libssl and some audio libs.
# The image is intentionally based on Debian (not Alpine) for SDK compatibility.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# System deps needed to compile psycopg2/cryptography wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libssl-dev \
      libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create isolated venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml .
# pip install from pyproject.toml (no lock file required for initial build)
RUN pip install --upgrade pip && pip install .


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

# Azure Speech SDK runtime deps (libssl, libasound for audio subsystem)
RUN apt-get update && apt-get install -y --no-install-recommends \
      libssl3 \
      libasound2 \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Non-root user for container security
RUN useradd -m -u 1001 nexus
WORKDIR /app
USER nexus

# Copy application code
COPY --chown=nexus:nexus nexus/     ./nexus/
COPY --chown=nexus:nexus frontend/  ./frontend/
COPY --chown=nexus:nexus alembic/   ./alembic/
COPY --chown=nexus:nexus alembic.ini .

# Expose
EXPOSE 8000

# Uvicorn — single worker in container; scale out via replicas
# Use --proxy-headers when behind Azure Front Door / App Gateway
CMD ["uvicorn", "nexus.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--workers", "1"]
