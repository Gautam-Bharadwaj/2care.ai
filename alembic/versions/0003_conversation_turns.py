"""traces: conversation_turns table for reasoning-trace JSONB storage

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-23

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False, index=True),
        sa.Column("turn_idx", sa.Integer, nullable=False, index=True),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patients.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("trace", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", "turn_idx", name="uq_session_turn"),
    )


def downgrade() -> None:
    op.drop_table("conversation_turns")
