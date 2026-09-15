"""채널 목록과 채널별 자동 숨김 설정."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy import text as sq
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import current_user, my_workspace_ids, require_channel
from app.db.models import (
    Action,
    Channel,
    ChannelRule,
    Comment,
    RiskAssessment,
    User,
    Video,
)
from app.db.session import get_db
from app.services import google_oauth as goog
from app.services import youtube_actions as yt

router = APIRouter(prefix="/channels", tags=["channels"])

# 자동 숨김을 켤 수 있는 분류. llm.py 의 카테고리에서 '정상'을 뺀 것이다
# (정상을 가린다는 건 말이 안 된다).
#
# 순서는 화면에 그대로 쓰인다. 되돌릴 수 없는 피해가 큰 것부터 놓았다.
HIDEABLE: list[tuple[str, str]] = [
    ("신상털기", "실명·주소·연락처 노출"),
    ("위협", "신체적 위해 암시"),
    ("자해", "자해·자살 관련"),
    ("성희롱", "성적 대상화"),
    ("혐오", "지역·성별·국적 비하"),
    ("욕설", "대상을 겨냥한 욕설"),
    ("괴롭힘", "반복적 시달림"),
    ("모욕", "인신공격·조롱"),
    ("스팸", "홍보·링크 도배"),
    ("기타", "위 어디에도 안 맞는 유해"),
]
HIDEABLE_NAMES = {n for n, _ in HIDEABLE}


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_title: str | None


class CategoryOption(BaseModel):
    name: str
    description: str
    enabled: bool


class AutoHideOut(BaseModel):
    channel_id: int
    categories: list[str] = Field(..., description="지금 켜져 있는 분류")
    options: list[CategoryOption] = Field(..., description="켤 수 있는 전체 목록")


class AutoHideUpdate(BaseModel):
    categories: list[str] = Field(
        ..., description="자동 숨김할 분류. 빈 배열이면 자동 숨김 없음"
    )

    @field_validator("categories")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in HIDEABLE_NAMES]
        if unknown:
            raise ValueError(f"켤 수 없는 분류: {', '.join(unknown)}")
        # 중복을 없애되 HIDEABLE 순서를 지킨다 (저장값이 화면 순서와 같아진다).
        chosen = set(v)
        return [n for n, _ in HIDEABLE if n in chosen]


CONNECT_PATH = "/api/channels/connect/callback"


def _connect_uri() -> str:
    return get_settings().oauth_redirect_base.rstrip("/") + CONNECT_PATH


@router.get("/connect/start", include_in_schema=False)
async def connect_start(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    """채널 소유자에게 '댓글 관리' 권한을 요청한다.

    로그인과 흐름을 나눈 이유: 로그인은 이메일만 있으면 되는데, 여기서는
    댓글을 가릴 수 있는 권한까지 달라고 해야 한다. 로그인할 때부터 그걸
    요구하면 동의 화면이 과해지고, 채널을 안 붙일 팀원도 있다.
    """
    cfg = get_settings()
    if not cfg.oauth_ready:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            ".env 에 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 이 없습니다.",
        )

    ws = await my_workspace_ids(db, user)
    if not ws:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "워크스페이스가 없습니다")

    state = goog.states.issue(kind="connect", user_id=user.id, workspace_id=ws[0])
    return RedirectResponse(
        goog.authorize_url(
            cfg.google_client_id,
            _connect_uri(),
            goog.LOGIN_SCOPES + goog.YOUTUBE_SCOPES,
            state,
            # 리프레시 토큰이 있어야 나중에도 조치를 할 수 있다.
            # 이게 없으면 1시간 뒤부터 숨김이 안 된다.
            offline=True,
        )
    )


@router.get("/connect/callback", include_in_schema=False)
async def connect_callback(
    state: str = Query(...),
    code: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    cfg = get_settings()
    data = goog.states.take(state)
    if data is None or data.get("kind") != "connect":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "state 가 유효하지 않습니다")
    if error or not code:
        return RedirectResponse(f"/app#/channels?error={error or 'cancelled'}")

    tokens = await goog.exchange_code(
        cfg.google_client_id, cfg.google_client_secret, code, _connect_uri()
    )
    if not tokens.refresh_token:
        # prompt=consent 를 줬는데도 안 오면, 이미 허용해둔 상태다.
        # 구글 계정 설정에서 접근을 지우고 다시 해야 한다.
        return RedirectResponse("/app#/channels?error=no_refresh_token")

    owned = await yt.my_channels(tokens.access_token)
    if not owned:
        return RedirectResponse("/app#/channels?error=no_channel")

    now = datetime.utcnow()
    붙임, 막힘 = 0, []
    for ch in owned:
        row = (
            await db.execute(
                select(Channel).where(
                    Channel.youtube_channel_id == ch["youtube_channel_id"]
                )
            )
        ).scalar_one_or_none()

        # 이미 다른 워크스페이스가 쓰고 있으면 넘겨받지 않는다.
        #
        # 구글이 소유권을 보증해줬더라도, 그 채널에는 앞사람이 모은 댓글과
        # 판정·조치 이력이 딸려 있다. 그게 통째로 넘어가면 앞사람은 자기
        # 데이터를 잃고, 새 사람은 자기가 모으지 않은 남의 기록을 보게 된다.
        # 어느 쪽도 조용히 일어나면 안 되는 일이라, 막고 알린다.
        if (
            row is not None
            and row.workspace_id is not None
            and row.workspace_id != data["workspace_id"]
        ):
            막힘.append(ch["title"])
            continue

        if row is None:
            row = Channel(youtube_channel_id=ch["youtube_channel_id"])
            db.add(row)
        row.channel_title = ch["title"]
        row.workspace_id = data["workspace_id"]
        row.connected_by_user_id = data["user_id"]
        row.connected_at = now
        row.youtube_refresh_token = tokens.refresh_token
        # 동의는 연동 화면에서 따로 받는다. 여기서는 연동만.
        붙임 += 1

    await db.commit()
    if 막힘 and not 붙임:
        return RedirectResponse("/app#/channels?error=already_connected")
    return RedirectResponse(f"/app#/channels?connected={붙임}")


class DisconnectResult(BaseModel):
    channel_id: int
    deleted_comments: int
    deleted_assessments: int
    deleted_actions: int


@router.post(
    "/{channel_id}/disconnect",
    response_model=DisconnectResult,
    summary="채널 연동 해제 (수집 데이터 전부 삭제)",
)
async def disconnect(
    channel: Channel = Depends(require_channel), db: AsyncSession = Depends(get_db)
):
    """연동을 끊고 그 채널에서 모은 것을 전부 지운다.

    두 가지를 반드시 한다.

      1. **토큰을 지운다.** 남겨두면 해제한 뒤에도 그 채널에 쓰기가 된다.
      2. **수집 데이터를 지운다.** YouTube API 정책상 연동을 끊으면 그 채널
         관련 데이터를 즉시 삭제해야 한다.

    전에는 workspace_id 만 비웠는데, 그러면 댓글이 DB 에 그대로 남은 채
    아무도 못 보는 상태가 된다 — 지운 것도 아니고 쓸 수 있는 것도 아니다.
    """
    cid = channel.id

    # 지우는 순서가 있다. 참조하는 쪽부터 지워야 외래키에 걸리지 않는다.
    comment_ids = select(Comment.id).where(Comment.channel_id == cid).scalar_subquery()

    액션 = await db.execute(
        delete(Action).where(Action.comment_id.in_(comment_ids))
    )
    판정 = await db.execute(
        delete(RiskAssessment).where(RiskAssessment.comment_id.in_(comment_ids))
    )
    # review_history 는 초안 테이블이라 ORM 모델이 없다. 직접 지운다.
    await db.execute(
        sq("DELETE FROM review_history WHERE comment_id IN "
           "(SELECT id FROM comments WHERE channel_id = :cid)"),
        {"cid": cid},
    )
    댓글 = await db.execute(delete(Comment).where(Comment.channel_id == cid))
    await db.execute(delete(Video).where(Video.channel_id == cid))
    await db.execute(delete(ChannelRule).where(ChannelRule.channel_id == cid))

    # 채널 행 자체는 남긴다. 다시 연동하면 같은 자리에 붙고, 무엇보다
    # 연동 이력이 있었다는 사실은 남아야 한다.
    channel.youtube_refresh_token = None
    channel.workspace_id = None
    channel.context = None
    channel.auto_hide_categories = None
    channel.ai_consent_agreed = None
    await db.commit()

    return DisconnectResult(
        channel_id=cid,
        deleted_comments=댓글.rowcount or 0,
        deleted_assessments=판정.rowcount or 0,
        deleted_actions=액션.rowcount or 0,
    )


class ContextOut(BaseModel):
    channel_id: int
    context: str = Field("", description="이 채널의 판단 기준")
    prompt_version: str = Field(..., description="지금 이 기준으로 매기면 남는 지문")
    updated_hint: str = Field(
        ..., description="바꾼 뒤 무엇을 해야 반영되는지"
    )


class ContextUpdate(BaseModel):
    context: str = Field(
        "", max_length=4000,
        description="영상이 바뀌어도 그대로인 기준만. 영상별 설명은 영상에 적는다",
    )


def _context_out(channel: Channel) -> ContextOut:
    from app.services.llm import prompt_version

    ctx = channel.context or ""
    return ContextOut(
        channel_id=channel.id,
        context=ctx,
        prompt_version=prompt_version(ctx),
        updated_hint=(
            "바꾼 기준은 다음 판별부터 적용됩니다. 이미 판별한 댓글에 적용하려면 "
            f"scripts/rejudge.py --channel {channel.id} --all 을 돌립니다."
        ),
    )


@router.get(
    "/{channel_id}/context", response_model=ContextOut, summary="채널 기준 조회"
)
async def get_context(channel: Channel = Depends(require_channel)):
    return _context_out(channel)


@router.put(
    "/{channel_id}/context", response_model=ContextOut, summary="채널 기준 변경"
)
async def set_context(
    payload: ContextUpdate,
    channel: Channel = Depends(require_channel),
    db: AsyncSession = Depends(get_db),
):
    """이 채널에서만 통하는 판단 기준을 적는다.

    프롬프트 맨 뒤에 붙는다 — 앞부분(모든 채널 공통)이 고정이어야 캐싱이
    걸려서 값이 1/10 이 된다.

    이미 판별한 댓글에는 소급하지 않는다. 기준을 고칠 때마다 수천 건이
    자동으로 다시 돌면 돈이 얼마나 나갈지 예측할 수 없기 때문이다.
    """
    channel.context = payload.context.strip() or None
    await db.commit()
    await db.refresh(channel)
    return _context_out(channel)


@router.get("", response_model=list[ChannelOut], summary="채널 목록")
async def list_channels(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    """내가 속한 워크스페이스의 채널만. 채널간 노출은 정책상 금지다."""
    ws = await my_workspace_ids(db, user)
    if not ws:
        return []
    result = await db.execute(
        select(Channel).where(Channel.workspace_id.in_(ws)).order_by(Channel.id)
    )
    return result.scalars().all()


def _out(channel: Channel) -> AutoHideOut:
    on = channel.auto_hide_set
    return AutoHideOut(
        channel_id=channel.id,
        categories=[n for n, _ in HIDEABLE if n in on],
        options=[
            CategoryOption(name=n, description=d, enabled=n in on) for n, d in HIDEABLE
        ],
    )


@router.get(
    "/{channel_id}/auto-hide",
    response_model=AutoHideOut,
    summary="자동 숨김 설정 조회",
)
async def get_auto_hide(channel: Channel = Depends(require_channel)):
    return _out(channel)


@router.put(
    "/{channel_id}/auto-hide",
    response_model=AutoHideOut,
    summary="자동 숨김 설정 변경",
)
async def set_auto_hide(
    payload: AutoHideUpdate,
    channel: Channel = Depends(require_channel),
    db: AsyncSession = Depends(get_db),
):
    """켠 분류는 이후 판별부터 사람을 안 거치고 가려진다.

    이미 판별된 댓글에는 소급되지 않는다 — 바꾼 정책을 과거 것에 적용하려면
    scripts/reroute.py 를 돌린다. 화면에서 누르자마자 예전 댓글이 무더기로
    사라지는 편이 더 위험해서 이렇게 나눴다.
    """
    channel.auto_hide_categories = ",".join(payload.categories) or None
    await db.commit()
    await db.refresh(channel)
    return _out(channel)
