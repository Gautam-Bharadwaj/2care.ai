"""Voice session latency instrumentation.

Per-turn timings are collected by listening to `metrics_collected` events
from the LiveKit AgentSession. Each event delivers exactly one of:

  - VADMetrics: not directly used here; speech-end timing is folded into
    EOUMetrics.
  - EOUMetrics: end-of-utterance and transcription delay (this is the
    "STT finalization" piece).
  - LLMMetrics: TTFT, total duration, token counts.
  - TTSMetrics: TTFB, total duration, characters generated.

We correlate them by `speech_id` (LiveKit emits the same id across all
four events for a single user turn), then once both LLM and TTS pieces
have arrived we emit one consolidated record:

    {
      "ts": "2026-05-23T...",
      "session_id": "...",
      "turn_idx": 7,
      "speech_id": "abc",
      "stt_finalization_ms": 124.5,
      "llm_ttft_ms": 218.0,
      "tts_ttfb_ms": 191.3,
      "speech_end_to_first_audio_ms": 533.8,
      "eou_delay_ms": 80.0,
      "llm_total_ms": 412.0,
      "tts_total_ms": 980.0
    }

`speech_end_to_first_audio_ms` is the headline metric the assignment
asks for. We also keep the older thinking→speaking timer as a fallback
in case `metrics_collected` doesn't arrive (e.g., realtime model path).

Each consolidated record is:
  - written as a JSONL line to `logs/latency.jsonl`
  - pushed onto a Redis list `latency:last100` (LPUSH + LTRIM to 100)
    so `GET /metrics/latency` can read it back without crawling logs.

The Redis writes are best-effort and never block the call.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis.asyncio as redis_async
import structlog
from livekit.agents import metrics as lk_metrics

log = structlog.get_logger("agent.metrics")


# Keys / sizes
LATENCY_BUFFER_KEY = "latency:last100"
LATENCY_BUFFER_SIZE = 100
# Healthcheck breadcrumbs — last successful timestamp per provider. Read by
# `GET /healthz` so operators can see the pipeline is making real calls
# without grepping logs.
LAST_LLM_CALL_TS_KEY = "health:last_llm_call_ts"
LAST_TTS_CALL_TS_KEY = "health:last_tts_call_ts"


# ---------------------------------------------------------------------------
# Per-turn accumulator
# ---------------------------------------------------------------------------


@dataclass
class TurnTimings:
    """Coalesced per-turn timings, keyed by speech_id.

    Stages arrive out-of-order over `metrics_collected`. We accept whatever
    shows up, then emit once we've seen both LLM and TTS (the two pieces
    needed to compute speech_end_to_first_audio_ms). If a stage is
    missing after a short grace window the turn is emitted anyway with
    that stage as None.
    """

    speech_id: str
    eou_delay_ms: float | None = None
    stt_finalization_ms: float | None = None
    llm_ttft_ms: float | None = None
    llm_total_ms: float | None = None
    llm_prompt_tokens: int | None = None
    llm_completion_tokens: int | None = None
    tts_ttfb_ms: float | None = None
    tts_total_ms: float | None = None
    tts_characters: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def headline_ms(self) -> float | None:
        """speech_end_to_first_audio_ms = STT finalization + LLM TTFT + TTS TTFB.

        Approximation: EOU delay overlaps with VAD's end-of-speech detection,
        which the user already perceives as silence — we don't bill that to
        the agent. The standard latency breakdown for this stack is the sum
        of the three streaming-pipeline stages.
        """
        if (
            self.stt_finalization_ms is None
            or self.llm_ttft_ms is None
            or self.tts_ttfb_ms is None
        ):
            return None
        return round(
            self.stt_finalization_ms + self.llm_ttft_ms + self.tts_ttfb_ms, 1
        )

    def is_complete(self) -> bool:
        """All three stages observed."""
        return (
            self.stt_finalization_ms is not None
            and self.llm_ttft_ms is not None
            and self.tts_ttfb_ms is not None
        )

    def as_record(
        self, *, session_id: str, turn_idx: int, patient_phone: str | None
    ) -> dict[str, Any]:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "turn_idx": turn_idx,
            "speech_id": self.speech_id,
            "patient_phone": patient_phone,
            "eou_delay_ms": _round(self.eou_delay_ms),
            "stt_finalization_ms": _round(self.stt_finalization_ms),
            "llm_ttft_ms": _round(self.llm_ttft_ms),
            "llm_total_ms": _round(self.llm_total_ms),
            "tts_ttfb_ms": _round(self.tts_ttfb_ms),
            "tts_total_ms": _round(self.tts_total_ms),
            "speech_end_to_first_audio_ms": self.headline_ms(),
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "tts_characters": self.tts_characters,
        }


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class LatencyTracker:
    """Coalesces per-stage events into per-turn records.

    Use:

        tracker = LatencyTracker(log_path=Path("logs/latency.jsonl"),
                                 session_id=ctx.room.name,
                                 patient_phone=phone)
        tracker.attach(session)
    """

    def __init__(
        self,
        log_path: Path,
        session_id: str,
        patient_phone: str | None = None,
        *,
        redis_url: str | None = None,
    ) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.patient_phone = patient_phone
        self._turn_idx = 0
        # speech_id → in-flight turn
        self._pending: dict[str, TurnTimings] = {}
        # Fallback thinking-state timer for pipelines that don't surface
        # `metrics_collected` (kept from the original implementation).
        self._thinking_started_at: float | None = None
        self._redis_url = redis_url
        self._redis: redis_async.Redis | None = None

    # -- public attach -------------------------------------------------------

    def attach(self, session) -> None:
        """Wire event handlers onto a LiveKit AgentSession."""

        @session.on("metrics_collected")
        def _on_metrics(ev) -> None:
            try:
                self._consume(ev.metrics)
            except Exception as e:  # noqa: BLE001
                log.error("metrics_consume_failed", err=str(e))
            # Pretty-print for the live log too.
            with contextlib.suppress(Exception):
                lk_metrics.log_metrics(ev.metrics)

        @session.on("agent_state_changed")
        def _on_state(ev) -> None:
            new_state = str(
                getattr(ev, "new_state", "") or getattr(ev, "state", "")
            ).lower()
            if new_state == "thinking":
                self._thinking_started_at = time.perf_counter()
            elif new_state == "speaking" and self._thinking_started_at is not None:
                elapsed_ms = (time.perf_counter() - self._thinking_started_at) * 1000
                self._thinking_started_at = None
                # Fallback record only fires when no per-stage record exists
                # for this turn — otherwise we'd double-count.
                if not self._pending:
                    self._turn_idx += 1
                    record = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "session_id": self.session_id,
                        "turn_idx": self._turn_idx,
                        "patient_phone": self.patient_phone,
                        "speech_end_to_first_audio_ms": round(elapsed_ms, 1),
                        "fallback": True,
                    }
                    self._write(record)

    # -- consumer ------------------------------------------------------------

    def _consume(self, m: Any) -> None:
        """Route a single LiveKit metric event into the right TurnTimings."""
        kind = getattr(m, "type", None) or type(m).__name__.lower()
        speech_id = getattr(m, "speech_id", None)
        if not speech_id:
            # Some metric kinds (VAD) carry no speech_id; we ignore them.
            return
        timings = self._pending.setdefault(speech_id, TurnTimings(speech_id))

        if kind == "eou_metrics":
            # EOUMetrics.end_of_utterance_delay is the gap between detected
            # speech-end and the moment the LLM/agent is scheduled to think.
            # transcription_delay is the STT-side finalization piece.
            timings.eou_delay_ms = _to_ms(getattr(m, "end_of_utterance_delay", None))
            timings.stt_finalization_ms = _to_ms(
                getattr(m, "transcription_delay", None)
            )
        elif kind == "stt_metrics":
            # If transcription_delay didn't appear via EOU (e.g., non-streaming
            # path) fall back to the STT request duration.
            if timings.stt_finalization_ms is None:
                timings.stt_finalization_ms = _to_ms(getattr(m, "duration", None))
        elif kind == "llm_metrics":
            timings.llm_ttft_ms = _to_ms(getattr(m, "ttft", None))
            timings.llm_total_ms = _to_ms(getattr(m, "duration", None))
            timings.llm_prompt_tokens = getattr(m, "prompt_tokens", None)
            timings.llm_completion_tokens = getattr(m, "completion_tokens", None)
            self._stamp_health(LAST_LLM_CALL_TS_KEY)
        elif kind == "tts_metrics":
            # Only count the first TTS chunk for ttfb — subsequent segments
            # within the same speech_id (paragraph splits) are not what the
            # caller perceives as "agent started speaking".
            if timings.tts_ttfb_ms is None:
                timings.tts_ttfb_ms = _to_ms(getattr(m, "ttfb", None))
                timings.tts_total_ms = _to_ms(getattr(m, "duration", None))
                timings.tts_characters = getattr(m, "characters_count", None)
            self._stamp_health(LAST_TTS_CALL_TS_KEY)
        else:
            return

        if timings.is_complete():
            self._flush(speech_id)

    def _flush(self, speech_id: str) -> None:
        timings = self._pending.pop(speech_id, None)
        if timings is None:
            return
        self._turn_idx += 1
        record = timings.as_record(
            session_id=self.session_id,
            turn_idx=self._turn_idx,
            patient_phone=self.patient_phone,
        )
        self._write(record)

    # -- output --------------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            with self.log_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
        log.info("turn_latency", **record)
        # Fire-and-forget Redis push; tests stub this by passing redis_url=None.
        if self._redis_url:
            asyncio.create_task(self._push_to_redis(record))

    def _stamp_health(self, key: str) -> None:
        """Set a `health:*` Redis key to the current UTC ISO timestamp.

        Fire-and-forget; the /healthz endpoint reads these to surface
        "last successful provider call" without spelunking the log file.
        """
        if not self._redis_url:
            return
        ts = datetime.now(timezone.utc).isoformat()
        asyncio.create_task(self._set_health_key(key, ts))

    async def _set_health_key(self, key: str, ts: str) -> None:
        try:
            if self._redis is None:
                self._redis = redis_async.Redis.from_url(
                    self._redis_url, decode_responses=True
                )
            await self._redis.set(key, ts)
        except Exception as e:  # noqa: BLE001
            log.warning("health_stamp_failed", key=key, err=str(e))

    async def _push_to_redis(self, record: dict[str, Any]) -> None:
        try:
            if self._redis is None:
                self._redis = redis_async.Redis.from_url(
                    self._redis_url, decode_responses=True
                )
            async with self._redis.pipeline() as pipe:
                pipe.lpush(LATENCY_BUFFER_KEY, json.dumps(record))
                pipe.ltrim(LATENCY_BUFFER_KEY, 0, LATENCY_BUFFER_SIZE - 1)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001
            log.warning("latency_redis_push_failed", err=str(e))

    async def aclose(self) -> None:
        # Flush anything still pending — better a partial record than nothing.
        for sid in list(self._pending.keys()):
            timings = self._pending.pop(sid)
            self._turn_idx += 1
            record = timings.as_record(
                session_id=self.session_id,
                turn_idx=self._turn_idx,
                patient_phone=self.patient_phone,
            )
            record["partial"] = True
            self._write(record)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()


def _to_ms(seconds: float | None) -> float | None:
    """LiveKit reports delays in seconds (float). Normalize to milliseconds."""
    if seconds is None:
        return None
    return float(seconds) * 1000.0


# ---------------------------------------------------------------------------
# Percentile math used by the /metrics/latency endpoint
# ---------------------------------------------------------------------------


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (no interpolation — cleaner with small N).

    Empty list returns None so the endpoint can render a `null` rather
    than crashing on a freshly-deployed worker. Uses ceil rather than
    round so p50 of [1..9] returns 5 (textbook median), not 4 (banker's
    rounding artifact).
    """
    import math

    if not values:
        return None
    if pct <= 0:
        return min(values)
    if pct >= 100:
        return max(values)
    sorted_vals = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_vals)))
    return sorted_vals[min(rank - 1, len(sorted_vals) - 1)]


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a list of latency records into p50/p90/p99 per stage.

    Skips records missing the relevant field (`None`). Returns a dict
    shape stable enough for the /metrics/latency endpoint to render.
    """
    def collect(field_name: str) -> list[float]:
        return [
            float(r[field_name]) for r in records
            if r.get(field_name) is not None
        ]

    def stats(values: list[float]) -> dict[str, float | None]:
        return {
            "n": len(values),
            "p50": _round(percentile(values, 50)),
            "p90": _round(percentile(values, 90)),
            "p99": _round(percentile(values, 99)),
        }

    return {
        "sample_size": len(records),
        "speech_end_to_first_audio_ms": stats(collect("speech_end_to_first_audio_ms")),
        "stt_finalization_ms": stats(collect("stt_finalization_ms")),
        "llm_ttft_ms": stats(collect("llm_ttft_ms")),
        "tts_ttfb_ms": stats(collect("tts_ttfb_ms")),
        "eou_delay_ms": stats(collect("eou_delay_ms")),
        "llm_total_ms": stats(collect("llm_total_ms")),
        "tts_total_ms": stats(collect("tts_total_ms")),
    }
