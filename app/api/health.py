"""헬스체크 라우터."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> dict[str, str]:
    """앱 자체가 살아있는지만 확인 (DB 접속 안 함)."""
    return {"status": "ok"}


@router.get("/db")
async def health_db(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """DB 접속 + pgvector 익스텐션 활성화 여부까지 확인."""
    version = (await db.execute(text("SELECT version()"))).scalar_one()
    vector_version = (
        await db.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
    ).scalar_one_or_none()

    return {
        "status": "ok",
        "postgres": version,
        "pgvector": vector_version or "not installed",
    }
