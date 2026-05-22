"""Tier 2 of the memory system: durable semantic memory.

Each `Memory` row is one fact about a patient, stored with an OpenAI
`text-embedding-3-small` embedding (1536 dims) and a `kind` enum
(preference / history / note). `recall()` fetches the k nearest rows
SCOPED BY patient_id — the patient filter is enforced in SQL, never in
Python, so a buggy ORM call can't accidentally return another patient's
data.

`summarize_and_persist()` runs at the end of each call: the LLM reads
the transcript and emits a JSON array of durable facts, each of which
is embedded and stored.
"""
from __future__ import annotations

import json
import logging
import re
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Memory, MemoryKind
from backend.services.embeddings import embed
from config import get_settings

logger = logging.getLogger("backend.memory_store")

SUMMARIZE_MODEL = "llama-3.3-70b-versatile"
SUMMARIZE_MAX_FACTS = 5


async def remember(
    db: AsyncSession,
    patient_id: UUID,
    content: str,
    kind: MemoryKind = MemoryKind.note,
) -> Memory:
    """Embed `content` and store as a new Memory row for this patient."""
    vec = await embed(content)
    entry = Memory(
        patient_id=patient_id,
        content=content,
        embedding=vec,
        kind=kind,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def recall(
    db: AsyncSession,
    patient_id: UUID,
    query: str,
    k: int = 4,
) -> list[str]:
    """Return up to k memory contents for `patient_id` ranked by similarity to `query`.

    Patient scoping is enforced in the WHERE clause — the ORDER BY (vector
    distance) is applied AFTER the patient filter, so this can never leak
    another patient's memories.
    """
    vec = await embed(query)
    stmt = (
        select(Memory.content)
        .where(Memory.patient_id == patient_id)
        .order_by(Memory.embedding.l2_distance(vec))
        .limit(k)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def summarize_and_persist(
    db: AsyncSession,
    patient_id: UUID,
    transcript: list[dict],
) -> list[Memory]:
    """Ask the LLM for durable facts about this patient, then store each."""
    if not transcript:
        return []

    convo = _format_transcript(transcript)
    if not convo.strip():
        return []

    facts = await _extract_facts(convo)
    stored: list[Memory] = []
    for fact in facts:
        try:
            kind = MemoryKind(fact.get("kind", "note"))
        except ValueError:
            kind = MemoryKind.note
        content = (fact.get("content") or "").strip()
        if not content:
            continue
        try:
            entry = await remember(db, patient_id, content, kind)
            stored.append(entry)
        except Exception:
            logger.exception("memory_persist_failed content=%r", content[:60])
    return stored


# --- helpers ---------------------------------------------------------------


def _format_transcript(transcript: list[dict]) -> str:
    lines = []
    for turn in transcript:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


_EXTRACT_PROMPT = """\
You are reviewing a clinical reception call transcript. Extract up to {max_facts}
DURABLE facts about the patient that would be useful for future calls.

Good examples:
- "prefers morning appointments"
- "speaks Hindi at home, English at work"
- "anxious about needles"
- "has chronic back pain mentioned during the call"
- "prefers female doctors"

DO NOT include:
- ephemeral things ("called on Tuesday", "asked about Dr. X today")
- the specific appointment they just booked — that's in the structured DB
- guesses or assumptions not supported by the transcript

Return ONLY a JSON array, no commentary. Each item:
  {{"content": "<short, declarative fact>", "kind": "preference|history|note"}}

If there are no durable facts worth keeping, return [].

Transcript:
{transcript}
"""


async def _extract_facts(transcript_text: str) -> list[dict]:
    settings = get_settings()
    prompt = _EXTRACT_PROMPT.format(
        max_facts=SUMMARIZE_MAX_FACTS, transcript=transcript_text
    )

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": SUMMARIZE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
        )
        if r.status_code != 200:
            logger.error("fact_extraction_http_failed status=%s body=%s", r.status_code, r.text[:200])
            return []
        raw = r.json()["choices"][0]["message"]["content"].strip()

    return _parse_facts(raw)


def _parse_facts(raw: str) -> list[dict]:
    """Strip code fences, parse JSON array. Return [] on any failure."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Remove leading and trailing fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("fact_extraction_invalid_json raw=%r", raw[:200])
        return []
    if not isinstance(parsed, list):
        return []
    return [f for f in parsed if isinstance(f, dict)]
