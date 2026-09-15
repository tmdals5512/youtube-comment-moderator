"""영상을 주기적으로 다시 긁어 새 댓글만 판별한다 — 실서비스와 같은 방식.

유튜브는 '댓글이 달렸다'는 알림(웹훅)을 주지 않는다. PubSubHubbub 은 새 영상
업로드만 알려준다. 그래서 실제 서비스도 주기적 폴링 말고는 방법이 없다.

돈이 새지 않도록 두 겹으로 막는다.
  - 수집은 upsert 라 같은 댓글이 다시 저장돼도 중복이 안 쌓인다
  - 판별은 status='pending' 인 것만 — 이미 본 댓글을 LLM 에 다시 보내지 않는다
그래서 새 댓글이 0건이면 이 스크립트는 쿼터만 조금 쓰고 돈은 안 쓴다.

    python -m scripts.watch --channel @진용진               채널의 최신 영상 (권장)
    python -m scripts.watch --channel @진용진 --every 3600   1시간마다
    python -m scripts.watch videos.txt                     목록 파일의 영상만
    python -m scripts.watch videos.txt --once              한 번만

채널 모드가 실서비스에 맞다. 매 주기마다 최신 영상 목록을 다시 받아오므로
새 영상이 올라오면 손대지 않아도 감시 대상에 들어온다. 목록 파일 모드는
특정 영상만 집중해서 볼 때 쓴다.
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import text as sq

from app.core.config import get_settings
from app.db.models import Channel, ChannelRule, Comment
from app.db.session import AsyncSessionLocal, engine
from app.services.collector import (
    YouTubeApiError,
    YouTubeCollector,
    save_comments,
    save_video,
)
from app.services.embed import Embedder
from app.services.llm import LlmJudge
from app.services.pipeline import process_many
from app.services.store import save_results
from scripts.collect_videos import parse_list

# 한 주기에 영상당 받아올 최대 댓글 수. 새 댓글은 최신순 앞쪽에 있으므로
# 매번 전체를 받을 필요가 없다.
PER_VIDEO = 200

# 한 주기에 판별할 최대 건수. 폭주(영상이 터져서 수천 건이 한 번에)에 대비한
# 비용 안전장치다. 넘치면 다음 주기로 넘어간다.
MAX_JUDGE = 300


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def resolve_targets(yt, args) -> list[tuple[str, str]]:
    """이번 주기에 볼 영상 목록.

    채널 모드는 매 주기 최신 영상을 다시 받아온다 — 그래야 새로 올라온
    영상이 사람 손을 안 거치고 감시 대상에 들어온다. 목록 파일 모드는
    고정이라 특정 영상만 집중해서 볼 때 쓴다.
    """
    if not args.channel:
        return parse_list(Path(args.list_file))
    ch = await yt.resolve_channel(args.channel)
    videos = await yt.channel_videos(ch, limit=args.videos)
    return [(args.topic, v["video_id"]) for v in videos]


async def collect_round(yt, targets) -> set[int]:
    """수집만. 새 댓글이 생긴 채널 id 를 돌려준다."""
    touched: set[int] = set()

    async with AsyncSessionLocal() as db:
        for topic, vid in targets:
            try:
                info = await yt.video_info(vid)
                comments = await yt.collect(vid, max_comments=PER_VIDEO)
            except YouTubeApiError as e:
                print(f"  [{now()}] {vid} 수집 실패 — {e.reason}")
                continue

            snip = info["snippet"]
            channel = (
                await db.execute(
                    select(Channel).where(
                        Channel.youtube_channel_id == snip["channelId"]
                    )
                )
            ).scalar_one_or_none()
            if channel is None:
                channel = Channel(
                    youtube_channel_id=snip["channelId"],
                    channel_title=snip.get("channelTitle"),
                )
                db.add(channel)
                await db.flush()

            before = (
                await db.execute(
                    sq("SELECT count(*) FROM comments WHERE channel_id = :c"),
                    {"c": channel.id},
                )
            ).scalar_one()

            await save_video(db, channel.id, info, topic)
            await save_comments(db, channel.id, comments)
            await db.commit()

            after = (
                await db.execute(
                    sq("SELECT count(*) FROM comments WHERE channel_id = :c"),
                    {"c": channel.id},
                )
            ).scalar_one()

            if after > before:
                print(f"  [{now()}] {snip['title'][:28]} — 새 댓글 {after - before}건")
                touched.add(channel.id)

    return touched


async def judge_round(channel_ids: set[int]) -> int:
    """아직 판별 안 한 댓글만 처리한다."""
    total = 0
    for cid in sorted(channel_ids):
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    sq("""
                    SELECT c.id, c.content, p.content
                    FROM comments c
                    LEFT JOIN comments p
                      ON p.youtube_comment_id = c.parent_comment_id
                    WHERE c.channel_id = :cid AND c.status = 'pending'
                    ORDER BY c.id
                    LIMIT :lim
                """),
                    {"cid": cid, "lim": MAX_JUDGE},
                )
            ).all()
            rules = (
                await db.execute(
                    select(ChannelRule).where(
                        ChannelRule.channel_id == cid, ChannelRule.enabled.is_(True)
                    )
                )
            ).scalars().all()
            ch = await db.get(Channel, cid)
            ctx = ch.context or ""
            auto_hide = ch.auto_hide_set

        if not rows:
            continue

        judge = LlmJudge(concurrency=8, max_calls=len(rows) + 10, channel_context=ctx)
        results = await process_many(
            rules, judge, [(r[1], r[2]) for r in rows], auto_hide
        )

        async with AsyncSessionLocal() as db:
            await save_results(
                db,
                [(r[0], v) for r, v in zip(rows, results)],
                model=get_settings().openai_model,
                prompt_version=judge.prompt_version,
            )

        st = judge.stats
        print(
            f"  [{now()}] 채널 {cid} 판별 {len(rows)}건"
            f" (실패 {st.errors}) · {st.cost_usd * 1400:.0f}원"
        )
        total += len(rows)
    return total


async def embed_round() -> int:
    """유사 사례 검색이 새 댓글에도 걸리도록 벡터를 채운다. 매우 싸다."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Comment.id, Comment.content)
                .where(Comment.embedding.is_(None))
                .limit(500)
            )
        ).all()
        if not rows:
            return 0

        from pgvector.sqlalchemy import Vector
        from sqlalchemy import bindparam

        from app.db.models import EMBEDDING_DIM

        vectors = await Embedder().embed([c for _, c in rows])
        table = Comment.__table__
        await db.execute(
            table.update()
            .where(table.c.id == bindparam("cid"))
            .values(embedding=bindparam("vec", type_=Vector(EMBEDDING_DIM))),
            [{"cid": cid, "vec": v} for (cid, _), v in zip(rows, vectors)],
        )
        await db.commit()
    return len(rows)


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("list_file", nargs="?", help="collect_videos 와 같은 형식의 목록 파일")
    ap.add_argument("--channel", help="@핸들 또는 채널ID. 최신 영상을 매 주기 다시 찾는다")
    ap.add_argument("--videos", type=int, default=5, help="채널 모드에서 볼 최신 영상 수")
    ap.add_argument("--topic", default="미분류", help="채널 모드에서 붙일 주제")
    ap.add_argument("--every", type=int, default=300, help="주기(초). 기본 300")
    ap.add_argument("--once", action="store_true", help="한 번만 돌고 끝")
    args = ap.parse_args()

    if not args.channel and not args.list_file:
        raise SystemExit("[FAIL] 목록 파일이나 --channel 중 하나는 있어야 한다.")
    if args.list_file and not Path(args.list_file).exists():
        raise SystemExit(f"[FAIL] 파일이 없다: {args.list_file}")

    key = get_settings().youtube_api_key
    if not key:
        raise SystemExit("[FAIL] .env에 YOUTUBE_API_KEY가 없다.")

    where = f"채널 {args.channel} 최신 {args.videos}개" if args.channel else args.list_file
    print(f"감시 시작 — {where} / {args.every}초마다")
    print("멈추려면 Ctrl+C\n")

    async with YouTubeCollector(key) as yt:
        while True:
            try:
                targets = await resolve_targets(yt, args)
            except YouTubeApiError as e:
                print(f"  [{now()}] 영상 목록을 못 받았다 — {e.reason}")
                if args.once:
                    break
                await asyncio.sleep(args.every)
                continue

            touched = await collect_round(yt, targets)
            if touched:
                await judge_round(touched)
                n = await embed_round()
                if n:
                    print(f"  [{now()}] 임베딩 {n}건")
            else:
                print(f"  [{now()}] 새 댓글 없음 (쿼터 {yt.stats.quota_units})")

            if args.once:
                break
            await asyncio.sleep(args.every)

    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n멈춤")
