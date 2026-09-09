"""LLM 이 틀린 문장에 COREPIN 을 돌려 서로 보완되는지 본다.

궁금한 것: COREPIN 이 LLM 의 구멍을 메워주는가?
  - 메워주면  -> 1차 COREPIN + 2차 LLM 2단계 구성에 근거가 생긴다
  - 똑같이 틀리면 -> COREPIN 을 빼도 잃을 게 없다

대조군(LLM 이 맞춘 문장)을 같이 돌린다. 없으면 "COREPIN 이 X% 잡았다"가
잘한 건지 못한 건지 판단할 기준이 없다.

무료 쿼터가 텍스트 단위로 차감되므로 호출 수를 최소화한다 (배치 100건씩).

    python -m scripts.eval_corepin
"""

import asyncio
import json
import os
import random
import sys
from pathlib import Path

import httpx

from app.core.config import get_settings

RESULT = Path(os.environ.get("CLAUDE_SCRATCHPAD", ".")) / "eval" / "llm_eval_v2_1000.json"
# 무료 티어 제한은 '분당 60'인데, 배치로 보내도 텍스트 하나가 1건으로 센다.
# 100건 배치를 보내면 그 자체로 100건을 쓴 것이 되어 즉시 429가 난다 (실측).
# 그래서 배치를 50으로 줄이고 분당 한 번씩만 보낸다.
BATCH = 50
WINDOW_SEC = 62
CONTROL_N = 100

_last_send = 0.0


async def corepin_batch(client: httpx.AsyncClient, texts: list[str]) -> list[dict]:
    global _last_send
    out: list[dict] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i : i + BATCH]

        wait = WINDOW_SEC - (asyncio.get_event_loop().time() - _last_send)
        if _last_send and wait > 0:
            print(f"    (레이트리밋 대기 {wait:.0f}초)")
            await asyncio.sleep(wait)

        for attempt in range(3):
            _last_send = asyncio.get_event_loop().time()
            r = await client.post("/v1/moderation/batch", json={"texts": chunk})
            if r.is_success:
                break
            if r.status_code == 429:
                delay = int(r.json().get("retry_after", 60)) + 2
                print(f"    (429 — {delay}초 후 재시도)")
                await asyncio.sleep(delay)
                continue
            raise SystemExit(f"[FAIL] {r.status_code}: {r.text[:300]}")
        else:
            raise SystemExit("[FAIL] 429 재시도 3회 초과")

        out += r.json()["results"]
    return out


def summarize(name: str, rows: list[dict], results: list[dict], want_block: bool) -> None:
    """want_block=True 면 '차단해야 맞는' 그룹이다."""
    blocked = sum(1 for r in results if r.get("blocked"))
    n = len(rows)
    verdict = "잡음" if want_block else "차단(오탐)"
    print(f"\n  {name}  ({n}건)")
    print(f"    COREPIN 차단 {blocked}건 ({blocked/n:.1%})  <- {verdict}")
    labels: dict[str, int] = {}
    for r in results:
        for l in r.get("labels") or []:
            labels[l] = labels.get(l, 0) + 1
    if labels:
        print(f"    라벨: {', '.join(f'{k} {v}' for k, v in sorted(labels.items()))}")
    for row, r in list(zip(rows, results))[:4]:
        mark = "차단" if r.get("blocked") else "통과"
        print(f"      [{mark}] {' '.join(row['text'].split())[:46]}")


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    if not RESULT.exists():
        raise SystemExit(f"[FAIL] LLM 결과 없음: {RESULT}")
    res = json.loads(RESULT.read_text(encoding="utf-8"))

    fp = [x for x in res if not x["gold_abuse"] and x["label"] == "harmful"]
    fn = [x for x in res if x["gold_abuse"] and x["label"] == "safe"]
    ok = [
        x
        for x in res
        if (x["gold_abuse"] and x["label"] == "harmful")
        or (not x["gold_abuse"] and x["label"] == "safe")
    ]
    ctrl_abuse = random.Random(42).sample([x for x in ok if x["gold_abuse"]], CONTROL_N // 2)
    ctrl_clean = random.Random(42).sample([x for x in ok if not x["gold_abuse"]], CONTROL_N // 2)

    total = len(fp) + len(fn) + CONTROL_N
    print(f"COREPIN 호출 예정: {total}건")
    print(f"  LLM 오탐(clean인데 harmful)  {len(fp)}건")
    print(f"  LLM 미탐(악플인데 safe)      {len(fn)}건")
    print(f"  대조군(LLM이 맞춘 것)        {CONTROL_N}건")

    cfg = get_settings()
    async with httpx.AsyncClient(
        base_url=cfg.corepin_base_url,
        headers={"Authorization": f"Bearer {cfg.corepin_api_key}"},
        timeout=60,
    ) as c:
        groups = [
            ("LLM 오탐 — clean 인데 LLM 이 harmful", fp, False),
            ("LLM 미탐 — 악플인데 LLM 이 safe", fn, True),
            ("대조군 — 악플, LLM 이 맞춤", ctrl_abuse, True),
            ("대조군 — clean, LLM 이 맞춤", ctrl_clean, False),
        ]
        print("\n" + "=" * 62)
        quota = None
        for name, rows, want in groups:
            if not rows:
                continue
            out = await corepin_batch(c, [x["text"] for x in rows])
            summarize(name, rows, out, want)
            quota = (out[-1].get("meta") or {}).get("quota_remaining", quota)

    print(f"\n남은 쿼터(지연 반영): {quota}")


if __name__ == "__main__":
    asyncio.run(main())
