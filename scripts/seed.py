"""데모용 채널 2개와 규칙을 넣는다.

같은 단어가 채널마다 다르게 처리되는 걸 보여주는 게 목적.

    python -m scripts.seed
"""

import asyncio
from datetime import datetime

from sqlalchemy import delete, select

from app.db.models import Channel, ChannelRule
from app.db.session import AsyncSessionLocal, engine
from app.services.pattern import expand

CHANNELS = [
    ("게임채널", "UC_demo_game", [
        ("시발", "block", True),
    ]),
    ("아이돌채널", "UC_demo_idol", [
        ("시발", "block", True),
        ("발컨", "block", False),   # 게임채널엔 등록 안 해서, 같은 댓글이 다르게 처리된다
    ]),
]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        for title, yt_id, rules in CHANNELS:
            channel = (
                await db.execute(select(Channel).where(Channel.channel_title == title))
            ).scalar_one_or_none()

            if channel is None:
                channel = Channel(
                    channel_title=title,
                    youtube_channel_id=yt_id,
                    ai_consent_agreed=True,
                    connected_at=datetime.now(),
                )
                db.add(channel)
                await db.flush()

            await db.execute(
                delete(ChannelRule).where(ChannelRule.channel_id == channel.id)
            )
            for value, action, variants in rules:
                db.add(
                    ChannelRule(
                        channel_id=channel.id,
                        rule_type="keyword",
                        rule_value=value,
                        compiled_regex=expand(value, variants),
                        action=action,
                        expand_variants=variants,
                        enabled=True,
                    )
                )
            print(f"[OK] channel_id={channel.id}  {title}  rules={len(rules)}")

        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
