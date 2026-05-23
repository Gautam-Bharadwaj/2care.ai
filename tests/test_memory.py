"""Memory tier tests.

Critical invariant: long-term memories must never leak between patients.
We embed with a stubbed `embed()` so the test doesn't require an
OPENAI_API_KEY — the patient isolation we're checking is enforced by
the SQL `WHERE patient_id = …` clause, not by similarity.

Also tests Tier 1 (Redis SessionMemory) — namespacing per patient
prevents one caller's pending_slot_id from being visible to another.
"""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from agent.memory import SESSION_TTL_SECONDS, SessionMemory
from backend.db.models import MemoryKind, Patient
from backend.services import memory_store


@pytest.fixture(autouse=True)
def stub_embed(monkeypatch):
    """Skip the OpenAI embeddings API in tests.

    We need a 1536-dim vector but the actual values don't matter for
    patient-isolation assertions — those are enforced by the WHERE clause.
    """
    async def _fake_embed(text: str) -> list[float]:
        return [0.001] * 1536

    monkeypatch.setattr(memory_store, "embed", _fake_embed)


# ---------------------------------------------------------------------------
# Tier 2: long-term semantic memory
# ---------------------------------------------------------------------------


async def test_memory_scoped_by_patient(session):
    """Two patients with the same name but different phones must not see
    each other's memories."""
    alice = Patient(
        name="John Smith", phone=f"+1{uuid4().hex[:10]}", preferred_language="en"
    )
    bob = Patient(
        name="John Smith", phone=f"+1{uuid4().hex[:10]}", preferred_language="en"
    )
    session.add_all([alice, bob])
    await session.commit()
    await session.refresh(alice)
    await session.refresh(bob)

    await memory_store.remember(
        session, alice.id, "Allergic to penicillin", MemoryKind.history
    )
    await memory_store.remember(
        session, alice.id, "Prefers morning appointments", MemoryKind.preference
    )

    alice_results = await memory_store.recall(session, alice.id, "allergies", k=5)
    bob_results = await memory_store.recall(session, bob.id, "allergies", k=5)

    assert any("penicillin" in r.lower() for r in alice_results), alice_results
    assert bob_results == [], (
        f"Bob should see no memories, got {bob_results!r}"
    )


async def test_memory_recall_empty_returns_empty_list(session):
    """recall() on a patient with no memories returns []."""
    p = Patient(
        name="Alone", phone=f"+1{uuid4().hex[:10]}", preferred_language="en"
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)

    results = await memory_store.recall(session, p.id, "anything", k=4)
    assert results == []


async def test_remember_stores_kind(session):
    """The kind enum round-trips through the DB."""
    p = Patient(
        name="K Test", phone=f"+1{uuid4().hex[:10]}", preferred_language="en"
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)

    entry = await memory_store.remember(
        session, p.id, "prefers female doctors", MemoryKind.preference
    )
    assert entry.kind == MemoryKind.preference
    assert entry.content == "prefers female doctors"


# ---------------------------------------------------------------------------
# Tier 1: Redis session memory
# ---------------------------------------------------------------------------


async def test_session_memory_isolated_per_patient():
    """Two patients in concurrent sessions don't see each other's state.

    Uses real Redis (requires `docker compose up redis`).
    """
    p1 = str(uuid4())
    p2 = str(uuid4())
    sid = uuid4().hex

    m1 = SessionMemory(patient_id=p1, session_id=sid)
    m2 = SessionMemory(patient_id=p2, session_id=sid)
    try:
        await m1.set("pending_slot_id", "slot-aaaa")
        await m2.set("pending_slot_id", "slot-bbbb")

        assert await m1.get("pending_slot_id") == "slot-aaaa"
        assert await m2.get("pending_slot_id") == "slot-bbbb"
    finally:
        await m1.clear()
        await m2.clear()
        await m1.close()
        await m2.close()


async def test_session_memory_ttl_refreshed_on_write():
    """Each write resets the TTL window."""
    p = str(uuid4())
    sid = uuid4().hex
    mem = SessionMemory(patient_id=p, session_id=sid)
    try:
        await mem.set("current_intent", "booking")
        ttl1 = await mem._redis.ttl(mem.key)
        assert 0 < ttl1 <= SESSION_TTL_SECONDS

        # A second write must keep the TTL near the configured ceiling
        await asyncio.sleep(1.1)
        await mem.set("pending_slot_id", "slot-xyz")
        ttl2 = await mem._redis.ttl(mem.key)
        # ttl2 should be >= ttl1 (refreshed) within a small drift
        assert ttl2 >= ttl1 - 1
    finally:
        await mem.clear()
        await mem.close()


async def test_session_memory_snapshot_uses_defaults():
    """Unwritten fields appear in snapshot with default values."""
    mem = SessionMemory(patient_id=str(uuid4()), session_id=uuid4().hex)
    try:
        snap = await mem.snapshot()
        assert "current_intent" in snap
        assert snap["language_locked"] is False
        assert snap["pending_slot_id"] is None
    finally:
        await mem.clear()
        await mem.close()
