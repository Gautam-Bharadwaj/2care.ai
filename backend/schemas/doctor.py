from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DoctorBase(BaseModel):
    name: str
    specialty: str
    languages_spoken: list[str] = Field(default_factory=list)


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(BaseModel):
    name: str | None = None
    specialty: str | None = None
    languages_spoken: list[str] | None = None


class DoctorRead(DoctorBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
