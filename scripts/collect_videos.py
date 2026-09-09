"""영상 목록을 받아 주제별로 댓글을 모은다.

채널 단위(collect_channel)는 평범한 영상이 섞여 유해댓글 밀도가 낮다.
논란이 붙은 영상만 골라 오면 그 문제가 없어진다. 대신 채널이 흩어지므로
영상마다 주제(정치·범죄·연예…)를 붙여 그걸 묶는 축으로 쓴다.

목록 파일 형식 — 주제와 URL을 공백으로 구분. '#' 로 시작하면 주석.
    정치   https://youtu.be/xxxxxxxxxxx
    범죄   https://www.youtube.com/watch?v=yyyyyyyyyyy
    # 주제를 안 적으면 '미분류'로 들어간다
    https://youtu.be/zzzzzzzzzzz

    python -m scripts.collect_videos videos.txt [영상당최대댓글]

같은 목록을 다시 돌려도 안전하다 (upsert). 새 댓글만 늘어난다.
영상당 쿼터는 대략 (댓글수/100 x 2) + 1 units. 300건 상한이면 5~7 units.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Channel
from app.db.session import AsyncSessionLocal, engine
from app.services.collector import (
    YouTubeApiError,
    YouTubeCollector,
    extract_video_id,
    save_comments,
    save_video,
)

# 안전장치. 논란 영상은 댓글이 수천 건이라 상한이 없으면 쿼터가 녹는다.
DEFAULT_PER_VIDEO = 300
QUOTA_CAP = 3000


def parse_list(path: Path) -> list[tuple[str, str]]:
    """(주제, 영상ID) 목록으로 바꾼다. 잘못된 줄은 이유를 찍고 건너뛴다."""
    out: list[tuple[str, str]] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        topic, url = ("미분류", parts[0]) if len(parts) == 1 else (parts[0], parts[-1])
        try:
            out.append((topic, extract_video_id(url)))
        except ValueError as e:
            print(f"  [{n}행 건너뜀] {e}")
    return out


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    if len(sys.argv) < 2:
        raise SystemExit("사용법: python -m scripts.collect_videos <목록파일> [영상당최대댓글]")

    path = Path(sys.argv[1])
    if not path.exists():
        raise SystemExit(f"[FAIL] 파일이 없다: {path}")
    per_video = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PER_VIDEO

    targets = parse_list(path)
    if not targets:
        raise SystemExit("[FAIL] 읽을 영상이 없다.")

    key = get_settings().youtube_api_key
    if not key:
        raise SystemExit("[FAIL] .env에 YOUTUBE_API_KEY가 없다.")

    print(f"영상 {len(targets)}개 / 영상당 최대 {per_video}건\n")
    by_topic: dict[str, int] = {}
    skipped: list[str] = []

    async with YouTubeCollector(key) as yt, AsyncSessionLocal() as db:
        for i, (topic, vid) in enumerate(targets, 1):
            if yt.stats.quota_units > QUOTA_CAP:
                print(f"\n[중단] 쿼터 상한 {QUOTA_CAP} 도달")
                break

            try:
                info = await yt.video_info(vid)
            except YouTubeApiError as e:
                print(f"[{i}/{len(targets)}] {vid} 실패 — {e}")
                skipped.append(vid)
                continue

            snip = info["snippet"]
            yt_channel_id, title = snip["channelId"], snip["title"]

            # 영상이 속한 채널을 먼저 만들어야 FK 가 걸린다.
            channel = (
                await db.execute(
                    select(Channel).where(Channel.youtube_channel_id == yt_channel_id)
                )
            ).scalar_one_or_none()
            if channel is None:
                channel = Channel(
                    youtube_channel_id=yt_channel_id,
                    channel_title=snip.get("channelTitle"),
                )
                db.add(channel)
                await db.flush()

            before = yt.stats.quota_units
            try:
                comments = await yt.collect(vid, max_comments=per_video)
            except YouTubeApiError as e:
                print(f"[{i}/{len(targets)}] {title[:26]} 실패 — {e.reason}")
                skipped.append(vid)
                continue

            await save_video(db, channel.id, info, topic)
            saved = await save_comments(db, channel.id, comments)
            await db.commit()

            by_topic[topic] = by_topic.get(topic, 0) + saved
            total = info.get("statistics", {}).get("commentCount")
            share = f" / 전체 {total}건" if total else ""
            note = "  (댓글 꺼짐)" if not comments else ""
            print(f"[{i}/{len(targets)}] {topic:<5} {title[:30]:<32}"
                  f" {saved:>4}건{share}  {yt.stats.quota_units - before}u{note}")

    st = yt.stats
    print(f"\n{'-' * 60}")
    for topic, n in sorted(by_topic.items(), key=lambda x: -x[1]):
        print(f"  {topic:<8}{n:>6}건")
    print(f"{'-' * 60}")
    print(f"  합계 {sum(by_topic.values())}건 / 쿼터 {st.quota_units} units"
          f" / 답글 보충 {st.replies_supplemented}건")
    if skipped:
        print(f"  건너뜀 {len(skipped)}개: {', '.join(skipped)}")
    print("\n  다음: python -m scripts.run_pipeline")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
