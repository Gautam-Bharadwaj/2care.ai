"""initial: pgvector + patients/doctors/slots/appointments/conversation_logs/memories

Revision ID: 0001
Revises:
Create Date: 2026-05-23

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    appointment_status = postgresql.ENUM(
        "scheduled", "completed", "cancelled", "no_show",
        name="appointment_status",
        create_type=False,
    )
    memory_kind = postgresql.ENUM(
        "preference", "history", "note",
        name="memory_kind",
        create_type=False,
    )
    appointment_status.create(op.get_bind(), checkfirst=True)
    memory_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "patients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False, unique=True, index=True),
        sa.Column("preferred_language", sa.String(8), server_default="en", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "doctors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("specialty", sa.String(100), nullable=False, index=True),
        sa.Column(
            "languages_spoken",
            postgresql.ARRAY(sa.String),
            server_default="{}",
            nullable=False,
        ),
    )

    op.create_table(
        "availability_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("doctors.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_booked", sa.Boolean, server_default="false", nullable=False),
        sa.UniqueConstraint("doctor_id", "start_time", name="uq_doctor_start_time"),
    )

    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patients.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("doctors.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("availability_slots.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="appointment_status", create_type=False),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "conversation_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patients.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("session_id", sa.String(64), nullable=False, index=True),
        sa.Column("transcript", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("language", sa.String(8), server_default="en", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patients.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(name="memory_kind", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("memories")
    op.drop_table("conversation_logs")
    op.drop_table("appointments")
    op.drop_table("availability_slots")
    op.drop_table("doctors")
    op.drop_table("patients")
    op.execute("DROP TYPE IF EXISTS memory_kind")
    op.execute("DROP TYPE IF EXISTS appointment_status")
