"""검토 큐 · 숨김 목록 · 조치 · 통계 (F_R_112, F_R_115, F_R_116).

관리자가 실제로 쓰는 화면이 읽는 API 다. 설계 전제 두 가지.

  1. 관리자는 전부 못 본다. 그래서 큐는 '확산도(좋아요+답글) 높은 순'으로
     준다 — 같은 유해댓글이라도 많이 퍼진 것부터 처리해야 피해가 준다.

  2. 숨김 목록은 반드시 열어볼 수 있어야 한다. 자동으로 가려진 것을
     관리자가 되돌릴 통로가 없으면 오탐이 영원히 안 보인다.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Action, Channel, Comment, RiskAssessment, Video
from app.db.session import get_db

router = APIRouter(tags=["review"])

ActionType = Literal["hide", "keep", "ban_author"]

# 조치 -> 댓글이 가질 상태
ACTION_STATUS = {"hide": "hidden", "keep": "passed", "ban_author": "hidden"}

# 카테고리 -> 위험도.
# 자동 숨김을 '욕설' 하나로 좁힌 뒤로는 거의 모든 판정이 검토 큐로 온다.
# 관리자가 178건을 위에서부터 훑는다면 무엇을 먼저 보여줄지가 중요해진다.
#
# critical 은 '늦으면 되돌릴 수 없는 것'으로 잡았다.
#   신상털기·위협 = 채널이 법적 책임을 지는 유형
#   자해         = 사람이 다칠 수 있어 대응 시급성이 가장 높다
# 나머지는 해악의 크기순이다. 모욕이 medium 인 것은 판단이 갈리기 때문이지
# 가벼워서가 아니다 — 어디까지가 모욕인지는 채널이 정할 문제다.
SEVERITY = {
    "신상털기": "critical", "위협": "critical", "자해": "critical",
    "혐오": "high", "성희롱": "high", "욕설": "high",
    "모욕": "medium", "괴롭힘": "medium", "기타": "medium",
    "스팸": "low", "정상": "low",
}
RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
BY_RANK = {v: k for k, v in RANK.items()}

# 좋아요+답글이 이만큼 넘으면 한 단계 올린다. 같은 표현이라도 많이 퍼진 쪽이
# 실제 피해가 크고, 관리자가 늦게 볼수록 손해가 누적된다.
SPREAD_BUMP = 100


def _spread():
    return func.coalesce(Comment.like_count, 0) + func.coalesce(
        Comment.total_reply_count, 0
    )


def _severity_rank():
    """카테고리를 위험도 순위로 바꾸고, 확산도가 높으면 한 단계 올린다."""
    base = case(
        {k: RANK[v] for k, v in SEVERITY.items()},
        value=RiskAssessment.category,
        else_=RANK["medium"],
    )
    return case((_spread() >= SPREAD_BUMP, func.greatest(base - 1, 0)), else_=base)


class QueueItem(BaseModel):
    comment_id: int
    severity: Literal["critical", "high", "medium", "low"] = Field(
        ..., description="처리 우선순위. 카테고리 + 확산도로 정한다"
    )

    channel_title: str | None
    video_id: str | None
    video_title: str | None
    published_at: datetime | None = Field(None, description="유튜브에 달린 시각")

    content: str
    author_name: str | None
    like_count: int
    total_reply_count: int
    is_reply: bool
    parent_content: str | None = Field(None, description="답글이면 부모 댓글 본문")

    status: str
    destination: str | None = Field(None, description="queue_judge=판단 필요 / queue_info=참고")
    decided_by: str | None = Field(None, description="rule=관리자 등록어 / llm")
    label: str | None
    category: str | None
    reason: str | None
    rule_value: str | None = Field(None, description="등록어에 걸렸으면 그 단어")


class ActionRequest(BaseModel):
    action: ActionType = Field(..., description="hide=숨김 / keep=유지 / ban_author=채널차단")
    actor: str | None = Field(None, max_length=100, description="처리한 관리자")
    note: str | None = Field(None, description="판단 근거 메모")


class ActionResult(BaseModel):
    comment_id: int
    action: ActionType
    status: str
    youtube_synced: bool = Field(
        False, description="유튜브 실제 반영 여부. 채널 소유자 OAuth 가 없어 항상 False"
    )
    reviewed_at: datetime


PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30, "all": None}


def _since(period: str):
    """기간 필터의 기준 시각. all 이면 None (필터 없음)."""
    days = PERIOD_DAYS.get(period)
    if days is None:
        return None
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)


class Stats(BaseModel):
    channel_id: int
    period: str
    total: int
    pending: int
    passed: int
    queued: int
    hidden: int
    unreviewed: int = Field(..., description="큐에 남아 관리자를 기다리는 건수")
    review_rate: float = Field(..., description="검토 전환율. NF_R_104 목표 30% 이하")
    by_category: dict[str, int]


async def _require_channel(db: AsyncSession, channel_id: int) -> None:
    if await db.get(Channel, channel_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"채널 {channel_id} 없음")


def _latest_assessment():
    """댓글별 '가장 최근' 판정만 고르는 서브쿼리.

    재판별하면 행이 쌓이므로 최신 것만 봐야 한다. id 가 순증이라 max(id) 로
    충분하다 (created_at 이 같은 초에 몰려도 안전하다).
    """
    return (
        select(
            RiskAssessment.comment_id.label("cid"),
            func.max(RiskAssessment.id).label("rid"),
        )
        .group_by(RiskAssessment.comment_id)
        .subquery()
    )


async def _rows(db, channel_id, where, limit, offset):
    latest = _latest_assessment()
    parent = Comment.__table__.alias("parent")
    rank = _severity_rank()

    stmt = (
        select(
            Comment,
            RiskAssessment,
            parent.c.content.label("parent_content"),
            Video.title.label("video_title"),
            Channel.channel_title.label("channel_title"),
            rank.label("rank"),
        )
        .join(latest, latest.c.cid == Comment.id)
        .join(RiskAssessment, RiskAssessment.id == latest.c.rid)
        .outerjoin(parent, parent.c.youtube_comment_id == Comment.parent_comment_id)
        .outerjoin(Video, Video.video_id == Comment.video_id)
        .outerjoin(Channel, Channel.id == Comment.channel_id)
        .where(Comment.channel_id == channel_id, *where)
        # 위험한 것부터, 같은 등급 안에서는 많이 퍼진 것부터.
        # 관리자 시간이 유한하다는 게 이 정렬의 이유다.
        .order_by(rank, _spread().desc(), Comment.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await db.execute(stmt)).all()


def _to_item(c, ra, parent_content, video_title, channel_title, rank) -> QueueItem:
    return QueueItem(
        comment_id=c.id,
        severity=BY_RANK[rank],
        channel_title=channel_title,
        video_id=c.video_id,
        video_title=video_title,
        published_at=c.published_at,
        content=c.content,
        author_name=c.author_name,
        like_count=c.like_count or 0,
        total_reply_count=c.total_reply_count or 0,
        is_reply=bool(c.is_reply),
        parent_content=parent_content,
        status=c.status,
        destination=ra.destination,
        decided_by=ra.stage,
        label=ra.risk_level,
        category=ra.category,
        reason=ra.reasoning,
        rule_value=ra.rule_value,
    )


@router.get(
    "/channels/{channel_id}/queue",
    response_model=list[QueueItem],
    summary="검토 큐 (관리자가 판단해야 할 댓글)",
)
async def queue(
    channel_id: int,
    kind: Literal["all", "judge", "info"] = Query(
        "all", description="judge=판단 필요만 / info=참고만"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    await _require_channel(db, channel_id)

    where = [Comment.status == "queued", Comment.reviewed_at.is_(None)]
    if kind != "all":
        target = "queue_judge" if kind == "judge" else "queue_info"
        where.append(RiskAssessment.destination == target)

    rows = await _rows(db, channel_id, where, limit, offset)
    return [_to_item(*r) for r in rows]


@router.get(
    "/channels/{channel_id}/hidden",
    response_model=list[QueueItem],
    summary="숨김 목록 (오탐을 발견하는 통로)",
)
async def hidden(
    channel_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    await _require_channel(db, channel_id)
    rows = await _rows(db, channel_id, [Comment.status == "hidden"], limit, offset)
    return [_to_item(*r) for r in rows]


@router.post(
    "/comments/{comment_id}/action",
    response_model=ActionResult,
    summary="조치 (숨김 / 유지 / 채널차단)",
)
async def act(
    comment_id: int, payload: ActionRequest, db: AsyncSession = Depends(get_db)
):
    comment = await db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"댓글 {comment_id} 없음")

    now = datetime.now(UTC).replace(tzinfo=None)

    # 유튜브 반영은 채널 소유자 OAuth 가 있어야 한다. 지금은 우리 채널이
    # 없어서 DB 에만 남긴다 — youtube_synced=False 가 그 표시다.
    db.add(
        Action(
            comment_id=comment_id,
            action_type=payload.action,
            actor=payload.actor,
            note=payload.note,
            youtube_synced=False,
            executed_at=now,
        )
    )
    comment.status = ACTION_STATUS[payload.action]
    comment.reviewed_at = now
    await db.commit()

    return ActionResult(
        comment_id=comment_id,
        action=payload.action,
        status=comment.status,
        youtube_synced=False,
        reviewed_at=now,
    )


@router.get(
    "/channels/{channel_id}/stats", response_model=Stats, summary="채널 처리 현황"
)
async def stats(
    channel_id: int,
    period: Literal["today", "7d", "30d", "all"] = Query(
        "all", description="수집 시각 기준"
    ),
    db: AsyncSession = Depends(get_db),
):
    await _require_channel(db, channel_id)

    since = _since(period)
    # 수집 시각 기준이다. 유튜브에 달린 시각이 아니라 우리가 가져온 시각 —
    # 오래된 영상을 오늘 수집하면 오늘 치로 잡히는 게 관리자 관점에 맞다.
    window = [Comment.collected_at >= since] if since else []

    counts = dict(
        (
            await db.execute(
                select(Comment.status, func.count())
                .where(Comment.channel_id == channel_id, *window)
                .group_by(Comment.status)
            )
        ).all()
    )
    total = sum(counts.values())

    unreviewed = (
        await db.execute(
            select(func.count())
            .select_from(Comment)
            .where(
                Comment.channel_id == channel_id,
                Comment.status == "queued",
                Comment.reviewed_at.is_(None),
                *window,
            )
        )
    ).scalar_one()

    latest = _latest_assessment()
    by_category = dict(
        (
            await db.execute(
                select(RiskAssessment.category, func.count())
                .join(latest, latest.c.rid == RiskAssessment.id)
                .join(Comment, Comment.id == RiskAssessment.comment_id)
                .where(
                    Comment.channel_id == channel_id,
                    RiskAssessment.category.isnot(None),
                    RiskAssessment.category != "정상",
                    *window,
                )
                .group_by(RiskAssessment.category)
                .order_by(func.count().desc())
            )
        ).all()
    )

    queued = counts.get("queued", 0)
    return Stats(
        channel_id=channel_id,
        period=period,
        total=total,
        pending=counts.get("pending", 0),
        passed=counts.get("passed", 0),
        queued=queued,
        hidden=counts.get("hidden", 0),
        unreviewed=unreviewed,
        review_rate=round(queued / total, 4) if total else 0.0,
        by_category=by_category,
    )


class HistoryItem(BaseModel):
    action_id: int
    comment_id: int
    action: str = Field(..., description="hide=숨김 / keep=유지 / ban_author=채널차단")
    actor: str | None
    note: str | None
    youtube_synced: bool
    executed_at: datetime | None

    content: str
    author_name: str | None
    category: str | None = Field(None, description="조치 당시 AI 가 본 분류")
    reason: str | None


@router.get(
    "/channels/{channel_id}/history",
    response_model=list[HistoryItem],
    summary="처리 이력 (누가 언제 무엇을 왜)",
)
async def history(
    channel_id: int,
    action: Literal["all", "hide", "keep", "ban_author"] = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """조치 이력을 최신순으로. 관리자가 자기 판단을 되짚어볼 수 있어야 하고,
    나중에 이 이력이 채널별 맥락을 만드는 재료가 된다 (F_R_116).
    """
    await _require_channel(db, channel_id)

    latest = _latest_assessment()
    where = [] if action == "all" else [Action.action_type == action]

    rows = (
        await db.execute(
            select(Action, Comment, RiskAssessment)
            .join(Comment, Comment.id == Action.comment_id)
            .outerjoin(latest, latest.c.cid == Comment.id)
            .outerjoin(RiskAssessment, RiskAssessment.id == latest.c.rid)
            .where(Comment.channel_id == channel_id, *where)
            .order_by(Action.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return [
        HistoryItem(
            action_id=a.id,
            comment_id=a.comment_id,
            action=a.action_type,
            actor=a.actor,
            note=a.note,
            youtube_synced=bool(a.youtube_synced),
            executed_at=a.executed_at,
            content=c.content,
            author_name=c.author_name,
            category=ra.category if ra else None,
            reason=ra.reasoning if ra else None,
        )
        for a, c, ra in rows
    ]


class SimilarCase(BaseModel):
    comment_id: int
    content: str
    similarity: float = Field(..., description="1.0 에 가까울수록 비슷하다")

    category: str | None
    status: str = Field(..., description="passed / queued / hidden")
    reviewed: bool = Field(..., description="관리자가 실제로 처리한 건인지")
    action: str | None = Field(None, description="hide / keep / ban_author")
    actor: str | None
    note: str | None
    executed_at: datetime | None


@router.get(
    "/comments/{comment_id}/similar",
    response_model=list[SimilarCase],
    summary="과거 유사 사례 (F_R_114)",
)
async def similar(
    comment_id: int,
    limit: int = Query(3, ge=1, le=10),
    # 최소 유사도로 자르지 않는다. 어디서 잘라야 하는지 근거가 아직 없다 —
    # 600건을 재보니 0.8 이상만 확실했고(카테고리 일치 93~97%), 그 아래는
    # 무작위로 두 건을 집었을 때의 일치율(59%)과 구분이 안 됐다.
    # 사람이 '참고가 됐다/안 됐다'를 라벨링해야 제대로 정할 수 있다.
    # 그때까지는 유사도 값을 그대로 내보내고 판단은 관리자에게 맡긴다.
    only_reviewed: bool = Query(
        False, description="관리자가 실제로 처리한 것만 보기"
    ),
    db: AsyncSession = Depends(get_db),
):
    """뜻이 비슷한 과거 댓글과, 그때 관리자가 내린 결정을 함께 돌려준다.

    단어가 겹치는 댓글이 아니라 '같은 사안'을 찾는 게 목적이다 —
    "주소 알아내서 찾아간다"와 "어디 사는지 알아냈음"은 겹치는 단어가
    없지만 관리자에겐 같은 건이다.

    같은 채널 안에서만 찾는다. 채널(고객사)간 데이터 결합·비교 노출은
    YouTube API 정책상 금지다.
    """
    target = await db.get(Comment, comment_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"댓글 {comment_id} 없음")
    if target.embedding is None:
        # 아직 벡터가 없으면 빈 목록. 화면은 '유사 사례 없음'으로 그리면 된다.
        return []

    latest = _latest_assessment()
    dist = Comment.embedding.cosine_distance(target.embedding)

    where = [
        Comment.channel_id == target.channel_id,
        Comment.id != comment_id,
        Comment.embedding.isnot(None),
    ]
    if only_reviewed:
        where.append(Comment.reviewed_at.isnot(None))

    # 조치 이력은 댓글당 여러 개일 수 있다. 가장 최근 것만 붙인다.
    last_action = (
        select(Action.comment_id.label("cid"), func.max(Action.id).label("aid"))
        .group_by(Action.comment_id)
        .subquery()
    )

    rows = (
        await db.execute(
            select(Comment, RiskAssessment, Action, dist.label("dist"))
            .outerjoin(latest, latest.c.cid == Comment.id)
            .outerjoin(RiskAssessment, RiskAssessment.id == latest.c.rid)
            .outerjoin(last_action, last_action.c.cid == Comment.id)
            .outerjoin(Action, Action.id == last_action.c.aid)
            .where(*where)
            .order_by(dist)
            .limit(limit)
        )
    ).all()

    return [
        SimilarCase(
            comment_id=c.id,
            content=c.content,
            similarity=round(1 - float(d), 4),
            category=ra.category if ra else None,
            status=c.status,
            reviewed=c.reviewed_at is not None,
            action=a.action_type if a else None,
            actor=a.actor if a else None,
            note=a.note if a else None,
            executed_at=a.executed_at if a else None,
        )
        for c, ra, a, d in rows
    ]
