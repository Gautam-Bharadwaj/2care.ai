"""Latency benchmark: drive a 20-turn synthetic conversation through the
real STT/LLM/TTS providers and record per-stage timings.

This is *not* a full voice-pipeline benchmark — it doesn't include VAD or
end-of-utterance detection. Those numbers vary primarily with the speaker
and aren't sensitive to the optimizations we apply server-side. What this
benchmark measures are the three stages we can actually tune:

    speech_end_to_first_audio_ms ≈ stt_finalization + llm_ttft + tts_ttfb

Usage:

    uv run python scripts/bench_latency.py             # 20 turns, English
    uv run python scripts/bench_latency.py --turns 50  # 50 turns
    uv run python scripts/bench_latency.py --lang hi   # Hindi STT+TTS+LLM
    uv run python scripts/bench_latency.py --json out.json   # write summary

Requires Deepgram + Groq + Cartesia API keys in `.env`. Network-bound;
expect ~30-90s wall clock for a 20-turn run depending on region.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

from agent.language import LANG_CONFIG, entry_for  # noqa: E402
from agent.metrics import percentile  # noqa: E402
from config import get_settings  # noqa: E402


# Synthetic prompts. Short, clinic-domain, designed so a small model can
# answer without tool calls — the goal is to measure the pipeline, not
# stress the LLM.
SAMPLE_PROMPTS = [
    "Hi, I'd like to book an appointment.",
    "What times are available tomorrow?",
    "Do you have anything in the morning?",
    "Can I see Dr. Mehta?",
    "Is ten thirty open?",
    "How long is the appointment?",
    "Can I reschedule my Friday slot?",
    "I need to cancel my visit.",
    "What's the address?",
    "Do you take walk-ins?",
    "Are you open Saturdays?",
    "Can I bring my mother?",
    "What should I bring?",
    "Is parking available?",
    "How early should I arrive?",
    "Will the doctor speak Hindi?",
    "Do you accept new patients?",
    "Can you send a confirmation?",
    "What's my next visit?",
    "Thanks, that's all.",
]


@dataclass
class TurnResult:
    turn_idx: int
    stt_finalization_ms: float | None = None
    llm_ttft_ms: float | None = None
    llm_total_ms: float | None = None
    tts_ttfb_ms: float | None = None
    speech_end_to_first_audio_ms: float | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Stage drivers
# ---------------------------------------------------------------------------


async def time_stt(text: str, *, language: str, settings) -> float:
    """Measure STT round-trip by sending a short synthesized clip.

    We can't actually drive Deepgram without audio, so this benchmark
    accepts a precomputed silence-padded WAV proxy and times the
    Deepgram /listen REST round-trip. This intentionally over-estimates
    real-time STT because Deepgram's streaming path is faster than the
    REST one — keep that in mind when reading the report.
    """
    audio = _silent_wav_bytes()
    url = (
        f"https://api.deepgram.com/v1/listen"
        f"?model=nova-3&language={LANG_CONFIG[language].deepgram_code}&smart_format=false"
    )
    headers = {
        "Authorization": f"Token {settings.deepgram_api_key}",
        "Content-Type": "audio/wav",
    }
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(url, headers=headers, content=audio)
    return (time.perf_counter() - start) * 1000


async def time_llm(prompt: str, *, settings) -> tuple[float, float]:
    """Measure LLM TTFT and total via Groq SDK streaming."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    start = time.perf_counter()
    ttft: float | None = None

    stream = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a clinic receptionist. Be brief."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=40,
        stream=True,
    )
    async for chunk in stream:
        if ttft is None and chunk.choices and chunk.choices[0].delta.content:
            ttft = (time.perf_counter() - start) * 1000
    total = (time.perf_counter() - start) * 1000
    return ttft or total, total


async def time_tts(text: str, *, language: str, settings) -> float:
    """Measure Cartesia TTS TTFB by reading the first WS chunk."""
    import websockets

    entry = entry_for(language)
    url = (
        f"wss://api.cartesia.ai/tts/websocket"
        f"?api_key={settings.cartesia_api_key}&cartesia_version=2024-06-10"
    )
    start = time.perf_counter()
    async with websockets.connect(url, max_size=None) as ws:
        msg = {
            "context_id": f"bench-{int(start*1000)}",
            "model_id": "sonic-2",
            "transcript": text,
            "voice": {"mode": "id", "id": entry.cartesia_voice_id},
            "language": entry.deepgram_code,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
            },
        }
        await ws.send(json.dumps(msg))
        # Wait for the first audio chunk.
        while True:
            raw = await ws.recv()
            try:
                data = json.loads(raw) if isinstance(raw, str) else None
            except json.JSONDecodeError:
                data = None
            if data is None:  # binary frame = audio
                break
            if data.get("type") in ("chunk", "audio"):
                break
            if data.get("type") == "error":
                raise RuntimeError(f"Cartesia error: {data}")
    return (time.perf_counter() - start) * 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _silent_wav_bytes(duration_s: float = 0.6, sample_rate: int = 16000) -> bytes:
    """Build a tiny silent WAV. Replace with real captured audio for a
    truer STT measurement."""
    import io
    import struct

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<h", 0) * int(duration_s * sample_rate))
    return buf.getvalue()


def _summarize(turns: list[TurnResult]) -> dict:
    def field(name: str) -> list[float]:
        return [getattr(t, name) for t in turns if getattr(t, name) is not None]

    def stats(values: list[float]) -> dict:
        return {
            "n": len(values),
            "mean": round(statistics.mean(values), 1) if values else None,
            "p50": round(percentile(values, 50), 1) if values else None,
            "p90": round(percentile(values, 90), 1) if values else None,
            "p99": round(percentile(values, 99), 1) if values else None,
        }

    return {
        "turns": len(turns),
        "errors": sum(1 for t in turns if t.error),
        "stt_finalization_ms": stats(field("stt_finalization_ms")),
        "llm_ttft_ms": stats(field("llm_ttft_ms")),
        "llm_total_ms": stats(field("llm_total_ms")),
        "tts_ttfb_ms": stats(field("tts_ttfb_ms")),
        "speech_end_to_first_audio_ms": stats(field("speech_end_to_first_audio_ms")),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def run_bench(turns: int, language: str) -> list[TurnResult]:
    settings = get_settings()
    if not settings.deepgram_api_key or not settings.groq_api_key or not settings.cartesia_api_key:
        raise SystemExit(
            "DEEPGRAM_API_KEY / GROQ_API_KEY / CARTESIA_API_KEY must be set."
        )

    prompts = (SAMPLE_PROMPTS * ((turns + len(SAMPLE_PROMPTS) - 1) // len(SAMPLE_PROMPTS)))[:turns]

    results: list[TurnResult] = []
    for i, prompt in enumerate(prompts, start=1):
        t = TurnResult(turn_idx=i)
        try:
            t.stt_finalization_ms = await time_stt(prompt, language=language, settings=settings)
            t.llm_ttft_ms, t.llm_total_ms = await time_llm(prompt, settings=settings)
            t.tts_ttfb_ms = await time_tts(prompt, language=language, settings=settings)
            t.speech_end_to_first_audio_ms = (
                t.stt_finalization_ms + t.llm_ttft_ms + t.tts_ttfb_ms
            )
        except Exception as e:  # noqa: BLE001
            t.error = f"{type(e).__name__}: {e}"
        print(
            f"turn {i:>2}: "
            f"stt={t.stt_finalization_ms!s:>7}  "
            f"llm_ttft={t.llm_ttft_ms!s:>7}  "
            f"tts_ttfb={t.tts_ttfb_ms!s:>7}  "
            f"total={t.speech_end_to_first_audio_ms!s:>7}  "
            f"{('ERR ' + t.error) if t.error else ''}"
        )
        results.append(t)
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=20)
    ap.add_argument("--lang", default="en", choices=sorted(LANG_CONFIG.keys()))
    ap.add_argument("--json", type=Path, default=None, help="Write JSON summary here")
    args = ap.parse_args()

    results = asyncio.run(run_bench(args.turns, args.lang))
    summary = _summarize(results)
    print()
    print("=== summary ===")
    print(json.dumps(summary, indent=2))
    if args.json:
        args.json.write_text(json.dumps({"language": args.lang, **summary}, indent=2))
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
