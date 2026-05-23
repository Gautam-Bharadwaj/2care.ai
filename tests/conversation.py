"""Programmatic conversation runner — text-only harness for scenario tests.

We can't drive the real voice pipeline in unit tests (it expects a
LiveKit room and a microphone). But the *contract* we want to validate
is the tool layer + scheduling service + state machine: does the agent
double-book? Does it ask before cancelling? Does it offer alternatives
when a slot becomes unavailable mid-booking?

This runner makes that testable by treating "what the LLM decides to do
next" as test code. The test writes a small state machine inline — the
user says X, the runner calls tool Y with args Z, asserts on the
result. The tools are the *production* scheduling-service calls (we
use the service directly so we don't need a running FastAPI; the
behavior under test is identical).

What it gives you:

- `say(text, detected_language="en")` records a user turn
- `list_slots(...)`, `book(...)`, `reschedule(...)`, `cancel(...)`,
  `alternatives(...)`, `find_appointments(...)` run the production
  service against the test DB. Each appends to `tool_calls`.
- `respond(text)` records the agent's spoken reply (so tests can
  assert on whether a clarifying question was asked)
- `assert_tool_called`, `assert_tool_not_called`, `assert_tool_sequence`
  read off the recorded tool log

The runner intentionally does NOT call an LLM. The "is this a
clarifying question?" assertion is the test author's responsibility —
they write the agent's reply explicitly. That keeps tests deterministic
and CI-fast; we already test prompt behavior separately in the live
voice tests (out of scope here).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import (
    Appointment,
    AppointmentStatus,
    AvailabilitySlot,
    Doctor,
    Patient,
)
from backend.services import scheduling


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: Any
    status: str = "ok"   # "ok" | "error"
    error: str | None = None


@dataclass
class Turn:
    role: str   # "user" | "assistant"
    content: str
    detected_language: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationRunner:
    """Per-test state holder + production tool wrapper."""

    def __init__(self, session: AsyncSession, patient: Patient) -> None:
        self.session = session
        self.patient = patient
        # Cache the patient_id at construction time. A failed scheduling
        # call rolls back the outer session, which expires all attached
        # objects — touching `patient.id` after that triggers a sync
        # lazy-load and a MissingGreenlet under AsyncSession. Caching
        # avoids that without callers having to know about the rollback.
        self.patient_id = patient.id if patient is not None else None
        self.turns: list[Turn] = []
        self.tool_calls: list[ToolCall] = []
        # Phase-5 style language-lock simulation. The voice runtime
        # tracks this with `LanguageLock`; for scenario tests we mirror
        # the state-machine fields without pulling in LiveKit.
        self.active_language = patient.preferred_language or "en"
        self.language_locked = bool(patient.preferred_language)
        self._lang_pending: str | None = None
        self._lang_streak = 0

    # -- user/agent turns ----------------------------------------------------

    def say(self, text: str, detected_language: str | None = None) -> None:
        """Record a user turn + advance the language-lock state machine."""
        self.turns.append(
            Turn(role="user", content=text, detected_language=detected_language)
        )
        if detected_language:
            self._observe_language(detected_language)

    def respond(self, text: str) -> None:
        """Record what the agent says back."""
        self.turns.append(Turn(role="assistant", content=text))

    # -- language lock -------------------------------------------------------

    def _observe_language(self, code: str) -> None:
        """Two-consecutive-turn lock window (matches LanguageLock in agent/main.py)."""
        from agent.language import normalize_detected, LOCK_THRESHOLD_TURNS

        lang = normalize_detected(code)
        if not lang or lang == self.active_language:
            self._lang_pending = None
            self._lang_streak = 0
            return

        if self._lang_pending == lang:
            self._lang_streak += 1
        else:
            self._lang_pending = lang
            self._lang_streak = 1

        if self._lang_streak >= LOCK_THRESHOLD_TURNS:
            self.active_language = lang
            self.language_locked = True
            self._lang_pending = None
            self._lang_streak = 0

    # -- tool wrappers --------------------------------------------------------

    async def list_slots(
        self,
        date_from: datetime,
        date_to: datetime,
        doctor_id: UUID | None = None,
        specialty: str | None = None,
    ) -> list[AvailabilitySlot]:
        return await self._tool(
            "list_available_slots",
            scheduling.list_available_slots,
            self.session,
            date_from=date_from,
            date_to=date_to,
            doctor_id=doctor_id,
            specialty=specialty,
        )

    async def book(self, slot_id: UUID) -> Appointment | None:
        return await self._tool(
            "book_appointment",
            scheduling.book_appointment,
            self.session,
            self.patient_id,
            slot_id,
        )

    async def reschedule(self, appointment_id: UUID, new_slot_id: UUID) -> Appointment | None:
        return await self._tool(
            "reschedule_appointment",
            scheduling.reschedule_appointment,
            self.session,
            appointment_id,
            new_slot_id,
        )

    async def cancel(self, appointment_id: UUID, reason: str = "patient requested") -> Appointment | None:
        return await self._tool(
            "cancel_appointment",
            scheduling.cancel_appointment,
            self.session,
            appointment_id,
            reason,
        )

    async def alternatives(self, slot_id: UUID, count: int = 3) -> list[AvailabilitySlot]:
        return await self._tool(
            "find_alternatives",
            scheduling.find_alternatives,
            self.session,
            slot_id,
            count,
        )

    async def find_my_appointments(self) -> list[Appointment]:
        """Returns the patient's active appointments via the production query path."""
        from sqlalchemy import select

        async def _query(db, patient_id):
            stmt = (
                select(Appointment)
                .where(Appointment.patient_id == patient_id)
                .where(Appointment.status == AppointmentStatus.scheduled)
            )
            return list((await db.execute(stmt)).scalars().all())

        return await self._tool(
            "find_patient_appointments",
            _query,
            self.session,
            self.patient_id,
        )

    # -- internal dispatch ---------------------------------------------------

    async def _tool(self, name: str, fn, *args, **kwargs):
        """Run a tool, record the call, and re-raise nothing — errors are
        captured on the ToolCall record so tests can assert on graceful
        handling without try/except boilerplate."""
        try:
            result = await fn(*args, **kwargs)
            # Strip the session arg from the recorded args dict — it would
            # bloat every assertion and isn't part of what the LLM sees.
            self.tool_calls.append(
                ToolCall(
                    name=name,
                    args=_summarize_args(args[1:], kwargs),
                    result=result,
                )
            )
            return result
        except Exception as e:  # noqa: BLE001
            self.tool_calls.append(
                ToolCall(
                    name=name,
                    args=_summarize_args(args[1:], kwargs),
                    result=None,
                    status="error",
                    error=f"{type(e).__name__}: {e}",
                )
            )
            return None

    # -- assertion helpers ---------------------------------------------------

    def calls_to(self, name: str) -> list[ToolCall]:
        return [c for c in self.tool_calls if c.name == name]

    def last_call(self, name: str | None = None) -> ToolCall | None:
        if name is None:
            return self.tool_calls[-1] if self.tool_calls else None
        for c in reversed(self.tool_calls):
            if c.name == name:
                return c
        return None

    def assert_tool_called(self, name: str) -> ToolCall:
        calls = self.calls_to(name)
        assert calls, (
            f"expected at least one call to {name!r}, "
            f"got: {[c.name for c in self.tool_calls]}"
        )
        return calls[-1]

    def assert_tool_not_called(self, name: str) -> None:
        calls = self.calls_to(name)
        assert not calls, (
            f"expected no calls to {name!r}, got {len(calls)}: "
            f"{[c.args for c in calls]}"
        )

    def assert_tool_sequence(self, expected: list[str]) -> None:
        """Tool names called in the same order (subsequence match — other
        tools may appear between them, but the relative order matches)."""
        names = [c.name for c in self.tool_calls]
        i = 0
        for n in names:
            if i < len(expected) and n == expected[i]:
                i += 1
        assert i == len(expected), (
            f"expected subsequence {expected}, got call sequence {names}"
        )


def _summarize_args(positional: tuple, keyword: dict) -> dict[str, Any]:
    """Tag positional args with their index since we don't have parameter names."""
    out = {f"arg{i}": _short(v) for i, v in enumerate(positional)}
    out.update({k: _short(v) for k, v in keyword.items()})
    return out


def _short(v: Any) -> Any:
    if isinstance(v, (UUID, datetime)):
        return str(v)
    return v


# ---------------------------------------------------------------------------
# Builder helpers — keep the scenario test bodies short by hiding the DB
# scaffolding behind named helpers.
# ---------------------------------------------------------------------------


async def make_patient(
    session, name: str = "Test Caller", preferred_language: str | None = None
) -> Patient:
    from uuid import uuid4

    p = Patient(
        name=name,
        phone=f"+1{uuid4().hex[:10]}",
        preferred_language=preferred_language or "en",
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return p


async def make_doctor(
    session,
    name: str = "Dr. Test",
    specialty: str = "General Medicine",
    languages: list[str] | None = None,
) -> Doctor:
    from uuid import uuid4

    d = Doctor(
        name=f"{name} {uuid4().hex[:4]}",
        specialty=specialty,
        languages_spoken=languages or ["en"],
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return d


async def make_slots(
    session,
    doctor: Doctor,
    starts: list[datetime],
    *,
    booked: bool = False,
) -> list[AvailabilitySlot]:
    from datetime import timedelta

    slots = [
        AvailabilitySlot(
            doctor_id=doctor.id,
            start_time=start,
            end_time=start + timedelta(minutes=30),
            is_booked=booked,
        )
        for start in starts
    ]
    session.add_all(slots)
    await session.commit()
    for s in slots:
        await session.refresh(s)
    return slots
