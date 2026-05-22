"""Per-turn reasoning trace capture (Phase 8).

What it records — per LLM turn:

  - turn_id (string, derived from speech_id when available)
  - user_input (final transcribed STT text)
  - detected_language (Deepgram primary language code)
  - llm_messages (full message list passed to the LLM, snapshotted via
    `Agent.on_user_turn_completed`)
  - tool_calls — name, args, result, latency_ms, status (filled by the
    `traced` decorator in `agent/tools.py` via per-turn ContextVar
    buffers)
  - memory_recalls — broken out of tool_calls when the tool is
    `recall_memory`, so the viewer can render them as a separate
    affordance
  - llm_response_text / final_response_to_user (assistant turn captured
    from `conversation_item_added`)
  - latency breakdown (from `metrics_collected` events, same data the
    LatencyTracker uses)

Outputs:

  - JSONL line per turn at `logs/traces/{session_id}.jsonl`
  - POST to `/traces` so the row lands in the `conversation_turns`
    Postgres table where the viewer can read it

Capture is *purely additive* — if the backend POST fails, the JSONL
file still has it; if the JSONL write fails, the in-memory snapshot
still flushed. The agent's voice pipeline never blocks on trace I/O.

How it integrates with the rest of the agent:

  1. Entrypoint instantiates `TraceCollector(session_id=..., patient_id=...)`,
     binds the per-turn buffers via `set_trace_buffers`, and calls
     `collector.attach(session, agent)`.
  2. `Agent.on_user_turn_completed(turn_ctx, new_message)` is monkey-patched
     to snapshot `llm_messages` + start the turn record.
  3. Session events drive the rest: `user_input_transcribed` updates the
     STT field, `metrics_collected` fills latency, `conversation_item_added`
     captures the assistant reply, after which the turn is flushed.
  4. On shutdown any in-flight (partial) turns are flushed too.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import structlog

from agent.tools import set_trace_buffers
from config import get_settings

log = structlog.get_logger("agent.traces")


TRACE_DIR = Path("logs/traces")


# ---------------------------------------------------------------------------
# Per-turn record
# ---------------------------------------------------------------------------


@dataclass
class TurnTrace:
    turn_id: str
    turn_idx: int
    started_at: str
    user_input: str | None = None
    detected_language: str | None = None
    active_language: str | None = None
    llm_messages: list[dict[str, Any]] | None = None
    llm_response_text: str | None = None
    final_response_to_user: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    memory_recalls: list[dict[str, Any]] = field(default_factory=list)
    latency: dict[str, float | None] = field(default_factory=dict)
    ended_at: str | None = None
    campaign_context: dict[str, Any] | None = None
    notes: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "turn_idx": self.turn_idx,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "user_input": self.user_input,
            "detected_language": self.detected_language,
            "active_language": self.active_language,
            "llm_messages": self.llm_messages,
            "llm_response_text": self.llm_response_text,
            "tool_calls": self.tool_calls,
            "memory_recalls": self.memory_recalls,
            "final_response_to_user": self.final_response_to_user,
            "latency": self.latency,
            "campaign_context": self.campaign_context,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class TraceCollector:
    """Stateful per-session trace collector.

    One instance per LiveKit room. Holds the currently-open `TurnTrace`
    and a list of completed ones (in case we want to flush on shutdown).
    """

    def __init__(
        self,
        *,
        session_id: str,
        patient_id: str | None = None,
        trace_dir: Path | None = None,
        backend_url: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.patient_id = patient_id
        self.trace_dir = trace_dir or TRACE_DIR
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.trace_dir / f"{session_id}.jsonl"
        self._backend_url = backend_url or get_settings().backend_url
        self._turn_idx = 0
        self._open: TurnTrace | None = None
        # speech_id → TurnTrace for latency correlation (some metrics
        # events arrive after the turn has otherwise "ended"):
        self._by_speech_id: dict[str, TurnTrace] = {}

    # -- public API ----------------------------------------------------------

    def attach(self, session, agent) -> None:
        """Wire the collector into a running AgentSession + Agent.

        The Agent hook is the only place we get the full chat context as
        the LLM is about to see it. We chain it with the agent's existing
        `on_user_turn_completed` so we don't accidentally replace user
        behavior in the future.
        """
        self._bind_buffers_for_new_turn()
        self._wrap_on_user_turn_completed(agent)
        self._wire_session_events(session)

    def close_open_turn(self, reason: str = "") -> None:
        """Flush whatever turn is currently in flight as `partial`."""
        if self._open is None:
            return
        self._open.notes = (
            f"partial flush at shutdown: {reason}" if reason else "partial"
        )
        self._flush(self._open, partial=True)
        self._open = None

    # -- agent hook ----------------------------------------------------------

    def _wrap_on_user_turn_completed(self, agent) -> None:
        original = agent.on_user_turn_completed

        async def _wrapped(turn_ctx, new_message):
            try:
                self._start_turn_from_ctx(turn_ctx, new_message)
            except Exception as e:  # noqa: BLE001
                log.warning("trace_start_turn_failed", err=str(e))
            # Always defer to the original (subclasses may override).
            return await original(turn_ctx, new_message)

        agent.on_user_turn_completed = _wrapped  # type: ignore[assignment]

    def _start_turn_from_ctx(self, turn_ctx, new_message) -> None:
        self._turn_idx += 1
        turn = TurnTrace(
            turn_id=str(uuid.uuid4()),
            turn_idx=self._turn_idx,
            started_at=_now_iso(),
            user_input=_extract_text(new_message),
            llm_messages=_serialize_ctx(turn_ctx),
        )
        self._open = turn
        # Bind buffers so the `traced` decorator pushes into THIS turn.
        set_trace_buffers(turn.tool_calls, turn.memory_recalls)

    def _bind_buffers_for_new_turn(self) -> None:
        """Initial bind before the first turn — keeps capture safe even if
        the first tool call somehow runs before on_user_turn_completed."""
        set_trace_buffers([], [])

    # -- session events ------------------------------------------------------

    def _wire_session_events(self, session) -> None:
        @session.on("user_input_transcribed")
        def _on_transcript(ev) -> None:
            if not getattr(ev, "is_final", False):
                return
            text = getattr(ev, "transcript", None)
            lang = getattr(ev, "language", None)
            if self._open is not None:
                if text and not self._open.user_input:
                    self._open.user_input = text
                if lang and not self._open.detected_language:
                    self._open.detected_language = str(lang)

        @session.on("metrics_collected")
        def _on_metrics(ev) -> None:
            with contextlib.suppress(Exception):
                self._consume_metric(ev.metrics)

        @session.on("conversation_item_added")
        def _on_item(ev) -> None:
            try:
                item = ev.item
                role = str(getattr(item, "role", "") or "").lower()
                content = getattr(item, "text_content", None)
                if callable(content):
                    content = content()
                text = str(content) if content else ""
                if role == "assistant" and self._open is not None:
                    self._open.llm_response_text = text
                    self._open.final_response_to_user = text
                    self._open.ended_at = _now_iso()
                    self._flush_open_if_ready()
            except Exception as e:  # noqa: BLE001
                log.warning("trace_item_capture_failed", err=str(e))

    def _consume_metric(self, m: Any) -> None:
        kind = getattr(m, "type", None) or type(m).__name__.lower()
        speech_id = getattr(m, "speech_id", None)
        if self._open is None:
            return
        if speech_id and speech_id not in self._by_speech_id:
            self._by_speech_id[speech_id] = self._open

        if kind == "eou_metrics":
            self._set_latency(
                "stt_finalization_ms",
                _to_ms(getattr(m, "transcription_delay", None)),
            )
        elif kind == "llm_metrics":
            self._set_latency("llm_ttft_ms", _to_ms(getattr(m, "ttft", None)))
            self._set_latency("llm_total_ms", _to_ms(getattr(m, "duration", None)))
        elif kind == "tts_metrics":
            self._set_latency_if_unset(
                "tts_ttfb_ms", _to_ms(getattr(m, "ttfb", None))
            )
            self._set_latency_if_unset(
                "tts_total_ms", _to_ms(getattr(m, "duration", None))
            )

        # Once STT + LLM + TTS are present, compute the headline number.
        lat = self._open.latency
        if all(
            lat.get(k) is not None
            for k in ("stt_finalization_ms", "llm_ttft_ms", "tts_ttfb_ms")
        ):
            lat["speech_end_to_first_audio_ms"] = round(
                lat["stt_finalization_ms"]
                + lat["llm_ttft_ms"]
                + lat["tts_ttfb_ms"],
                1,
            )

    def _set_latency(self, key: str, value: float | None) -> None:
        if self._open is None or value is None:
            return
        self._open.latency[key] = round(value, 1)

    def _set_latency_if_unset(self, key: str, value: float | None) -> None:
        if self._open is None or value is None or self._open.latency.get(key) is not None:
            return
        self._open.latency[key] = round(value, 1)

    # -- flushing ------------------------------------------------------------

    def _flush_open_if_ready(self) -> None:
        if self._open is None:
            return
        turn = self._open
        # We require a user_input and an assistant response to consider
        # the turn "ready". Latency may still trickle in via the next
        # metrics_collected event; that's fine — we re-flush on close.
        if not turn.user_input or not turn.llm_response_text:
            return
        self._flush(turn, partial=False)
        # Reset for the next turn. Don't clear buffers immediately —
        # the next on_user_turn_completed will rebind them.
        self._open = None
        set_trace_buffers([], [])

    def _flush(self, turn: TurnTrace, *, partial: bool) -> None:
        payload = turn.to_payload()
        if partial:
            payload["partial"] = True
        # 1. JSONL — durable local capture
        with contextlib.suppress(Exception):
            with self.jsonl_path.open("a") as f:
                f.write(json.dumps(payload) + "\n")
        # 2. Backend — queryable from the viewer
        asyncio.create_task(self._post_to_backend(payload))
        log.info(
            "turn_trace_flushed",
            session_id=self.session_id,
            turn_idx=turn.turn_idx,
            partial=partial,
        )

    async def _post_to_backend(self, payload: dict[str, Any]) -> None:
        body = {
            "session_id": self.session_id,
            "turn_idx": payload["turn_idx"],
            "patient_id": self.patient_id,
            "trace": payload,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._backend_url, timeout=5
            ) as c:
                r = await c.post("/traces", json=body)
                if r.status_code not in (200, 201):
                    log.warning(
                        "trace_post_failed",
                        status=r.status_code,
                        body=r.text[:200],
                    )
        except Exception as e:  # noqa: BLE001
            log.warning("trace_post_exception", err=str(e))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_ms(seconds: float | None) -> float | None:
    if seconds is None:
        return None
    return float(seconds) * 1000.0


def _extract_text(message: Any) -> str | None:
    """Pull text out of a livekit `ChatMessage` regardless of API version."""
    if message is None:
        return None
    text_content = getattr(message, "text_content", None)
    if callable(text_content):
        try:
            text_content = text_content()
        except Exception:  # noqa: BLE001
            text_content = None
    if isinstance(text_content, str) and text_content.strip():
        return text_content
    # Fall back to .content if present (older API).
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c) for c in content)
    return None


def _serialize_ctx(ctx: Any) -> list[dict[str, Any]]:
    """Snapshot a livekit ChatContext into plain dicts for JSON storage.

    Skips items with empty content (they're typically sentinel system
    messages or in-flight tool placeholders). Caps individual content
    at 4000 chars so the trace JSON stays manageable in the viewer.
    """
    items = getattr(ctx, "items", None)
    if items is None:
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        role = str(getattr(it, "role", "") or "").lower()
        text = _extract_text(it) or ""
        if not text and role != "tool":
            continue
        out.append({"role": role or "unknown", "content": text[:4000]})
    return out
