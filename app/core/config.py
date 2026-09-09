"""애플리케이션 설정. .env 파일 또는 환경변수에서 읽는다."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Outlier API"
    debug: bool = False

    # PostgreSQL (pgvector/pg17)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "postgres"
    postgres_user: str = "postgres"
    postgres_password: str
    db_echo: bool = False

    # YouTube Data API.
    # 이 키로 되는 건 '읽기'뿐이다. 숨김/차단 같은 조치는 OAuth 2.0 +
    # 채널 소유자 권한이 따로 필요하다 (comments.setModerationStatus).
    # 키가 없어도 서버는 떠야 하므로 None 허용.
    youtube_api_key: str | None = None

    # COREPIN 유해표현 탐지 API.
    # 측정 결과 파이프라인에서 제외하기로 했다 — 명백한 욕설 탐지가 무료
    # 규칙과 1.4%p 차이인데 건당 5원이고, 무료 티어가 월 1,000건이라
    # 실서비스가 불가능했다. 비교 측정용으로만 설정을 남겨둔다.
    # 무료 티어: 분당 60건 / 월 1,000건 (요청이 아니라 텍스트 단위). 초과 시 429.
    corepin_api_key: str | None = None
    corepin_base_url: str = "https://api.corepin.ai"

    # LLM 판별. 실측 건당 약 0.17원 (2,000건 돌려 $0.24).
    # 시스템 프롬프트가 1,024토큰을 넘으면 프롬프트 캐싱이 걸려 입력값이 1/10이 된다.
    # 그 아래로는 캐싱이 안 되므로, 짧게 줄이는 게 오히려 비싸진다.
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-luna"
    # 사고 방지용 상한. 한 번 실행에서 이 건수를 넘으면 멈춘다.
    llm_max_calls_per_run: int = 600

    # 프론트엔드가 브라우저에서 이 API 를 부르려면 여기에 그 주소가 있어야 한다.
    # 없으면 브라우저가 CORS 로 막는다 (서버는 멀쩡한데 화면만 안 뜬다).
    # 기본값은 흔한 로컬 개발 포트들. .env 에서 콤마로 구분해 덮어쓸 수 있다.
    #   CORS_ORIGINS=http://localhost:5173,https://우리도메인
    # 배포할 때는 반드시 실제 도메인으로 좁혀야 한다.
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"      # Vite
        "http://localhost:3000,http://127.0.0.1:3000,"      # Next.js / CRA
        "http://localhost:8080,http://127.0.0.1:8080"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("youtube_api_key", "corepin_api_key", "openai_api_key", mode="before")
    @classmethod
    def _strip_key(cls, v):
        """키 앞뒤 공백·줄바꿈을 떼어낸다.

        대시보드에서 복사하면 개행이 딸려오는 일이 잦은데, 그대로 헤더에 넣으면
        401이 나고 원인 찾기가 번거롭다. 실제로 한 번 겪었다.
        """
        return v.strip() if isinstance(v, str) else v

    @property
    def database_url(self) -> URL:
        """asyncpg 드라이버용 접속 URL.

        URL.create를 쓰는 이유: 비밀번호에 @ ? # 같은 특수문자가 들어가도
        SQLAlchemy가 알아서 이스케이프해준다. 문자열 f-string으로 조립하면 깨진다.
        """
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
