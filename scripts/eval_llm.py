"""UnSmile 라벨로 LLM 판별 성능을 채점한다.

악플 N건 + clean N건을 무작위로 뽑아 LLM에 돌리고 정답과 대조한다.
50:50 표본이라 재현율은 그대로 믿어도 되지만, 정밀도는 실제 악플 비율로
따로 보정해야 한다 (실제 채널에서는 악플이 8% 안팎이다).

    python -m scripts.eval_llm --dry       # 표본·프롬프트·예상비용만 확인 (API 호출 없음)
    python -m scripts.eval_llm             # 실제 실행 (기본 500+500)
    python -m scripts.eval_llm --n 100     # 규모 조절
"""

import argparse
import asyncio
import csv
import json
import os
import random
import sys
from pathlib import Path

from app.services.llm import SYSTEM_PROMPT, LlmJudge, sanitize

DEFAULT_CSV = Path.home() / "Downloads" / "kor_unsmile_train.csv"
OUT_DIR = Path(os.environ.get("CLAUDE_SCRATCHPAD", ".")) / "eval"
SEED = 42


def load_sample(path: Path, n: int) -> list[tuple[str, bool]]:
    """(문장, 악플여부) 목록. 악플 n건 + clean n건."""
    csv.field_size_limit(10**7)
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    col = list(rows[0].keys())[0]

    abuse = [r[col] for r in rows if r["악플/욕설"].strip() == "1"]
    clean = [r[col] for r in rows if r["clean"].strip() == "1"]

    rnd = random.Random(SEED)
    picked = [(t, True) for t in rnd.sample(abuse, min(n, len(abuse)))]
    picked += [(t, False) for t in rnd.sample(clean, min(n, len(clean)))]
    rnd.shuffle(picked)  # 순서 편향 제거
    return picked


def report(sample, verdicts) -> None:
    # LLM 은 block/review/pass 3분류, 정답은 악플/clean 2분류다.
    # block 만 '유해로 판정'으로 볼지, review 까지 포함할지 둘 다 본다.
    for name, positives in (("block만", {"block"}), ("block+review", {"block", "review"})):
        tp = sum(1 for (_, gold), v in zip(sample, verdicts) if gold and v.verdict in positives)
        fp = sum(1 for (_, gold), v in zip(sample, verdicts) if not gold and v.verdict in positives)
        fn = sum(1 for (_, gold), v in zip(sample, verdicts) if gold and v.verdict not in positives)
        rec = tp / (tp + fn) if tp + fn else 0
        prec = tp / (tp + fp) if tp + fp else 0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
        print(f"\n  [{name}을 유해로 볼 때]")
        print(f"    재현율 {rec:6.1%}   정밀도 {prec:6.1%}   F1 {f1:6.1%}")
        print(f"    악플을 잡음 {tp} / 놓침 {fn}   |   clean 오탐 {fp}")

    print("\n  판정 분포")
    for v in ("block", "review", "pass"):
        a = sum(1 for (_, g), r in zip(sample, verdicts) if g and r.verdict == v)
        c = sum(1 for (_, g), r in zip(sample, verdicts) if not g and r.verdict == v)
        print(f"    {v:<7} 악플 {a:>4}건 / clean {c:>4}건")


def show_errors(sample, verdicts, limit: int = 8) -> None:
    miss = [(t, v) for (t, g), v in zip(sample, verdicts) if g and v.verdict == "pass"]
    over = [(t, v) for (t, g), v in zip(sample, verdicts) if not g and v.verdict == "block"]

    print(f"\n  ── 놓친 악플 (pass 처리) {len(miss)}건 중 일부 ──")
    for t, v in miss[:limit]:
        print(f"    {' '.join(t.split())[:52]}")
        print(f"      -> {v.reason[:44]}")
    print(f"\n  ── 오탐 (clean 인데 block) {len(over)}건 중 일부 ──")
    for t, v in over[:limit]:
        print(f"    {' '.join(t.split())[:52]}")
        print(f"      -> {v.reason[:44]}")


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500, help="라벨당 표본 수 (기본 500)")
    ap.add_argument("--dry", action="store_true", help="API 호출 없이 표본과 예상비용만")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = ap.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"[FAIL] CSV 없음: {args.csv}")

    sample = load_sample(args.csv, args.n)
    n_abuse = sum(1 for _, g in sample if g)
    print(f"표본 {len(sample)}건 (악플 {n_abuse} / clean {len(sample)-n_abuse}, seed={SEED})")

    if args.dry:
        import tiktoken

        enc = tiktoken.encoding_for_model("gpt-5.6-luna")
        sys_tok = len(enc.encode(SYSTEM_PROMPT))
        body = sum(len(enc.encode(sanitize(t))) for t, _ in sample)
        out_est = 40 * len(sample)
        cost = ((sys_tok * len(sample) + body) * 0.2 + out_est * 1.2) / 1e6

        print(f"\n=== 실제로 보낼 프롬프트 ===\n{SYSTEM_PROMPT}\n")
        print("=== 표본 예시 10건 (정답 라벨 포함) ===")
        for t, g in sample[:10]:
            print(f"  [{'악플 ' if g else 'clean'}] {' '.join(sanitize(t).split())[:56]}")
        print(f"\n=== 예상 비용 ===")
        print(f"  시스템 프롬프트 {sys_tok}토큰 x {len(sample)}건")
        print(f"  본문 합계 {body:,}토큰 / 출력 추정 {out_est:,}토큰")
        print(f"  약 ${cost:.3f}  (약 {cost*1400:.0f}원)  <- 캐싱 미적용 기준, 실제는 더 낮음")
        print("\n실행하려면 --dry 를 빼고 다시 돌리세요.")
        return

    judge = LlmJudge(concurrency=8, max_calls=len(sample) + 10)
    print("판정 중...")
    verdicts = await judge.judge_many([t for t, _ in sample])

    st = judge.stats
    print(f"\n호출 {st.calls}건 / 실패 {st.errors}건")
    print(f"토큰 입력 {st.input_tokens:,} (캐시 {st.cached_tokens:,}) / 출력 {st.output_tokens:,}")
    print(f"비용 ${st.cost_usd:.4f} (약 {st.cost_usd*1400:.0f}원)")

    if st.errors:
        first = next((v.error for v in verdicts if v.error), None)
        print(f"  첫 오류: {first}")

    report(sample, verdicts)
    show_errors(sample, verdicts)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"llm_eval_v2_{len(sample)}.json"
    out.write_text(
        json.dumps(
            [
                {"text": t, "gold_abuse": g, "verdict": v.verdict, "label": v.label,
                 "category": v.category, "confidence": v.confidence, "reason": v.reason}
                for (t, g), v in zip(sample, verdicts)
            ],
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n전체 결과 저장: {out}")


if __name__ == "__main__":
    asyncio.run(main())
