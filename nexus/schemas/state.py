"""
In-memory session state model.

This is the ground truth during a live session. It lives in Redis (or
in-process dict for development) and is persisted to PostgreSQL periodically
and on phase transitions.

Keeping it as a Pydantic model means it serialises cleanly to JSON for Redis
storage and WebSocket broadcasts.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from nexus.db.models import (
    AssumptionStatus,
    EnergyLevel,
    KeyMomentType,
    Phase,
    PhaseOutcome,
    TensionStatus,
)


class AssumptionState(BaseModel):
    id: UUID
    text: str
    status: AssumptionStatus = AssumptionStatus.SURFACED
    evidence: str | None = None
    phase: Phase
    activity: int


class TensionState(BaseModel):
    id: UUID
    description: str
    stakeholders: list[str] = Field(default_factory=list)
    status: TensionStatus = TensionStatus.OPEN
    resolution_note: str | None = None
    phase: Phase


class DirectionState(BaseModel):
    id: UUID
    description: str
    is_selected: bool = False
    stress_test_results: dict | None = None
    scenario_results: dict | None = None


class CommitmentState(BaseModel):
    id: UUID
    owner: str
    action: str
    deadline: datetime | None = None
    verbatim: str | None = None


class KeyMomentState(BaseModel):
    id: UUID
    moment_type: KeyMomentType
    description: str
    phase: Phase
    activity: int
    occurred_at: datetime


class LiveSessionState(BaseModel):
    """
    The complete live state of a NEXUS session.
    Matches the session state model described in the system prompt.
    """
    session_id: UUID
    current_phase: Phase = Phase.REFRAME
    current_activity: int = 1          # 1-4
    current_x: str | None = None      # the problem being solved
    phase_outcome: PhaseOutcome | None = None

    assumptions: list[AssumptionState] = Field(default_factory=list)
    tensions: list[TensionState] = Field(default_factory=list)
    directions: list[DirectionState] = Field(default_factory=list)
    commitments: list[CommitmentState] = Field(default_factory=list)
    energy_level: EnergyLevel = EnergyLevel.MEDIUM
    key_moments: list[KeyMomentState] = Field(default_factory=list)

    # Rolling context summaries (keyed by "PHASE:activity")
    context_summary: dict[str, str] = Field(default_factory=dict)

    # Token tracking
    estimated_tokens: int = 0

    # Recent transcript (current activity only — not full session)
    current_activity_transcript: list[str] = Field(default_factory=list)

    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True
