# Week 02 — 하네스 A/B 실험 보고서

ReAct형 vs Plan-then-Execute형. 모델·태스크·도구를 고정하고 하네스만 독립변수로 바꿨다.

---

## 1부. 변형 정의

### 1-1. 고정한 조건 (통제 변인)

| 항목 | 값 |
|---|---|
| provider | OpenAI 호환 (`ANTHROPIC_API_KEY` 미설정 → `tools_shared.py:89`가 openai 경로 선택) |
| 모델 | `gpt-4o-mini` (`AGENT_MODEL` 미설정 시 기본값, `tools_shared.py:90`) |
| 도구 | `read_file(path)`, `count_pattern(path, pattern)` — 두 하네스가 `tools_shared.py`에서 동일하게 import |
| 도구 스키마 | `TOOL_SPECS` 하나를 공유. `Chat`이 provider별로 변환 |
| 태스크 | `app.log`에서 ERROR가 가장 많은 시간대 (`TASK.md`의 `task:` 줄) |
| 성공 기준 | 최종 답변에 `14:00`이 포함되면 O (`TASK.md`의 `expected:` 줄, 실행 전 커밋 `68bfead`) |
| 입력 | `app.log` 60줄, ERROR 19건. 변경 없이 사용 |
| 실행 횟수 | 하네스당 3회 (`run_ab.py --runs 3`) |
| 컨텍스트 상한 | `read_file`이 4000자에서 절단 (`tools_shared.py:27`) — 양쪽 동일 |

`run_ab.py`는 `fn(task, log=log)`로만 호출하므로 두 하네스의 나머지 파라미터는 모두 기본값이다.

### 1-2. 시스템 프롬프트 (하네스별로 다름)

**ReAct** — 하나:
> "You solve tasks with the tools you are given. Before every tool call, write one line that starts with 'Thought:' ... When the task is complete, reply with a line that starts with 'Answer:' and make no tool call."

**Plan-then-Execute** — 둘:
> `SYSTEM_PLAN`: "You are a planner. Reply with a JSON list of short strings, one per step, and nothing else."
>
> `SYSTEM_EXEC`: "You execute one step of a plan at a time ... If the step cannot be done as planned, reply with a line that starts with 'OFF_PLAN:' ..."

ReAct만 `Thought:`를 요구한다. Plan-then-Execute는 추론을 텍스트로 남기게 하지 않는다.

### 1-3. 다섯 요소를 어디에 어떻게 다르게 잡았나

| 축 | ReAct | Plan-then-Execute | 다른가 |
|---|---|---|---|
| **1. 컨텍스트 관리** | `Chat` **1개**. 시스템 프롬프트 + 전체 히스토리를 매 호출마다 재전송. 요약·삭제 없음 | `Chat` **2개** (`planner`는 `tools=False`, `executor`). 두 대화가 히스토리를 **공유하지 않고** 각자 누적 | **O** |
| **2. 도구 granularity** | `read_file`, `count_pattern` 2개 | 동일 | **X — 통제 변인** |
| **3. 종료 조건** | 상한 **1개**: `max_steps=8`. 그 외에는 모델이 도구를 부르지 않으면 종료 | 상한 **4개**: 계획 길이 / `max_tool_rounds=3` (스텝당 툴 왕복) / `max_replan=1` / 계획 JSON 파싱 실패 시 즉시 종료 | **O** |
| **4. 에러 복구** | 도구 예외를 문자열로 되돌려 Observation으로 만든다 (`tools_shared.py:189`). 모델이 다음 스텝에서 처리 | 위와 동일 + `OFF_PLAN` 신호 시 재계획. **단 1회** | **O** |
| **5. 인간 개입 지점** | 메커니즘 있음 (`IRREVERSIBLE` + `ask_human`). 그러나 `IRREVERSIBLE = set()`이 비어 있어 **한 번도 발동하지 않음** | 메커니즘 **자체가 없음** | 구조는 다르나 **측정 안 됨** |

**실제로 비교된 축은 1, 3, 4 셋이다.** 축 2는 의도적으로 고정했고, 축 5는 양쪽 모두 `interventions=0`으로 기록되어 측정에 실패했다.

### 1-4. 이 실험의 한계 (정직한 기록)

1. **축 5가 측정되지 않았다.** starter의 도구가 둘 다 읽기 전용이라 `IRREVERSIBLE`에 넣을 대상이 없었다. 쓰기 도구를 추가하지 않는 한 이 축은 0 대 0으로 남는다.
2. **로그의 Observation이 절단된다.** `tools_shared.py:191`이 도구 출력을 200자에서 자르고 줄바꿈을 `|`로 치환한다. `count_pattern`은 숫자라 온전하지만 `read_file` 출력은 온전하지 않다. Plan-then-Execute의 스텝 텍스트도 300자에서 잘린다 (`harness_plan_execute.py:69`).
3. **Plan-then-Execute 로그에는 `Thought:`가 한 줄도 없다.** 기록 누락이 아니라 `SYSTEM_EXEC`가 요구하지 않아서다. 즉 같은 모델을 써도 하네스에 따라 추적 가능한 사고 기록의 유무가 갈린다.
4. **표본이 하네스당 3회다.** 최소 요구치이며, 성공률 차이(3/3 vs 2/3)를 통계적으로 주장할 수 있는 크기는 아니다.

---

## 2부. 측정표

`results.csv` 전문:

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | O | 3,887 | 3 | 0 | |
| 2 | react | O | 3,722 | 3 | 0 | |
| 3 | react | O | 7,160 | 4 | 0 | |
| 4 | plan_exec | O | 119,302 | 19 | 0 | replans=1 |
| 5 | plan_exec | **X** | 40,414 | 19 | 0 | replans=1 |
| 6 | plan_exec | O | 33,954 | 18 | 0 | replans=1 |

집계:

| | 성공 | 평균 토큰 | 평균 반복 | 평균 개입 |
|---|:---:|---:|---:|:---:|
| react | **3/3** | **4,923** | **3.3** | 0 |
| plan_exec | 2/3 | 64,557 | 18.7 | 0 |
| 배율 (plan_exec / react) | — | **13.1배** | **5.6배** | — |

`plan_exec`는 3회 모두 `replans=1`로, 매번 재계획 상한을 다 썼다.

---

## 3부. 해석

<!-- 여기를 직접 쓴다. 한 문단.

답해야 할 것: 어느 하네스가 어떤 지표로 이겼고, **왜** 그런가.
"ReAct가 이겼다"가 아니라 "1부의 어느 축 차이가 2부의 어느 숫자를 만들었나".

로그에서 근거를 찾을 지점:

(1) iters 3.3 vs 18.7
    logs/react-01.txt — 한 step 안에 count_pattern이 몇 개 호출됐나?
    logs/plan_exec-06.txt — 같은 일에 모델 호출이 몇 번 필요했나?
    harness_plan_execute.py:38 의 max_tool_rounds=3 과 시간대 9개를 나란히 놓고 볼 것.
    로그에 'OFF_PLAN: step exceeded the tool-call budget'이 몇 번 나오나?
    → 어느 축인가?

(2) tokens 4,923 vs 64,557 (13배)
    harness_plan_execute.py:42, 53 — Chat이 몇 개이고 각자 무엇을 쌓나?
    harness_react.py:30 — 몇 개인가?
    → 어느 축인가?

(3) success 3/3 vs 2/3   ← 가장 좋은 소재
    두 하네스의 모델이 같은 종류의 실수(정규식 오류로 카운트가 전부 0)를 했다.
    logs/react-03.txt  — 0이 나온 뒤 모델이 무엇을 했나?
    logs/plan_exec-05.txt — 0이 나온 뒤 무엇을 했나? 최종 답변은?
    harness_plan_execute.py:37 의 max_replan=1 도 함께 볼 것.
    → 같은 모델, 같은 도구, 같은 실수. 구조의 무엇이 회복과 실패를 갈랐나?

-->
