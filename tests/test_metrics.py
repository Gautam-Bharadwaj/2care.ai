"""Tests for the per-turn latency tracker + percentile aggregation.

These run without Postgres or a live Redis — the tracker's Redis push is
optional (gated on `redis_url`) so we exercise the in-memory event-coalesce
logic directly.

Covered:

- Stage coalescing: EOU/STT/LLM/TTS events arriving in any order for the
  same `speech_id` produce a single record with the right per-stage
  numbers, including the headline `speech_end_to_first_audio_ms`.
- Partial turns at shutdown: `aclose()` flushes whatever pieces are in
  flight, marking them `partial=true`.
- Multiple concurrent speech_ids don't bleed timings into each other.
- `percentile()` matches a textbook nearest-rank implementation including
  the empty-list edge case.
- `summarize()` ignores records that are missing the relevant field
  rather than crashing.
- Fallback path: a thinking→speaking transition without any
  `metrics_collected` events still produces a record marked
  `fallback=true`.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.metrics import (
    LATENCY_BUFFER_KEY,
    LATENCY_BUFFER_SIZE,
    LatencyTracker,
    TurnTimings,
    percentile,
    summarize,
)


# ---------------------------------------------------------------------------
# percentile() math
# ---------------------------------------------------------------------------


def test_percentile_empty_returns_none():
    assert percentile([], 50) is None
    assert percentile([], 90) is None


def test_percentile_single_value():
    assert percentile([42.0], 50) == 42.0
    assert percentile([42.0], 99) == 42.0


def test_percentile_p50_p90_p99():
    values = [float(i) for i in range(1, 101)]  # 1..100
    # Nearest-rank: p50 of 100 values → index 50 → value 50
    assert percentile(values, 50) == 50.0
    assert percentile(values, 90) == 90.0
    assert percentile(values, 99) == 99.0


def test_percentile_unsorted_input():
    """Input order shouldn't affect output. Nearest-rank with ceil → for
    N=9, p50 = sorted[ceil(4.5)-1] = sorted[4] = 5; p90 = sorted[8] = 9."""
    assert percentile([5, 2, 8, 1, 9, 3, 7, 4, 6], 50) == 5
    assert percentile([5, 2, 8, 1, 9, 3, 7, 4, 6], 90) == 9


def test_percentile_clamps_pct_extremes():
    assert percentile([1, 2, 3], 0) == 1
    assert percentile([1, 2, 3], 100) == 3


# ---------------------------------------------------------------------------
# summarize()
# ---------------------------------------------------------------------------


def test_summarize_on_empty_records_returns_nones():
    out = summarize([])
    assert out["sample_size"] == 0
    for k in (
        "speech_end_to_first_audio_ms",
        "stt_finalization_ms",
        "llm_ttft_ms",
        "tts_ttfb_ms",
    ):
        assert out[k] == {"n": 0, "p50": None, "p90": None, "p99": None}


def test_summarize_ignores_records_missing_field():
    records = [
        {"speech_end_to_first_audio_ms": 400, "llm_ttft_ms": 200, "stt_finalization_ms": 100, "tts_ttfb_ms": 100},
        {"speech_end_to_first_audio_ms": None, "llm_ttft_ms": 300, "stt_finalization_ms": 100, "tts_ttfb_ms": None},
        {"speech_end_to_first_audio_ms": 600, "llm_ttft_ms": 400, "stt_finalization_ms": 100, "tts_ttfb_ms": 100},
    ]
    out = summarize(records)
    assert out["sample_size"] == 3
    # speech_end_to_first_audio_ms collected from 2/3 records
    assert out["speech_end_to_first_audio_ms"]["n"] == 2
    # llm_ttft from all 3
    assert out["llm_ttft_ms"]["n"] == 3
    # tts_ttfb from 2
    assert out["tts_ttfb_ms"]["n"] == 2


def test_summarize_p50_of_known_values():
    records = [
        {"speech_end_to_first_audio_ms": float(v)}
        for v in [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    ]
    out = summarize(records)["speech_end_to_first_audio_ms"]
    assert out["n"] == 10
    assert out["p50"] == 500.0
    assert out["p90"] == 900.0


# ---------------------------------------------------------------------------
# TurnTimings.is_complete + headline_ms
# ---------------------------------------------------------------------------


def test_headline_ms_returns_none_until_all_stages_present():
    t = TurnTimings(speech_id="x")
    assert t.headline_ms() is None
    t.stt_finalization_ms = 100
    assert t.headline_ms() is None
    t.llm_ttft_ms = 200
    assert t.headline_ms() is None
    t.tts_ttfb_ms = 150
    # Headline = sum of three stages
    assert t.headline_ms() == 450.0
    assert t.is_complete()


# ---------------------------------------------------------------------------
# LatencyTracker coalescing
# ---------------------------------------------------------------------------


def _fake_event_handlers():
    """Build a MagicMock session.on(...) decorator that records handlers."""
    handlers: dict[str, callable] = {}

    def _on(name):
        def _decorator(fn):
            handlers[name] = fn
            return fn
        return _decorator

    session = MagicMock()
    session.on = _on
    return session, handlers


def _eou(speech_id, stt_ms=120.0, eou_ms=80.0):
    return SimpleNamespace(
        type="eou_metrics",
        speech_id=speech_id,
        end_of_utterance_delay=eou_ms / 1000,
        transcription_delay=stt_ms / 1000,
    )


def _llm(speech_id, ttft_ms=200.0, total_ms=400.0):
    return SimpleNamespace(
        type="llm_metrics",
        speech_id=speech_id,
        ttft=ttft_ms / 1000,
        duration=total_ms / 1000,
        prompt_tokens=42,
        completion_tokens=18,
    )


def _tts(speech_id, ttfb_ms=150.0, total_ms=900.0, chars=60):
    return SimpleNamespace(
        type="tts_metrics",
        speech_id=speech_id,
        ttfb=ttfb_ms / 1000,
        duration=total_ms / 1000,
        characters_count=chars,
    )


def test_tracker_coalesces_one_turn_into_one_record(tmp_path: Path):
    tracker = LatencyTracker(
        log_path=tmp_path / "lat.jsonl", session_id="s-1", patient_phone="+1"
    )
    session, handlers = _fake_event_handlers()
    tracker.attach(session)

    on_metrics = handlers["metrics_collected"]
    sid = "sp-1"
    on_metrics(SimpleNamespace(metrics=_eou(sid, stt_ms=110, eou_ms=80)))
    on_metrics(SimpleNamespace(metrics=_llm(sid, ttft_ms=220, total_ms=420)))
    on_metrics(SimpleNamespace(metrics=_tts(sid, ttfb_ms=180, total_ms=900)))

    lines = (tmp_path / "lat.jsonl").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["session_id"] == "s-1"
    assert record["turn_idx"] == 1
    assert record["speech_id"] == "sp-1"
    assert record["stt_finalization_ms"] == 110.0
    assert record["llm_ttft_ms"] == 220.0
    assert record["tts_ttfb_ms"] == 180.0
    # 110 + 220 + 180 = 510
    assert record["speech_end_to_first_audio_ms"] == 510.0
    assert record["eou_delay_ms"] == 80.0
    assert record["llm_prompt_tokens"] == 42


def test_tracker_handles_events_out_of_order(tmp_path: Path):
    tracker = LatencyTracker(log_path=tmp_path / "lat.jsonl", session_id="s")
    session, handlers = _fake_event_handlers()
    tracker.attach(session)

    sid = "sp-2"
    handlers["metrics_collected"](SimpleNamespace(metrics=_tts(sid)))
    handlers["metrics_collected"](SimpleNamespace(metrics=_llm(sid)))
    handlers["metrics_collected"](SimpleNamespace(metrics=_eou(sid)))

    lines = (tmp_path / "lat.jsonl").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["speech_id"] == sid
    assert record["speech_end_to_first_audio_ms"] is not None


def test_tracker_isolates_two_concurrent_speech_ids(tmp_path: Path):
    tracker = LatencyTracker(log_path=tmp_path / "lat.jsonl", session_id="s")
    session, handlers = _fake_event_handlers()
    tracker.attach(session)
    on = handlers["metrics_collected"]

    # interleaved
    on(SimpleNamespace(metrics=_eou("a", stt_ms=100)))
    on(SimpleNamespace(metrics=_eou("b", stt_ms=200)))
    on(SimpleNamespace(metrics=_llm("a", ttft_ms=150)))
    on(SimpleNamespace(metrics=_tts("a", ttfb_ms=120)))  # a complete
    on(SimpleNamespace(metrics=_llm("b", ttft_ms=250)))
    on(SimpleNamespace(metrics=_tts("b", ttfb_ms=200)))  # b complete

    lines = [json.loads(l) for l in (tmp_path / "lat.jsonl").read_text().splitlines()]
    assert len(lines) == 2
    by_sid = {r["speech_id"]: r for r in lines}
    # a = 100 + 150 + 120 = 370
    assert by_sid["a"]["speech_end_to_first_audio_ms"] == 370.0
    # b = 200 + 250 + 200 = 650
    assert by_sid["b"]["speech_end_to_first_audio_ms"] == 650.0


def test_tracker_ignores_events_with_no_speech_id(tmp_path: Path):
    tracker = LatencyTracker(log_path=tmp_path / "lat.jsonl", session_id="s")
    session, handlers = _fake_event_handlers()
    tracker.attach(session)

    handlers["metrics_collected"](SimpleNamespace(metrics=SimpleNamespace(
        type="vad_metrics", speech_id=None
    )))
    assert not (tmp_path / "lat.jsonl").exists() or (tmp_path / "lat.jsonl").read_text() == ""


async def test_tracker_aclose_flushes_partial_turns(tmp_path: Path):
    tracker = LatencyTracker(log_path=tmp_path / "lat.jsonl", session_id="s")
    session, handlers = _fake_event_handlers()
    tracker.attach(session)
    # Only EOU+LLM, no TTS — the turn is incomplete.
    handlers["metrics_collected"](SimpleNamespace(metrics=_eou("x")))
    handlers["metrics_collected"](SimpleNamespace(metrics=_llm("x")))

    # Nothing flushed yet
    assert not (tmp_path / "lat.jsonl").exists()

    await tracker.aclose()
    lines = [json.loads(l) for l in (tmp_path / "lat.jsonl").read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["partial"] is True
    assert lines[0]["tts_ttfb_ms"] is None
    # Headline can't be computed
    assert lines[0]["speech_end_to_first_audio_ms"] is None


def test_tracker_fallback_path_when_no_metrics_collected(tmp_path: Path):
    """If `metrics_collected` never fires (legacy realtime pipeline), the
    thinking→speaking timer should still produce a fallback record."""
    tracker = LatencyTracker(log_path=tmp_path / "lat.jsonl", session_id="s")
    session, handlers = _fake_event_handlers()
    tracker.attach(session)

    state = handlers["agent_state_changed"]
    state(SimpleNamespace(new_state="thinking"))
    # Manually crank the timer back so the elapsed delta is observable.
    tracker._thinking_started_at -= 0.300  # 300ms ago
    state(SimpleNamespace(new_state="speaking"))

    lines = [json.loads(l) for l in (tmp_path / "lat.jsonl").read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0].get("fallback") is True
    assert lines[0]["speech_end_to_first_audio_ms"] >= 290


# ---------------------------------------------------------------------------
# Buffer constants
# ---------------------------------------------------------------------------


def test_buffer_constants_match_endpoint_contract():
    """If we ever change these, the /metrics/latency endpoint default
    `limit` query stays in sync."""
    assert LATENCY_BUFFER_KEY == "latency:last100"
    assert LATENCY_BUFFER_SIZE == 100
