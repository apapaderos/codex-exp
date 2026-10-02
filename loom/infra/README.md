# Deploying Loom

Loom is one container image with two roles, plus a durable volume. It runs anywhere that
can run containers. The provider is not chosen yet, so nothing here is provider-specific.

## The five needs, and what fills them

| Need (spec) | In docker-compose | Azure | AWS | GCP |
| --- | --- | --- | --- | --- |
| App hosting | `app` service (uvicorn, port 8080) | Container Apps | App Runner or ECS Fargate + ALB | Cloud Run |
| Durable storage | named volumes `engagements`, `archive`, `jobs` | Azure Files share | EFS | Filestore |
| Job runner | `worker` service (`loom worker`, file queue on the volume) | Container Apps (min 1 replica) | ECS service | Cloud Run (always-on CPU) or GCE |
| Secrets store | `infra/.env` | Key Vault → env | Secrets Manager → env | Secret Manager → env |
| Notifications | `LOOM_NOTIFY_WEBHOOK` (JSON `{"text": ...}`) | Teams incoming webhook / Logic App | SNS / Slack webhook | Pub/Sub / Chat webhook |

Run exactly **one worker**. Engagement locks use `flock`, which is reliable on a local disk
and on NFS-based shares (EFS, Filestore). On SMB (Azure Files), run the app as a single
replica with `LOOM_INLINE_WORKER=1`, or swap the queue in `engine/loom/jobs.py` for a
managed one (only `enqueue` and `claim` change).

## Run it

```bash
cd loom
cp infra/.env.example infra/.env          # set ANTHROPIC_API_KEY and LOOM_APP_PASSWORD
docker compose -f infra/docker-compose.yml up -d --build
open http://localhost:8080                # user: LOOM_APP_USER, password: LOOM_APP_PASSWORD
```

Smoke test without an API key: set `LOOM_RUNNER=stub`; the demo engagement runs through
every gate with placeholder artifacts.

## Before a cloud deployment (the approval gate)

Loom's own rule applies to its infrastructure: anything that creates a paid resource,
opens network access, or touches credentials waits for your approval. Nothing in this folder
provisions cloud resources on its own. The steps, in order:

1. **Data rules.** Confirm PwC's rules allow client engagement material in this cloud region
   and through the Anthropic API. This comes before anything else.
2. **Pick the provider** from the table above. Claude Code then writes the IaC (Terraform or
   Bicep) for that column into `infra/<provider>/`, and you review the plan before `apply`.
3. **Network.** Publish only the app, behind the provider's HTTPS ingress and ideally your
   corporate SSO (Entra ID Easy Auth, ALB OIDC, IAP) in front of the basic auth. The worker
   needs outbound HTTPS to `api.anthropic.com` and, for research-secondary, the open web.
4. **Secrets.** `ANTHROPIC_API_KEY`, `LOOM_APP_PASSWORD`, `LOOM_NOTIFY_WEBHOOK` go into the
   secret store, injected as environment variables.
5. **Reused skills.** Mount a read-only volume with `evidence`, `reframe`, `solve-for-x` and
   `senior-experience-architect`, and set `LOOM_EXTRA_SKILL_DIRS` to it.
6. **Backups.** Snapshot the storage volume daily; it is the only state Loom has.

## Operating

- Health: `GET /healthz`.
- Failed jobs keep their traceback in `.loom-jobs/failed/`. Re-queue by moving the file to
  `pending/`, or press *Resume* on the engagement page.
- Cost per agent run is logged on each `agent_done` event in the engagement's `log.jsonl`.
- `LOOM_MAX_TURNS` and `LOOM_MAX_BUDGET_USD` cap each agent run.
