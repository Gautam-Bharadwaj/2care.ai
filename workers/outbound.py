"""Celery tasks that place outbound campaign calls.

Each task:

  1. Loads the `CampaignJob` row and the underlying appointment / patient.
  2. Marks the job `in_progress` and records the LiveKit room name.
  3. Creates a LiveKit SIP participant via the configured outbound trunk,
     passing `participant_metadata` that carries the campaign type +
     appointment ID. The agent reads this metadata on join (see
     `agent/main.py::_extract_campaign_metadata`) and picks the matching
     opening line + records the outcome on call end.
  4. Returns the LiveKit participant identity for traceability.

If the dial fails (Twilio unreachable, trunk misconfigured) the job is
flipped to `failed` and `last_error` captured. We don't retry inside the
task — the daily beat sweep picks up any `pending` rows whose
`scheduled_for` has already passed, so transient outages self-heal on the
next sweep.

The opening prompts live in `agent/campaigns.py`. We don't pass the
prompt over SIP — the agent looks it up from its own table keyed on the
`campaign_type` field of the metadata.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from livekit import api
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.db.models import (
    Appointment,
    AppointmentStatus,
    AvailabilitySlot,
    CampaignJob,
    CampaignJobStatus,
    CampaignKind,
    CampaignOutcomeKind,
    Doctor,
    Patient,
)
from backend.db.session import SessionLocal
from config import get_settings
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy task (kept for the inbound test harness)
# ---------------------------------------------------------------------------


@celery_app.task(name="twocare.outbound_call")
def outbound_call(to_phone: str, room_name: str) -> dict:
    """Dial `to_phone` and bridge them into `room_name` via LiveKit SIP."""
    return asyncio.run(_dial(to_phone, room_name, metadata=None))


# ---------------------------------------------------------------------------
# Campaign tasks
# ---------------------------------------------------------------------------


@celery_app.task(name="twocare.place_reminder_call", bind=True)
def place_reminder_call(self, job_id: str) -> dict:
    """24h-before reminder call. See module docstring for control flow."""
    return asyncio.run(_run_campaign_job(job_id, CampaignKind.reminder))


@celery_app.task(name="twocare.place_followup_call", bind=True)
def place_followup_call(self, job_id: str) -> dict:
    """48h-after post-visit follow-up call."""
    return asyncio.run(_run_campaign_job(job_id, CampaignKind.followup))


@celery_app.task(name="twocare.sweep_reminder_window")
def sweep_reminder_window() -> dict:
    """Periodic 9am IST sweep: schedule reminders for all appointments 24h out.

    Triggered by the beat schedule in `workers.celery_app`. Idempotent:
    `uq_campaign_appt_kind` blocks duplicate jobs, so re-running the sweep
    on the same day is a no-op for already-scheduled appointments.
    """
    return asyncio.run(_sweep_reminder_window())


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


async def _run_campaign_job(job_id: str, expected_kind: CampaignKind) -> dict:
    async with SessionLocal() as db:
        stmt = (
            select(CampaignJob)
            .where(CampaignJob.id == uuid.UUID(job_id))
            .options(
                selectinload(CampaignJob.outcome),
            )
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if job is None:
            logger.error("campaign_job_not_found job_id=%s", job_id)
            return {"ok": False, "reason": "job_not_found"}
        if job.kind != expected_kind:
            logger.error(
                "campaign_job_kind_mismatch job_id=%s expected=%s got=%s",
                job_id,
                expected_kind,
                job.kind,
            )
            return {"ok": False, "reason": "kind_mismatch"}
        if job.status in (CampaignJobStatus.in_progress, CampaignJobStatus.completed):
            logger.info("campaign_job_already_running job_id=%s", job_id)
            return {"ok": True, "reason": "already_running"}

        # Load the appointment + patient + doctor + slot for metadata.
        appt_stmt = (
            select(Appointment)
            .where(Appointment.id == job.appointment_id)
            .options(
                selectinload(Appointment.patient),
                selectinload(Appointment.doctor),
                selectinload(Appointment.slot),
            )
        )
        appt = (await db.execute(appt_stmt)).scalar_one_or_none()
        if appt is None:
            job.status = CampaignJobStatus.failed
            job.last_error = "appointment_missing"
            await db.commit()
            return {"ok": False, "reason": "appointment_missing"}

        # Cancelled-since-scheduled? Don't bother the patient.
        if (
            job.kind == CampaignKind.reminder
            and appt.status != AppointmentStatus.scheduled
        ):
            job.status = CampaignJobStatus.completed
            job.last_error = f"skipped: appointment status={appt.status.value}"
            await _record_outcome_inline(
                db, job, CampaignOutcomeKind.other, "skipped: not scheduled"
            )
            await db.commit()
            return {"ok": True, "reason": "skipped_not_scheduled"}

        room_name = f"campaign-{job.kind.value}-{job.id.hex[:8]}"
        metadata = {
            "campaign_type": job.kind.value,
            "appointment_id": str(appt.id),
            "campaign_job_id": str(job.id),
            "patient_phone": appt.patient.phone,
            "patient_name": appt.patient.name,
            "doctor_name": appt.doctor.name,
            "appointment_start": appt.slot.start_time.isoformat(),
        }

        job.status = CampaignJobStatus.in_progress
        job.room_name = room_name
        job.attempts += 1
        await db.commit()

        try:
            result = await _dial(
                to_phone=appt.patient.phone,
                room_name=room_name,
                metadata=metadata,
            )
        except Exception as e:  # noqa: BLE001 — Twilio/LiveKit can raise anything
            logger.exception("campaign_dial_failed job_id=%s", job_id)
            job.status = CampaignJobStatus.failed
            job.last_error = str(e)[:1000]
            await _record_outcome_inline(
                db, job, CampaignOutcomeKind.no_answer, f"dial_failed: {e!r}"[:500]
            )
            await db.commit()
            return {"ok": False, "reason": "dial_failed", "error": str(e)}

        await db.commit()
        return {
            "ok": True,
            "job_id": job_id,
            "room_name": room_name,
            "participant_identity": result.get("participant_identity"),
        }


async def _record_outcome_inline(
    db, job: CampaignJob, kind: CampaignOutcomeKind, notes: str
) -> None:
    """Used when the worker itself needs to record an outcome (dial failure,
    skipped-because-cancelled). For agent-driven outcomes, the agent POSTs
    to `/campaigns/jobs/{id}/outcome` instead."""
    from backend.db.models import CampaignOutcome

    existing = job.outcome
    if existing is None:
        db.add(CampaignOutcome(job_id=job.id, outcome=kind, notes=notes))
    else:
        existing.outcome = kind
        existing.notes = notes
        existing.recorded_at = datetime.now(timezone.utc)


async def _dial(to_phone: str, room_name: str, metadata: dict | None) -> dict:
    settings = get_settings()
    if not settings.livekit_sip_trunk_id:
        raise RuntimeError("LIVEKIT_SIP_TRUNK_ID is not configured")

    async with api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    ) as lkapi:
        # Pre-create the room so we can stamp room-level metadata too —
        # belt-and-suspenders for the agent in case participant.metadata is
        # missing on join (e.g., the participant joins before we patch it).
        if metadata is not None:
            try:
                await lkapi.room.create_room(
                    api.CreateRoomRequest(
                        name=room_name, metadata=json.dumps(metadata)
                    )
                )
            except Exception:  # noqa: BLE001
                # Room may already exist if a retry — fine, the metadata
                # from the original create wins.
                logger.debug("room_create_existed room=%s", room_name)

        request = api.CreateSIPParticipantRequest(
            sip_trunk_id=settings.livekit_sip_trunk_id,
            sip_call_to=to_phone,
            room_name=room_name,
            participant_identity=f"caller_{to_phone}",
            participant_name=to_phone,
            participant_metadata=json.dumps(metadata) if metadata else "",
            wait_until_answered=True,
        )
        result = await lkapi.sip.create_sip_participant(request)
        logger.info("outbound call started to=%s room=%s", to_phone, room_name)
        return {
            "participant_identity": result.participant_identity,
            "room": room_name,
        }


async def _sweep_reminder_window() -> dict:
    """Find all scheduled appointments starting in [now+23h, now+25h] and
    schedule reminder calls for them. The ±1h window absorbs clock drift
    between the beat scheduler and per-appointment ETAs.

    Calls the backend HTTP route rather than going to the DB directly so
    the scheduling logic stays in one place (`backend/routers/campaigns.py`).
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    start = (now + _SWEEP_LOOKAHEAD_MIN).date()
    end = (now + _SWEEP_LOOKAHEAD_MAX).date()

    async with httpx.AsyncClient(base_url=settings.backend_url, timeout=30) as c:
        r = await c.post(
            "/campaigns/reminders/schedule",
            json={"start_date": start.isoformat(), "end_date": end.isoformat()},
        )
    if r.status_code != 200:
        logger.error("sweep_failed status=%s body=%s", r.status_code, r.text[:200])
        return {"ok": False, "status": r.status_code}
    body = r.json()
    logger.info(
        "reminder_sweep done scheduled=%s skipped=%s",
        body.get("scheduled"),
        body.get("skipped"),
    )
    return {"ok": True, **body}


from datetime import timedelta as _td

# Sweep window: appointments ~24h from now. ±1h buffer so we catch every
# appointment regardless of when the beat tick lands (9:00am IST exactly
# is a fiction in distributed schedulers).
_SWEEP_LOOKAHEAD_MIN = _td(hours=23)
_SWEEP_LOOKAHEAD_MAX = _td(hours=25)
