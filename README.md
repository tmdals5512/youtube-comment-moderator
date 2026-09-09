# youtube-comment-moderator

유튜브 채널 관리자를 위한 유해댓글 검토 도구. 댓글을 모아 유해성을 판별하고,
관리자가 우선순위대로 확인해 조치할 수 있게 한다.

광주 인공지능사관학교 기업연계 프로젝트 (팀 Outlier).

---

## 무엇을 하는가

채널에 달린 댓글은 사람이 다 읽을 수 없을 만큼 많다. 그렇다고 자동으로
가려버리면 멀쩡한 댓글이 조용히 사라진다. 이 도구는 **판별은 기계가 하고
조치는 사람이 하도록** 나눈다.

```
댓글 수집
   ↓
① 관리자 등록어         차단어면 → 숨김 (LLM 안 부름)
   ↓                    검토어면 → 표시만 해두고 계속
② LLM 판별              유해 / 애매 / 정상 + 카테고리
   ↓
③ 검토 큐               위험도순으로 관리자에게 쌓인다
   ↓
④ 관리자 조치           숨김 / 유지 / 채널차단
```

등록어에 안 걸린 댓글도 전부 LLM으로 보낸다. 등록어만 검사하면 관리자가 미리
상상한 단어만 잡히고, 새 은어나 우회 표기는 그대로 빠져나가기 때문이다.

## 설계에서 정한 것들

측정해보고 정한 것이라 되돌리려면 근거가 필요하다.

**자동 숨김을 두지 않는다.** `AUTO_HIDE_CATEGORIES`는 비어 있다. 마지막까지
'욕설' 하나는 남겨뒀었지만 실데이터에서 무너졌다 — 방송 고지문 인용,
행동 지적, `보지마`를 성기로 읽은 오독까지 사람을 안 거치고 가려졌다.
되돌릴 수 없는 조치를 기계에 맡기는 설계 자체가 문제였다. 자세한 사례는
[`app/services/pipeline.py`](app/services/pipeline.py) 주석에 있다.

**1차 규칙에 기본 차단 목록이 없다.** `DEFAULT_BLOCK_WORDS = []`.
1차는 판별기가 아니라 관리자 통제 장치다. 등록어가 하나도 없어도 파이프라인은
정상 동작한다(전부 LLM으로 간다).

**외부 유해표현 API는 쓰지 않는다.** 비교 측정 코드만 남겼다.
COREPIN은 무료 규칙 대비 탐지율 차이가 1.4%p인데 건당 5원이었고,
OpenAI Moderation은 한국어 탐지율이 19.8%였다.

**LLM이 주는 confidence는 쓰지 않는다.** 맞을 때 0.98, 틀릴 때 0.96으로
구분이 되지 않았다.

## 실행

PostgreSQL(pgvector)과 Python 3.13이 필요하다.

```bash
# 1. DB
docker run -d --name outlier-db \
  -e POSTGRES_PASSWORD=<비밀번호> -p 5432:5432 pgvector/pgvector:pg17

# 2. 설정
cp .env.example .env        # DB 비밀번호와 API 키를 채운다
pip install -r requirements.txt
python -m scripts.init_db   # 테이블 생성

# 3. 서버
python -m uvicorn app.main:app --port 8000
```

| 주소 | 화면 |
|---|---|
| http://127.0.0.1:8000/ | 온보딩 (로그인 → 채널 연동 → 동의) |
| http://127.0.0.1:8000/app | 관리자 화면 (대시보드·검토 큐·숨김·기준·이력) |
| http://127.0.0.1:8000/docs | API 문서 (Swagger) |

인증은 아직 붙지 않았다. Google/YouTube OAuth가 들어오기 전까지 온보딩은
클릭으로 넘어간다.

## API

`/docs`에 전부 있고, 응답 예시는
[`docs/api/samples.json`](docs/api/samples.json)에 있다 (댓글 원문과 작성자명은
가려두었다).

| | |
|---|---|
| `GET /api/channels` | 연동된 채널 목록 |
| `GET /api/channels/{id}/stats` | 처리 현황 (검토 전환율, 카테고리 분포) |
| `GET /api/channels/{id}/queue` | 검토 큐 — 위험도순 |
| `GET /api/channels/{id}/hidden` | 숨김 목록 — 오탐을 발견하는 통로 |
| `GET /api/channels/{id}/history` | 처리 이력 (누가 언제 무엇을 왜) |
| `POST /api/comments/{id}/action` | 조치 (숨김 / 유지 / 채널차단) |
| `GET /api/comments/{id}/similar` | 과거 유사 사례 (임베딩 유사도) |
| `GET·POST·PATCH·DELETE /api/channels/{id}/rules` | 등록어 관리 |
| `POST /api/moderation/check` | 댓글 1건 즉시 판정 |

검토 큐 정렬은 카테고리별 위험도에 확산도(좋아요+답글)를 반영한다.
신상털기·위협·자해는 늦으면 되돌릴 수 없어 가장 위로 올린다.

## 구조

```
app/
  api/        엔드포인트 (channels, rules, moderation, review, health)
  services/
    collector.py   YouTube 수집. 기본 호출은 답글의 58%만 오므로
                   잘린 스레드를 따로 보충해 전량을 맞춘다
    pattern.py     등록어 1개에서 우회표현 정규식을 만든다
                   (초성·겹자음·사이끼어들기·모음늘이기)
    moderation.py  등록어 검사 (allow → block → review 순, 채널별 격리)
    llm.py         LLM 판별. 판단원칙과 예시로 문맥·공격대상·의도를 보게 한다
    pipeline.py    위 조각을 순서대로 잇는다. 정책은 route() 하나에만 있다
    embed.py       임베딩 (유사 사례 검색용)
  db/         SQLAlchemy 모델·세션
  static/     관리자 화면 (빌드 단계 없는 정적 파일)
scripts/      수집·판별·평가 CLI
tests/        pytest
```

정책을 `route()` 하나에 모아둔 덕에, 무엇을 숨길지 바꿔도 LLM을 다시 부르지 않고
저장된 판정만으로 재배치할 수 있다 (`scripts/reroute.py`). 프롬프트 자체를
고쳤을 때만 다시 묻는다 (`scripts/rejudge.py`).

## 주요 스크립트

```bash
python -m scripts.collect_channel @채널핸들 20   # 최신 영상 20개의 댓글 수집
python -m scripts.run_pipeline --channel 4 --save  # 파이프라인 실행 (--save 없으면 저장 안 함)
python -m scripts.rejudge --channel 4 --dry      # 프롬프트 개정 후 재판정 (건수·비용만)
python -m scripts.reroute --channel 4 --apply    # 정책만 바꿨을 때 재배치 (LLM 안 부름)
python -m scripts.eval_llm --n 200               # 판별 정확도 채점
python -m pytest                                 # 테스트
```

## 데이터 취급

- 댓글 원문과 작성자 정보는 수집 후 최대 30일 보관하고, 이후에는 위험도 등
  파생 지표만 남긴다
- 채널마다 데이터를 격리한다. 서로 다른 채널의 지표를 한 화면에서 비교해
  보여주지 않는다
- 채널 연동을 해제하면 해당 채널 데이터를 전부 삭제한다
- 수집 산출물(`eval/`)과 `.env`는 레포에 올리지 않는다 (`.gitignore`)

유튜브 정책상 댓글을 완전히 삭제할 수는 없다. 조치는
`comments.setModerationStatus(rejected)`로 비공개 처리하는 것이고,
채널차단도 해당 채널에서만 적용된다.

## 평가 데이터

정확도 채점에는 [UnSmile](https://github.com/smilegate-ai/korean_unsmile_dataset)을
쓴다. CC BY-NC-ND라 채점 용도로만 쓰고, 단어를 추출해 규칙에 넣거나 상업적으로
이용하지 않는다. 레포에 포함하지 않으므로 직접 받아야 한다.

## 팀

박승민(PM·백엔드) · 김지황(백엔드·DB) · 박정호(데이터 수집) ·
조민준(PM·발표) · 신효은(프론트엔드). 멘토 정연주.
