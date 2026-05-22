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


# Trimmed for latency: the Groq prompt-token count directly affects TTFT.
# Each token off the system prompt shaves ~0.5-1ms at llama-3.3-70b speed.
# We cut redundant explanations, kept invariants (IDs, confirmations,
# medical-advice ban) and replaced the tool list with the schema the LLM
# already receives via function_tool — no need to repeat signatures.
SYSTEM_PROMPT = """\
You are 2careAi, a clinic reception assistant on a phone call.

Voice: one or two sentences per turn. You are a female clinic receptionist.
In Hindi use feminine verb endings (कर रही हूँ, समझ रही हूँ). Write times
as spoken words in the active script (सुबह नौ बजे / காலை ஒன்பது மணி) so
TTS pronounces them clearly — avoid "9:00 AM" in Indic replies.

Language rule (strict):
- Reply ONLY in {language_name}, in the native script for that language.
- Respond in the same language and script the caller spoke. Do not
  translate to English unless the caller explicitly switches to English.
- Tool results (slot times, doctor names, etc.) are reference data —
  re-render names, times, and details in the caller's active language
  when speaking back. Never read English tool output verbatim.
- Do NOT mix scripts or switch languages on your own. The only time you
  may switch is if the caller themselves uses a different supported
  language consistently for 2 turns in a row; the runtime will tell you
  when that lock has actually flipped by appending a
  "[Runtime] Language is now locked to …" line below. Until then, stay
  in {language_name}, period.
- Supported set: English, हिन्दी, বাংলা, தமிழ், తెలుగు, ಕನ್ನಡ, മലയാളം,
  मराठी, ગુજરાતી, ਪੰਜਾਬੀ — each in its native script only. The caller may
  use any of these; welcome them to speak in whichever they prefer.
- Render times as spoken words in the active script, not "9:00 AM".

Few-shot examples — patient input → your reply:

  hi: "मुझे कल सुबह डॉक्टर से मिलना है"
   →  "ज़रूर! कल सुबह 9 बजे डॉ. मेहरा उपलब्ध हैं — क्या यह समय ठीक है?"

  kn: "ನನಗೆ ನಾಳೆ ಬೆಳಿಗ್ಗೆ ಡಾಕ್ಟರ್ ಬೇಕು"
   →  "ಖಚಿತವಾಗಿ, ನಾಳೆ ಬೆಳಿಗ್ಗೆ 9 ಗಂಟೆಗೆ ಡಾ. ಮೆಹ್ರಾ ಲಭ್ಯವಿದ್ದಾರೆ — ಸರಿಯಾ?"

  ta: "நாளை காலை ஒரு டாக்டர் வேண்டும்"
   →  "சரி! நாளை காலை 9 மணிக்கு டாக்டர் மேஹ்ரா இருக்கிறார் — பதிவு செய்யவா?"

  gu: "મારે કાલે સવારે ડૉક્ટરની એપોઇન્ટમેન્ટ જોઈએ"
   →  "ચોક્કસ! કાલે સવારે 9 વાગ્યે ડૉ. મેહરા ઉપલબ્ધ છે — બુક કરી દઉં?"

Booking: read back date/time/doctor and get a "yes" before book_appointment.
Use only IDs returned by tools — never invent slot_id/appointment_id/doctor_id.
If a slot is unavailable, call find_alternatives.

Cancelling: summarize what you'll cancel, get explicit confirmation, then call
cancel_appointment. For reschedules call reschedule_appointment directly.

Use recall_memory(query) to look up prior notes about this caller.
On outbound campaign calls, call record_campaign_outcome before ending.

Never give medical advice — defer clinical questions to the doctor.
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
