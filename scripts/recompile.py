"""등록된 모든 규칙의 정규식을 현재 expand() 기준으로 다시 만든다.

변형 생성 규칙을 고치면 이미 저장된 compiled_regex는 옛날 것이라 반영이 안 된다.
그때 한 번 돌려주면 된다.

    python -m scripts.recompile
"""

import asyncio

from sqlalchemy import select

from app.db.models import ChannelRule
from app.db.session import AsyncSessionLocal, engine
from app.services.pattern import expand


async def main() -> None:
    async with AsyncSessionLocal() as db:
        rules = (await db.execute(select(ChannelRule))).scalars().all()
        changed = 0
        for r in rules:
            new = expand(r.rule_value, r.expand_variants)
            if new != r.compiled_regex:
                print(f"  ch={r.channel_id} {r.rule_value}")
                print(f"    before: {r.compiled_regex}")
                print(f"    after : {new}")
                r.compiled_regex = new
                changed += 1
        await db.commit()
        print(f"\n{changed}/{len(rules)}건 갱신")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
