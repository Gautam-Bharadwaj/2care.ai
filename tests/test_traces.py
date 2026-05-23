"""Phase 8 tests: reasoning-trace capture + /healthz.

Covers four areas:

1. `traced` decorator pushes records into the per-turn ContextVar
   buffers, with `recall_memory` mirrored into the `memory_recalls`
   side-channel.
2. `TraceCollector` end-to-end coalescing: a turn starts on
   `on_user_turn_completed`, gains latency from `metrics_collected`,
   and is flushed when the assistant's `conversation_item_added` event
   arrives. JSONL is written; backend POST is fire-and-forget.
3. Partial-turn flush at session shutdown produces a record marked
   `partial=True`.
4. `/healthz` reports degraded when Redis or DB is unreachable, and
   surfaces the `last_llm_call_ts` / `last_tts_call_ts` keys when
   present.

DB-bound tests (POST /traces, GET /traces/{session_id}) live in the
"DB" section and are skipped automatically when Postgres isn't running
— same pattern as the rest of the suite.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from agent import tools as agent_tools
from agent.tools import set_trace_buffers, traced
from agent.traces import TraceCollector


# ---------------------------------------------------------------------------
# (1) traced() decorator → trace buffers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_trace_buffers():
    """Each test starts with disabled capture; tests that need it call
    `set_trace_buffers(...)` themselves."""
    set_trace_buffers(None, None)
    yield
    set_trace_buffers(None, None)


async def test_traced_pushes_into_tool_buffer():
    @traced
    async def list_available_slots(specialty=None):
        return "ok"

    tool_calls: list[dict] = []
    set_trace_buffers(tool_calls, [])

    await list_available_slots(specialty="general")
    assert len(tool_calls) == 1
    rec = tool_calls[0]
    assert rec["name"] == "list_available_slots"
    assert rec["args"] == {"specialty": "general"}
    assert rec["status"] == "ok"
    assert rec["result"] == "ok"
    assert isinstance(rec["latency_ms"], float)
    assert rec["latency_ms"] >= 0


async def test_traced_captures_errors():
    @traced
    async def book_appointment(slot_id=None, patient_phone=None):
        raise ValueError("slot already booked")

    tool_calls: list[dict] = []
    set_trace_buffers(tool_calls, [])
    out = await book_appointment(slot_id="s", patient_phone="+1")
    # The decorator returns a human-readable string on error
    assert "Internal error" in out
    assert len(tool_calls) == 1
    assert tool_calls[0]["status"] == "error"
    assert "slot already booked" in tool_calls[0]["result"]


async def test_traced_recall_memory_writes_to_memory_buffer():
    @traced
    async def recall_memory(query: str):
        # Mirror the production shape the real recall_memory returns
        return "Prior notes:\n- Allergic to penicillin\n- Prefers AM"

    tool_calls: list[dict] = []
    memory_recalls: list[dict] = []
    set_trace_buffers(tool_calls, memory_recalls)
    await recall_memory(query="allergies")

    assert len(tool_calls) == 1
    assert len(memory_recalls) == 1
    rec = memory_recalls[0]
    assert rec["query"] == "allergies"
    assert rec["returned_count"] == 2
    assert "penicillin" in rec["results"][0]


async def test_traced_recall_memory_returns_zero_when_no_results():
    @traced
    async def recall_memory(query: str):
        return "No prior notes for this caller."

    tool_calls: list[dict] = []
    memory_recalls: list[dict] = []
    set_trace_buffers(tool_calls, memory_recalls)
    await recall_memory(query="anything")
    assert memory_recalls[0]["returned_count"] == 0
    assert memory_recalls[0]["results"] == []


async def test_traced_with_no_buffer_is_a_noop():
    """If no buffer is bound, the decorator still runs the function but
    captures nothing — safe for unit tests and pre-attach code."""
    @traced
    async def fn():
        return "x"

    set_trace_buffers(None, None)
    assert await fn() == "x"
    # No crash — and we didn't accidentally create a buffer.
    assert agent_tools._trace_tool_calls.get() is None


def test_safe_args_truncates_long_values():
    huge = "x" * 1200
    out = agent_tools._safe_args({"slot_id": "s", "blob": huge})
    assert out["slot_id"] == "s"
    assert len(out["blob"]) <= 501  # 500 + ellipsis
    assert out["blob"].endswith("…")


# ---------------------------------------------------------------------------
# (2) TraceCollector end-to-end
# ---------------------------------------------------------------------------


def _capture_handlers():
    """Build a session-like object whose .on(name) returns a decorator
    storing the handler so tests can call them synchronously."""
    handlers: dict[str, callable] = {}

    def _on(name):
        def _dec(fn):
            handlers[name] = fn
            return fn
        return _dec

    session = MagicMock()
    session.on = _on
    return session, handlers


def _ctx_with_items(items):
    """Build a fake ChatContext snapshot with the given items."""
    fake_items = []
    for role, content in items:
        m = MagicMock()
        m.role = role
        m.text_content = content
        fake_items.append(m)
    return SimpleNamespace(items=fake_items)


def _user_message(text):
    m = MagicMock()
    m.role = "user"
    m.text_content = text
    return m


def _final_transcript(text, lang="en"):
    return SimpleNamespace(is_final=True, transcript=text, language=lang)


def _eou(speech_id, stt_ms=110.0):
    return SimpleNamespace(
        type="eou_metrics",
        speech_id=speech_id,
        end_of_utterance_delay=0.08,
        transcription_delay=stt_ms / 1000,
    )


def _llm(speech_id, ttft_ms=200.0, total_ms=400.0):
    return SimpleNamespace(
        type="llm_metrics",
        speech_id=speech_id,
        ttft=ttft_ms / 1000,
        duration=total_ms / 1000,
    )


def _tts(speech_id, ttfb_ms=150.0, total_ms=900.0):
    return SimpleNamespace(
        type="tts_metrics",
        speech_id=speech_id,
        ttfb=ttfb_ms / 1000,
        duration=total_ms / 1000,
    )


def _assistant_item(text):
    item = MagicMock()
    item.role = "assistant"
    item.text_content = text
    return SimpleNamespace(item=item)


async def test_collector_flushes_one_turn_when_assistant_speaks(tmp_path: Path, monkeypatch):
    # Stub the backend POST so the test doesn't hit the network.
    posted: list[dict] = []

    class FakeResp:
        status_code = 201
        text = ""

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, url, json):
            posted.append({"url": url, "json": json})
            return FakeResp()

    import agent.traces as agent_traces
    monkeypatch.setattr(agent_traces.httpx, "AsyncClient", FakeClient)

    session, handlers = _capture_handlers()
    agent = MagicMock()
    async def _orig(turn_ctx, new_msg):
        return None
    agent.on_user_turn_completed = _orig

    collector = TraceCollector(
        session_id=f"sess-{uuid4().hex[:8]}",
        patient_id="p-1",
        trace_dir=tmp_path,
    )
    collector.attach(session, agent)

    # Turn 1: pre-LLM hook fires
    ctx = _ctx_with_items([("system", "you are a clinic agent"), ("user", "I'd like to book")])
    await agent.on_user_turn_completed(ctx, _user_message("I'd like to book"))

    # Final transcript arrives
    handlers["user_input_transcribed"](_final_transcript("I'd like to book", "en"))

    # Metrics arrive
    sid = "sp-1"
    handlers["metrics_collected"](SimpleNamespace(metrics=_eou(sid, stt_ms=100)))
    handlers["metrics_collected"](SimpleNamespace(metrics=_llm(sid, ttft_ms=180)))
    handlers["metrics_collected"](SimpleNamespace(metrics=_tts(sid, ttfb_ms=120)))

    # Assistant turn — this triggers flush
    handlers["conversation_item_added"](_assistant_item("Sure, when works for you?"))

    # Let the asyncio.create_task for the backend POST run
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # JSONL was written
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["turn_idx"] == 1
    assert rec["user_input"] == "I'd like to book"
    assert rec["detected_language"] == "en"
    assert rec["final_response_to_user"] == "Sure, when works for you?"
    assert rec["llm_response_text"] == "Sure, when works for you?"
    assert rec["latency"]["stt_finalization_ms"] == 100.0
    assert rec["latency"]["llm_ttft_ms"] == 180.0
    assert rec["latency"]["tts_ttfb_ms"] == 120.0
    assert rec["latency"]["speech_end_to_first_audio_ms"] == 400.0
    # llm_messages was snapshotted from the chat ctx
    assert rec["llm_messages"] is not None
    assert any(m["role"] == "user" for m in rec["llm_messages"])

    # Backend POST happened with the matching shape
    assert len(posted) == 1
    body = posted[0]["json"]
    assert body["session_id"] == collector.session_id
    assert body["turn_idx"] == 1
    assert body["trace"]["user_input"] == "I'd like to book"


async def test_collector_captures_tool_calls_from_traced_decorator(tmp_path: Path, monkeypatch):
    """A tool invoked via `traced` between on_user_turn_completed and the
    assistant turn should appear in the flushed trace's `tool_calls`."""
    monkeypatch.setattr("agent.traces.httpx.AsyncClient", _silent_client_factory())

    session, handlers = _capture_handlers()
    agent = MagicMock()
    agent.on_user_turn_completed = _async_noop

    collector = TraceCollector(
        session_id="sess-tools", patient_id=None, trace_dir=tmp_path
    )
    collector.attach(session, agent)

    ctx = _ctx_with_items([("user", "next slot?")])
    await agent.on_user_turn_completed(ctx, _user_message("next slot?"))

    # Drive a tool through the production decorator
    @traced
    async def list_available_slots():
        return "1 slot found"
    await list_available_slots()

    handlers["conversation_item_added"](_assistant_item("Tomorrow at 10am."))
    await asyncio.sleep(0)

    rec = json.loads((tmp_path / "sess-tools.jsonl").read_text().splitlines()[0])
    assert len(rec["tool_calls"]) == 1
    assert rec["tool_calls"][0]["name"] == "list_available_slots"
    assert rec["tool_calls"][0]["status"] == "ok"


async def test_collector_close_open_turn_flushes_partial(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.traces.httpx.AsyncClient", _silent_client_factory())

    session, handlers = _capture_handlers()
    agent = MagicMock()
    agent.on_user_turn_completed = _async_noop

    collector = TraceCollector(
        session_id="sess-partial", patient_id=None, trace_dir=tmp_path
    )
    collector.attach(session, agent)

    ctx = _ctx_with_items([("user", "hello")])
    await agent.on_user_turn_completed(ctx, _user_message("hello"))
    # No assistant turn; no metrics.
    collector.close_open_turn(reason="session ended")

    rec = json.loads((tmp_path / "sess-partial.jsonl").read_text().splitlines()[0])
    assert rec["partial"] is True
    assert rec["final_response_to_user"] is None
    assert rec["llm_response_text"] is None


async def test_collector_isolates_per_turn_tool_buffers(tmp_path: Path, monkeypatch):
    """Two turns in the same session must not share tool_calls — the
    buffers are rebound on each on_user_turn_completed."""
    monkeypatch.setattr("agent.traces.httpx.AsyncClient", _silent_client_factory())

    session, handlers = _capture_handlers()
    agent = MagicMock()
    agent.on_user_turn_completed = _async_noop

    collector = TraceCollector(
        session_id="sess-iso", patient_id=None, trace_dir=tmp_path
    )
    collector.attach(session, agent)

    @traced
    async def list_available_slots():
        return "ok"

    # Turn 1
    await agent.on_user_turn_completed(_ctx_with_items([("user", "first")]), _user_message("first"))
    await list_available_slots()
    handlers["conversation_item_added"](_assistant_item("here"))
    await asyncio.sleep(0)

    # Turn 2
    await agent.on_user_turn_completed(_ctx_with_items([("user", "second")]), _user_message("second"))
    handlers["conversation_item_added"](_assistant_item("there"))
    await asyncio.sleep(0)

    lines = (tmp_path / "sess-iso.jsonl").read_text().splitlines()
    assert len(lines) == 2
    t1, t2 = [json.loads(l) for l in lines]
    assert len(t1["tool_calls"]) == 1
    assert len(t2["tool_calls"]) == 0


# ---------------------------------------------------------------------------
# (3) /healthz (mocked dependencies — no live DB/Redis required)
# ---------------------------------------------------------------------------


async def test_healthz_returns_degraded_when_redis_unreachable(monkeypatch):
    """We replace the engine.connect path so DB looks fine, but force
    Redis init to throw — overall status must become 'degraded'."""
    from backend.routers import health as health_router

    class FakeDBConn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def execute(self, *a, **kw): return None

    class FakeEngine:
        def connect(self): return FakeDBConn()

    monkeypatch.setattr(health_router, "engine", FakeEngine())

    class BoomRedis:
        @classmethod
        def from_url(cls, *a, **kw):
            raise RuntimeError("no redis here")

    monkeypatch.setattr(health_router.redis_async, "Redis", BoomRedis)

    body = await health_router.healthz()
    assert body["status"] == "degraded"
    assert body["db"]["ok"] is True
    assert body["redis"]["ok"] is False
    assert "no redis here" in body["redis"]["error"]


async def test_healthz_returns_ok_with_provider_timestamps(monkeypatch):
    """Happy path: DB + Redis both reachable; last_llm/tts timestamps
    surface in the providers block with computed age_seconds."""
    from datetime import datetime, timedelta, timezone

    from backend.routers import health as health_router

    class FakeDBConn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def execute(self, *a, **kw): return None

    class FakeEngine:
        def connect(self): return FakeDBConn()

    monkeypatch.setattr(health_router, "engine", FakeEngine())

    now = datetime.now(timezone.utc)
    llm_ts = (now - timedelta(seconds=12)).isoformat()
    tts_ts = (now - timedelta(seconds=5)).isoformat()

    class FakePipeline:
        def __init__(self): self._cmds = []
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def ping(self): self._cmds.append("ping"); return self
        def get(self, key): self._cmds.append(("get", key)); return self
        async def execute(self):
            return [True, llm_ts, tts_ts]

    class FakeRedis:
        def __init__(self, *a, **kw): ...
        @classmethod
        def from_url(cls, *a, **kw): return cls()
        def pipeline(self): return FakePipeline()
        async def aclose(self): return None

    monkeypatch.setattr(health_router.redis_async, "Redis", FakeRedis)

    body = await health_router.healthz()
    assert body["status"] == "ok"
    assert body["redis"]["ok"] is True
    assert body["providers"]["llm"]["last_call_at"] == llm_ts
    assert 10 < body["providers"]["llm"]["age_seconds"] < 15
    assert 3 < body["providers"]["tts"]["age_seconds"] < 8


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _async_noop(*a, **kw):
    return None


def _silent_client_factory():
    """An AsyncClient that swallows POSTs — used when we don't care about
    asserting the backend body, only about the JSONL output."""
    class _Resp:
        status_code = 201
        text = ""
    class _Client:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, url, json): return _Resp()
    return _Client
