"""주인 없는 채널을 내 워크스페이스에 붙인다.

인증을 붙이기 전에 스크립트로 만든 채널들은 workspace_id 가 비어 있다.
그대로 두면 로그인해도 화면에 아무것도 안 나온다 — 권한 검사가 '내
워크스페이스의 채널'만 통과시키기 때문이다.

    python -m scripts.claim_channels                      상태만 본다
    python -m scripts.claim_channels --email a@b.com      그 사람에게 붙인다
    python -m scripts.claim_channels --email a@b.com --channel 4

개발 편의용이다. 실서비스에서는 OAuth 연동으로만 채널이 생긴다.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db.models import Channel, User, Workspace, WorkspaceMember
from app.db.session import AsyncSessionLocal, engine


async def main() -> None:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser()
    ap.add_argument("--email", help="채널을 넘겨받을 계정 (먼저 한 번 로그인해야 생긴다)")
    ap.add_argument("--channel", type=int, help="특정 채널만. 생략하면 주인 없는 것 전부")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        channels = (await db.execute(select(Channel).order_by(Channel.id))).scalars().all()
        users = (await db.execute(select(User).order_by(User.id))).scalars().all()

        print("[채널]")
        for c in channels:
            owner = f"workspace {c.workspace_id}" if c.workspace_id else "주인 없음"
            print(f"  {c.id:3} {c.channel_title or '(제목 없음)':24} {owner}")

        print("\n[계정]")
        if not users:
            print("  없음 — 브라우저에서 먼저 구글 로그인을 한 번 해야 계정이 생긴다.")
        for u in users:
            ws = (
                await db.execute(
                    select(Workspace)
                    .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                    .where(WorkspaceMember.user_id == u.id)
                )
            ).scalars().all()
            print(f"  {u.id:3} {u.email:30} 워크스페이스 {[w.id for w in ws] or '없음'}")

        if not args.email:
            print("\n붙이려면: python -m scripts.claim_channels --email <로그인한 이메일>")
            await engine.dispose()
            return

        user = (
            await db.execute(select(User).where(User.email == args.email))
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"\n[FAIL] {args.email} 계정이 없다. 먼저 로그인할 것.")

        ws_id = (
            await db.execute(
                select(WorkspaceMember.workspace_id)
                .where(WorkspaceMember.user_id == user.id)
                .order_by(WorkspaceMember.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if ws_id is None:
            raise SystemExit("\n[FAIL] 워크스페이스가 없다. 로그인하면 자동으로 생긴다.")

        targets = [
            c
            for c in channels
            if (args.channel is None and c.workspace_id is None)
            or (args.channel is not None and c.id == args.channel)
        ]
        if not targets:
            print("\n붙일 채널이 없다.")
            await engine.dispose()
            return

        for c in targets:
            c.workspace_id = ws_id
            c.connected_by_user_id = user.id
        await db.commit()

        print(f"\n[OK] 채널 {[c.id for c in targets]} -> 워크스페이스 {ws_id} ({args.email})")

    await engine.dispose()


asyncio.run(main())
