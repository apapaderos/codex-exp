---
name: loom-spec-decision
description: Decision record template for Loom spec-decision.
---

# Decision record

Frontmatter per schemas/spec.json (`type: decision`). Body:

```markdown
# Decision <item_id>: <the question, phrased as a question>

## Context
Two or three sentences: what problem this serves (problem id), what the room said.

## Options
| Option | For | Against | Cost / effort |
| --- | --- | --- | --- |
| A ... | | | |
| B ... | | | |
| Do nothing | | | |

## Criteria
What the decision should be judged on, in priority order.

## Rationale from the room
What was argued, by whom (role), with source refs.

## Recommendation
The option the room leaned to, or "none reached" with what would settle it.

## Who
- Decides: <owner>
- Consulted: ...
- Informed: ...

## Deadline and consequences
Decide by <date>. Once decided: what changes, who acts next, where the decision is recorded.
```

Every factual claim carries an evidence ref in brackets, e.g. [outcomes#d2].
