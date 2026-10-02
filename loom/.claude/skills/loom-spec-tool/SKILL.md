---
name: loom-spec-tool
description: Tool requirements template with users and acceptance criteria for Loom spec-tool.
---

# Tool specification

Frontmatter per schemas/spec.json (`type: tool`). Body:

```markdown
# Tool <item_id>: <name>

## Problem
What it solves (problem id), for whom, what happens today without it.

## Users and jobs
| User (role) | Job to be done | How often |
| --- | --- | --- |

## Requirements
- MUST ...
- SHOULD ...
- COULD ...
Each requirement is one testable sentence.

## Constraints
Data (what personal data, where it may live), security, systems it must connect to,
budget, accessibility.

## Acceptance criteria
Given / when / then statements a tester can run.

## Out of scope
What it will not do in this version.

## Open questions
Anything the builder must ask, with who can answer.
```

Do not name a vendor or product unless the room decided one; if they did, cite it.
