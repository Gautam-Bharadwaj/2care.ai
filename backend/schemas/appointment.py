from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from backend.db.models import AppointmentStatus


class AppointmentCreate(BaseModel):
    patient_id: UUID
    slot_id: UUID
    notes: str | None = None


class AppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    doctor_id: UUID
    slot_id: UUID
    status: AppointmentStatus
    notes: str | None = None
    created_at: datetime


class AppointmentDetailRead(BaseModel):
    """Appointment enriched with doctor + slot details."""

    id: UUID
    patient_id: UUID
    doctor_id: UUID
    doctor_name: str
    doctor_specialty: str
    slot_id: UUID
    start_time: datetime
    end_time: datetime
    status: AppointmentStatus
    notes: str | None = None
    created_at: datetime


class RescheduleRequest(BaseModel):
    new_slot_id: UUID


class CancelRequest(BaseModel):
    reason: str = "patient requested cancellation"
