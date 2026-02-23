from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from nexus.db.models import EnergyLevel, Phase, PhaseOutcome, SessionStatus


class SessionCreate(BaseModel):
    client_name: str
    current_x: str | None = None


class SessionResponse(BaseModel):
    id: UUID
    client_name: str
    facilitator_id: str
    status: SessionStatus
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdvanceActivityRequest(BaseModel):
    context_summary: str   # facilitator-provided summary of what just happened


class SetOutcomeRequest(BaseModel):
    outcome: PhaseOutcome


class SetEnergyRequest(BaseModel):
    level: EnergyLevel


class AddAssumptionRequest(BaseModel):
    text: str


class AddTensionRequest(BaseModel):
    description: str
    stakeholders: list[str] = []


class AddDirectionRequest(BaseModel):
    description: str


class AddCommitmentRequest(BaseModel):
    owner: str
    action: str
    deadline: datetime | None = None
    verbatim: str | None = None


class CommandRequest(BaseModel):
    command: str
    note: str | None = None   # optional facilitator note (used by 'capture' command)


class OverrideRequest(BaseModel):
    contribution_id: UUID
    override_text: str


class FollowThroughRequest(BaseModel):
    period: str   # "30d" or "90d"
    rate: float   # 0.0 - 1.0
