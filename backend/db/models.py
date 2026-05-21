import enum
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class AppointmentStatus(str, enum.Enum):
    scheduled = "scheduled"
    completed = "completed"
    cancelled = "cancelled"
    no_show = "no_show"


class MemoryKind(str, enum.Enum):
    preference = "preference"
    history = "history"
    note = "note"


class CampaignKind(str, enum.Enum):
    reminder = "reminder"     # 24h-before nudge
    followup = "followup"     # 48h-after check-in


class CampaignJobStatus(str, enum.Enum):
    pending = "pending"           # scheduled, not yet queued to Celery
    queued = "queued"             # handed to Celery, waiting to dial
    in_progress = "in_progress"   # call placed, agent talking to patient
    completed = "completed"       # call ended with a recorded outcome
    failed = "failed"             # dial failed / agent crashed / Twilio error


class CampaignOutcomeKind(str, enum.Enum):
    confirmed = "confirmed"       # "yes I'll be there"
    rescheduled = "rescheduled"   # picked a new slot mid-call
    cancelled = "cancelled"       # caller cancelled the appointment
    voicemail = "voicemail"       # answering machine detected
    no_answer = "no_answer"       # rang out / busy / no pickup
    rejected = "rejected"         # asked to be removed / hung up immediately
    other = "other"


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    preferred_language: Mapped[str] = mapped_column(String(8), default="en")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")
    memories: Mapped[list["Memory"]] = relationship(back_populates="patient")


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    specialty: Mapped[str] = mapped_column(String(100), index=True)
    languages_spoken: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    slots: Mapped[list["AvailabilitySlot"]] = relationship(back_populates="doctor")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="doctor")


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    doctor_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_booked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    doctor: Mapped[Doctor] = relationship(back_populates="slots")
    appointment: Mapped[Optional["Appointment"]] = relationship(
        back_populates="slot", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("doctor_id", "start_time", name="uq_doctor_start_time"),
    )


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("patients.id"), index=True
    )
    doctor_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("doctors.id"), index=True
    )
    slot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("availability_slots.id"), unique=True
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        SAEnum(AppointmentStatus, name="appointment_status"),
        default=AppointmentStatus.scheduled,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    patient: Mapped[Patient] = relationship(back_populates="appointments")
    doctor: Mapped[Doctor] = relationship(back_populates="appointments")
    slot: Mapped[AvailabilitySlot] = relationship(back_populates="appointment")


class ConversationLog(Base):
    __tablename__ = "conversation_logs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    patient_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("patients.id"), nullable=True, index=True
    )
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    transcript: Mapped[dict] = mapped_column(JSONB, default=dict)
    language: Mapped[str] = mapped_column(String(8), default="en")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CampaignJob(Base):
    """A scheduled outbound campaign call.

    One row per (appointment, campaign kind). Created either by the periodic
    9am IST sweep, by an admin via the `/campaigns/*/schedule` endpoint, or
    on-demand. The Celery worker reads `status='pending'` rows whose
    `scheduled_for` has elapsed, dials, and marks `status='completed'` once
    the agent records a `CampaignOutcome`.
    """

    __tablename__ = "campaign_jobs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    appointment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[CampaignKind] = mapped_column(
        SAEnum(CampaignKind, name="campaign_kind")
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    status: Mapped[CampaignJobStatus] = mapped_column(
        SAEnum(CampaignJobStatus, name="campaign_job_status"),
        default=CampaignJobStatus.pending,
    )
    room_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    outcome: Mapped[Optional["CampaignOutcome"]] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("appointment_id", "kind", name="uq_campaign_appt_kind"),
    )


class CampaignOutcome(Base):
    """The result of one completed `CampaignJob`.

    Written by the agent on call end (or by the worker on dial failure).
    Exactly one row per job — the unique FK enforces this.
    """

    __tablename__ = "campaign_outcomes"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaign_jobs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    outcome: Mapped[CampaignOutcomeKind] = mapped_column(
        SAEnum(CampaignOutcomeKind, name="campaign_outcome_kind")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped[CampaignJob] = relationship(back_populates="outcome")


class ConversationTurn(Base):
    """Per-turn reasoning trace (Phase 8).

    One row per LLM call. The full structured trace lives in `trace` so
    we can render it in the trace viewer without redefining columns
    every time we want to capture a new field. Frequently filtered
    columns (session_id, turn_idx) are first-class for fast queries.

    The shape stored in `trace` mirrors the Pydantic schema in
    `backend/schemas/trace.py::TurnTracePayload` — keep them in sync.
    """

    __tablename__ = "conversation_turns"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_idx: Mapped[int] = mapped_column(index=True)
    patient_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    trace: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("session_id", "turn_idx", name="uq_session_turn"),
    )


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("patients.id"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))
    kind: Mapped[MemoryKind] = mapped_column(SAEnum(MemoryKind, name="memory_kind"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    patient: Mapped[Patient] = relationship(back_populates="memories")
