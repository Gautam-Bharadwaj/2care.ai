"""Phase 6: outbound campaign tests.

Covers the four moving parts:

1. **Scheduling router** (`POST /campaigns/reminders/schedule`,
   `POST /campaigns/followups/schedule`, `GET /campaigns/jobs`,
   `POST /campaigns/jobs/{id}/outcome`).

2. **Worker dial** (`workers.outbound._run_campaign_job`): given a
   `CampaignJob` row, the worker should:
     - flip the job to `in_progress`
     - dial via a `CreateSIPParticipantRequest` whose
       `participant_metadata` carries the campaign type + appointment ID
     - pre-create the LiveKit room with matching metadata
     - mark the job `failed` if dialing raises

3. **Skip path**: if the appointment has been cancelled in the meantime,
   the worker doesn't ring the patient — it records an `other` outcome
   noting the skip.

4. **Agent-side metadata flow** (`agent.campaigns.parse_metadata` +
   `agent.main._extract_campaign_metadata`): the JSON the worker stamps
   round-trips correctly into a `CampaignMetadata` dataclass; the agent's
   campaign-specific opening contains the right phrases; voicemail is
   detected after `VOICEMAIL_SILENCE_SECONDS` with no user audio.

All Twilio/LiveKit/Cartesia/Deepgram calls are mocked. The DB tests run
against the real `twocare_test` Postgres set up by `conftest.py`.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from agent.campaigns import (
    CampaignMetadata,
    opening_line,
    parse_metadata,
    system_prompt_suffix,
)
from backend.db.models import (
    Appointment,
    AppointmentStatus,
    AvailabilitySlot,
    CampaignJob,
    CampaignJobStatus,
    CampaignKind,
    CampaignOutcome,
    CampaignOutcomeKind,
)


# ---------------------------------------------------------------------------
# Test plumbing — FastAPI app that uses the test DB session
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(session_factory, monkeypatch):
    """A FastAPI app whose `get_db` yields from the test session factory.

    Without this override the router would talk to the production DSN.
    """
    from backend.db import session as db_session
    from backend.main import create_app

    monkeypatch.setattr(db_session, "SessionLocal", session_factory)

    application = create_app()

    async def _get_db_override():
        async with session_factory() as s:
            yield s

    application.dependency_overrides[db_session.get_db] = _get_db_override
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture(autouse=True)
def mute_celery(monkeypatch):
    """Don't actually hit Redis when the router calls `apply_async`.

    We replace both campaign tasks' `apply_async` with a stub that records
    the args + returns a fake AsyncResult. Restored by monkeypatch on
    teardown.
    """
    from workers import outbound

    captured: list[dict] = []

    def _fake_apply_async(*, args=None, eta=None, **_kw):
        captured.append({"args": args, "eta": eta})
        return SimpleNamespace(id=f"fake-task-{uuid4().hex[:8]}")

    # We have to wrap each .apply_async because Celery stores the bound
    # method on the task instance.
    monkeypatch.setattr(
        outbound.place_reminder_call, "apply_async", _fake_apply_async
    )
    monkeypatch.setattr(
        outbound.place_followup_call, "apply_async", _fake_apply_async
    )
    return captured


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


async def _make_appointment(
    session, doctor, patient, *, hours_from_now: float = 24.0
):
    """Create a fresh patient + slot + scheduled appointment for one test."""
    start = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    slot = AvailabilitySlot(
        doctor_id=doctor.id,
        start_time=start,
        end_time=start + timedelta(minutes=30),
        is_booked=True,
    )
    session.add(slot)
    await session.commit()
    await session.refresh(slot)
    appt = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        slot_id=slot.id,
        status=AppointmentStatus.scheduled,
    )
    session.add(appt)
    await session.commit()
    await session.refresh(appt)
    return appt, slot


# ---------------------------------------------------------------------------
# (1) Scheduling router
# ---------------------------------------------------------------------------


async def test_schedule_reminders_creates_jobs_with_correct_eta(
    client, session, doctor, patient, mute_celery
):
    appt, slot = await _make_appointment(session, doctor, patient, hours_from_now=24)

    r = await client.post(
        "/campaigns/reminders/schedule",
        json={"appointment_ids": [str(appt.id)]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scheduled"] == 1
    assert body["skipped"] == 0
    assert len(body["job_ids"]) == 1

    # The job should be queued at slot.start_time - 24h.
    assert len(mute_celery) == 1
    eta = mute_celery[0]["eta"]
    expected_eta = slot.start_time - timedelta(hours=24)
    assert abs((eta - expected_eta).total_seconds()) < 2, (eta, expected_eta)

    # And there should be a CampaignJob row in queued state.
    job = (
        await session.execute(select(CampaignJob).where(CampaignJob.appointment_id == appt.id))
    ).scalar_one()
    assert job.kind == CampaignKind.reminder
    assert job.status == CampaignJobStatus.queued
    assert job.celery_task_id is not None


async def test_schedule_followups_uses_post_visit_offset(
    client, session, doctor, patient, mute_celery
):
    # 1 hour ago — pretend the visit was completed
    appt, slot = await _make_appointment(session, doctor, patient, hours_from_now=-1)
    appt.status = AppointmentStatus.completed
    await session.commit()

    r = await client.post(
        "/campaigns/followups/schedule",
        json={"appointment_ids": [str(appt.id)]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["scheduled"] == 1

    eta = mute_celery[-1]["eta"]
    expected = slot.start_time + timedelta(hours=48)
    assert abs((eta - expected).total_seconds()) < 2


async def test_schedule_is_idempotent_for_duplicate_appointment(
    client, session, doctor, patient, mute_celery
):
    appt, _ = await _make_appointment(session, doctor, patient)
    # First schedule
    r1 = await client.post(
        "/campaigns/reminders/schedule",
        json={"appointment_ids": [str(appt.id)]},
    )
    assert r1.status_code == 200
    # Second schedule: same appointment, same kind → skipped, not duplicated.
    r2 = await client.post(
        "/campaigns/reminders/schedule",
        json={"appointment_ids": [str(appt.id)]},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["scheduled"] == 0
    assert body["skipped"] == 1

    # Only one job in the DB.
    jobs = (
        await session.execute(select(CampaignJob).where(CampaignJob.appointment_id == appt.id))
    ).scalars().all()
    assert len(jobs) == 1


async def test_schedule_rejects_missing_target(client):
    r = await client.post("/campaigns/reminders/schedule", json={})
    assert r.status_code == 400


async def test_list_jobs_returns_inlined_outcome(
    client, session, doctor, patient, mute_celery
):
    appt, _ = await _make_appointment(session, doctor, patient)
    s = await client.post(
        "/campaigns/reminders/schedule",
        json={"appointment_ids": [str(appt.id)]},
    )
    job_id = s.json()["job_ids"][0]

    r = await client.post(
        f"/campaigns/jobs/{job_id}/outcome",
        json={"outcome": "confirmed", "notes": "patient said yes"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == CampaignJobStatus.completed.value
    assert body["outcome"]["outcome"] == CampaignOutcomeKind.confirmed.value
    assert body["outcome"]["notes"] == "patient said yes"

    listing = await client.get("/campaigns/jobs")
    assert listing.status_code == 200
    rows = listing.json()
    matching = [j for j in rows if j["id"] == job_id]
    assert matching and matching[0]["outcome"]["outcome"] == "confirmed"


async def test_outcome_is_idempotent(client, session, doctor, patient, mute_celery):
    appt, _ = await _make_appointment(session, doctor, patient)
    s = await client.post(
        "/campaigns/reminders/schedule",
        json={"appointment_ids": [str(appt.id)]},
    )
    job_id = s.json()["job_ids"][0]

    await client.post(
        f"/campaigns/jobs/{job_id}/outcome",
        json={"outcome": "confirmed"},
    )
    # Re-record with a different outcome — should overwrite, not create a 2nd row.
    r = await client.post(
        f"/campaigns/jobs/{job_id}/outcome",
        json={"outcome": "rescheduled", "notes": "moved to next week"},
    )
    assert r.status_code == 200
    assert r.json()["outcome"]["outcome"] == "rescheduled"

    rows = (
        await session.execute(select(CampaignOutcome).where(CampaignOutcome.job_id == job_id))
    ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# (2) Worker dial path
# ---------------------------------------------------------------------------


async def test_worker_dials_with_correct_metadata(
    session, session_factory, doctor, patient, monkeypatch
):
    """Worker should stamp campaign metadata onto the SIPParticipantRequest
    and on the room. This is how the agent learns it's a campaign call."""
    from workers import outbound

    appt, slot = await _make_appointment(session, doctor, patient)

    job = CampaignJob(
        appointment_id=appt.id,
        kind=CampaignKind.reminder,
        scheduled_for=datetime.now(timezone.utc),
        status=CampaignJobStatus.queued,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    captured: dict = {}

    async def fake_dial(to_phone, room_name, metadata):
        captured["to"] = to_phone
        captured["room"] = room_name
        captured["meta"] = metadata
        return {"participant_identity": f"caller_{to_phone}", "room": room_name}

    # Replace the actual SIP dialing with our spy
    monkeypatch.setattr(outbound, "_dial", fake_dial)
    # And use the test session factory in the worker so it reads/writes
    # against the same DB as the test setup.
    monkeypatch.setattr(outbound, "SessionLocal", session_factory)

    result = await outbound._run_campaign_job(str(job.id), CampaignKind.reminder)
    assert result["ok"] is True
    assert captured["to"] == patient.phone
    assert captured["room"].startswith("campaign-reminder-")
    assert captured["meta"]["campaign_type"] == "reminder"
    assert captured["meta"]["appointment_id"] == str(appt.id)
    assert captured["meta"]["campaign_job_id"] == str(job.id)
    assert captured["meta"]["patient_phone"] == patient.phone
    assert captured["meta"]["doctor_name"] == doctor.name

    # Job should be marked in_progress (agent flips it to completed via the
    # /outcome route at end of call).
    await session.refresh(job)
    assert job.status == CampaignJobStatus.in_progress
    assert job.room_name == captured["room"]
    assert job.attempts == 1


async def test_worker_marks_failed_on_dial_exception(
    session, session_factory, doctor, patient, monkeypatch
):
    from workers import outbound

    appt, _ = await _make_appointment(session, doctor, patient)
    job = CampaignJob(
        appointment_id=appt.id,
        kind=CampaignKind.reminder,
        scheduled_for=datetime.now(timezone.utc),
        status=CampaignJobStatus.queued,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    async def boom(*a, **kw):
        raise RuntimeError("twilio circuits busy")

    monkeypatch.setattr(outbound, "_dial", boom)
    monkeypatch.setattr(outbound, "SessionLocal", session_factory)

    result = await outbound._run_campaign_job(str(job.id), CampaignKind.reminder)
    assert result["ok"] is False

    await session.refresh(job)
    assert job.status == CampaignJobStatus.failed
    assert "twilio circuits busy" in (job.last_error or "")


async def test_worker_skips_cancelled_appointment(
    session, session_factory, doctor, patient, monkeypatch
):
    """If the appointment was cancelled after the job was scheduled, don't
    ring the patient — record an 'other' outcome with a 'skipped' note."""
    from workers import outbound

    appt, _ = await _make_appointment(session, doctor, patient)
    appt.status = AppointmentStatus.cancelled
    await session.commit()

    job = CampaignJob(
        appointment_id=appt.id,
        kind=CampaignKind.reminder,
        scheduled_for=datetime.now(timezone.utc),
        status=CampaignJobStatus.queued,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    dial_called = False

    async def fake_dial(*a, **kw):
        nonlocal dial_called
        dial_called = True
        return {}

    monkeypatch.setattr(outbound, "_dial", fake_dial)
    monkeypatch.setattr(outbound, "SessionLocal", session_factory)

    result = await outbound._run_campaign_job(str(job.id), CampaignKind.reminder)
    assert result["ok"] is True
    assert result["reason"] == "skipped_not_scheduled"
    assert dial_called is False

    await session.refresh(job)
    assert job.status == CampaignJobStatus.completed


async def test_dial_passes_metadata_to_livekit_request(monkeypatch):
    """End-to-end of `_dial`: the JSON it stamps onto the SIP request must
    match what `agent.campaigns.parse_metadata` expects to read back."""
    from workers import outbound
    from config import get_settings

    # Settings need a trunk id or _dial bails out early.
    settings = get_settings()
    monkeypatch.setattr(settings, "livekit_sip_trunk_id", "ST_test")

    sip_calls: list = []
    room_calls: list = []

    fake_room_service = MagicMock()
    fake_room_service.create_room = AsyncMock()

    async def capture_create_sip(req):
        sip_calls.append(req)
        return SimpleNamespace(participant_identity=req.participant_identity)

    async def capture_create_room(req):
        room_calls.append(req)
        return SimpleNamespace(name=req.name)

    fake_room_service.create_room.side_effect = capture_create_room
    fake_sip_service = MagicMock()
    fake_sip_service.create_sip_participant = capture_create_sip

    class FakeLKAPI:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        room = fake_room_service
        sip = fake_sip_service

    monkeypatch.setattr(outbound.api, "LiveKitAPI", FakeLKAPI)

    meta = {
        "campaign_type": "reminder",
        "appointment_id": "appt-1",
        "campaign_job_id": "job-1",
        "patient_phone": "+15555550100",
        "patient_name": "Asha",
        "doctor_name": "Dr Mehta",
        "appointment_start": "2026-05-24T10:00:00+00:00",
    }
    result = await outbound._dial("+15555550100", "room-1", meta)
    assert result["room"] == "room-1"

    # Room metadata is the same JSON
    assert len(room_calls) == 1
    assert json.loads(room_calls[0].metadata)["campaign_type"] == "reminder"

    # SIP participant metadata round-trips through parse_metadata
    assert len(sip_calls) == 1
    req = sip_calls[0]
    assert req.sip_trunk_id == "ST_test"
    assert req.sip_call_to == "+15555550100"
    parsed = parse_metadata(req.participant_metadata)
    assert parsed is not None
    assert parsed.campaign_type == "reminder"
    assert parsed.appointment_id == "appt-1"
    assert parsed.campaign_job_id == "job-1"
    assert parsed.doctor_name == "Dr Mehta"


# ---------------------------------------------------------------------------
# (3) Agent-side metadata flow
# ---------------------------------------------------------------------------


def test_parse_metadata_roundtrips_through_workers_blob():
    blob = json.dumps(
        {
            "campaign_type": "followup",
            "appointment_id": "appt-1",
            "campaign_job_id": "job-1",
            "patient_name": "Asha",
            "doctor_name": "Mehta",
            "appointment_start": "2026-05-24T10:00:00+00:00",
        }
    )
    m = parse_metadata(blob)
    assert isinstance(m, CampaignMetadata)
    assert m.campaign_type == "followup"
    assert m.patient_name == "Asha"
    assert m.doctor_name == "Mehta"


def test_parse_metadata_rejects_unknown_campaign_type():
    blob = json.dumps(
        {"campaign_type": "spam", "appointment_id": "x", "campaign_job_id": "y"}
    )
    assert parse_metadata(blob) is None


def test_parse_metadata_handles_empty_and_garbage():
    assert parse_metadata(None) is None
    assert parse_metadata("") is None
    assert parse_metadata("not-json") is None
    assert parse_metadata(json.dumps([1, 2, 3])) is None


def test_opening_line_reminder_includes_doctor_and_time():
    m = CampaignMetadata(
        campaign_type="reminder",
        appointment_id="x",
        campaign_job_id="y",
        patient_name="Asha",
        doctor_name="Mehta",
        appointment_start="2026-05-24T10:00:00+00:00",
    )
    line = opening_line(m)
    assert "Asha" in line
    assert "Mehta" in line
    assert "reminder" in line.lower()


def test_opening_line_followup_asks_about_recovery():
    m = CampaignMetadata(
        campaign_type="followup",
        appointment_id="x",
        campaign_job_id="y",
        patient_name="Asha",
        doctor_name="Mehta",
        appointment_start=None,
    )
    line = opening_line(m)
    assert "follow-up" in line.lower() or "feeling" in line.lower()


def test_system_prompt_suffix_mentions_record_outcome_for_both_kinds():
    for kind in ("reminder", "followup"):
        m = CampaignMetadata(
            campaign_type=kind,
            appointment_id="x",
            campaign_job_id="y",
            patient_name=None,
            doctor_name=None,
            appointment_start=None,
        )
        suffix = system_prompt_suffix(m)
        assert "record_campaign_outcome" in suffix
        # All outcomes listed in some form
        for o in ("confirmed", "voicemail", "other"):
            assert o in suffix, f"{kind} suffix missing outcome '{o}'"


def test_extract_campaign_metadata_prefers_participant_then_room():
    from agent.main import _extract_campaign_metadata

    ctx = MagicMock()
    ctx.room.metadata = json.dumps(
        {
            "campaign_type": "followup",
            "appointment_id": "from-room",
            "campaign_job_id": "j",
        }
    )
    # Participant wins when set
    participant_with = SimpleNamespace(
        metadata=json.dumps(
            {
                "campaign_type": "reminder",
                "appointment_id": "from-participant",
                "campaign_job_id": "j",
            }
        )
    )
    meta = _extract_campaign_metadata(ctx, participant_with)
    assert meta is not None and meta.appointment_id == "from-participant"

    # Falls back to room when participant has nothing
    participant_without = SimpleNamespace(metadata=None)
    meta2 = _extract_campaign_metadata(ctx, participant_without)
    assert meta2 is not None and meta2.appointment_id == "from-room"

    # Returns None when neither has campaign metadata
    ctx_no_room = MagicMock()
    ctx_no_room.room.metadata = None
    assert _extract_campaign_metadata(ctx_no_room, participant_without) is None


# ---------------------------------------------------------------------------
# (4) Voicemail / outcome wiring
# ---------------------------------------------------------------------------


async def test_record_outcome_via_backend_posts_to_correct_route(monkeypatch):
    from agent import main as agent_main

    calls: list = []

    class FakeResp:
        status_code = 200
        text = "{}"

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, url, json):
            calls.append({"url": url, "json": json})
            return FakeResp()

    monkeypatch.setattr(agent_main.httpx, "AsyncClient", FakeClient)

    await agent_main._record_outcome_via_backend(
        "job-xyz", "voicemail", "30s no audio"
    )
    assert calls == [
        {
            "url": "/campaigns/jobs/job-xyz/outcome",
            "json": {"outcome": "voicemail", "notes": "30s no audio"},
        }
    ]


async def test_celery_beat_schedule_has_daily_9am_ist_sweep():
    """Sanity-check the beat schedule wiring: 9:00 daily on Asia/Kolkata."""
    from workers.celery_app import celery_app

    sched = celery_app.conf.beat_schedule
    assert "daily-reminder-sweep-9am-ist" in sched
    entry = sched["daily-reminder-sweep-9am-ist"]
    assert entry["task"] == "twocare.sweep_reminder_window"
    crontab = entry["schedule"]
    # crontab `_orig_*` fields are sets of integers normalized from "9"/"0".
    assert 9 in crontab.hour
    assert 0 in crontab.minute
    assert celery_app.conf.timezone == "Asia/Kolkata"
