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


OPENINGS: dict[str, dict[str, str]] = {
    "en": {
        "reminder": "Hi {name}, this is a reminder for your appointment with Dr. {doc} tomorrow at {when}. Are you able to make it?",
        "followup": "Hi {name}, this is a quick follow-up from the clinic. How are you feeling after your visit with Dr. {doc}?"
    },
    "hi": {
        "reminder": "नमस्ते {name}, मैं 2careAi क्लिनिक से बोल रही हूँ। कल {when} डॉक्टर {doc} के साथ आपका अपॉइंटमेंट है। क्या आप आ पाएंगे?",
        "followup": "नमस्ते {name}, मैं 2careAi क्लिनिक से बोल रही हूँ। डॉक्टर {doc} से मिलने के बाद अब आप कैसा महसूस कर रहे हैं?"
    },
    "bn": {
        "reminder": "নমস্কার {name}, আমি 2careAi ক্লিনিক থেকে বলছি। আগামীকাল {when} ডাক্তার {doc}-এর সাথে আপনার অ্যাপয়েন্টমেন্ট আছে। আপনি কি আসতে পারবেন?",
        "followup": "নমস্কার {name}, আমি 2careAi ক্লিনিক থেকে বলছি। ডাক্তার {doc}-এর সাথে দেখা করার পর এখন আপনি কেমন বোধ করছেন?"
    },
    "ta": {
        "reminder": "வணக்கம் {name}, 2careAi கிளினிக்கில் இருந்து பேசுகிறேன். நாளை {when} டாக்டர் {doc} உடன் உங்கள் அப்பாயிண்ட்மெண்ட் உள்ளது. உங்களால் வர முடியுமா?",
        "followup": "வணக்கம் {name}, 2careAi கிளினிக்கில் இருந்து பேசுகிறேன். டாக்டர் {doc} உடனான உங்கள் சந்திப்பிற்குப் பிறகு இப்போது உடல்நிலை எப்படி இருக்கிறது?"
    },
    "te": {
        "reminder": "నమస్కారం {name}, నేను 2careAi క్లినిక్ నుండి మాట్లాడుతున్నాను. రేపు {when} డాక్టర్ {doc} గారితో మీ అపాయింట్‌మెంట్ ఉంది. మీరు రాగలరా?",
        "followup": "నమస్కారం {name}, నేను 2careAi క్లినిక్ నుండి మాట్లాడుతున్నాను. డాక్టర్ {doc} గారిని కలిసిన తర్వాత ఇప్పుడు మీకు ఎలా ఉంది?"
    },
    "kn": {
        "reminder": "ನಮಸ್ಕಾರ {name}, ನಾನು 2careAi ಕ್ಲಿನಿಕ್‌ನಿಂದ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ. ನಾಳೆ {when} ಡಾಕ್ಟರ್ {doc} ಅವರೊಂದಿಗೆ ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಇದೆ. ನಿಮಗೆ ಬರಲು ಸಾಧ್ಯವೇ?",
        "followup": "ನಮಸ್ಕಾರ {name}, ನಾನು 2careAi ಕ್ಲಿನಿಕ್‌ನಿಂದ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ. ಡಾಕ್ಟರ್ {doc} ಅವರ ಭೇಟಿಯ ನಂತರ ನಿಮಗೆ ಈಗ ಹೇಗೆ ಅನ್ನಿಸುತ್ತಿದೆ?"
    },
    "ml": {
        "reminder": "നമസ്കാരം {name}, ഞാൻ 2careAi ക്ലിനിക്കിൽ നിന്നാണ് സംസാരിക്കുന്നത്. നാളെ {when} ഡോക്ടർ {doc}-മായി അപ്പോയിന്റ്മെന്റ് ഉണ്ട്. വരാൻ സാധിക്കുമോ?",
        "followup": "നമസ്കാരം {name}, ഞാൻ 2careAi ക്ലിനിക്കിൽ നിന്നാണ് സംസാരിക്കുന്നത്. ഡോക്ടർ {doc}-നെ കണ്ടതിന് ശേഷം ഇപ്പോൾ എങ്ങനെയുണ്ട്?"
    },
    "mr": {
        "reminder": "नमस्कार {name}, मी 2careAi क्लिनिकमधून बोलत आहे. उद्या {when} डॉक्टर {doc} यांच्यासोबत तुमची अपॉइंटमेंट आहे. तुम्ही येऊ शकाल का?",
        "followup": "नमस्कार {name}, मी 2careAi क्लिनिकमधून बोलत आहे. डॉक्टर {doc} यांच्या भेटीनंतर आता तुम्हाला कसे वाटत आहे?"
    },
    "gu": {
        "reminder": "નમસ્તે {name}, હું 2careAi ક્લિનિકથી વાત કરી રહી છું. કાલે {when} ડૉક્ટર {doc} સાથે તમારી એપોઇન્ટમેન્ટ છે. શું તમે આવી શકશો?",
        "followup": "નમસ્તે {name}, હું 2careAi ક્લિનિકથી વાત કરી રહી છું. ડૉક્ટર {doc} ની મુલાકાત પછી હવે તમને કેવું લાગે છે?"
    },
    "pa": {
        "reminder": "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ {name}, ਮੈਂ 2careAi ਕਲੀਨਿਕ ਤੋਂ ਬੋਲ ਰਹੀ ਹਾਂ। ਕੱਲ੍ਹ {when} ਡਾਕਟਰ {doc} ਨਾਲ ਤੁਹਾਡੀ ਅਪਾਇੰਟਮੈਂਟ ਹੈ। ਕੀ ਤੁਸੀਂ ਆ ਸਕੋਗੇ?",
        "followup": "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ {name}, ਮੈਂ 2careAi ਕਲੀਨਿਕ ਤੋਂ ਬੋਲ ਰਹੀ ਹਾਂ। ਡਾਕਟਰ {doc} ਨੂੰ ਮਿਲਣ ਤੋਂ ਬਾਅਦ ਹੁਣ ਤੁਸੀਂ ਕਿਵੇਂ ਮਹਿਸੂਸ ਕਰ ਰਹੇ ਹੋ?"
    }
}


def opening_line(meta: CampaignMetadata, lang: str = "en") -> str:
    """The first sentence the agent says on the call.

    Dynamically adapts to the patient's preferred language.
    """
    lang_code = (lang or "en").split("-", 1)[0].lower()
    templates = OPENINGS.get(lang_code, OPENINGS["en"])
    template = templates.get(meta.campaign_type, templates["reminder"])

    if lang_code == "hi":
        name = meta.patient_name or "जी"
        doc = meta.doctor_name or "अपने डॉक्टर"
    elif lang_code == "ta":
        name = meta.patient_name or "அவர்களே"
        doc = meta.doctor_name or "உங்கள் மருத்துவர்"
    elif lang_code == "kn":
        name = meta.patient_name or "ಅವರೇ"
        doc = meta.doctor_name or "ನಿಮ್ಮ ವೈದ್ಯರು"
    elif lang_code == "gu":
        name = meta.patient_name or "જી"
        doc = meta.doctor_name or "તમારા ડૉક્ટર"
    else:
        name = meta.patient_name or "there"
        doc = meta.doctor_name or "your doctor"

    when = _format_time(meta.appointment_start)

    return template.format(name=name, doc=doc, when=when)


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
