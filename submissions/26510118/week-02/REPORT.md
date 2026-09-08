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

이 태스크에서는 ReAct가 세 지표 모두 앞섰지만(성공 3/3 대 2/3, 반복 3.33 대 18.67, 토큰 4,923 대 64,557) 지표마다 원인이 다르다. **성공률을 가른 것은 에러 복구(축 4)이고 종료 조건(축 3)이 겹친다.** 두 하네스의 모델이 모두 정규식을 잘못 써 카운트가 전부 0으로 돌아온 구간이 있었는데(`logs/react-03.txt`, `logs/plan_exec-05.txt`), ReAct는 그 0을 Observation으로 받아 같은 루프 안에서 패턴을 바꿔 복구했고, Plan-then-Execute는 미리 굳은 계획과 `max_replan=1`이라는 유연성 상한 때문에 방향을 틀지 못한 채 "ERROR 줄이 없다"는 답으로 종료했다. **반복 횟수를 늘린 것은 종료 조건(축 3)이다.** 스텝당 도구 호출을 3라운드로 묶은 `max_tool_rounds=3`이 시간대 9개를 세야 하는 이 태스크와 맞지 않아 `OFF_PLAN`과 재계획이 반복됐고, 3회 실행 모두 재계획 상한을 다 썼다. **토큰 차이는 컨텍스트 관리(축 1)와 그 반복 증가가 겹친 결과다.** Plan-then-Execute는 `planner`와 `executor` 두 Chat이 히스토리를 따로 누적하므로 반복이 늘 때 양쪽 컨텍스트가 함께 커지고, 그래서 토큰 격차(13.11배)가 반복 격차(5.60배)보다 크다. 같은 모델과 같은 도구를 써도 하네스가 정한 유연성 상한, 스텝 예산, 컨텍스트 분할 방식이 성공률과 비용을 직접 결정했다.

