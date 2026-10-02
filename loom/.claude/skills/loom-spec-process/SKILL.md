---
name: loom-spec-process
description: Process specification template with RACI and exceptions for Loom spec-process.
---

# Process specification

Frontmatter per schemas/spec.json (`type: process`). Body:

```markdown
# Process <item_id>: <name>

## Purpose
The problem it solves (problem id) and what "good" looks like.

## Trigger and end
Starts when ... Ends when ...

## Steps
| # | Step | Role | Input | Output | Time limit |
| --- | --- | --- | --- | --- | --- |

## RACI
| Activity | Responsible | Accountable | Consulted | Informed |
| --- | --- | --- | --- | --- |
Exactly one Accountable per activity.

## Hand-offs
Every point where work changes hands: what is handed over, how, and how receipt is confirmed.

## Exceptions
| Exception | Detected by | Handled by | Escalation |
| --- | --- | --- | --- |

## Service level
Measurable promises (feeds tracking KPIs).

## Transition
How the old way is retired, pilot scope, date live.
```

Write roles, not names, in steps; the owner assigns names at gate 4.
