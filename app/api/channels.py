"""채널 목록 (화면 드롭다운용)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Channel
from app.db.session import get_db

router = APIRouter(prefix="/channels", tags=["channels"])


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_title: str | None


@router.get("", response_model=list[ChannelOut], summary="채널 목록")
async def list_channels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).order_by(Channel.id))
    return result.scalars().all()
