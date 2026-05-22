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
            "Hi! I can help you book an appointment. "
            "Please speak in English, Hindi, Tamil, or any language you are "
            "comfortable with. What do you need?"
        ),
    ),
    "hi": LangEntry(
        deepgram_code="hi",
        cartesia_voice_id="56e35e2d-6eb6-4226-ab8b-9776515a7094",
        display_name="हिन्दी",
        greeting_text=(
            "नमस्ते! मैं अपॉइंटमेंट बुक करने में मदद कर रही हूँ। "
            "आप हिन्दी, अंग्रेज़ी, तमिल या जिस भाषा में चाहें बात कर सकते हैं। "
            "आपको क्या चाहिए?"
        ),
    ),
    "bn": LangEntry(
        deepgram_code="bn",
        cartesia_voice_id="59ba7dee-8f9a-432f-a6c0-ffb33666b654",
        display_name="বাংলা",
        greeting_text=(
            "নমস্কার! আমি অ্যাপয়েন্টমেন্ট বুক করতে সাহায্য করছি। "
            "আপনি বাংলা, ইংরেজি, হিন্দি বা যেকোনো ভাষায় কথা বলতে পারেন। "
            "আপনার কী দরকার?"
        ),
    ),
    "ta": LangEntry(
        deepgram_code="ta",
        cartesia_voice_id="7f98e662-142d-41ba-89a2-12452640ce6d",
        display_name="தமிழ்",
        greeting_text=(
            "வணக்கம்! நான் சந்திப்பு பதிவு செய்ய உதவுகிறேன். "
            "தமிழ், ஆங்கிலம், ஹிந்தி அல்லது நீங்கள் விரும்பும் மொழியில் "
            "பேசலாம். உங்களுக்கு என்ன வேண்டும்?"
        ),
    ),
    "te": LangEntry(
        deepgram_code="te",
        # Bhavani - Reassuring Companion (Cartesia). Was …a8b (404); correct …a8f.
        cartesia_voice_id="76961778-5ce4-4aa9-9cdf-66a029d61a8f",
        display_name="తెలుగు",
        greeting_text=(
            "నమస్కారం! అపాయింట్‌మెంట్ బుక్ చేయడంలో నేను సహాయం చేస్తాను. "
            "తెలుగు, ఇంగ్లీష్, హిందీ లేదా మీకు సౌకర్యమైన భాషలో "
            "మాట్లాడవచ్చు. మీకు ఏమి కావాలి?"
        ),
    ),
    "kn": LangEntry(
        deepgram_code="kn",
        cartesia_voice_id="7c6219d2-e8d2-462c-89d8-7ecba7c75d65",
        display_name="ಕನ್ನಡ",
        greeting_text=(
            "ನಮಸ್ಕಾರ! ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಲು ನಾನು ಸಹಾಯ ಮಾಡುತ್ತೇನೆ. "
            "ಕನ್ನಡ, ಇಂಗ್ಲಿಷ್, ಹಿಂದಿ ಅಥವಾ ನಿಮಗೆ ಸೌಕರ್ಯವಾದ ಭಾಷೆಯಲ್ಲಿ "
            "ಮಾತನಾಡಬಹುದು. ನಿಮಗೆ ಏನು ಬೇಕು?"
        ),
    ),
    "ml": LangEntry(
        deepgram_code="ml",
        cartesia_voice_id="b426013c-002b-4e89-8874-8cd20b68373a",
        display_name="മലയാളം",
        greeting_text=(
            "നമസ്കാരം! അപ്പോയിന്റ്മെന്റ് ബുക്ക് ചെയ്യാൻ ഞാൻ സഹായിക്കും. "
            "മലയാളം, ഇംഗ്ലീഷ്, ഹിന്ദി അല്ലെങ്കിൽ നിങ്ങൾക്ക് "
            "സുഖമായ ഭാഷയിൽ സംസാരിക്കാം. നിങ്ങൾക്ക് എന്താണ് വേണ്ടത്?"
        ),
    ),
    "mr": LangEntry(
        deepgram_code="mr",
        cartesia_voice_id="5c32dce6-936a-4892-b131-bafe474afe5f",
        display_name="मराठी",
        greeting_text=(
            "नमस्कार! मी अपॉइंटमेंट बुक करण्यात मदत करते. "
            "मराठी, इंग्रजी, हिंदी किंवा तुम्हाला सोयीच्या भाषेत "
            "बोलू शकता. तुम्हाला काय हवे आहे?"
        ),
    ),
    "gu": LangEntry(
        deepgram_code="gu",
        cartesia_voice_id="4590a461-bc68-4a50-8d14-ac04f5923d22",
        display_name="ગુજરાતી",
        greeting_text=(
            "નમસ્તે! હું એપોઇન્ટમેન્ટ બુક કરવામાં મદદ કરું છું. "
            "ગુજરાતી, અંગ્રેજી, હિન્દી અથવા તમને ગમતી ભાષામાં "
            "બોલી શકો છો. તમને શું જોઈએ છે?"
        ),
    ),
    "pa": LangEntry(
        deepgram_code="pa",
        cartesia_voice_id="991c62ce-631f-48b0-8060-2a0ebecbd15b",
        display_name="ਪੰਜਾਬੀ",
        greeting_text=(
            "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਅਪਾਇੰਟਮੈਂਟ ਬੁਕ ਕਰਨ ਵਿੱਚ ਮਦਦ ਕਰਦੀ ਹਾਂ। "
            "ਪੰਜਾਬੀ, ਅੰਗਰੇਜ਼ੀ, ਹਿੰਦੀ ਜਾਂ ਜਿਸ ਭਾਸ਼ਾ ਵਿੱਚ ਚਾਹੋ ਬੋਲ ਸਕਦੇ ਹੋ। "
            "ਤੁਹਾਨੂੰ ਕੀ ਚਾਹੀਦਾ ਹੈ?"
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
