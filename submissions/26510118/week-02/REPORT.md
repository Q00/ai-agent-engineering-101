# Week 02 — 하네스 A/B 실험 보고서

ReAct형 vs Plan-then-Execute형. 모델·태스크·도구를 고정하고 하네스만 독립변수로 바꿨다.
starter를 그대로 쓰지 않고, 로그에서 확인한 교란 요인을 제거한 뒤 비교했다.

## 1. 변형 정의

### 고정한 조건

| 항목 | 값 |
|---|---|
| provider / 모델 | OpenAI 호환, `gpt-4o-mini` (`ANTHROPIC_API_KEY` 미설정 → `tools_shared.py:89-92`) |
| 도구 | `read_file(path)`, `count_pattern(path, pattern)` — 두 하네스가 `tools_shared.py`에서 동일하게 import. 스키마도 `TOOL_SPECS` 하나를 공유 |
| 태스크 / 성공 기준 | `app.log`의 ERROR 최다 시간대. 최종 답변에 `14:00`이 포함되면 O (`TASK.md`, 실행 전 커밋 `68bfead`) |
| 입력 | `app.log` 60줄 / ERROR 19건, 변경 없음. `read_file`은 4000자에서 절단 |
| 실행 | `python run_ab.py --runs 3`. 하네스 파라미터는 전부 기본값 |

### 시스템 프롬프트 (전문)

**ReAct — 하나:**
> You solve tasks with the tools you are given. Before every tool call, write one line that starts with 'Thought:' saying what you know and what you will do next. When the task is complete, reply with a line that starts with 'Answer:' and make no tool call.

**Plan-then-Execute — 둘:**
> `SYSTEM_PLAN`: You are a planner. Reply with a JSON list of short strings, one per step, and nothing else. No prose, no code fences. **Every step must be a call to one of the tools listed in the message, written with that exact function name. Do not invent function names, and do not add steps for reasoning, summarising, or formatting the answer.**

> `SYSTEM_EXEC`: You execute one step of a plan at a time with the tools you are given. If the step cannot be done as planned, reply with a line that starts with 'OFF_PLAN:' and explain why. When asked for the final answer, reply with a line that starts with 'Answer:'.

굵은 부분이 starter에 없던 변주다 (아래 ①). 나머지는 starter와 동일하다.

### 다섯 요소를 어디에 어떻게 다르게 잡았나

| 축 | ReAct | Plan-then-Execute | |
|---|---|---|---|
| **1 컨텍스트 관리** | `Chat` 1개. 전체 히스토리를 매 호출 재전송 | `Chat` 2개 (`planner`는 `tools=False`, `executor`). 두 대화가 히스토리를 공유하지 않고 각자 누적 | 다름 |
| **2 도구 granularity** | 도구 2개 | 동일 | **통제 변인** |
| **3 종료 조건** | 상한 1개: `max_steps=8` | 상한 4개: 계획 길이 / `max_tool_rounds=3` / `max_replan=1` / 계획 JSON 파싱 실패 | 다름 |
| **4 에러 복구** | 도구 예외를 Observation으로 되돌림 | 위와 동일 + `OFF_PLAN` 시 재계획 1회 | 다름 |
| **5 인간 개입** | `IRREVERSIBLE` + `ask_human` 있으나 집합이 비어 미발동 | 메커니즘 없음 | 측정 안 됨 |

### 내가 준 변주와 그 근거

starter 6회(run 1-6)를 먼저 돌리고 로그를 읽은 뒤 설계했다. 실행 순서는 아래와 같다.
`results.csv`의 run 번호는 19-24가 뒤에 붙어 있지만 **시간 순서로는 두 번째**다. 기존
번호와 겹치지 않게 다시 매긴 것이며, 각 행의 `note`에 원래 번호와 로그 파일명을 적었다.

```
1  run 1-6     starter 그대로
2  run 19-24   기각한 변주 ③ (원래 번호 7-12)
3  run 7-12    변주 ① 적용 — 최종 설계
4  run 13-18   변주 ② 추가 — 기각
```

**① 채택 — `SYSTEM_PLAN`에 도구 제약 추가**

starter 6회 전부, 계획의 **60~71%가 실행 불가능한 단계**였다. `extract_hour_from_timestamp()`, `count_errors_per_hour()` 같은 존재하지 않는 함수다. planner가 `tools=False`로 돌아 도구 스키마를 보지 못하기 때문이다. 이대로면 A/B가 "계획이라는 전략"이 아니라 "도구를 모르는 planner"를 재게 된다. 전략 비교를 위해 제거해야 할 교란이라고 판단했다.

**② 기각 — `max_tool_rounds` 3→9**

①을 적용하자 병목이 스텝당 도구 예산으로 옮겨가(3회 전부 `step exceeded the tool-call budget`) 9로 올려봤다. 결과는 반대였다. ①이 모든 단계를 도구 호출로 강제하므로 재계획이 시간대를 24단계로 나열했고, 커진 예산 덕에 실패할 단계마다 9회씩 소진했다(`logs/plan_exec-18.txt`). 평균 15,498 → 548,644 토큰. 되돌렸다.

**③ 기각 — 하네스별 상한 조정 (run 19-24)**

starter 6회를 읽고 처음 세운 방향이다. `run_variant.py`로 하네스 파일은 건드리지 않은 채
plan_exec의 `max_tool_rounds`를 3→9로 올리고, react의 `max_steps`를 8→4로 내렸다.
두 이유로 버렸다.

첫째, **두 팔을 서로 다른 방향으로 움직였다.** 한쪽은 예산을 늘리고 한쪽은 줄였으니 결과가
갈려도 하네스 차이인지 상한 차이인지 말할 수 없다. 이 과제가 요구하는 것은 두 하네스의
A/B인데, 각 하네스를 자기 자신의 기본값과 비교한 꼴이 됐다.

둘째, **변주 ① 이전에 돌린 숫자다.** `logs/plan_exec-07.txt`의 계획에
`extract_hour_from_timestamps(app.log)`, `group_error_counts_by_hour()` 같은 존재하지 않는
함수가 그대로 들어 있다. planner 교란이 남은 상태라 최종 비교에 쓸 수 없다.

| 하네스 | 조건 | 성공 | 평균 토큰 | 평균 반복 | starter 대비 |
|---|---|:---:|---:|---:|---|
| plan_exec | `max_tool_rounds=9` | 3/3 | 126,172 | 22.67 | 토큰 2배, 한 회는 245,529 |
| react | `max_steps=4` | 3/3 | 4,361 | 3.33 | 사실상 동일 |

react 쪽은 상한이 구속하지 않았다. starter에서도 3~4회면 끝나므로 8을 4로 내려도 달라질 것이
없었다. plan_exec 쪽 아이디어만 변주 ①을 적용한 뒤 변주 ②로 다시 시도했고, 그때는 명확히
역효과였다(run 13-18).

이 6회와 `run_variant.py`는 `bcbaf48`에서 지웠다가 `141d4ee`·`4d8fb01`로 복원했다. 기각한
시도도 채점 근거이므로 지우지 말았어야 했다. 로그는 원본 파일명 그대로, 내용은 손대지 않았다.

### 이 실험의 한계

1. **축 5 미측정** — 도구가 둘 다 읽기 전용이라 `IRREVERSIBLE`에 넣을 대상이 없었다. 양쪽 `interventions=0`.
2. **로그 Observation 절단** — `tools_shared.py:191`이 도구 출력을 200자에서 자른다. `count_pattern`은 숫자라 온전하지만 `read_file` 출력은 아니다.
3. **plan_exec 로그에 `Thought:` 0줄** — `SYSTEM_EXEC`가 요구하지 않아서다. 하네스에 따라 추적 가능한 사고 기록의 유무가 갈린다.
4. **표본이 블록당 3회** — react는 코드를 바꾸지 않았는데도 9회 중 1회 실패했다(run 13). 3회는 성공률을 주장하기에 작다.

## 2. 측정표

네 블록을 돌렸다. **B가 최종 설계**이고, A는 스펙이 요구하는 starter 기본값, C와 D는 기각한
변주다. 순서 열은 실제 실행 순서다.

| 순서 | 블록 | 하네스 | 조건 | 성공 | 평균 토큰 | 평균 반복 |
|:---:|---|---|---|:---:|---:|---:|
| 1 | A (run 1-6) | react | starter | 3/3 | 4,923 | 3.33 |
| | | plan_exec | starter | 2/3 | 64,557 | 18.67 |
| 2 | D (run 19-24) | react | 변주 ③ | 3/3 | 4,361 | 3.33 |
| | | plan_exec | 변주 ③ | 3/3 | 126,172 | 22.67 |
| 3 | **B (run 7-12)** | **react** | **변경 없음** | **3/3** | **4,328** | **3.33** |
| | | **plan_exec** | **변주 ①** | **2/3** | **15,498** | **10.00** |
| 4 | C (run 13-18) | react | 변경 없음 | 2/3 | 6,889 | 4.67 |
| | | plan_exec | 변주 ①+② | 2/3 | 548,644 | 116.00 |

D는 변주 ① 이전이라 plan_exec 숫자에 planner 교란이 남아 있다. A와는 비교되지만 B·C와는
같은 선에 놓을 수 없다.

`results.csv` 전문:

| run | harness | ok | tokens | iters | int | note |
|---:|---|:---:|---:|---:|:---:|---|
| 1 | react | O | 3,887 | 3 | 0 | |
| 2 | react | O | 3,722 | 3 | 0 | |
| 3 | react | O | 7,160 | 4 | 0 | |
| 4 | plan_exec | O | 119,302 | 19 | 0 | replans=1 |
| 5 | plan_exec | **X** | 40,414 | 19 | 0 | replans=1 |
| 6 | plan_exec | O | 33,954 | 18 | 0 | replans=1 |
| 7 | react | O | 5,626 | 4 | 0 | |
| 8 | react | O | 3,701 | 3 | 0 | |
| 9 | react | O | 3,657 | 3 | 0 | |
| 10 | plan_exec | O | 10,056 | 8 | 0 | replans=1 |
| 11 | plan_exec | O | 17,394 | 11 | 0 | replans=1 |
| 12 | plan_exec | **X** | 19,045 | 11 | 0 | replans=1 |
| 13 | react | **X** | 13,241 | 8 | 0 | |
| 14 | react | O | 3,715 | 3 | 0 | |
| 15 | react | O | 3,712 | 3 | 0 | |
| 16 | plan_exec | O | 177,015 | 58 | 0 | replans=1 |
| 17 | plan_exec | O | 145,908 | 45 | 0 | replans=1 |
| 18 | plan_exec | **X** | 1,323,009 | 245 | 0 | replans=1 |
| 19 | plan_exec | O | 95,610 | 18 | 0 | max_tool_rounds=9 · 원래 run 7 · `plan_exec-07.txt` |
| 20 | plan_exec | O | 37,378 | 18 | 0 | max_tool_rounds=9 · 원래 run 8 · `plan_exec-08.txt` |
| 21 | plan_exec | O | 245,529 | 32 | 0 | max_tool_rounds=9 · 원래 run 9 · `plan_exec-09.txt` |
| 22 | react | O | 3,757 | 3 | 0 | max_steps=4 · 원래 run 10 · `react-10.txt` |
| 23 | react | O | 5,585 | 4 | 0 | max_steps=4 · 원래 run 11 · `react-11.txt` |
| 24 | react | O | 3,740 | 3 | 0 | max_steps=4 · 원래 run 12 · `react-12.txt` |

run 19-24는 기각한 변주 ③이며 시간 순으로는 run 1-6 바로 다음이다. 로그 파일명은 원래
번호를 그대로 쓴다. 로그를 고치지 않기 위해서이며, 대응은 위 `note` 열과 `results.csv`에
적혀 있다.

변주 ①의 직접 효과: 계획 중 실행 불가능한 단계가 **60~71% → 0%** (`logs/plan_exec-10~12.txt`).

## 3. 해석

최종 비교에서는 ReAct가 성공률 3/3, 평균 4,328토큰, 3.33회 반복으로 Plan-then-Execute의 2/3, 15,498토큰, 10.00회 반복보다 앞섰다. 다만 **축 1(컨텍스트 관리)** 쪽에서 계획 단계가 실제 제공된 도구만 사용하도록 제약을 추가하자, 실행 불가능한 계획이 사라지고(60~71% → 0%, `logs/plan_exec-10~12.txt`) 토큰 사용량도 64,557에서 15,498로 줄었다. 이는 초기 성능 격차의 상당 부분이 두 전략 자체의 차이보다는 planner가 도구 정보를 제대로 반영하지 못한 구현상의 문제에서 발생했음을 보여준다. 그럼에도 Plan-then-Execute의 성공률과 효율성이 여전히 낮았는데, 이는 계획을 먼저 고정하고 재계획을 1회로 제한하는 **축 3·4(종료 조건과 에러 복구)** 구조가 실행 중 오류에 유연하게 대응하기 어렵기 때문으로 해석할 수 있다. 실제로 run 12는 예산 초과로 두 번 이탈한 뒤 재계획 기회를 모두 쓰고 "모든 시간대의 ERROR가 0"이라는 오답으로 종료했다(`logs/plan_exec-12.txt`). 또한 **축 3**의 도구 호출 허용 횟수를 3에서 9로 늘렸을 때 24단계 계획과 245회 호출, 1,323,009토큰까지 비용이 급증해(`logs/plan_exec-18.txt`) 하네스의 설계 요소들이 서로 독립적이지 않다는 점도 확인됐다. 다만 ReAct 역시 동일한 코드에서 한 차례 실패했으므로(run 13), 이번 결과는 성공률의 우열을 단정하기보다 각 하네스의 효율성과 실패 양상을 비교한 결과로 보는 것이 적절하다.
