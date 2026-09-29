---
name: loom-question-design
description: Writing unbiased survey and email questions for people inside a client organisation - one gap per question, audience sizing, and normalising raw responses. Use for Loom research-primary.
---

# Question design

## Rules

1. One gap per question. Every question has `gap_ref`. If a gap needs two questions, it
   was two gaps.
2. Never ask what is already known (archive, secondary findings, earlier responses).
   People's time is the scarcest input in the pipeline.
3. Unbiased wording:
   - no leading questions ("How much does the slow laptop process frustrate you?" → "How
     long after your start date did you have a working laptop?")
   - no double-barrelled questions
   - no jargon or EC vocabulary; the respondent's words
   - behaviour before opinion: ask what happened last time, then what they think
4. Format: `open` for why/how, `single_choice` or `multi_choice` with exhaustive options
   including "Other" and "Don't know", `scale` 1-5 with labelled ends.
5. Keep a survey under 8 questions and 5 minutes. An email under 4 questions.

## Channel and audience

- survey: many respondents, comparable answers, low context needed.
- email: few people with specific knowledge; context matters; allow a reply in their words.
- `audience` names who (roles, not individuals, unless the brief names them).
- `audience_size`: enough to see a pattern. Roughly 5-8 for email interviews by role, 20+
  for a survey where you want proportions.

## Body

The ready-to-send text: a two-line intro saying why they are asked and how answers are used
(anonymised by default), the questions, and a closing date.

## Normalising responses

One answer per respondent per question, keyed by `question_id`. Quote; never strengthen,
summarise away, or merge. Keep "don't know" and blanks as answers where the question was
shown. Respondents anonymised as r1, r2… Record `respondents` as the number of people, not
answers.
