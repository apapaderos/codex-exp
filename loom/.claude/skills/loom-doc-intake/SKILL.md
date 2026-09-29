---
name: loom-doc-intake
description: Reading documents, slides, diagrams and images into claims that cite page and region, and describing visuals in text. Use for Loom intake-docs and workshop-capture.
---

# Document and diagram intake

## Sources

Give every file a ref s1, s2… and a kind: document, slides, diagram, image, spreadsheet,
other. Read PDFs page by page. Look at images directly.

## Claims

- One statement per claim, ids c1, c2…
- `source_ref` is precise: `s2 p4`, `s3 slide 7`, `s5 region top-left sticky cluster`.
- Mark the document's date and owner if stated; a 2019 process document describes 2019.
- Distinguish what the document says from what it implies. Implications are claims with
  lower confidence (0.5) and the word "implies" in the text.

## Diagrams and images

Describe in text, in reading order: the elements, the connections and their direction,
labels verbatim, groupings, anything crossed out or highlighted. Then one line on what the
visual implies, marked as inference. For photos of walls or canvases (workshop capture):
transcribe each sticky or card that is legible; list illegible ones as illegible, never
guess.

## Spreadsheets

State what the columns are, the row count, and the figures that matter to the challenge,
with the cell range as region.

## Failures

A file you cannot read (password, corrupt, unsupported) goes in the handoff under
`unsure`. Never invent content for it.
