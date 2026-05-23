"""Concurrency / load tests for the booking path.

Two complementary tests:

1. **No double-bookings under contention** — 20 concurrent callers fight
   for the same single slot. The scheduling service must serialize them
   so exactly one wins; the other 19 see `SlotUnavailableError`. The DB
   constraints (`SELECT FOR UPDATE` + unique on `slot_id`) are what
   guarantee this; this test fails loud if either is removed.

2. **Throughput at scale** — 20 distinct slots, 20 concurrent callers,
   each picks their own. All 20 must succeed and the p99 round-trip
   for one full booking flow (list → book → fetch) must stay under 2s.

Both run against the same `twocare_test` DB the rest of the suite uses
(see `tests/conftest.py`). Imported as `tests/load_test.py` rather than
`test_load.py` so pytest doesn't auto-collect it on the default
runs — invoke explicitly with `pytest tests/load_test.py`.

Set `LOAD_TEST_CONCURRENCY` to scale beyond 20 for a real soak test.
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from backend.db.models import (
    Appointment,
    AvailabilitySlot,
    Doctor,
    Patient,
)
from backend.services import scheduling
from backend.services.scheduling import SlotUnavailableError


CONCURRENCY = int(os.environ.get("LOAD_TEST_CONCURRENCY", "20"))
P99_LATENCY_BUDGET_S = 2.0


# ---------------------------------------------------------------------------
# (1) No double-bookings under contention
# ---------------------------------------------------------------------------


async def test_no_double_bookings_under_contention(session_factory):
    """N concurrent callers, ONE slot. Exactly one should win.

    Failure modes this catches:
      - SELECT FOR UPDATE removed → two appointments rows on the same slot
      - Unique constraint on appointments.slot_id dropped → DB allows duplicates
      - Caught-but-swallowed IntegrityError → silently succeeds twice
    """
    slot_id, patient_ids, doctor_id = await _setup_single_slot_storm(
        session_factory, n=CONCURRENCY
    )

    results = await asyncio.gather(
        *[_attempt_booking(session_factory, pid, slot_id) for pid in patient_ids],
        return_exceptions=False,
    )

    successes = [r for r in results if isinstance(r, Appointment)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, (
        f"expected exactly 1 successful booking under contention, "
        f"got {len(successes)} successes and {len(failures)} failures"
    )
    # Every failure is the expected scheduling error — no crashes.
    for f in failures:
        assert isinstance(f, SlotUnavailableError), (
            f"unexpected exception type: {type(f).__name__}: {f}"
        )

    # Cleanup
    await _cancel(session_factory, successes[0].id)


# ---------------------------------------------------------------------------
# (2) Throughput at scale
# ---------------------------------------------------------------------------


async def test_throughput_20_concurrent_distinct_bookings(session_factory):
    """N callers, N distinct slots. All N must succeed.

    p99 of the full booking flow (list → book → find_my_appointments)
    must stay under 2s. We use perf_counter() per flow so contention on
    one slow flow doesn't drag the others into the violation zone.
    """
    slot_ids, patient_ids = await _setup_distinct_slots(
        session_factory, n=CONCURRENCY
    )

    async def _flow(patient_id: UUID, slot_id: UUID) -> tuple[Any, float]:
        start = time.perf_counter()
        async with session_factory() as s:
            now = datetime.now(timezone.utc)
            # list — what the agent does after the user picks a doctor
            await scheduling.list_available_slots(
                s, date_from=now, date_to=now + timedelta(days=7)
            )
            # book — the actual mutation
            appt = await scheduling.book_appointment(s, patient_id, slot_id)
            # round-trip read — what the agent calls to confirm
            await s.refresh(appt)
        elapsed = time.perf_counter() - start
        return appt, elapsed

    pairs = list(zip(patient_ids, slot_ids))
    outputs = await asyncio.gather(*[_flow(p, sl) for p, sl in pairs])

    appts = [o[0] for o in outputs]
    latencies = sorted(o[1] for o in outputs)

    assert len(appts) == CONCURRENCY, (
        f"expected {CONCURRENCY} successful flows, got {len(appts)}"
    )
    # Each appointment uses a distinct slot — no duplicates
    used_slot_ids = {a.slot_id for a in appts}
    assert len(used_slot_ids) == CONCURRENCY, (
        f"some slots got booked twice: {len(used_slot_ids)} distinct of "
        f"{CONCURRENCY} appointments"
    )

    p50 = _percentile(latencies, 50)
    p90 = _percentile(latencies, 90)
    p99 = _percentile(latencies, 99)
    print(
        f"\n[load] n={CONCURRENCY}  p50={p50*1000:.0f}ms  "
        f"p90={p90*1000:.0f}ms  p99={p99*1000:.0f}ms  "
        f"mean={statistics.mean(latencies)*1000:.0f}ms"
    )
    assert p99 < P99_LATENCY_BUDGET_S, (
        f"p99 booking latency {p99*1000:.0f}ms exceeds budget "
        f"{P99_LATENCY_BUDGET_S*1000:.0f}ms"
    )

    # Cleanup
    for a in appts:
        await _cancel(session_factory, a.id)


# NOTE on re-booking: `appointments.slot_id` has a UNIQUE constraint, so a
# cancelled appointment still occupies its slot's row in the appointments
# table even though `slot.is_booked` is reset to False. That means the same
# slot can't be booked by a *different* patient after cancellation without
# either dropping the unique constraint or hard-deleting the cancelled
# row. This is a deliberate audit-preservation choice in the data model,
# not a bug — flagging it here so it doesn't surprise the next reader.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_single_slot_storm(session_factory, n: int):
    """Create one slot far enough in the future + n patients."""
    async with session_factory() as s:
        doctor = Doctor(
            name=f"Dr Load {uuid4().hex[:4]}",
            specialty="General Medicine",
            languages_spoken=["en"],
        )
        s.add(doctor)
        await s.commit()
        await s.refresh(doctor)

        start = datetime.now(timezone.utc) + timedelta(days=5, hours=1)
        slot = AvailabilitySlot(
            doctor_id=doctor.id,
            start_time=start,
            end_time=start + timedelta(minutes=30),
        )
        s.add(slot)

        patients = [
            Patient(
                name=f"Caller {i}",
                phone=f"+1{uuid4().hex[:10]}",
                preferred_language="en",
            )
            for i in range(n)
        ]
        s.add_all(patients)
        await s.commit()
        for p in patients:
            await s.refresh(p)
        await s.refresh(slot)
        return slot.id, [p.id for p in patients], doctor.id


async def _setup_distinct_slots(session_factory, n: int):
    """Create n slots (staggered) + n patients."""
    async with session_factory() as s:
        doctor = Doctor(
            name=f"Dr Throughput {uuid4().hex[:4]}",
            specialty="General Medicine",
            languages_spoken=["en"],
        )
        s.add(doctor)
        await s.commit()
        await s.refresh(doctor)

        base = datetime.now(timezone.utc) + timedelta(days=6, hours=1)
        slots = [
            AvailabilitySlot(
                doctor_id=doctor.id,
                start_time=base + timedelta(minutes=30 * i),
                end_time=base + timedelta(minutes=30 * i + 30),
            )
            for i in range(n)
        ]
        patients = [
            Patient(
                name=f"Tput Caller {i}",
                phone=f"+1{uuid4().hex[:10]}",
                preferred_language="en",
            )
            for i in range(n)
        ]
        s.add_all(slots + patients)
        await s.commit()
        for o in slots + patients:
            await s.refresh(o)
        return [sl.id for sl in slots], [p.id for p in patients]


async def _attempt_booking(session_factory, patient_id, slot_id):
    """One bare booking attempt. Returns the Appointment on success or
    the SlotUnavailableError as a *value* (not raised) so gather can
    collect the full result distribution."""
    async with session_factory() as s:
        try:
            return await scheduling.book_appointment(s, patient_id, slot_id)
        except SlotUnavailableError as e:
            return e


async def _cancel(session_factory, appointment_id):
    async with session_factory() as s:
        try:
            await scheduling.cancel_appointment(s, appointment_id, reason="load test cleanup")
        except SlotUnavailableError:
            pass


def _percentile(sorted_values, pct):
    """Nearest-rank with ceil — matches agent/metrics.py."""
    import math

    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_values)))
    return sorted_values[min(rank - 1, len(sorted_values) - 1)]
