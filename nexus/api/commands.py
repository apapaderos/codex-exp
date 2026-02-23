"""
Facilitator command endpoints (MODE 2: CONTRIBUTE).

POST /sessions/{id}/command   — execute a NEXUS command
POST /sessions/{id}/override  — record a facilitator correction to a NEXUS contribution
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.auth import CurrentUser, require_facilitator
from nexus.db.database import get_db
from nexus.modes.contribute import execute, record_facilitator_override
from nexus.schemas.session import CommandRequest, OverrideRequest

router = APIRouter(prefix="/sessions", tags=["commands"])


@router.post("/{session_id}/command")
async def run_command(
    session_id: UUID,
    body: CommandRequest,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    result = await execute(
        session_id=session_id,
        command=body.command,
        db=db,
        facilitator_note=body.note,
    )
    return {
        "command": result.command,
        "output": result.output,
        "latency_ms": result.latency_ms,
    }


@router.post("/{session_id}/override")
async def override_contribution(
    session_id: UUID,
    body: OverrideRequest,
    user: CurrentUser = Depends(require_facilitator),
    db: AsyncSession = Depends(get_db),
):
    await record_facilitator_override(
        session_id=session_id,
        contribution_id=body.contribution_id,
        override_text=body.override_text,
        db=db,
    )
    return {"status": "recorded"}
