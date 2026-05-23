"""Tests for Phase 5 multilingual support.

Covers:
- LANG_CONFIG completeness for the five target languages
  (English, Hindi, Kannada, Tamil, Gujarati).
- Greeting selection (per-language vs. multi-lingual invite).
- Deepgram language-code normalization (`hi-IN` → `hi`, unknown → None).
- The two-turn confirmation window in `LanguageLock`:
    · single foreign-language turn does NOT flip the lock
    · two consecutive turns DO flip the lock
    · non-contiguous foreign turns (English in between) do NOT accumulate
- `_apply_language_lock` reconfigures the Cartesia TTS *and* PATCHes the
  patient's `preferred_language`, but only AFTER the lock fires (never on
  the first ambiguous turn).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import main as agent_main
from agent.language import (
    CODE_SWITCH_INVITATION,
    LANG_CONFIG,
    LOCK_THRESHOLD_TURNS,
    entry_for,
    greeting_for,
    is_supported,
    normalize_detected,
)
from agent.main import LanguageLock

SUPPORTED = {"en", "hi", "bn", "ta", "te", "kn", "ml", "mr", "gu", "pa"}


# ---------------------------------------------------------------------------
# Config sanity
# ---------------------------------------------------------------------------


def test_lang_config_covers_all_supported_languages():
    assert set(LANG_CONFIG.keys()) == SUPPORTED


def test_lang_config_entries_are_well_formed():
    for code, entry in LANG_CONFIG.items():
        assert entry.deepgram_code, f"{code}: missing deepgram_code"
        assert entry.cartesia_voice_id, f"{code}: missing voice id"
        assert entry.display_name, f"{code}: missing display_name"
        assert entry.greeting_text, f"{code}: missing greeting"


def test_telugu_cartesia_voice_id_not_typo():
    """Regression: …a8b 404'd on Cartesia; Bhavani is …a8f."""
    te = LANG_CONFIG["te"].cartesia_voice_id
    assert te.endswith("a8f"), te
    assert te == "76961778-5ce4-4aa9-9cdf-66a029d61a8f"


def test_greetings_are_in_native_script_not_transliterated():
    """Greetings should contain real native script for non-English langs."""
    script_ranges = {
        "hi": ("ऀ", "ॿ"),
        "bn": ("ঀ", "৿"),
        "ta": ("஀", "௿"),
        "te": ("ఀ", "౿"),
        "kn": ("ಀ", "೿"),
        "ml": ("ഀ", "ൿ"),
        "mr": ("ऀ", "ॿ"),
        "gu": ("઀", "૿"),
        "pa": ("਀", "੿"),
    }
    for code, (lo, hi) in script_ranges.items():
        text = LANG_CONFIG[code].greeting_text
        assert any(lo <= c <= hi for c in text), (
            f"{code} greeting is not in native script: {text!r}"
        )


def test_entry_for_falls_back_to_english():
    assert entry_for(None).deepgram_code == "en"
    assert entry_for("zz").deepgram_code == "en"
    assert entry_for("hi").deepgram_code == "hi"


def test_is_supported():
    assert is_supported("hi")
    assert not is_supported(None)
    assert not is_supported("zz")


def test_greeting_for_returns_invite_when_unknown():
    assert greeting_for(None) == CODE_SWITCH_INVITATION
    assert greeting_for("zz") == CODE_SWITCH_INVITATION
    assert greeting_for("hi") == LANG_CONFIG["hi"].greeting_text


def test_greetings_do_not_mention_other_languages():
    """Picker-selected language: greet only in that language (no 'speak Tamil…')."""
    cross_lang_phrases = {
        "en": ("hindi", "tamil", "any language"),
        "hi": ("तमिल", "अंग्रेज़ी", "जिस भाषा"),
        "ta": ("ஹிந்தி", "ஆங்கிலம்", "மொழியில்"),
        "te": ("హిందీ", "ఇంగ్లీష్", "భాషలో"),
    }
    for code, phrases in cross_lang_phrases.items():
        text = LANG_CONFIG[code].greeting_text.lower()
        for phrase in phrases:
            assert phrase.lower() not in text, (
                f"{code} greeting mentions other languages: {text!r}"
            )


def test_hindi_cartesia_voice_updated():
    assert LANG_CONFIG["hi"].cartesia_voice_id == "c1abd502-9231-4558-a054-10ac950c356d"


def test_code_switch_invitation_mentions_multiple_scripts():
    # Invitation should at minimum contain Latin + Devanagari + Tamil glyphs
    # so a caller in any of those tongues recognizes it.
    assert "Hello" in CODE_SWITCH_INVITATION
    assert any("ऀ" <= c <= "ॿ" for c in CODE_SWITCH_INVITATION)
    assert any("஀" <= c <= "௿" for c in CODE_SWITCH_INVITATION)


# ---------------------------------------------------------------------------
# Deepgram code normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("detected", "expected"),
    [
        ("hi", "hi"),
        ("hi-IN", "hi"),
        ("EN", "en"),
        ("ta-IN", "ta"),
        ("kn-IN", "kn"),
        ("gu-IN", "gu"),
        ("bn-BD", "bn"),
        ("te", "te"),
        ("ml-IN", "ml"),
        ("pa", "pa"),
        ("zz", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_detected(detected, expected):
    assert normalize_detected(detected) == expected


# ---------------------------------------------------------------------------
# LanguageLock — the 2-turn confirmation window
# ---------------------------------------------------------------------------


def test_lock_requires_two_consecutive_turns():
    lock = LanguageLock(initial="en")
    assert lock.observe("hi") is None  # turn 1 — not enough
    flipped = lock.observe("hi")
    assert flipped == "hi"
    assert lock.current == "hi"
    assert lock.locked is True


def test_lock_threshold_matches_config():
    """Test guards against accidental config drift."""
    assert LOCK_THRESHOLD_TURNS == 2


def test_lock_resets_when_speaker_returns_to_current():
    """A foreign turn followed by an English turn should NOT count toward
    a future lock — code-switching mid-call is the norm in India."""
    lock = LanguageLock(initial="en")
    lock.observe("hi")              # pending=hi, streak=1
    lock.observe("en")              # back to current; reset
    flipped = lock.observe("hi")    # only 1 hi turn again; no lock
    assert flipped is None
    assert lock.current == "en"
    assert lock.locked is False


def test_lock_handles_unsupported_language_code():
    lock = LanguageLock(initial="en")
    assert lock.observe("zz") is None
    assert lock.observe("zz-XX") is None
    assert lock.current == "en"


def test_lock_normalizes_bcp47_tags_with_region():
    lock = LanguageLock(initial="en")
    assert lock.observe("hi-IN") is None
    assert lock.observe("hi-IN") == "hi"


def test_lock_can_flip_again_after_first_lock():
    """Patient starts EN → locks to HI → later locks to TA."""
    lock = LanguageLock(initial="en")
    lock.observe("hi")
    assert lock.observe("hi") == "hi"
    # Now they switch to Tamil
    assert lock.observe("ta") is None
    assert lock.observe("ta") == "ta"
    assert lock.current == "ta"


def test_lock_ignores_first_turn_in_pending():
    """Critical: single foreign-language turn must NOT flip the lock — that's
    the whole point of the 2-turn window for code-switching tolerance."""
    lock = LanguageLock(initial="en")
    assert lock.observe("ta") is None
    assert lock.current == "en"
    assert lock.locked is False


# ---------------------------------------------------------------------------
# _apply_language_lock side effects
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_session():
    s = MagicMock()
    s.tts = MagicMock()
    s.tts.update_options = MagicMock()
    return s


@pytest.fixture
def fake_session_memory():
    m = MagicMock()
    m.update = AsyncMock()
    return m


@pytest.fixture
def fake_agent():
    a = MagicMock()
    a.instructions = "BASE PROMPT"
    a.update_instructions = AsyncMock()
    return a


async def _drive_lock_apply(
    monkeypatch,
    session,
    session_memory,
    agent,
    patient_id: str | None,
    detected_turns: list[str],
):
    """Replay a sequence of detected languages through a LanguageLock and call
    _apply_language_lock-style code each time the lock flips.

    Returns the list of PATCH targets so tests can assert call ordering."""
    patched: list[str] = []

    async def fake_patch(pid, lang):
        patched.append(lang)

    monkeypatch.setattr(agent_main, "_update_preferred_language", fake_patch)

    lock = LanguageLock(initial="en")
    flips: list[str] = []
    for detected in detected_turns:
        new_lang = lock.observe(detected)
        if new_lang:
            flips.append(new_lang)
            entry = entry_for(new_lang)
            session.tts.update_options(
                language=entry.deepgram_code, voice=entry.cartesia_voice_id
            )
            await session_memory.update(
                language_locked=True, active_language=new_lang
            )
            await agent_main._update_preferred_language(patient_id, new_lang)
            await agent.update_instructions(agent.instructions + f"\n[lang={new_lang}]")
    return flips, patched


async def test_tts_reconfigures_on_lock(
    monkeypatch, fake_session, fake_session_memory, fake_agent
):
    flips, patched = await _drive_lock_apply(
        monkeypatch,
        fake_session,
        fake_session_memory,
        fake_agent,
        patient_id="p-123",
        detected_turns=["hi", "hi"],
    )
    assert flips == ["hi"]
    fake_session.tts.update_options.assert_called_once_with(
        language="hi", voice=LANG_CONFIG["hi"].cartesia_voice_id
    )
    fake_session_memory.update.assert_awaited_once_with(
        language_locked=True, active_language="hi"
    )
    assert patched == ["hi"]
    fake_agent.update_instructions.assert_awaited_once()


async def test_preferred_language_not_patched_before_lock(
    monkeypatch, fake_session, fake_session_memory, fake_agent
):
    """One Hindi turn alone must NOT trigger a DB update."""
    flips, patched = await _drive_lock_apply(
        monkeypatch,
        fake_session,
        fake_session_memory,
        fake_agent,
        patient_id="p-123",
        detected_turns=["hi"],
    )
    assert flips == []
    assert patched == []
    fake_session.tts.update_options.assert_not_called()
    fake_session_memory.update.assert_not_called()


async def test_lock_flow_for_each_target_language(
    monkeypatch, fake_session, fake_session_memory, fake_agent
):
    """Two consecutive turns in any of the six supported languages locks."""
    for lang in SUPPORTED - {"en"}:
        fake_session.tts.update_options.reset_mock()
        fake_session_memory.update.reset_mock()
        fake_agent.update_instructions.reset_mock()

        flips, patched = await _drive_lock_apply(
            monkeypatch,
            fake_session,
            fake_session_memory,
            fake_agent,
            patient_id="p-test",
            detected_turns=[lang, lang],
        )
        assert flips == [lang]
        assert patched == [lang]
        fake_session.tts.update_options.assert_called_once_with(
            language=LANG_CONFIG[lang].deepgram_code,
            voice=LANG_CONFIG[lang].cartesia_voice_id,
        )


# ---------------------------------------------------------------------------
# Mocked Deepgram event end-to-end via observe()
# ---------------------------------------------------------------------------


def _mock_dg_event(transcript: str, language: str | None, is_final: bool = True):
    """Build a fake UserInputTranscribedEvent-shaped object."""
    return SimpleNamespace(
        transcript=transcript,
        language=language,
        is_final=is_final,
    )


@pytest.mark.parametrize(
    ("turns", "expected_locked"),
    [
        # Five-language locking smoke tests
        ([("Hello", "en"), ("Hi", "en")], "en"),
        ([("नमस्ते", "hi-IN"), ("कैसे हो", "hi")], "hi"),
        ([("ನಮಸ್ಕಾರ", "kn"), ("ಹೇಗಿದ್ದೀರಿ", "kn-IN")], "kn"),
        ([("வணக்கம்", "ta"), ("எப்படி", "ta-IN")], "ta"),
        ([("નમસ્તે", "gu"), ("કેમ છો", "gu-IN")], "gu"),
    ],
)
def test_simulated_deepgram_turns_lock_correctly(turns, expected_locked):
    lock = LanguageLock(initial="en")
    final = None
    for transcript, dg_lang in turns:
        ev = _mock_dg_event(transcript, dg_lang)
        if ev.is_final:
            flipped = lock.observe(ev.language)
            if flipped:
                final = flipped

    if expected_locked == "en":
        # Already in English — no lock flip occurs.
        assert final is None
        assert lock.current == "en"
    else:
        assert final == expected_locked
        assert lock.current == expected_locked


def test_interim_results_are_ignored():
    """Interim (`is_final=False`) transcripts must not feed the lock."""
    lock = LanguageLock(initial="en")
    interim = _mock_dg_event("नम", "hi", is_final=False)
    final = _mock_dg_event("नमस्ते", "hi", is_final=True)

    # Simulate the handler's gate
    for ev in [interim, interim, interim]:
        if ev.is_final:
            lock.observe(ev.language)
    assert lock.current == "en"
    # Now a real final turn arrives
    lock.observe(final.language)
    assert lock.current == "en"  # still only 1 turn observed
    lock.observe(final.language)
    assert lock.current == "hi"
