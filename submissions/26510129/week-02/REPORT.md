# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

학번 26510129

## 0. 실행 조건 (재현용)

| 항목 | 값 |
|---|---|
| Provider | OpenRouter (OpenAI 호환 API) |
| 모델 | `nvidia/nemotron-3.5-lightning:free` |
| 환경변수 | `OPENAI_BASE_URL=https://openrouter.ai/api/v1`, `OPENAI_API_KEY=<openrouter key>`, `AGENT_MODEL=nvidia/nemotron-3.5-lightning:free` |
| 실행 | `python run_ab.py --runs 3` 후 부족분 보충 실행. 총 9 run (react 5, plan_exec 4) |
| 코드 | `weeks/week-02/starter/` 무변경. `tools_shared.py`, `harness_react.py`, `harness_plan_execute.py`, `run_ab.py` 모두 starter와 동일 |
| 태스크 | `TASK.md` — app.log에서 ERROR 라인이 가장 많은 시간대. `expected: 14:00` |
| 툴 | `read_file(path)` 파일 앞 4000자 반환 / `count_pattern(path, pattern)` 정규식 매칭 라인 수 반환. 둘 다 cwd 밖 경로 거부 |
| ReAct 설정 | `max_steps=8`, `IRREVERSIBLE=set()` |
| Plan-then-Execute 설정 | `max_replan=1`, `max_tool_rounds=3` |

기록 사항. 첫 커밋(`6f41880`)이 run 1 시작 직후에 이루어져 `react-01.txt`와 `results.csv` 1행이 함께 들어갔다. `TASK.md`는 그 전후로 바뀌지 않았다. 첫 실행이 plan_exec 도중 중단돼 재실행했고, 그 결과 run 번호가 react 1–4, 8 / plan_exec 5–7, 9로 섞였다. 모든 run은 `logs/`에 남아 있다.

## 1. 변형 정의

모델, 태스크, 툴을 고정하고 harness만 바꿨다. 다섯 축 기준으로 두 harness가 같은 것과 다른 것:

| 축 | ReAct (`harness_react.py`) | Plan-then-Execute (`harness_plan_execute.py`) | 차이 |
|---|---|---|---|
| 1 컨텍스트 관리 | 대화 하나. 매 호출에 전체 히스토리(Thought, 툴 호출, Observation) 재전송 | planner 대화와 executor 대화 분리. executor에는 계획 전체 + 모든 스텝의 툴 결과와 응답이 누적되어 매 호출 재전송 | **다름** |
| 2 툴 granularity | `tools_shared.TOOL_SPECS` 2개 | 동일 | 같음 |
| 3 종료 조건 | (a) 툴 호출 없는 응답이 오면 그 텍스트를 답으로 반환, (b) `max_steps=8` 소진 | (a) 계획 JSON 파싱 실패 시 즉시 종료, (b) 계획 스텝을 모두 소진하면 "Answer:로 답하라"를 한 번 더 요청하고 종료, (c) 스텝이 `OFF_PLAN`이면 재계획 1회 | **다름** |
| 4 에러 복구 | 툴 예외 → `error: ...` 문자열을 Observation으로 반환 (`Chat.run_tools`) | 동일 + `OFF_PLAN` → planner에게 남은 계획 재요청 (`max_replan=1`), 스텝당 툴 3라운드 초과 시 강제 `OFF_PLAN` | 설계는 다르나 이번 실행에서 추가 층은 미발동 |
| 5 인간 개입 | `IRREVERSIBLE`이 빈 집합. 읽기 전용 툴이라 개입 지점 없음 | 개입 지점 없음 | 같음 (둘 다 interventions=0) |

정리하면 실질적으로 움직인 축은 **1 컨텍스트 관리**와 **3 종료 조건** 둘이다.

## 2. 측정

`results.csv` 전체:

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---|---|---|---|
| 1 | react | O | 4161 | 2 | 0 | |
| 2 | react | O | 3808 | 2 | 0 | |
| 3 | react | X | 6546 | 3 | 0 | |
| 4 | react | O | 3626 | 2 | 0 | |
| 5 | plan_exec | O | 51075 | 11 | 0 | replans=0 |
| 6 | plan_exec | O | 30062 | 10 | 0 | replans=0 |
| 7 | plan_exec | O | 28500 | 10 | 0 | replans=0 |
| 8 | react | O | 3197 | 2 | 0 | |
| 9 | plan_exec | O | 50026 | 11 | 0 | replans=0 |

집계:

| harness | n | 성공 | tokens 평균 (min–max) | iters 평균 (min–max) | 소요 시간 |
|---|---|---|---|---|---|
| react | 5 | 4/5 | 4,268 (3,197–6,546) | 2.2 (2–3) | 28–90 s |
| plan_exec | 4 | 4/4 | 39,916 (28,500–51,075) | 10.5 (10–11) | 287–470 s |

## 3. 해석

이 태스크에서 ReAct는 토큰(9.4배 적음), 반복 횟수(4.8배 적음), 시간에서 이겼고, Plan-then-Execute는 성공률(4/4 vs 4/5)에서 앞섰다. 차이를 만든 것은 종료 조건 축과 컨텍스트 관리 축이다.

**종료 조건 → iters.** plan_exec 네 run 모두 `[step 1]`에서 이미 `Answer: 14:00`을 냈다 (`logs/plan_exec-05.txt`, `-06`, `-07`, `-09`). 모델은 파일을 한 번 읽고 답을 알았지만 harness의 종료 조건이 "답이 나왔는가"가 아니라 "계획 스텝을 다 밟았는가"라서 남은 4–5스텝을 계속 실행했고, 그 스텝들의 응답은 전부 `Answer: 14:00` 반복이었다. iters 2 → 10의 차이는 거의 전부 이 낭비다. ReAct는 모델이 툴 호출을 멈추는 순간 끝나므로 "읽기 1회 + 답 1회 = 2"로 수렴했다.

**컨텍스트 관리 → tokens.** iters는 4.8배인데 tokens는 9.4배다. executor 대화에 `read_file` 결과(4000자)와 각 스텝의 응답이 누적되고 매 호출마다 전부 재전송되기 때문에 호출당 비용이 뒤로 갈수록 커진다. `plan_exec-05`와 `plan_exec-09`는 step 1에서 `read_file`을 두 번 불러 컨텍스트에 파일이 두 벌 들어갔고, 그 결과 50–51k 토큰으로 한 번만 읽은 두 run(28–30k)보다 1.7배 컸다. 같은 계획·같은 모델에서 툴 호출 한 번의 차이가 총 비용을 1.7배 갈랐다. 같은 누적 구조가 ReAct에도 있지만 호출이 2번이라 드러나지 않는다.

**종료 조건 → 성공률.** 유일한 실패 `react-03`은 모델이 `count_pattern(pattern='00:.*ERROR')`이라는 잘못된 정규식으로 0을 받은 뒤 출력이 붕괴한 경우다 (`[step 3] ... HH HH count count ... incapaz hospitalized ...`). 툴 호출이 없었으므로 ReAct 종료 조건 (a)가 이 붕괴 텍스트를 "답"으로 받아 종료했다. `Answer:`로 시작하는지만 검사했어도 재시도 기회가 있었다. 반면 plan_exec는 마지막에 "Answer:로 시작해 답하라"를 별도 요청하는 단계가 있어 같은 붕괴에도 한 번 더 기회가 있는 구조다. 다만 n=4에서 그 단계가 실제로 실패를 구제한 사례는 없었으므로 성공률 차이가 구조 덕인지 표본 운인지는 이 데이터로 단정할 수 없다.

**에러 복구 축은 기여하지 않았다.** 8 run에서 툴 예외(`error:`)가 한 번도 없었고 `replans=0`으로 OFF_PLAN 경로도 열리지 않았다. react-03의 `count_pattern -> 0`은 잘못된 질문에 대한 정확한 답이라 툴 입장에선 에러가 아니고, 이어진 출력 붕괴는 모델 쪽 실패라 어느 층도 잡지 않았다. 이 코드의 에러 복구는 툴 실패만 다루고 모델 출력 실패는 다루지 않는다.

**부록 finding.** `plan_exec-06`의 계획 마지막 스텝은 문자열 `'14:00'`이었다. planner는 툴이 없어 파일을 볼 수 없는데 계획에 답을 적었고, 우연히 맞았다. 계획 단계가 실행 없이 결론을 내는 경로가 열려 있다는 뜻이며, 틀린 값을 적었다면 executor가 그 스텝을 그대로 출력할 위험이 있다.

**결론.** 한 번 읽으면 끝나는 소형 파일 태스크에서는 매 스텝 재판단하는 ReAct가 계획을 미리 굳히는 방식보다 비용 면에서 압도적으로 유리하고, 계획을 고정하는 것의 이점(예측 가능성, 마지막 검증 단계)은 태스크가 그만큼 길지 않아 드러나지 않았다. 두 harness의 지표 차이는 모델 능력이 아니라 종료 조건과 컨텍스트 누적 방식이라는 harness 설계 결정에서 나왔다.
