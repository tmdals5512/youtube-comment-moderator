"""관리자 조치를 실제 유튜브에 반영한다 (F_R_115).

[숨김] 을 누르면 여기를 거쳐 유튜브 댓글창에서 실제로 가려진다.
실채널로 끝까지 확인했다 (2026-09-15, 숨김 → 시청자 조회에서 사라짐,
복구 → 다시 보임).

알아둘 것 두 가지.

  1. **댓글을 지울 수는 없다.** 유튜브가 허용하는 건 '비공개 처리'(rejected)
     뿐이다. 남들에게 안 보이게 되지만 작성자 본인에게는 보일 수 있다.
     그래서 화면에도 '삭제'가 아니라 '숨김'이라고 쓴다.

  2. **채널 소유자 권한이 있어야 한다.** 우리가 수집만 한 채널(진용진 등)은
     조치를 못 한다. 주인이 연동하면서 허락해줘야 토큰이 생긴다.
"""

from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.services.google_oauth import refresh_access_token

API = "https://www.googleapis.com/youtube/v3"

# 유튜브가 허용하는 처리 상태. 'deleted' 같은 건 없다.
HIDE = "rejected"       # 비공개 — 남들에게 안 보인다
PUBLISH = "published"   # 공개 — 숨겼던 걸 되돌린다


class NotConnected(RuntimeError):
    """채널 소유자가 연동하지 않아 조치 권한이 없다."""


@dataclass
class ActionResult:
    ok: bool
    detail: str = ""


async def _access_token(refresh_token: str | None) -> str:
    cfg = get_settings()
    if not refresh_token:
        raise NotConnected(
            "이 채널은 소유자 연동이 안 돼 있습니다. "
            "채널 관리에서 [채널 연결]을 먼저 해주세요."
        )
    if not cfg.oauth_ready:
        raise NotConnected(".env 에 GOOGLE_CLIENT_ID / SECRET 이 없습니다.")
    return await refresh_access_token(
        cfg.google_client_id, cfg.google_client_secret, refresh_token
    )


def _hint(status: int, body: str) -> str:
    """구글 오류를 원인이 보이는 문장으로 바꾼다."""
    낱말 = {
        "insufficientPermissions": (
            "권한이 모자랍니다. 연동할 때 'YouTube 관리' 항목을 허용했는지 "
            "확인하고, 채널을 다시 연결해주세요."
        ),
        "forbidden": "이 채널의 댓글을 관리할 권한이 없습니다. 소유자 계정으로 연동해야 합니다.",
        "commentNotFound": "유튜브에서 이미 사라진 댓글입니다.",
        "processingFailure": "유튜브 쪽 일시 오류입니다. 잠시 뒤 다시 시도해주세요.",
        "invalid_grant": "연동이 해제됐습니다. 채널을 다시 연결해주세요.",
    }
    for k, v in 낱말.items():
        if k in body:
            return v
    return f"유튜브 오류 {status}: {body[:160]}"


async def set_moderation(
    refresh_token: str | None, youtube_comment_ids: list[str], status: str
) -> ActionResult:
    """댓글을 비공개 처리하거나 되돌린다.

    한 번에 여러 개를 보낼 수 있다(쉼표로 이어서). 관리자가 목록에서
    여러 건을 한꺼번에 처리하는 걸 염두에 둔 것이다.
    """
    if not youtube_comment_ids:
        return ActionResult(True, "대상 없음")

    token = await _access_token(refresh_token)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{API}/comments/setModerationStatus",
            params={"id": ",".join(youtube_comment_ids), "moderationStatus": status},
            headers={"Authorization": f"Bearer {token}"},
        )
    # 성공이면 204 No Content 가 온다. 본문이 없다.
    if r.status_code in (200, 204):
        return ActionResult(True)
    return ActionResult(False, _hint(r.status_code, r.text))


async def ban_author(
    refresh_token: str | None, youtube_comment_id: str
) -> ActionResult:
    """작성자를 이 채널에서 차단한다.

    플랫폼 전체 차단이 아니다. 이 채널에만 적용된다 — 그래서 화면에도
    '채널 차단'이라고 쓴다. 유튜브 API 는 댓글 ID 로 차단을 건다.
    """
    token = await _access_token(refresh_token)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{API}/comments/setModerationStatus",
            params={
                "id": youtube_comment_id,
                "moderationStatus": HIDE,
                "banAuthor": "true",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code in (200, 204):
        return ActionResult(True)
    return ActionResult(False, _hint(r.status_code, r.text))


async def my_channels(access_token: str) -> list[dict]:
    """연동한 계정이 소유한 채널 목록.

    보통 하나지만, 브랜드 계정을 여럿 가진 사람도 있다.
    """
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{API}/channels",
            params={"part": "snippet", "mine": "true", "maxResults": 50},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if not r.is_success:
        raise RuntimeError(_hint(r.status_code, r.text))
    return [
        {
            "youtube_channel_id": it["id"],
            "title": it["snippet"].get("title") or "(제목 없음)",
        }
        for it in r.json().get("items", [])
    ]
