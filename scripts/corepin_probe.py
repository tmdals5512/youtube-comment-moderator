"""COREPIN API 점검.

확인하려는 것 세 가지.
  1) 키가 유효한가 / 응답 형식이 문서와 같은가
  2) 배치 호출이 쿼터를 '요청 1건'으로 세는가 '텍스트 N건'으로 세는가
     -> 월 1,000 무료가 텍스트 1,000건인지 100,000건인지가 여기서 갈린다.
        예산 전략 전체가 이 답에 달려 있다.
  3) 초성 위장("ㅅㅂ")을 잡는가, 아니면 정상이라고 오판하는가
     -> 이 프로젝트의 전제 그 자체다.

쿼터를 최소로 쓴다: classify 1회 + batch 1회.

    .venv/Scripts/python.exe -m scripts.corepin_probe
"""

import json
import sys

import httpx

from app.core.config import get_settings

# 그룹별로 하나씩. 배치 한 번에 다 넣는다.
SAMPLES = [
    ("정상", "오늘 영상 잘 봤습니다"),
    ("명백 욕설", "개새끼야"),
    ("초성 위장", "ㅅㅂ 못하네"),        # <- 핵심 가설
    ("특수문자 위장", "ㅂ@ㅅ ㅋㅋ"),      # <- 핵심 가설
]

PROBE_TEXT = "시발 진짜 못하네"


def show(label: str, data: dict) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:900])


def quota_of(data: dict) -> int | None:
    return (data.get("meta") or {}).get("quota_remaining")


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")

    cfg = get_settings()
    if not cfg.corepin_api_key:
        raise SystemExit("[FAIL] .env에 COREPIN_API_KEY가 없다.")

    with httpx.Client(
        base_url=cfg.corepin_base_url,
        headers={"Authorization": f"Bearer {cfg.corepin_api_key}"},
        timeout=20,
    ) as c:
        # 1) 인증 없이 되는 헬스체크부터 (쿼터 소모 없음)
        h = c.get("/v1/health")
        print(f"[health] {h.status_code} {h.text[:200]}")
        if not h.is_success:
            raise SystemExit("[FAIL] 서버에 닿지 않는다. base_url 확인 필요.")

        m = c.get("/v1/models")
        print(f"[models] {m.status_code} {m.text[:300]}")

        # 2) 단건 호출 — 응답 형식과 쿼터 기준선 확인
        r1 = c.post("/v1/moderation/classify", json={"text": PROBE_TEXT})
        if not r1.is_success:
            raise SystemExit(f"[FAIL] classify {r1.status_code}: {r1.text[:400]}")
        d1 = r1.json()
        show(f"classify: {PROBE_TEXT!r}", d1)
        before = quota_of(d1)

        # 3) 배치 호출 — 쿼터가 몇 개 줄어드는지가 핵심
        r2 = c.post(
            "/v1/moderation/batch",
            json={"texts": [t for _, t in SAMPLES]},
        )
        if not r2.is_success:
            raise SystemExit(f"[FAIL] batch {r2.status_code}: {r2.text[:400]}")
        d2 = r2.json()
        show("batch (4건)", d2)
        after = quota_of(d2)

    print("\n" + "=" * 62)
    print("결과 요약")
    print("=" * 62)

    # meta.quota_remaining 은 실시간이 아니라 지연 반영된다. 연속 호출 두 번을
    # 비교하면 0이 나오기도 해서, 이 값 하나로 과금 기준을 판단하면 틀린다.
    # 실측 결과: 누적으로 보면 '보낸 텍스트 수'와 정확히 일치했다.
    # -> 배치는 쿼터를 아껴주지 않는다. 레이트리밋(분당 60 요청)과 지연만 줄여준다.
    if before is not None and after is not None:
        print(f"  쿼터(지연 반영): {before} -> {after}")
        print(f"  * 이 차이만으로 판단하지 말 것. 누적 소모 = 보낸 텍스트 총합이다.")
        print(f"  * 월 1,000 '텍스트'가 상한 -> 표적 샘플링 필수.")
    else:
        print("  쿼터 필드(meta.quota_remaining)가 응답에 없다. 대시보드로 확인 필요.")

    # 가설 확인: 위장 표현을 잡는가
    results = d2.get("results") or d2.get("items") or []
    if results:
        print("\n  샘플별 판정:")
        for (group, text), res in zip(SAMPLES, results):
            blocked = res.get("blocked")
            labels = ",".join(res.get("labels") or []) or "-"
            print(f"    [{'차단' if blocked else '통과'}] {group:<8} {labels:<10} {text}")
        print("\n  * '초성 위장'과 '특수문자 위장'이 통과로 나오면,")
        print("    COREPIN 뒤에 우리 신호 판정이 필요하다는 전제가 실증된 것.")


if __name__ == "__main__":
    main()
