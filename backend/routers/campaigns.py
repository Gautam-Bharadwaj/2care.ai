"""Campaign scheduling + outcome endpoints.

Two write surfaces and one read surface:

- `POST /campaigns/reminders/schedule` — schedule 24h-before reminder
  calls for a batch of appointments (or a date range).
- `POST /campaigns/followups/schedule` — schedule 48h-after follow-up
  calls.
- `GET  /campaigns/jobs` — list jobs, filterable by status/kind, with
  the recorded outcome inlined.
- `POST /campaigns/jobs/{id}/outcome` — used by the agent at call end
  to record `confirmed` / `voicemail` / etc. Idempotent: re-posting the
  same outcome is a no-op; posting a different one updates.

The actual dialing happens in `workers/outbound.py`. The scheduling
endpoints just write `CampaignJob` rows with `status=pending` and hand
them to Celery via `apply_async(eta=…)`. Celery beat sweeps any rows
the scheduler missed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.models import (
    Appointment,
    AppointmentStatus,
    AvailabilitySlot,
    CampaignJob,
    CampaignJobStatus,
    CampaignKind,
    CampaignOutcome,
)
from backend.db.session import get_db
from backend.schemas.campaign import (
    CampaignJobRead,
    CampaignOutcomeWrite,
    CampaignScheduleRequest,
    CampaignScheduleResponse,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# How long before / after the appointment we place the call.
REMINDER_OFFSET = timedelta(hours=-24)  # 24h before
FOLLOWUP_OFFSET = timedelta(hours=48)   # 48h after


async def _appointments_in_range(
    db: AsyncSession, start: date, end: date
) -> list[Appointment]:
    """Scheduled appointments whose start_time falls in [start, end+1d)."""
    if end < start:
        raise HTTPException(status_code=400, detail="end_date < start_date")
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    stmt = (
        select(Appointment)
        .join(AvailabilitySlot, Appointment.slot_id == AvailabilitySlot.id)
        .where(
            Appointment.status == AppointmentStatus.scheduled,
            AvailabilitySlot.start_time >= start_dt,
            AvailabilitySlot.start_time < end_dt,
        )
        .options(selectinload(Appointment.slot))
    )
    return list((await db.execute(stmt)).scalars().all())


async def _resolve_target_appointments(
    db: AsyncSession, payload: CampaignScheduleRequest
) -> list[Appointment]:
    if payload.appointment_ids:
        stmt = (
            select(Appointment)
            .where(Appointment.id.in_(payload.appointment_ids))
            .options(selectinload(Appointment.slot))
        )
        return list((await db.execute(stmt)).scalars().all())
    if payload.start_date and payload.end_date:
        return await _appointments_in_range(db, payload.start_date, payload.end_date)
    raise HTTPException(
        status_code=400,
        detail="provide either appointment_ids or start_date+end_date",
    )


async def _schedule_campaign_batch(
    db: AsyncSession,
    appointments: list[Appointment],
    kind: CampaignKind,
    offset: timedelta,
) -> CampaignScheduleResponse:
    """Create CampaignJob rows + dispatch them to Celery.

    Skips appointments that already have a job of this kind — the
    `uq_campaign_appt_kind` constraint guarantees one job per
    (appointment, kind) and we don't want duplicate calls."""
    # Pre-load existing jobs for this kind so we can skip duplicates without
    # one round-trip per appointment.
    existing_stmt = select(CampaignJob.appointment_id).where(
        CampaignJob.appointment_id.in_([a.id for a in appointments]),
        CampaignJob.kind == kind,
    )
    existing_ids = set((await db.execute(existing_stmt)).scalars().all())

    created: list[CampaignJob] = []
    skipped = 0
    for appt in appointments:
        if appt.id in existing_ids:
            skipped += 1
            continue
        if appt.slot is None:
            skipped += 1
            continue
        scheduled_for = appt.slot.start_time + offset
        job = CampaignJob(
            appointment_id=appt.id,
            kind=kind,
            scheduled_for=scheduled_for,
        )
        db.add(job)
        created.append(job)

    await db.commit()
    for job in created:
        await db.refresh(job)

    # Hand off to Celery. We import here so the FastAPI process doesn't pull
    # in the broker connection at module import time (tests run without a
    # live Redis).
    from workers.outbound import place_followup_call, place_reminder_call

    dispatch = {
        CampaignKind.reminder: place_reminder_call,
        CampaignKind.followup: place_followup_call,
    }[kind]

    for job in created:
        eta = job.scheduled_for
        if eta.tzinfo is None:
            eta = eta.replace(tzinfo=timezone.utc)
        # If the ETA has already passed (e.g. backfilling old appointments),
        # let Celery run it immediately instead of in the past — Celery
        # tolerates either but a past ETA logs warnings.
        async_result = dispatch.apply_async(args=[str(job.id)], eta=eta)
        job.status = CampaignJobStatus.queued
        job.celery_task_id = async_result.id
    if created:
        await db.commit()

    return CampaignScheduleResponse(
        scheduled=len(created),
        skipped=skipped,
        job_ids=[job.id for job in created],
    )


@router.post("/reminders/schedule", response_model=CampaignScheduleResponse)
async def schedule_reminders(
    payload: CampaignScheduleRequest, db: AsyncSession = Depends(get_db)
):
    appointments = await _resolve_target_appointments(db, payload)
    return await _schedule_campaign_batch(
        db, appointments, CampaignKind.reminder, REMINDER_OFFSET
    )


@router.post("/followups/schedule", response_model=CampaignScheduleResponse)
async def schedule_followups(
    payload: CampaignScheduleRequest, db: AsyncSession = Depends(get_db)
):
    appointments = await _resolve_target_appointments(db, payload)
    return await _schedule_campaign_batch(
        db, appointments, CampaignKind.followup, FOLLOWUP_OFFSET
    )


@router.get("/jobs", response_model=list[CampaignJobRead])
async def list_jobs(
    status: CampaignJobStatus | None = Query(default=None),
    kind: CampaignKind | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(CampaignJob)
        .options(selectinload(CampaignJob.outcome))
        .order_by(CampaignJob.scheduled_for.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(CampaignJob.status == status)
    if kind is not None:
        stmt = stmt.where(CampaignJob.kind == kind)
    return list((await db.execute(stmt)).scalars().all())


@router.get("/jobs/{job_id}", response_model=CampaignJobRead)
async def get_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(CampaignJob)
        .where(CampaignJob.id == job_id)
        .options(selectinload(CampaignJob.outcome))
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="campaign job not found")
    return job


@router.post("/jobs/{job_id}/outcome", response_model=CampaignJobRead)
async def record_outcome(
    job_id: UUID,
    payload: CampaignOutcomeWrite,
    db: AsyncSession = Depends(get_db),
):
    """Idempotent outcome recording. Called by the agent on call end."""
    stmt = (
        select(CampaignJob)
        .where(CampaignJob.id == job_id)
        .options(selectinload(CampaignJob.outcome))
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="campaign job not found")

    if job.outcome is None:
        outcome = CampaignOutcome(
            job_id=job.id, outcome=payload.outcome, notes=payload.notes
        )
        db.add(outcome)
        job.outcome = outcome
    else:
        job.outcome.outcome = payload.outcome
        job.outcome.notes = payload.notes
        job.outcome.recorded_at = datetime.now(timezone.utc)

    job.status = CampaignJobStatus.completed
    await db.commit()
    await db.refresh(job)
    return job
