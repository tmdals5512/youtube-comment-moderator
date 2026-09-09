"""임베딩이 없는 댓글에 벡터를 채운다 (F_R_114 유사 사례 검색용).

이미 채워진 건 건너뛰므로 여러 번 돌려도 안전하고, 새 댓글을 수집한 뒤
다시 돌리면 새 것만 처리한다.

    python -m scripts.embed_comments                채널 전체
    python -m scripts.embed_comments --channel 2    한 채널만
    python -m scripts.embed_comments --dry          비용만 확인
"""

import argparse
import asyncio
import sys

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, select

from app.db.models import EMBEDDING_DIM, Comment
from app.db.session import AsyncSessionLocal, engine
from app.services.embed import MODEL, Embedder

# 한 번 실행에서 처리할 상한. 사고로 수만 건을 돌리는 걸 막는다.
MAX_PER_RUN = 5000


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", type=int, default=None)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        q = select(Comment.id, Comment.content).where(Comment.embedding.is_(None))
        if args.channel:
            q = q.where(Comment.channel_id == args.channel)
        rows = (await db.execute(q.order_by(Comment.id).limit(MAX_PER_RUN))).all()

        if not rows:
            print("임베딩이 없는 댓글이 없다. 할 일 없음.")
            await engine.dispose()
            return

        chars = sum(len(c) for _, c in rows)
        print(f"대상 {len(rows)}건 / 약 {chars:,}자 / 모델 {MODEL}")
        # 한글은 대략 글자당 1토큰을 조금 넘는다. 어차피 매우 싸서 어림으로 충분.
        print(f"예상 비용 약 ${chars * 1.2 / 1_000_000 * 0.02:.4f}")

        if args.dry:
            await engine.dispose()
            return

        embedder = Embedder()
        print("생성 중...")
        vectors = await embedder.embed([c for _, c in rows])

        # id 마다 값이 달라 executemany 로 한 번에 보낸다.
        # ORM(update(Comment)) 이 아니라 Table 을 쓰는 이유: ORM 의 대량 UPDATE
        # 경로는 행마다 PK 를 요구해서 bindparam 조합을 받지 않는다. Core 는 그냥 돈다.
        table = Comment.__table__
        await db.execute(
            table.update()
            .where(table.c.id == bindparam("cid"))
            .values(embedding=bindparam("vec", type_=Vector(EMBEDDING_DIM))),
            [{"cid": cid, "vec": v} for (cid, _), v in zip(rows, vectors)],
        )
        await db.commit()

    st = embedder.stats
    print(f"\n완료 {st.texts}건 / 호출 {st.calls}회 / {st.tokens:,} 토큰")
    print(f"비용 ${st.cost_usd:.4f} (약 {st.cost_usd * 1400:.1f}원)")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
