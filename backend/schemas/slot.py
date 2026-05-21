from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SlotBase(BaseModel):
    doctor_id: UUID
    start_time: datetime
    end_time: datetime


class SlotCreate(SlotBase):
    pass


class SlotRead(SlotBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_booked: bool


class SlotDetailRead(BaseModel):
    """Slot enriched with doctor metadata for human-readable display."""

    id: UUID
    doctor_id: UUID
    doctor_name: str
    doctor_specialty: str
    doctor_languages: list[str]
    start_time: datetime
    end_time: datetime
    is_booked: bool
