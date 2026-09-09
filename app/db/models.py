"""ORM 모델.

MVP① 범위에 필요한 3개만 정의한다.
나머지 초안 테이블(workspaces, actions 등)은 아직 손대지 않는다.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pgvector.sqlalchemy import Vector

from app.db.base import Base

# 임베딩 차원. text-embedding-3-small 의 기본값이다.
EMBEDDING_DIM = 1536


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(64))
    channel_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ai_consent_agreed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 이 채널에서 쓰이는 표현을 LLM 에게 알려주는 문장들.
    # 예: "ㄹㅈㄷ : '레전드'의 초성. 감탄 표현이며 비하가 아니다."
    # 프롬프트 맨 뒤에 붙는다 — 앞부분이 고정이어야 캐싱이 걸리기 때문.
    # 관리자가 직접 쓰는 게 아니라, 검토 이력에서 패턴을 찾아 시스템이 제안한다.
    context: Mapped[str | None] = mapped_column(Text, nullable=True)

    rules: Mapped[list["ChannelRule"]] = relationship(back_populates="channel")


class ChannelRule(Base):
    """채널별로 관리자가 등록한 단어 규칙 (F_R_108)."""

    __tablename__ = "channel_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)

    rule_type: Mapped[str] = mapped_column(String(20), default="keyword")

    # 관리자가 입력한 원문. 화면에 보여주고, 수정할 때 쓰는 값.
    rule_value: Mapped[str] = mapped_column(String(255))

    # rule_value로부터 자동 생성한 정규식. 매칭 엔진이 쓰는 값.
    # 등록/수정 시 한 번만 만들어 저장한다 (댓글마다 다시 만들지 않으려고).
    compiled_regex: Mapped[str] = mapped_column(Text)

    # block=즉시 차단 / review=보류 표시 / allow=예외(통과)
    action: Mapped[str] = mapped_column(String(10), default="block")

    # 초성·공백·특수문자 변형까지 잡을지. 고유명사는 꺼두는 게 안전하다.
    expand_variants: Mapped[bool] = mapped_column(Boolean, default=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    channel: Mapped["Channel"] = relationship(back_populates="rules")


class Video(Base):
    """수집 대상 영상.

    topic 은 유튜브가 주는 값이 아니라 우리가 붙이는 라벨이다(정치·범죄·연예…).
    채널이 아니라 '사건' 단위로 데이터를 모으면 채널당 댓글이 흩어져서
    채널별 패턴이 안 보인다. 그래서 topic 을 채널 대신 묶는 축으로 쓴다.
    기획서의 업종별 규칙(F_R_127)에서 '업종' 자리에 들어가는 값이기도 하다.
    """

    __tablename__ = "videos"

    video_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)

    topic: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    # 유튜브가 알려주는 총 댓글 수. 우리가 받은 건수와 비교해야
    # "이 표본이 전체의 몇 %인가"에 답할 수 있다.
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now()
    )


class Comment(Base):
    """수집된 댓글 (F_R_107). MVP①에서는 판정 테스트 입력으로만 쓴다."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)

    # 재수집해도 중복이 안 생기도록 unique. upsert의 충돌 기준 컬럼이다.
    youtube_comment_id: Mapped[str] = mapped_column(String(64), unique=True)
    video_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 답글이면 부모 댓글 ID. 3차 LLM에 맥락으로 넣는다 —
    # "그만해라" 같은 댓글은 부모를 봐야 사람도 판단할 수 있다.
    parent_comment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_reply: Mapped[bool] = mapped_column(Boolean, default=False)

    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 반복 악플러 추적 · 채널차단(banAuthor) 대상 식별에 쓴다.
    author_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    content: Mapped[str] = mapped_column(Text)

    # 우선순위 신호. 확산도(좋아요)와 논쟁 강도(답글 수) — 둘 다 수집 시 공짜로 온다.
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    total_reply_count: Mapped[int] = mapped_column(Integer, default=0)

    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 유튜브상 수정 시각. published_at 과 다르면 수정된 댓글 → 재판별 대상.
    content_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())

    # 원문·작성자 정보 파기 기한. 이후엔 파생 지표만 남긴다 (YouTube API 정책).
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # 과거 유사 사례 검색용 벡터 (F_R_114).
    # '이 댓글과 비슷한 걸 예전에 관리자가 어떻게 처리했나'를 보여주려는 것.
    # 판단 근거를 사람에게 주는 게 목적이라 같은 채널 안에서만 찾는다 —
    # 채널간 데이터 결합은 YouTube API 정책상 금지다.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )

    # 파이프라인 통과 후의 현재 상태.
    #   pending  아직 판별 안 함
    #   passed   그대로 공개
    #   queued   검토 큐 (관리자가 봐야 함)
    #   hidden   가려짐
    # 관리자가 조치하면 여기가 바뀌고 actions 에 이력이 남는다.
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    # 관리자가 처리한 시각. NULL 이면 아직 안 본 것 —
    # 검토 큐는 status='queued' AND reviewed_at IS NULL 로 뽑는다.
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RiskAssessment(Base):
    """판별 결과 1건.

    댓글 하나에 여러 행이 쌓일 수 있다 — 프롬프트를 고쳐 재판별하면
    이전 판정을 지우지 않고 새 행을 넣는다. 무엇이 언제 왜 바뀌었는지가
    남아야 관리자에게 설명할 수 있고, 우리도 프롬프트 변경 효과를 본다.
    """

    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), index=True)

    # 최종 결정을 내린 단계: rule(관리자 등록어) / llm
    stage: Mapped[str] = mapped_column(String(10))

    # 이 판정이 댓글을 어디로 보냈나 (hidden / queue_judge / queue_info / passed)
    destination: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    # LLM 결과. 규칙에서 끝났으면 비어 있다.
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)   # harmful/ambiguous/safe
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # 규칙에 걸렸으면 무엇에 걸렸는지. 관리자에게 "이 단어 때문"이라고
    # 말해줄 수 있어야 해서 값까지 같이 남긴다.
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_action: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now()
    )


class Action(Base):
    """관리자 조치 이력 (F_R_115).

    실제 유튜브 반영은 채널 소유자 OAuth 가 있어야 한다. 지금은 우리 채널이
    없어서 DB 에만 기록한다 — youtube_synced 가 그 구분이다.
    """

    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), index=True)

    # hide(숨김) / keep(유지) / ban_author(채널차단)
    action_type: Mapped[str] = mapped_column(String(20))

    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 유튜브에 실제로 반영됐는지. OAuth 붙기 전까지는 항상 False.
    youtube_synced: Mapped[bool] = mapped_column(Boolean, default=False)

    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now()
    )
