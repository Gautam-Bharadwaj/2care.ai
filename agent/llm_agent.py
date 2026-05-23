"""LLM agent definition: system prompt, Groq LLM, and Agent builder."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from livekit.agents import Agent, function_tool
from livekit.plugins import groq

from agent.language import LANG_CONFIG, DEFAULT_LANGUAGE
from agent.tools import (
    book_appointment,
    cancel_appointment,
    find_alternatives,
    find_patient_appointments,
    list_available_slots,
    recall_memory,
    record_campaign_outcome,
    reschedule_appointment,
)

LLM_MODEL = "llama-3.3-70b-versatile"


# Structured for latency: Groq TTFT scales with prompt tokens. Keeps the
# full receptionist playbook (personality + booking flow) without dumping
# every few-shot from the product spec into the prompt.
SYSTEM_PROMPT = """\
You are a warm, human clinic receptionist on a live phone call (2careAi).
Never sound robotic, scripted, or like an IVR. Short, natural replies —
one or two sentences, one question at a time.

Personality: polite, calm, caring, trustworthy. Female receptionist —
Hindi/Marathi feminine forms (कर रही हूँ); natural feminine Tamil speech.
Light fillers when natural: "okay", "sure", "got it", "alright", "one
second", "lemme check" (or natural equivalents in Indic languages).
Never repeat the same opener every turn. Never claim you are AI unless asked.
No medical advice — defer clinical questions to the doctor.

Language (this call: {language_name}):
- Primary language: {language_name}. Match how the caller speaks — if they
  use Hinglish (Hindi+English mix), reply similarly; if pure native script,
  use that script.
- Re-render tool results (doctors, times) in the caller's style — never read
  raw English JSON aloud.
- Switch language only when the runtime appends "[Runtime] Language is now
  locked to …" after the caller uses another supported language for 2 turns.
- Times as spoken words (सुबह नौ बजे / காலை ஒன்பது மணி), not "9:00 AM".

Booking flow (skip steps they already answered):
1) Need — doctor/specialty. If they said "dentist tomorrow", don't re-ask why.
2) Date — kal/tomorrow/Monday/evening/weekend.
3) Time — morning vs evening; offer two slots when helpful.
4) list_available_slots / find_alternatives — only IDs from tools.
5) Name/phone if missing.
6) Read back doctor+date+time; clear "yes" before book_appointment.

Unavailable slot: "Actually woh slot booked hai — 6 PM ya 7:30 available hai."
Confirm: "Okay Rahul ji, kal 5 PM Dr. Sharma ke saath — confirm kar doon?"
After book: warm confirmation + thanks.

Reschedule → reschedule_appointment. Cancel → confirm, then cancel_appointment.
Follow-up/outbound: ask how they feel; offer follow-up only if relevant.
Angry caller: brief apology, stay calm, fix fast. Confused: "No worries, main
help kar deti hoon." Interruptions: continue — don't restart the whole call.

recall_memory(query) for prior notes. record_campaign_outcome before ending
outbound campaign calls.
"""


def _context_block(patient_context: dict[str, Any]) -> str:
    """Format per-caller context (identity + recalled memories) for the prompt."""
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    lines = ["", "Patient context:", f"- today: {today} (UTC)"]
    if patient_context.get("phone"):
        lines.append(
            f"- patient_phone: {patient_context['phone']}  "
            "(pass this to tools that need patient_phone)"
        )
    if patient_context.get("patient_name"):
        lines.append(f"- caller_name: {patient_context['patient_name']}")
    if patient_context.get("preferred_language"):
        lines.append(
            f"- preferred_language: {patient_context['preferred_language']}"
        )

    recent = patient_context.get("recent_appointments") or []
    if recent:
        lines.append("- recent_appointments:")
        for appt in recent[:3]:
            lines.append(f"    · {appt}")

    memories = patient_context.get("relevant_memories") or []
    if memories:
        lines.append("- prior notes:")
        for note in memories:
            lines.append(f"    · {note}")

    return "\n".join(lines)


def build_llm() -> groq.LLM:
    """Construct the Groq LLM with tool calling enabled."""
    return groq.LLM(model=LLM_MODEL, tool_choice="auto")


_LIVEKIT_TOOLS = [
    function_tool(list_available_slots),
    function_tool(book_appointment),
    function_tool(reschedule_appointment),
    function_tool(cancel_appointment),
    function_tool(find_patient_appointments),
    function_tool(find_alternatives),
    function_tool(recall_memory),
    function_tool(record_campaign_outcome),
]


def _language_name(code: str | None) -> str:
    entry = LANG_CONFIG.get(code or DEFAULT_LANGUAGE) or LANG_CONFIG[DEFAULT_LANGUAGE]
    return entry.display_name


def build_agent(
    patient_context: dict[str, Any],
    *,
    campaign_suffix: str | None = None,
) -> Agent:
    """Build a LiveKit Agent with system prompt + per-caller context + tools.

    `campaign_suffix` is appended verbatim to the base prompt when the call
    is an outbound campaign — see `agent/campaigns.py::system_prompt_suffix`.
    """
    lang_name = _language_name(patient_context.get("preferred_language"))
    instructions = SYSTEM_PROMPT.format(language_name=lang_name) + "\n" + _context_block(
        patient_context
    )
    if campaign_suffix:
        instructions += campaign_suffix
    return Agent(instructions=instructions, tools=_LIVEKIT_TOOLS)
