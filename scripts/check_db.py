"""DB 접속 점검 스크립트.

출력은 Windows 콘솔(cp949) 깨짐을 피하려고 ASCII로만 찍는다.

서버를 띄우지 않고 접속만 빠르게 확인할 때 사용한다.

    .venv/Scripts/python.exe -m scripts.check_db
"""

import asyncio

from sqlalchemy import text

from app.db.session import engine


async def main() -> None:
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT version()"))).scalar_one()
        vector = (
            await conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar_one_or_none()
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                )
            )
            .scalars()
            .all()
        )

    print(f"[OK] {version}")
    print(f"[OK] pgvector: {vector or 'NOT INSTALLED'}")
    print(f"[OK] public schema: {len(tables)} tables")
    for name in tables:
        print(f"  - {name}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
