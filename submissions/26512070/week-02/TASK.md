# Task — week-02 harness A/B

두 하네스에 **같은** 태스크를 준다. `run_ab.py`가 아래 `task:`와 `expected:`
줄을 읽는다. 성공 판정 기준은 **실행 전에** 이 파일에 적고 커밋했으며, 실행
결과를 본 뒤에는 고치지 않는다.

task: app.log에서 ERROR 줄이 가장 많은 시간대(HH:00)는 언제인가? HH:00 형식의 시간 하나로 답하라.

## 성공 판정 기준

최종 답 문자열에 `app.log`에서 ERROR 줄이 가장 많은 시간대가 `HH:00` 형태로
들어 있으면 **O**, 아니면 **X**로 한다. 부분 점수는 없다. 최종 답이 없는 채로
종료한 실행(MAX_STEPS 도달, 계획 파싱 실패 등)도 **X**로 기록하고 지우지 않는다.

expected: 14:00

## 기준 입력

`app.log`는 `weeks/week-02/starter/app.log`를 **바이트 단위로 그대로** 복사한
것이다 (`md5 abc8e955b08258e1d593541b27c5730b`). 60줄, 3082바이트, ERROR 19줄.
정답 근거는 시간대별 ERROR 줄 수다.

| 시간대 | 14 | 12 | 15 | 13 | 10 | 17 | 16 | 11 | 09 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ERROR 줄 | **6** | 3 | 2 | 2 | 2 | 1 | 1 | 1 | 1 |

## 실험 조건 (두 하네스 공통 = 상수)

A/B는 **두 설정**에서 각각 돌렸다. 설정 안에서는 하네스만 독립변수이고,
설정 사이에서는 하네스 코드가 아니라 **모델 호출 상수만** 달라진다.

| 항목 | 설정 A (run 1–6) | 설정 B (run 7–12) |
|---|---|---|
| provider | OpenRouter (OpenAI 호환 API) | 동일 |
| model | `nvidia/nemotron-3.5-lightning:free` | `anthropic/claude-sonnet-4.5` |
| `max_tokens` | **1024** (강의 뼈대 값) | **미지정** — 보내지 않고 공급자 기본값 |
| temperature | API 기본값 (미지정) | 동일 |
| 도구 집합 | `read_file(path)`, `count_pattern(path, pattern)` 두 개뿐 | 동일 |
| 도구 모듈 | `tools_shared.py` — 두 하네스가 동일하게 import | 동일 |
| 되돌릴 수 없는 도구 | **없음** → `interventions`는 구조적으로 0 | 동일 |
| 하네스 로직 | `max_steps=6`, `max_replan=1` | **동일 (변경 없음)** |
| 실행 횟수 | 하네스당 3회 | 하네스당 3회 |

설정 A는 실패한 채로 `results.csv`에 남는다. 지우지 않는 이유는 그 실패가
설정 B를 만든 근거이기 때문이다. 두 설정 사이에서 하네스 코드의 다섯 축
값은 하나도 바뀌지 않았고, 바뀐 것은 모델명과 토큰 상한 두 개뿐이다.
설정 B에서 추가된 코드는 응답의 `finish_reason`을 **로그에 적는 것**뿐이며,
종료 판정에는 쓰지 않는다 — 축3을 설정 A와 똑같이 유지하기 위해서다.

### 모델이 두 번 바뀐 경위

1주차에 쓰던 `minimax/minimax-m3:free`로 잡으려 했으나 실행 직전 스모크
테스트에서 404를 받았다: *"This model is unavailable for free. The paid version
is available now."* 무료 티어에서 빠진 것이다. 과제 README가
`nvidia/nemotron-3.5-lightning:free`를 "tested with the starter"로 명시하고
있어 그것으로 바꿨고, 1요청짜리 스모크로 도구 호출을 확인한 뒤 설정 A 6회를
돌렸다.

설정 A는 6회 중 4회가 `max_tokens=1024`에 잘렸다. 계획 호출 6건이 전부 단어
중간에서 끊겼고, `2357 = 2×1024 + 309`가 그 증거다. 즉 Plan-then-Execute가
계획을 못 세운 것이 아니라 **계획을 출력할 차례가 오기 전에 잘린** 것이다.
잘림이 원인이라면 그 상한은 태스크와 무관한 인공적 제약이므로, 설정 B에서는
`max_tokens`를 아예 보내지 않고 공급자 기본값에 맡긴다. 모델도 강의
`tools_shared.py`가 OpenRouter 기본값으로 명시한 `anthropic/claude-sonnet-4.5`로
올려, 남는 차이가 모델의 무능이 아니라 하네스 설계에 귀속되게 한다.

`count_pattern`은 강의 뼈대의 `(text, pattern)` 대신 `(path, pattern)`으로 두었다.
`app.log`가 3082바이트라 `read_file`이 통째로 반환하는데, 그 전문을 도구 인자로
매 호출마다 실어 보내면 호출당 800토큰 가까이가 태스크와 무관하게 붙는다.
이 변경은 **두 하네스에 동일하게** 적용되므로 축2(도구 granularity)는 상수로 남는다.

## 하네스별로 다르게 잡은 값 (= 독립변수)

| 축 | ReAct | Plan-then-Execute |
|---|---|---|
| 1 컨텍스트 관리 | 전체 Thought/Observation 누적 | 계획 + 직전 단계 결과만 |
| 2 도구 granularity | **동일** | **동일** |
| 3 종료 조건 | `finish` 호출 또는 `max_steps=6` | 계획 단계 소진 |
| 4 에러 복구 | 에러를 Observation으로 되돌려 모델이 고침 | 재계획 **1회**까지만 (`max_replan=1`) |
| 5 개입 지점 | **동일** — 완전 자율 | **동일** — 완전 자율 |

`max_steps=6`은 강의 기본값 8이 아니다. OpenRouter 무료 티어의 하루 50요청
한도 안에 6회 실행을 넣기 위한 값이며, 이 상한 때문에 미완으로 끝난 실행이
있으면 축3의 결과로 `REPORT.md`에 기록한다.
