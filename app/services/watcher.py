"""연동된 채널을 서버가 스스로 감시한다.

전에는 scripts/watch.py 를 개발자가 터미널에서 돌려야 새 댓글이 들어왔다.
관리자한테는 터미널이 없다. 채널을 연결했으면 그걸로 끝이어야 하고, 한 시간
뒤에 화면을 열면 새 댓글이 위험도 순으로 줄 서 있어야 한다. 그래서 파기
(retention.py)와 같은 방식으로 서버가 켜져 있는 동안 혼자 돈다.

한 주기에 하는 일, 채널마다:

    ① 최신 영상 N개 목록                      (유튜브 1 unit)
    ② 영상마다 댓글 받기                       (영상당 2~3 units)
    ③ DB 에 넣기 — 이미 있는 댓글은 그대로     (upsert)
    ④ 새로 들어온 것(pending)만 LLM 판별       (건당 약 0.17원)
    ⑤ 행선지대로 검토 큐 / 통과

③④ 덕에 같은 영상을 매시간 다시 긁어도 새 댓글이 0건이면 돈은 안 든다.

감시 대상은 '연동(리프레시 토큰 있음) + AI 판별 동의' 둘 다 된 채널만이다.
동의 없이 판별하면 정책 위반이고, 연동 안 된 채널은 조치를 못 하니 굳이
서버가 돈을 써서 볼 이유가 없다 — 그런 채널은 스크립트로 따로 돌린다.

돈이 새는 길 두 개를 막는다.
  - 실행당 상한 (llm_max_calls_per_run): 한 채널이 한 주기에 태울 수 있는 양
  - 하루 상한 (llm_daily_cap): 폭주 영상이 매시간 상한까지 태우는 걸 막는다.
    넘치면 그날은 멈추고, 댓글은 pending 으로 남아 다음 날 이어간다.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy import text as sq

from app.core.config import get_settings
from app.db.models import Channel, ChannelRule
from app.db.session import AsyncSessionLocal
from app.services.collector import YouTubeApiError, YouTubeCollector, save_comments, save_video
from app.services.llm import LlmJudge
from app.services.pipeline import process_many
from app.services.store import save_results

log = logging.getLogger("outlier.watch")

# 서버가 막 떴을 때는 다른 일이 몰린다. 조금 기다렸다 시작한다.
FIRST_DELAY = 90

# 마지막으로 돈 결과. /api/health/watch 가 이걸 보여준다 — 관리자가
# "이거 돌고 있는 거 맞아?" 를 확인할 곳이 없으면 멈춰도 아무도 모른다.
상태: dict = {
    "last_run_at": None,
    "last_result": None,
    "today": None,          # 하루 상한을 세는 기준 날짜 (UTC)
    "llm_calls_today": 0,
}


def _오늘() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _남은_오늘_호출() -> int:
    """하루 상한에서 오늘 쓴 만큼 뺀 것. 날이 바뀌면 0 부터 다시 센다."""
    if 상태["today"] != _오늘():
        상태["today"] = _오늘()
        상태["llm_calls_today"] = 0
    return max(get_settings().llm_daily_cap - 상태["llm_calls_today"], 0)


async def 대상_채널(db) -> list[Channel]:
    """연동 + 동의 둘 다 된 채널만."""
    return list(
        (
            await db.execute(
                select(Channel).where(
                    Channel.workspace_id.isnot(None),
                    Channel.youtube_refresh_token.isnot(None),
                    Channel.ai_consent_agreed.is_(True),
                ).order_by(Channel.id)
            )
        ).scalars().all()
    )


async def _수집(yt, channel: Channel, cfg) -> int:
    """채널 하나의 최신 영상 댓글을 받아 DB 에 넣는다. 새로 들어온 건수를 돌려준다."""
    ch_info = await yt.resolve_channel(channel.youtube_channel_id)
    videos = await yt.channel_videos(ch_info, limit=cfg.watch_videos_per_channel)

    새로 = 0
    async with AsyncSessionLocal() as db:
        for v in videos:
            try:
                comments = await yt.collect(v["video_id"], max_comments=cfg.watch_per_video)
            except YouTubeApiError as e:
                if e.reason == "quotaExceeded":
                    raise
                log.warning("영상 %s 수집 실패 — %s", v["video_id"], e.reason)
                continue

            전 = (
                await db.execute(
                    sq("SELECT count(*) FROM comments WHERE channel_id = :c"),
                    {"c": channel.id},
                )
            ).scalar_one()

            # videos.list 를 따로 부르지 않는다 (영상당 1 unit 절약).
            # 목록에서 받은 제목·게시일로 충분하고, 채널은 이미 안다.
            await save_video(
                db, channel.id,
                {"id": v["video_id"],
                 "snippet": {"title": v["title"], "publishedAt": v["published_at"]},
                 "statistics": {}},
            )
            await save_comments(db, channel.id, comments)

            후 = (
                await db.execute(
                    sq("SELECT count(*) FROM comments WHERE channel_id = :c"),
                    {"c": channel.id},
                )
            ).scalar_one()
            새로 += 후 - 전
    return 새로


async def _판별(channel: Channel, 한도: int) -> int:
    """이 채널의 pending 댓글을 한도 안에서 판별한다. 판별한 건수를 돌려준다."""
    if 한도 <= 0:
        return 0
    cfg = get_settings()
    limit = min(한도, cfg.llm_max_calls_per_run)

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                sq("""
                    SELECT c.id, c.content, p.content
                    FROM comments c
                    LEFT JOIN comments p ON p.youtube_comment_id = c.parent_comment_id
                    WHERE c.channel_id = :cid AND c.status = 'pending'
                    ORDER BY c.id
                    LIMIT :lim
                """),
                {"cid": channel.id, "lim": limit},
            )
        ).all()
        if not rows:
            return 0
        rules = (
            await db.execute(
                select(ChannelRule).where(
                    ChannelRule.channel_id == channel.id, ChannelRule.enabled.is_(True)
                )
            )
        ).scalars().all()
        ch = await db.get(Channel, channel.id)
        ctx, auto_hide = ch.context or "", ch.auto_hide_set

    judge = LlmJudge(concurrency=8, max_calls=len(rows) + 10, channel_context=ctx)
    results = await process_many(rules, judge, [(r[1], r[2]) for r in rows], auto_hide)

    async with AsyncSessionLocal() as db:
        await save_results(
            db,
            [(r[0], v) for r, v in zip(rows, results)],
            model=cfg.openai_model,
            prompt_version=judge.prompt_version,
        )

    상태["llm_calls_today"] += judge.stats.calls
    return len(rows)


async def _임베딩() -> int:
    """유사 사례 검색용 벡터. 매우 싸다 (1,000건에 1원 안 됨)."""
    from pgvector.sqlalchemy import Vector
    from sqlalchemy import bindparam

    from app.db.models import EMBEDDING_DIM, Comment
    from app.services.embed import Embedder

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Comment.id, Comment.content)
                .where(Comment.embedding.is_(None), Comment.content.isnot(None))
                .limit(500)
            )
        ).all()
        if not rows:
            return 0
        vectors = await Embedder().embed([c for _, c in rows])
        t = Comment.__table__
        await db.execute(
            t.update()
            .where(t.c.id == bindparam("cid"))
            .values(embedding=bindparam("vec", type_=Vector(EMBEDDING_DIM))),
            [{"cid": cid, "vec": v} for (cid, _), v in zip(rows, vectors)],
        )
        await db.commit()
    return len(rows)


async def watch_once() -> dict:
    """한 주기. 무엇을 얼마나 했는지 돌려준다."""
    cfg = get_settings()
    결과 = {"channels": 0, "collected": 0, "judged": 0, "embedded": 0,
            "quota_units": 0, "skipped": [], "errors": []}

    if not cfg.youtube_api_key:
        결과["errors"].append("YOUTUBE_API_KEY 없음")
        return 결과

    async with AsyncSessionLocal() as db:
        채널들 = await 대상_채널(db)
    결과["channels"] = len(채널들)
    if not 채널들:
        return 결과

    async with YouTubeCollector(cfg.youtube_api_key) as yt:
        for ch in 채널들:
            try:
                새로 = await _수집(yt, ch, cfg)
                결과["collected"] += 새로
            except YouTubeApiError as e:
                결과["errors"].append(f"채널 {ch.id}: {e.reason}")
                if e.reason == "quotaExceeded":
                    # 오늘 쿼터가 끝났다. 다른 채널도 안 된다.
                    break
                continue
            except Exception as e:  # 한 채널이 죽어도 나머지는 본다
                결과["errors"].append(f"채널 {ch.id}: {type(e).__name__}")
                continue

            # 새 댓글이 없어도 pending 이 남아 있을 수 있다 (전 주기에 상한에
            # 걸렸거나, 동의 전에 수집만 해둔 것). 그래서 매번 본다.
            남은 = _남은_오늘_호출()
            if 남은 <= 0:
                결과["skipped"].append(f"채널 {ch.id}: 오늘 LLM 상한 도달")
                continue
            try:
                결과["judged"] += await _판별(ch, 남은)
            except Exception as e:
                결과["errors"].append(f"채널 {ch.id} 판별: {type(e).__name__}")
        결과["quota_units"] = yt.stats.quota_units

    if 결과["judged"] and cfg.openai_api_key:
        try:
            결과["embedded"] = await _임베딩()
        except Exception as e:
            결과["errors"].append(f"임베딩: {type(e).__name__}")

    상태["last_run_at"] = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    상태["last_result"] = 결과
    return 결과


async def run_forever() -> None:
    """서버가 사는 동안 주기적으로 돈다. 한 번 실패해도 멈추지 않는다."""
    cfg = get_settings()
    if not cfg.watch_enabled:
        log.info("감시 꺼짐 (WATCH_ENABLED=false)")
        return
    await asyncio.sleep(FIRST_DELAY)
    while True:
        try:
            r = await watch_once()
            if r["collected"] or r["judged"] or r["errors"]:
                log.info(
                    "감시 — 채널 %d · 새 댓글 %d · 판별 %d · 쿼터 %d · 오류 %s",
                    r["channels"], r["collected"], r["judged"],
                    r["quota_units"], r["errors"] or "없음",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("감시 주기가 실패했다. 다음 주기에 다시 시도한다")
        await asyncio.sleep(get_settings().watch_interval)
