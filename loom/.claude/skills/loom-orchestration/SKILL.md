---
name: loom-orchestration
description: Stage order, gate rules, and the state.json and log.jsonl formats for Loom engagements. Use when moving a Loom engagement forward, recording a gate decision, or checking why an engagement is where it is.
---

# Loom orchestration

The orchestrator is a small state machine. `engine/loom/orchestrator.py` is the reference
implementation; this skill states the rules it implements.

## Stages and gates

| Stage | Agents, in order | Ends at |
| --- | --- | --- |
| 1 intake | intake-voice (if transcripts), intake-docs (if documents), then the brief merge | Gate 1 |
| 2 research | per round NN: research-secondary, then research-primary | Gate 2 |
| 3 framing | framing | straight into stage 4 (no gate) |
| 4 workshop | workshop-designer → Gate 3 → people run the room → workshop-capture | stage 5 |
| 5 delegation | delegation, then one spec agent per routed item | Gate 4 |
| 6 tracking | tracking → wait on adoption; when all KPIs are met → Gate 5 (close) | archivist, closed |

## Status cycle (per stage)

`queued → running → awaiting_gate → done`, with `waiting_org` for the long pauses:
responses to research questions, the workshop itself, and adoption. `waiting_org` is not
stuck; the app shows who was asked and how many answered.

## Triggers (the only three)

1. A gate decision (`pending_decision` in state.json, `loom decide`, or the app).
2. New files in `00-inputs/` declared complete (transcripts, documents, workshop, tracking).
3. Responses arriving (`00-inputs/responses/` or a hand-written `round-NN/responses.md`).

The orchestrator never polls agents and never decides content.

## Decisions

| Gate | Allowed | Effect |
| --- | --- | --- |
| 1 Input | approve, revise | approve needs `research_direction` set in brief.md; opens research round 1 |
| 2 Research | approve, revise, reject, another_round, send | send → wait on responses → next round; another_round → next round with the notes as prompts; approve → framing |
| 3 Workshop | approve, revise, reject | approve → wait for the workshop to run; capture inputs trigger workshop-capture |
| 4 Delegation | approve (with assignments), revise, reject | not an approval: records which spec goes to which owner and when you meet them |
| 5 Close | close, restart, revise | close → archivist; restart → archivist plus a new engagement linked by `parent` |

- revise: re-run the current stage with the reviewer's notes.
- reject: go back to `target_stage` (earlier than the current one). Research history is
  never overwritten; re-entering stage 2 opens a fresh round.
- A blocked gate (`gate.error` set: an agent failed or its output did not validate after one
  retry) accepts only revise or reject.
- A refused decision is logged and shown in `gate.refused`; nothing moves.

## state.json (schemas/state.json)

```json
{
  "id": "2026-10-acme-onboarding", "title": "...", "intent": "...", "sponsor": "...",
  "stage": 2, "status": "awaiting_gate", "closed": false,
  "stages": {"1": {"status": "done", "started_at": "...", "finished_at": "..."}, "2": {"status": "awaiting_gate"}, ...},
  "research": {"round": 1, "prompts": []},
  "gate": {"gate": 2, "opened_at": "...", "question": "...", "error": null},
  "waiting": null,
  "notes": "",
  "pending_decision": null,
  "decisions": [{"gate": 1, "decision": "approve", "by": "andreas", "at": "...", "notes": ""}],
  "assignments": []
}
```

To decide by hand, set `pending_decision` and run `loom run <id>`:

```json
"pending_decision": {"decision": "revise", "by": "andreas", "notes": "Weight the store managers' view more"}
```

## log.jsonl (schemas/log-event.json)

One JSON object per line, append-only: `{"ts", "actor", "event", "stage", "detail"}`.
Events: created, dispatched, agent_done, agent_failed, validated, invalid, stage,
gate_opened, gate_decision, waiting, wait_ended, inputs, merged, closed, note.

## File contracts

Every agent writes its artifact(s) plus a handoff (`handoff-<agent>.md`, or
`<item>.handoff.md` for spec agents) validating against `schemas/handoff.json`. A stage
cannot leave running until its artifacts validate. Cross-file checks the orchestrator
also runs: `gap_ref` resolves in secondary.md, `question_id` resolves in questions.md,
`evidence_refs` in problems.md resolve, the agenda covers every co-creation problem,
routing covers every action.
