"""Operator healthcheck.

Returns a compound status object covering:

- Postgres (SELECT 1)
- Redis (PING)
- Last successful Groq LLM call (from `health:last_llm_call_ts`)
- Last successful Cartesia TTS call (from `health:last_tts_call_ts`)

Top-level `status` is "ok" iff DB + Redis are both reachable. Provider
freshness is reported but not fatal — a long idle window doesn't mean
anything is broken, it might just mean no calls today.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import redis.asyncio as redis_async
from fastapi import APIRouter
from sqlalchemy import text

from agent.metrics import LAST_LLM_CALL_TS_KEY, LAST_TTS_CALL_TS_KEY
from backend.db.session import engine
from config import get_settings

router = APIRouter(tags=["health"])


async def _check_db(timeout: float = 2.0) -> dict:
    """Run `SELECT 1` against Postgres with a short timeout."""
    started = datetime.now(timezone.utc)
    try:
        async with asyncio.timeout(timeout):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return {"ok": True, "latency_ms": _ms_since(started)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200], "latency_ms": _ms_since(started)}


async def _check_redis(timeout: float = 1.0) -> tuple[dict, dict, dict]:
    """PING Redis and read both provider timestamps in one round-trip."""
    settings = get_settings()
    started = datetime.now(timezone.utc)
    client = None
    llm_ts = tts_ts = None
    try:
        client = redis_async.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
        async with asyncio.timeout(timeout):
            async with client.pipeline() as pipe:
                pipe.ping()
                pipe.get(LAST_LLM_CALL_TS_KEY)
                pipe.get(LAST_TTS_CALL_TS_KEY)
                pong, llm_ts, tts_ts = await pipe.execute()
        redis_info = {"ok": bool(pong), "latency_ms": _ms_since(started)}
    except Exception as e:  # noqa: BLE001
        redis_info = {
            "ok": False,
            "error": str(e)[:200],
            "latency_ms": _ms_since(started),
        }
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    return (
        redis_info,
        _provider_status("llm", llm_ts),
        _provider_status("tts", tts_ts),
    )


def _provider_status(name: str, ts: str | None) -> dict:
    """Render a provider's last-seen timestamp + age in human form."""
    if not ts:
        return {"name": name, "last_call_at": None, "age_seconds": None}
    try:
        last = datetime.fromisoformat(ts)
        age = (datetime.now(timezone.utc) - last).total_seconds()
    except ValueError:
        return {"name": name, "last_call_at": ts, "age_seconds": None}
    return {"name": name, "last_call_at": ts, "age_seconds": round(age, 1)}


def _ms_since(started: datetime) -> float:
    return round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 1)


@router.get("/healthz")
async def healthz() -> dict:
    """Compound healthcheck.

    Returns 200 always — the JSON body's `status` field is what
    monitoring should read. Returning 503 on degraded state would be
    nicer for k8s probes but breaks dashboards that key off body content.
    Add a `?strict=true` query param if you need probe-friendly behavior.
    """
    db_status, (redis_status, llm_status, tts_status) = await asyncio.gather(
        _check_db(), _check_redis()
    )
    overall_ok = bool(db_status.get("ok") and redis_status.get("ok"))
    return {
        "status": "ok" if overall_ok else "degraded",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "db": db_status,
        "redis": redis_status,
        "providers": {
            "llm": llm_status,
            "tts": tts_status,
        },
    }
