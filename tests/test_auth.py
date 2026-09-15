"""인증·데이터 격리.

이 파일이 지키려는 것은 하나다 — **남의 채널이 보이면 안 된다.**
채널 격리는 YouTube API 정책이자 고객사 간 신뢰의 문제라, 엔드포인트가
하나라도 검사를 빠뜨리면 그 경로로 샌다. 그래서 '채널을 다루는 모든
엔드포인트'를 목록으로 만들어 전부 훑는다.
"""

from datetime import datetime, timedelta

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

pytestmark = pytest.mark.asyncio


# 채널을 다루는 엔드포인트 전부. 새 엔드포인트를 만들면 여기에 추가한다.
# (method, 경로틀) — {cid} 에 채널 id 가 들어간다.
CHANNEL_ENDPOINTS = [
    ("GET", "/api/channels/{cid}/queue"),
    ("GET", "/api/channels/{cid}/hidden"),
    ("GET", "/api/channels/{cid}/stats"),
    ("GET", "/api/channels/{cid}/history"),
    ("GET", "/api/channels/{cid}/rules"),
    ("GET", "/api/channels/{cid}/auto-hide"),
]


TEST_EMAILS = ["a@test.local", "b@test.local"]


async def _purge():
    """이 파일이 만드는 행을 모두 지운다.

    시작할 때도 부른다. 앞 테스트가 중간에 죽어 뒷정리를 못 했을 때,
    남은 행의 unique 제약 때문에 그 뒤 테스트가 줄줄이 무너지기 때문이다.
    """
    async with AsyncSessionLocal() as db:
        users = (
            await db.execute(select(User).where(User.email.in_(TEST_EMAILS)))
        ).scalars().all()
        if not users:
            return
        uids = [u.id for u in users]
        wids = list(
            (
                await db.execute(
                    select(WorkspaceMember.workspace_id).where(
                        WorkspaceMember.user_id.in_(uids)
                    )
                )
            ).scalars().all()
        )
        chans = list(
            (
                await db.execute(
                    select(Channel.id).where(Channel.workspace_id.in_(wids or [-1]))
                )
            ).scalars().all()
        ) or [-1]
        cmts = list(
            (
                await db.execute(select(Comment.id).where(Comment.channel_id.in_(chans)))
            ).scalars().all()
        ) or [-1]

        await db.execute(delete(Action).where(Action.comment_id.in_(cmts)))
        await db.execute(delete(RiskAssessment).where(RiskAssessment.comment_id.in_(cmts)))
        await db.execute(delete(Comment).where(Comment.id.in_(cmts)))
        await db.execute(delete(Channel).where(Channel.id.in_(chans)))
        await db.execute(delete(Session).where(Session.user_id.in_(uids)))
        await db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id.in_(uids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(Workspace).where(Workspace.id.in_(wids or [-1])))
        await db.commit()


@pytest_asyncio.fixture
async def world():
    """서로 모르는 두 사람과, 각자의 채널·댓글을 만든다."""
    await _purge()
    async with AsyncSessionLocal() as db:
        made = {}
        for key, email in [("a", "a@test.local"), ("b", "b@test.local")]:
            ws = Workspace(name=f"{key} ws", type="personal")
            db.add(ws)
            await db.flush()

            user = User(email=email, name=key, google_id=f"sub-{key}")
            db.add(user)
            await db.flush()

            db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))

            ch = Channel(
                workspace_id=ws.id,
                youtube_channel_id=f"UC-{key}",
                channel_title=f"{key} 채널",
            )
            db.add(ch)
            await db.flush()

            c = Comment(
                channel_id=ch.id,
                youtube_comment_id=f"cmt-{key}",
                content=f"{key} 의 비밀 댓글",
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

            s = Session.new(user.id)
            db.add(s)
            made[key] = {"user": user, "ws": ws, "channel": ch,
                         "comment": c, "token": s.token}

        await db.commit()
        ids = {
            k: {"channel": v["channel"].id, "comment": v["comment"].id,
                "token": v["token"], "user": v["user"].id, "ws": v["ws"].id}
            for k, v in made.items()
        }

    yield ids

    await _purge()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def as_(token):
    return {COOKIE: token}


class TestLoginRequired:
    """로그인 없이는 아무것도 안 된다."""

    @pytest.mark.parametrize("method,path", CHANNEL_ENDPOINTS)
    async def test_anonymous_is_rejected(self, client, world, method, path):
        r = await client.request(method, path.format(cid=world["a"]["channel"]))
        assert r.status_code == 401, f"{method} {path} 가 익명 요청을 통과시켰다"

    async def test_channel_list_requires_login(self, client):
        assert (await client.get("/api/channels")).status_code == 401

    async def test_action_requires_login(self, client, world):
        r = await client.post(
            f"/api/comments/{world['a']['comment']}/action", json={"action": "keep"}
        )
        assert r.status_code == 401


class TestIsolation:
    """핵심 — b 는 a 의 것을 어떤 경로로도 볼 수 없다."""

    @pytest.mark.parametrize("method,path", CHANNEL_ENDPOINTS)
    async def test_other_channel_is_invisible(self, client, world, method, path):
        r = await client.request(
            method,
            path.format(cid=world["a"]["channel"]),
            cookies=as_(world["b"]["token"]),
        )
        # 403 이 아니라 404 여야 한다. 403 은 '존재한다'는 걸 알려준다.
        assert r.status_code == 404, f"{method} {path} 로 남의 채널이 샜다"

    async def test_own_channel_is_visible(self, client, world):
        r = await client.get(
            f"/api/channels/{world['a']['channel']}/queue",
            cookies=as_(world["a"]["token"]),
        )
        assert r.status_code == 200
        assert any("a 의 비밀" in x["content"] for x in r.json())

    async def test_channel_list_shows_only_mine(self, client, world):
        r = await client.get("/api/channels", cookies=as_(world["b"]["token"]))
        assert r.status_code == 200
        titles = [c["channel_title"] for c in r.json()]
        assert "a 채널" not in titles
        assert "b 채널" in titles

    async def test_cannot_act_on_others_comment(self, client, world):
        r = await client.post(
            f"/api/comments/{world['a']['comment']}/action",
            json={"action": "hide"},
            cookies=as_(world["b"]["token"]),
        )
        assert r.status_code == 404

    async def test_cannot_see_others_similar(self, client, world):
        r = await client.get(
            f"/api/comments/{world['a']['comment']}/similar",
            cookies=as_(world["b"]["token"]),
        )
        assert r.status_code == 404

    async def test_cannot_change_others_auto_hide(self, client, world):
        r = await client.put(
            f"/api/channels/{world['a']['channel']}/auto-hide",
            json={"categories": ["스팸"]},
            cookies=as_(world["b"]["token"]),
        )
        assert r.status_code == 404

    async def test_cannot_add_rule_to_others_channel(self, client, world):
        r = await client.post(
            f"/api/channels/{world['a']['channel']}/rules",
            json={"rule_value": "침입", "action": "block"},
            cookies=as_(world["b"]["token"]),
        )
        assert r.status_code == 404


class TestSession:
    async def test_me_returns_workspace(self, client, world):
        r = await client.get("/api/auth/me", cookies=as_(world["a"]["token"]))
        assert r.status_code == 200
        assert r.json()["email"] == "a@test.local"
        assert r.json()["role"] == "owner"

    async def test_garbage_token_is_rejected(self, client, world):
        r = await client.get("/api/auth/me", cookies=as_("not-a-real-token"))
        assert r.status_code == 401

    async def test_expired_session_is_rejected(self, client, world):
        async with AsyncSessionLocal() as db:
            s = (
                await db.execute(
                    select(Session).where(Session.token == world["a"]["token"])
                )
            ).scalar_one()
            s.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()

        r = await client.get("/api/auth/me", cookies=as_(world["a"]["token"]))
        assert r.status_code == 401

    async def test_logout_kills_session(self, client, world):
        assert (
            await client.post("/api/auth/logout", cookies=as_(world["a"]["token"]))
        ).status_code == 204
        r = await client.get("/api/auth/me", cookies=as_(world["a"]["token"]))
        assert r.status_code == 401


class TestActorIsServerSide:
    async def test_actor_comes_from_session_not_body(self, client, world):
        """조치자를 요청 본문으로 위조할 수 없어야 한다."""
        r = await client.post(
            f"/api/comments/{world['a']['comment']}/action",
            json={"action": "keep", "actor": "남의이름@test.local", "note": "메모"},
            cookies=as_(world["a"]["token"]),
        )
        assert r.status_code == 200

        hist = await client.get(
            f"/api/channels/{world['a']['channel']}/history",
            cookies=as_(world["a"]["token"]),
        )
        actors = [h["actor"] for h in hist.json()]
        assert "a@test.local" in actors
        assert "남의이름@test.local" not in actors
