"""
Context window manager.

During a 5.5-hour session the transcript can reach 40,000-60,000 tokens.
This module implements the rolling window strategy from the system prompt:
  - Keep the full current activity transcript.
  - Summarise previous activities into compact text.
  - Expose a single build_context() function that returns what should go
    into the LLM prompt for any CONTRIBUTE command.
"""
from __future__ import annotations

from uuid import UUID

import structlog

from nexus.config import settings
from nexus.services import state_manager
from nexus.services.llm import chat

log = structlog.get_logger(__name__)

# Approximate token budget for context passed to LLM calls (leave headroom for output)
_CONTEXT_BUDGET = 12_000   # tokens
_SUMMARY_PROMPT = """\
Compress the following session transcript into a structured summary of 150 words or fewer.
Preserve: assumptions surfaced, key tensions, reframe moments, energy shifts, decisions made.
Discard: pleasantries, repetition, filler.

Transcript:
{transcript}
"""


async def maybe_compress(session_id: UUID) -> bool:
    """
    If the current session's estimated token count exceeds the threshold,
    compress the oldest un-summarised activity transcript into the context_summary dict.

    Returns True if compression occurred.
    """
    state = state_manager.require_state(session_id)

    if state.estimated_tokens < settings.nexus_context_token_limit * 0.85:
        return False

    # The current activity transcript is the live window — don't compress it.
    # Instead, look for any un-compressed earlier activity text still in memory.
    # (In this architecture, earlier transcripts are cleared on activity advance
    #  and replaced by context_summary entries; so we compact the current one early.)
    key = f"{state.current_phase}:{state.current_activity}"
    if key in state.context_summary:
        # Already summarised — nothing to do
        return False

    if not state.current_activity_transcript:
        return False

    full_text = "\n".join(state.current_activity_transcript)
    summary = await chat(
        _SUMMARY_PROMPT.format(transcript=full_text),
        timeout=30.0,   # longer timeout acceptable for compression
    )
    state.context_summary[key] = summary

    # Keep only the last 20 utterances in the live window
    state.current_activity_transcript = state.current_activity_transcript[-20:]
    state.estimated_tokens = sum(len(t) // 4 for t in state.current_activity_transcript)

    log.info("context_compressed", session_id=str(session_id), key=key)
    return True


def build_context(session_id: UUID, include_full_transcript: bool = True) -> str:
    """
    Assemble the context string passed to CONTRIBUTE and SYNTHESIZE commands.

    Structure:
      [PROBLEM] current_x
      [PHASE] current_phase / Activity N
      [HISTORY] compressed summaries of past activities
      [CURRENT ACTIVITY TRANSCRIPT] last N utterances
      [ASSUMPTIONS] list
      [TENSIONS] list
      [DIRECTIONS] list
      [COMMITMENTS] list
    """
    state = state_manager.require_state(session_id)
    parts: list[str] = []

    parts.append(f"[PROBLEM]\n{state.current_x or 'Not yet defined'}")
    parts.append(f"[PHASE] {state.current_phase} / Activity {state.current_activity}")
    if state.phase_outcome:
        parts.append(f"[REFRAME OUTCOME] {state.phase_outcome}")
    parts.append(f"[ENERGY] {state.energy_level}")

    if state.context_summary:
        summaries = "\n".join(
            f"  {k}: {v}" for k, v in sorted(state.context_summary.items())
        )
        parts.append(f"[HISTORY]\n{summaries}")

    if include_full_transcript and state.current_activity_transcript:
        transcript = "\n".join(state.current_activity_transcript[-40:])
        parts.append(f"[CURRENT ACTIVITY TRANSCRIPT]\n{transcript}")

    if state.assumptions:
        alist = "\n".join(
            f"  - [{a.status}] {a.text}" + (f" (evidence: {a.evidence})" if a.evidence else "")
            for a in state.assumptions
        )
        parts.append(f"[ASSUMPTIONS]\n{alist}")

    if state.tensions:
        tlist = "\n".join(
            f"  - [{t.status}] {t.description} (stakeholders: {', '.join(t.stakeholders)})"
            for t in state.tensions
        )
        parts.append(f"[TENSIONS]\n{tlist}")

    if state.directions:
        dlist = "\n".join(
            f"  - {'[SELECTED] ' if d.is_selected else ''}{d.description}"
            for d in state.directions
        )
        parts.append(f"[DIRECTIONS EXPLORED]\n{dlist}")

    if state.commitments:
        clist = "\n".join(
            f"  - {c.owner}: {c.action}" + (f" (deadline: {c.deadline})" if c.deadline else "")
            for c in state.commitments
        )
        parts.append(f"[COMMITMENTS]\n{clist}")

    return "\n\n".join(parts)
