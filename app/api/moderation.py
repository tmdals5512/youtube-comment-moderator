"""댓글 판정 (F_R_109)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas import CheckRequest, CheckResponse
from app.services.moderation import judge

router = APIRouter(prefix="/moderation", tags=["moderation"])


@router.post("/check", response_model=CheckResponse, summary="댓글 1건 판정")
async def check(payload: CheckRequest, db: AsyncSession = Depends(get_db)):
    v = await judge(db, payload.channel_id, payload.text)
    return CheckResponse(
        verdict=v.verdict,
        matched_rule_id=v.matched_rule_id,
        matched_pattern=v.matched_pattern,
        matched_text=v.matched_text,
        reason=v.reason,
    )
