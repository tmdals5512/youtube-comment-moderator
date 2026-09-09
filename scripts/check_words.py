"""1차 기본 차단어 후보를 실제 유튜브 댓글로 검증한다.

rules_default.py 에 적어둔 재등록 조건 두 가지를 지키기 위한 도구다.
  1. 검증을 실제 사용처(유튜브 댓글)에서 할 것
  2. 2글자 단어의 초성 확장을 쓰지 말 것

채점 방법: 이미 LLM 판정이 붙어 있는 댓글을 쓴다. 어떤 단어에 걸린 댓글을
LLM 이 safe 라고 했다면 그건 오탐 후보다. 사람이 전부 눈으로 볼 필요 없이
의심스러운 것만 골라낼 수 있다.

1차 차단은 LLM 을 건너뛰므로 되돌릴 기회가 없다. 그래서 기준은
'오탐 0건' 이어야 한다. 하나라도 나오면 그 단어는 넣지 않는다.

    python -m scripts.check_words                 기본 후보 검사
    python -m scripts.check_words 씨발 병신 지랄    직접 지정
    python -m scripts.check_words --expand        초성 확장을 켜고 (위험 확인용)
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import text as sq

from app.db.session import AsyncSessionLocal, engine
from app.services.pattern import FLAGS, expand, normalize

EVAL_DIR = Path(os.environ.get("CLAUDE_SCRATCHPAD", ".")) / "eval"

# 검사할 후보. '문맥과 무관하게 욕'인 것만 올린다.
# 강조어로도 쓰이는 것(존나 등)은 일부러 뺐다 — 프롬프트 예시에서도
# "와 이거 존나 잘만들었네" 는 정상으로 판정하기로 했다.
CANDIDATES = [
    # 확실해 보이는 것
    "씨발", "시발", "씨팔", "시팔", "씨빨",
    "좆같", "좆됐", "좆까",
    "개새끼", "개새기", "개색기",
    "병신", "븅신",
    "지랄", "니미", "애미", "썅",
    "미친년", "미친놈",
    # 위험할 것으로 의심되는 것 — 왜 빼야 하는지 눈으로 보려고 같이 돌린다
    "새끼", "보지", "씹", "존나",
]


def load_labeled() -> list[tuple[str, str]]:
    """(본문, LLM 라벨) 목록. 라벨이 있어야 오탐을 셀 수 있다."""
    out: list[tuple[str, str]] = []
    for f in sorted(EVAL_DIR.glob("pipeline_run*.json")):
        for r in json.load(f.open(encoding="utf-8")):
            if r.get("llm_label"):
                out.append((r["text"], r["llm_label"]))
    return out


async def load_db() -> list[tuple[str, str]]:
    """DB 에 있는 댓글 + 최신 판정 (라벨 없으면 빈 문자열)."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(sq("""
            SELECT c.content, coalesce(ra.risk_level, '')
            FROM comments c
            LEFT JOIN LATERAL (
                SELECT risk_level FROM risk_assessments r
                WHERE r.comment_id = c.id ORDER BY r.id DESC LIMIT 1
            ) ra ON true
        """))).all()
    await engine.dispose()
    return [(r[0], r[1]) for r in rows]


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("words", nargs="*", default=None)
    ap.add_argument("--expand", action="store_true", help="초성·공백 변형까지 잡는다")
    ap.add_argument("--show", type=int, default=4, help="단어당 보여줄 예시 수")
    args = ap.parse_args()

    words = args.words or CANDIDATES

    # 라벨 붙은 것(오탐 판정용) + DB 전체(적용 범위 확인용)를 합친다
    texts = load_labeled()
    seen = {t for t, _ in texts}
    texts += [(t, l) for t, l in asyncio.run(load_db()) if t not in seen]

    labeled = sum(1 for _, l in texts if l)
    print(f"검사 대상 {len(texts)}건 (LLM 라벨 있는 것 {labeled}건)")
    print(f"초성·공백 확장: {'켬 — 위험' if args.expand else '끔'}\n")

    print(f"{'단어':<8}{'걸림':>5}{'유해':>5}{'오탐':>5}  판정")
    print("-" * 62)

    verdict: dict[str, list[str]] = {"안전": [], "위험": [], "무의미": []}
    details: list[tuple[str, list[str]]] = []

    for w in words:
        rx = re.compile(expand(w, args.expand), FLAGS)
        hits = [(t, l) for t, l in texts if rx.search(normalize(t))]
        bad = [x for x in hits if x[1] in ("harmful", "ambiguous")]
        # LLM 이 정상이라 한 것에 걸렸다 = 자동 차단하면 안 되는 것
        false_hits = [t for t, l in hits if l == "safe"]

        if false_hits:
            mark, bucket = "X 오탐 있음", "위험"
        elif not hits:
            mark, bucket = "- 한 건도 안 걸림", "무의미"
        else:
            mark, bucket = "O 오탐 0", "안전"
        verdict[bucket].append(w)

        print(f"{w:<8}{len(hits):>5}{len(bad):>5}{len(false_hits):>5}  {mark}")
        if false_hits:
            details.append((w, false_hits))

    if details:
        print(f"\n{'=' * 62}\n오탐 — 이 단어를 넣으면 아래 댓글이 조용히 차단된다\n{'=' * 62}")
        for w, examples in details:
            print(f"\n[{w}] {len(examples)}건")
            for t in examples[: args.show]:
                print(f"   {' '.join(t.split())[:56]}")

    print(f"\n{'=' * 62}")
    print(f"넣어도 되는 것 ({len(verdict['안전'])}개): {' '.join(verdict['안전']) or '없음'}")
    print(f"넣으면 안 되는 것 ({len(verdict['위험'])}개): {' '.join(verdict['위험']) or '없음'}")
    print(f"데이터에 안 나온 것 ({len(verdict['무의미'])}개): {' '.join(verdict['무의미']) or '없음'}")
    print("\n* '안 나온 것'은 안전하다는 뜻이 아니라 판단 근거가 없다는 뜻이다.")


if __name__ == "__main__":
    main()
