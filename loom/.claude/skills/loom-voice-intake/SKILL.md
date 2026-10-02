---
name: loom-voice-intake
description: Turning noisy, partly mis-transcribed meeting transcripts (MS Teams, Zoom, Otter exports) into structured claims with honest confidence. Use for Loom intake-voice and workshop-capture.
---

# Voice intake

Transcripts come from recording tools, never audio. They are noisy: wrong words, merged
speakers, missing punctuation, Greek and English mixed, names misspelled.

## 1. Clean without rewriting

- Keep the transcript's timestamps. Normalise to `hh:mm:ss`.
- Fix only obvious formatting (line breaks, duplicated fragments from caption overlap).
- Never "correct" a word to what you think was meant. If a word looks wrong, that is an
  uncertainty, not an edit.

## 2. Separate speakers

- Use the label the tool gives. If two people share a label (room microphone), write
  `Room (unclear speaker)` and lower confidence of claims whose attribution matters.
- Record a role only when stated in the meeting ("as head of HR, I…").

## 3. Extract claims

A claim is one statement about the problem, organisation, history, constraint, number,
owner, or intent. Keep the speaker's meaning and hedges ("I think", "maybe"). Split lists
into separate claims. Skip greetings and logistics.

## 4. Score confidence (0 to 1)

Start at 0.9 and subtract:

| Signal | Subtract |
| --- | --- |
| A word in the claim is marked inaudible, garbled, or implausible in context | 0.3 |
| The speaker hedges or says they are unsure | 0.1 |
| Attribution is unclear | 0.1 |
| The claim is a number, date or name heard once and not repeated | 0.1 |
| Second-hand ("they told me that…") | 0.1 |

Never below 0.1. Below 0.5 is "low confidence": kept, listed, and weighted less wherever
it is used as evidence. A low-confidence claim never carries a problem alone.

## 5. List doubts

Every passage you could not hear goes in `uncertain[]`: timestamp, the words as
transcribed, and why you doubt them. Do not chase or resolve them; the reviewer may at
gate 1.

## 6. Output

Claims with ids c1, c2… in transcript order. The body gives a two-paragraph plain summary
of the conversation (who said what, where they disagreed) for the gate 1 reviewer.
