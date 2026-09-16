"""Google 로그인 · 로그아웃 · 내 정보 (F_R_101).

처음 로그인하면 개인 워크스페이스를 하나 만들어준다. 워크스페이스가 없으면
채널을 붙일 데가 없어서, 가입 직후에 아무것도 못 하게 된다.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import COOKIE, current_user
from app.db.models import (
    SESSION_DAYS,
    Channel,
    Session,
    User,
    Workspace,
    WorkspaceMember,
)
from app.db.session import get_db
from app.services import google_oauth as goog

router = APIRouter(prefix="/auth", tags=["auth"])

CALLBACK_PATH = "/api/auth/google/callback"


class Me(BaseModel):
    id: int
    email: str
    name: str | None
    picture: str | None
    workspace_id: int
    workspace_name: str
    role: str


def _redirect_uri() -> str:
    return get_settings().oauth_redirect_base.rstrip("/") + CALLBACK_PATH


def _require_oauth():
    cfg = get_settings()
    if not cfg.oauth_ready:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            ".env 에 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 이 없습니다. "
            "Google Cloud Console 에서 OAuth 클라이언트를 만들어 넣어주세요.",
        )
    return cfg


@router.get("/google/login", summary="구글 로그인 시작")
async def login(next: str = Query("/app", description="로그인 후 돌아갈 곳")):
    cfg = _require_oauth()
    # next 는 사용자가 준 값이라 그대로 믿지 않는다. 외부 주소를 넣으면
    # 로그인 직후 남의 사이트로 튕기는 통로가 된다.
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/app"
    state = goog.states.issue(kind="login", next=safe_next)
    return RedirectResponse(
        goog.authorize_url(
            cfg.google_client_id, _redirect_uri(), goog.LOGIN_SCOPES, state
        )
    )


@router.get("/google/callback", include_in_schema=False)
async def callback(
    response: Response,
    state: str = Query(...),
    code: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    cfg = _require_oauth()

    # 여기서 400 JSON 을 던지면 브라우저에 {"detail": ...} 가 그대로 뜬다.
    # 로그인을 취소했거나 서버가 재시작돼 state 가 날아간 사람이 보는 첫
    # 화면이 그거면 안 된다. 채널 연동 쪽(connect_callback)은 이미 화면으로
    # 돌려보내고 있었는데 로그인만 빠져 있었다.
    data = goog.states.take(state)
    if data is None or data.get("kind") != "login":
        return RedirectResponse("/app?login_error=state")
    if error or not code:
        return RedirectResponse(f"/app?login_error={error or 'cancelled'}")

    tokens = await goog.exchange_code(
        cfg.google_client_id, cfg.google_client_secret, code, _redirect_uri()
    )
    profile = await goog.fetch_profile(tokens.access_token)

    user = await _upsert_user(db, profile)
    session = Session.new(user.id)
    db.add(session)
    await db.commit()

    resp = RedirectResponse(data.get("next", "/app"))
    resp.set_cookie(
        COOKIE,
        session.token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,           # JS 가 못 읽는다. XSS 로 세션을 훔치기 어려워진다.
        samesite="lax",          # 남의 사이트에서 온 요청에는 쿠키를 안 붙인다.
        secure=cfg.session_cookie_secure,
        path="/",
    )
    return resp


async def _upsert_user(db: AsyncSession, p: goog.GoogleProfile) -> User:
    """구글 sub 로 찾고, 없으면 이메일로 한 번 더 찾는다.

    이메일로도 보는 이유: 초안 데이터나 수동으로 만든 계정에는 google_id 가
    없을 수 있다. 그런 계정에 sub 를 채워 넣어 이어붙인다.
    """
    user = (
        await db.execute(select(User).where(User.google_id == p.sub))
    ).scalar_one_or_none()
    if user is None:
        user = (
            await db.execute(select(User).where(User.email == p.email))
        ).scalar_one_or_none()

    now = datetime.now(UTC).replace(tzinfo=None)
    if user is None:
        user = User(email=p.email, name=p.name, google_id=p.sub, picture=p.picture)
        db.add(user)
        await db.flush()
    else:
        user.google_id = p.sub
        # 이메일은 다른 계정이 이미 쓰고 있을 수 있다 (수동으로 만든 계정,
        # 또는 구글에서 이메일을 바꾼 경우). 그대로 덮어쓰면 unique 제약에
        # 걸려 로그인 자체가 500 으로 죽는다. 비어 있을 때만 옮긴다.
        if user.email != p.email:
            taken = (
                await db.execute(select(User.id).where(User.email == p.email))
            ).scalar_one_or_none()
            if taken is None:
                user.email = p.email
        user.name = p.name or user.name
        user.picture = p.picture or user.picture
    user.last_login_at = now

    await _ensure_workspace(db, user)
    await db.commit()
    await db.refresh(user)
    return user


async def _ensure_workspace(db: AsyncSession, user: User) -> None:
    """소속이 하나도 없으면 개인 워크스페이스를 만들어준다."""
    has = (
        await db.execute(
            select(WorkspaceMember.id).where(WorkspaceMember.user_id == user.id).limit(1)
        )
    ).scalar_one_or_none()
    if has is not None:
        return

    ws = Workspace(name=f"{user.name or user.email.split('@')[0]}의 워크스페이스",
                   type="personal")
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))


DEV_EMAIL = "dev@localhost"


@router.get("/dev-login", include_in_schema=False)
async def dev_login(
    next: str = Query("/app"), db: AsyncSession = Depends(get_db)
):
    """구글 없이 로그인한 척한다. 로컬 개발 전용.

    주인 없는 채널(인증 붙이기 전에 스크립트로 만든 것들)도 같이 넘겨받는다.
    안 그러면 로그인은 됐는데 화면이 비어 있어서 뭐가 잘못된 건지 헷갈린다.
    """
    cfg = get_settings()
    if not cfg.dev_login_allowed:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "개발용 로그인이 꺼져 있습니다. .env 에 DEBUG=true, DEV_LOGIN=true 가 "
            "있어야 하고 localhost 에서만 됩니다.",
        )

    user = (
        await db.execute(select(User).where(User.email == DEV_EMAIL))
    ).scalar_one_or_none()
    if user is None:
        user = User(email=DEV_EMAIL, name="개발자")
        db.add(user)
        await db.flush()
    user.last_login_at = datetime.now(UTC).replace(tzinfo=None)

    await _ensure_workspace(db, user)
    await db.flush()

    ws_id = (
        await db.execute(
            select(WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == user.id)
            .order_by(WorkspaceMember.id)
            .limit(1)
        )
    ).scalar_one()

    # 주인 없는 채널을 이 워크스페이스로. 이미 주인이 있는 건 건드리지 않는다.
    orphans = (
        await db.execute(select(Channel).where(Channel.workspace_id.is_(None)))
    ).scalars().all()
    for ch in orphans:
        ch.workspace_id = ws_id
        ch.connected_by_user_id = user.id

    session = Session.new(user.id)
    db.add(session)
    await db.commit()

    safe_next = next if next.startswith("/") and not next.startswith("//") else "/app"
    resp = RedirectResponse(safe_next)
    resp.set_cookie(
        COOKIE, session.token, max_age=SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=cfg.session_cookie_secure, path="/",
    )
    return resp


@router.get("/start", include_in_schema=False)
async def start(next: str = Query("/app")):
    """설정에 맞는 로그인으로 보낸다.

    화면은 구글을 쓰는지 개발용을 쓰는지 알 필요가 없다. 여기로 보내면
    서버가 알아서 고른다 — .env 만 바꾸면 화면은 그대로 둬도 된다.
    """
    cfg = get_settings()
    if cfg.oauth_ready:
        return RedirectResponse(f"/api/auth/google/login?next={next}")
    if cfg.dev_login_allowed:
        return RedirectResponse(f"/api/auth/dev-login?next={next}")
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "로그인 방법이 설정되지 않았습니다. .env 에 GOOGLE_CLIENT_ID/SECRET 을 "
        "넣거나, 개발 중이면 DEV_LOGIN=true 를 켜주세요.",
    )


@router.get("/me", response_model=Me, summary="내 정보")
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user.id)
            .order_by(WorkspaceMember.id)
            .limit(1)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "워크스페이스가 없습니다")

    ws, role = row
    return Me(
        id=user.id, email=user.email, name=user.name, picture=user.picture,
        workspace_id=ws.id, workspace_name=ws.name, role=role,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="로그아웃")
async def logout(
    response: Response,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """이 사용자의 세션을 전부 끊는다.

    한 개만 지우지 않는 이유: 로그아웃을 누르는 상황은 대개 '남이 쓸까 봐'
    이고, 다른 기기에 남은 세션을 남겨두면 그 기대와 어긋난다.
    """
    for s in (
        await db.execute(select(Session).where(Session.user_id == user.id))
    ).scalars().all():
        await db.delete(s)
    await db.commit()
    response.delete_cookie(COOKIE, path="/")
