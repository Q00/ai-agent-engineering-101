# Week 02 보고서: 같은 태스크, 하네스만 바꾼 A/B

모델과 도구와 태스크를 고정하고 하네스만 ReAct형과 Plan-then-Execute형으로 바꿨다. 태스크는 app.log에서 ERROR가 가장 많은 시간대를 찾는 것이고 정답은 14:00이다. 성공률은 두 하네스 모두 3/3으로 같았다. 차이는 토큰과 반복에서만 났다.

## 1. 변형 정의

두 하네스가 공유하는 것은 모두 tools_shared.py에 있다. 모델 상수 MODEL(nvidia/nemotron-3.5-lightning:free), 도구 두 종(TOOLS_IMPL, TOOL_SPECS의 read_file과 count_pattern), 계측기 Meter(tokens, iters, interventions)가 같다. 태스크와 성공 기준은 TASK.md의 task: 줄과 expected: 14:00을 두 하네스가 함께 쓴다. 다른 것은 루프 구조뿐이다.

| 축 | ReAct (harness_react.py) | Plan-then-Execute (harness_plan_execute.py) |
|---|---|---|
| 컨텍스트 관리 | Chat 하나(run_react의 chat)에 Thought와 Observation을 계속 누적한다. | Chat 두 개로 나눈다. planner(tools=False)와 executor가 따로 있고, 실행자는 계획을 JSON 문자열로만 받는다(executor.add_user의 Plan: 부분). 단계마다 "Execute step i" 문장이 히스토리에 쌓인다. |
| 도구 granularity | 도구 스키마만 준다. read_file의 4,000자 상한(tools_shared.py의 f.read()[:4000])이 실제 단위를 정한다. | 계획 프롬프트가 도구 시그니처를 텍스트로 노출한다(planner.add_user의 "Available tools: read_file(path), count_pattern(path, pattern)"). 계획 문장이 호출 단위를 미리 못박는다. |
| 종료 조건 | 상한은 max_steps=8이다. 모델이 도구 호출 없이 답하면 그 자리에서 끝난다(if not reply.tool_calls: return). | 종료 기준이 계획 소진이다(while i < len(plan)). 여기에 단계별 도구 예산 max_tool_rounds=3과 재계획 상한 max_replan=1이 붙는다. 루프가 끝나면 최종 답을 한 번 더 요청한다. |
| 에러 복구 | 도구 예외만 다룬다. tools_shared.py run_tools의 out = f"error: {e}"로 오류가 Observation이 되어 돌아온다. | 도구 예외 복구는 같다. 여기에 계획 계층 복구가 하나 더 있다. OFF_PLAN이면 재계획을 1회 한다. 다만 parse_plan이 None을 주면 break로 끊긴다. 형식 오류에는 복구 경로가 없다. |
| 인간 개입 지점 | IRREVERSIBLE 집합과 ask_human()이 있다. 거부하면 meter.interventions가 오른다. | 승인 훅이 아예 없다. |

## 2. 측정표

results.csv 6행을 그대로 옮겼다. 벽시계 시간은 각 로그의 [judge] 줄에서 읽었다.

| run | harness | success | tokens | iters | interventions | note | 벽시계(초) | 로그 |
|---|---|---|---|---|---|---|---|---|
| 1 | react | O | 4087 | 2 | 0 |  | 112.4 | logs/react-01.txt |
| 2 | react | O | 4169 | 2 | 0 |  | 103.5 | logs/react-02.txt |
| 3 | react | O | 4547 | 2 | 0 |  | 71.4 | logs/react-03.txt |
| 4 | plan_exec | O | 34415 | 10 | 0 | replans=0 | 282.6 | logs/plan_exec-04.txt |
| 5 | plan_exec | O | 69137 | 17 | 0 | replans=1 | 337.1 | logs/plan_exec-05.txt |
| 6 | plan_exec | O | 25908 | 10 | 0 | replans=1 | 236.8 | logs/plan_exec-06.txt |

하네스별 요약이다. n=3이라 평균과 최소~최대만 적는다.

| 하네스 | 성공 | 토큰 평균 | 토큰 범위 | 반복 평균 | 반복 범위 | 개입 | 벽시계 평균 | 벽시계 범위 |
|---|---|---|---|---|---|---|---|---|
| ReAct | 3/3 | 4,268 | 4,087~4,547 | 2.0 | 2~2 | 0 | 95.8초 | 71.4~112.4 |
| Plan-then-Execute | 3/3 | 43,153 | 25,908~69,137 | 12.3 | 10~17 | 0 | 285.5초 | 236.8~337.1 |

## 3. 해석

네 지표 중 성공과 개입은 무승부였다. 승부는 토큰과 반복에서만 났고 둘 다 ReAct가 이겼다. 평균 기준으로 토큰 10.1배, 반복 6.2배, 벽시계 3.0배 차이다. ReAct 쪽 비용을 낮춘 축은 도구 granularity다. logs/react-01.txt를 보면 첫 호출에서 read_file 한 번으로 app.log 전체(3,022자, read_file 상한 4,000자 안)를 받는다. 그다음 두 번째 호출에서 count_pattern을 한 번도 부르지 않고 시간대별 개수를 직접 세어 답했다(09이 1건, 12가 3건, 14가 6건). logs/react-03.txt는 그 계산을 줄 단위로 다 펼쳐 놓았고 경로는 같다. 굵은 도구 하나가 집계 도구를 불필요하게 만든 것이다. 대신 첫 스텝에서는 시스템 프롬프트가 요구한 Thought: 줄이 없었다. 세 로그 모두 step 1의 텍스트가 비어 있고 곧바로 도구를 불렀다. 형식 준수는 하네스가 강제하지 못했다.

Plan-then-Execute의 비용은 종료 조건 축에서 나왔다. logs/plan_exec-04.txt는 step 1에서 이미 Answer: 14:00을 냈는데도 step 2부터 6까지 계속 실행됐다. step 2에서 4까지는 도구를 부르지 않고 ERROR 줄을 다시 나열하고 시간대 표를 그렸다. 종료가 답이 나온 시점이 아니라 계획 소진 시점이라서 호출 다섯 번이 그냥 더 들었다. 두 축이 겹치면 더 커진다. logs/plan_exec-05.txt의 step 2 "count ERROR lines per hour"에서 모델은 시간대마다 count_pattern을 하나씩 불러 max_tool_rounds=3에 걸렸다. OFF_PLAN 뒤 재계획을 1회 했지만 새 계획에서도 같은 방식으로 다시 상한을 넘겼다. 재계획 예산이 소진되어 OFF_PLAN 상태로 다음 단계로 넘어갔고, 최종 답은 그래도 맞았다. 반복 17, 토큰 69,137로 6런 중 최댓값이다. 같은 로그의 step 1에서는 read_file을 두 번 중복 호출하기도 했다.

에러 복구 축의 비대칭은 logs/plan_exec-06.txt에 그대로 남았다. step 1에서 count_pattern으로 14시만 확인하고 바로 Answer를 냈고, step 2에서 상한을 넘겨 OFF_PLAN이 됐다. 그런데 재계획 응답이 JSON 리스트를 닫지 못한 채 산문을 덧붙여 파싱에 실패했다. 그래서 루프가 break로 끊기고, 루프 밖의 강제 최종 답 요청에서 14:00이 나왔다. 도구 오류는 문자열 Observation으로 복구되지만 계획과 재계획의 형식 오류에는 복구 경로가 없다. 컨텍스트 관리 축은 이 차이를 증폭했다. 두 하네스 모두 요약이나 절단 없이 매 호출에서 전체 히스토리를 다시 보낸다. 표의 tokens를 iters로 나누면 호출당 평균 토큰이 ReAct는 2,044~2,274인데 Plan-then-Execute는 2,591~4,067이고, 반복이 가장 많은 run 5가 가장 높다. 평균 반복이 6.2배인데 평균 토큰이 10.1배로 더 벌어진 이유가 여기다. 마지막으로 인간 개입 지점 축은 이번 실험에서 관측되지 않았다. harness_react.py의 IRREVERSIBLE이 빈 집합이고 read_file과 count_pattern이 모두 읽기 전용이라, 승인 경로를 탈 호출이 애초에 없었다. 6런 전부 interventions=0인 것은 두 하네스가 같다는 증거가 아니라 이 축을 측정하지 않았다는 뜻이다.

## 4. 재현 방법

실행 날짜는 2026-09-08이다. 제공자는 OpenAI 호환 엔드포인트인 OpenRouter다. tools_shared.py의 PROVIDER는 ANTHROPIC_API_KEY가 없으면 openai로 잡히므로 그 변수는 비워 둔다. 모델은 nvidia/nemotron-3.5-lightning:free이고 openai 경로의 기본값이라 AGENT_MODEL을 따로 주지 않았다. 이 모델을 고른 이유는 실행 전 계획 프롬프트 프로브에서 무료 모델 중 유일하게 문자열 JSON 리스트를 돌려줬기 때문이다. 1주차에 쓴 nvidia/nemotron-3-super-120b-a12b:free는 같은 프롬프트에 도구 호출 객체 리스트를 돌려줘 parse_plan이 실패했고, z-ai/glm-5.2:free와 minimax/minimax-m2.7:free는 이날 무료 제공이 끊겨 404가 났다(자세한 기록은 tools_shared.py의 모델 기본값 커밋 메시지). 다른 모델로 재현하면 Plan-then-Execute가 계획 파싱 단계에서 전부 X가 될 수 있으므로, 같은 경향을 보려면 모델을 고정해야 한다.

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<openrouter key>
python run_ab.py --runs 3            # 또는: uv run --with openai python run_ab.py --runs 3
```

판정은 run_ab.py의 judge가 한다. 최종 답에 expected 문자열이 들어 있는지만 본다. 기준은 실행 전에 TASK.md에 적고 결과를 본 뒤에 바꾸지 않았다.

## 5. 한계

n=3이라 평균과 최소~최대만 적었고 표준편차나 신뢰구간은 쓰지 않았다. 무료 티어 모델 하나만 썼으므로 하네스 차이가 다른 모델에서도 같은 방향인지는 모른다. 이 모델은 추론형이어서 completion 토큰에 로그에 보이지 않는 추론이 섞인다. 토큰 지표는 그만큼 부풀 수 있다. 벽시계 시간은 무료 티어 대기와 네트워크에 좌우되므로 하네스 비용 지표로는 토큰과 반복이 더 믿을 만하다. app.log가 60줄 3,022자로 read_file 상한 안에 들어온다는 점도 조건이다. 파일이 상한을 넘으면 read_file 한 번으로 끝내는 ReAct의 우위는 사라질 수 있다. 개입 지표는 읽기 전용 도구만 쓴 결과라 이번 A/B에서 아무 정보를 주지 않는다.

## 6. 추가 실험: 하네스를 직접 구현해 축을 바꿔 보기

위 1~5절의 채점용 실행은 교수님 스타터 하네스를 그대로 돌린 것이다. 실습에서는 다섯 축의 값을 내가 다시 정해 두 하네스를 강의 뼈대에서 새로 구현했고, 그 코드와 결과는 extra/lab-own-harness/에 있다(결정과 이유는 decisions.md, 두 루프의 머메이드 순서도는 README.md). 스타터와 달리 정한 곳은 다섯 곳이다. ReAct는 명시적 finish 도구 호출만 종료로 인정하고 평문 답은 거부한다(R2). Thought: 줄 없이 도구를 부르면 그 호출을 거부하는 Observation을 돌려 형식을 강제한다(R4). Plan-then-Execute는 실행자가 Answer:를 내면 남은 단계를 건너뛰고 끝낸다(P5). 계획이나 재계획이 JSON 리스트가 아니면 한 번 더 요청한다(P6). 승인 훅을 ReAct와 같게 둔다(P7).

이 실행은 OpenRouter 무료 한도가 소진되어 Claude Code 구독의 claude -p를 모델 백엔드로 썼고 모델은 claude-sonnet-4-5다. 모델이 다르므로 1~3절의 nemotron 결과와 직접 비교는 하지 않는다. 또 claude -p는 자기 컨텍스트를 매 호출에 싣기 때문에 토큰 열이 하네스 비용을 반영하지 않아, 하네스가 실제로 만든 프롬프트 글자수(prompt_chars)와 모델 호출 수로 비교했다.

| 하네스 | 성공 | 호출 평균(범위) | 프롬프트 글자수 평균(범위) | 벽시계 평균(초) |
|---|---|---|---|---|
| ReAct(자체 구현) | 3/3 | 3.3 (2~4) | 13,245 (5,345~17,356) | 84.4 |
| Plan-then-Execute(자체 구현) | 3/3 | 6.7 (6~7) | 33,550 (30,282~36,961) | 124.5 |

관찰은 여섯 가지다. 첫째, P5 조기 종료가 세 실행 모두 발동해 각각 2, 5, 4단계를 건너뛰었다. 3절에서 본 "1단계에서 답이 나왔는데 계획 끝까지 실행"하는 낭비가 이 결정으로 사라졌다. 둘째, R2 finish 도구는 모델이 평문으로 답하려던 두 번을 거부했고 두 번 다 다음 스텝에서 회복했다. 비용은 스텝 1회씩이다. 셋째, R4 Thought 강제는 한 번도 발동하지 않았다. 이 모델은 형식을 지켰으므로 장치의 비용은 0이었고, 그 가치는 모델에 따라 달라진다. 넷째, 단계별 도구 예산 3라운드에는 걸리지 않았다. 이 모델은 시간대별 count_pattern 24개를 한 턴에 묶어 부르므로 라운드가 1로 끝나는데, 3절의 모델은 한 턴에 하나씩 불러 같은 예산에 걸렸다. 라운드 예산의 실질 의미는 모델의 병렬 호출 습관에 좌우된다. 다섯째, 여섯 실행 중 두 실행의 첫 스텝에서 모델이 "도구가 없다" 또는 "도구는 MCP 서버"라고 답했다. 하네스가 준 정보가 아니라 claude -p가 함께 싣는 Claude Code 컨텍스트가 새어 들어온 것이고, 둘 다 재계획과 평문 거부로 회복됐다. 이 백엔드의 수치는 그만큼 흔들리며, 동시에 에러 복구 축이 왜 필요한지 보여 준다. 여섯째, 판정의 관대함이 드러났다. plan_exec-05는 Answer: 줄이 비어 있었지만 본문에 14:00이 있어 O가 되었으므로, 성공 기준을 Answer: 줄의 값으로 좁혀야 한다.

같은 모델(nemotron)로 스타터 하네스와 이 구현을 나란히 돌리는 것이 다음 단계이고, 한도가 풀리면 추가 커밋으로 붙일 예정이다.
