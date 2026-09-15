"""ORM 모델."""

import secrets
from datetime import datetime, timedelta

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

# 로그인 세션 유지 기간.
SESSION_DAYS = 14


class User(Base):
    """Google 로그인으로 만들어지는 계정.

    비밀번호를 받지 않는다. 유튜브 채널을 붙이려면 어차피 구글 인증이
    필요해서, 로그인 수단을 따로 두면 계정이 둘로 갈린다.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 구글이 주는 고유 식별자(sub). 이메일은 바뀔 수 있어서 이걸 기준으로 찾는다.
    google_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Workspace(Base):
    """채널을 담는 단위. 권한과 데이터 격리의 경계다.

    채널을 유저에 직접 매달지 않은 이유는, 한 채널을 여러 관리자가 같이
    봐야 하기 때문이다. 담당자가 바뀌어도 채널 연동이 유지돼야 한다.
    """

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20), default="personal")  # personal / team
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class WorkspaceMember(Base):
    """누가 어느 워크스페이스에 속하는지. 이 표에 없으면 접근이 막힌다."""

    __tablename__ = "workspace_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # owner / admin / member
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class Session(Base):
    """로그인 세션.

    JWT 가 아니라 DB 에 두는 이유: 계약 종료·연동 해제 시 '즉시' 끊을 수
    있어야 한다. 서명 토큰은 만료 전까지 회수할 방법이 없다.
    """

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @staticmethod
    def new(user_id: int, days: int = SESSION_DAYS) -> "Session":
        now = datetime.utcnow()
        return Session(
            # 32바이트 난수. 추측으로 남의 세션을 맞힐 수 없어야 한다.
            token=secrets.token_urlsafe(32)[:64],
            user_id=user_id,
            created_at=now,
            expires_at=now + timedelta(days=days),
            last_seen_at=now,
        )


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id"), nullable=True, index=True
    )
    youtube_channel_id: Mapped[str] = mapped_column(String(64))
    channel_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # AI 자동 모더레이션에 대한 관리자 명시 동의 (YouTube API 정책).
    # 동의 없이는 판별을 돌리지 않는다.
    ai_consent_agreed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    connected_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # 채널 소유자의 OAuth 리프레시 토큰. 이게 있어야 댓글을 실제로 가릴 수
    # 있다(comments.setModerationStatus). 액세스 토큰은 1시간짜리라 저장하지
    # 않고 필요할 때마다 새로 받는다.
    #
    # 연동을 해제하면 반드시 NULL 로 지운다 — 남겨두면 해제한 뒤에도
    # 그 채널에 쓰기가 가능한 상태가 된다.
    youtube_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 이 채널에서 쓰이는 표현을 LLM 에게 알려주는 문장들.
    # 예: "ㄹㅈㄷ : '레전드'의 초성. 감탄 표현이며 비하가 아니다."
    # 프롬프트 맨 뒤에 붙는다 — 앞부분이 고정이어야 캐싱이 걸리기 때문.
    # 관리자가 직접 쓰는 게 아니라, 검토 이력에서 패턴을 찾아 시스템이 제안한다.
    context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 이 채널이 사람을 안 거치고 바로 가리기로 정한 분류. 콤마로 구분한다.
    # NULL 또는 빈 문자열이면 자동 숨김 없음 — 새 채널의 기본값이다.
    # 무엇을 자동으로 가릴지는 채널마다 답이 다르다. '모욕'을 어디까지로
    # 볼지는 뉴스 채널과 게임 채널이 같을 수 없어서, 값을 코드가 아니라
    # 채널이 들고 있게 했다.
    auto_hide_categories: Mapped[str | None] = mapped_column(Text, nullable=True)

    rules: Mapped[list["ChannelRule"]] = relationship(back_populates="channel")

    @property
    def auto_hide_set(self) -> set[str]:
        """auto_hide_categories 를 집합으로. 미설정이면 빈 집합."""
        raw = self.auto_hide_categories or ""
        return {c.strip() for c in raw.split(",") if c.strip()}


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

    # 이 영상에만 해당하는 설명. 판별할 때 프롬프트에 같이 넣는다.
    #
    # 채널 맥락과 나눈 이유: 한 채널이 여러 성격의 영상을 올린다. 진용진은
    # 머니게임도 올리고 길거리 실험도 올린다. "남녀가 팀으로 갈려 싸운다"를
    # 채널에 박아두면 실험 영상에는 틀린 정보가 들어간다.
    #
    # 대개는 비워둬도 된다 — title 만으로도 무슨 영상인지 전달된다.
    # 머니게임처럼 출연자 구도를 알아야 판단이 되는 영상에만 적는다.
    context: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # 어떤 기준으로 매긴 판정인지. 프롬프트와 채널 맥락에서 뽑은 지문이다.
    # 이게 없으면 프롬프트를 고친 뒤 "이 판정은 구기준인가 신기준인가"를
    # 알 수 없다. 실제로 4,568건이 그렇게 섞여 구분이 불가능했다.
    prompt_version: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )

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
