"""
MODE 4: LEARN — continuous background integration.

After each session, integrates the structured data export into the
cross-session knowledge base:
  - Classifies problem by industry, type, subtype
  - Updates pattern frequencies
  - Updates assumption danger scores
  - Stores vector embedding for future semantic search
  - Flags anomalies for Director review

Called by the synthesis API after a session is closed and the structured
export has been generated.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.db.models import KnowledgeEntry, PhaseOutcome
from nexus.services.llm import embed

log = structlog.get_logger(__name__)


async def integrate_session(
    session_id: UUID,
    export: dict,
    db: AsyncSession,
) -> KnowledgeEntry:
    """
    Main entry point. Takes the structured export from MODE 3 SYNTHESIZE
    and writes a KnowledgeEntry to the DB.

    The source_session_id is stored but NEVER exposed via any API endpoint.
    """
    taxonomy = export.get("problem_taxonomy", {})
    reframe_outcome_str = export.get("reframe_outcome", "VALIDATE")
    anonymized_summary = export.get("anonymized_summary", "")

    # Build the text we'll embed for semantic search
    embed_text = _build_embed_text(export)
    embedding = await embed(embed_text)

    # Extract danger assumptions (ones that were REFUTED or were dangerous)
    danger_assumptions = [
        a["text"] for a in export.get("assumptions", [])
        if a.get("was_dangerous") or a.get("status") == "REFUTED"
    ]

    # Extract cross-industry analogies
    analogies = export.get("cross_industry_analogies", [])

    # Methodology effectiveness scores
    effectiveness = export.get("methodology_effectiveness", {})

    try:
        outcome = PhaseOutcome(reframe_outcome_str)
    except ValueError:
        outcome = PhaseOutcome.VALIDATE

    entry = KnowledgeEntry(
        id=uuid4(),
        industry=taxonomy.get("industry", "Unknown"),
        problem_type=taxonomy.get("problem_type", "Unknown"),
        problem_subtype=taxonomy.get("problem_subtype"),
        summary=anonymized_summary,
        outcome=outcome,
        key_assumptions=[a["text"] for a in export.get("assumptions", [])[:10]],
        danger_assumptions=danger_assumptions,
        cross_industry_analogies=analogies,
        commitment_follow_through_rate=None,   # populated later via 30/90-day check-ins
        reframe_depth_score=effectiveness.get("reframe_depth_score"),
        embedding=embedding,
        source_session_id=session_id,
    )

    db.add(entry)
    await db.flush()

    log.info(
        "knowledge_entry_created",
        entry_id=str(entry.id),
        industry=entry.industry,
        problem_type=entry.problem_type,
        outcome=entry.outcome,
    )

    # Anomaly detection
    await _flag_anomalies(entry, db)

    return entry


async def update_follow_through(
    session_id: UUID,
    period: str,  # "30d" or "90d"
    rate: float,
    db: AsyncSession,
) -> None:
    """
    Update commitment follow-through rate from check-in data.
    Called externally (e.g., a 30-day follow-up webhook).
    """
    from sqlalchemy import select
    result = await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.source_session_id == session_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        log.warning("knowledge_entry_not_found_for_followthrough", session_id=str(session_id))
        return

    if period == "30d":
        entry.commitment_follow_through_rate = rate
    elif period == "90d":
        # Average if 30d rate already set
        if entry.commitment_follow_through_rate is not None:
            entry.commitment_follow_through_rate = (entry.commitment_follow_through_rate + rate) / 2
        else:
            entry.commitment_follow_through_rate = rate

    await db.flush()
    log.info("follow_through_updated", session_id=str(session_id), period=period, rate=rate)


async def _flag_anomalies(entry: KnowledgeEntry, db: AsyncSession) -> None:
    """
    Simple anomaly checks. In production, compare against historical distributions.
    For now, flag based on threshold rules.
    """
    anomalies: list[str] = []

    if entry.reframe_depth_score is not None and entry.reframe_depth_score < 2.0:
        anomalies.append(f"Very low reframe depth score: {entry.reframe_depth_score}")

    if len(entry.danger_assumptions) > 5:
        anomalies.append(f"High number of danger assumptions: {len(entry.danger_assumptions)}")

    if anomalies:
        log.warning(
            "knowledge_anomaly_flagged",
            entry_id=str(entry.id),
            anomalies=anomalies,
        )
        # In production: write to nx_anomalies table or send alert to Director dashboard


def _build_embed_text(export: dict) -> str:
    """Build the text string we'll embed for semantic similarity search."""
    taxonomy = export.get("problem_taxonomy", {})
    parts = [
        f"Industry: {taxonomy.get('industry', '')}",
        f"Problem type: {taxonomy.get('problem_type', '')}",
        f"Problem subtype: {taxonomy.get('problem_subtype', '')}",
        f"Summary: {export.get('anonymized_summary', '')}",
        f"Outcome: {export.get('reframe_outcome', '')}",
    ]
    assumptions = [a["text"] for a in export.get("assumptions", [])[:5]]
    if assumptions:
        parts.append("Key assumptions: " + "; ".join(assumptions))
    analogies = export.get("cross_industry_analogies", [])
    if analogies:
        parts.append("Analogies: " + "; ".join(analogies[:3]))
    return "\n".join(parts)
