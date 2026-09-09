"""1차 규칙 단어를 몇 개까지 넣는 게 좋은지 UnSmile 라벨로 채점한다.

단어를 늘리면 재현율은 오르지만 오탐도 같이 오른다. 어디서 꺾이는지를
데이터로 보고 정하려는 것. API 비용 0원.

단어 출처는 UnSmile 이 아니다 (CC-BY-NC-ND 라 파생물 논란 소지).
일반적으로 알려진 욕설·은어 + 우리가 유튜브에서 실제로 발견한 표현을 쓴다.

    python -m scripts.tune_rules [UnSmile CSV 경로]
"""

import csv
import re
import sys
from pathlib import Path

from app.services.pattern import FLAGS, expand, normalize

DEFAULT_CSV = Path.home() / "Downloads" / "kor_unsmile_train.csv"

# 단계별로 누적해서 넣는다. 앞쪽일수록 확실한 것.
TIERS: dict[str, list[str]] = {
    "T1 핵심 욕설": [
        "시발", "존나", "지랄", "병신", "개새끼", "좆", "썅", "니미",
    ],
    "T2 흔한 욕설": [
        "미친놈", "미친년", "등신", "머저리", "새끼", "닥쳐", "꺼져",
        "뒤져", "개소리", "찌질", "멍청이", "쓰레기", "역겹", "혐오스",
    ],
    "T3 성적·모욕": [
        "씹", "자지", "보지", "걸레", "창녀", "후장", "젖탱", "꼴리",
    ],
    "T4 유튜브 은어": [
        # 우리가 수집한 댓글에서 실제로 발견한 것들.
        # COREPIN 이 통과시켰던 표현이라 1차에서 잡아야 한다.
        "ㅆㅅㅌㅊ", "ㅈ같", "ㅈ망", "ㅈ됐", "노답", "극혐", "틀딱", "급식충",
    ],
}


def load(path: Path) -> tuple[list[str], list[str], list[str]]:
    csv.field_size_limit(10**7)
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    col = list(rows[0].keys())[0]  # BOM 때문에 이름이 지저분해서 위치로 잡는다
    abuse = [r[col] for r in rows if r["악플/욕설"].strip() == "1"]
    clean = [r[col] for r in rows if r["clean"].strip() == "1"]
    return [r[col] for r in rows], abuse, clean


def compile_words(words: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(expand(w, True), FLAGS) for w in words]


def score(pats, abuse, clean) -> tuple[float, float, float]:
    def hit(t: str) -> bool:
        n = normalize(t)
        return any(p.search(n) for p in pats)

    tp = sum(map(hit, abuse))          # 악플을 악플이라 함
    fp = sum(map(hit, clean))          # 정상을 악플이라 함
    recall = tp / len(abuse)
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return recall, precision, f1


def bar(v: float, width: int = 24) -> str:
    return "#" * round(v * width) + "." * (width - round(v * width))


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not path.exists():
        raise SystemExit(f"[FAIL] CSV 없음: {path}")

    _, abuse, clean = load(path)
    print(f"채점 대상: 악플/욕설 {len(abuse)}건 · clean {len(clean)}건\n")

    words: list[str] = []
    print(f"{'단계':<14}{'단어':>4}  {'재현율':>7} {'정밀도':>7} {'F1':>7}")
    print("-" * 62)

    rows = []
    for name, extra in TIERS.items():
        words += extra
        r, p, f1 = score(compile_words(words), abuse, clean)
        rows.append((name, len(words), r, p, f1))
        print(f"{name:<14}{len(words):>4}  {r:>6.1%} {p:>7.1%} {f1:>7.1%}  {bar(f1)}")

    print("\n=== 단어를 하나씩 뺐을 때 F1 변화 (기여도) ===")
    base_r, base_p, base_f1 = score(compile_words(words), abuse, clean)
    deltas = []
    for w in words:
        rest = [x for x in words if x != w]
        _, _, f1 = score(compile_words(rest), abuse, clean)
        deltas.append((base_f1 - f1, w))
    deltas.sort(reverse=True)

    print("  기여 큰 단어 (빼면 F1 하락):")
    for d, w in deltas[:6]:
        print(f"    {w:<10} {d:+.2%}")
    print("  기여 없거나 해로운 단어 (빼는 게 나음):")
    for d, w in deltas[-6:]:
        print(f"    {w:<10} {d:+.2%}")


if __name__ == "__main__":
    main()
