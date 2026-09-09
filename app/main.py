"""Outlier 백엔드 진입점."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import channels, health, moderation, review, rules
from app.core.config import get_settings
from app.db.session import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 기동 시점에 DB가 실제로 붙는지 확인한다.
    # 여기서 죽으면 잘못된 접속정보로 서버가 뜨는 걸 막을 수 있다.
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    description="유튜브 다채널 유해댓글 관리 서비스",
    version="0.1.0",
    lifespan=lifespan,
)

# 프론트엔드는 다른 포트에서 돈다. 이게 없으면 브라우저가 API 호출을 막는다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
app.include_router(rules.router, prefix="/api")
app.include_router(moderation.router, prefix="/api")
app.include_router(review.router, prefix="/api")


STATIC_DIR = Path(__file__).parent / "static"

# 관리자 화면은 빌드 단계 없는 정적 파일이다. 같은 서버에서 내보내므로
# 프론트를 따로 띄울 필요도, CORS 를 탈 일도 없다.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# 온보딩 1~6. 디자인 캔버스의 아트보드를 그대로 옮긴 정적 화면이다.
# 실제 인증은 아직 없다 — Google/YouTube OAuth 가 붙기 전까지는 클릭으로 넘어간다.
ONBOARDING = {
    1: "1-login.html",
    2: "2-signup.html",
    3: "3-connect.html",
    4: "4-select.html",
    5: "5-consent.html",
    6: "6-done.html",
}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """첫 화면은 로그인. 프로토타입 흐름이 여기서 시작한다."""
    return FileResponse(STATIC_DIR / "screens" / ONBOARDING[1])


@app.get("/onboarding/{step}", include_in_schema=False)
async def onboarding(step: int) -> FileResponse:
    if step not in ONBOARDING:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"온보딩 {step}단계 없음")
    return FileResponse(STATIC_DIR / "screens" / ONBOARDING[step])


@app.get("/app", include_in_schema=False)
async def admin() -> FileResponse:
    """관리자 화면 (대시보드·검토 큐·숨김·관리 기준·이력)."""
    return FileResponse(STATIC_DIR / "app.html")
