from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_db
from backend.schemas.memory import (
    MemoryCreate,
    MemoryRead,
    MemoryRecallRequest,
    SummarizeRequest,
    SummarizeResponse,
)
from backend.services.memory_store import recall, remember, summarize_and_persist

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", response_model=MemoryRead, status_code=201)
async def create_memory(payload: MemoryCreate, db: AsyncSession = Depends(get_db)):
    return await remember(db, payload.patient_id, payload.content, payload.kind)


@router.post("/recall", response_model=list[str])
async def recall_memories(
    payload: MemoryRecallRequest, db: AsyncSession = Depends(get_db)
):
    return await recall(db, payload.patient_id, payload.query, payload.k)


@router.post("/summarize", response_model=SummarizeResponse)
async def summarize(payload: SummarizeRequest, db: AsyncSession = Depends(get_db)):
    stored = await summarize_and_persist(db, payload.patient_id, payload.transcript)
    return SummarizeResponse(facts_stored=len(stored))
