"""Reasoning-trace schemas.

A `TurnTracePayload` is the dict shape we store in
`ConversationTurn.trace`. We keep it loose (no required fields beyond
turn_id + role transitions) so the agent can ship traces even when some
stages are missing — the viewer renders whatever is present.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ToolCallRecord(BaseModel):
    name: str
    args: dict[str, Any] = {}
    result: str | None = None
    latency_ms: float | None = None
    status: str | None = None  # "ok" | "error"
    started_at: str | None = None


class MemoryRecallRecord(BaseModel):
    query: str
    returned_count: int
    latency_ms: float | None = None
    results: list[str] | None = None


class TurnLatencyBreakdown(BaseModel):
    stt_finalization_ms: float | None = None
    llm_ttft_ms: float | None = None
    tts_ttfb_ms: float | None = None
    speech_end_to_first_audio_ms: float | None = None


class TurnTracePayload(BaseModel):
    """Free-shape per-turn trace. Keep field names stable — the viewer
    keys off them directly. New fields are fine; renames are not."""

    turn_id: str
    turn_idx: int
    started_at: str
    ended_at: str | None = None
    user_input: str | None = None
    detected_language: str | None = None
    active_language: str | None = None
    llm_messages: list[dict[str, Any]] | None = None
    llm_response_text: str | None = None
    tool_calls: list[ToolCallRecord] = []
    memory_recalls: list[MemoryRecallRecord] = []
    final_response_to_user: str | None = None
    latency: TurnLatencyBreakdown | None = None
    campaign_context: dict[str, Any] | None = None
    notes: str | None = None


class TraceCreate(BaseModel):
    session_id: str
    turn_idx: int
    patient_id: UUID | None = None
    trace: TurnTracePayload


class TraceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: str
    turn_idx: int
    patient_id: UUID | None = None
    trace: dict[str, Any]
    created_at: datetime


class SessionTraces(BaseModel):
    """What `GET /traces/{session_id}` returns."""

    session_id: str
    turns: list[TraceRead]
