"""Voice page → backend bridge.

Two endpoints power the new patient-facing booking page at
`frontend/src/pages/BookingPage.tsx`:

  POST /voice/token  — mint a LiveKit access token + room name + URL
                       so the page can connect a real WebRTC session
                       to the agent worker. The agent picks up the room
                       (dispatch rule matches `booking-*`) and runs the
                       full STT/LLM/TTS pipeline.

  POST /voice/chat   — text-only LLM bridge used by the legacy static
                       voice page (`docs/voice/`). Kept for fallback so
                       the prototype still works without a running
                       agent worker or LiveKit Cloud connection.

Both endpoints reuse `agent/llm_agent.SYSTEM_PROMPT` + the production
`agent/language.LANG_CONFIG`, so dialog quality matches a real call.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from livekit import api as livekit_api
import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from agent.llm_agent import LLM_MODEL, SYSTEM_PROMPT
from agent.language import (
    LANG_CONFIG,
    DEFAULT_LANGUAGE,
    cartesia_tts_payload,
    entry_for,
    is_supported,
)
from config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


class VoiceMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class VoiceChatRequest(BaseModel):
    """One round-trip for the static voice page."""

    messages: list[VoiceMessage] = Field(default_factory=list)
    language: str | None = Field(
        default=None,
        description="ISO code (en, hi, ta, te, bn, mr). Drives the SYSTEM_PROMPT lang slot.",
    )


class VoiceChatResponse(BaseModel):
    response: str
    model: str


# Demo-friendly variant of the production prompt. The production prompt
# tells the LLM to call tools (book_appointment, list_available_slots,
# etc.); for the static page we don't have a DB, so we tell the model to
# roleplay the tool effects conversationally instead.
DEMO_SYSTEM_SUFFIX_TEMPLATE = """\

[Demo mode · {language_name}]
No database — roleplay booking naturally: "Lemme check… Tuesday 9 and
9:30 are open — which works?" Invent plausible slots; after "yes" say
"Done ji, appointment confirm ho gaya" warmly. One question per turn.
Match caller tone (Hinglish if they use it). Never sound like a chatbot.
{script_rule}
"""

# Per-language reminder of which Unicode script every word in the reply
# must be written in. Particularly important for Gujarati and Kannada,
# where llama-3.3 will otherwise drift into Devanagari (close-enough
# Hindi cousin script). Keyed by ISO language code.
SCRIPT_RULES: dict[str, str] = {
    "en": (
        "Use plain English / Latin alphabet only. Numerals as digits (10:30 AM)."
    ),
    "hi": (
        "Hindi session: use Devanagari OR natural Hinglish (Latin) to match "
        "the caller — never drift into Tamil/Kannada/Gujarati script."
    ),
    "kn": (
        "Write in Kannada script ONLY (Unicode block ಀ–೿). Every word must "
        "use Kannada letters (ಅ-ಹ). Do NOT use Devanagari, Latin, or any "
        "other script. If you're tempted to write in Hindi or English, "
        "switch back to Kannada immediately."
    ),
    "ta": (
        "Write in Tamil script ONLY (Unicode block ஀–௿). Every word must "
        "use Tamil letters (அ-ஹ). Do NOT use Devanagari, Latin, or any "
        "other script."
    ),
    "gu": (
        "Write in Gujarati script ONLY (Unicode block ઀–૿). Every word must "
        "use Gujarati letters. Do NOT slip into Devanagari or Latin."
    ),
    "bn": (
        "Write in Bengali script ONLY (Unicode block অ–৺). Do NOT use "
        "Devanagari, Latin, or any other script."
    ),
    "te": (
        "Write in Telugu script ONLY (Unicode block ఀ–౿). Do NOT use "
        "Devanagari, Latin, or any other script."
    ),
    "mr": (
        "Write in Devanagari script for Marathi. Do NOT mix Latin. "
        "Use Marathi forms (e.g. तुम्हाला, अपॉइंटमेंट), not Hindi-only phrasing."
    ),
    "ml": (
        "Write in Malayalam script ONLY (Unicode block ഀ–ൿ). Do NOT use "
        "Devanagari, Latin, or any other script."
    ),
    "pa": (
        "Write in Gurmukhi script ONLY (Unicode block ਅ–ੴ). Do NOT use "
        "Devanagari, Latin, or any other script."
    ),
}


def _build_messages(req: VoiceChatRequest) -> list[dict]:
    """Build the OpenAI chat-completion message list.

    The production SYSTEM_PROMPT has a `{language_name}` placeholder for
    Phase 5 language locking. We fill it from the request's `language`
    (or default to English) and append the demo-mode suffix.
    """
    code = req.language or DEFAULT_LANGUAGE
    entry = LANG_CONFIG.get(code) or LANG_CONFIG[DEFAULT_LANGUAGE]
    script_rule = SCRIPT_RULES.get(code, SCRIPT_RULES["en"])
    system = (
        SYSTEM_PROMPT.format(language_name=entry.display_name)
        + DEMO_SYSTEM_SUFFIX_TEMPLATE.format(
            language_name=entry.display_name,
            script_rule=script_rule,
        )
    )

    out: list[dict] = [{"role": "system", "content": system}]
    for m in req.messages:
        out.append({"role": m.role, "content": m.content})

    # Last-mile reminder: prepend a *user* turn that re-states the
    # language constraint right before the model generates. This is
    # belt-and-suspenders — llama-3.3 sometimes drifts mid-conversation
    # and a fresh instruction immediately before generation has more
    # weight than the same line buried in the system prompt.
    out.append({
        "role": "system",
        "content": (
            f"Reminder: reply in {entry.display_name} only. {script_rule}"
        ),
    })
    return out


@router.post("/chat", response_model=VoiceChatResponse)
async def chat(req: VoiceChatRequest) -> VoiceChatResponse:
    settings = get_settings()
    if not settings.groq_api_key:
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is not configured on the backend.",
        )

    # Groq is OpenAI-compatible — reuse the openai client with its base_url.
    client = AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        timeout=20.0,
    )

    messages = _build_messages(req)

    # Model fallback. Groq's free tier has separate per-day token quotas
    # per model. When the 70b production model rate-limits, the demo
    # endpoint silently falls back to the 8b instant model so the page
    # stays responsive. The 8b model handles Indic-language replies
    # noticeably worse, so we keep 70b as the primary.
    model_chain = [LLM_MODEL, "llama-3.1-8b-instant"]
    completion = None
    last_err: Exception | None = None
    used_model: str = LLM_MODEL
    for model in model_chain:
        try:
            completion = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=220,
                temperature=0.5,
            )
            used_model = model
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            # Only fall through to the next model on a rate-limit. Other
            # errors (auth, network) should fail loudly.
            err_str = str(e)
            if "rate_limit" not in err_str and "429" not in err_str:
                logger.exception("groq call failed (non-rate-limit)")
                raise HTTPException(status_code=502, detail=f"upstream LLM error: {e!s}")
            logger.warning("groq rate-limit on %s, trying next model", model)
    if completion is None:
        logger.error("all groq models rate-limited: %s", last_err)
        raise HTTPException(
            status_code=502,
            detail=f"upstream LLM rate-limited on all models: {last_err!s}",
        )

    reply = (completion.choices[0].message.content or "").strip()
    if not reply:
        reply = "Sorry, I didn't catch that. Could you say that again?"

    # Script-validation retry. The headline problem is llama-3.3
    # drifting into Devanagari (Hindi) when the caller asked for
    # Gujarati — the two scripts look related to the model and it
    # confuses them. If the reply doesn't match the expected Unicode
    # block, re-prompt with a louder instruction *once*. Single retry
    # so a stubborn reply doesn't blow up latency.
    expected_lang = req.language or DEFAULT_LANGUAGE
    if not _reply_matches_script(reply, expected_lang):
        logger.info("voice_chat_script_drift first_chars=%r lang=%s", reply[:40], expected_lang)
        try:
            retry_messages = messages + [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": (
                        "Your last reply was in the wrong script. "
                        f"Rewrite it in {LANG_CONFIG[expected_lang].display_name}. "
                        f"{SCRIPT_RULES.get(expected_lang, '')}"
                    ),
                },
            ]
            retry = await client.chat.completions.create(
                model=used_model,
                messages=retry_messages,
                max_tokens=180,
                temperature=0.3,  # tighter on retry — less drift
            )
            retry_text = (retry.choices[0].message.content or "").strip()
            if retry_text and _reply_matches_script(retry_text, expected_lang):
                reply = retry_text
            # If retry still drifts, keep the original — better to ship
            # *something* than to fail the request.
        except Exception:  # noqa: BLE001
            logger.exception("voice_chat retry failed")

    return VoiceChatResponse(response=reply, model=used_model)


# ────────────────────────────────────────────────────────────────
# POST /voice/stt · POST /voice/tts
# Browser fallback: Deepgram + Cartesia (same providers as the agent).
# ────────────────────────────────────────────────────────────────


class VoiceSttResponse(BaseModel):
    text: str
    language: str


class VoiceTtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    language: str = Field(default=DEFAULT_LANGUAGE)


@router.post("/stt", response_model=VoiceSttResponse)
async def transcribe_audio(
    audio: UploadFile = File(...),
    language: str = Form(default=DEFAULT_LANGUAGE),
) -> VoiceSttResponse:
    """Transcribe a short mic clip with Deepgram nova-3.

    The frontend records WebM/Opus from MediaRecorder and posts it here
    when LiveKit is unavailable. We pin Deepgram to the patient's picked
    language (not `multi`) so Hindi/Tamil accuracy stays high.
    """
    settings = get_settings()
    if not settings.deepgram_api_key:
        raise HTTPException(status_code=503, detail="DEEPGRAM_API_KEY is not configured.")

    lang = language if is_supported(language) else DEFAULT_LANGUAGE
    dg_lang = entry_for(lang).deepgram_code
    blob = await audio.read()
    if not blob or len(blob) < 80:
        return VoiceSttResponse(text="", language=lang)

    content_type = audio.content_type or "audio/webm"
    url = (
        "https://api.deepgram.com/v1/listen"
        f"?model=nova-3&language={dg_lang}&smart_format=false&punctuate=true"
    )
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.post(
                url,
                headers={
                    "Authorization": f"Token {settings.deepgram_api_key}",
                    "Content-Type": content_type,
                },
                content=blob,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        logger.exception("deepgram stt failed")
        raise HTTPException(status_code=502, detail=f"STT failed: {e!s}")

    try:
        text = (
            data["results"]["channels"][0]["alternatives"][0]["transcript"] or ""
        ).strip()
    except (KeyError, IndexError, TypeError):
        text = ""
    return VoiceSttResponse(text=text, language=lang)


@router.post("/tts")
async def synthesize_speech(req: VoiceTtsRequest) -> Response:
    """Synthesize speech with Cartesia sonic-2 for the browser fallback path."""
    settings = get_settings()
    if not settings.cartesia_api_key:
        raise HTTPException(status_code=503, detail="CARTESIA_API_KEY is not configured.")

    lang = req.language if is_supported(req.language) else DEFAULT_LANGUAGE
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers={
                    "X-API-Key": settings.cartesia_api_key,
                    "Cartesia-Version": "2024-06-10",
                },
                json=cartesia_tts_payload(req.text, lang),
            )
            r.raise_for_status()
            audio_bytes = r.content
    except httpx.HTTPError as e:
        logger.exception("cartesia tts failed")
        raise HTTPException(status_code=502, detail=f"TTS failed: {e!s}")

    if not audio_bytes:
        raise HTTPException(status_code=502, detail="TTS returned empty audio.")
    return Response(content=audio_bytes, media_type="audio/mpeg")


# Script-validation table. Maps language code → predicate that returns
# True iff the text contains enough characters in the expected Unicode
# range to count as "actually written in that language". The "enough"
# is intentionally loose (one third of the non-whitespace characters)
# so a reply with a foreign proper noun (e.g. "Dr. Mehra") doesn't
# trigger a false-positive retry.
def _reply_matches_script(text: str, lang: str) -> bool:
    if not text:
        return False
    ranges: tuple[tuple[str, str], ...] = {
        "en": (("\x20", "\x7e"),),
        "hi": (("ऀ", "ॿ"),),
        "mr": (("ऀ", "ॿ"),),
        "kn": (("ಀ", "೿"),),
        "ta": (("஀", "௿"),),
        "te": (("ఀ", "౿"),),
        "gu": (("઀", "૿"),),
        "bn": (("ঀ", "৿"),),
        "ml": (("ഀ", "ൿ"),),
        "pa": (("਀", "੿"),),
    }.get(lang, ())
    if not ranges:
        return True  # unknown language — don't second-guess
    if lang == "en":
        # English: at least one ASCII letter, and no big chunk of any
        # Indic block. Cheap heuristic — full text is normally Latin.
        return any(c.isascii() and c.isalpha() for c in text)
    significant = sum(1 for c in text if not c.isspace() and c not in ".,;:!?-—…'\"")
    if significant == 0:
        return False
    in_range = sum(
        1
        for c in text
        if any(lo <= c <= hi for lo, hi in ranges)
    )
    # Hindi demo/live: allow Devanagari or natural Hinglish (Latin).
    if lang == "hi":
        latin = sum(1 for c in text if c.isascii() and c.isalpha())
        return in_range / significant >= 0.33 or latin / significant >= 0.45
    return in_range / significant >= 0.33


# ────────────────────────────────────────────────────────────────
# POST /voice/token
# ────────────────────────────────────────────────────────────────


class VoiceTokenRequest(BaseModel):
    """What the booking page sends when the patient taps Start Call."""

    language: str = Field(default=DEFAULT_LANGUAGE, description="ISO code: en/hi/kn/ta/gu")
    patient_phone: str | None = Field(
        default=None,
        description="E.164 number if known. Used as the LiveKit identity; "
        "otherwise we generate a guest id.",
    )


class VoiceTokenResponse(BaseModel):
    token: str
    room: str
    livekit_url: str


@router.post("/token", response_model=VoiceTokenResponse)
async def mint_token(req: VoiceTokenRequest) -> VoiceTokenResponse:
    """Sign a LiveKit JWT for a fresh `booking-{uuid}` room.

    The agent worker is configured (in `agent/main.py`) to accept every
    room whose name matches `booking-*`, so each call creates a new
    room and gets a fresh agent dispatch. Room metadata carries the
    patient's chosen language + phone so the agent can greet correctly
    on the very first turn — no detect-then-switch on call open.
    """
    settings = get_settings()
    if not (settings.livekit_api_key and settings.livekit_api_secret and settings.livekit_url):
        raise HTTPException(
            status_code=503,
            detail="LiveKit credentials are not configured (LIVEKIT_API_KEY / "
            "LIVEKIT_API_SECRET / LIVEKIT_URL).",
        )

    # Validate language code. Unknown codes get bumped to English so the
    # agent always has a clean starting point.
    lang = req.language if is_supported(req.language) else DEFAULT_LANGUAGE

    # Room name: short uuid so the LiveKit dispatch rule's prefix match
    # is unambiguous and trace-viewer URLs stay readable.
    room_name = f"booking-{uuid.uuid4().hex[:12]}"

    # Identity: patient phone if available (lets the agent resolve
    # prior memory automatically), else a guest tag.
    identity = req.patient_phone or f"guest-{uuid.uuid4().hex[:8]}"

    metadata = json.dumps(
        {
            "preferred_language": lang,
            "patient_phone": req.patient_phone,
            "source": "frontend-booking-page",
        }
    )

    try:
        token = (
            livekit_api.AccessToken(
                settings.livekit_api_key,
                settings.livekit_api_secret,
            )
            .with_identity(identity)
            .with_name(req.patient_phone or "Caller")
            .with_metadata(metadata)
            .with_grants(
                livekit_api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,           # mic
                    can_subscribe=True,          # agent's TTS audio
                    can_publish_data=True,       # patient could send data too
                )
            )
            .to_jwt()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("livekit token mint failed")
        raise HTTPException(status_code=500, detail=f"token mint failed: {e!s}")

    return VoiceTokenResponse(
        token=token,
        room=room_name,
        livekit_url=settings.livekit_url,
    )
