"""API 요청/응답 스키마."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Action = Literal["block", "review", "allow"]


class RuleCreate(BaseModel):
    rule_value: str = Field(
        ..., min_length=1, max_length=255, description="등록할 단어", examples=["시발"]
    )
    action: Action = Field("block", description="block=즉시 차단 / review=보류 / allow=예외")
    expand_variants: bool = Field(True, description="초성·공백·특수문자 변형까지 잡을지")


class RuleUpdate(BaseModel):
    action: Action | None = None
    expand_variants: bool | None = None
    enabled: bool | None = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    rule_value: str
    action: Action
    expand_variants: bool
    enabled: bool
    compiled_regex: str
    created_at: datetime


class CheckRequest(BaseModel):
    channel_id: int = Field(..., examples=[1])
    text: str = Field(..., min_length=1, examples=["ㅅㅂ 못하네"])


class CheckResponse(BaseModel):
    verdict: Literal["block", "review", "pass"]
    stage: str = "rule_based"
    matched_rule_id: int | None = None
    matched_pattern: str | None = None
    matched_text: str | None = None
    reason: str
