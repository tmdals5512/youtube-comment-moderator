"""OpenAI Moderation API 를 우리 LLM 판정과 대조한다.

무료 API 라 2차(COREPIN 자리)에 넣을 수 있는지 보는 게 목적이다.
핵심 질문 두 가지.
  - 우리가 놓친 걸 잡아주나 (safe 로 통과시킨 것 중 flagged 가 있나)
  - 우리가 잡은 걸 놓치나 (harmful 중 not flagged 가 얼마나)

주의: 정답 라벨이 없다. 그래서 이건 '정확도'가 아니라 '불일치 목록'이다.
어느 쪽이 맞는지는 사람이 봐야 안다 — 그 목록을 뽑는 게 이 스크립트의 일이다.

    python -m scripts.eval_moderation [입력json] [건수]
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from openai import AsyncOpenAI, RateLimitError

from app.core.config import get_settings

MODEL = "omni-moderation-latest"
BATCH = 32              # moderation 은 input 배열을 받는다
PAUSE = 1.0             # 무료지만 분당 한도가 따로 있다 (실측으로 429 를 맞았다)
OUT_DIR = Path(os.environ.get("CLAUDE_SCRATCHPAD", ".")) / "eval"
DEFAULT_IN = OUT_DIR / "pipeline_run_ch7.json"


async def one_batch(client, chunk: list[str]) -> list[dict]:
    """429 는 기다렸다 다시 친다."""
    safe = [t if t.strip() else "." for t in chunk]   # 빈 문자열은 API 가 거부한다
    for attempt in range(7):
        try:
            r = await client.moderations.create(model=MODEL, input=safe)
            return [x.model_dump() for x in r.results]
        except RateLimitError:
            wait = min(2 ** attempt, 60)
            print(f"  [429] {wait}s 대기 후 재시도     ", end="\r")
            await asyncio.sleep(wait)
    raise SystemExit("[FAIL] 레이트 리밋이 안 풀린다. 나중에 다시 돌려라.")


async def moderate(client, texts: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(texts), BATCH):
        out += await one_batch(client, texts[i : i + BATCH])
        print(f"  {min(i + BATCH, len(texts))}/{len(texts)}          ", end="\r")
        await asyncio.sleep(PAUSE)
    print()
    return out


def top_category(res: dict) -> tuple[str, float]:
    scores = res.get("category_scores", {}) or {}
    if not scores:
        return "", 0.0
    name = max(scores, key=lambda k: scores[k] or 0.0)
    return name, scores[name] or 0.0


def line(t: str, n: int = 46) -> str:
    return " ".join(t.split())[:n]


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IN
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if not src.exists():
        raise SystemExit(f"[FAIL] 파일 없음: {src}")

    rows = json.load(src.open(encoding="utf-8"))[:limit]
    print(f"{src.name} — {len(rows)}건 / {MODEL} (무료)\n")

    client = AsyncOpenAI(api_key=get_settings().openai_api_key)
    results = await moderate(client, [r["text"] for r in rows])

    for r, m in zip(rows, results):
        cat, score = top_category(m)
        r["mod_flagged"] = bool(m.get("flagged"))
        r["mod_category"] = cat
        r["mod_score"] = round(score, 4)

    ours_bad = [r for r in rows if r["llm_label"] == "harmful"]
    ours_ok = [r for r in rows if r["llm_label"] == "safe"]
    flagged = [r for r in rows if r["mod_flagged"]]

    print("\n" + "=" * 66)
    print(f"Moderation 이 flagged 로 본 것: {len(flagged)}건 ({len(flagged)/len(rows):.1%})")
    print(f"우리 LLM 이 harmful 로 본 것:   {len(ours_bad)}건 ({len(ours_bad)/len(rows):.1%})")
    print("=" * 66)

    both = [r for r in ours_bad if r["mod_flagged"]]
    only_ours = [r for r in ours_bad if not r["mod_flagged"]]
    only_mod = [r for r in ours_ok if r["mod_flagged"]]

    print(f"\n  둘 다 유해            {len(both):>4}건")
    print(f"  우리만 유해           {len(only_ours):>4}건   <- Moderation 이 놓친 것")
    print(f"  Moderation 만 유해    {len(only_mod):>4}건   <- 우리가 놓쳤을 수 있는 것")
    if ours_bad:
        print(f"\n  우리 harmful 중 Moderation 도 잡은 비율: {len(both)/len(ours_bad):.1%}")

    print("\n" + "-" * 66)
    print("[우리만 유해] Moderation 이 못 잡은 것 — 한국어 약점이 여기 드러난다")
    print("-" * 66)
    for r in only_ours[:12]:
        print(f"  {r['llm_category']:<5} {line(r['text'])}")
    if not only_ours:
        print("  (없음)")

    print("\n" + "-" * 66)
    print("[Moderation 만 유해] 우리가 통과시킨 것 — 미탐이면 여기 있다")
    print("-" * 66)
    for r in sorted(only_mod, key=lambda x: -x["mod_score"])[:12]:
        print(f"  {r['mod_category']:<22}{r['mod_score']:.2f}  {line(r['text'], 32)}")
    if not only_mod:
        print("  (없음)")

    print("\n" + "-" * 66)
    print("우리 카테고리별 Moderation 적중률")
    print("-" * 66)
    cats: dict[str, list] = {}
    for r in ours_bad:
        cats.setdefault(r["llm_category"], []).append(r)
    for cat, group in sorted(cats.items(), key=lambda x: -len(x[1])):
        hit = sum(1 for r in group if r["mod_flagged"])
        print(f"  {cat:<6}{len(group):>4}건 중 {hit:>3}건 잡음  ({hit/len(group):5.1%})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "moderation_compare.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n전체 결과: {out}")


if __name__ == "__main__":
    asyncio.run(main())
