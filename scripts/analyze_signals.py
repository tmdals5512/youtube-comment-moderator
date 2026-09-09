"""실제 유튜브 댓글로 신호 분포를 본다.

목적: 발표자료의 "초성표현 +30" 임계값을 추측이 아니라 데이터로 정하는 것.
한 번 받은 댓글은 캐시해서 쿼터를 다시 쓰지 않는다.

    .venv/Scripts/python.exe -m scripts.analyze_signals <영상URL> [개수]
"""

import json
import os
import sys
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.services.signals import extract
from scripts.yt_probe import API, call, extract_id

CACHE = Path(
    os.environ.get("CLAUDE_SCRATCHPAD", ".")
) / "comments_cache"


def fetch(vid: str, want: int) -> list[dict]:
    cache_file = CACHE / f"{vid}.json"
    if cache_file.exists():
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        if len(rows) >= want:
            print(f"[캐시] {cache_file.name} 에서 {len(rows)}건 (쿼터 0)")
            return rows[:want]

    key = get_settings().youtube_api_key
    rows: list[dict] = []
    token = None
    with httpx.Client(base_url=API, params={"key": key}, timeout=15) as client:
        while len(rows) < want:
            page = call(
                client, "commentThreads", part="snippet", videoId=vid,
                maxResults=min(100, want - len(rows)), order="time",
                textFormat="plainText",
                **({"pageToken": token} if token else {}),
            )
            rows += [
                {
                    "text": t["snippet"]["topLevelComment"]["snippet"]["textOriginal"],
                    "likes": t["snippet"]["topLevelComment"]["snippet"]["likeCount"],
                }
                for t in page.get("items", [])
            ]
            token = page.get("nextPageToken")
            if not token:
                break

    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"[수집] {len(rows)}건 (캐시 저장)")
    return rows


def bar(n: int, total: int, width: int = 30) -> str:
    filled = round(width * n / total) if total else 0
    return "#" * filled + "." * (width - filled)


def histogram(title: str, values: list[float], edges: list[float]) -> None:
    print(f"\n  {title}")
    total = len(values)
    for lo, hi in zip(edges, edges[1:] + [1.01]):
        n = sum(1 for v in values if lo <= v < hi)
        label = f"{lo:.0%}~{hi:.0%}" if hi <= 1 else f"{lo:.0%}+"
        print(f"    {label:>9}  {bar(n, total)} {n:4d}건 ({n/total:5.1%})")


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")

    vid = extract_id(sys.argv[1])
    want = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    rows = fetch(vid, want)
    scored = [(r, extract(r["text"])) for r in rows]
    total = len(scored)

    print(f"\n{'='*70}\n댓글 {total}건 신호 분석\n{'='*70}")

    edges = [0.0, 0.05, 0.10, 0.20, 0.30, 0.50]
    histogram("① 자모 비율 (ㅋㅋㅋ 포함 = 발표자료 그대로 구현했을 때)",
              [s.jamo_ratio for _, s in scored], edges)
    histogram("② 자모 비율 (ㅋㅎㅠㅜ 제외 = 실제 위장 신호)",
              [s.meaning_jamo_ratio for _, s in scored], edges)

    print("\n  ③ 그 밖의 신호")
    for name, n in [
        ("글자사이 특수문자 삽입", sum(1 for _, s in scored if s.has_gap_filler)),
        ("동일문자 3연속 이상", sum(1 for _, s in scored if s.max_repeat >= 3)),
        ("한글 비율 50% 미만", sum(1 for _, s in scored if s.hangul_ratio < 0.5)),
        ("10자 미만 짧은 댓글", sum(1 for _, s in scored if s.length < 10)),
    ]:
        print(f"    {name:<22} {bar(n, total)} {n:4d}건 ({n/total:5.1%})")

    # ㅋㅋㅋ 때문에 오탐되는 케이스를 직접 보여준다
    noise = sorted(
        [x for x in scored if x[1].jamo_ratio >= 0.15 and x[1].meaning_jamo_ratio < 0.05],
        key=lambda x: -x[1].jamo_ratio,
    )
    print(f"\n  ④ 자모 비율만 봤으면 오탐됐을 정상 댓글: {len(noise)}건 ({len(noise)/total:.1%})")
    for r, s in noise[:5]:
        text = " ".join(r["text"].split())[:45]
        print(f"    자모 {s.jamo_ratio:.0%} → 의미자모 {s.meaning_jamo_ratio:.0%}  {text}")

    # 실제로 의심스러운 것들
    suspect = sorted(scored, key=lambda x: -x[1].meaning_jamo_ratio)
    print("\n  ⑤ 의미자모 비율 상위 (실제 검토 후보)")
    for r, s in suspect[:8]:
        if s.meaning_jamo_ratio == 0:
            break
        text = " ".join(r["text"].split())[:45]
        flag = " [특수문자삽입]" if s.has_gap_filler else ""
        print(f"    {s.meaning_jamo_ratio:5.0%} L{r['likes']:<4} {text}{flag}")

    print("\n  ⑥ 임계값별 검토 대상 비율 (목표: 30% 이하)")
    for th in (0.05, 0.10, 0.15, 0.20, 0.30):
        n = sum(1 for _, s in scored if s.meaning_jamo_ratio >= th)
        mark = " ← 목표 충족" if n / total <= 0.30 else ""
        print(f"    의미자모 {th:.0%} 이상 →  {n:4d}건 ({n/total:5.1%}){mark}")


if __name__ == "__main__":
    main()
