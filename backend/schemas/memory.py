from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from backend.db.models import MemoryKind


class MemoryCreate(BaseModel):
    patient_id: UUID
    content: str
    kind: MemoryKind = MemoryKind.note


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    content: str
    kind: MemoryKind
    created_at: datetime


class MemoryRecallRequest(BaseModel):
    patient_id: UUID
    query: str
    k: int = 4


class SummarizeRequest(BaseModel):
    patient_id: UUID
    transcript: list[dict]


class SummarizeResponse(BaseModel):
    facts_stored: int
