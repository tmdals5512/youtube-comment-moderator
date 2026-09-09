"""영상 하나의 댓글을 답글까지 전부 받아 DB에 넣는다.

    .venv/Scripts/python.exe -m scripts.collect <영상URL> [최대건수]

같은 영상을 다시 돌려도 안전하다 (upsert). 수정된 댓글·바뀐 좋아요만 갱신된다.
"""

import asyncio
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Channel
from app.db.session import AsyncSessionLocal, engine
from app.services.collector import (
    YouTubeApiError,
    YouTubeCollector,
    extract_video_id,
    save_comments,
)


async def _upsert_channel(db, info: dict) -> Channel:
    """영상이 속한 채널 행을 찾거나 만든다.

    comments.channel_id 가 FK라 채널이 먼저 있어야 한다. 수집 대상은 남의
    채널이므로 ai_consent_agreed 는 세우지 않는다 — 조치(숨김·차단)는
    OAuth + 채널 소유자 동의가 있어야 하고, 수집만으로는 그 권한이 없다.
    """
    snippet = info["snippet"]
    yt_id = snippet["channelId"]

    channel = (
        await db.execute(select(Channel).where(Channel.youtube_channel_id == yt_id))
    ).scalar_one_or_none()

    if channel is None:
        channel = Channel(
            youtube_channel_id=yt_id,
            channel_title=snippet["channelTitle"],
        )
        db.add(channel)
        await db.flush()
    return channel


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        raise SystemExit("사용법: python -m scripts.collect <영상URL> [최대건수]")

    video_id = extract_video_id(sys.argv[1])
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else None

    key = get_settings().youtube_api_key
    if not key:
        raise SystemExit("[FAIL] .env에 YOUTUBE_API_KEY가 없다.")

    async with YouTubeCollector(key) as yt:
        try:
            info = await yt.video_info(video_id)
        except YouTubeApiError as e:
            raise SystemExit(f"[FAIL] {e}")

        print(f"영상   : {info['snippet']['title'][:60]}")
        print(f"채널   : {info['snippet']['channelTitle']}")
        print(f"댓글수 : {info.get('statistics', {}).get('commentCount', '비공개')} (답글 포함)")
        print("수집 중...")

        try:
            comments = await yt.collect(video_id, max_comments=cap)
        except YouTubeApiError as e:
            raise SystemExit(f"[FAIL] {e}")

    st = yt.stats
    if st.comments_disabled:
        print("[SKIP] 댓글이 꺼진 영상")
        return

    tops = sum(1 for c in comments if not c.is_reply)
    replies = len(comments) - tops

    async with AsyncSessionLocal() as db:
        channel = await _upsert_channel(db, info)
        saved = await save_comments(db, channel.id, comments)

    print(f"\n[OK] 최상위 {tops}건 + 답글 {replies}건 = {len(comments)}건")
    print(f"     답글 내역: 기본응답 {st.replies_inline}건"
          f" + 보충 {st.replies_supplemented}건 (잘린 스레드 {st.truncated_threads}개)")
    print(f"     쿼터 {st.quota_units} units")
    print(f"[OK] DB 저장 {saved}건 (channel_id={channel.id}, 재실행해도 중복 없음)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
