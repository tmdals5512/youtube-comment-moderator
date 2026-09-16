"""유튜브가 붙잡아둔 댓글을 우리가 받아올 수 있는가.

API 키로 commentThreads 를 부르면 '공개된' 댓글만 온다. 유튜브가 보류
(heldForReview) 하거나 스팸으로 분류한 건 응답에 아예 안 들어온다.
그런데 관리자가 정말 판단해야 하는 건 대개 그쪽이다.

문서만 보고 '되겠지' 하고 수집기를 고치면 나중에 조용히 0건이 온다.
그래서 실제로 불러보고, 무엇이 되고 무엇이 안 되는지 눈으로 확인한다.

    python -m scripts.yt_held_probe <채널번호>

채널번호는 우리 DB 의 channels.id 다. 그 채널에 리프레시 토큰이
있어야 한다 (채널 관리 화면에서 연동).
"""

import asyncio
import sys

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Channel, Video
from app.db.session import AsyncSessionLocal, engine
from app.services import google_oauth as goog
from app.services.collector import API_BASE

# 유튜브가 댓글을 분류해두는 상태들.
상태들 = ("published", "heldForReview", "likelySpam")


async def 불러보기(client: httpx.AsyncClient, 설명: str, **params) -> None:
    r = await client.get("/commentThreads", params=params)
    if r.is_success:
        items = r.json().get("items", [])
        print(f"  {설명:<46} OK  {len(items)}건")
        for t in items[:3]:
            top = t["snippet"]["topLevelComment"]["snippet"]
            글 = top["textOriginal"].replace("\n", " ")[:38]
            print(f"       · {top['authorDisplayName'][:12]:<12} {글}")
        return
    err = r.json().get("error", {})
    reason = (err.get("errors") or [{}])[0].get("reason", "unknown")
    print(f"  {설명:<46} 실패 {r.status_code} {reason}")
    if err.get("message"):
        print(f"       {err['message'][:80]}")


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        raise SystemExit("사용법: python -m scripts.yt_held_probe <채널번호>")
    cid = int(sys.argv[1])
    cfg = get_settings()

    async with AsyncSessionLocal() as db:
        ch = await db.get(Channel, cid)
        if ch is None:
            raise SystemExit(f"[FAIL] 채널 {cid} 없음")
        if not ch.youtube_refresh_token:
            raise SystemExit(
                f"[FAIL] 채널 {cid} ({ch.channel_title}) 에 리프레시 토큰이 없다.\n"
                "       /app#/channels 에서 [+ 채널 연결] 을 먼저 해야 한다."
            )
        영상 = (
            await db.execute(
                select(Video).where(Video.channel_id == cid).limit(1)
            )
        ).scalar_one_or_none()
        핸들, 제목 = ch.youtube_channel_id, ch.channel_title
        영상id = 영상.youtube_video_id if 영상 else None

    token = await goog.refresh_access_token(
        cfg.google_client_id, cfg.google_client_secret, ch.youtube_refresh_token
    )

    print(f"채널: {제목} ({핸들})")
    print(f"영상: {영상id or '(수집된 영상 없음 — 영상별 조회는 건너뜀)'}\n")

    async with httpx.AsyncClient(
        base_url=API_BASE,
        timeout=20,
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        print("[채널 전체 기준]")
        for st in 상태들:
            await 불러보기(
                c,
                f"allThreadsRelatedToChannelId + {st}",
                part="snippet",
                allThreadsRelatedToChannelId=핸들,
                moderationStatus=st,
                maxResults=20,
                textFormat="plainText",
            )

        if 영상id:
            print("\n[영상 하나 기준]")
            for st in 상태들:
                await 불러보기(
                    c,
                    f"videoId + {st}",
                    part="snippet",
                    videoId=영상id,
                    moderationStatus=st,
                    maxResults=20,
                    textFormat="plainText",
                )

        print("\n[비교 — 지금 수집기가 쓰는 방식 (API 키, 공개만)]")
        async with httpx.AsyncClient(base_url=API_BASE, timeout=20) as k:
            if 영상id:
                await 불러보기(
                    k,
                    "videoId (키 인증, moderationStatus 없음)",
                    part="snippet",
                    videoId=영상id,
                    maxResults=20,
                    textFormat="plainText",
                    key=cfg.youtube_api_key,
                )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
