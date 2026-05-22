"""Operator-facing latency endpoint.

Reads the last N per-turn latency records from Redis (written by the
agent via `agent.metrics.LatencyTracker`) and returns p50/p90/p99 for
each pipeline stage.

This is intentionally a read-only endpoint with no auth — it's an
internal ops dashboard, not patient data. If the backend later grows
auth middleware, this route should require the `staff` role.
"""
from __future__ import annotations

import json
import logging

import redis.asyncio as redis_async
from fastapi import APIRouter, Query

from agent.metrics import LATENCY_BUFFER_KEY, LATENCY_BUFFER_SIZE, summarize
from config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _client() -> redis_async.Redis:
    return redis_async.Redis.from_url(get_settings().redis_url, decode_responses=True)


async def _load_recent(limit: int) -> list[dict]:
    """Pull the freshest `limit` records out of `latency:last100`.

    The agent LPUSH-es so index 0 is the newest turn. We return them in
    that order — most-recent-first matches what an operator scanning the
    JSON output expects to see.
    """
    client = _client()
    try:
        # LRANGE 0 -1 returns the whole buffer.
        raw = await client.lrange(LATENCY_BUFFER_KEY, 0, max(0, limit - 1))
    finally:
        await client.aclose()

    out: list[dict] = []
    for line in raw:
        try:
            out.append(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            # Skip malformed entries — better partial data than 500.
            continue
    return out


@router.get("/latency")
async def latency_summary(
    limit: int = Query(default=LATENCY_BUFFER_SIZE, ge=1, le=LATENCY_BUFFER_SIZE),
    include_raw: bool = Query(
        default=False, description="Include the raw per-turn records under `recent`."
    ),
):
    """Aggregate latency over the last N turns.

    Sample shape:

    ```json
    {
      "sample_size": 47,
      "speech_end_to_first_audio_ms": {"n": 47, "p50": 432.0, "p90": 612.0, "p99": 951.0},
      "stt_finalization_ms": {"n": 47, "p50": 118.0, ...},
      "llm_ttft_ms": {...},
      "tts_ttfb_ms": {...},
      ...
    }
    ```
    """
    records = await _load_recent(limit)
    body: dict = summarize(records)
    if include_raw:
        body["recent"] = records
    return body
