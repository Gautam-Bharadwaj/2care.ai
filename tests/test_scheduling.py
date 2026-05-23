"""Scheduling service tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.db.models import (
    Appointment,
    AppointmentStatus,
    AvailabilitySlot,
    Patient,
)
from backend.services.scheduling import (
    PastTimeError,
    SlotUnavailableError,
    book_appointment,
    cancel_appointment,
    find_alternatives,
    reschedule_appointment,
)


async def test_happy_path_booking(session, patient, future_slot):
    appointment = await book_appointment(session, patient.id, future_slot.id)

    assert appointment.id is not None
    assert appointment.patient_id == patient.id
    assert appointment.slot_id == future_slot.id
    assert appointment.doctor_id == future_slot.doctor_id
    assert appointment.status == AppointmentStatus.scheduled

    await session.refresh(future_slot)
    assert future_slot.is_booked is True


async def test_double_booking_prevented(session, patient, future_slot):
    await book_appointment(session, patient.id, future_slot.id)

    with pytest.raises(SlotUnavailableError):
        await book_appointment(session, patient.id, future_slot.id)


async def test_past_time_rejected(session, patient, doctor):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    past_slot = AvailabilitySlot(
        doctor_id=doctor.id,
        start_time=past,
        end_time=past + timedelta(minutes=30),
    )
    session.add(past_slot)
    await session.commit()
    await session.refresh(past_slot)

    with pytest.raises(PastTimeError):
        await book_appointment(session, patient.id, past_slot.id)


async def test_reschedule_frees_old_slot(session, patient, doctor):
    now = datetime.now(timezone.utc)
    slot_a = AvailabilitySlot(
        doctor_id=doctor.id,
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=1, minutes=30),
    )
    slot_b = AvailabilitySlot(
        doctor_id=doctor.id,
        start_time=now + timedelta(days=2),
        end_time=now + timedelta(days=2, minutes=30),
    )
    session.add_all([slot_a, slot_b])
    await session.commit()
    await session.refresh(slot_a)
    await session.refresh(slot_b)

    appointment = await book_appointment(session, patient.id, slot_a.id)
    rescheduled = await reschedule_appointment(session, appointment.id, slot_b.id)

    await session.refresh(slot_a)
    await session.refresh(slot_b)

    assert rescheduled.slot_id == slot_b.id
    assert slot_a.is_booked is False
    assert slot_b.is_booked is True


async def test_find_alternatives_returns_three(session, patient, doctor):
    """Book the middle slot; alternatives should be 3 nearby slots from the same doctor."""
    now = datetime.now(timezone.utc)
    slots = []
    for i in range(5):
        start = now + timedelta(days=i + 1, hours=3)  # offset to avoid collisions
        slots.append(
            AvailabilitySlot(
                doctor_id=doctor.id,
                start_time=start,
                end_time=start + timedelta(minutes=30),
            )
        )
    session.add_all(slots)
    await session.commit()
    for s in slots:
        await session.refresh(s)

    booked = await book_appointment(session, patient.id, slots[2].id)

    alternatives = await find_alternatives(session, slots[2].id, count=3)

    assert len(alternatives) == 3
    alt_ids = {alt.id for alt in alternatives}
    assert booked.slot_id not in alt_ids
    # All alternatives should be unbooked
    for alt in alternatives:
        assert alt.is_booked is False


async def test_concurrent_booking_race(session_factory, doctor):
    """Two concurrent bookings on the same slot: exactly one should win."""
    async with session_factory() as setup:
        start = datetime.now(timezone.utc) + timedelta(days=3, hours=1)
        slot = AvailabilitySlot(
            doctor_id=doctor.id,
            start_time=start,
            end_time=start + timedelta(minutes=30),
        )
        patient_a = Patient(
            name="Alice", phone=f"+1{uuid4().hex[:10]}", preferred_language="en"
        )
        patient_b = Patient(
            name="Bob", phone=f"+1{uuid4().hex[:10]}", preferred_language="en"
        )
        setup.add_all([slot, patient_a, patient_b])
        await setup.commit()
        slot_id, a_id, b_id = slot.id, patient_a.id, patient_b.id

    async def try_book(patient_id):
        async with session_factory() as s:
            try:
                return await book_appointment(s, patient_id, slot_id)
            except SlotUnavailableError as exc:
                return exc

    results = await asyncio.gather(try_book(a_id), try_book(b_id))

    successes = [r for r in results if isinstance(r, Appointment)]
    failures = [r for r in results if isinstance(r, SlotUnavailableError)]

    assert len(successes) == 1, f"expected 1 success, got {len(successes)}: {results}"
    assert len(failures) == 1, f"expected 1 failure, got {len(failures)}: {results}"

    # Cleanup so this test doesn't leave a booked slot lingering for the session
    async with session_factory() as cleanup:
        appointment = successes[0]
        await cancel_appointment(cleanup, appointment.id, reason="test cleanup")
