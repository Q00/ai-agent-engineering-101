# REPORT.md

## 1. Variant definition

두 하네스는 tools_shared.py의 같은 모델·같은 도구(read_file, count_pattern)를
공유하고, 다섯 축 중 세 축에서 다르게 설계했다.

- 종료 조건(axis 3): react는 모델 응답에 tool_calls가 없으면 그걸로 끝으로 나온다. 
  (magic string 없이, provider가 이미 구분해주는 신호를 그대로 씀). plan_exec는
  처음엔 정해진 plan을 순서대로 실행하게 설계했지만, executor에게 1단계부터
  `submit_answer` 도구를 계속 쥐여줬기 때문에 실제로는 계획을 다 안 끝내도
  아무 때나 답을 낼 수 있다 — 결과적으로 이 축이 두 하네스를 거의 같게 만들어
  버렸다(아래 해석 참고).
- 오류 복구(axis 4): starter는 plan을 순수 텍스트(JSON 문자열)로 받아서
  `json.loads`로 파싱했고, lab에서 그게 깨지는 걸 직접 봤다
  (`../lab/logs/plan_exec-05.txt`, 모델이 JSON 대신 `'read_file("app.log")'`라는
  문자열을 냄). 이번 버전은 plan/정답/실패 신호를 전부 `submit_plan`,
  `submit_answer`, `report_off_plan`이라는 tool call로 강제했다.
- **컨텍스트 관리(axis 1)**: react는 하나의 대화(Chat)에 모든 턴이 쌓인다.
  plan_exec는 planner용 Chat과 executor용 Chat을 분리해서, executor의
  컨텍스트가 계획 단계의 사고 과정으로 오염되지 않게 했다.

human intervention(axis 5)은 두 하네스 다 read-only 도구만 쓰므로 건드리지
않았고, 모든 런에서 interventions=0이다.

## 2. Measurements

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---|---|---|---|
| 1 | react | X | 1993 | 2 | 0 | |
| 2 | react | X | 264 | 1 | 0 | |
| 3 | react | X | 220 | 1 | 0 | |
| 4 | plan_exec | O | 4688 | 3 | 0 | replans=0 |
| 5 | plan_exec | O | 5404 | 3 | 0 | replans=0 |
| 6 | plan_exec | O | 4663 | 3 | 0 | replans=0 |
| 7 | react | O | 3479 | 2 | 0 | |
| 8 | react | O | 4136 | 2 | 0 | |
| 9 | react | O | 3973 | 2 | 0 | |

react: 6회 중 3회 성공(50%), 성공한 런은 평균 약 3,863 tokens.
plan_exec: 3회 중 3회 성공(100%), 평균 약 4,918 tokens.

## 3. Interpretation

react의 실패 3개(run 1~3)는 하네스 로직 때문이 아니었다 — 로그를 보면
모델이 첫 턴 또는 두 번째 턴에서 tool_calls도 없고 텍스트도 없는 완전히 빈
응답을 냈다(run 2는 365초를 기다리고도 빈 응답). 같은 하네스, 같은 모델로
바로 다시 돌린 run 7~9는 3/3 성공했고 토큰도 평상시 수준(~3,500~4,100)이었다.
즉 이 실패는 axis 설계가 아니라 무료 티어 모델의 그 시점 가용성 문제였다.

plan_exec는 이번엔 3/3 다 성공했고, 토큰도 lab에서 starter로 돌렸을 때
(23,220~33,827 tokens, `../lab/results.csv`)보다 훨씬 쌌다(4,663~5,404).
그런데 로그를 보면(`logs/plan_exec-04.txt`~`06.txt`) 세 런 다 계획이 5~7단계
였는데도 실제로는 `read_file`을 한 번 부르고 바로 `submit_answer`를 불러
끝냈다. 즉 plan_exec가 "계획을 더 잘 세워서" 싼 게 아니라, executor가
1단계부터 조기 종료용 도구(`submit_answer`)에 접근할 수 있게 설계했더니
사실상 react와 거의 같은 방식으로 동작해버린 것이다. starter가 lab에서
훨씬 비쌌던 이유는 반대로 정확히 이 조기 종료 경로가 없어서, 답을 이미
찾고도 남은 계획 단계를 끝까지 실행했기 때문이었다(`../lab/logs/plan_exec-04.txt`).

종합하면, 이번 실험에서 토큰/성공률을 가장 크게 움직인 축은 "react냐
plan_exec냐"라는 하네스 구분 자체가 아니라 종료 조건(axis 3)이었다 —
언제 멈출 수 있는지를 얼마나 유연하게 허용했는지가, 계획을 짜는지 여부보다
비용에 더 큰 영향을 줬다. 이 재설계는 오류 복구(axis 4) 목표였던 JSON
파싱 실패는 확실히 없앴지만, 그 대신 plan_exec라는 하네스의 정체성
(계획을 끝까지 따른다는 것)을 약하게 만들었다는 트레이드오프가 있다.

## 4. Reproducibility note: model selection

`AGENT_MODEL=nvidia/nemotron-3.5-lightning:free`로 고정하기 전에 세 모델을
먼저 시도했다가 버렸다: `minimax/minimax-m3:free`는 더 이상 무료로 제공되지
않아 404로 즉시 실패했고, `nvidia/nemotron-3-super-120b-a12b:free`는 응답
자체가 비어 와서(`resp.choices`가 None) 클라이언트에서 크래시가 났고,
`google/gemma-4-31b-it:free`는 업스트림 429(rate limit)로 막혔다. 이 셋은
모델 성능 문제가 아니라 무료 티어 가용성 문제라서 결과에 넣지 않았고,
대신 재현하는 사람을 위해 여기 남긴다.
