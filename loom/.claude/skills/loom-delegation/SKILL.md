---
name: loom-delegation
description: Classifying workshop actions as decision, process or tool, and splitting compound actions, for Loom delegation.
---

# Delegation

## Types

- **decision**: someone must choose between options and record the choice. Output: a
  decision record. Test: "once chosen, is the work done?" → decision.
- **process**: a way of working across people must be defined or changed. Output: steps,
  roles, RACI. Test: "does it repeat and involve hand-offs?" → process.
- **tool**: a system, template, dashboard or artefact must be built or configured. Output:
  requirements and acceptance criteria. Test: "could a builder start from it?" → tool.

## Splitting

Split an action whose text contains more than one verb of different types: "Decide the
owner and set up the tracker" → x4a decision, x4b tool. Keep `from_action: x4` on both.
Split when owners differ. Do not split steps of one process.

## Correcting types

The room labels quickly. If an action labelled tool is really "choose which tool", it is a
decision first. Correct the type and explain in the routing body.

## Carrying context

Keep owner and due from outcomes. An owner of TBC stays TBC and is listed in the handoff:
gate 4 is where a person assigns it.
