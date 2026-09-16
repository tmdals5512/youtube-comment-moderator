"""서버 안에서 도는 감시.

유튜브와 LLM 은 가짜로 바꿔 끼운다. 여기서 확인하는 건 세 가지 —
누구를 보는가, 새 것만 판별하는가, 하루 상한을 지키는가. 셋 중 하나가
틀리면 남의 채널을 보거나, 같은 댓글에 돈을 두 번 쓰거나, 폭주 영상 하나에
카드가 뚫린다.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.models import (
    Action,
    Channel,
    Comment,
    RiskAssessment,
    Video,
    Workspace,
)
from app.db.session import AsyncSessionLocal
from app.services import watcher
from app.services.collector import CollectedComment
from app.services.llm import LlmStats, LlmVerdict

pytestmark = pytest.mark.asyncio

접두 = "UC-watch-"


async def _purge():
    async with AsyncSessionLocal() as db:
        chans = list(
            (
                await db.execute(
                    select(Channel.id).where(Channel.youtube_channel_id.like(f"{접두}%"))
                )
            ).scalars().all()
        ) or [-1]
        cmts = list(
            (
                await db.execute(select(Comment.id).where(Comment.channel_id.in_(chans)))
            ).scalars().all()
        ) or [-1]
        wss = list(
            (
                await db.execute(select(Workspace.id).where(Workspace.name == "watch ws"))
            ).scalars().all()
        ) or [-1]
        await db.execute(delete(Action).where(Action.comment_id.in_(cmts)))
        await db.execute(delete(RiskAssessment).where(RiskAssessment.comment_id.in_(cmts)))
        await db.execute(delete(Comment).where(Comment.id.in_(cmts)))
        await db.execute(delete(Video).where(Video.channel_id.in_(chans)))
        await db.execute(delete(Channel).where(Channel.id.in_(chans)))
        await db.execute(delete(Workspace).where(Workspace.id.in_(wss)))
        await db.commit()


@pytest_asyncio.fixture
async def 세상():
    """채널 셋: 다 된 것 / 연동만 / 동의만. 첫 번째만 감시 대상이어야 한다."""
    await _purge()
    async with AsyncSessionLocal() as db:
        ws = Workspace(name="watch ws", type="personal")
        db.add(ws)
        await db.flush()
        둘다 = Channel(workspace_id=ws.id, youtube_channel_id=f"{접두}ok",
                     channel_title="둘 다", youtube_refresh_token="rt",
                     ai_consent_agreed=True)
        연동만 = Channel(workspace_id=ws.id, youtube_channel_id=f"{접두}tok",
                      channel_title="연동만", youtube_refresh_token="rt",
                      ai_consent_agreed=None)
        동의만 = Channel(workspace_id=ws.id, youtube_channel_id=f"{접두}yes",
                      channel_title="동의만", youtube_refresh_token=None,
                      ai_consent_agreed=True)
        db.add_all([둘다, 연동만, 동의만])
        await db.commit()
        ids = {"둘다": 둘다.id, "연동만": 연동만.id, "동의만": 동의만.id}
    # 하루 상한 카운터도 매 테스트 깨끗하게.
    watcher.상태.update(today=None, llm_calls_today=0, last_run_at=None, last_result=None)
    yield ids
    await _purge()


def _댓글(i: int, vid="vid-1") -> CollectedComment:
    return CollectedComment(
        youtube_comment_id=f"{접두}c{i}", video_id=vid, content=f"댓글 {i}",
        author_name="a", author_channel_id=None,
        published_at=datetime.now(UTC).replace(tzinfo=None), content_updated_at=None,
        like_count=0, total_reply_count=0, parent_comment_id=None,
    )


class 가짜유튜브:
    """수집기 흉내. 어떤 채널을 물어봤는지 기록한다."""

    def __init__(self, key, 댓글수=2):
        self.물어본 = []
        self.댓글수 = 댓글수

        class S:
            quota_units = 7
        self.stats = S()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def resolve_channel(self, raw):
        self.물어본.append(raw)
        return {"id": raw}

    async def channel_videos(self, ch, limit):
        return [{"video_id": "vid-1", "title": "영상", "published_at": "2026-01-01T00:00:00Z"}]

    async def collect(self, video_id, max_comments=None):
        return [_댓글(i, video_id) for i in range(self.댓글수)]


class 가짜판별:
    """LLM 흉내. 몇 번 불렸는지 센다."""

    만든수 = 0

    def __init__(self, concurrency=8, max_calls=None, channel_context=""):
        가짜판별.만든수 += 1
        self.stats = LlmStats()
        self.prompt_version = "test"

    async def judge(self, text, parent_text=None, video=None):
        self.stats.calls += 1
        return LlmVerdict("pass", label="safe", category="정상", confidence=0.9, reason="테스트")


@pytest.fixture
def 가짜들(monkeypatch):
    monkeypatch.setattr(watcher, "YouTubeCollector", 가짜유튜브)
    monkeypatch.setattr(watcher, "LlmJudge", 가짜판별)

    # 실제 DB 에 연동·동의된 채널이 있으면 그것까지 대상에 잡힌다. 그러면
    # 가짜 수집기가 실채널에 가짜 댓글을 넣는다 — 실제로 한 번 그랬다.
    # 이 테스트가 만든 채널만 보게 한다.
    원래 = watcher.대상_채널

    async def 우리것만(db):
        return [c for c in await 원래(db) if c.youtube_channel_id.startswith(접두)]
    monkeypatch.setattr(watcher, "대상_채널", 우리것만)

    async def 임베딩없음():
        return 0
    monkeypatch.setattr(watcher, "_임베딩", 임베딩없음)

    cfg = get_settings()
    monkeypatch.setattr(cfg, "youtube_api_key", "가짜")
    monkeypatch.setattr(cfg, "llm_daily_cap", 3000)
    가짜판별.만든수 = 0


class TestTargets:
    async def test_연동과_동의가_둘_다_있어야_본다(self, 세상):
        async with AsyncSessionLocal() as db:
            ids = {c.id for c in await watcher.대상_채널(db)}
        assert 세상["둘다"] in ids
        assert 세상["연동만"] not in ids
        assert 세상["동의만"] not in ids


class TestOnce:
    async def test_새_댓글을_받아_판별해_큐에_넣는다(self, 세상, 가짜들):
        r = await watcher.watch_once()
        assert r["channels"] == 1
        assert r["collected"] == 2
        assert r["judged"] == 2
        assert r["errors"] == []

        async with AsyncSessionLocal() as db:
            상태들 = (
                await db.execute(
                    select(Comment.status).where(Comment.channel_id == 세상["둘다"])
                )
            ).scalars().all()
        # pending 으로 남은 게 없어야 한다.
        assert sorted(상태들) == ["passed", "passed"]

    async def test_두_번_돌아도_같은_댓글에_돈을_안_쓴다(self, 세상, 가짜들):
        await watcher.watch_once()
        r = await watcher.watch_once()
        # 두 번째 주기: 유튜브는 같은 댓글을 다시 주지만 DB 가 걸러서
        # 새 댓글 0, 판별 0 이어야 한다.
        assert r["collected"] == 0
        assert r["judged"] == 0

    async def test_하루_상한을_넘기면_멈추고_pending_으로_남긴다(self, 세상, 가짜들, monkeypatch):
        monkeypatch.setattr(get_settings(), "llm_daily_cap", 1)
        r = await watcher.watch_once()
        assert r["collected"] == 2
        assert r["judged"] == 1            # 상한만큼만
        assert watcher.상태["llm_calls_today"] == 1

        async with AsyncSessionLocal() as db:
            pending = (
                await db.execute(
                    select(Comment).where(
                        Comment.channel_id == 세상["둘다"], Comment.status == "pending"
                    )
                )
            ).scalars().all()
        # 나머지 하나는 잃어버리지 않고 pending 으로 남아 다음 날 이어간다.
        assert len(pending) == 1

        r2 = await watcher.watch_once()
        assert r2["judged"] == 0
        assert any("상한" in s for s in r2["skipped"])

    async def test_날이_바뀌면_상한을_다시_센다(self, 세상, 가짜들):
        watcher.상태["today"] = "2000-01-01"
        watcher.상태["llm_calls_today"] = 999_999
        assert watcher._남은_오늘_호출() == get_settings().llm_daily_cap
        assert watcher.상태["llm_calls_today"] == 0

    async def test_상태를_남긴다(self, 세상, 가짜들):
        assert watcher.상태["last_run_at"] is None
        await watcher.watch_once()
        assert watcher.상태["last_run_at"] is not None
        assert watcher.상태["last_result"]["judged"] == 2

    async def test_유튜브_키가_없으면_조용히_넘어간다(self, 세상, 가짜들, monkeypatch):
        monkeypatch.setattr(get_settings(), "youtube_api_key", None)
        r = await watcher.watch_once()
        assert r["judged"] == 0
        assert any("YOUTUBE_API_KEY" in e for e in r["errors"])
