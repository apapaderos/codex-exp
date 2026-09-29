# Loom

Loom runs a PwC Athens Experience Center engagement end to end, from raw intake through
workshop to tracked adoption, with four human gates. The architecture is `docs/loom-spec.md`;
the decisions taken while building it are in `docs/architecture.md`.

## Stage order

1. **Intake**: intake-voice (transcripts) and intake-docs (documents, diagrams), then the brief merge. → Gate 1
2. **Research**: rounds of research-secondary then research-primary, until a person calls it. → Gate 2 each round
3. **Framing**: framing splits problems into offline and co-creation. (no gate; flows into 4)
4. **Workshop**: workshop-designer → Gate 3 → people run the room → workshop-capture.
5. **Delegation**: delegation routes actions; spec-decision, spec-process, spec-tool write specs. → Gate 4
6. **Tracking**: tracking sets KPIs and OKRs and follows adoption; when goals are met → close (archivist) or restart as a new engagement.

## Gates

1. **Input** (after intake): is the signal right, where should research point? Approval needs `research_direction` in brief.md.
2. **Research** (after each round): another round, curated questions out, or done. A person is the stopping rule; there is no saturation metric.
3. **Workshop** (after framing and design): is the problem split right, is the session ready to run?
4. **Delegation** (after specs): not an approval. Which specs go to which owners and when you meet them. The meetings are human work.

Decisions: approve, revise (with notes), reject (back to an earlier stage), plus another_round and send at gate 2, and close or restart at the end.

## The three rules

1. The orchestrator only reads `state.json`, dispatches the next agent, and moves the stage forward. It writes no content.
2. Every agent writes its artifact plus a handoff saying what it did, what it is unsure of, and what the reviewer should check.
3. No stage advances past a gate until `state.json` records a human decision.

**No agent may advance a stage past a gate. No agent writes state.json or log.jsonl except the orchestrator.**

## File contracts

- The engagement folder is the single source of truth. Agents never talk to each other; they read and write files there.
- Every artifact is markdown with YAML frontmatter. The frontmatter validates against `schemas/<artifact>.json` before the stage can leave running. The body is for humans.
- Ids make the chain traceable: claims `c1`, findings `f1`, gaps `g1`, questions `q1`, answers `a1`, problems `p1`, decisions `d1`, actions `x1`. `evidence_refs` look like `02-research/round-01/responses.md#a4`.
- Low-confidence claims (below 0.5) are kept, and weigh less wherever they are used as evidence.
- Only research-secondary touches the web. Loom sends nothing to anyone: people send questions and offline messages.

## Working here

- `loom status`, `loom run <id>`, `loom decide <id> <decision>`, `loom validate` (see `engine/loom/cli.py`).
- `LOOM_RUNNER=stub` writes schema-valid placeholders with no API calls; `LOOM_RUNNER=sdk` runs the real agents.
- Tests: `pytest` from this folder. Any change to a schema, an agent's outputs, or the orchestrator must keep `tests/test_pipeline.py` green.
- Engagement data is client data. Do not commit real engagements; `engagements/` and `archive/` are git-ignored except the demo.
