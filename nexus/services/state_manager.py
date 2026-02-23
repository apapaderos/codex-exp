"""
Session state manager.

Owns the in-memory LiveSessionState for each active session.
Handles reads, updates, phase transitions, and periodic persistence to DB.

In production, swap the in-process dict for Redis using model_dump() / model_validate().
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.db.models import (
    Assumption,
    Commitment,
    Direction,
    EnergyLevel,
    KeyMoment,
    KeyMomentType,
    Phase,
    PhaseOutcome,
    SessionState,
    Tension,
    AssumptionStatus,
)
from nexus.schemas.state import (
    AssumptionState,
    CommitmentState,
    DirectionState,
    KeyMomentState,
    LiveSessionState,
    TensionState,
)

log = structlog.get_logger(__name__)

# ── In-process store (replace with Redis in production) ───────────────────────
_sessions: dict[UUID, LiveSessionState] = {}


def get_state(session_id: UUID) -> LiveSessionState | None:
    return _sessions.get(session_id)


def require_state(session_id: UUID) -> LiveSessionState:
    state = _sessions.get(session_id)
    if state is None:
        raise KeyError(f"No active state for session {session_id}")
    return state


def init_state(session_id: UUID, current_x: str | None = None) -> LiveSessionState:
    state = LiveSessionState(session_id=session_id, current_x=current_x)
    _sessions[session_id] = state
    log.info("session_state_initialized", session_id=str(session_id))
    return state


def teardown_state(session_id: UUID) -> None:
    _sessions.pop(session_id, None)


# ── Transcript ────────────────────────────────────────────────────────────────

def append_transcript(session_id: UUID, text: str) -> None:
    state = require_state(session_id)
    state.current_activity_transcript.append(text)
    # Rough token estimate: 1 token ≈ 4 chars
    state.estimated_tokens += len(text) // 4
    state.updated_at = datetime.utcnow()


# ── Assumptions ───────────────────────────────────────────────────────────────

def add_assumption(session_id: UUID, text: str) -> AssumptionState:
    state = require_state(session_id)
    assumption = AssumptionState(
        id=uuid4(),
        text=text,
        phase=state.current_phase,
        activity=state.current_activity,
    )
    state.assumptions.append(assumption)
    state.updated_at = datetime.utcnow()
    return assumption


def update_assumption(session_id: UUID, assumption_id: UUID, **kwargs) -> AssumptionState | None:
    state = require_state(session_id)
    for a in state.assumptions:
        if a.id == assumption_id:
            for k, v in kwargs.items():
                setattr(a, k, v)
            state.updated_at = datetime.utcnow()
            return a
    return None


# ── Tensions ──────────────────────────────────────────────────────────────────

def add_tension(session_id: UUID, description: str, stakeholders: list[str]) -> TensionState:
    state = require_state(session_id)
    tension = TensionState(
        id=uuid4(),
        description=description,
        stakeholders=stakeholders,
        phase=state.current_phase,
    )
    state.tensions.append(tension)
    state.updated_at = datetime.utcnow()
    return tension


# ── Directions ────────────────────────────────────────────────────────────────

def add_direction(session_id: UUID, description: str) -> DirectionState:
    state = require_state(session_id)
    direction = DirectionState(id=uuid4(), description=description)
    state.directions.append(direction)
    state.updated_at = datetime.utcnow()
    return direction


def select_direction(session_id: UUID, direction_id: UUID) -> None:
    state = require_state(session_id)
    for d in state.directions:
        d.is_selected = d.id == direction_id
    state.updated_at = datetime.utcnow()


# ── Commitments ───────────────────────────────────────────────────────────────

def add_commitment(
    session_id: UUID, owner: str, action: str, deadline: datetime | None, verbatim: str | None
) -> CommitmentState:
    state = require_state(session_id)
    commitment = CommitmentState(
        id=uuid4(), owner=owner, action=action, deadline=deadline, verbatim=verbatim
    )
    state.commitments.append(commitment)
    state.updated_at = datetime.utcnow()
    return commitment


# ── Key moments ───────────────────────────────────────────────────────────────

def flag_key_moment(
    session_id: UUID, moment_type: KeyMomentType, description: str
) -> KeyMomentState:
    state = require_state(session_id)
    moment = KeyMomentState(
        id=uuid4(),
        moment_type=moment_type,
        description=description,
        phase=state.current_phase,
        activity=state.current_activity,
        occurred_at=datetime.utcnow(),
    )
    state.key_moments.append(moment)
    state.updated_at = datetime.utcnow()
    return moment


# ── Phase / activity transitions ──────────────────────────────────────────────

def advance_activity(session_id: UUID, context_summary: str) -> LiveSessionState:
    """Move to next activity within current phase (or next phase)."""
    state = require_state(session_id)
    key = f"{state.current_phase}:{state.current_activity}"
    state.context_summary[key] = context_summary
    state.current_activity_transcript.clear()

    if state.current_activity < 4:
        state.current_activity += 1
    else:
        # Phase transition
        phases = list(Phase)
        idx = phases.index(Phase(state.current_phase))
        if idx < len(phases) - 1:
            state.current_phase = phases[idx + 1]
            state.current_activity = 1

    state.updated_at = datetime.utcnow()
    log.info("activity_advanced", session_id=str(session_id), phase=state.current_phase, activity=state.current_activity)
    return state


def set_phase_outcome(session_id: UUID, outcome: PhaseOutcome) -> None:
    state = require_state(session_id)
    state.phase_outcome = outcome
    state.updated_at = datetime.utcnow()


def set_energy(session_id: UUID, level: EnergyLevel) -> None:
    state = require_state(session_id)
    state.energy_level = level
    state.updated_at = datetime.utcnow()


# ── Persistence (flush to DB) ─────────────────────────────────────────────────

async def persist_state(session_id: UUID, db: AsyncSession) -> None:
    """Write in-memory state to the DB. Called on phase transitions and session end."""
    from sqlalchemy import select

    state = require_state(session_id)

    result = await db.execute(select(SessionState).where(SessionState.session_id == session_id))
    db_state = result.scalar_one_or_none()
    if db_state is None:
        db_state = SessionState(session_id=session_id)
        db.add(db_state)

    db_state.current_phase = state.current_phase
    db_state.current_activity = state.current_activity
    db_state.current_x = state.current_x
    db_state.energy_level = state.energy_level
    db_state.phase_outcome = state.phase_outcome
    db_state.context_summary = state.context_summary
    db_state.estimated_tokens = state.estimated_tokens

    await db.flush()
    log.info("session_state_persisted", session_id=str(session_id))
