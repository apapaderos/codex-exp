# Loom

A multi-agent system that runs a PwC Athens Experience Center engagement end to end, from raw
intake through workshop to tracked adoption, with four human gates.

- Spec: [`docs/loom-spec.md`](docs/loom-spec.md)
- As built, with every decision that needs your check: [`docs/architecture.md`](docs/architecture.md)
- Project rules for Claude Code: [`CLAUDE.md`](CLAUDE.md)
- Deploying: [`infra/README.md`](infra/README.md)

## What is here

| | |
| --- | --- |
| 15 agents | `.claude/agents/`: the 14 from the spec (orchestrator, intake-voice, intake-docs, research-secondary, research-primary, framing, workshop-designer, workshop-capture, delegation, spec-decision, spec-process, spec-tool, tracking, archivist) plus `ask`, which answers what you type in the conversation (read-only) |
| 12 skills | `.claude/skills/loom-*`. The 4 reused skills (evidence, reframe, solve-for-x, senior-experience-architect) are referenced from your own setup |
| 16 schemas | `schemas/`: one per artifact, plus state, log line, handoff, routing |
| Engine | `engine/loom/`: orchestrator state machine, validators, SDK and stub runners, job queue, `loom` CLI |
| App | `app/`: three columns like Claude: navigation, the engagement conversation with decision cards and a next-step bar, and the engagement panel (progress, files, people, tracking) |
| Infra | `infra/`: container image, compose file (app, worker, volumes), env template |
| Demo | `engagements/2026-10-demo-onboarding/`: a transcript and a process doc, ready to run |

## Quick start (no API key needed)

```bash
cd loom
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                                          # the whole pipeline, stub agents

export LOOM_RUNNER=stub                         # placeholders; use sdk for real agents
loom run 2026-10-demo-onboarding                # → pauses at gate 1
# set research_direction in engagements/2026-10-demo-onboarding/01-intake/brief.md, then
loom decide 2026-10-demo-onboarding approve     # → research round 1, pauses at gate 2
loom decide 2026-10-demo-onboarding send        # → waits on the org
printf "Laptops came a week late\nNobody owns the buddy system\n" > answers.txt
loom add 2026-10-demo-onboarding responses answers.txt
loom decide 2026-10-demo-onboarding approve     # → framing, workshop design, gate 3
loom validate
```

Instead of `loom decide`, you can write the decision into `state.json` yourself and run
`loom run <id>`:

```json
"pending_decision": {"decision": "approve", "by": "andreas"}
```

Web app: `LOOM_INLINE_WORKER=1 uvicorn app.main:app --port 8080`, run from this folder, then open
http://localhost:8080 and press *New engagement*. Add `LOOM_UNDO_SECONDS=5` to shorten the undo
window while you try it.

## Real agents

```bash
export ANTHROPIC_API_KEY=...
export LOOM_RUNNER=sdk
export LOOM_EXTRA_SKILL_DIRS=~/.claude/skills   # where your reused skills live
loom new "Client X onboarding" --intent "..." --sponsor COO --client "Client X" --sector retail
loom add <id> transcripts meeting.vtt
loom add <id> documents deck.pdf
```

Before you run real client material, settle the data question in `docs/architecture.md`.
