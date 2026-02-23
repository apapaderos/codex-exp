"""
MODE 1: CAPTURE — always active during sessions.

Processes the transcript stream, extracts structured intelligence, and
updates the session state model. This is the continuous background brain.

Design:
- Runs as an async task per session, consuming TranscriptEvents from speech.py.
- Every N utterances (configurable) sends a batch to the LLM for extraction.
- Detected items (assumptions, tensions, etc.) go into state_manager.
- Significant moments are flagged for the facilitator tablet via a broadcast queue.
"""
from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID

import structlog

from nexus.db.models import KeyMomentType
from nexus.services import state_manager
from nexus.services.context import maybe_compress
from nexus.services.llm import chat
from nexus.services.speech import SpeechSession, TranscriptEvent

log = structlog.get_logger(__name__)

# How many final transcript utterances to batch before running extraction
_EXTRACT_BATCH_SIZE = 5

_EXTRACTION_PROMPT = """\
You are the CAPTURE module of NEXUS. Analyse the following transcript utterances from a live \
strategic session and extract structured intelligence.

Current session context:
{context}

New transcript utterances:
{utterances}

Return ONLY valid JSON with this exact structure (no markdown, no explanation):
{{
  "assumptions": [
    {{"text": "...", "status": "SURFACED|CHALLENGED|EVIDENCED|REFUTED"}}
  ],
  "tensions": [
    {{"description": "...", "stakeholders": ["...", "..."]}}
  ],
  "key_moments": [
    {{"type": "REFRAME|ASSUMPTION_CHALLENGED|ENERGY_SHIFT|COMMITMENT|FACILITATOR_FLAG", "description": "..."}}
  ],
  "energy_shift": null,
  "summary": "One sentence describing what happened in this batch."
}}

Rules:
- Only extract what is clearly present. An empty list is fine.
- energy_shift: "LOW", "MEDIUM", or "HIGH" if you detect a significant change, else null.
- assumptions: only substantive claims about the world or the problem that can be tested.
- tensions: only genuine conflicts between stakeholder interests or viewpoints.
"""


async def run_capture_loop(
    session_id: UUID,
    speech_session: SpeechSession,
    broadcast: asyncio.Queue,
) -> None:
    """
    Main capture coroutine. Runs for the lifetime of a session WebSocket.

    broadcast: queue consumed by the WebSocket handler to push events to the tablet.
    """
    log.info("capture_loop_started", session_id=str(session_id))
    batch: list[str] = []
    sequence = 0

    async for event in speech_session.events():
        # Persist interim results for live display only — don't extract from them
        if not event.is_final:
            await broadcast.put({
                "type": "transcript_interim",
                "text": event.text,
                "speaker": event.speaker,
            })
            continue

        # Final utterance
        state_manager.append_transcript(session_id, event.text)
        sequence += 1

        await broadcast.put({
            "type": "transcript_final",
            "text": event.text,
            "speaker": event.speaker,
            "sequence": sequence,
        })

        batch.append(event.text)

        if len(batch) >= _EXTRACT_BATCH_SIZE:
            await _extract_and_update(session_id, batch, broadcast)
            batch.clear()

            # Compression check after each extraction
            await maybe_compress(session_id)

    # Flush remaining
    if batch:
        await _extract_and_update(session_id, batch, broadcast)

    log.info("capture_loop_ended", session_id=str(session_id))


async def _extract_and_update(
    session_id: UUID, utterances: list[str], broadcast: asyncio.Queue
) -> None:
    from nexus.services.context import build_context

    context_str = build_context(session_id, include_full_transcript=False)
    utterance_str = "\n".join(f"  - {u}" for u in utterances)

    try:
        raw = await chat(
            _EXTRACTION_PROMPT.format(context=context_str, utterances=utterance_str),
            temperature=0.2,
            timeout=15.0,
        )
        data = _parse_json(raw)
    except Exception as exc:
        log.warning("capture_extraction_failed", error=str(exc))
        return

    state = state_manager.get_state(session_id)
    if state is None:
        return

    # Assumptions
    for a in data.get("assumptions", []):
        new = state_manager.add_assumption(session_id, a["text"])
        if a.get("status") and a["status"] != "SURFACED":
            state_manager.update_assumption(session_id, new.id, status=a["status"])
        await broadcast.put({"type": "assumption_captured", "assumption": new.model_dump(mode="json")})

    # Tensions
    for t in data.get("tensions", []):
        new = state_manager.add_tension(session_id, t["description"], t.get("stakeholders", []))
        await broadcast.put({"type": "tension_captured", "tension": new.model_dump(mode="json")})

    # Key moments
    for m in data.get("key_moments", []):
        try:
            mtype = KeyMomentType(m["type"])
        except ValueError:
            continue
        new = state_manager.flag_key_moment(session_id, mtype, m["description"])
        await broadcast.put({
            "type": "key_moment_flagged",
            "moment": new.model_dump(mode="json"),
            "alert": True,   # tablet should flash this to facilitator
        })

    # Energy shift
    if data.get("energy_shift"):
        from nexus.db.models import EnergyLevel
        try:
            state_manager.set_energy(session_id, EnergyLevel(data["energy_shift"]))
            await broadcast.put({"type": "energy_updated", "level": data["energy_shift"]})
        except ValueError:
            pass

    log.debug("capture_batch_processed", session_id=str(session_id), items_found=len(data.get("assumptions", [])) + len(data.get("tensions", [])))


def _parse_json(raw: str) -> dict:
    """Extract JSON from LLM output, tolerating markdown fences."""
    raw = raw.strip()
    # Strip markdown fences if present
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    return json.loads(raw)
