# Loom: engineering handover

**From:** Andreas Papaderos (product owner) · **Date:** October 2026
**Branch:** `claude/sweet-ptolemy-4mdvhi` in `apapaderos/codex-exp`, folder `loom/`
**Goal of the next phase:** take Loom from a working single-user prototype to a
**production-ready team app** hosted in PwC's cloud: real sign-in, several people working on
several engagements, data handled under PwC's rules, monitored and backed up.

Read this first, then `docs/loom-spec.md` (the product spec) and `docs/architecture.md`
(how it was built and every decision taken along the way).

---

## 1. What Loom is, in one paragraph

Loom runs a PwC Athens Experience Center engagement end to end in six steps (Understand,
Research, Frame, Workshop, Hand over, Track), with a person deciding at four points. Claude
agents do the reading, research and writing; people make every call. Each engagement is a
folder of markdown files with structured headers, so every claim in a final spec can be walked
back to the transcript line or survey answer it came from. The app looks and behaves like
Claude: navigation on the left, one long conversation per engagement in the centre, and the
engagement's progress, files, people and tracking on the right.

## 2. Where it stands

| Area | State |
| --- | --- |
| Engine (state machine, validation, job queue) | **Working.** Deterministic orchestrator, 16 JSON Schemas, traceability checks, one automatic retry per agent, undoable decisions. |
| Agents | **Working live.** 15 Claude Code subagents (14 from the spec + a read-only `ask` agent) and 12 skills. |
| Web app | **Working, single user.** Three-column conversation UI; editing of drafts and inputs; live activity; sample data. |
| Local run | **Working.** `./start.sh` on macOS with a Claude login or API key. |
| Container image | **Written, never built** (no Docker daemon was available). `infra/Dockerfile` + `docker-compose.yml`. |
| Cloud deployment | **Not started.** Provider not chosen; infra is provider-neutral. |
| Sign-in, multi-user, permissions | **Not started.** One shared basic-auth password. |
| CI | **None.** Tests run locally. |

### Verified end to end

- **Live, through the browser, from a fresh checkout started with `./start.sh`:** one full
  engagement with real Claude agents from New engagement to Close and archive, using the sample
  files. All artifacts validated; about 30 minutes of agent time; **$5.47** with the default
  models (opus for framing, workshop design and spec writing).
- **Demo mode** (`LOOM_RUNNER=stub`, no AI calls): the same walk passes in about 70 seconds with
  `tools/e2e_walk.py`.
- **Unit and integration tests:** `pytest` → 12 passed (pipeline, gates, reject/revise paths,
  blocked steps, job queue, app conversation flow, undo, editing rules, SDK runner write guard).
- **On Andreas's MacBook Air:** `./start.sh` → sign-in → app loads.

### Not verified

- Docker image build and the compose setup.
- Any cloud deployment, TLS, SSO.
- Teams/Slack notifications (code exists; never pointed at a real webhook).
- Web page fetching during research (blocked in the build sandbox; search worked). Should work
  on a normal network; check it.
- More than one person using it at once.

## 3. Run it (15 minutes)

```bash
git clone https://github.com/apapaderos/codex-exp.git
cd codex-exp && git checkout claude/sweet-ptolemy-4mdvhi && cd loom
./start.sh                       # live: needs a Claude login (it offers one) or ANTHROPIC_API_KEY
LOOM_RUNNER=stub ./start.sh      # demo: no AI calls, instant
```

Then *New engagement → Try it with the sample onboarding files*, and follow the bar above the
message box. Tests: `.venv/bin/pytest`. Browser walk: see `tools/e2e_walk.py`.
Command line without a browser: `.venv/bin/loom --help` (`status`, `run`, `decide`, `add`,
`validate`, `inbox`, `doctor`, `login`, `sample`, `worker`).

## 4. How it works

```
browser ──HTTP──▶ app (FastAPI, server-rendered + small JS, polls /live every 2.5 s)
                    │ writes inputs, enqueues jobs, never runs agents in a request
                    ▼
                 job queue (files in .loom-jobs/: pending → running → done/failed; delayed jobs = undo window)
                    │
                    ▼
                 worker (thread pool) ──▶ orchestrator.advance/decide/inputs_arrived
                                              │ one state machine per engagement, file lock
                                              ▼
                                          runner ──▶ Claude Agent SDK (one subprocess per agent run)
                                              │       tools + model + skills from .claude/agents/*.md
                                              │       PreToolUse hook denies writes outside the task's files
                                              ▼
                                          engagements/<id>/   ← single source of truth
                                            state.json · log.jsonl · run.log · 00-inputs/ · 01-intake/ … 06-tracking/
                                          archive/<id>.md    ← read first by the next engagement's research
```

| Path | What it is |
| --- | --- |
| `engine/loom/orchestrator.py` | The state machine: stages, gates, decisions, dispatch, validation, retries. **Start here.** |
| `engine/loom/runners.py` | `SdkRunner` (real agents via `claude_agent_sdk.query`) and the write guard. |
| `engine/loom/stubs.py` | `StubRunner`: schema-valid placeholders for demo mode and tests. |
| `engine/loom/state.py`, `artifacts.py`, `refs.py` | state.json/log.jsonl I/O, frontmatter parsing, schema and reference checks. |
| `engine/loom/jobs.py` | File-based queue and worker. |
| `engine/loom/ask.py` | Replies to what people type in the conversation (read-only agent). |
| `engine/loom/cli.py` | `loom` command, including `doctor` and `login`. |
| `app/main.py` | HTTP routes. `app/narrate.py` turns the event log into the conversation (plain words only). |
| `app/templates/`, `app/static/` | Jinja templates, one CSS file, one JS file. No build step. |
| `.claude/agents/`, `.claude/skills/` | Agent definitions and methods. Same files serve Claude Code and the app. |
| `schemas/` | JSON Schema per artifact, plus state, log line, handoff, routing. |
| `infra/` | Dockerfile, compose (app + worker + volumes), env template, provider mapping. |
| `CLAUDE.md` | Project rules. If you use Claude Code on this repo, it reads these. |

**Invariants to keep** (tests cover most; don't break them):

1. Only the orchestrator writes `state.json` and `log.jsonl`. Agents write only the files their
   task names; the SDK hook enforces it.
2. No step passes a decision point without a recorded human decision.
3. Every artifact's frontmatter validates against its schema before a step can finish; ids and
   `evidence_refs` must resolve.
4. The interface never shows the words gate, schema, agent or stage. `app/narrate.py` is the
   only translation layer; a test scans the rendered page.
5. Loom never contacts anyone. People send the questions and messages.

## 5. Decisions your work depends on (owner: Andreas, unless noted)

1. **PwC data rules: blocking.** May client engagement material go to the Anthropic API, and be
   stored in the chosen cloud region? This decides whether to call Claude directly, through
   Azure AI Foundry or AWS Bedrock (both host Claude), and where data may live.
2. **Cloud provider.** Azure is the natural fit for PwC (Entra ID, Teams, M365). The mapping for
   each provider is in `infra/README.md`.
3. **Who may see what.** Everyone on the EC team sees every engagement, or per-engagement
   membership? Do clients ever log in? (Assumed: EC team only, per-engagement membership.)
4. **Shared skills.** `evidence`, `reframe`, `solve-for-x` and `senior-experience-architect`
   live in Andreas's claude.ai account, not in the repo. Production needs them in the repo
   (recommended) or mounted from a managed location.
5. **Open product checks** in `docs/architecture.md`: editing Loom's drafts (beyond the
   spec's "brief only"), the 20-second undo window, an explicit Close step, and the 15th agent.

## 6. Backlog to production-ready

Priorities: **P0** = must have before any real client material; **P1** = before the team uses it
daily; **P2** = soon after. Sizes are rough (S ≤ 2 days, M ≤ 1 week, L ≤ 3 weeks).

### P0: safe to use with real data

| # | Item | Done when | Size |
| --- | --- | --- | --- |
| 1 | **Model access route per PwC rules** (decision 5.1). Add a provider setting: Anthropic API, Azure AI Foundry or Bedrock. The Agent SDK supports these through environment settings; confirm with Anthropic's docs. | A live run completes through the approved route; nothing goes to an unapproved endpoint (verified in egress logs). | M |
| 2 | **Sign-in with Entra ID (SSO)**, replacing basic auth. Use the platform's built-in auth (e.g. Azure Container Apps Easy Auth) or OIDC in the app. Record the real user on every decision and edit (`by` is already plumbed through). | Only PwC accounts in an allowed group get in; every `gate_decision`/edit in `log.jsonl` shows the person's email. | M |
| 3 | **Per-engagement access.** Members list on each engagement; nav, pages, files and jobs filtered by membership; an admin role. | A test user who isn't a member gets 404 on the engagement, its files and its live updates. | M |
| 4 | **Request security.** CSRF protection on all POST forms; security headers (CSP, frame-ancestors); upload size and type limits; keep the existing path-traversal checks and markdown escaping. | A security review (or the `/security-review` command) finds nothing high; uploads over the limit are refused. | S |
| 5 | **Secrets.** API keys and webhook in the cloud secret store (Key Vault), injected as environment variables; nothing in images or repo. | Image scan and repo scan show no secrets. | S |
| 6 | **Data retention and deletion.** Rules for how long engagements and archives live; a delete/export function for an engagement. | Deleting an engagement removes its folder, archive entry and jobs; documented retention applied. | S |

### P1: reliable for daily team use

| # | Item | Done when | Size |
| --- | --- | --- | --- |
| 7 | **Build and deploy the container** to the chosen cloud with IaC (Bicep or Terraform in `infra/<provider>/`). App behind HTTPS; one worker. Deployments from CI with an approval step. | `git push` to main → tested image → staging; a manual approval promotes to production. | M |
| 8 | **Durable storage.** Engagement folders on a managed share (Azure Files or equivalent) with daily snapshots. Note: file locks use `flock`, which is unreliable on SMB shares: either use NFS (Azure Files NFS, EFS, Filestore) or move the lock and the job queue to the database (item 9). | Kill the container mid-step → restart → the engagement resumes from `state.json` without loss. Restore from a snapshot tested once. | M |
| 9 | **Managed queue and state index** (recommended for multi-user). Keep engagement files as the source of truth, but move the job queue to a managed queue (Service Bus/SQS) or Postgres, and keep a small database index of engagements, members and status for the nav and inbox (today it scans folders). Interfaces to change: `jobs.enqueue/claim`, `state.list_ids/load`. | 50 engagements load the nav in < 300 ms; two workers never run the same engagement at once. | M |
| 10 | **CI.** Run `pytest` and `tools/e2e_walk.py` in demo mode on every pull request; lint (ruff) and type check. | Red builds block merging. | S |
| 11 | **Observability.** Structured logs; per-agent duration, cost and failure rate (cost is already in `log.jsonl` as `cost_usd`); alerts on failed jobs and stuck steps; a cost dashboard per engagement and per month. | An agent failure pages someone; monthly cost is visible without reading logs. | M |
| 12 | **Guardrails on agent runs.** Wall-clock timeout per agent (today only `LOOM_MAX_TURNS`), per-run budget (`LOOM_MAX_BUDGET_USD` exists, set it), and a monthly cap per team. | A runaway agent stops at the limit and the step shows a clear "try again" card. | S |
| 13 | **Notifications** to Teams (decision waiting, wait ended) using the existing webhook hook in `engine/loom/notify.py`; link straight to the decision card. | Andreas gets a Teams message within a minute of a decision opening, and the link lands on the card. | S |
| 14 | **Live updates without polling** (optional). Server-sent events instead of 2.5 s polling, for many users. | The page updates within 1 s of an event; server load flat with 20 open tabs. | S |

### P2: product quality

| # | Item | Done when | Size |
| --- | --- | --- | --- |
| 15 | Reading PDFs, Word, PowerPoint and images as inputs: confirm the agents handle each well; add text extraction for formats Claude's Read tool doesn't cover (e.g. .docx, .pptx). | Each format in a test engagement yields cited claims. | M |
| 16 | Tune the framing (triage) skill against 3 to 5 past engagements, as the spec's phase 3 requires. | The split matches how Andreas would have split them in each case. | M |
| 17 | Backfill the archive with past engagements (spec phase 5); decide on search (keyword now; embeddings later if needed). | A new engagement's first research round cites a relevant past engagement. | M |
| 18 | Accessibility pass (keyboard, screen reader, contrast) and phone layout testing. | WCAG 2.1 AA on the main flows. | S |
| 19 | Export: a clean PDF/Word of the brief, problems, session design and specs. | One-click export per step. | S |

**Suggested order:** decisions 5.1 to 5.3 → items 1, 2, 5, 10 → 7, 8 → 3, 4, 6 → 9, 11, 12, 13 →
P2. A first production release for the EC team is items 1 to 8 plus 10 to 13.

## 7. Known limitations and gotchas

- **One process, threads:** the inline worker (`LOOM_INLINE_WORKER=1`) runs in the web process.
  For production, run `loom worker` as its own service (compose already does).
- **Agent runs are subprocesses** of the bundled Claude Code CLI (inside the `claude-agent-sdk`
  wheel, ~230 MB). The image needs that wheel for its platform and a writable `HOME`.
- **Engagement ids** are made from the title and month; renaming isn't supported.
- **`run.log`** grows per engagement; rotate it or move activity lines to the database.
- **Frontmatter formatting:** agents occasionally wrote unquoted colons and needed a retry; every
  task now tells them to quote values. If retries show up in logs (`"event": "invalid"`), look
  there first.
- **Cost drivers:** framing, workshop design and the four spec writers use opus. Each can be
  switched with `LOOM_MODEL_<AGENT>=sonnet` to trade quality for cost.
- **The repo root** holds an unrelated older project (NEXUS: `server.py`, `static/`, etc.).
  Loom is self-contained in `loom/`; consider moving it to its own repository.

## 8. Contacts

- Product owner and spec: **Andreas Papaderos**, Director, PwC Athens Experience Center.
- The spec and as-built notes: `docs/loom-spec.md`, `docs/architecture.md`.
