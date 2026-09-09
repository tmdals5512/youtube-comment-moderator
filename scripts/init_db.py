"""ORM 모델과 실제 테이블의 컬럼을 맞춘다.

초안 테이블이 이미 만들어져 있어서 ADD COLUMN 으로 따라가는 중이다.
(마이그레이션 도구는 아직 안 붙였으므로 임시 스크립트. 테이블이 더 늘어나면
 Alembic 으로 옮겨야 한다.)

    python -m scripts.init_db
"""

import asyncio

from sqlalchemy import text

from app.db.session import engine

STATEMENTS = [
    # ── MVP① 규칙기반 (F_R_108) ──
    "ALTER TABLE channel_rules ADD COLUMN IF NOT EXISTS compiled_regex TEXT",
    "ALTER TABLE channel_rules ADD COLUMN IF NOT EXISTS action VARCHAR(10) DEFAULT 'block'",
    "ALTER TABLE channel_rules ADD COLUMN IF NOT EXISTS expand_variants BOOLEAN DEFAULT TRUE",
    "UPDATE channel_rules SET compiled_regex = rule_value WHERE compiled_regex IS NULL",
    "ALTER TABLE channel_rules ALTER COLUMN compiled_regex SET NOT NULL",

    # ── 채널별 설정 ──
    # LLM 프롬프트 뒤에 붙일 채널 맥락 (은어 뜻 등)
    "ALTER TABLE channels ADD COLUMN IF NOT EXISTS context TEXT",

    # ── 수집 대상 영상 ──
    # 어떤 사건의 영상인지(topic)를 남겨야 나중에 주제별로 나눠 볼 수 있다.
    """CREATE TABLE IF NOT EXISTS videos (
        video_id      VARCHAR(64) PRIMARY KEY,
        channel_id    INTEGER NOT NULL REFERENCES channels(id),
        title         VARCHAR(500),
        topic         VARCHAR(40),
        comment_count INTEGER,
        published_at  TIMESTAMP,
        collected_at  TIMESTAMP DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_videos_channel ON videos (channel_id)",
    "CREATE INDEX IF NOT EXISTS ix_videos_topic ON videos (topic)",

    # ── 댓글 수집기 (F_R_107) ──
    # 답글 지원
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS parent_comment_id VARCHAR(64)",
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS is_reply BOOLEAN DEFAULT FALSE",
    "CREATE INDEX IF NOT EXISTS ix_comments_parent ON comments (parent_comment_id)",

    # 우선순위 신호 (확산도 · 논쟁 강도)
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS like_count INTEGER DEFAULT 0",
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS total_reply_count INTEGER DEFAULT 0",

    # 작성자 식별 (반복 악플러 추적 · banAuthor 대상)
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS author_channel_id VARCHAR(64)",
    "CREATE INDEX IF NOT EXISTS ix_comments_author ON comments (author_channel_id)",

    # 댓글 수정 감지 → 재판별 대상 판단
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS content_updated_at TIMESTAMP",

    # 30일 보관 정책 (YouTube API 정책). 이후엔 파생 지표만 남긴다.
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS retention_expires_at TIMESTAMP",
    "CREATE INDEX IF NOT EXISTS ix_comments_retention ON comments (retention_expires_at)",

    # ── 과거 유사 사례 (F_R_114) ──
    # pgvector 를 고른 이유가 이것이다. 단어가 겹치지 않아도 뜻이 비슷하면 찾는다.
    "CREATE EXTENSION IF NOT EXISTS vector",
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS embedding vector(1536)",
    # 코사인 거리 기준 근사 검색. 데이터가 적을 땐 없어도 되지만,
    # 채널 하나가 수만 건이 되면 인덱스 없이는 매번 전수 비교가 된다.
    """CREATE INDEX IF NOT EXISTS ix_comments_embedding
       ON comments USING hnsw (embedding vector_cosine_ops)""",

    # ── 판별 결과 · 관리자 조치 ──
    # 파이프라인 결과를 DB 에 남겨야 검토 큐 API 가 읽을 게 생긴다.
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'pending'",
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP",
    "CREATE INDEX IF NOT EXISTS ix_comments_status ON comments (status)",

    "ALTER TABLE risk_assessments ADD COLUMN IF NOT EXISTS destination VARCHAR(16)",
    "ALTER TABLE risk_assessments ADD COLUMN IF NOT EXISTS category VARCHAR(20)",
    "ALTER TABLE risk_assessments ADD COLUMN IF NOT EXISTS model VARCHAR(40)",
    "ALTER TABLE risk_assessments ADD COLUMN IF NOT EXISTS rule_id INTEGER",
    "ALTER TABLE risk_assessments ADD COLUMN IF NOT EXISTS rule_value VARCHAR(255)",
    "ALTER TABLE risk_assessments ADD COLUMN IF NOT EXISTS rule_action VARCHAR(10)",
    "CREATE INDEX IF NOT EXISTS ix_ra_dest ON risk_assessments (destination)",

    "ALTER TABLE actions ADD COLUMN IF NOT EXISTS actor VARCHAR(100)",
    "ALTER TABLE actions ADD COLUMN IF NOT EXISTS note TEXT",
    "ALTER TABLE actions ADD COLUMN IF NOT EXISTS youtube_synced BOOLEAN DEFAULT FALSE",

    # upsert 의 충돌 기준. 이게 있어야 같은 영상을 다시 돌려도 중복이 안 쌓인다.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_comments_yt_id ON comments (youtube_comment_id)",
]


async def main() -> None:
    async with engine.begin() as conn:
        for sql in STATEMENTS:
            await conn.execute(text(sql))
            print(f"[OK] {sql[:72]}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
