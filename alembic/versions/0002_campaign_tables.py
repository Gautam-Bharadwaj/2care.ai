"""campaigns: campaign_jobs + campaign_outcomes + supporting enums

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    campaign_kind = postgresql.ENUM(
        "reminder", "followup", name="campaign_kind", create_type=False
    )
    campaign_job_status = postgresql.ENUM(
        "pending", "queued", "in_progress", "completed", "failed",
        name="campaign_job_status",
        create_type=False,
    )
    campaign_outcome_kind = postgresql.ENUM(
        "confirmed", "rescheduled", "cancelled",
        "voicemail", "no_answer", "rejected", "other",
        name="campaign_outcome_kind",
        create_type=False,
    )
    campaign_kind.create(op.get_bind(), checkfirst=True)
    campaign_job_status.create(op.get_bind(), checkfirst=True)
    campaign_outcome_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "campaign_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "appointment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("appointments.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "kind",
            postgresql.ENUM(name="campaign_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="campaign_job_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("room_name", sa.String(128), nullable=True),
        sa.Column("celery_task_id", sa.String(128), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("appointment_id", "kind", name="uq_campaign_appt_kind"),
    )

    op.create_table(
        "campaign_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaign_jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column(
            "outcome",
            postgresql.ENUM(name="campaign_outcome_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("campaign_outcomes")
    op.drop_table("campaign_jobs")
    op.execute("DROP TYPE IF EXISTS campaign_outcome_kind")
    op.execute("DROP TYPE IF EXISTS campaign_job_status")
    op.execute("DROP TYPE IF EXISTS campaign_kind")
