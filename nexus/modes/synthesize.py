"""
MODE 3: SYNTHESIZE — post-session document generation.

Generates three artefacts after a session ends:
1. SYNTHESIS DOCUMENT — client-facing (delivered within 48 hours)
2. FACILITATOR DEBRIEF — internal methodology performance review
3. STRUCTURED DATA EXPORT — feeds MODE 4 LEARN

All three are generated as markdown strings. The caller (API layer) is
responsible for formatting/delivering them (PDF, email, etc.).
"""
from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from nexus.db.models import Session as SessionModel
from nexus.services import state_manager
from nexus.services.context import build_context
from nexus.services.llm import chat, chat_stream

log = structlog.get_logger(__name__)


# ── Prompt templates ──────────────────────────────────────────────────────────

_SYNTHESIS_DOC_PROMPT = """\
You are NEXUS generating the post-session Synthesis Document for a PwC client.
This is a professional deliverable. Write for C-suite executives.
Short sentences. Clear structure. No filler. No methodology jargon.
Use the client's language, not ours.

Session data:
{context}

Generate the Synthesis Document with these exact sections:

# Executive Summary
[1 paragraph — the journey from stated problem to outcome]

# The Reframe
- Stated problem: ...
- Tested X: ...
- Pathway: REFRAME / SHARPEN / VALIDATE
- Reasoning: [2-3 sentences]

# Key Assumptions
[Table: Assumption | Status | Evidence]

# Stakeholder Tension Map
[Bullet per tension: description — stakeholders involved — resolution status]

# Directions Explored
[Per direction: name, one-line description, key stress test findings]

# Chosen Direction
- Direction: ...
- Rationale: [3-4 sentences]
- Prototype described: ...

# Roadmap
[Table: Action | Owner | Deadline | Milestone]

# Personal Commitments
[Verbatim statements from The Round, attributed to role only — not name]

# Recommended Next Steps
[3-5 bullets with suggested PwC service line connections where relevant]
"""

_FACILITATOR_DEBRIEF_PROMPT = """\
You are NEXUS generating the internal Facilitator Debrief for a Solve for X session.
This is for the facilitator's eyes only. Be honest, specific, and direct.

Session data:
{context}

Generate the Facilitator Debrief with these sections:

# Session Effectiveness Metrics
- Reframe depth: [score 1-5 with reasoning]
- Assumption quality: [score 1-5 with reasoning]
- Commitment specificity: [score 1-5 with reasoning]
- Energy arc: [describe LOW/MEDIUM/HIGH curve through session]

# What Worked
[2-4 bullets — specific moments or activities that generated real value]

# What Didn't Work
[2-4 bullets — specific moments or activities that underperformed]

# Suggestions for Follow-Up
[2-3 concrete recommendations for the next engagement with this client]

# NEXUS Performance
- Contributions made: [number]
- Facilitator overrides: [number]
- Notable errors or corrections: [list any]
"""

_STRUCTURED_EXPORT_PROMPT = """\
You are NEXUS generating a structured data export for the cross-session learning engine.
Return ONLY valid JSON. No markdown, no explanation.

Session data:
{context}

Generate JSON with this structure:
{{
  "problem_taxonomy": {{
    "industry": "...",
    "problem_type": "...",
    "problem_subtype": "..."
  }},
  "reframe_outcome": "REFRAME|SHARPEN|VALIDATE",
  "anonymized_summary": "...",
  "assumptions": [
    {{"text": "...", "status": "SURFACED|CHALLENGED|EVIDENCED|REFUTED", "was_dangerous": true|false}}
  ],
  "tensions": [
    {{"description": "...", "stakeholders_count": 2, "resolved": true|false}}
  ],
  "direction_chosen": {{
    "description": "...",
    "top_failure_modes": ["...", "..."]
  }},
  "cross_industry_analogies": ["...", "..."],
  "commitment_count": 0,
  "methodology_effectiveness": {{
    "reframe_depth_score": 3.5,
    "assumption_quality_score": 3.0,
    "commitment_specificity_score": 4.0
  }}
}}
"""


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_synthesis_document(session_id: UUID, db: AsyncSession) -> str:
    """Client-facing synthesis document (markdown)."""
    context = _build_full_context(session_id)
    log.info("generating_synthesis_document", session_id=str(session_id))
    return await chat(
        _SYNTHESIS_DOC_PROMPT.format(context=context),
        temperature=0.3,
        timeout=60.0,
    )


async def generate_synthesis_document_stream(session_id: UUID, db: AsyncSession):
    """Streaming variant for large sessions."""
    context = _build_full_context(session_id)
    async for chunk in chat_stream(
        _SYNTHESIS_DOC_PROMPT.format(context=context),
        temperature=0.3,
    ):
        yield chunk


async def generate_facilitator_debrief(session_id: UUID, db: AsyncSession) -> str:
    """Internal facilitator debrief (markdown)."""
    context = _build_full_context(session_id)
    log.info("generating_facilitator_debrief", session_id=str(session_id))

    # Add contribution stats to context
    from sqlalchemy import func
    from nexus.db.models import ContributionLog
    stats = await db.execute(
        select(
            func.count(ContributionLog.id).label("total"),
            func.count(ContributionLog.facilitator_override).label("overrides"),
        ).where(ContributionLog.session_id == session_id)
    )
    row = stats.one()
    stats_context = f"\nNEXUS contribution count: {row.total}\nFacilitator overrides: {row.overrides}\n"

    return await chat(
        _FACILITATOR_DEBRIEF_PROMPT.format(context=context + stats_context),
        temperature=0.3,
        timeout=60.0,
    )


async def generate_structured_export(session_id: UUID, db: AsyncSession) -> dict:
    """Structured JSON export for MODE 4 LEARN."""
    import json, re
    context = _build_full_context(session_id)
    log.info("generating_structured_export", session_id=str(session_id))
    raw = await chat(
        _STRUCTURED_EXPORT_PROMPT.format(context=context),
        temperature=0.1,
        timeout=60.0,
    )
    # Strip markdown fences if present
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    return json.loads(raw)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_full_context(session_id: UUID) -> str:
    """
    For post-session synthesis we include the full state model with all summaries.
    If the session state has been torn down (after session end), pull from DB instead.
    """
    state = state_manager.get_state(session_id)
    if state:
        return build_context(session_id, include_full_transcript=True)
    # State already torn down — indicate this to caller; DB pull handled at API layer
    return f"[Session {session_id} state not in memory — context built from DB summaries]"
