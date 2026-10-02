# Loom — agent architecture

Sep 28, 2026 · @Andreas Papaderos

Loom is a multi-agent system that runs a PwC Athens Experience Center engagement end to end, from raw intake through workshop to tracked adoption, with four human gates. Prototype first in Claude Code or Cowork, then hand the architecture to the team to build as an app ecosystem.

## Pipeline at a glance

&#91;embedded content: Loom pipeline · 6 stages, 1 internal loop, 1 restart\]

Stages run in order, with two loops. Research cycles between secondary and primary until the human calls it. Tracking closes the circle: once KPI goals are met, scale pressure or growth ambition raises new problems that enter as a fresh engagement.

## The agents

The core agents by stage. The build adds three more, an orchestrator, a workshop capture agent and an archivist, listed under Agent definitions.

| Agent | Stage | Takes in | Puts out |
| --- | --- | --- | --- |
| Voice intake | 1 | Transcripts uploaded from recording tools such as MS Teams, noisy and partly mis-transcribed | Structured signal, with low-confidence passages flagged |
| Document and diagram intake | 1 | Written material, slides, diagrams, images | Structured signal from text and visuals |
| Secondary research | 2 | Intake output, plus the engagement archive | Context and prior art: what is already known |
| Primary research | 2 | Gaps left by secondary research | Questions for surveys or emails to people in the org |
| Framing | 3 | Intake plus both research streams | Two problem sets: offline-solvable, and co-creation |
| Workshop design | 4 | The co-creation set only | A Solve-for-X session design |
| Delegation | 5 | Workshop decisions and action items | Routing of each item to the right sub-agent |
| Sub-agents: decisions, processes, tools | 5 | One action item each, by type | An implementation spec an owner can act on |
| Tracking | 6 | Each spec | KPIs, OKRs, and adoption status back through the org |

Solve-for-X sits inside workshop design rather than governing the pipeline. Reframe, Explore and Shape structure the session, not the agents upstream of it.

## Human gates

Four points where a person intervenes. Nothing passes between these stages unattended.

1. **After intake.** Review the raw input and steer the system towards the right primary and secondary research direction.
2. **After research.** Review results, add prompts to pull in more specific information, and curate the questions that go out to primary research participants. This gate is also the stopping rule for the research loop: no saturation metric, a person decides whether to push further or call it.
3. **At workshop design.** Orchestrate and approve the session before it runs.
4. **At delegation.** Not an approval. A person holds meetings that explain the requirements to the different participants in the organisation. The system produces the specs; it cannot do this part.

The workshop itself is run by people, not by Loom. Stage 4 produces the design, and what comes out of the room becomes input to stage 5.

## Engagement archive

Without this, every engagement starts cold and the system relearns the same organisational politics and constraints each time.

Each engagement is stored as a markdown file holding its context, the decisions taken, the story behind it, the workshop output and the action items. Secondary research hits this index first, before going outward, and feeds anything useful to primary research so the org is not asked what has already been answered.

One decision to take early: flat markdown works at twenty engagements and becomes unsearchable at two hundred. Structured frontmatter per file, covering at least client, sector, challenge type and what actually landed, makes retrieval something better than keyword luck.

## Build in Claude Code

One repo, four layers. Agents are Claude Code subagents, markdown files with YAML frontmatter in `.claude/agents/` ([docs](https://code.claude.com/docs/en/subagents)). Each gets its own context window, tool list and model. Skills hold the reusable method each agent follows. The engagement folder on disk is the single source of truth: agents never talk to each other directly, they read and write files there.

```
loom/
  CLAUDE.md                     # project rules: stages, gates, file contracts, never skip a gate
  .claude/
    agents/
      orchestrator.md           # advances state, dispatches, never produces content
      intake-voice.md
      intake-docs.md
      research-secondary.md
      research-primary.md
      framing.md
      workshop-designer.md
      workshop-capture.md
      delegation.md
      spec-decision.md
      spec-process.md
      spec-tool.md
      tracking.md
      archivist.md
    skills/                     # one folder per skill, each with SKILL.md
  schemas/                      # JSON Schema per artifact type
  archive/                      # one markdown file per closed engagement
  engagements/
    2026-10-acme-onboarding/
      state.json                # current stage, status, gate decisions
      log.jsonl                 # append-only event log: who did what, when
      00-inputs/                # raw uploads: transcripts, docs, diagrams
      01-intake/
      02-research/              # round-01/, round-02/ ... one folder per loop
      03-framing/
      04-workshop/              # design/ and capture/
      05-delegation/            # decisions/ processes/ tools/
      06-tracking/
  app/                          # the web interface, built on the Claude Agent SDK
  infra/                        # cloud environment as code, provisioned via Claude Code
```

Three rules hold the system together. First, the orchestrator only reads `state.json`, dispatches the next agent, and moves the stage forward; it writes no content. Second, every agent writes its artifact plus a short `handoff.md` saying what it did, what it is unsure of, and what the reviewer should check. Third, no stage advances past a gate until `state.json` records a human decision.

The workshop gets a capture step, `workshop-capture`, that reuses the intake skills on what comes out of the room: photos of walls, facilitator notes, meeting transcripts. Delegation reads that, not the design.

### Environment

Loom runs in the cloud; the provider is not chosen yet. The whole environment is defined as code in `infra/` and provisioned from your machine through Claude Code, so it can be torn down, rebuilt, and handed to the team as-is. Whatever the provider, Loom needs five things:

- **App hosting** for the web interface.
- **Durable storage** for the engagement folders and the archive, replacing the local disk.
- **A job runner** for agent runs that outlive a browser tab and the days-long waits on the org.
- **A secrets store** for the Anthropic API key and any connector credentials.
- **Notifications** for gates opening and waits ending.

Claude Code writes and runs the provisioning, but every change that creates a paid resource, opens network access or touches credentials waits for your approval, the same gate principle as the rest of Loom.

## Orchestration

Every stage runs the same status cycle, so the orchestrator is a small state machine rather than a clever agent.

&#91;embedded content: stage status cycle · 5 states, 1 gate\]

At a gate the reviewer has three answers: approve, revise with notes, or reject back to an earlier stage. Research adds a fourth, run another round, which sends it back to Running with the reviewer's new prompts. Waiting on the org is the long pause: primary research sits there for days while survey and email responses come in, and the app shows it as such rather than as stuck.

The orchestrator runs on three triggers only: a gate decision, a new file in `00-inputs/`, or responses arriving. It never polls agents and never decides content. Every transition is appended to `log.jsonl`, so an engagement can be replayed or resumed after days away.

| Gate | Sits after | Reviewer decides |
| --- | --- | --- |
| 1 Input | Intake | Is the signal right, and where should research point? |
| 2 Research | Each research round | Another round, curated questions out, or done |
| 3 Workshop | Framing and workshop design | Is the problem split right, is the session ready to run? |
| 4 Delegation | Specs from all three sub-agents | Which specs go to which owners, and when you meet them |

## Handoff contracts

Each artifact is a markdown file with YAML frontmatter: the frontmatter is the contract a downstream agent can rely on, the body is for humans. Every frontmatter validates against its schema in `schemas/` before the stage can leave Running.

| Artifact | Written by | Read by | Required frontmatter |
| --- | --- | --- | --- |
| `01-intake/signal-voice.md` | intake-voice | research-secondary, gate 1 | `speakers`, `claims[]` each with `text` `confidence` `timestamp`, `uncertain[]` |
| `01-intake/signal-docs.md` | intake-docs | research-secondary, gate 1 | `sources[]`, `claims[]` each with `text` `source_ref`, `diagrams[]` with a text description |
| `01-intake/brief.md` | orchestrator merge, edited at gate 1 | every later stage | `challenge`, `sponsor`, `stakeholders[]`, `constraints[]`, `research_direction` |
| `02-research/round-NN/secondary.md` | research-secondary | research-primary, gate 2 | `findings[]` each with `text` `source` `from_archive`, `gaps[]` |
| `02-research/round-NN/questions.md` | research-primary | gate 2, then sent out | `audience`, `channel` survey or email, `questions[]` each with `gap_ref` |
| `02-research/round-NN/responses.md` | you, pasted or exported | research-secondary next round | `respondents`, `answers[]` keyed to `questions[]` |
| `03-framing/problems.md` | framing | workshop-designer, gate 3 | `offline[]` and `cocreate[]`, each with `problem` `owner` `evidence_refs[]` |
| `04-workshop/design/agenda.md` | workshop-designer | gate 3, you in the room | `problems_in_scope[]`, `duration`, `acts[]` Reframe Explore Shape |
| `04-workshop/capture/outcomes.md` | workshop-capture | delegation | `decisions[]`, `actions[]` each with `type` decision, process or tool, `owner`, `due` |
| `05-delegation/<type>/<item>.md` | spec-decision, spec-process, spec-tool | gate 4, tracking | `item_id`, `owner`, `definition_of_done`, `dependencies[]` |
| `06-tracking/<item>.md` | tracking | archivist, you | `kpis[]` each with `metric` `baseline` `target` `by`, `okr`, `status` |
| `archive/<engagement>.md` | archivist | research-secondary on the next engagement | `client`, `sector`, `challenge_type`, `decisions[]`, `landed[]`, `lessons[]` |

The `evidence_refs` and `gap_ref` fields are what make the chain traceable: any claim in a spec can be walked back to the transcript line or survey answer it came from. Offline problems from framing get their own short route: framing drafts the email or message, you send it, and the agreement is logged in `03-framing/offline-log.md`.

## Agent definitions

Fourteen subagent files. Tools are kept narrow on purpose: only research agents touch the web, only the orchestrator touches `state.json`. Model choices are a starting point to tune once you see cost and quality.

| Agent file | Tools | Model | Skill it loads | Done when |
| --- | --- | --- | --- | --- |
| `orchestrator.md` | Read, Write (state and log only), Task | sonnet | `loom-orchestration` | Next agent dispatched, state updated |
| `intake-voice.md` | Read, Write | sonnet | `loom-voice-intake` | Every claim has a confidence; doubts listed |
| `intake-docs.md` | Read, Write | sonnet | `loom-doc-intake` | Every claim cites a page or diagram |
| `research-secondary.md` | Read, Write, Grep, WebSearch, WebFetch | sonnet | `loom-secondary-research`, `evidence` | Archive checked first; gaps listed |
| `research-primary.md` | Read, Write | sonnet | `loom-question-design` | Each question maps to one gap |
| `framing.md` | Read, Write | opus | `loom-problem-triage`, `reframe` | Every problem is offline or co-creation, with evidence |
| `workshop-designer.md` | Read, Write | opus | `solve-for-x`, `senior-experience-architect` | Agenda covers every co-creation problem |
| `workshop-capture.md` | Read, Write | sonnet | `loom-voice-intake`, `loom-doc-intake` | Every action has a type, owner and due date |
| `delegation.md` | Read, Write, Task | sonnet | `loom-delegation` | Each action routed to one spec agent |
| `spec-decision.md` | Read, Write | opus | `loom-spec-decision` | Decision record with options, rationale, owner |
| `spec-process.md` | Read, Write | opus | `loom-spec-process` | Process with steps, roles, RACI, exceptions |
| `spec-tool.md` | Read, Write | opus | `loom-spec-tool` | Tool spec with users, requirements, acceptance |
| `tracking.md` | Read, Write | sonnet | `loom-kpi-okr` | Each spec has baseline, target and date |
| `archivist.md` | Read, Write | haiku | `loom-archive` | Archive file validates against its schema |

One full file, to show the shape every agent follows:

```markdown
---
name: research-secondary
description: Secondary research for a Loom engagement. Use when a Loom
  engagement is in stage 2 and needs context, prior art or archive lookups.
tools: Read, Write, Grep, WebSearch, WebFetch
model: sonnet
---

You are the secondary research agent in Loom.

Read first: engagements/<id>/01-intake/brief.md, and if this is round 2 or
later, the previous round's responses.md and the reviewer notes in state.json.

Always search archive/ before the web. Mark every finding from_archive: true
or false. Never state a figure you did not open a source for.

Write engagements/<id>/02-research/round-NN/secondary.md following
schemas/secondary.json, then handoff.md: what you found, the gaps you
could not close, and what the reviewer should check. Follow the
loom-secondary-research skill for method. Do not write questions for the
org; that is research-primary's job.
```

## Skills

Agents say who does the work; skills say how. Twelve new skills live in `.claude/skills/`, and four you already have are reused rather than rebuilt. Copy those four into the repo so the build does not depend on your personal setup.

| Skill | New or reuse | Used by | What it encodes |
| --- | --- | --- | --- |
| `loom-orchestration` | New | orchestrator | Stage order, gate rules, state.json and log.jsonl formats |
| `loom-voice-intake` | New | intake-voice, workshop-capture | Cleaning noisy transcripts, speaker separation, confidence scoring, never guessing a misheard word, low-confidence claims kept but weighted less |
| `loom-doc-intake` | New | intake-docs, workshop-capture | Reading docs and diagrams, describing visuals in text, citing page and region |
| `loom-secondary-research` | New | research-secondary | Archive-first search, source quality, finding-to-gap mapping |
| `evidence` | Reuse | research-secondary | Provenance table, what could not be verified |
| `loom-question-design` | New | research-primary | Unbiased survey and email questions, one gap per question, audience sizing |
| `loom-problem-triage` | New | framing | The offline versus co-creation test, with criteria |
| `reframe` | Reuse | framing | Challenging the framing before splitting it |
| `solve-for-x` | Reuse | workshop-designer | Reframe, Explore, Shape agenda formats |
| `senior-experience-architect` | Reuse | workshop-designer | Facilitation guide and room dynamics |
| `loom-delegation` | New | delegation | Classifying actions as decision, process or tool, splitting compound actions |
| `loom-spec-decision` | New | spec-decision | Decision record template |
| `loom-spec-process` | New | spec-process | Process spec template with RACI and exceptions |
| `loom-spec-tool` | New | spec-tool | Tool requirements template with acceptance criteria |
| `loom-kpi-okr` | New | tracking | Picking measurable KPIs, setting baselines, writing one OKR per item |
| `loom-archive` | New | archivist | Archive frontmatter, what counts as landed, lessons worth keeping |

The triage skill carries the most judgement and deserves the most care. A first cut at its test: a problem is offline when one owner can decide it and the others only need informing; it is co-creation when several parties hold part of the answer, or when agreement matters as much as the answer itself.

## App interface

Loom looks and behaves like Claude, with the extra harness an engagement needs. Three columns: almost everything happens in the conversation in the centre, the left is where you go, and the right is what the current engagement holds. It is a web app on the Claude Agent SDK, hosted in the cloud environment described under Build in Claude Code.

The design goal is that someone of any seniority, who has never seen the flow, can start an engagement and follow it to the end without a manual.

&#91;embedded content: Loom app wireframe · navigation, conversation, engagement panel\]

### Centre: the conversation

Each engagement is one long thread that runs for weeks. Loom speaks in plain language, one short message per event, and everything you act on appears inline:

- **Progress messages** say what just happened and what comes next.
- **Output cards** summarise what an agent produced in a line or two, with Open in panel to read it in full on the right.
- **Decision cards** replace gates. Each holds a question, two to four buttons, and one line saying what happens after you press. Buttons name the outcome, never just Approve: Send questions, Dig deeper, Redirect.
- **Choice chips** for quick inputs, such as picking the challenge type or confirming the sponsor.
- **The composer** takes free text, files and notes at any point. Text typed while a decision is open becomes your note to the agent.

A **next-step bar** is pinned above the composer and never scrolls away. It always states the one thing to do now, or that nothing needs you and why: "Loom is waiting on replies from the team." When you return after days, a divider marks what happened since you were last here.

### Left: navigation and settings

- **New engagement** at the top.
- **Engagements**, grouped by Needs you, Running, Waiting on the org and Done. Needs you is the morning inbox; a dot shows where a decision waits.
- **Archive** of closed engagements, searchable.
- **Settings** at the bottom: team access, notifications, connectors, and the environment.

### Right: the engagement

- **Progress**: the six steps, with the current one highlighted. Clicking a past step jumps the conversation to it.
- **Files**: everything produced so far, grouped by step. Files open in a preview over the panel. The brief is editable here, the only file you edit by hand.
- **People**: sponsor, stakeholders, and later the owners of each spec.
- **Tracking**: appears once the Track step starts, with each item's KPIs and status.

The panel collapses. The conversation works on its own, so a phone or a narrow window still runs the whole flow.

### Guided without a manual

The interface uses plain step names. The internal stage names stay in the code.

| On screen | Internal stage | One-line explanation Loom gives when the step opens |
| --- | --- | --- |
| Understand | Intake | I read what you gave me and pull out what matters. |
| Research | Research loop | I look for context, then draft questions for your team. |
| Frame | Framing | I sort the problems: settle by email, or solve in the room. |
| Workshop | Workshop design and capture | I design the session; afterwards, you upload what came out. |
| Hand over | Delegation | I turn each action into a spec someone can own. |
| Track | Tracking | I set targets and follow whether the change lands. |

Five rules keep it self-explanatory:

1. One primary action at a time, always visible in the next-step bar.
2. Every button says what it causes; every decision card says what happens next.
3. No system words on screen: no gate, schema, agent or stage.
4. Nothing irreversible without a confirm, and a decision can be undone until the next step has started.
5. Help is inline, never a separate page: each step opens with its one-line explanation, and any card has a quiet Why am I seeing this.

### Kicking off an engagement

New engagement opens a fresh thread where Loom leads with three questions, one at a time:

1. **What is the challenge?** Type a sentence, or drop in files.
2. **Who is involved?** The sponsor and key stakeholders, as chips you can add to.
3. **What do you have?** An inline drop zone for transcripts from MS Teams or similar tools, documents and diagrams.

Loom then shows a card titled Here is what I understood, with the challenge, people and inputs, and a single Start button. From there, the next-step bar takes over.

Notifications go out on two events only: a decision is waiting, or a wait has ended. Each one links straight to the decision card in the thread.

## Build order

Build the spine before the agents, and run every phase on one real past engagement so each stage has true input to chew on. The app comes last: until then, gates are you editing `state.json` by hand in Claude Code.

| Phase | Build | Done when |
| --- | --- | --- |
| 0 Spine | Repo layout, CLAUDE.md, schemas, state.json and log.jsonl, orchestrator | A dummy engagement moves through all six stages with stub agents, pausing at each gate |
| 1 Intake | Both intake agents and their skills | A real noisy transcript yields claims with honest confidence, and doubts are flagged, not guessed |
| 2 Research loop | Both research agents, the archive search, the waiting state | Two rounds run, questions trace to gaps, responses feed round 2 |
| 3 Framing and workshop | Framing, triage skill, workshop designer on solve-for-x, workshop capture | A past engagement's problems split the way you would have split them |
| 4 Delegation and tracking | Delegation, three spec agents, tracking | Each workshop action becomes a spec an owner could act on without asking you |
| 5 Archive | Archivist, archive frontmatter, backfill of past engagements | A new engagement's round 1 surfaces something useful from an old one |
| 6 App | Three-column app: navigation, conversation with decision cards, engagement panel, notifications, on the Agent SDK, deployed from infra/ to the cloud | Someone new to Loom starts and finishes an engagement with no help from you |

Phase 3 is the one to be patient with. If triage splits problems badly, everything after it inherits the error, so tune that skill against several past engagements before moving on.

## Kickoff prompt

Export this doc as markdown into the repo as `docs/loom-spec.md`, then paste this into Claude Code from the repo root. It asks for phase 0 only and makes Claude Code plan before it writes, so you review the structure before anything is built.

```
Read docs/loom-spec.md in full. It is the architecture for Loom, a
multi-agent system that runs Experience Center engagements through six
stages with four human gates.

Build phase 0 only, the spine:
1. The repo layout exactly as in "Build in Claude Code".
2. CLAUDE.md stating the stage order, the gate rules, the three rules,
   and that no agent may advance a stage past a gate.
3. A JSON Schema in schemas/ for every artifact in "Handoff contracts".
4. state.json and log.jsonl formats, with a small validator script.
5. .claude/agents/orchestrator.md and stub files for the other 13
   agents: correct frontmatter, a one-paragraph role, and output that
   writes a schema-valid placeholder artifact plus handoff.md.
6. A dummy engagement in engagements/ that I can run end to end.

Before writing any file, show me the plan: the files you will create,
the schema fields you are unsure of, and any place where the spec is
ambiguous. Wait for my go-ahead.

Done means: I run the dummy engagement, it pauses at gate 1, I set the
gate decision in state.json, and it moves on, through all six stages.
```

Each later phase gets its own prompt of the same shape: point at the section, name what done looks like, ask for the plan first.

## Decisions and open questions

- [x] Settled: the handoff contract fields are confirmed as drafted, and phase 0 builds the schemas from them.
- [x] Settled: the stage 3 agent is called Framing.
- [x] Archive retrieval: keyword search over frontmatter is enough to start; decide at phase 5 whether embeddings earn their cost.
- [x] Settled: the two intake agents stay separate, one for voice and one for documents and diagrams.
- [x] Settled: low-confidence passages are kept, not dropped or chased, and carry less weight wherever they are used as evidence downstream.
- [x] Settled: Loom runs in the cloud, provisioned end to end from your machine through Claude Code. Still open: which provider, and whether client engagement data may sit there under PwC's data rules. Check the data question before phase 1, not phase 6: phases 1 to 5 already send real engagement material through the Anthropic API.
- [x] Settled: Loom records nothing itself. Recording happens in tools such as MS Teams, and their own transcripts are uploaded, never audio, so Loom needs no transcription step. The name stays.
