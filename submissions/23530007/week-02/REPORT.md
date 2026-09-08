# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

- Student ID: 23530007
- Task: `app.log`에서 ERROR가 가장 많은 시간대를 HH:00 형태로 답하기
- Success criterion: 최종 답변에 `14:00`이 포함되면 O (기준은 실행 전 `TASK.md`에 커밋)
- Provider / model: (실행 시 기록)
- How to run: `export ANTHROPIC_API_KEY=...` 후 `python run_ab.py --runs 3`

## 1. 변형 정의 — 무엇이 같고 무엇이 다른가

독립변수는 하네스 하나다. 모델, 태스크, 도구 집합은 상수로 고정했다. 두 하네스
(`harness_react.py`, `harness_plan_execute.py`)는 같은 `tools_shared.py`에서
`Chat`, `Meter`, `TOOLS_IMPL`, `TOOL_SPECS`, `MODEL`을 import하므로 도구 구현과
스키마, 모델명, 토큰 계측이 모두 동일하다.

강의의 다섯 요소 중 실제로 다르게 잡은 것은 1·3·4이고, 2는 의도적으로 고정,
5는 두 하네스 모두 비활성이다.

| 요소 | ReAct | Plan-then-Execute | 같음/다름 |
|---|---|---|---|
| 1 컨텍스트 관리 | `Chat` 하나. 전체 history를 매 호출에 그대로 전달 | `Chat` 둘. planner(`tools=False`)와 executor를 분리. planner는 태스크·계획·재계획만, executor는 계획 + 단계별 지시와 도구 결과만 본다 | **다름** |
| 2 도구 granularity | `read_file(path)`, `count_pattern(path, pattern)` | 동일 (같은 모듈 import) | 같음 (통제변수) |
| 3 종료 조건 | 모델이 도구 호출 없이 답하면 종료, 아니면 `max_steps=8` 상한 | 계획의 단계 수가 실행 횟수를 결정. 단계당 도구 라운드는 `max_tool_rounds=3`, 마지막에 최종 답변 강제 호출 1회. 종료 시점이 모델 판단이 아니라 구조로 정해진다 | **다름** |
| 4 에러 복구 | 도구 예외를 Observation(`error: ...`)으로 되돌려 다음 스텝에서 모델이 재판단. 매 스텝 방향 전환 가능 | 도구 예외 처리는 공유(동일)하되, 계획 이탈은 `OFF_PLAN` → 재계획 **1회**(`max_replan=1`)로 상한. 추가로 계획 JSON 파싱 실패라는 고유 실패 모드가 있다 | **다름** |
| 5 개입 지점 | `IRREVERSIBLE = set()` — 비어 있어 승인 절차가 발동하지 않음 | 개입 훅 자체가 없음 | 같음 (둘 다 0) |

요소 5에 대한 단서: 스타터 도구는 `read_file`, `count_pattern` 둘 다 읽기
전용이라 되돌릴 수 없는 행동이 없다. 따라서 `interventions` 지표는 두 하네스
모두 구조적으로 0이 되며, 이 지표로는 하네스가 갈리지 않는다. 이 지표를 살리려면
쓰기/삭제 도구를 양쪽에 똑같이 추가하고 `IRREVERSIBLE`에 등록해야 하는데, 그러면
도구 집합이 바뀌어 통제변수가 흔들린다. 이번 실험에서는 0으로 고정된 지표로
보고한다.

## 2. 측정표

<!-- results.csv 6행 이상. 실행 후 채운다. -->
_실행 대기 중 — API 키가 준비되면 `python run_ab.py --runs 3`로 6회 실행하고 이 표를 `results.csv`에서 채운다._

## 3. 해석

<!-- 어느 요소의 차이가 어느 지표를 움직였는지, logs/의 근거를 들어 한 문단. -->
_실행 대기 중 — 실측 데이터 없이는 쓰지 않는다._
