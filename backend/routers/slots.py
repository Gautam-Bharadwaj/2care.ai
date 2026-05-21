from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AvailabilitySlot
from backend.db.session import get_db
from backend.schemas.slot import SlotDetailRead
from backend.services.scheduling import find_alternatives, list_available_slots

router = APIRouter(prefix="/slots", tags=["slots"])


def _to_detail(slot: AvailabilitySlot) -> dict:
    return {
        "id": slot.id,
        "doctor_id": slot.doctor_id,
        "doctor_name": slot.doctor.name,
        "doctor_specialty": slot.doctor.specialty,
        "doctor_languages": list(slot.doctor.languages_spoken or []),
        "start_time": slot.start_time,
        "end_time": slot.end_time,
        "is_booked": slot.is_booked,
    }


@router.get("/available", response_model=list[SlotDetailRead])
async def get_available_slots(
    date_from: datetime,
    date_to: datetime,
    doctor_id: UUID | None = None,
    specialty: str | None = None,
    language: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    slots = await list_available_slots(
        db,
        date_from=date_from,
        date_to=date_to,
        doctor_id=doctor_id,
        specialty=specialty,
        language=language,
    )
    return [_to_detail(s) for s in slots]


@router.get("/{slot_id}/alternatives", response_model=list[SlotDetailRead])
async def get_alternatives(
    slot_id: UUID,
    count: int = 3,
    db: AsyncSession = Depends(get_db),
):
    slots = await find_alternatives(db, slot_id, count=count)
    return [_to_detail(s) for s in slots]
