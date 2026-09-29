---
name: loom-secondary-research
description: Archive-first secondary research for Loom engagements - searching past engagements before the web, judging source quality, and mapping findings to gaps. Use for research-secondary.
---

# Secondary research

## 1. Archive first

The archive is `archive/*.md`, one file per closed engagement, with frontmatter: client,
sector, challenge_type, tags, decisions, landed, did_not_land, lessons.

1. Grep frontmatter for the client name, the sector, and the challenge type words.
2. Read every hit's lessons and landed/did_not_land lists in full.
3. Anything relevant is a finding with `from_archive: true` and `source: archive/<file>`.
   Lessons about how this organisation decides, who blocks, what failed before, are the
   most valuable findings in the whole pipeline.

## 2. Then outward

Follow `research_direction` from the brief. Priority of sources: the client's own
published material; regulators and official statistics; peer-reviewed or established
research bodies; reputable trade press; vendor material last (flag it as vendor).

- Open every source you cite. Never state a figure you did not read on the page.
- Record the date of each source; flag anything older than three years.
- Confidence: 0.9 primary/official, 0.7 established secondary, 0.5 trade press, 0.3 vendor
  or single anonymous source.

## 3. Gaps

A gap is a question the brief needs answered that neither archive nor web could answer,
usually because only people inside the organisation know it. Write each gap so it can be
turned into one question: specific, answerable, with `why_it_matters`.

Round 2 and later: close gaps using responses.md (cite `02-research/round-NN/responses.md#aN`
in `evidence_refs`), and do not reopen gaps already answered.

## 4. Body

A short narrative: what is already known, what the archive tells us about this kind of
engagement, what remains open. The evidence skill's provenance table goes at the end.
