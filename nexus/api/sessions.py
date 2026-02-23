"""
Session management endpoints.

POST   /sessions              — create a new session
GET    /sessions/{id}         — get session info
POST   /sessions/{id}/start   — start the session (sets status=ACTIVE, inits state)
POST   /sessions/{id}/end     — end the session (persists state, triggers synthesis queue)
POST   /sessions/{id}/advance — advance to next activity (with summary)
POST   /sessions/{id}/outcome — set REFRAME phase outcome
POST   /sessions/{id}/energy  — update energy level
POST   /sessions/{id}/assumptions   — add an assumption
POST   /sessions/{id}/tensions      — add a tension
POST   /sessions/{id}/directions    — add a direction
POST   /sessions/{id}/commitments   — add a commitment
GET    /sessions/{id}/state         — get full live session state
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.auth import CurrentUser, require_facilitator
from nexus.db.database import get_db
from nexus.db.models import Session as SessionModel, SessionStatus
from nexus.schemas.session import (
    AddAssumptionRequest,
    AddCommitmentRequest,
    AddDirectionRequest,
    AddTensionRequest,
    AdvanceActivityRequest,
    SessionCreate,
    SessionResponse,
    SetEnergyRequest,
    SetOutcomeRequest,
)
from nexus.services import state_manager

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    session = SessionModel(
        client_name=body.client_name,
        facilitator_id=user.oid,
        status=SessionStatus.SCHEDULED,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: UUID,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    return await _get_or_404(session_id, db)


@router.post("/{session_id}/start", response_model=SessionResponse)
async def start_session(
    session_id: UUID,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_or_404(session_id, db)
    if session.status != SessionStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="Session already started or completed")

    session.status = SessionStatus.ACTIVE
    session.started_at = datetime.utcnow()
    await db.flush()

    # Initialise in-memory state
    state_manager.init_state(session_id)
    await db.refresh(session)
    return session


@router.post("/{session_id}/end", response_model=SessionResponse)
async def end_session(
    session_id: UUID,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_or_404(session_id, db)
    if session.status != SessionStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Session is not active")

    # Persist final state before teardown
    if state_manager.get_state(session_id):
        await state_manager.persist_state(session_id, db)

    session.status = SessionStatus.COMPLETED
    session.ended_at = datetime.utcnow()
    await db.flush()

    # State stays in memory briefly so synthesis can access it; torn down by synthesis router
    await db.refresh(session)
    return session


# ── State mutations ───────────────────────────────────────────────────────────

@router.post("/{session_id}/advance")
async def advance_activity(
    session_id: UUID,
    body: AdvanceActivityRequest,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    _require_active(session_id)
    state = state_manager.advance_activity(session_id, body.context_summary)
    await state_manager.persist_state(session_id, db)
    return {"phase": state.current_phase, "activity": state.current_activity}


@router.post("/{session_id}/outcome")
async def set_outcome(
    session_id: UUID,
    body: SetOutcomeRequest,
    user: CurrentUser = Depends(require_facilitator),
):
    _require_active(session_id)
    state_manager.set_phase_outcome(session_id, body.outcome)
    return {"outcome": body.outcome}


@router.post("/{session_id}/energy")
async def set_energy(
    session_id: UUID,
    body: SetEnergyRequest,
    user: CurrentUser = Depends(require_facilitator),
):
    _require_active(session_id)
    state_manager.set_energy(session_id, body.level)
    return {"energy_level": body.level}


@router.post("/{session_id}/assumptions")
async def add_assumption(
    session_id: UUID,
    body: AddAssumptionRequest,
    user: CurrentUser = Depends(require_facilitator),
):
    _require_active(session_id)
    assumption = state_manager.add_assumption(session_id, body.text)
    return assumption.model_dump(mode="json")


@router.post("/{session_id}/tensions")
async def add_tension(
    session_id: UUID,
    body: AddTensionRequest,
    user: CurrentUser = Depends(require_facilitator),
):
    _require_active(session_id)
    tension = state_manager.add_tension(session_id, body.description, body.stakeholders)
    return tension.model_dump(mode="json")


@router.post("/{session_id}/directions")
async def add_direction(
    session_id: UUID,
    body: AddDirectionRequest,
    user: CurrentUser = Depends(require_facilitator),
):
    _require_active(session_id)
    direction = state_manager.add_direction(session_id, body.description)
    return direction.model_dump(mode="json")


@router.post("/{session_id}/commitments")
async def add_commitment(
    session_id: UUID,
    body: AddCommitmentRequest,
    user: CurrentUser = Depends(require_facilitator),
):
    _require_active(session_id)
    commitment = state_manager.add_commitment(
        session_id, body.owner, body.action, body.deadline, body.verbatim
    )
    return commitment.model_dump(mode="json")


@router.get("/{session_id}/state")
async def get_state(
    session_id: UUID,
    user: CurrentUser = Depends(require_facilitator),
):
    state = state_manager.get_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No active state for this session")
    return state.model_dump(mode="json")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_404(session_id: UUID, db: AsyncSession) -> SessionModel:
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _require_active(session_id: UUID) -> None:
    if state_manager.get_state(session_id) is None:
        raise HTTPException(status_code=400, detail="Session not active or state not initialised")
