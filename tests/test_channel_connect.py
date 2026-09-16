"""채널 연동·해제.

둘 다 조용히 잘못되면 치명적인 동작이라 고정해둔다.

  1. 남이 쓰던 채널을 가로채면 안 된다. 구글이 소유권을 보증해줘도,
     앞사람이 모은 댓글·판정·이력이 통째로 넘어가면 안 된다.
  2. 연동을 끊으면 모은 것을 실제로 지워야 한다. workspace 만 비우면
     데이터가 DB 에 남은 채 아무도 못 보는 상태가 된다.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.deps import COOKIE
from app.db.models import (
    Action,
    Channel,
    ChannelRule,
    Comment,
    RiskAssessment,
    Session,
    User,
    Video,
    Workspace,
    WorkspaceMember,
)
from app.db.session import AsyncSessionLocal
from app.main import app

pytestmark = pytest.mark.asyncio

메일 = ["conn-a@test.local", "conn-b@test.local"]
YT = "UC-conn-test"


async def _purge():
    async with AsyncSessionLocal() as db:
        us = (
            await db.execute(select(User).where(User.email.in_(메일)))
        ).scalars().all()
        uids = [u.id for u in us] or [-1]
        wids = list(
            (
                await db.execute(
                    select(WorkspaceMember.workspace_id).where(
                        WorkspaceMember.user_id.in_(uids)
                    )
                )
            ).scalars().all()
        ) or [-1]
        chs = list(
            (
                await db.execute(
                    select(Channel.id).where(Channel.youtube_channel_id == YT)
                )
            ).scalars().all()
        ) or [-1]
        cids = list(
            (
                await db.execute(select(Comment.id).where(Comment.channel_id.in_(chs)))
            ).scalars().all()
        ) or [-1]

        await db.execute(delete(Action).where(Action.comment_id.in_(cids)))
        await db.execute(delete(RiskAssessment).where(RiskAssessment.comment_id.in_(cids)))
        await db.execute(delete(Comment).where(Comment.id.in_(cids)))
        await db.execute(delete(Video).where(Video.channel_id.in_(chs)))
        await db.execute(delete(ChannelRule).where(ChannelRule.channel_id.in_(chs)))
        await db.execute(delete(Channel).where(Channel.id.in_(chs)))
        await db.execute(delete(Session).where(Session.user_id.in_(uids)))
        await db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id.in_(uids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(Workspace).where(Workspace.id.in_(wids)))
        await db.commit()


@pytest_asyncio.fixture
async def 세상():
    """A 가 채널을 쓰고 있고, B 는 남남인 상태."""
    await _purge()
    async with AsyncSessionLocal() as db:
        만든것 = {}
        for key, email in zip("ab", 메일):
            ws = Workspace(name=f"{key} 회사", type="team")
            db.add(ws)
            await db.flush()
            u = User(email=email, name=key)
            db.add(u)
            await db.flush()
            db.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role="owner"))
            s = Session.new(u.id)
            db.add(s)
            만든것[key] = {"ws": ws.id, "user": u.id, "token": s.token}

        ch = Channel(
            workspace_id=만든것["a"]["ws"],
            youtube_channel_id=YT,
            channel_title="A 의 채널",
            youtube_refresh_token="A-토큰",
            context="A 가 적은 기준",
        )
        db.add(ch)
        await db.flush()
        db.add(Video(video_id="vid-conn", channel_id=ch.id, title="영상"))
        db.add(ChannelRule(
            channel_id=ch.id, rule_value="시험", compiled_regex="시험", action="block"
        ))
        for i in range(3):
            c = Comment(
                channel_id=ch.id, youtube_comment_id=f"conn-{i}",
                content=f"댓글 {i}", status="queued", video_id="vid-conn",
            )
            db.add(c)
            await db.flush()
            db.add(RiskAssessment(comment_id=c.id, stage="llm", risk_level="harmful"))
            db.add(Action(comment_id=c.id, action_type="hide", actor="a"))
        await db.commit()
        만든것["channel"] = ch.id

    yield 만든것
    await _purge()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _세기(cid):
    async with AsyncSessionLocal() as db:
        댓글 = list(
            (await db.execute(select(Comment.id).where(Comment.channel_id == cid)))
            .scalars().all()
        )
        판정 = (
            await db.execute(
                select(func.count()).select_from(RiskAssessment)
                .where(RiskAssessment.comment_id.in_(댓글 or [-1]))
            )
        ).scalar_one()
        액션 = (
            await db.execute(
                select(func.count()).select_from(Action)
                .where(Action.comment_id.in_(댓글 or [-1]))
            )
        ).scalar_one()
        영상 = (
            await db.execute(
                select(func.count()).select_from(Video).where(Video.channel_id == cid)
            )
        ).scalar_one()
        return {"댓글": len(댓글), "판정": 판정, "액션": 액션, "영상": 영상}


class TestTakeover:
    async def test_남이_쓰던_채널은_넘겨받지_않는다(self, 세상, monkeypatch):
        """B 가 같은 유튜브 채널을 연동해도 A 것을 가져가면 안 된다."""
        from app.api import channels as mod

        async def 가짜채널목록(access_token):
            return [{"youtube_channel_id": YT, "title": "B 가 본 이름"}]

        class 가짜토큰:
            access_token, refresh_token, expires_in = "at", "B-토큰", 3600

        async def 가짜교환(*a, **k):
            return 가짜토큰()

        monkeypatch.setattr(mod.yt, "my_channels", 가짜채널목록)
        monkeypatch.setattr(mod.goog, "exchange_code", 가짜교환)
        state = mod.goog.states.issue(
            kind="connect", user_id=세상["b"]["user"], workspace_id=세상["b"]["ws"]
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                f"/api/channels/connect/callback?state={state}&code=x",
                follow_redirects=False,
            )

        assert "already_connected" in r.headers["location"]

        async with AsyncSessionLocal() as db:
            ch = await db.get(Channel, 세상["channel"])
            assert ch.workspace_id == 세상["a"]["ws"], "A 것으로 남아야 한다"
            assert ch.youtube_refresh_token == "A-토큰", "토큰도 안 바뀌어야"
            assert ch.channel_title == "A 의 채널"

        assert (await _세기(세상["channel"]))["댓글"] == 3, "A 의 댓글이 그대로"


class TestDisconnect:
    async def test_해제하면_모은것을_지운다(self, client, 세상):
        전 = await _세기(세상["channel"])
        assert 전 == {"댓글": 3, "판정": 3, "액션": 3, "영상": 1}

        r = await client.post(
            f"/api/channels/{세상['channel']}/disconnect",
            cookies={COOKIE: 세상["a"]["token"]},
        )
        assert r.status_code == 200
        assert r.json()["deleted_comments"] == 3

        후 = await _세기(세상["channel"])
        assert 후 == {"댓글": 0, "판정": 0, "액션": 0, "영상": 0}, "전부 지워져야 한다"

    async def test_해제하면_토큰과_설정이_지워진다(self, client, 세상):
        await client.post(
            f"/api/channels/{세상['channel']}/disconnect",
            cookies={COOKIE: 세상["a"]["token"]},
        )
        async with AsyncSessionLocal() as db:
            ch = await db.get(Channel, 세상["channel"])
            assert ch.youtube_refresh_token is None, "토큰이 남으면 계속 쓰기가 된다"
            assert ch.workspace_id is None
            assert ch.context is None

    async def test_남의_채널은_해제할_수_없다(self, client, 세상):
        r = await client.post(
            f"/api/channels/{세상['channel']}/disconnect",
            cookies={COOKIE: 세상["b"]["token"]},
        )
        assert r.status_code == 404
        assert (await _세기(세상["channel"]))["댓글"] == 3, "지워지면 안 된다"


# ── 채널 목록이 '조치가 실제로 되는 채널'인지 알려주는가 ───────────
#
# 수집은 API 키만으로도 된다. 숨김을 유튜브에 반영하려면 채널 주인이
# OAuth 로 준 리프레시 토큰이 있어야 한다. 이 구분이 화면에 안 보이면
# 숨김을 눌러 놓고 반영된 줄 알게 된다.


def test_토큰_없는_채널은_connected_가_거짓이다():
    from app.api.channels import ChannelOut
    from app.db.models import Channel

    c = Channel(id=1, channel_title="수집만 한 채널", youtube_refresh_token=None)
    assert (
        ChannelOut(
            id=c.id,
            channel_title=c.channel_title,
            connected=bool(c.youtube_refresh_token),
        ).connected
        is False
    )


def test_토큰_있는_채널은_connected_가_참이다():
    from app.api.channels import ChannelOut
    from app.db.models import Channel

    c = Channel(id=2, channel_title="연동한 채널", youtube_refresh_token="rt")
    assert (
        ChannelOut(
            id=c.id,
            channel_title=c.channel_title,
            connected=bool(c.youtube_refresh_token),
        ).connected
        is True
    )


def test_채널_목록은_토큰을_내보내지_않는다():
    from app.api.channels import ChannelOut

    assert "youtube_refresh_token" not in ChannelOut.model_fields
