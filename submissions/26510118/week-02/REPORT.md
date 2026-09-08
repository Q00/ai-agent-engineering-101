# Week 02 — 하네스 A/B 실험 보고서

ReAct형 vs Plan-then-Execute형. 모델·태스크·도구를 고정하고 하네스만 독립변수로 바꿨다.
코드는 `weeks/week-02/starter/`를 **수정 없이** 사용했다 (해시 동일).

## 1. 변형 정의

### 고정한 조건

| 항목 | 값 |
|---|---|
| provider / 모델 | OpenAI 호환, `gpt-4o-mini` (`ANTHROPIC_API_KEY` 미설정 → `tools_shared.py:89-92`) |
| 도구 | `read_file(path)`, `count_pattern(path, pattern)` — 두 하네스가 `tools_shared.py`에서 동일하게 import. 스키마도 `TOOL_SPECS` 하나를 공유 |
| 태스크 / 성공 기준 | `app.log`의 ERROR 최다 시간대. 최종 답변에 `14:00`이 포함되면 O (`TASK.md`, 실행 전 커밋 `68bfead`) |
| 입력 | `app.log` 60줄 / ERROR 19건, 변경 없음. `read_file`은 4000자에서 절단 (`tools_shared.py:27`) |
| 실행 | 하네스당 3회 (`python run_ab.py --runs 3`). 하네스 파라미터는 전부 기본값 — `run_ab.py`가 `fn(task, log=log)`로만 호출한다 |

### 시스템 프롬프트 (전문)

**ReAct — 하나:**

> You solve tasks with the tools you are given. Before every tool call, write one line that starts with 'Thought:' saying what you know and what you will do next. When the task is complete, reply with a line that starts with 'Answer:' and make no tool call.

**Plan-then-Execute — 둘:**

> `SYSTEM_PLAN`: You are a planner. Reply with a JSON list of short strings, one per step, and nothing else. No prose, no code fences.

> `SYSTEM_EXEC`: You execute one step of a plan at a time with the tools you are given. If the step cannot be done as planned, reply with a line that starts with 'OFF_PLAN:' and explain why. When asked for the final answer, reply with a line that starts with 'Answer:'.

ReAct만 `Thought:`를 요구한다. Plan-then-Execute는 추론을 텍스트로 남기게 하지 않는다.

### 다섯 요소를 어디에 어떻게 다르게 잡았나

| 축 | ReAct | Plan-then-Execute | |
|---|---|---|---|
| **1 컨텍스트 관리** | `Chat` **1개**. 시스템 프롬프트 + 전체 히스토리를 매 호출 재전송. 요약·삭제 없음 (`harness_react.py:30`) | `Chat` **2개** (`planner`는 `tools=False`, `executor`). 두 대화가 히스토리를 **공유하지 않고** 각자 누적 (`:42`, `:53`) | 다름 |
| **2 도구 granularity** | 도구 2개 | 동일 | **통제 변인** |
| **3 종료 조건** | 상한 **1개**: `max_steps=8`. 그 외엔 모델이 도구를 부르지 않으면 종료 | 상한 **4개**: 계획 길이 / `max_tool_rounds=3` (스텝당 툴 왕복) / `max_replan=1` / 계획 JSON 파싱 실패 시 즉시 종료 (`:37-38`, `:47`) | 다름 |
| **4 에러 복구** | 도구 예외를 문자열로 되돌려 Observation으로 만든다 (`tools_shared.py:189`) | 위와 동일 + `OFF_PLAN` 시 재계획, **단 1회** | 다름 |
| **5 인간 개입** | 메커니즘 있음 (`IRREVERSIBLE` + `ask_human`) 그러나 `IRREVERSIBLE = set()`이 비어 **미발동** | 메커니즘 **자체가 없음** | 측정 안 됨 |

**실제로 비교된 축은 1, 3, 4 셋이다.** 축 2는 의도적으로 고정했고, 축 5는 양쪽 `interventions=0`으로 측정에 실패했다.

### 이 실험의 한계

1. **축 5 미측정** — starter 도구가 둘 다 읽기 전용이라 `IRREVERSIBLE`에 넣을 대상이 없었다.
2. **로그 Observation 절단** — `tools_shared.py:191`이 도구 출력을 200자에서 자르고 줄바꿈을 `|`로 치환한다. `count_pattern`은 숫자라 온전하지만 `read_file` 출력은 온전하지 않다.
3. **plan_exec 로그에 `Thought:` 0줄** — 누락이 아니라 `SYSTEM_EXEC`가 요구하지 않아서다. 같은 모델이라도 하네스에 따라 추적 가능한 사고 기록의 유무가 갈린다.
4. **표본 하네스당 3회** — 최소 요구치. 성공률 3/3 대 2/3을 통계적으로 주장할 크기는 아니다.

## 2. 측정표

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | O | 3,887 | 3 | 0 | |
| 2 | react | O | 3,722 | 3 | 0 | |
| 3 | react | O | 7,160 | 4 | 0 | |
| 4 | plan_exec | O | 119,302 | 19 | 0 | replans=1 |
| 5 | plan_exec | **X** | 40,414 | 19 | 0 | replans=1 |
| 6 | plan_exec | O | 33,954 | 18 | 0 | replans=1 |

| | 성공 | 평균 토큰 | 평균 반복 | 평균 개입 |
|---|:---:|---:|---:|:---:|
| react | **3/3** | **4,923** | **3.33** | 0 |
| plan_exec | 2/3 | 64,557 | 18.67 | 0 |
| 배율 | — | **13.11배** | **5.60배** | — |

`plan_exec`는 3회 모두 `replans=1`로 재계획 상한을 매번 다 썼다.

## 3. 해석

<!-- 여기를 직접 쓴다. 한 문단.

답할 것: 1부의 어느 축 차이가 2부의 어느 숫자를 만들었나.
"ReAct가 이겼다"가 아니다.

로그에서 근거를 찾을 지점:

(1) iters 3.33 vs 18.67
    logs/react-01.txt — 한 step 안에 count_pattern이 몇 개 호출됐나?
    logs/plan_exec-06.txt — 같은 일에 모델 호출이 몇 번 필요했나?
    harness_plan_execute.py:38 의 max_tool_rounds=3 과 시간대 9개를 나란히 볼 것.
    로그에 'OFF_PLAN: step exceeded the tool-call budget'이 몇 번 나오나?

(2) tokens 4,923 vs 64,557 (13배)
    harness_plan_execute.py:42, 53 — Chat이 몇 개이고 각자 무엇을 쌓나?
    harness_react.py:30 — 몇 개인가?

(3) success 3/3 vs 2/3   ← 가장 좋은 소재
    두 하네스의 모델이 같은 종류의 실수(정규식 오류로 카운트 전부 0)를 했다.
    logs/react-03.txt   — 0이 나온 뒤 모델이 무엇을 했나?
    logs/plan_exec-05.txt — 0이 나온 뒤 무엇을 했나? 최종 답변은?
    harness_plan_execute.py:37 의 max_replan=1 도 함께 볼 것.
    같은 모델, 같은 도구, 같은 실수. 구조의 무엇이 회복과 실패를 갈랐나?

-->
