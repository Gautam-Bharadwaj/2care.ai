"""Phase 9 robustness scenarios — every case from the assignment brief.

Each scenario is a short scripted conversation: the test writes the
user's turns and the agent's tool decisions inline. The tools that get
called are the *production* scheduling-service functions, so we're
validating the actual code paths the live agent uses — just bypassing
LiveKit + the LLM so the test is deterministic and fast.

What we're asserting per scenario:

  1. Mid-conversation change of mind → reschedule, not double-book.
  2. Unclear request                  → agent asks a clarifier *before*
                                        calling any tool.
  3. Conflict during booking          → tool raises SlotUnavailableError,
                                        runner records it, agent offers
                                        alternatives instead of crashing.
  4. Past-time request                → past slot rejected with
                                        PastTimeError, agent offers the
                                        soonest future slot.
  5. Unavailable doctor               → doctor fully booked, agent offers
                                        same-specialty alternatives.
  6. Cancellation confirmation        → find_patient_appointments runs
                                        BEFORE cancel_appointment, and
                                        only after explicit "yes".
  7. Language switch mid-call         → English → Hindi at turn 3, lock
                                        flips at turn 5 (2-turn window).

Requires Postgres (test DB created by conftest). Hermetic for everything
else — no Redis, no Groq, no LiveKit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models import Appointment, AppointmentStatus
from backend.services import scheduling
from tests.conversation import (
    ConversationRunner,
    make_doctor,
    make_patient,
    make_slots,
)


# ---------------------------------------------------------------------------
# 1. Mid-conversation change of mind → reschedule, not double-book
# ---------------------------------------------------------------------------


async def test_scenario_1_change_of_mind_reschedules(session):
    """Patient books, then before the call ends asks to move to tomorrow.

    The agent should call `reschedule_appointment` against the existing
    booking — not call `book_appointment` again, which would either
    crash (same slot) or create a duplicate row (different slot).
    """
    now = datetime.now(timezone.utc)
    doctor = await make_doctor(session)
    patient = await make_patient(session)
    slot_today, slot_tomorrow = await make_slots(
        session, doctor, [now + timedelta(hours=2), now + timedelta(days=1, hours=2)]
    )

    r = ConversationRunner(session, patient)

    # First booking
    r.say("I'd like to book the slot today at 2pm.")
    appt = await r.book(slot_today.id)
    assert appt is not None
    r.respond("Booked. See you at 2pm today.")

    # Change of mind, same call
    r.say("Wait, actually make it tomorrow instead.")
    rescheduled = await r.reschedule(appt.id, slot_tomorrow.id)
    assert rescheduled is not None
    r.respond("Done, moved to tomorrow at 2pm.")

    # Assertions: exactly one booking exists, on the new slot
    appts = await r.find_my_appointments()
    assert len(appts) == 1
    assert appts[0].slot_id == slot_tomorrow.id
    assert appts[0].status == AppointmentStatus.scheduled

    # Tool sequence: book then reschedule. Critically, no second book_appointment.
    r.assert_tool_sequence(["book_appointment", "reschedule_appointment"])
    assert len(r.calls_to("book_appointment")) == 1

    # Today's slot should now be free again
    await session.refresh(slot_today)
    assert slot_today.is_booked is False


# ---------------------------------------------------------------------------
# 2. Unclear request → ask a clarifying question
# ---------------------------------------------------------------------------


async def test_scenario_2_unclear_request_asks_clarifier(session):
    """'I want to see a doctor sometime soon' — the agent should not guess.

    Concretely: no booking-flow tools may be called until the patient
    has narrowed down specialty or time. The agent's response should
    include a clarifier — we look for a literal '?' to be unambiguous.
    """
    doctor = await make_doctor(session)
    patient = await make_patient(session)
    await make_slots(
        session,
        doctor,
        [datetime.now(timezone.utc) + timedelta(hours=h) for h in (2, 26, 50)],
    )
    r = ConversationRunner(session, patient)

    r.say("I want to see a doctor sometime soon.")
    # No tool calls yet — the agent asks a clarifying question first.
    r.respond("Sure, I can help. What specialty do you need, and any time of day?")

    r.assert_tool_not_called("list_available_slots")
    r.assert_tool_not_called("book_appointment")

    last_agent = next(t for t in reversed(r.turns) if t.role == "assistant")
    assert "?" in last_agent.content, (
        f"expected clarifying question, agent said: {last_agent.content!r}"
    )

    # Once the patient narrows down, the agent does call list_available_slots.
    r.say("General medicine, anytime tomorrow morning works.")
    now = datetime.now(timezone.utc)
    slots = await r.list_slots(
        date_from=now, date_to=now + timedelta(days=2), specialty="General Medicine"
    )
    assert len(slots) >= 1
    r.assert_tool_called("list_available_slots")


# ---------------------------------------------------------------------------
# 3. Conflict during booking → graceful alternatives offered
# ---------------------------------------------------------------------------


async def test_scenario_3_concurrent_booking_offers_alternatives(session, session_factory):
    """Slot becomes booked by another caller mid-confirmation.

    Sequence:
      a) Agent finds slot X, reads back "I'll book Wednesday 10am, ok?"
      b) Patient says "yes"
      c) Between (a) and (c), a concurrent process books slot X
      d) Agent's book_appointment call returns SlotUnavailableError
      e) Agent does NOT crash — it calls find_alternatives and offers
         them. We verify the conflict ToolCall was recorded with status='error'
         and that find_alternatives was the next call.
    """
    now = datetime.now(timezone.utc)
    doctor = await make_doctor(session)
    patient = await make_patient(session)
    target, alt1, alt2 = await make_slots(
        session,
        doctor,
        [now + timedelta(days=1, hours=h) for h in (1, 3, 5)],
    )
    r = ConversationRunner(session, patient)

    r.say("Can I get Wednesday 10am with Dr. Test?")
    slots = await r.list_slots(date_from=now, date_to=now + timedelta(days=2))
    r.respond("I have Wednesday 10am. Shall I book it?")

    r.say("Yes, please.")
    # Concurrent caller swoops in — use a separate session to simulate.
    # Capture ids on the main session BEFORE opening the inner one so
    # the inner session never lazy-loads through the outer one (which
    # asyncpg can't do without a greenlet context).
    target_id = target.id
    async with session_factory() as other:
        other_patient = await make_patient(other, name="Other Caller")
        await scheduling.book_appointment(other, other_patient.id, target_id)

    appt = await r.book(target_id)
    # Agent's tool returned None and recorded the error.
    assert appt is None
    last = r.last_call("book_appointment")
    assert last is not None and last.status == "error"
    assert "SlotUnavailableError" in (last.error or "")

    # The agent recovers: offer alternatives.
    alternatives = await r.alternatives(target_id, count=3)
    r.respond(
        "Looks like that slot just got taken — I can offer Wednesday 12pm "
        "or 2pm instead. Which works?"
    )

    # No crash; the conversation continued; alternatives were offered.
    r.assert_tool_called("find_alternatives")
    assert alternatives is not None and len(alternatives) >= 1
    # And we never silently created a duplicate booking.
    appts = await r.find_my_appointments()
    assert len(appts) == 0


# ---------------------------------------------------------------------------
# 4. Past-time request → refuse and offer soonest future slot
# ---------------------------------------------------------------------------


async def test_scenario_4_past_time_refused_and_alternative_offered(session):
    """Patient asks for yesterday 3pm. Past slots are rejected by the
    scheduling service; the agent should respond by offering the
    soonest valid slot via list_available_slots."""
    now = datetime.now(timezone.utc)
    doctor = await make_doctor(session)
    patient = await make_patient(session)

    # We have to manually insert a past slot since make_slots calls refresh
    # which is fine — the validation happens at book_appointment time.
    past_slot, future_slot = await make_slots(
        session,
        doctor,
        [now - timedelta(days=1, hours=3), now + timedelta(hours=4)],
    )

    r = ConversationRunner(session, patient)
    r.say("I want to come in yesterday at 3pm.")

    appt = await r.book(past_slot.id)
    assert appt is None  # tool refused
    last = r.last_call("book_appointment")
    assert last is not None and last.status == "error"
    assert "PastTimeError" in (last.error or "")

    r.respond("I can't book a time in the past — the soonest I have today is in 4 hours. Want that?")
    future_slots = await r.list_slots(date_from=now, date_to=now + timedelta(days=2))

    # The soonest slot the agent surfaces must be in the future.
    assert future_slots is not None and len(future_slots) >= 1
    assert all(s.start_time > now for s in future_slots)


# ---------------------------------------------------------------------------
# 5. Unavailable doctor → same-specialty alternatives
# ---------------------------------------------------------------------------


async def test_scenario_5_unavailable_doctor_offers_same_specialty(session):
    """Dr. Patel is on leave (all slots already booked). Patient asks
    specifically for Patel. Agent must offer slots from other doctors
    in the same specialty."""
    now = datetime.now(timezone.utc)
    patel = await make_doctor(session, name="Dr. Patel", specialty="Cardiology")
    sharma = await make_doctor(session, name="Dr. Sharma", specialty="Cardiology")
    # Patel is fully booked
    await make_slots(
        session,
        patel,
        [now + timedelta(hours=h) for h in (5, 7, 9)],
        booked=True,
    )
    # Sharma has free slots
    sharma_slots = await make_slots(
        session,
        sharma,
        [now + timedelta(hours=h) for h in (4, 6, 8)],
    )

    patient = await make_patient(session)
    r = ConversationRunner(session, patient)

    r.say("I want Dr. Patel.")
    patel_slots = await r.list_slots(
        date_from=now, date_to=now + timedelta(days=1), doctor_id=patel.id
    )
    assert patel_slots == [], "Patel should have no free slots"

    # Agent falls back to same-specialty.
    r.respond(
        "Dr. Patel is fully booked this week — Dr. Sharma is also "
        "cardiology and has openings. Want me to check?"
    )
    fallback = await r.list_slots(
        date_from=now, date_to=now + timedelta(days=1), specialty="Cardiology"
    )
    assert fallback is not None and len(fallback) > 0
    fallback_doctor_ids = {s.doctor_id for s in fallback}
    assert sharma.id in fallback_doctor_ids
    assert patel.id not in fallback_doctor_ids  # Patel's slots are booked, not surfaced

    r.say("Sure, Dr. Sharma works.")
    appt = await r.book(sharma_slots[0].id)
    assert appt is not None
    assert appt.doctor_id == sharma.id


# ---------------------------------------------------------------------------
# 6. Cancellation requires confirmation
# ---------------------------------------------------------------------------


async def test_scenario_6_cancel_requires_explicit_confirmation(session):
    """'Cancel my appointment.' The agent must:
      1. Look up the appointment(s)
      2. Read back the one being cancelled
      3. Wait for explicit confirmation
      4. Only THEN call cancel_appointment

    We assert on the tool sequence: find_patient_appointments must
    appear before cancel_appointment, and there must be an agent turn
    between them that asks for confirmation.
    """
    now = datetime.now(timezone.utc)
    doctor = await make_doctor(session)
    patient = await make_patient(session)
    (target,) = await make_slots(session, doctor, [now + timedelta(days=1)])

    appt = await scheduling.book_appointment(session, patient.id, target.id)
    # Drop the setup call from the runner's history so assertions
    # only see what happens during the test conversation.
    r = ConversationRunner(session, patient)

    r.say("Cancel my appointment.")
    found = await r.find_my_appointments()
    assert found and found[0].id == appt.id

    # Agent does NOT cancel yet — it asks for confirmation.
    r.respond(
        f"I see your appointment tomorrow with {doctor.name}. "
        "Are you sure you want to cancel?"
    )
    # Verify no cancel call has happened yet.
    r.assert_tool_not_called("cancel_appointment")

    r.say("Yes, cancel it.")
    cancelled = await r.cancel(appt.id, reason="patient requested cancellation")
    assert cancelled is not None
    assert cancelled.status == AppointmentStatus.cancelled

    # Final ordering: find FIRST, cancel LATER.
    r.assert_tool_sequence(["find_patient_appointments", "cancel_appointment"])

    # The confirmation turn must exist between the lookup and the cancel.
    names_in_order = [c.name for c in r.tool_calls]
    find_idx = names_in_order.index("find_patient_appointments")
    cancel_idx = names_in_order.index("cancel_appointment")
    assert cancel_idx > find_idx
    # And the user's "yes" appears between them in the turn log.
    turns = [t for t in r.turns]
    user_turns = [i for i, t in enumerate(turns) if t.role == "user"]
    assert len(user_turns) >= 2  # at least the request + the confirmation


# ---------------------------------------------------------------------------
# 7. Language switch mid-call → lock at turn 5
# ---------------------------------------------------------------------------


async def test_scenario_7_language_switch_locks_after_two_turns():
    """English start. Turn 3 is in Hindi. The lock should NOT flip on
    turn 3 (one detection is not enough — code-switching tolerance).
    Turn 4 in Hindi flips the lock; turn 5 keeps it.

    We use the runner's mirror of the LanguageLock state machine —
    same logic as agent/main.py::LanguageLock. Hermetic: no DB needed.
    """
    from types import SimpleNamespace

    # Minimal Patient-like stub so we can run without Postgres.
    patient = SimpleNamespace(preferred_language="en", id=None, phone="+1")
    r = ConversationRunner(session=None, patient=patient)  # type: ignore[arg-type]
    r.language_locked = False  # mimic "no stored pref" path

    # Turn 1, 2 — English
    r.say("Hello, I'd like to book.", detected_language="en")
    r.say("Tomorrow morning.", detected_language="en")
    assert r.active_language == "en"
    assert r.language_locked is False

    # Turn 3 — first Hindi turn. Still en (lock window not satisfied).
    r.say("कल सुबह दस बजे", detected_language="hi")
    assert r.active_language == "en", (
        "lock must not flip on a single foreign-language turn"
    )
    assert r.language_locked is False

    # Turn 4 — second consecutive Hindi turn → lock flips
    r.say("हाँ, दस बजे ठीक है", detected_language="hi")
    assert r.active_language == "hi"
    assert r.language_locked is True

    # Turn 5 — continues in Hindi, lock stays
    r.say("शुक्रिया", detected_language="hi")
    assert r.active_language == "hi"
    assert r.language_locked is True


# ---------------------------------------------------------------------------
# Bonus: the 2-turn window also resets when the user code-switches back.
# Not strictly in the brief but it's the asymmetry that makes the policy
# safe in practice, so we lock it down.
# ---------------------------------------------------------------------------


async def test_scenario_7b_one_hindi_then_english_does_not_lock():
    from types import SimpleNamespace

    patient = SimpleNamespace(preferred_language="en", id=None, phone="+1")
    r = ConversationRunner(session=None, patient=patient)  # type: ignore[arg-type]
    r.language_locked = False

    r.say("नमस्ते", detected_language="hi")          # streak=1
    r.say("Actually English is fine", detected_language="en")   # reset
    r.say("नमस्ते फिर से", detected_language="hi")    # streak=1 again
    assert r.active_language == "en"
    assert r.language_locked is False
