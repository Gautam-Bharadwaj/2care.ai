from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PatientBase(BaseModel):
    name: str
    phone: str
    preferred_language: str = "en"


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    preferred_language: str | None = None


class PatientRead(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
