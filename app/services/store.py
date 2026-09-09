"""판별 결과를 DB 에 남긴다.

pipeline.py 는 DB 를 모르는 순수 로직으로 두고(테스트가 쉬워진다),
저장은 여기서만 한다.

남기는 곳 두 군데.
  risk_assessments  판정 1건 = 1행. 재판별해도 덮어쓰지 않고 쌓는다.
  comments.status   지금 이 댓글이 어디 있는지 (큐 조회가 이걸로 돈다)
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import update

from app.db.models import Comment, RiskAssessment
from app.services.pipeline import (
    HIDDEN,
    PASSED,
    QUEUE_INFO,
    QUEUE_JUDGE,
    PipelineResult,
)

# 행선지 -> 댓글의 현재 상태.
# 판단 필요와 참고는 둘 다 '관리자가 볼 목록'이라 queued 로 합친다.
# 어느 쪽이었는지는 risk_assessments.destination 에 남아 구분할 수 있다.
STATUS = {
    HIDDEN: "hidden",
    QUEUE_JUDGE: "queued",
    QUEUE_INFO: "queued",
    PASSED: "passed",
}


async def save_results(
    db,
    pairs: Sequence[tuple[int, PipelineResult]],
    model: str | None = None,
) -> int:
    """(comment_id, 판별결과) 목록을 저장하고 저장한 건수를 돌려준다."""
    if not pairs:
        return 0

    now = datetime.now(UTC).replace(tzinfo=None)

    db.add_all(
        RiskAssessment(
            comment_id=cid,
            stage=r.decided_by,
            destination=r.destination,
            risk_level=r.llm_label,
            category=r.llm_category,
            reasoning=r.reason or None,
            model=model if r.decided_by == "llm" else None,
            rule_value=r.rule_value,
            rule_action=r.rule_action,
            created_at=now,
        )
        for cid, r in pairs
    )

    # 상태는 행선지별로 묶어서 한 번에 갱신한다 (건당 UPDATE 를 피한다).
    by_status: dict[str, list[int]] = {}
    for cid, r in pairs:
        by_status.setdefault(STATUS[r.destination], []).append(cid)

    for status, ids in by_status.items():
        await db.execute(
            update(Comment)
            .where(Comment.id.in_(ids))
            # 관리자가 이미 처리한 댓글은 건드리지 않는다.
            # 사람의 판단이 재판별 결과보다 우선한다.
            .where(Comment.reviewed_at.is_(None))
            .values(status=status)
        )

    await db.commit()
    return len(pairs)
