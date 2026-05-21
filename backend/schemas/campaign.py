from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from backend.db.models import (
    CampaignJobStatus,
    CampaignKind,
    CampaignOutcomeKind,
)


class CampaignScheduleRequest(BaseModel):
    """Schedule a batch of campaign calls.

    Either pass `appointment_ids` to schedule those specifically, or
    `start_date`/`end_date` to sweep every appointment whose start_time
    falls in that inclusive window.
    """

    appointment_ids: list[UUID] | None = None
    start_date: date | None = None
    end_date: date | None = None


class CampaignScheduleResponse(BaseModel):
    scheduled: int
    skipped: int
    job_ids: list[UUID]


class CampaignOutcomeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outcome: CampaignOutcomeKind
    notes: str | None = None
    recorded_at: datetime


class CampaignOutcomeWrite(BaseModel):
    outcome: CampaignOutcomeKind
    notes: str | None = None


class CampaignJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    appointment_id: UUID
    kind: CampaignKind
    status: CampaignJobStatus
    scheduled_for: datetime
    room_name: str | None = None
    attempts: int
    last_error: str | None = None
    created_at: datetime
    outcome: CampaignOutcomeRead | None = None
