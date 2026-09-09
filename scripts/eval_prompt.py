"""프롬프트를 고친 뒤, 문제가 드러난 구간만 다시 판별해 전후를 비교한다.

전량 재판별은 안 한다 — 7,000건이면 1,200원이고, 대부분은 애초에 문제가
없던 것들이라 다시 물어볼 이유가 없다. 대신 네 구간을 본다.

  위협 / 기타   고치려던 대상. 제 자리를 찾아가야 한다.
  모욕 표본     가장 많은 카테고리. 행동 지적이 빠져야 한다.
  정상 표본     놓친 게 늘지 않았는지. 이게 제일 중요하다 —
                기준을 느슨하게 하면 오탐은 줄지만 미탐이 늘고, 그건 안 보인다.

DB 에는 쓰지 않는다. 비교만 하고 끝낸다.

    python -m scripts.eval_prompt
    python -m scripts.eval_prompt --apply    결과를 DB 에 반영
"""

import argparse
import asyncio
import collections
import sys

from sqlalchemy import text as sq

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal, engine
from app.services.llm import LlmJudge

# (이름, SQL 조건, 표본 상한)
GROUPS = [
    ("위협", "ra.category = '위협'", None),
    ("기타", "ra.category = '기타'", None),
    ("모욕", "ra.category = '모욕'", 200),
    ("정상", "ra.risk_level = 'safe'", 200),
]

SQL = """
SELECT c.id, c.content, p.content AS parent, ra.risk_level, ra.category
FROM comments c
JOIN risk_assessments ra
  ON ra.id = (SELECT max(id) FROM risk_assessments WHERE comment_id = c.id)
LEFT JOIN comments p ON p.youtube_comment_id = c.parent_comment_id
WHERE c.channel_id = :cid AND {cond}
ORDER BY {order}
{limit}
"""


async def load(cid: int, cond: str, cap: int | None):
    order = "random()" if cap else "c.like_count DESC"
    limit = f"LIMIT {cap}" if cap else ""
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(sq(SQL.format(cond=cond, order=order, limit=limit)), {"cid": cid})
        ).all()


def line(t: str, n: int = 44) -> str:
    return " ".join(t.split())[:n]


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", type=int, default=4)
    ap.add_argument("--apply", action="store_true", help="결과를 DB 에 반영")
    args = ap.parse_args()

    total_cost = 0.0
    changed_all: list[tuple[int, str, str, str, str, str]] = []

    for name, cond, cap in GROUPS:
        rows = await load(args.channel, cond, cap)
        if not rows:
            print(f"[{name}] 대상 없음\n")
            continue

        judge = LlmJudge(concurrency=8, max_calls=len(rows) + 5)
        res = await asyncio.gather(
            *(judge.judge(r[1], parent_text=r[2]) for r in rows)
        )
        total_cost += judge.stats.cost_usd

        moved = [
            (r[0], r[1], r[3], r[4], v.label, v.category)
            for r, v in zip(rows, res)
            if v.category != r[4]
        ]
        changed_all += moved
        after = collections.Counter(v.category or "(실패)" for v in res)

        print(f"[{name}] {len(rows)}건 → 바뀐 것 {len(moved)}건 ({len(moved)/len(rows):.0%})")
        print("   " + " · ".join(f"{k} {n}" for k, n in after.most_common(6)))
        for cid_, txt, _, _, lab, cat in moved[:5]:
            print(f"     -> {cat:<5} {line(txt)}")
        print(f"   실패 {judge.stats.errors}건 / {judge.stats.cost_usd * 1400:.0f}원\n")

    print("=" * 62)
    print(f"합계 비용 {total_cost * 1400:.0f}원 / 바뀐 것 {len(changed_all)}건")

    # 미탐 확인: 정상이었는데 유해로 올라간 것 = 이전에 놓쳤던 것
    newly_bad = [x for x in changed_all if x[2] == "safe" and x[4] != "safe"]
    print(f"\n이전에 '정상'이었는데 유해로 바뀐 것: {len(newly_bad)}건")
    for _, txt, _, _, lab, cat in newly_bad[:8]:
        print(f"   [{cat}] {line(txt, 50)}")
    if not newly_bad:
        print("   (없음 — 기준을 느슨하게 고쳤는데 놓친 게 늘지 않았다)")

    if args.apply:
        from app.services.moderation import judge_rules  # noqa: F401
        print("\n--apply 는 아직 붙이지 않았다. run_pipeline 으로 재판별해라.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
