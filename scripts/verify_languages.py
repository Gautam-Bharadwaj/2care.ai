"""Multilingual end-to-end verification.

Scope of what this can verify *from inside the codebase, without
LiveKit/Twilio/microphone hardware*:

  * The agent's reply for a representative booking utterance in each
    of the 5 supported languages.
  * That the reply is in the **correct script** (Unicode-block ratio
    check, ≥70% in-script).
  * That the reply is **contextually relevant** to a booking request
    (matches one or more booking-domain keywords in that language).
  * That the agent **maintains language across the conversation** when
    given a second user turn.
  * Round-trip latency to the `/voice/chat` endpoint.

What this CAN'T verify (be honest with the operator):

  * STT accuracy. We don't pipe real audio through Deepgram here;
    the patient page uses the browser's `SpeechRecognition` and the
    LiveKit pipeline uses Deepgram. Test those manually.
  * TTS quality. Browser `SpeechSynthesis` ships locale-specific voices
    that vary by OS; the Cartesia voices likewise need manual ear-check
    once you've selected the actual voice IDs for kn / gu.
  * Tool calls (book_appointment, list_available_slots). The /voice/chat
    endpoint is text-only LLM, no tool calling — the production agent
    worker has those but isn't exercised by this script.
  * Language lock firing after 2 consistent turns. That's a runtime
    state machine in `agent/main.py` exercised by `tests/test_multilingual.py`.

Usage:
    uv run python scripts/verify_languages.py
    # writes docs/language-verification-report.md
    # exit code 0 if all 5 languages pass, 1 otherwise
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
REPORT_PATH = REPO_ROOT / "docs" / "language-verification-report.md"

# ---------------------------------------------------------------------------
# Test corpus — per-language input + expected behaviour
# ---------------------------------------------------------------------------

#                                Unicode range (start, end, inclusive)
SCRIPT_RANGES: dict[str, tuple[str, str] | None] = {
    "en": None,                # Special-cased — Latin / ASCII letters
    "hi": ("ऀ", "ॿ"),  # Devanagari
    "kn": ("ಀ", "೿"),  # Kannada
    "ta": ("஀", "௿"),  # Tamil
    "gu": ("઀", "૿"),  # Gujarati
}

# Booking-domain vocabulary the agent's reply should hit at least one of.
# We check word membership in a way that handles both space-separated
# scripts (Latin, Devanagari) and conjunct scripts (Tamil, Kannada,
# Gujarati) — substring match is good enough.
BOOKING_KEYWORDS: dict[str, list[str]] = {
    "en": ["appointment", "doctor", "slot", "tomorrow", "morning", "book", "available", "Dr"],
    "hi": ["अपॉइंटमेंट", "डॉक्टर", "बुक", "सुबह", "कल", "समय", "उपलब्ध", "डॉ"],
    "kn": ["ಡಾಕ್ಟರ್", "ಬುಕ್", "ಬೆಳಿಗ್ಗೆ", "ನಾಳೆ", "ಸಮಯ", "ಲಭ್ಯ", "ಡಾ"],
    "ta": ["டாக்டர்", "சந்திப்பு", "காலை", "நாளை", "நேரம்", "பதிவு", "டாக்"],
    "gu": ["એપોઇન્ટમેન્ટ", "ડૉક્ટર", "બુક", "સવારે", "કાલે", "સમય", "ઉપલબ્ધ", "ડૉ"],
}

# The "patient" input we send per language. Phrased as the brief
# requested: "appointment with a general physician tomorrow morning".
TEST_UTTERANCES: dict[str, str] = {
    "en": "I want to book an appointment with a general physician tomorrow morning",
    "hi": "मुझे कल सुबह एक जनरल फिजिशियन के साथ अपॉइंटमेंट बुक करनी है",
    "kn": "ನನಗೆ ನಾಳೆ ಬೆಳಿಗ್ಗೆ ಒಬ್ಬ ಸಾಮಾನ್ಯ ವೈದ್ಯರ ಬಳಿ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಬೇಕು",
    "ta": "எனக்கு நாளை காலை ஒரு பொது மருத்துவரிடம் சந்திப்பு பதிவு செய்ய வேண்டும்",
    "gu": "મારે કાલે સવારે જનરલ ફિઝિશિયન સાથે એપોઇન્ટમેન્ટ બુક કરવી છે",
}

# A follow-up second turn that's intentionally a 2-word affirmative —
# this is where the agent is most likely to drift into English if the
# system prompt is weak.
FOLLOWUP_UTTERANCES: dict[str, str] = {
    "en": "Yes, 9:00 AM works.",
    "hi": "हाँ, सुबह 9 बजे ठीक है।",
    "kn": "ಹೌದು, ಬೆಳಿಗ್ಗೆ 9 ಗಂಟೆಗೆ ಸರಿ.",
    "ta": "ஆம், காலை 9 மணி சரி.",
    "gu": "હા, સવારે 9 વાગ્યે બરાબર છે.",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    user_input: str
    agent_reply: str
    latency_ms: float
    script_ratio: float
    script_pass: bool
    keyword_pass: bool
    keywords_hit: list[str] = field(default_factory=list)


@dataclass
class LanguageResult:
    lang: str
    display_name: str
    turn1: TurnResult | None = None
    turn2: TurnResult | None = None
    error: str | None = None

    @property
    def overall_pass(self) -> bool:
        if self.error or not self.turn1 or not self.turn2:
            return False
        return (
            self.turn1.script_pass and self.turn1.keyword_pass
            and self.turn2.script_pass
        )


# ---------------------------------------------------------------------------
# Script + keyword analysis
# ---------------------------------------------------------------------------


def script_ratio(text: str, lang: str) -> float:
    """Fraction of non-whitespace, non-punctuation chars that fall in the
    expected Unicode block for `lang`. Returns 0.0–1.0."""
    if not text:
        return 0.0
    rng = SCRIPT_RANGES.get(lang)
    significant = [c for c in text if not c.isspace() and c not in ".,;:!?\"'-—…()[]"]
    if not significant:
        return 0.0
    if lang == "en":
        # Latin letters only. Numerics + ASCII punctuation are OK
        # but we count only alphabetic ASCII as "in script".
        in_range = sum(1 for c in significant if c.isascii() and c.isalpha())
    elif rng:
        lo, hi = rng
        in_range = sum(1 for c in significant if lo <= c <= hi)
    else:
        return 1.0
    return in_range / len(significant)


def keyword_check(text: str, lang: str) -> tuple[bool, list[str]]:
    """At least one booking-domain keyword should appear in the reply."""
    hits = [kw for kw in BOOKING_KEYWORDS.get(lang, []) if kw in text]
    return (len(hits) > 0, hits)


# ---------------------------------------------------------------------------
# Backend round-trip
# ---------------------------------------------------------------------------


def post_chat(lang: str, messages: list[dict]) -> tuple[str, float]:
    """POST to /voice/chat. Returns (response_text, latency_ms).

    Retries on transient 5xx upstream errors (Groq rate-limit / 502)
    with exponential backoff, since the script fires 5 languages × 2
    turns back-to-back which is exactly when Groq's free tier shrugs.

    Uses urllib (no aiohttp/httpx in the script's dep set) so this runs
    against the system Python with zero install."""
    body = json.dumps({"language": lang, "messages": messages}).encode("utf-8")
    delays = [0, 1.5, 4.0]   # 3 attempts total
    t0 = time.perf_counter()
    last_err: Exception | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        req = urllib.request.Request(
            f"{BACKEND_URL}/voice/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            return data.get("response", ""), (time.perf_counter() - t0) * 1000
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in (502, 503, 504, 429):
                # Non-transient → bail out immediately.
                raise
        except urllib.error.URLError as e:
            last_err = e
            # Connection refused / DNS — treat as transient too.
    raise last_err if last_err else urllib.error.URLError("unknown error")


# ---------------------------------------------------------------------------
# Per-language runner
# ---------------------------------------------------------------------------


def run_language(lang: str, display_name: str) -> LanguageResult:
    result = LanguageResult(lang=lang, display_name=display_name)

    # Turn 1: patient asks to book.
    try:
        reply1, lat1 = post_chat(
            lang,
            [{"role": "user", "content": TEST_UTTERANCES[lang]}],
        )
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        result.error = f"backend / upstream error: {e!s}"
        return result
    ratio1 = script_ratio(reply1, lang)
    kw1_pass, kw1_hits = keyword_check(reply1, lang)
    result.turn1 = TurnResult(
        user_input=TEST_UTTERANCES[lang],
        agent_reply=reply1,
        latency_ms=lat1,
        script_ratio=ratio1,
        script_pass=ratio1 >= 0.70,
        keyword_pass=kw1_pass,
        keywords_hit=kw1_hits,
    )

    # Turn 2: short affirmative — verifies the agent doesn't drift.
    try:
        history = [
            {"role": "user", "content": TEST_UTTERANCES[lang]},
            {"role": "assistant", "content": reply1},
            {"role": "user", "content": FOLLOWUP_UTTERANCES[lang]},
        ]
        reply2, lat2 = post_chat(lang, history)
    except urllib.error.URLError as e:
        result.error = f"backend unreachable on turn 2: {e!s}"
        return result
    ratio2 = script_ratio(reply2, lang)
    kw2_pass, kw2_hits = keyword_check(reply2, lang)
    result.turn2 = TurnResult(
        user_input=FOLLOWUP_UTTERANCES[lang],
        agent_reply=reply2,
        latency_ms=lat2,
        script_ratio=ratio2,
        script_pass=ratio2 >= 0.70,
        keyword_pass=kw2_pass,  # second turn doesn't have to mention booking words
        keywords_hit=kw2_hits,
    )

    return result


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def write_report(results: list[LanguageResult]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Language verification report")
    lines.append("")
    lines.append(
        f"_Generated by `scripts/verify_languages.py` against `{BACKEND_URL}`._"
    )
    lines.append("")
    lines.append(
        "**Scope.** Tests the `/voice/chat` LLM endpoint for each of the 5 "
        "supported languages with a representative booking utterance and a "
        "follow-up affirmative. Verifies (a) the reply is in the correct "
        "Unicode script (≥70% in-script chars), and (b) the reply is "
        "contextually relevant to a booking request. Real STT (Deepgram) "
        "and TTS (Cartesia) need to be eye/ear-checked manually."
    )
    lines.append("")

    # Summary table at the top — per the brief.
    lines.append("## Summary")
    lines.append("")
    lines.append("| Lang | Script | Reply in-script | Contextual | Stays in language | Overall |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in results:
        in_script = "✓" if (r.turn1 and r.turn1.script_pass) else "✗"
        contextual = "✓" if (r.turn1 and r.turn1.keyword_pass) else "✗"
        stays = "✓" if (r.turn2 and r.turn2.script_pass) else "✗"
        overall = "**PASS**" if r.overall_pass else "FAIL"
        ratio_str = f"{r.turn1.script_ratio:.0%}" if r.turn1 else "–"
        if r.lang == "en":
            rng_str = "Latin"
        else:
            rng = SCRIPT_RANGES[r.lang]
            assert rng is not None
            rng_str = f"U+{ord(rng[0]):04X}–U+{ord(rng[1]):04X}"
        lines.append(
            f"| `{r.lang}` ({r.display_name}) | "
            f"{rng_str} | {in_script} ({ratio_str}) | {contextual} | "
            f"{stays} | {overall} |"
        )
    lines.append("")

    # Per-language detail blocks.
    lines.append("")
    lines.append("## Per-language detail")
    for r in results:
        lines.append("")
        lines.append(f"### `{r.lang}` — {r.display_name}")
        lines.append("")
        if r.error:
            lines.append(f"**Error:** {r.error}")
            continue
        for label, t in (("Turn 1 (booking request)", r.turn1), ("Turn 2 (affirmative)", r.turn2)):
            if not t:
                lines.append(f"_{label}_ — not exercised")
                continue
            lines.append(f"**{label}**")
            lines.append("")
            lines.append(f"  patient: `{t.user_input}`")
            lines.append(f"  agent:   `{t.agent_reply}`")
            lines.append("")
            script_pass = "PASS" if t.script_pass else "FAIL"
            keyword_pass = "PASS" if t.keyword_pass else "FAIL"
            lines.append(
                f"  - script ratio: {t.script_ratio:.0%} ({script_pass}, threshold 70%)"
            )
            lines.append(
                f"  - booking keywords matched: {keyword_pass} ({', '.join(t.keywords_hit) or '(none)'})"
            )
            lines.append(f"  - LLM round-trip latency: {t.latency_ms:.0f} ms")
            lines.append("")

    # Caveats section — be honest about what isn't covered.
    lines.append("")
    lines.append("## Caveats (NOT covered by this script)")
    lines.append("")
    lines.append("- **STT (Deepgram) accuracy.** Manual test required: speak the test")
    lines.append("  utterance per language into the browser at `http://localhost:5174/`")
    lines.append("  and confirm the transcript that lands at `/voice/chat`.")
    lines.append("- **TTS (Cartesia / browser SpeechSynthesis) quality.** Ear-check")
    lines.append("  pronunciation per language. **Kannada and Gujarati Cartesia voices")
    lines.append("  are placeholder IDs in `agent/language.py`** — replace from")
    lines.append("  <https://play.cartesia.ai/> before the demo. Browser")
    lines.append("  SpeechSynthesis voices ship per-OS — Chrome on macOS has Indic")
    lines.append("  voices preinstalled; some Linux distros do not.")
    lines.append("- **Tool calls.** `/voice/chat` is text-only LLM; the production")
    lines.append("  voice agent (LiveKit + tools) has these but isn't exercised here.")
    lines.append("- **Language lock after 2 turns.** Hermetically tested in")
    lines.append("  `tests/test_multilingual.py::test_scenario_7_language_switch_locks_after_two_turns`.")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"Testing /voice/chat at {BACKEND_URL}\n")

    # We can't import from `agent.language` cleanly without setting up the
    # Python path; just hard-code the display names here. Keep in sync with
    # agent/language.py::LANG_CONFIG.
    languages = [
        ("en", "English"),
        ("hi", "हिन्दी"),
        ("kn", "ಕನ್ನಡ"),
        ("ta", "தமிழ்"),
        ("gu", "ગુજરાતી"),
    ]

    results: list[LanguageResult] = []
    for code, display in languages:
        print(f"--- {code} ({display}) ---")
        r = run_language(code, display)
        results.append(r)
        if r.error:
            print(f"  ERROR: {r.error}")
            continue
        for label, t in (("turn1", r.turn1), ("turn2", r.turn2)):
            if not t:
                continue
            ok_script = "OK" if t.script_pass else "FAIL"
            ok_kw = "OK" if t.keyword_pass else "FAIL"
            print(
                f"  {label}: script {t.script_ratio:.0%} {ok_script} · "
                f"keywords {ok_kw} · {t.latency_ms:.0f} ms"
            )
            print(f"    → {t.agent_reply[:120]}{'…' if len(t.agent_reply) > 120 else ''}")
        verdict = "PASS" if r.overall_pass else "FAIL"
        print(f"  overall: {verdict}\n")

    write_report(results)
    print(f"Wrote {REPORT_PATH}")
    passed = sum(1 for r in results if r.overall_pass)
    print(f"\n{passed}/{len(results)} languages PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
