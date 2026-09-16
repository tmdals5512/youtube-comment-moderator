"""조치가 유튜브로 나가는 경로.

되돌리기 어려운 동작이라 고정해둔다. 특히 두 가지.

  1. 유튜브 호출이 실패해도 관리자 판단은 DB 에 남아야 한다.
     실패했다고 없던 일로 만들면, 관리자는 같은 댓글을 또 보게 된다.
  2. youtube_synced 는 '실제로 반영됐는가' 여야 한다.
     호출할 게 없었던 경우까지 True 로 두면 이력에서 구분이 안 된다.
"""

from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.deps import COOKIE
from app.db.models import (
    Action,
    Channel,
    Comment,
    RiskAssessment,
    Session,
    User,
    Workspace,
    WorkspaceMember,
)
from app.db.session import AsyncSessionLocal
from app.main import app
from app.services import youtube_actions as yt

pytestmark = pytest.mark.asyncio


async def _ok(*a, **k):
    return yt.ActionResult(True)

메일 = "yt@test.local"


async def _purge():
    async with AsyncSessionLocal() as db:
        u = (
            await db.execute(select(User).where(User.email == 메일))
        ).scalar_one_or_none()
        if not u:
            return
        ws = list(
            (
                await db.execute(
                    select(WorkspaceMember.workspace_id).where(
                        WorkspaceMember.user_id == u.id
                    )
                )
            ).scalars().all()
        ) or [-1]
        chans = list(
            (
                await db.execute(select(Channel.id).where(Channel.workspace_id.in_(ws)))
            ).scalars().all()
        ) or [-1]
        cmts = list(
            (
                await db.execute(select(Comment.id).where(Comment.channel_id.in_(chans)))
            ).scalars().all()
        ) or [-1]
        await db.execute(delete(Action).where(Action.comment_id.in_(cmts)))
        await db.execute(
            delete(RiskAssessment).where(RiskAssessment.comment_id.in_(cmts))
        )
        await db.execute(delete(Comment).where(Comment.id.in_(cmts)))
        await db.execute(delete(Channel).where(Channel.id.in_(chans)))
        await db.execute(delete(Session).where(Session.user_id == u.id))
        await db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id == u.id))
        await db.execute(delete(User).where(User.id == u.id))
        await db.execute(delete(Workspace).where(Workspace.id.in_(ws)))
        await db.commit()


@pytest_asyncio.fixture
async def 세상():
    """연동된 채널 하나와, 큐에 있는 댓글 하나."""
    await _purge()
    async with AsyncSessionLocal() as db:
        ws = Workspace(name="yt ws", type="personal")
        db.add(ws)
        await db.flush()
        u = User(email=메일, name="yt")
        db.add(u)
        await db.flush()
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role="owner"))

        ch = Channel(
            workspace_id=ws.id,
            youtube_channel_id="UC-yt",
            channel_title="연동된 채널",
            youtube_refresh_token="가짜-리프레시-토큰",
        )
        db.add(ch)
        await db.flush()

        c = Comment(
            channel_id=ch.id,
            youtube_comment_id="yt-cmt-1",
            content="숨길 댓글",
            status="queued",
        )
        db.add(c)
        await db.flush()
        db.add(
            RiskAssessment(
                comment_id=c.id, stage="llm", destination="queue_judge",
                risk_level="harmful", category="모욕",
            )
        )
        s = Session.new(u.id)
        db.add(s)
        await db.commit()
        ids = {"channel": ch.id, "comment": c.id, "token": s.token}

    yield ids
    await _purge()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _action(client, 세상, **body):
    return await client.post(
        f"/api/comments/{세상['comment']}/action",
        json=body,
        cookies={COOKIE: 세상["token"]},
    )


async def _actions(comment_id):
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(select(Action).where(Action.comment_id == comment_id))
        ).scalars().all()


class TestHide:
    async def test_숨김이_유튜브로_나간다(self, client, 세상, monkeypatch):
        불린것 = {}

        async def 가짜(refresh, ids, status):
            불린것.update(refresh=refresh, ids=ids, status=status)
            return yt.ActionResult(True)

        monkeypatch.setattr(yt, "set_moderation", 가짜)
        r = await _action(client, 세상, action="hide")

        assert r.status_code == 200
        assert r.json()["youtube_synced"] is True
        assert 불린것["ids"] == ["yt-cmt-1"]
        assert 불린것["status"] == yt.HIDE      # 'rejected' — 삭제가 아니라 비공개
        assert 불린것["refresh"] == "가짜-리프레시-토큰"

    async def test_유튜브가_실패해도_판단은_남는다(self, client, 세상, monkeypatch):
        async def 실패(*a, **k):
            return yt.ActionResult(False, "권한이 모자랍니다")

        monkeypatch.setattr(yt, "set_moderation", 실패)
        r = await _action(client, 세상, action="hide", note="관리자 메모")

        assert r.status_code == 200
        assert r.json()["youtube_synced"] is False
        assert "권한이 모자랍니다" in r.json()["note"]
        assert "관리자 메모" in r.json()["note"]   # 원래 메모도 지우지 않는다

        기록 = await _actions(세상["comment"])
        assert len(기록) == 1, "유튜브가 실패해도 이력은 남아야 한다"
        assert 기록[0].youtube_synced is False

    async def test_유튜브가_터져도_판단은_남는다(self, client, 세상, monkeypatch):
        async def 터짐(*a, **k):
            raise RuntimeError("네트워크 끊김")

        monkeypatch.setattr(yt, "set_moderation", 터짐)
        r = await _action(client, 세상, action="hide")

        assert r.status_code == 200
        assert r.json()["youtube_synced"] is False
        assert len(await _actions(세상["comment"])) == 1


class TestKeep:
    async def test_공개중인_것을_유지하면_유튜브를_안_부른다(
        self, client, 세상, monkeypatch
    ):
        """반영할 게 없다. 그렇다고 synced=True 로 두면 이력이 거짓이 된다."""
        불렸나 = {"yes": False}

        async def 가짜(*a, **k):
            불렸나["yes"] = True
            return yt.ActionResult(True)

        monkeypatch.setattr(yt, "set_moderation", 가짜)
        r = await _action(client, 세상, action="keep")

        assert r.status_code == 200
        assert 불렸나["yes"] is False, "공개 상태면 유튜브를 부를 이유가 없다"
        assert r.json()["youtube_synced"] is False

    async def test_숨겼던_것을_유지하면_공개로_되돌린다(
        self, client, 세상, monkeypatch
    ):
        async with AsyncSessionLocal() as db:
            c = await db.get(Comment, 세상["comment"])
            c.status = "hidden"
            await db.commit()

        불린것 = {}

        async def 가짜(refresh, ids, status):
            불린것["status"] = status
            return yt.ActionResult(True)

        monkeypatch.setattr(yt, "set_moderation", 가짜)
        r = await _action(client, 세상, action="keep")

        assert 불린것["status"] == yt.PUBLISH
        assert r.json()["youtube_synced"] is True

    async def test_복구하면_검토_큐로_돌아간다(self, client, 세상, monkeypatch):
        """숨긴 걸 되돌리면 '아직 판단 안 함' 으로 가야 한다.

        통과로 처리하면 그 댓글이 숨김 목록에서도 검토 큐에서도 빠져서,
        잘못 숨겼다가 되돌린 댓글을 다시 볼 방법이 없어진다.
        """
        async with AsyncSessionLocal() as db:
            c = await db.get(Comment, 세상["comment"])
            c.status = "hidden"
            c.reviewed_at = datetime(2020, 1, 1)
            await db.commit()

        monkeypatch.setattr(
            yt, "set_moderation",
            lambda *a, **k: _ok(),
        )
        r = await _action(client, 세상, action="keep")
        assert r.json()["status"] == "queued"

        async with AsyncSessionLocal() as db:
            c = await db.get(Comment, 세상["comment"])
            # 검토 큐는 status 와 reviewed_at 을 함께 본다. 하나만 되돌리면
            # 어느 목록에도 안 뜬다 — 실제로 그렇게 사라진 적이 있다.
            assert c.status == "queued"
            assert c.reviewed_at is None

    async def test_검토_큐에서_유지하면_통과로_남는다(
        self, client, 세상, monkeypatch
    ):
        """같은 keep 이라도 공개중인 걸 유지한 것은 판단이 끝난 것이다."""
        r = await _action(client, 세상, action="keep")
        assert r.json()["status"] == "passed"

        async with AsyncSessionLocal() as db:
            c = await db.get(Comment, 세상["comment"])
            assert c.status == "passed"
            assert c.reviewed_at is not None


class TestDuplicate:
    async def test_hiding_hidden_comment_is_409(self, client, 세상, monkeypatch):
        """이미 가려진 걸 또 가리면 이력만 쌓인다. 화면은 버튼을 감췼지만
        API 는 열려 있었다."""
        monkeypatch.setattr(yt, "set_moderation", lambda *a, **k: _ok())
        first = await _action(client, 세상, action="hide")
        assert first.status_code == 200

        second = await _action(client, 세상, action="hide")
        assert second.status_code == 409
        assert len(await _actions(세상["comment"])) == 1


class TestNotConnected:
    async def test_미연동_채널은_안내가_남는다(self, client, 세상, monkeypatch):
        async with AsyncSessionLocal() as db:
            ch = await db.get(Channel, 세상["channel"])
            ch.youtube_refresh_token = None
            await db.commit()

        r = await _action(client, 세상, action="hide")

        assert r.status_code == 200
        assert r.json()["youtube_synced"] is False
        assert "연동" in r.json()["note"]
        # 유튜브에 못 보냈어도 우리 DB 에서는 숨김 처리가 된다
        assert r.json()["status"] == "hidden"
