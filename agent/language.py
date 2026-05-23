"""Multilingual configuration for the 2careAi voice pipeline.

Ten Indian languages with per-language Deepgram STT and Cartesia sonic-3
TTS voices. Swap voice IDs at https://play.cartesia.ai/
"""
from __future__ import annotations

from dataclasses import dataclass

LOCK_THRESHOLD_TURNS = 2
DEFAULT_LANGUAGE = "en"

CARTESIA_MODEL = "sonic-3"
CARTESIA_SPEED = 0.92
CARTESIA_EMOTION = "calm"


@dataclass(frozen=True)
class LangEntry:
    deepgram_code: str
    cartesia_voice_id: str
    display_name: str
    greeting_text: str


LANG_CONFIG: dict[str, LangEntry] = {
    "en": LangEntry(
        deepgram_code="en",
        cartesia_voice_id="a7a59115-2425-4192-844c-1e98ec7d6877",
        display_name="English",
        greeting_text=(
            "Hello, good morning. Hope you're doing well. "
            "How may I help you today?"
        ),
    ),
    "hi": LangEntry(
        deepgram_code="hi",
        # Hindi Narrator Woman — clearer clinic reception tone than the old voice.
        cartesia_voice_id="c1abd502-9231-4558-a054-10ac950c356d",
        display_name="हिन्दी",
        greeting_text=(
            "नमस्ते जी, आप कैसे हैं? सब ठीक है ना? "
            "क्या डॉक्टर की अपॉइंटमेंट बुक करनी थी?"
        ),
    ),
    "bn": LangEntry(
        deepgram_code="bn",
        cartesia_voice_id="59ba7dee-8f9a-432f-a6c0-ffb33666b654",
        display_name="বাংলা",
        greeting_text=(
            "নমস্কার, আপনি কেমন আছেন? "
            "ডাক্তারের অ্যাপয়েন্টমেন্ট বুক করতে চেয়েছিলেন?"
        ),
    ),
    "ta": LangEntry(
        deepgram_code="ta",
        cartesia_voice_id="7f98e662-142d-41ba-89a2-12452640ce6d",
        display_name="தமிழ்",
        greeting_text=(
            "வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்? "
            "மருத்துவர் சந்திப்பு பதிவு செய்ய வேண்டுமா?"
        ),
    ),
    "te": LangEntry(
        deepgram_code="te",
        # Bhavani - Reassuring Companion (Cartesia). Was …a8b (404); correct …a8f.
        cartesia_voice_id="76961778-5ce4-4aa9-9cdf-66a029d61a8f",
        display_name="తెలుగు",
        greeting_text=(
            "నమస్కారం, మీరు ఎలా ఉన్నారు? "
            "డాక్టర్ అపాయింట్‌మెంట్ బుక్ చేయాలనుకుంటున్నారా?"
        ),
    ),
    "kn": LangEntry(
        deepgram_code="kn",
        cartesia_voice_id="7c6219d2-e8d2-462c-89d8-7ecba7c75d65",
        display_name="ಕನ್ನಡ",
        greeting_text=(
            "ನಮಸ್ಕಾರ, ನೀವು ಹೇಗಿದ್ದೀರಿ? "
            "ವೈದ್ಯರ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಬೇಕೇ?"
        ),
    ),
    "ml": LangEntry(
        deepgram_code="ml",
        cartesia_voice_id="b426013c-002b-4e89-8874-8cd20b68373a",
        display_name="മലയാളം",
        greeting_text=(
            "നമസ്കാരം, സുഖമാണോ? "
            "ഡോക്ടർ അപ്പോയിന്റ്മെന്റ് ബുക്ക് ചെയ്യണോ?"
        ),
    ),
    "mr": LangEntry(
        deepgram_code="mr",
        cartesia_voice_id="5c32dce6-936a-4892-b131-bafe474afe5f",
        display_name="मराठी",
        greeting_text=(
            "नमस्कार, तुम्ही कसे आहात? "
            "डॉक्टरची अपॉइंटमेंट बुक करायची आहे का?"
        ),
    ),
    "gu": LangEntry(
        deepgram_code="gu",
        cartesia_voice_id="4590a461-bc68-4a50-8d14-ac04f5923d22",
        display_name="ગુજરાતી",
        greeting_text=(
            "નમસ્તે, તમે કેમ છો? "
            "ડૉક્ટરની એપોઇન્ટમેન્ટ બુક કરવી છે?"
        ),
    ),
    "pa": LangEntry(
        deepgram_code="pa",
        cartesia_voice_id="991c62ce-631f-48b0-8060-2a0ebecbd15b",
        display_name="ਪੰਜਾਬੀ",
        greeting_text=(
            "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਤੁਸੀਂ ਠੀਕ ਹੋ? "
            "ਡਾਕਟਰ ਦੀ ਅਪਾਇੰਟਮੈਂਟ ਬੁਕ ਕਰਨੀ ਸੀ?"
        ),
    ),
}

CODE_SWITCH_INVITATION = (
    "Hello / नमस्ते / নমস্কার / வணக்கம் / నమస్కారం / ನಮಸ್ಕಾರ / "
    "നമസ്കാരം / नमस्कार / નમસ્તે / ਸਤ ਸ੍ਰੀ ਅਕਾਲ — "
    "I am the clinic assistant. Please speak in English, Hindi, Tamil, "
    "or any language you are comfortable with. How can I help you today?"
)


def entry_for(language: str | None) -> LangEntry:
    if not language:
        return LANG_CONFIG[DEFAULT_LANGUAGE]
    return LANG_CONFIG.get(language, LANG_CONFIG[DEFAULT_LANGUAGE])


def is_supported(language: str | None) -> bool:
    return bool(language) and language in LANG_CONFIG


def greeting_for(language: str | None) -> str:
    if is_supported(language):
        return LANG_CONFIG[language].greeting_text  # type: ignore[index]
    return CODE_SWITCH_INVITATION


def cartesia_tts_payload(text: str, language: str | None) -> dict:
    entry = entry_for(language)
    return {
        "model_id": CARTESIA_MODEL,
        "transcript": text,
        "voice": {"mode": "id", "id": entry.cartesia_voice_id},
        "language": entry.deepgram_code,
        "generation_config": {
            "speed": CARTESIA_SPEED,
            "emotion": CARTESIA_EMOTION,
            "volume": 1.0,
        },
        "output_format": {
            "container": "mp3",
            "encoding": "mp3",
            "sample_rate": 44100,
            "bit_rate": 128000,
        },
    }


def normalize_detected(code: str | None) -> str | None:
    if not code:
        return None
    primary = code.split("-", 1)[0].lower()
    return primary if primary in LANG_CONFIG else None
