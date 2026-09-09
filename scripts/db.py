"""DB를 터미널에서 바로 들여다본다. GUI 설치 없이 쓰는 용도.

    python -m scripts.db                     테이블 목록과 행 수
    python -m scripts.db comments            comments 앞 20행
    python -m scripts.db comments 5          앞 5행
    python -m scripts.db "SELECT ..."        직접 쿼리

읽기 전용이다. SELECT / WITH 로 시작하지 않는 쿼리는 거부한다 —
실수로 DELETE 를 치는 사고를 막으려는 것.
"""

import asyncio
import sys

from sqlalchemy import text as sq

from app.db.session import engine

SUMMARY = """
SELECT c.relname AS 테이블, c.reltuples::bigint AS 대략_행수
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname
"""


def show(cols, rows, width: int = 34) -> None:
    """터미널 폭에 맞춰 자른다. 한글이 많아 넉넉히 못 준다."""
    if not rows:
        print("  (행 없음)")
        return
    w = []
    for i, c in enumerate(cols):
        longest = max([len(str(c))] + [len(str(r[i])) for r in rows])
        w.append(min(longest, width))
    print("  " + " | ".join(str(c)[: w[i]].ljust(w[i]) for i, c in enumerate(cols)))
    print("  " + "-+-".join("-" * x for x in w))
    for r in rows:
        cells = []
        for i, v in enumerate(r):
            s = " ".join(str(v).split()) if v is not None else ""
            cells.append((s[: w[i] - 1] + "…" if len(s) > w[i] else s).ljust(w[i]))
        print("  " + " | ".join(cells))


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    async with engine.connect() as conn:
        if arg is None:
            r = await conn.execute(sq(SUMMARY))
            rows = r.fetchall()
            print("public 스키마 테이블\n")
            show(list(r.keys()), rows)
            print("\n  실제 행 수 (비어 보이면 통계가 오래된 것)")
            for (name, _) in rows:
                n = (await conn.execute(sq(f'SELECT count(*) FROM "{name}"'))).scalar_one()
                print(f"    {name:<22}{n:>8}")
            print("\n  다음: python -m scripts.db comments")
            await engine.dispose()
            return

        if " " in arg.strip():           # 직접 쿼리
            q = arg.strip()
            if not q.lower().lstrip("( ").startswith(("select", "with")):
                raise SystemExit("[거부] SELECT / WITH 만 허용한다 (읽기 전용)")
        else:                             # 테이블 이름
            q = f'SELECT * FROM "{arg}" ORDER BY 1 DESC LIMIT {limit}'

        r = await conn.execute(sq(q))
        rows = r.fetchall()
        print(f"{len(rows)}행\n")
        show(list(r.keys()), rows)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
