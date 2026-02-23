"""
Post-session synthesis endpoints (MODE 3 + MODE 4).

POST /sessions/{id}/synthesize        — trigger full synthesis (runs all three artefacts)
GET  /sessions/{id}/synthesis/document — stream the synthesis document
GET  /sessions/{id}/synthesis/debrief  — get facilitator debrief
POST /sessions/{id}/followthrough      — update commitment follow-through rate
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.auth import CurrentUser, require_facilitator
from nexus.db.database import get_db
from nexus.modes import learn, synthesize
from nexus.schemas.session import FollowThroughRequest
from nexus.services import state_manager

router = APIRouter(prefix="/sessions", tags=["synthesis"])


@router.post("/{session_id}/synthesize")
async def trigger_synthesis(
    session_id: UUID,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    """
    Triggers the full post-session synthesis pipeline in the background:
      1. Generate structured export
      2. Feed into knowledge base (MODE 4 LEARN)

    The synthesis document and debrief are generated on-demand via the
    GET endpoints below (streaming), so the client controls timing.
    """
    background_tasks.add_task(_run_learn_pipeline, session_id)
    return {"status": "synthesis_started", "session_id": str(session_id)}


@router.get("/{session_id}/synthesis/document")
async def get_synthesis_document(
    session_id: UUID,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    """Stream the client-facing synthesis document."""
    _check_session_ended(session_id)

    async def generate():
        async for chunk in synthesize.generate_synthesis_document_stream(session_id, db):
            yield chunk

    return StreamingResponse(generate(), media_type="text/markdown")


@router.get("/{session_id}/synthesis/debrief")
async def get_facilitator_debrief(
    session_id: UUID,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    """Return the facilitator debrief (non-streaming, internal use)."""
    _check_session_ended(session_id)
    debrief = await synthesize.generate_facilitator_debrief(session_id, db)
    return {"debrief": debrief}


@router.post("/{session_id}/followthrough")
async def update_follow_through(
    session_id: UUID,
    body: FollowThroughRequest,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    if body.period not in ("30d", "90d"):
        raise HTTPException(status_code=400, detail="period must be '30d' or '90d'")
    if not 0.0 <= body.rate <= 1.0:
        raise HTTPException(status_code=400, detail="rate must be between 0.0 and 1.0")

    await learn.update_follow_through(session_id, body.period, body.rate, db)
    return {"status": "updated"}


# ── Background task ───────────────────────────────────────────────────────────

async def _run_learn_pipeline(session_id: UUID) -> None:
    """Runs in the background: generate structured export → integrate into knowledge base."""
    from nexus.db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            export = await synthesize.generate_structured_export(session_id, db)
            await learn.integrate_session(session_id, export, db)
            await db.commit()
        except Exception as exc:
            import structlog
            structlog.get_logger(__name__).error(
                "learn_pipeline_failed", session_id=str(session_id), error=str(exc)
            )
        finally:
            # Tear down in-memory state after synthesis is complete
            state_manager.teardown_state(session_id)


def _check_session_ended(session_id: UUID) -> None:
    """
    For synthesis endpoints, the session can be either still in memory
    (just ended) or already torn down. Both are valid.
    No action needed; the synthesize module handles both cases.
    """
    pass
