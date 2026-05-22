"""LLM tool functions.

Each tool calls the FastAPI backend over HTTP and returns a human-readable
string. Backend errors are caught and turned into short natural-language
messages the LLM can use to recover.

`@traced` logs every call (name, args, latency_ms, success/error,
response preview) via structlog — this is our reasoning trace.

Tools that need patient or session context read it from ContextVars set
by the entrypoint at session start (`set_patient_context`,
`set_session_memory`). This keeps tool signatures clean for the LLM:
`recall_memory(query)` instead of `recall_memory(patient_id, query)`.
"""
from __future__ import annotations

import contextvars
import functools
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx
import structlog

from config import get_settings

if TYPE_CHECKING:
    from agent.memory import SessionMemory

log = structlog.get_logger("agent.tools")


# Per-call context — set by the entrypoint before the LLM runs.
_patient_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "patient_context", default=None
)
_session_memory: contextvars.ContextVar["SessionMemory | None"] = contextvars.ContextVar(
    "session_memory", default=None
)
# Campaign metadata (only set on outbound campaign calls). None for normal
# inbound calls — `record_campaign_outcome` returns an error in that case so
# the LLM doesn't accidentally try to record outcomes on inbound chats.
_campaign_meta: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "campaign_meta", default=None
)


def set_patient_context(ctx: dict | None) -> None:
    """Inject the current caller's identity for tools to read.

    `ctx` keys: id (UUID str), phone, name, preferred_language.
    """
    _patient_context.set(ctx)


def set_session_memory(memory: "SessionMemory | None") -> None:
    _session_memory.set(memory)


def set_campaign_meta(meta: dict | None) -> None:
    """Inject the campaign job context. None for non-campaign calls."""
    _campaign_meta.set(meta)


def get_campaign_meta() -> dict | None:
    return _campaign_meta.get()


# Reasoning-trace buffer. The TraceCollector (agent/traces.py) sets a
# fresh list on this ContextVar at the start of every turn; the `traced`
# decorator below appends one record per tool invocation. Setting None
# (or never setting it) disables capture — safe for tests and for the
# pre-Agent-init phase of the entrypoint.
_trace_tool_calls: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "trace_tool_calls", default=None
)
_trace_memory_recalls: contextvars.ContextVar[
    list[dict] | None
] = contextvars.ContextVar("trace_memory_recalls", default=None)


def set_trace_buffers(
    tool_calls: list[dict] | None, memory_recalls: list[dict] | None
) -> None:
    """Bind per-turn buffers so the `traced` decorator can record into them.

    Pass `(None, None)` to disable capture (the default state)."""
    _trace_tool_calls.set(tool_calls)
    _trace_memory_recalls.set(memory_recalls)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=get_settings().backend_url, timeout=15)


def traced(fn):
    """Log tool invocation: name, args, latency_ms, status, response preview.

    Also pushes a structured record into the active turn's trace buffer
    (see `set_trace_buffers`). Capture is a no-op when no buffer is bound.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            result = await fn(*args, **kwargs)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            log.info(
                "tool_call",
                tool=fn.__name__,
                args=kwargs,
                latency_ms=elapsed_ms,
                status="ok",
                response=str(result)[:200],
            )
            _record_to_trace(
                name=fn.__name__,
                args=kwargs,
                result=str(result),
                latency_ms=elapsed_ms,
                status="ok",
                started_at=started_at,
            )
            return result
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            log.error(
                "tool_call",
                tool=fn.__name__,
                args=kwargs,
                latency_ms=elapsed_ms,
                status="error",
                err=f"{type(e).__name__}: {e}",
            )
            _record_to_trace(
                name=fn.__name__,
                args=kwargs,
                result=f"{type(e).__name__}: {e}",
                latency_ms=elapsed_ms,
                status="error",
                started_at=started_at,
            )
            return f"Internal error in {fn.__name__}: {e}. Try a different approach."

    return wrapper


def _record_to_trace(
    *,
    name: str,
    args: dict,
    result: str,
    latency_ms: float,
    status: str,
    started_at: str,
) -> None:
    """Append a tool-call record (and, for recall_memory, a memory_recall
    record) to whatever per-turn buffers are bound on the ContextVars."""
    buf = _trace_tool_calls.get()
    if buf is not None:
        buf.append(
            {
                "name": name,
                "args": _safe_args(args),
                "result": result[:1500],
                "latency_ms": latency_ms,
                "status": status,
                "started_at": started_at,
            }
        )

    # recall_memory is a tool call AND a semantic-memory lookup — record
    # both so the trace viewer can render memory lookups as a separate
    # affordance from "the agent picked up the phone".
    if name == "recall_memory":
        mbuf = _trace_memory_recalls.get()
        if mbuf is not None:
            mbuf.append(
                {
                    "query": str(args.get("query", "")),
                    "returned_count": _count_memory_results(result),
                    "latency_ms": latency_ms,
                    "results": _extract_memory_lines(result),
                }
            )


def _safe_args(args: dict) -> dict:
    """Truncate long values so the trace JSON stays render-able."""
    out: dict = {}
    for k, v in args.items():
        s = str(v)
        out[k] = s if len(s) <= 500 else s[:500] + "…"
    return out


def _count_memory_results(result: str) -> int:
    """recall_memory returns 'Prior notes:\\n- a\\n- b' or 'No prior notes...'."""
    if not result or "No prior notes" in result:
        return 0
    return sum(1 for line in result.splitlines() if line.startswith("- "))


def _extract_memory_lines(result: str) -> list[str]:
    if not result or "No prior notes" in result:
        return []
    return [
        line[2:].strip()
        for line in result.splitlines()
        if line.startswith("- ")
    ]


def _fmt_when(iso_or_dt: str | datetime) -> str:
    """Format ISO datetime as 'Tue Jun 4, 10:00 AM'."""
    if isinstance(iso_or_dt, str):
        dt = datetime.fromisoformat(iso_or_dt.replace("Z", "+00:00"))
    else:
        dt = iso_or_dt
    return dt.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def _parse_iso_date(s: str) -> datetime | None:
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _ensure_patient(c: httpx.AsyncClient, phone: str) -> str | None:
    """Return patient_id, creating the patient if not found."""
    r = await c.get("/patients", params={"phone": phone})
    if r.status_code == 200 and r.json():
        return r.json()[0]["id"]
    r = await c.post(
        "/patients",
        json={"name": "Caller", "phone": phone, "preferred_language": "en"},
    )
    if r.status_code == 201:
        return r.json()["id"]
    return None


async def _maybe_session_set(**fields) -> None:
    mem = _session_memory.get()
    if mem is None:
        return
    try:
        await mem.update(**fields)
    except Exception as e:
        log.error("session_memory_write_failed", err=str(e))


# --- tools -----------------------------------------------------------------


@traced
async def list_available_slots(
    specialty: str | None = None,
    doctor_name: str | None = None,
    date: str | None = None,
) -> str:
    """List up to 5 available appointment slots, optionally filtered by
    specialty, doctor_name (substring match), or date (YYYY-MM-DD)."""
    now = datetime.now(timezone.utc)
    if date:
        target = _parse_iso_date(date)
        if target is None:
            return f"Could not parse date '{date}'. Use YYYY-MM-DD."
        date_from = target.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = date_from + timedelta(days=1)
    else:
        date_from = now
        date_to = now + timedelta(days=14)

    async with _client() as c:
        doctor_id: str | None = None
        if doctor_name:
            r = await c.get("/doctors", params={"name": doctor_name})
            if r.status_code != 200 or not r.json():
                return f"No doctor matched '{doctor_name}'."
            doctor_id = r.json()[0]["id"]

        params: dict = {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        }
        if doctor_id:
            params["doctor_id"] = doctor_id
        if specialty:
            params["specialty"] = specialty

        r = await c.get("/slots/available", params=params)
        if r.status_code != 200:
            return f"Could not fetch slots ({r.status_code}): {r.text[:200]}"
        slots = r.json()

    if not slots:
        return "No available slots match those criteria. Try a different date or specialty."

    if doctor_name and slots:
        await _maybe_session_set(last_doctor_mentioned=slots[0]["doctor_name"])

    top = slots[:5]
    lines = [f"Found {len(slots)} slot(s). Top {len(top)}:"]
    for s in top:
        when = _fmt_when(s["start_time"])
        lines.append(
            f"- slot_id={s['id']} | {when} | {s['doctor_name']} ({s['doctor_specialty']})"
        )
    return "\n".join(lines)


@traced
async def book_appointment(slot_id: str, patient_phone: str) -> str:
    """Book a slot for the caller. Looks up or creates the patient by phone."""
    async with _client() as c:
        patient_id = await _ensure_patient(c, patient_phone)
        if patient_id is None:
            return "Could not register the caller's phone with the system."

        r = await c.post(
            "/appointments",
            json={"patient_id": patient_id, "slot_id": slot_id},
        )
        if r.status_code == 201:
            appt = r.json()
            when = _fmt_when(appt["start_time"])
            await _maybe_session_set(
                current_intent="booked",
                pending_slot_id=None,
                last_doctor_mentioned=appt["doctor_name"],
                confirmation_pending=None,
            )
            return (
                f"Booked. appointment_id={appt['id']} for {appt['doctor_name']} "
                f"at {when}."
            )
        if r.status_code == 409:
            return (
                f"That slot is no longer available. "
                f"Call find_alternatives with slot_id={slot_id} to suggest nearby times."
            )
        if r.status_code == 400:
            detail = r.json().get("detail", r.text)
            return f"Cannot book this slot: {detail}"
        return f"Booking failed ({r.status_code}): {r.text[:200]}"


@traced
async def reschedule_appointment(appointment_id: str, new_slot_id: str) -> str:
    """Move an existing appointment to a new slot."""
    async with _client() as c:
        r = await c.patch(
            f"/appointments/{appointment_id}/reschedule",
            json={"new_slot_id": new_slot_id},
        )
        if r.status_code == 200:
            appt = r.json()
            when = _fmt_when(appt["start_time"])
            await _maybe_session_set(current_intent="rescheduled")
            return (
                f"Rescheduled appointment {appointment_id} to {when} with "
                f"{appt['doctor_name']}."
            )
        if r.status_code == 409:
            detail = r.json().get("detail", r.text)
            return (
                f"Cannot reschedule: {detail}. "
                f"Call find_alternatives with slot_id={new_slot_id} for nearby times."
            )
        if r.status_code == 404:
            return f"Appointment {appointment_id} not found."
        return f"Reschedule failed ({r.status_code}): {r.text[:200]}"


@traced
async def cancel_appointment(appointment_id: str, reason: str) -> str:
    """Cancel an existing appointment and free its slot."""
    async with _client() as c:
        r = await c.request(
            "DELETE",
            f"/appointments/{appointment_id}",
            json={"reason": reason},
        )
        if r.status_code == 200:
            await _maybe_session_set(current_intent="cancelled")
            return f"Cancelled appointment {appointment_id}. Reason recorded."
        if r.status_code == 404:
            return f"Appointment {appointment_id} not found."
        return f"Cancel failed ({r.status_code}): {r.text[:200]}"


@traced
async def find_patient_appointments(patient_phone: str) -> str:
    """List the caller's scheduled/cancelled/completed appointments."""
    async with _client() as c:
        r = await c.get("/patients", params={"phone": patient_phone})
        if r.status_code != 200 or not r.json():
            return f"No patient record for phone {patient_phone}."
        patient_id = r.json()[0]["id"]

        r = await c.get("/appointments", params={"patient_id": patient_id})
        if r.status_code != 200:
            return f"Could not fetch appointments ({r.status_code})."
        appts = r.json()

    if not appts:
        return f"No appointments on file for {patient_phone}."

    lines = [f"{len(appts)} appointment(s) for {patient_phone}:"]
    for a in appts:
        when = _fmt_when(a["start_time"])
        lines.append(
            f"- appointment_id={a['id']} | {when} | {a['doctor_name']} | "
            f"status={a['status']}"
        )
    return "\n".join(lines)


@traced
async def find_alternatives(slot_id: str) -> str:
    """Return up to 3 alternative slots near the given one."""
    async with _client() as c:
        r = await c.get(f"/slots/{slot_id}/alternatives", params={"count": 3})
        if r.status_code != 200:
            return f"Could not fetch alternatives ({r.status_code}): {r.text[:200]}"
        slots = r.json()

    if not slots:
        return f"No alternatives available near slot {slot_id}."

    lines = [f"{len(slots)} alternative(s):"]
    for s in slots:
        when = _fmt_when(s["start_time"])
        lines.append(
            f"- slot_id={s['id']} | {when} | {s['doctor_name']} ({s['doctor_specialty']})"
        )
    return "\n".join(lines)


@traced
async def recall_memory(query: str) -> str:
    """Look up prior durable notes about the current caller.

    Patient is implicit (set at session start). Returns up to 4 relevant
    notes ranked by semantic similarity to `query`.
    """
    ctx = _patient_context.get()
    if not ctx or not ctx.get("id"):
        return "No patient context available — cannot recall memories."

    async with _client() as c:
        r = await c.post(
            "/memory/recall",
            json={"patient_id": ctx["id"], "query": query, "k": 4},
        )
        if r.status_code != 200:
            return f"Could not recall memories ({r.status_code}): {r.text[:200]}"
        memories = r.json()

    if not memories:
        return "No prior notes for this caller."
    return "Prior notes:\n" + "\n".join(f"- {m}" for m in memories)


VALID_CAMPAIGN_OUTCOMES = {
    "confirmed",
    "rescheduled",
    "cancelled",
    "voicemail",
    "no_answer",
    "rejected",
    "other",
}


@traced
async def record_campaign_outcome(outcome: str, notes: str | None = None) -> str:
    """Record the result of an outbound campaign call.

    `outcome` must be one of: confirmed, rescheduled, cancelled, voicemail,
    no_answer, rejected, other. `notes` is free-text context for the
    clinical team's review (e.g., the new appointment time, the patient's
    reason for declining, anything escalation-worthy from a follow-up).

    Returns the recorded outcome. Idempotent — re-recording overwrites.
    Only valid on outbound campaign calls; returns an error string on
    normal inbound calls.
    """
    meta = _campaign_meta.get()
    if not meta or not meta.get("campaign_job_id"):
        return (
            "No campaign in progress — record_campaign_outcome is only for "
            "outbound reminder/follow-up calls."
        )
    if outcome not in VALID_CAMPAIGN_OUTCOMES:
        return (
            f"'{outcome}' is not a valid outcome. Use one of: "
            + ", ".join(sorted(VALID_CAMPAIGN_OUTCOMES))
        )
    async with _client() as c:
        r = await c.post(
            f"/campaigns/jobs/{meta['campaign_job_id']}/outcome",
            json={"outcome": outcome, "notes": notes},
        )
        if r.status_code != 200:
            return f"Could not record outcome ({r.status_code}): {r.text[:200]}"
    return f"Outcome recorded: {outcome}"


# --- export -----------------------------------------------------------------

PLAIN_TOOLS = {
    "list_available_slots": list_available_slots,
    "book_appointment": book_appointment,
    "reschedule_appointment": reschedule_appointment,
    "cancel_appointment": cancel_appointment,
    "find_patient_appointments": find_patient_appointments,
    "find_alternatives": find_alternatives,
    "recall_memory": recall_memory,
    "record_campaign_outcome": record_campaign_outcome,
}
