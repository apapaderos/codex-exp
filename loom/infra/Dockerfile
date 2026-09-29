# Loom: one image, two roles (app and worker). Build context is the loom/ folder:
#   docker build -f infra/Dockerfile -t loom .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LOOM_ROOT=/srv/loom HOME=/home/loom

RUN useradd --create-home --home-dir /home/loom --uid 10001 loom \
 && apt-get update && apt-get install -y --no-install-recommends ca-certificates git ripgrep \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/loom
COPY pyproject.toml ./
COPY engine ./engine
RUN pip install --no-cache-dir .

# Agent definitions, skills, schemas, rules and the app. Engagement data is NOT baked in:
# it lives on the durable volume mounted at engagements/, archive/ and .loom-jobs/.
COPY CLAUDE.md ./
COPY .claude ./.claude
COPY schemas ./schemas
COPY docs ./docs
COPY app ./app
COPY engagements/2026-10-demo-onboarding ./engagements/2026-10-demo-onboarding
RUN mkdir -p archive .loom-jobs && chown -R loom:loom /srv/loom /home/loom

USER loom
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
