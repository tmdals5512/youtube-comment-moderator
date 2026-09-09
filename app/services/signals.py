"""텍스트 구조 이상 신호 추출.

주의: UnSmile 라벨로 재보니 변별력이 약했다 (악플 1.45% vs clean 1.05%).
초성 비율이 아주 높은 문장은 오히려 ㄱㅅ · ㅇㅈ · ㄹㅇ 같은 정상 축약어라,
'비율 × 가중치' 형태의 점수 모델로는 표현이 안 된다.

그래서 판별 단계로는 쓰지 않고, 표본 추출이나 분석 보조로만 쓴다.
외부 API도 사전도 안 쓰므로 비용은 0이다.
"""

import re
import unicodedata
from dataclasses import dataclass, asdict

# 단독으로 쓰인 한글 자모 (U+3131~U+318E). "ㅅㅂ"의 ㅅ, ㅂ이 여기 해당한다.
JAMO = re.compile(r"[ㄱ-ㆎ]")

# 자모지만 유해성과 거의 무관한 것들.
# 한국어 댓글에서 ㅋㅋㅋ · ㅎㅎ · ㅠㅠ는 압도적으로 흔해서, 이걸 안 빼고
# 자모 비율을 세면 멀쩡한 댓글이 전부 고위험으로 잡힌다.
# (ㅗ는 욕설 제스처로도 쓰이므로 제외 목록에 넣지 않는다)
EMOTIVE_JAMO = set("ㅋㅎㅠㅜㄷㅡ")  # ㄷㄷ=놀람, ㅡㅡ=못마땅. 실데이터 상위권을 이것들이 차지했다.

HANGUL = re.compile(r"[가-힣]")

# 한글 사이에 위장용 특수문자가 끼어든 형태. "ㅂ@ㅅ", "시*발".
# 문장부호(. , ! ? ~ ...)는 반드시 빼야 한다. 실데이터에서 "습니다.집들어올"
# 같은 평범한 문장이 20.9% 걸려서 신호 구실을 전혀 못 했다.
# 또한 공백을 지운 문자열에 적용하면 안 된다 — "유빈, 슈카"가 "빈,슈"로 붙어 오탐된다.
VEIL = r"@#$%^&*+=|/\<>_"
GAP_FILLER = re.compile(rf"[가-힣ㄱ-ㆎ][{re.escape(VEIL)}][가-힣ㄱ-ㆎ]")

# 같은 문자가 3번 이상 연속 ("시이이이발", "ㅅㅅㅅㅂ")
REPEAT = re.compile(r"(.)\1{2,}")


@dataclass
class Signals:
    length: int
    jamo_ratio: float          # 단독 자모 비율 (전체)
    meaning_jamo_ratio: float  # ㅋㅎㅠㅜ 제외한 자모 비율 ← 실제로 쓸 값
    meaning_jamo_count: int
    has_gap_filler: bool       # 글자 사이 특수문자 삽입
    max_repeat: int            # 최대 동일문자 연속 길이
    hangul_ratio: float        # 한글 완성형 비율 (낮으면 외국어/이모지 위주)

    def as_dict(self) -> dict:
        return asdict(self)


def extract(text: str) -> Signals:
    """댓글 하나에서 구조 신호를 뽑는다. 판정은 하지 않는다 (scoring.py의 몫)."""
    text = unicodedata.normalize("NFC", text)

    # 공백을 뺀 실질 문자만 분모로 쓴다. 공백 많은 댓글이 유리해지면 안 된다.
    body = "".join(text.split())
    total = len(body) or 1

    jamo = JAMO.findall(body)
    meaning = [j for j in jamo if j not in EMOTIVE_JAMO]
    repeat = max((len(m.group()) for m in REPEAT.finditer(body)), default=0)

    return Signals(
        length=len(body),
        jamo_ratio=len(jamo) / total,
        meaning_jamo_ratio=len(meaning) / total,
        meaning_jamo_count=len(meaning),
        # 공백을 지우지 않은 원문에 적용한다 (위 주석 참고).
        has_gap_filler=bool(GAP_FILLER.search(text)),
        max_repeat=repeat,
        hangul_ratio=len(HANGUL.findall(body)) / total,
    )
