from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ConversationLogCreate(BaseModel):
    patient_id: UUID | None = None
    session_id: str
    transcript: Any  # list of {role, content, ...} dicts; stored as JSONB
    language: str = "en"
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ConversationLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID | None
    session_id: str
    transcript: Any
    language: str
    started_at: datetime
    ended_at: datetime | None
