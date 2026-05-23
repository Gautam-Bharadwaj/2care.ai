"""Campaign-aware opening prompts + system-prompt additions for outbound calls.

When an outbound campaign call connects, the agent reads
`participant.metadata` (set by `workers.outbound._dial`) to learn:

  - `campaign_type`: 'reminder' or 'followup'
  - `appointment_id`: which appointment this call is about
  - `campaign_job_id`: the CampaignJob row to record an outcome against
  - `patient_phone`, `patient_name`, `doctor_name`, `appointment_start`

This module exposes:

  - `parse_metadata(raw)` → CampaignMetadata | None
  - `opening_line(meta, *, lang)` → first thing the agent says
  - `system_prompt_suffix(meta)` → bullet-point instructions appended to the
    base SYSTEM_PROMPT so the LLM knows it's making an outbound call (not
    receiving one) and what outcomes to capture.

The opening lines are intentionally short and end with an open question so
the patient drives the rest of the conversation through the existing tool
suite (book/reschedule/cancel).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)


CampaignType = Literal["reminder", "followup"]


@dataclass(frozen=True)
class CampaignMetadata:
    campaign_type: CampaignType
    appointment_id: str
    campaign_job_id: str
    patient_name: str | None
    doctor_name: str | None
    appointment_start: str | None  # ISO 8601


def parse_metadata(raw: str | None) -> CampaignMetadata | None:
    """Parse the JSON blob LiveKit hands us on the participant.

    Returns None for inbound calls (no metadata) or malformed payloads —
    the caller treats None as "this is a normal inbound call, use the
    default opening".
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("campaign_metadata_unparseable raw=%r", raw[:200])
        return None
    if not isinstance(data, dict):
        return None
    ctype = data.get("campaign_type")
    if ctype not in ("reminder", "followup"):
        return None
    appt_id = data.get("appointment_id")
    job_id = data.get("campaign_job_id")
    if not appt_id or not job_id:
        return None
    return CampaignMetadata(
        campaign_type=ctype,  # type: ignore[arg-type]
        appointment_id=str(appt_id),
        campaign_job_id=str(job_id),
        patient_name=_str_or_none(data.get("patient_name")),
        doctor_name=_str_or_none(data.get("doctor_name")),
        appointment_start=_str_or_none(data.get("appointment_start")),
    )


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _format_time(iso: str | None) -> str:
    """Format an ISO timestamp into a colloquial '3:30pm on Saturday'.

    Falls back to the raw string if parsing fails — better to say something
    awkward than to drop the time entirely."""
    if not iso:
        return "your scheduled time"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return dt.strftime("%-I:%M %p on %A").lstrip("0")


def opening_line(meta: CampaignMetadata) -> str:
    """The first sentence the agent says on the call.

    Kept short and natural — the rest of the conversation runs through the
    standard tool suite once the patient responds.
    """
    name = meta.patient_name or "there"
    doc = meta.doctor_name or "your doctor"
    when = _format_time(meta.appointment_start)

    if meta.campaign_type == "reminder":
        return (
            f"Hi {name}, this is a reminder for your appointment with "
            f"Dr. {doc} tomorrow at {when}. Are you able to make it?"
        )
    # followup
    return (
        f"Hi {name}, this is a quick follow-up from the clinic. "
        f"How are you feeling after your visit with Dr. {doc}?"
    )


def system_prompt_suffix(meta: CampaignMetadata) -> str:
    """Extra instructions to append to the SYSTEM_PROMPT for this call.

    The LLM needs to know two things that change for outbound campaigns:

    1. The agent placed this call, not the patient — so the opening
       intent is fixed.
    2. We need a structured outcome at the end. The agent's last action
       before the call ends should be to call `record_campaign_outcome`
       (added in `agent/tools.py`) with one of the canonical outcomes.
    """
    if meta.campaign_type == "reminder":
        purpose = (
            "Confirm whether the patient will attend the appointment. If they "
            "want to reschedule, use reschedule_appointment. If they want to "
            "cancel, use cancel_appointment. If they confirm attendance, "
            "thank them and end the call."
        )
        outcomes = (
            "confirmed — patient will attend\n"
            "rescheduled — patient picked a new slot during this call\n"
            "cancelled — patient cancelled the appointment\n"
            "rejected — patient asked not to be called again\n"
            "voicemail — answering machine picked up\n"
            "other — anything else"
        )
    else:  # followup
        purpose = (
            "Check on the patient's recovery and capture anything notable "
            "as a durable memory (use the existing recall_memory pattern in "
            "reverse — let the post-call summarizer pick up the transcript)."
        )
        outcomes = (
            "confirmed — patient is doing fine, no concerns\n"
            "rescheduled — booked a follow-up visit during this call\n"
            "cancelled — n/a, but use 'other' with notes for unusual cases\n"
            "rejected — patient asked not to be called again\n"
            "voicemail — answering machine picked up\n"
            "other — recovery concerns, complaints, or anything escalation-worthy"
        )

    return (
        "\n\n[Campaign call — outbound]\n"
        f"- Call type: {meta.campaign_type}\n"
        f"- Appointment id: {meta.appointment_id}\n"
        f"- Doctor: {meta.doctor_name or '(unknown)'}\n"
        f"- Appointment time: {meta.appointment_start or '(unknown)'}\n"
        f"- Purpose: {purpose}\n"
        f"- Before ending the call, you MUST call record_campaign_outcome "
        f"with one of these outcomes:\n{outcomes}\n"
        "- Keep this short — patients did not expect a call. Two-three "
        "exchanges is ideal."
    )
