"""서버를 띄운 상태에서 판정 결과를 표로 확인한다.

    .venv/Scripts/python.exe -m scripts.demo [포트]
"""

import sys

import httpx

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8000}/api"

SAMPLES = [
    "ㅅㅂ 못하네",
    "시1발 진짜",
    "시이이이발",
    "발컨이네 ㅋㅋ",
    "오늘 영상 재밌네요",
]

MARK = {"block": "[차단]", "review": "[보류]", "pass": "[통과]"}


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as c:
        channels = [
            (ch["id"], ch["title"])
            for ch in [
                {"id": int(i), "title": t}
                for i, t in _channels(c)
            ]
        ]

        for cid, title in channels:
            rules = c.get(f"/channels/{cid}/rules").json()
            print(f"\n=== {title} (channel_id={cid}) ===")
            for r in rules:
                v = "O" if r["expand_variants"] else "X"
                print(f"  {r['rule_value']:<7} {r['action']:<6} 변형={v}")
            print()
            for text in SAMPLES:
                d = c.post(
                    "/moderation/check", json={"channel_id": cid, "text": text}
                ).json()
                print(f"  {MARK[d['verdict']]} {text:<20} {d['reason']}")


def _channels(c: httpx.Client):
    """시드로 만든 채널을 찾는다 (채널 목록 API는 아직 없음)."""
    for cid in range(1, 20):
        r = c.get(f"/channels/{cid}/rules")
        if r.status_code == 200 and r.json():
            yield cid, f"채널 {cid}"


if __name__ == "__main__":
    main()
