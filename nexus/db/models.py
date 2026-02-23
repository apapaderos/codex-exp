"""
SQLAlchemy ORM models for NEXUS.
All tables prefixed with `nx_` to avoid collisions in shared schemas.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ── Enums ────────────────────────────────────────────────────────────────────

class Phase(str, PyEnum):
    REFRAME = "REFRAME"
    EXPLORE = "EXPLORE"
    SHAPE = "SHAPE"


class PhaseOutcome(str, PyEnum):
    REFRAME = "REFRAME"       # problem was wrong
    SHARPEN = "SHARPEN"       # problem was incomplete
    VALIDATE = "VALIDATE"     # problem was right


class SessionStatus(str, PyEnum):
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class EnergyLevel(str, PyEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class KeyMomentType(str, PyEnum):
    REFRAME = "REFRAME"
    ASSUMPTION_CHALLENGED = "ASSUMPTION_CHALLENGED"
    ENERGY_SHIFT = "ENERGY_SHIFT"
    COMMITMENT = "COMMITMENT"
    FACILITATOR_FLAG = "FACILITATOR_FLAG"


class AssumptionStatus(str, PyEnum):
    SURFACED = "SURFACED"
    CHALLENGED = "CHALLENGED"
    EVIDENCED = "EVIDENCED"
    REFUTED = "REFUTED"


class TensionStatus(str, PyEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DEFERRED = "DEFERRED"


# ── Base ─────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Session ──────────────────────────────────────────────────────────────────

class Session(Base):
    __tablename__ = "nx_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_name: Mapped[str] = mapped_column(String(255))          # NOT anonymized at rest; anonymized on export
    facilitator_id: Mapped[str] = mapped_column(String(255))       # Azure AD object ID
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), default=SessionStatus.SCHEDULED)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    state: Mapped[SessionState | None] = relationship("SessionState", back_populates="session", uselist=False, cascade="all, delete-orphan")
    transcript_segments: Mapped[list[TranscriptSegment]] = relationship("TranscriptSegment", back_populates="session", cascade="all, delete-orphan")
    assumptions: Mapped[list[Assumption]] = relationship("Assumption", back_populates="session", cascade="all, delete-orphan")
    tensions: Mapped[list[Tension]] = relationship("Tension", back_populates="session", cascade="all, delete-orphan")
    directions: Mapped[list[Direction]] = relationship("Direction", back_populates="session", cascade="all, delete-orphan")
    commitments: Mapped[list[Commitment]] = relationship("Commitment", back_populates="session", cascade="all, delete-orphan")
    key_moments: Mapped[list[KeyMoment]] = relationship("KeyMoment", back_populates="session", cascade="all, delete-orphan")
    contributions: Mapped[list[ContributionLog]] = relationship("ContributionLog", back_populates="session", cascade="all, delete-orphan")


# ── Session State (live, mutable) ────────────────────────────────────────────

class SessionState(Base):
    """
    The session state model described in the system prompt.
    One row per session; updated in-place as the session progresses.
    """
    __tablename__ = "nx_session_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nx_sessions.id"), unique=True)

    current_phase: Mapped[Phase] = mapped_column(Enum(Phase), default=Phase.REFRAME)
    current_activity: Mapped[int] = mapped_column(Integer, default=1)   # 1-4
    current_x: Mapped[str | None] = mapped_column(Text)                 # the problem statement
    energy_level: Mapped[EnergyLevel] = mapped_column(Enum(EnergyLevel), default=EnergyLevel.MEDIUM)
    phase_outcome: Mapped[PhaseOutcome | None] = mapped_column(Enum(PhaseOutcome))

    # Rolling context: compressed summary of past phases (not full transcript)
    context_summary: Mapped[dict | None] = mapped_column(JSONB)

    # Token count of current context window (used by context manager)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    session: Mapped[Session] = relationship("Session", back_populates="state")


# ── Transcript Segments ───────────────────────────────────────────────────────

class TranscriptSegment(Base):
    """Rolling transcript — older segments are compressed, not deleted."""
    __tablename__ = "nx_transcript_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nx_sessions.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    is_compressed: Mapped[bool] = mapped_column(Boolean, default=False)
    phase: Mapped[Phase] = mapped_column(Enum(Phase))
    activity: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship("Session", back_populates="transcript_segments")


# ── Assumptions ───────────────────────────────────────────────────────────────

class Assumption(Base):
    __tablename__ = "nx_assumptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nx_sessions.id"))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[AssumptionStatus] = mapped_column(Enum(AssumptionStatus), default=AssumptionStatus.SURFACED)
    evidence: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[Phase] = mapped_column(Enum(Phase))
    activity: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship("Session", back_populates="assumptions")


# ── Tensions ──────────────────────────────────────────────────────────────────

class Tension(Base):
    __tablename__ = "nx_tensions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nx_sessions.id"))
    description: Mapped[str] = mapped_column(Text)
    stakeholders: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[TensionStatus] = mapped_column(Enum(TensionStatus), default=TensionStatus.OPEN)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[Phase] = mapped_column(Enum(Phase))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship("Session", back_populates="tensions")


# ── Directions ────────────────────────────────────────────────────────────────

class Direction(Base):
    __tablename__ = "nx_directions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nx_sessions.id"))
    description: Mapped[str] = mapped_column(Text)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    stress_test_results: Mapped[dict | None] = mapped_column(JSONB)   # failure modes, risks, deps
    scenario_results: Mapped[dict | None] = mapped_column(JSONB)      # best/worst/likely at 12/24m
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship("Session", back_populates="directions")


# ── Commitments ───────────────────────────────────────────────────────────────

class Commitment(Base):
    __tablename__ = "nx_commitments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nx_sessions.id"))
    owner: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(Text)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verbatim: Mapped[str | None] = mapped_column(Text)              # exact words spoken
    follow_through_30d: Mapped[bool | None] = mapped_column(Boolean)
    follow_through_90d: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship("Session", back_populates="commitments")


# ── Key Moments ───────────────────────────────────────────────────────────────

class KeyMoment(Base):
    __tablename__ = "nx_key_moments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nx_sessions.id"))
    moment_type: Mapped[KeyMomentType] = mapped_column(Enum(KeyMomentType))
    description: Mapped[str] = mapped_column(Text)
    phase: Mapped[Phase] = mapped_column(Enum(Phase))
    activity: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship("Session", back_populates="key_moments")


# ── Contribution Log ──────────────────────────────────────────────────────────

class ContributionLog(Base):
    """Audit log of every NEXUS contribution during a session."""
    __tablename__ = "nx_contributions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nx_sessions.id"))
    command: Mapped[str] = mapped_column(String(50))        # e.g. "what do we know"
    input_context: Mapped[str | None] = mapped_column(Text) # snapshot of state at trigger time
    output: Mapped[str] = mapped_column(Text)               # NEXUS response (markdown bullets)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    facilitator_override: Mapped[str | None] = mapped_column(Text)  # if facilitator corrected NEXUS
    phase: Mapped[Phase] = mapped_column(Enum(Phase))
    activity: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship("Session", back_populates="contributions")


# ── Cross-session Knowledge Base ──────────────────────────────────────────────

class KnowledgeEntry(Base):
    """
    Anonymized patterns from past sessions.
    Embeddings enable semantic search for 'pattern' and 'provoke' commands.
    """
    __tablename__ = "nx_knowledge"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Taxonomy
    industry: Mapped[str] = mapped_column(String(100))
    problem_type: Mapped[str] = mapped_column(String(100))
    problem_subtype: Mapped[str | None] = mapped_column(String(100))

    # The pattern (anonymized)
    summary: Mapped[str] = mapped_column(Text)
    outcome: Mapped[PhaseOutcome] = mapped_column(Enum(PhaseOutcome))
    key_assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    danger_assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list)  # ones that were wrong
    cross_industry_analogies: Mapped[list[str]] = mapped_column(JSONB, default=list)

    # Metrics
    commitment_follow_through_rate: Mapped[float | None] = mapped_column(Float)
    reframe_depth_score: Mapped[float | None] = mapped_column(Float)

    # Vector embedding (text-embedding-3-large = 3072 dims; use 1536 for ada-002)
    embedding: Mapped[Vector | None] = mapped_column(Vector(1536))

    source_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # NOT exposed via API
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
