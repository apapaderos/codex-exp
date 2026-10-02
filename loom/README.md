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

## Run it on your machine (live, with Claude)

You need Python 3.11+ and one of: a Claude Code login (run `claude` once and sign in) or an
`ANTHROPIC_API_KEY`. Then, from this folder:

```bash
./start.sh
```

The first run sets up `loom/.venv`, then `loom doctor` checks everything in plain words
(Python, folders, your shared skills, and one tiny Claude call). Your browser opens at
http://localhost:8080.

Try it end to end:

1. **New engagement → Try it with the sample onboarding files.** Loom starts reading the
   kickoff call and the process document. You can watch what it's doing under the progress
   line ("Reading…", "Searching the web: …").
2. **Understand.** Open the cards, then type where research should point and press
   *Research this direction*. You have a few seconds to Undo.
3. **Research.** While it works, ask it anything in the composer. When it's your turn,
   open *Questions for the team* and press **Edit** to change the draft, then *Send questions*.
4. **Waiting on replies.** Press *Add the sample replies* (or paste your own), then
   *That's all the replies, continue*. Round 2 runs on the replies.
5. **Enough research, frame it** → the problems are split and the session designed.
   *The session is ready* → *Add the sample workshop notes* → *That's everything from the room*.
6. **Hand over.** Set owners and meeting dates → *Record the hand-over*.
7. **Track.** *Add the sample owner update* → *Updates added, check progress* → *Close and archive*.

A full live run takes about 25 to 35 minutes of Claude work and, with the default models, costs
roughly $5 to $8 (the panel shows usage so far).

**Your own material.** The files in `samples/` are plain text: edit them before you add them,
or start a real engagement with *New engagement* and drop in your own transcripts and
documents. When it's your turn you can edit Loom's drafts for that step and any text file you
gave it ("Your files" in the panel); the brief has its own form.

Useful settings (environment variables): `LOOM_RUNNER=stub` demo mode with no Claude calls,
`PORT=8090`, `LOOM_UNDO_SECONDS` (10 locally), `LOOM_MODEL_FRAMING=sonnet` etc. to trade
quality for cost, `LOOM_EXTRA_SKILL_DIRS` if your shared skills live somewhere other than
`~/.claude/skills`. Engagements are stored in `engagements/`, closed ones in `archive/`.

Before you put real client material through it, settle the data question in
`docs/architecture.md`.

## Command line (no browser)

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
