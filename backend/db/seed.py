"""Seed the database with 5 doctors and 2 weeks of 30-minute slots."""
import asyncio
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from backend.db.models import AvailabilitySlot, Doctor
from backend.db.session import SessionLocal


DOCTORS_SEED = [
    {
        "name": "Dr. Aarav Sharma",
        "specialty": "General Medicine",
        "languages_spoken": ["en", "hi"],
    },
    {
        "name": "Dr. Meera Krishnan",
        "specialty": "Cardiology",
        "languages_spoken": ["en", "ta"],
    },
    {
        "name": "Dr. Marcus Lee",
        "specialty": "Pediatrics",
        "languages_spoken": ["en"],
    },
    {
        "name": "Dr. Priya Patel",
        "specialty": "General Medicine",
        "languages_spoken": ["en", "hi", "gu"],
    },
    {
        "name": "Dr. Rajesh Iyer",
        "specialty": "Cardiology",
        "languages_spoken": ["en", "ta", "hi"],
    },
]

DAY_START = time(9, 0)
DAY_END = time(17, 0)
SLOT_MINUTES = 30
DAYS_AHEAD = 14


def _slot_starts_for(day: datetime) -> list[datetime]:
    """Yield 30-minute slot starts between 9:00 and 17:00 (last slot starts 16:30)."""
    starts: list[datetime] = []
    cursor = day.replace(
        hour=DAY_START.hour, minute=DAY_START.minute, second=0, microsecond=0
    )
    end_of_day = day.replace(
        hour=DAY_END.hour, minute=DAY_END.minute, second=0, microsecond=0
    )
    while cursor + timedelta(minutes=SLOT_MINUTES) <= end_of_day:
        starts.append(cursor)
        cursor += timedelta(minutes=SLOT_MINUTES)
    return starts


async def seed() -> None:
    async with SessionLocal() as db:
        existing = await db.execute(select(Doctor).limit(1))
        if existing.scalar_one_or_none() is not None:
            print("seed: doctors already present, skipping")
            return

        doctors = [Doctor(**d) for d in DOCTORS_SEED]
        db.add_all(doctors)
        await db.flush()  # assigns IDs without committing

        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        slot_count = 0
        for offset in range(DAYS_AHEAD):
            day = today + timedelta(days=offset)
            # weekdays only (Mon=0 .. Sun=6)
            if day.weekday() >= 5:
                continue
            for start in _slot_starts_for(day):
                for doc in doctors:
                    db.add(
                        AvailabilitySlot(
                            doctor_id=doc.id,
                            start_time=start,
                            end_time=start + timedelta(minutes=SLOT_MINUTES),
                        )
                    )
                    slot_count += 1

        await db.commit()
        print(f"seeded {len(doctors)} doctors and {slot_count} availability slots")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
