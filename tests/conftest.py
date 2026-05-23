"""Pytest fixtures for the scheduling tests.

Uses a dedicated `twocare_test` database. Schema is created from
`Base.metadata` (no alembic) at the start of the test session and torn down
at the end. Each test gets its own session, but the engine and DB are shared
across the session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db.base import Base
from backend.db.models import AvailabilitySlot, Doctor, Patient

ADMIN_DATABASE_URL = "postgresql+asyncpg://twocare:twocare@localhost:5432/postgres"
TEST_DATABASE_URL = "postgresql+asyncpg://twocare:twocare@localhost:5432/twocare_test"


async def _ensure_test_db() -> None:
    admin = create_async_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'twocare_test'")
            )
            if result.scalar() is None:
                await conn.execute(text("CREATE DATABASE twocare_test"))
    finally:
        await admin.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    await _ensure_test_db()
    eng = create_async_engine(TEST_DATABASE_URL)

    # Wipe + recreate so each test session starts from a clean slate
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture(loop_scope="session")
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture(loop_scope="session")
async def doctor(session):
    d = Doctor(
        name=f"Dr. Test {uuid4().hex[:6]}",
        specialty="General Medicine",
        languages_spoken=["en"],
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return d


@pytest_asyncio.fixture(loop_scope="session")
async def patient(session):
    p = Patient(
        name="Test Patient",
        phone=f"+1{uuid4().hex[:10]}",
        preferred_language="en",
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def future_slot(session, doctor):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(minutes=30)
    slot = AvailabilitySlot(doctor_id=doctor.id, start_time=start, end_time=end)
    session.add(slot)
    await session.commit()
    await session.refresh(slot)
    return slot
