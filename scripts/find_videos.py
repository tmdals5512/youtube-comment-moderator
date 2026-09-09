"""수집할 만한 논란 영상을 검색해서 목록 파일로 뽑아준다.

사람이 유튜브를 뒤지는 대신 API 로 조건을 걸어 찾는다. 거르는 조건:
  - 최근 업로드 (오래된 논란 영상은 이미 청소돼서 껍데기만 남는다)
  - 댓글 일정 수 이상
  - 댓글이 꺼져 있지 않을 것 (논란 영상은 아예 꺼두는 경우가 흔하다)

    python -m scripts.find_videos 정치:논란 범죄:사건 연예:논란
    python -m scripts.find_videos 논란 --days 3 --min-comments 1000

'주제:검색어' 형식. 콜론이 없으면 검색어를 그대로 주제로 쓴다.
결과는 videos.txt 로 저장되며, 그대로 collect_videos 에 넘길 수 있다.

쿼터: 검색어당 100 units + 영상 50개당 1 units. 검색어 5개면 약 505 units.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import get_settings
from app.services.collector import YouTubeApiError, YouTubeCollector

DEFAULT_OUT = Path("videos.txt")


async def search(yt, keyword: str, after: str, limit: int) -> list[str]:
    """검색어 하나로 영상 ID 목록을 받는다 (100 units)."""
    data = await yt._call(
        "search",
        part="snippet",
        q=keyword,
        type="video",
        order="viewCount",       # 조회수 높은 것 = 댓글도 많다
        regionCode="KR",
        relevanceLanguage="ko",
        publishedAfter=after,
        maxResults=min(50, limit),
    )
    return [it["id"]["videoId"] for it in data.get("items", [])]


async def details(yt, video_ids: list[str]) -> list[dict]:
    """영상 50개씩 묶어 제목·댓글수를 받는다 (묶음당 1 unit)."""
    out = []
    for i in range(0, len(video_ids), 50):
        data = await yt._call(
            "videos", part="snippet,statistics", id=",".join(video_ids[i : i + 50])
        )
        out += data.get("items", [])
    return out


def usable(info: dict, min_comments: int) -> int | None:
    """쓸 만하면 댓글 수를, 아니면 None 을 준다.

    commentCount 키가 아예 없으면 댓글을 꺼둔 영상이다. 0 과 구분해야 한다.
    """
    count = info.get("statistics", {}).get("commentCount")
    if count is None:
        return None
    n = int(count)
    return n if n >= min_comments else None


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("keywords", nargs="+", help="'주제:검색어' 또는 '검색어'")
    ap.add_argument("--days", type=int, default=7, help="며칠 이내 업로드 (기본 7)")
    ap.add_argument("--min-comments", type=int, default=300)
    ap.add_argument("--per-keyword", type=int, default=25, help="검색어당 후보 수")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    key = get_settings().youtube_api_key
    if not key:
        raise SystemExit("[FAIL] .env에 YOUTUBE_API_KEY가 없다.")

    after = (datetime.now(UTC) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{args.days}일 이내 / 댓글 {args.min_comments}건 이상\n")

    rows: list[tuple[str, int, str, str, str]] = []   # 주제, 댓글수, 채널, 제목, id
    seen: set[str] = set()

    async with YouTubeCollector(key) as yt:
        for raw in args.keywords:
            topic, _, kw = raw.rpartition(":")
            topic = topic or kw

            try:
                ids = await search(yt, kw, after, args.per_keyword)
                infos = await details(yt, ids)
            except YouTubeApiError as e:
                print(f"[{kw}] 실패 — {e}")
                continue

            kept = 0
            for info in infos:
                if info["id"] in seen:      # 다른 검색어에서 이미 나온 영상
                    continue
                n = usable(info, args.min_comments)
                if n is None:
                    continue
                seen.add(info["id"])
                rows.append((topic, n, info["snippet"]["channelTitle"],
                             info["snippet"]["title"], info["id"]))
                kept += 1
            print(f"[{topic:<5}] '{kw}' 검색 {len(infos)}개 중 {kept}개 통과")

    if not rows:
        print("\n조건에 맞는 영상이 없다. --days 를 늘리거나 --min-comments 를 낮춰라.")
        return

    rows.sort(key=lambda r: -r[1])

    print(f"\n{'-' * 78}")
    print(f"{'주제':<6}{'댓글':>7}  {'채널':<16} 제목")
    print("-" * 78)
    for topic, n, ch, title, _ in rows:
        print(f"{topic:<6}{n:>7}  {ch[:14]:<16} {title[:38]}")

    lines = [f"# {datetime.now():%Y-%m-%d} 검색 결과. 필요없는 줄은 지우고 쓰면 된다.",
             f"# 조건: {args.days}일 이내 / 댓글 {args.min_comments}건 이상 / 댓글 꺼진 영상 제외", ""]
    for topic, n, ch, title, vid in rows:
        lines.append(f"# {ch} | {title}  ({n:,}건)")
        lines.append(f"{topic}  https://youtu.be/{vid}")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n{len(rows)}개 -> {args.out}")
    print(f"  1) {args.out} 열어서 안 쓸 줄 지우기")
    print(f"  2) python -m scripts.collect_videos {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
