# ── NEXUS MVP — multi-stage Dockerfile ───────────────────────────────────────
#
# Stage 1: builder  — install Python deps into a virtual env
# Stage 2: runtime  — copy only the venv + app; no build tools in prod image
#
# Azure Speech SDK requires libssl and libasound — use Debian, not Alpine.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libssl-dev \
      libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

# Azure Speech SDK runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      libssl3 \
      libasound2 \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd -m -u 1001 nexus
WORKDIR /app
USER nexus

COPY --chown=nexus:nexus server.py .
COPY --chown=nexus:nexus static/   ./static/

EXPOSE 8000

CMD ["uvicorn", "server:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
