"""Reasoning-trace ingestion + read endpoints.

- `POST /traces` — agent writes one row per LLM turn. Upserts on
  (session_id, turn_idx) so a re-flush during shutdown doesn't duplicate.
- `GET /traces/{session_id}` — the trace viewer reads the full
  ordered timeline for one call.
- `GET /traces` — list distinct session_ids (most recent first) so the
  viewer can offer a session picker without forcing the operator to
  memorize UUIDs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ConversationTurn
from backend.db.session import get_db
from backend.schemas.trace import SessionTraces, TraceCreate, TraceRead

router = APIRouter(prefix="/traces", tags=["traces"])


@router.post("", response_model=TraceRead, status_code=201)
async def create_trace(payload: TraceCreate, db: AsyncSession = Depends(get_db)):
    """Insert a turn trace. Idempotent on (session_id, turn_idx) — if the
    agent reflushes the same turn (shutdown race), the second write
    overwrites the trace JSON rather than failing on the unique
    constraint."""
    values = {
        "session_id": payload.session_id,
        "turn_idx": payload.turn_idx,
        "patient_id": payload.patient_id,
        "trace": payload.trace.model_dump(mode="json"),
    }
    stmt = (
        pg_insert(ConversationTurn)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["session_id", "turn_idx"],
            set_={"trace": values["trace"], "patient_id": values["patient_id"]},
        )
        .returning(ConversationTurn)
    )
    row = (await db.execute(stmt)).scalar_one()
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/{session_id}", response_model=SessionTraces)
async def get_session_traces(session_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(ConversationTurn)
        .where(ConversationTurn.session_id == session_id)
        .order_by(ConversationTurn.turn_idx)
    )
    turns = list((await db.execute(stmt)).scalars().all())
    if not turns:
        raise HTTPException(
            status_code=404, detail=f"no traces for session {session_id}"
        )
    return {"session_id": session_id, "turns": turns}


@router.get("", response_model=list[dict])
async def list_sessions(
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Distinct session_ids in the trace store, newest first.

    Returns shape: `[{session_id, turn_count, last_turn_at}, ...]` for the
    session picker in the viewer. We don't bother with auth here — same
    as `/metrics/latency`, this is an internal ops surface.
    """
    stmt = (
        select(
            ConversationTurn.session_id,
            func.count(ConversationTurn.id).label("turn_count"),
            func.max(ConversationTurn.created_at).label("last_turn_at"),
        )
        .group_by(ConversationTurn.session_id)
        .order_by(desc("last_turn_at"))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "session_id": r.session_id,
            "turn_count": r.turn_count,
            "last_turn_at": r.last_turn_at.isoformat() if r.last_turn_at else None,
        }
        for r in rows
    ]
