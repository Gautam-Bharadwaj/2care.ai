"""Tier 1 of the memory system: session memory in Redis.

`SessionMemory` is the agent's short-term working state during a single
call. It lives in a Redis hash keyed by `session:{patient_id}:{session_id}`,
expires 30 minutes after the last write, and gets cleared on session end.

Keep it small — anything that should outlive the call belongs in the
long-term semantic memory (`backend/services/memory_store.py`) or the
structured DB.
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis
import structlog

from config import get_settings

log = structlog.get_logger("agent.memory")

SESSION_TTL_SECONDS = 30 * 60  # 30 minutes, refreshed on every write

# Default field schema (used by snapshot() so the LLM sees a stable shape
# even when nothing has been written yet).
DEFAULT_FIELDS: dict[str, Any] = {
    "current_intent": None,            # 'booking' | 'rescheduling' | 'cancelling' | 'chitchat'
    "pending_slot_id": None,           # slot we're about to confirm with the caller
    "last_doctor_mentioned": None,     # for "with that same doctor" follow-ups
    "language_locked": False,          # once a language is locked in, don't switch mid-call
    "confirmation_pending": None,      # {"kind": "book|cancel|reschedule", "details": "..."}
}


class SessionMemory:
    """Per-call working state, namespaced by patient + session."""

    def __init__(
        self,
        patient_id: str,
        session_id: str,
        redis_client: redis.Redis | None = None,
    ) -> None:
        if not patient_id or not session_id:
            raise ValueError("patient_id and session_id are required")
        self.patient_id = str(patient_id)
        self.session_id = str(session_id)
        self.key = f"session:{self.patient_id}:{self.session_id}"
        self._redis = redis_client or self._default_client()

    @staticmethod
    def _default_client() -> redis.Redis:
        return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)

    # --- read ---------------------------------------------------------------

    async def get(self, field: str) -> Any:
        raw = await self._redis.hget(self.key, field)
        return _decode(raw)

    async def snapshot(self) -> dict[str, Any]:
        """Return the full hash, merged over the default schema.

        Suitable for direct injection into the system prompt.
        """
        raw = await self._redis.hgetall(self.key)
        snap = dict(DEFAULT_FIELDS)
        for k, v in raw.items():
            snap[k] = _decode(v)
        return snap

    # --- write --------------------------------------------------------------

    async def set(self, field: str, value: Any) -> None:
        """Set one field and refresh the hash's TTL."""
        async with self._redis.pipeline() as pipe:
            pipe.hset(self.key, field, _encode(value))
            pipe.expire(self.key, SESSION_TTL_SECONDS)
            await pipe.execute()
        log.info("session_set", key=self.key, field=field)

    async def update(self, **fields: Any) -> None:
        """Set several fields atomically and refresh TTL."""
        if not fields:
            return
        mapping = {k: _encode(v) for k, v in fields.items()}
        async with self._redis.pipeline() as pipe:
            pipe.hset(self.key, mapping=mapping)
            pipe.expire(self.key, SESSION_TTL_SECONDS)
            await pipe.execute()
        log.info("session_update", key=self.key, fields=list(fields))

    async def clear(self) -> None:
        await self._redis.delete(self.key)
        log.info("session_cleared", key=self.key)

    async def close(self) -> None:
        await self._redis.aclose()


def _encode(value: Any) -> str:
    return json.dumps(value, default=str)


def _decode(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
