# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

학번 26510129

## 1. 변형 정의

**고정한 것.** 모델은 OpenRouter의 `nvidia/nemotron-3.5-lightning:free`를 OpenAI 호환 API로 호출했다(`OPENAI_BASE_URL=https://openrouter.ai/api/v1`, `OPENAI_API_KEY`, `AGENT_MODEL` 세 변수 설정 후 `python run_ab.py --runs 3`, 부족분은 재실행으로 보충). 툴은 `read_file(path)`(파일 앞 4000자 반환)와 `count_pattern(path, pattern)`(정규식 매칭 라인 수 반환) 둘이며 cwd 밖 경로는 거부한다. 태스크는 `TASK.md`의 "app.log에서 ERROR 라인이 가장 많은 시간대", `expected: 14:00`. 코드는 `weeks/week-02/starter/`와 동일하고 ReAct는 `max_steps=8`, `IRREVERSIBLE=set()`, Plan-then-Execute는 `max_replan=1`, `max_tool_rounds=3`으로 돌렸다.

**바꾼 것.** 다섯 축 중 두 harness가 다르게 설정한 축:

| 축 | ReAct (`harness_react.py`) | Plan-then-Execute (`harness_plan_execute.py`) | 차이 |
|---|---|---|---|
| 1 컨텍스트 관리 | 대화 하나. 매 호출에 전체 히스토리(Thought, 툴 호출, Observation) 재전송 | planner 대화와 executor 대화 분리. executor에는 계획 전체 + 모든 스텝의 툴 결과와 응답이 누적되어 매 호출 재전송 | **다름** |
| 2 툴 granularity | `tools_shared.TOOL_SPECS` 2개 | 동일 | 같음 |
| 3 종료 조건 | (a) 툴 호출 없는 응답이 오면 그 텍스트를 답으로 반환, (b) `max_steps=8` 소진 | (a) 계획 JSON 파싱 실패 시 즉시 종료, (b) 계획 스텝을 모두 소진하면 "Answer:로 답하라"를 한 번 더 요청하고 종료, (c) 스텝이 `OFF_PLAN`이면 재계획 1회 | **다름** |
| 4 에러 복구 | 툴 예외 → `error: ...` 문자열을 Observation으로 반환 (`Chat.run_tools`) | 동일 + `OFF_PLAN` → planner에게 남은 계획 재요청 (`max_replan=1`), 스텝당 툴 3라운드 초과 시 강제 `OFF_PLAN` | 설계는 다르나 이번 실행에서 추가 층은 미발동 |
| 5 인간 개입 | `IRREVERSIBLE`이 빈 집합. 읽기 전용 툴이라 개입 지점 없음 | 개입 지점 없음 | 같음 (둘 다 interventions=0) |

## 2. 측정

`results.csv` 전체 (9 run. 첫 실행이 plan_exec 도중 중단돼 재실행한 탓에 run 번호가 섞였고, 첫 커밋 `6f41880`에 run 1이 함께 들어갔다. 모든 run은 `logs/`에 있다):

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

## 3. 해석

ReAct는 토큰(평균 4,268 vs 39,916, 9.4배), 반복 횟수(평균 2.2 vs 10.5, 4.8배), 소요 시간(28–90 s vs 287–470 s)에서 이겼고, Plan-then-Execute는 성공률(4/4 vs 4/5)에서 앞섰다. iters 차이는 **종료 조건 축**이 만들었다. plan_exec 네 run 모두 `[step 1]`에서 이미 `Answer: 14:00`을 냈지만(`logs/plan_exec-05.txt`, `-06`, `-07`, `-09`) 종료 조건이 "답이 나왔는가"가 아니라 "계획 스텝을 다 밟았는가"라서 남은 4–5스텝을 계속 실행했고, 그 스텝들은 새 정보 없이 ERROR 19줄 목록과 시간대별 표를 다시 만들거나 `Answer: 14:00`만 반복했다. 반면 ReAct는 모델이 툴 호출을 멈추는 순간 끝나 "읽기 1회 + 답 1회 = 2"로 수렴했다. tokens 차이가 iters 차이(4.8배)보다 큰 9.4배인 것은 **컨텍스트 관리 축** 때문이다. executor에 `read_file` 결과 4000자와 각 스텝 응답이 누적돼 매 호출 재전송되므로 뒤로 갈수록 호출당 비용이 커지고, 같은 구조가 ReAct에도 있지만 호출이 2번이라 드러나지 않는다. 같은 plan_exec 안에서도 `-05`와 `-09`는 step 1에서 `read_file`을 두 번 부르고 step 2–3에서 ERROR 전체를 코드블록과 표로 재출력해 50–51k로, 나머지 두 run(28–30k)보다 1.7배 컸다. 성공률 차이도 종료 조건 축에서 나왔다. 유일한 실패 `react-03`은 모델이 `count_pattern(pattern='00:.*ERROR')`이라는 잘못된 정규식으로 0을 받은 뒤 출력이 붕괴한 경우인데(`[step 3] ... HH HH count count ... incapaz hospitalized ...`), 툴 호출이 없었으므로 ReAct 종료 조건 (a)가 이 붕괴 텍스트를 "답"으로 받아 종료했다. `Answer:`로 시작하는지만 검사했어도 재시도 기회가 있었다. plan_exec는 마지막에 "Answer:로 답하라"를 별도 요청하는 단계가 있어 같은 붕괴에도 한 번 더 기회가 있지만, n=4에서 그 단계가 실제로 실패를 구제한 사례는 없어 성공률 차이가 구조 덕인지 표본 운인지는 단정할 수 없다. **에러 복구 축**은 기여하지 않았다. 9 run에서 툴 예외(`error:`)가 없었고 `replans=0`으로 OFF_PLAN 경로도 열리지 않았으며, react-03의 `count_pattern -> 0`은 잘못된 질문에 대한 정확한 답이라 툴 에러가 아니고 이어진 출력 붕괴는 모델 실패라 어느 층도 잡지 않았다. 이 코드의 에러 복구는 툴 실패만 다루고 모델 출력 실패는 다루지 않는다. 부수 finding으로 `plan_exec-06`의 계획 마지막 스텝은 문자열 `'14:00'`이었다. planner는 툴이 없어 파일을 볼 수 없으므로 태스크 문구의 `HH:00`에서 지어낸 값이고 맞은 것은 우연이며, 틀린 값이었다면 executor가 그 스텝을 그대로 출력할 위험이 있다. 결론적으로 한 번 읽으면 끝나는 소형 태스크에서는 매 스텝 재판단하는 ReAct가 비용에서 압도적으로 유리하고, 계획 고정의 이점(예측 가능성, 마지막 검증 단계)은 태스크가 짧아 드러나지 않았다. 지표 차이는 모델 능력이 아니라 종료 조건과 컨텍스트 누적 방식이라는 harness 설계에서 나왔다.
