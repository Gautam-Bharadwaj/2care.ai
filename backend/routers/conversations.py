from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ConversationLog
from backend.db.session import get_db
from backend.schemas.conversation import ConversationLogCreate, ConversationLogRead

router = APIRouter(prefix="/conversation-logs", tags=["conversations"])


@router.post("", response_model=ConversationLogRead, status_code=201)
async def create_conversation_log(
    payload: ConversationLogCreate, db: AsyncSession = Depends(get_db)
):
    started_at = payload.started_at or datetime.now(timezone.utc)
    log = ConversationLog(
        patient_id=payload.patient_id,
        session_id=payload.session_id,
        transcript=payload.transcript,
        language=payload.language,
        started_at=started_at,
        ended_at=payload.ended_at,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


@router.get("", response_model=list[ConversationLogRead])
async def list_conversation_logs(
    patient_id: UUID | None = None,
    session_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ConversationLog)
    if patient_id is not None:
        stmt = stmt.where(ConversationLog.patient_id == patient_id)
    if session_id is not None:
        stmt = stmt.where(ConversationLog.session_id == session_id)
    stmt = stmt.order_by(ConversationLog.started_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()
