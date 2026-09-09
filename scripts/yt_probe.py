"""YouTube Data API 키 점검.

확인하려는 것: 우리가 소유하지 않은 남의 채널의 공개 댓글을 실제로 받아올 수 있는가.
이게 되어야 파일럿 채널 수집(F_R_107)이 성립한다.

이 키로 되는 건 '읽기'뿐이다. 숨김/차단(comments.setModerationStatus)은
OAuth 2.0 + 채널 소유자 권한이 따로 필요하므로 여기서는 확인하지 않는다.

    .venv/Scripts/python.exe -m scripts.yt_probe [영상URL또는ID] [수집개수]
"""

import re
import sys

import httpx

from app.core.config import get_settings
from app.services.collector import API_BASE as API
from app.services.collector import HINTS, extract_video_id

# 윈도우 콘솔 기본 인코딩(cp949)으로는 한글이 깨진다.
# SystemExit 메시지는 stderr로 나가므로 둘 다 바꿔줘야 한다.
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")


def extract_id(raw: str) -> str:
    """collector 의 파서를 쓰되, CLI 답게 SystemExit 으로 바꿔준다."""
    try:
        return extract_video_id(raw)
    except ValueError as e:
        raise SystemExit(f"[FAIL] {e}")


def call(client: httpx.Client, path: str, **params) -> dict:
    """API 호출 + 에러를 사람이 읽을 수 있게 번역."""
    r = client.get(f"/{path}", params=params)
    if r.is_success:
        return r.json()

    body = r.json().get("error", {})
    errors = body.get("errors") or [{}]
    reason = errors[0].get("reason", "unknown")

    print(f"\n[FAIL] {path} -> HTTP {r.status_code} ({reason})")
    print(f"       {body.get('message', '')}")
    if hint := HINTS.get(reason):
        print(f"  >>>  {hint}")
    raise SystemExit(1)


def main() -> None:
    key = get_settings().youtube_api_key
    if not key:
        raise SystemExit("[FAIL] .env에 YOUTUBE_API_KEY가 없다.")

    if len(sys.argv) < 2:
        raise SystemExit("사용법: python -m scripts.yt_probe <영상URL또는ID> [수집개수]")
    vid = extract_id(sys.argv[1])
    want = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    quota = 0

    with httpx.Client(base_url=API, params={"key": key}, timeout=15) as client:
        # 1) 영상 메타데이터 — 누구 채널인지 확인하는 게 목적
        meta = call(client, "videos", part="snippet,statistics", id=vid)
        if not meta.get("items"):
            raise SystemExit(f"[FAIL] 영상을 못 찾았다: {vid} (비공개이거나 삭제됨)")
        quota += 1

        item = meta["items"][0]
        snip, stats = item["snippet"], item.get("statistics", {})

        print(f"[OK] 영상   : {snip['title'][:60]}")
        print(f"[OK] 채널   : {snip['channelTitle']}  ({snip['channelId']})")
        print(f"[OK] 댓글수 : {stats.get('commentCount', '비공개')}")
        print("     ^ 우리 소유가 아닌 채널이면 '남의 채널 읽기 성공'이 증명된 것")

        # 2) 댓글 수집 — 페이지네이션이 실제로 도는지까지 확인
        comments, token, pages = [], None, 0
        while len(comments) < want:
            page = call(
                client,
                "commentThreads",
                part="snippet",
                videoId=vid,
                maxResults=min(100, want - len(comments)),
                order="time",
                textFormat="plainText",
                **({"pageToken": token} if token else {}),
            )
            quota += 1
            pages += 1
            comments += [
                t["snippet"]["topLevelComment"]["snippet"] for t in page.get("items", [])
            ]
            token = page.get("nextPageToken")
            if not token:
                break

    print(f"\n[OK] 수집 {len(comments)}건 / {pages}페이지 / 쿼터 {quota} units 소모")
    print(f"     (일일 한도 10,000 units 기준 남은 여유 충분)")

    print("\n--- 샘플 5건 ---")
    for c in comments[:5]:
        text = " ".join(c["textOriginal"].split())
        print(f"  {c['publishedAt'][:10]}  L{c['likeCount']:<4} {c['authorDisplayName'][:12]:<12} {text[:50]}")

    # 수집 파이프라인 설계에 바로 쓸 정보
    print("\n--- 쓸 수 있는 필드 ---")
    print(f"  {', '.join(sorted(comments[0].keys()))}" if comments else "  (댓글 없음)")
    print("  * authorDisplayName / authorChannelId / textOriginal 은 개인정보 —")
    print("    30일 보관 정책 대상. comments 테이블에 retention_expires_at 필요.")


if __name__ == "__main__":
    main()
