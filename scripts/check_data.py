"""데이터가 믿을 만한지 점검한다. 읽기만 한다.

정확도(맞았나)는 사람 라벨이 있어야 잴 수 있다. 이 스크립트가 보는 것은
그 앞 단계 — **판정을 믿고 쓸 수 있는 상태인가** 다.

  ① 수집   빠짐없이 가져왔나
  ② 판별   전부 판별됐나, 실패한 건 없나
  ③ 이력   지금 남아 있는 판정이 어느 기준으로 매겨진 것인가
  ④ 흔들림 같은 댓글을 다시 물으면 같은 답이 나오나 (--shake)

    python -m scripts.check_data
    python -m scripts.check_data --channel 4
    python -m scripts.check_data --channel 4 --shake 40   # LLM 재호출(유료)
"""

import argparse
import asyncio
import collections
import sys

from sqlalchemy import text as sq

from app.db.session import AsyncSessionLocal, engine


def 제목(s: str) -> None:
    print(f"\n{s}\n" + "─" * 58)


def 줄(이름: str, 값, 비고: str = "") -> None:
    print(f"  {이름:26} {str(값):>12}   {비고}")


async def 수집(db, cid) -> None:
    제목("① 수집 — 빠짐없이 가져왔나")
    rows = (await db.execute(sq("""
        SELECT v.video_id, left(v.title, 26) AS 제목, v.comment_count AS 유튜브,
               count(c.id) AS 수집, count(*) FILTER (WHERE c.is_reply) AS 답글
        FROM videos v LEFT JOIN comments c ON c.video_id = v.video_id
        WHERE (CAST(:cid AS INT) IS NULL OR v.channel_id = :cid)
        GROUP BY 1,2,3 ORDER BY 4 DESC
    """), {"cid": cid})).all()

    for r in rows:
        비율 = f"{r[3]/r[2]*100:.0f}%" if r[2] else "?"
        print(f"  {r[1]:28} 유튜브 {r[2] or 0:>6,} · 수집 {r[3]:>6,} ({비율})"
              f" · 답글 {r[4]:,}")
    print("\n  ※ 유튜브 수치는 답글·삭제분 계산이 달라 그대로 비교하면 안 된다.")
    print("     '수집이 적다' 가 아니라 '어디까지 받았나' 를 보는 값이다.")

    끊김 = (await db.execute(sq("""
        SELECT count(*) FROM comments c
        WHERE (CAST(:cid AS INT) IS NULL OR c.channel_id = :cid)
          AND c.parent_comment_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM comments p
                          WHERE p.youtube_comment_id = c.parent_comment_id)
    """), {"cid": cid})).scalar()
    줄("부모 없는 답글", f"{끊김:,}", "0 이어야 정상" if 끊김 == 0 else "← 확인 필요")

    중복 = (await db.execute(sq("""
        SELECT count(*) FROM (
          SELECT youtube_comment_id FROM comments
          WHERE (CAST(:cid AS INT) IS NULL OR channel_id = :cid)
          GROUP BY 1 HAVING count(*) > 1) t
    """), {"cid": cid})).scalar()
    줄("중복 수집", f"{중복:,}", "0 이어야 정상" if 중복 == 0 else "← 확인 필요")


async def 판별(db, cid) -> None:
    제목("② 판별 — 전부 판별됐나")
    r = (await db.execute(sq("""
        SELECT count(*) AS 전체,
               count(*) FILTER (WHERE c.status = 'pending') AS 미판별,
               count(*) FILTER (WHERE ra.id IS NULL) AS 판정없음,
               count(*) FILTER (WHERE ra.id IS NOT NULL
                                AND coalesce(ra.risk_level,'') = ''
                                AND ra.stage = 'llm') AS 판별실패,
               count(*) FILTER (WHERE c.embedding IS NULL) AS 임베딩없음
        FROM comments c
        LEFT JOIN risk_assessments ra
          ON ra.id = (SELECT max(id) FROM risk_assessments WHERE comment_id = c.id)
        WHERE (CAST(:cid AS INT) IS NULL OR c.channel_id = :cid)
    """), {"cid": cid})).first()

    전체 = r[0] or 1
    줄("전체 댓글", f"{r[0]:,}")
    줄("아직 판별 안 함", f"{r[1]:,}", f"{r[1]/전체*100:.1f}%")
    줄("판정 기록 없음", f"{r[2]:,}", f"{r[2]/전체*100:.1f}%")
    줄("판별하다 실패", f"{r[3]:,}",
       f"{r[3]/전체*100:.1f}%  ← 근거 없이 큐에 쌓임" if r[3] else "")
    줄("임베딩 없음", f"{r[4]:,}", "유사 사례 검색이 안 됨" if r[4] else "")


async def 이력(db, cid) -> None:
    제목("③ 이력 — 지금 판정이 어느 기준으로 매겨졌나")
    rows = (await db.execute(sq("""
        SELECT coalesce(ra.prompt_version, '(기록 없음)') AS 버전,
               min(ra.created_at)::date AS 처음,
               max(ra.created_at)::date AS 마지막,
               count(*) AS 건수
        FROM risk_assessments ra
        JOIN comments c ON c.id = ra.comment_id
        WHERE (CAST(:cid AS INT) IS NULL OR c.channel_id = :cid)
          AND ra.id = (SELECT max(id) FROM risk_assessments WHERE comment_id = c.id)
        GROUP BY 1 ORDER BY 4 DESC
    """), {"cid": cid})).all()

    from app.services.llm import prompt_version as 현재버전
    맥락 = (await db.execute(sq(
        "SELECT coalesce(context,'') FROM channels WHERE id = :cid"), {"cid": cid}
    )).scalar() if cid else ""
    지금 = 현재버전(맥락 or "")

    print(f"  지금 기준: {지금}\n")
    for 버전, 처음, 마지막, n in rows:
        표 = "  ← 지금 기준" if 버전 == 지금 else ""
        기간 = str(처음) if 처음 == 마지막 else f"{처음}~{마지막}"
        print(f"  {버전:16} {기간:24} {n:>7,}건{표}")

    낡음 = sum(n for v, _, _, n in rows if v != 지금)
    if 낡음:
        전체 = sum(n for *_, n in rows)
        print(f"\n  ※ {낡음:,}건({낡음/전체*100:.0f}%)이 지금과 다른 기준으로 매겨졌다.")
        print("     정확도를 재려면 한 기준으로 맞춰야 한다:")
        print(f"       python -m scripts.rejudge --channel {cid or '<id>'} --all")

    쌓임 = (await db.execute(sq("""
        SELECT count(*) FROM (
          SELECT comment_id FROM risk_assessments ra
          JOIN comments c ON c.id = ra.comment_id
          WHERE (CAST(:cid AS INT) IS NULL OR c.channel_id = :cid)
          GROUP BY 1 HAVING count(*) > 1) t
    """), {"cid": cid})).scalar()
    줄("여러 번 판별된 댓글", f"{쌓임:,}", "이력이 쌓이는 건 정상")


async def 흔들림(db, cid, n) -> None:
    제목(f"④ 흔들림 — 같은 댓글을 {3}번 물으면 같은 답이 나오나")
    from app.services.llm import LlmJudge

    rows = (await db.execute(sq("""
        SELECT c.content FROM comments c
        JOIN risk_assessments ra
          ON ra.id = (SELECT max(id) FROM risk_assessments WHERE comment_id = c.id)
        WHERE (CAST(:cid AS INT) IS NULL OR c.channel_id = :cid)
          AND ra.risk_level IS NOT NULL AND ra.risk_level <> ''
        ORDER BY md5(c.content) LIMIT :n
    """), {"cid": cid, "n": n})).all()
    글 = [r[0] for r in rows]

    ch = (await db.execute(sq(
        "SELECT coalesce(context,'') FROM channels WHERE id = :cid"), {"cid": cid}
    )).scalar() if cid else ""

    회 = []
    비용 = 0.0
    for _ in range(3):
        j = LlmJudge(concurrency=8, max_calls=len(글) + 10, channel_context=ch or "")
        회.append(await asyncio.gather(*(j.judge(t) for t in 글)))
        비용 += j.stats.cost_usd * 1400

    같은판정 = sum(1 for i in range(len(글)) if len({회[k][i].label for k in range(3)}) == 1)
    같은유형 = sum(
        1 for i in range(len(글))
        if len({(회[k][i].label, 회[k][i].category) for k in range(3)}) == 1
    )
    줄("3번 다 같은 판정", f"{같은판정}/{len(글)}", f"{같은판정/len(글)*100:.0f}%")
    줄("3번 다 같은 유형", f"{같은유형}/{len(글)}", f"{같은유형/len(글)*100:.0f}%")
    줄("비용", f"{비용:.0f}원")

    print("\n  갈린 예:")
    보임 = 0
    for i, t in enumerate(글):
        답 = [f"{회[k][i].label}/{회[k][i].category}" for k in range(3)]
        if len(set(답)) > 1 and 보임 < 4:
            보임 += 1
            print(f'    "{" ".join(t.split())[:46]}"')
            print(f"       {' · '.join(답)}")


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", type=int, default=None, help="생략하면 전체")
    ap.add_argument("--shake", type=int, default=0,
                    help="흔들림 측정할 댓글 수 (LLM 을 3번 부른다, 유료)")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        대상 = f"채널 {args.channel}" if args.channel else "전체 채널"
        print(f"데이터 점검 · {대상}")
        await 수집(db, args.channel)
        await 판별(db, args.channel)
        await 이력(db, args.channel)
        if args.shake:
            await 흔들림(db, args.channel, args.shake)
        else:
            print("\n  (흔들림 측정은 --shake 40 으로. LLM 을 3번 부른다)")

    await engine.dispose()


asyncio.run(main())
