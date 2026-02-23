"""
MODE 2: CONTRIBUTE — triggered by facilitator commands.

Implements all 8 commands from the system prompt:
  what do we know | challenge this | provoke | scenario |
  stress test | pattern | capture | synthesize

Each command returns a ContributionResult within the 8-second SLA.
"""
from __future__ import annotations

import time
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.db.models import ContributionLog, KeyMomentType, Phase
from nexus.schemas.state import LiveSessionState
from nexus.services import state_manager
from nexus.services.context import build_context
from nexus.services.llm import chat, embed

log = structlog.get_logger(__name__)

VALID_COMMANDS = frozenset({
    "what do we know",
    "challenge this",
    "provoke",
    "scenario",
    "stress test",
    "pattern",
    "capture",
    "synthesize",
})


class ContributionResult:
    def __init__(self, command: str, output: str, latency_ms: int):
        self.command = command
        self.output = output
        self.latency_ms = latency_ms


# ── Command prompts ───────────────────────────────────────────────────────────

_PROMPTS: dict[str, str] = {
    "what do we know": """\
Based on the session state below, summarise where we are right now.
3-5 bullets. Each bullet = one concrete insight, tension, assumption, or decision.
No filler. Start each bullet with a strong verb or noun phrase.

{context}
""",

    "challenge this": """\
The room has converged on a dominant position. Surface 2-3 counter-arguments.
Be specific. Cite a structural weakness, a hidden assumption, or disconfirming evidence.
Format: bullet per counter-argument. Include a one-line source note (data, analogy, or logical inference).

{context}
""",

    "provoke": """\
Surface 2-3 cross-industry cases with structural similarity to the problem below.
Goal: break the client's frame of reference. Choose cases from different industries.
Format per case: [Industry] — [What they did] — [Why it's relevant here].
Max 2 lines per case. Cite the source (company, published research, or sector trend).

{context}
""",

    "scenario": """\
Model outcomes for the selected direction at 12 months and 24 months.
Return exactly three scenarios: BEST, WORST, LIKELY.
Format per scenario:
  [SCENARIO TYPE] 12M: ... / 24M: ...
  Key assumptions that must hold: ...

{context}
""",

    "stress test": """\
List the top 5 failure modes for the selected direction.
Format per item:
  [#] Failure mode: ...
      Dependency at risk: ...
      Severity: HIGH/MEDIUM/LOW
      Mitigation: ...

{context}
""",

    "pattern": """\
Search the cross-session knowledge base (anonymised) for patterns similar to this problem.
Data provided below. Surface the most relevant 2-3 analogies.
Format per pattern:
  [Industry / Type]: ...
  Outcome: REFRAME / SHARPEN / VALIDATE
  Key insight: ...
  Watch out for: ...

{context}

Cross-session patterns:
{knowledge_context}
""",

    "synthesize": """\
Generate a real-time synthesis of the session journey so far.
Structure:
  STATED PROBLEM: ...
  JOURNEY: [phase] → [what happened] → [outcome]
  KEY DECISIONS: bullet list
  OPEN QUESTIONS: bullet list
  ENERGY ARC: describe the energy curve through the session

Keep it tight. This is a mid-session snapshot, not the final synthesis document.

{context}
""",
}


# ── Main dispatch ─────────────────────────────────────────────────────────────

async def execute(
    session_id: UUID,
    command: str,
    db: AsyncSession,
    facilitator_note: str | None = None,
) -> ContributionResult:
    """
    Execute a facilitator command and return the result.
    Logs the contribution to the DB.
    """
    command = command.strip().lower()
    if command not in VALID_COMMANDS:
        return ContributionResult(command=command, output=f"Unknown command: '{command}'", latency_ms=0)

    state = state_manager.get_state(session_id)
    if state is None:
        return ContributionResult(command=command, output="No active session found.", latency_ms=0)

    context = build_context(session_id)
    start = time.monotonic()

    if command == "capture":
        output = await _handle_capture(session_id, facilitator_note or "Facilitator flagged this moment.")
    elif command == "pattern":
        knowledge_context = await _fetch_knowledge_context(context, db)
        prompt = _PROMPTS["pattern"].format(context=context, knowledge_context=knowledge_context)
        output = await chat(prompt, temperature=0.3)
    else:
        prompt = _PROMPTS[command].format(context=context)
        output = await chat(prompt, temperature=0.3)

    latency_ms = int((time.monotonic() - start) * 1000)

    # Log to DB
    log_entry = ContributionLog(
        session_id=session_id,
        command=command,
        input_context=context[:2000],   # truncate for storage
        output=output,
        latency_ms=latency_ms,
        phase=state.current_phase,
        activity=state.current_activity,
    )
    db.add(log_entry)
    await db.flush()

    log.info("contribution_executed", command=command, latency_ms=latency_ms, session_id=str(session_id))
    return ContributionResult(command=command, output=output, latency_ms=latency_ms)


async def record_facilitator_override(
    session_id: UUID,
    contribution_id: UUID,
    override_text: str,
    db: AsyncSession,
) -> None:
    from sqlalchemy import select
    result = await db.execute(
        select(ContributionLog).where(ContributionLog.id == contribution_id)
    )
    entry = result.scalar_one_or_none()
    if entry:
        entry.facilitator_override = override_text
        await db.flush()


# ── Capture helper ────────────────────────────────────────────────────────────

async def _handle_capture(session_id: UUID, description: str) -> str:
    state_manager.flag_key_moment(session_id, KeyMomentType.FACILITATOR_FLAG, description)
    return f"Captured: {description}"


# ── Knowledge base search ─────────────────────────────────────────────────────

async def _fetch_knowledge_context(context: str, db: AsyncSession) -> str:
    """Semantic search against the knowledge base. Returns anonymised patterns."""
    from sqlalchemy import text

    try:
        query_embedding = await embed(context[:2000])
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        result = await db.execute(
            text("""
                SELECT industry, problem_type, summary, outcome,
                       cross_industry_analogies, danger_assumptions
                FROM nx_knowledge
                ORDER BY embedding <-> :embedding::vector
                LIMIT 3
            """),
            {"embedding": embedding_str},
        )
        rows = result.fetchall()

        if not rows:
            return "No relevant patterns found in cross-session knowledge base."

        parts = []
        for row in rows:
            analogies = ", ".join(row.cross_industry_analogies[:2]) if row.cross_industry_analogies else "none"
            parts.append(
                f"Industry: {row.industry} / Type: {row.problem_type}\n"
                f"  Outcome: {row.outcome}\n"
                f"  Summary: {row.summary}\n"
                f"  Analogies: {analogies}\n"
                f"  Danger assumptions: {', '.join(row.danger_assumptions[:3]) if row.danger_assumptions else 'none'}"
            )
        return "\n\n".join(parts)

    except Exception as exc:
        log.warning("knowledge_search_failed", error=str(exc))
        return "Knowledge base search unavailable."
