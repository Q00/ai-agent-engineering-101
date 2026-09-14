# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

학번 `26512072`. OpenRouter `nvidia/nemotron-3.5-lightning:free`,
`reasoning_effort=none`, Python 3.12.7, OpenAI SDK 2.54.0을 사용했다.
성공 기준은 실행 전 커밋 `d6b7028`의 [TASK.md](TASK.md)에 기록했고, 실행 조건과 재현 방법은
[README.md](README.md)에 정리했다. 실행 명령은 저장소 루트에서
`powershell -NoProfile -ExecutionPolicy Bypass -File .\submissions\26512072\week-02\run_openrouter.ps1`이다.

## 1. 변형 정의

같은 원본 `app.log`에서 ERROR가 가장 많은 시간을 답하게 했다. 두 하네스는 같은 모델과
[tools_shared.py](tools_shared.py)의 `read_file`, `count_pattern` 도구를 사용한다.
`TASK.md`에 미리 정한 `14:00`이 최종 답변에 포함되면 성공이다. 최종 v3 비교는 모델·태스크·
도구·`temperature=0.2`·`max_tokens=2048`·`max_steps=16`을 고정하고 하네스만 바꿨다.

| 설계 요소 | ReAct | Plan-then-Execute |
| --- | --- | --- |
| 컨텍스트 관리 | 한 대화에 도구 결과를 누적하고 매번 다음 행동 결정 | 계획용·실행용 대화 분리; 실행 대화에 문제·계획·단계 결과 누적 |
| 도구 단위 | `read_file`, `count_pattern` | 같은 도구·스키마 사용; 최초 계획 생성에서는 도구 비활성화 |
| 종료 조건 | 도구 호출 없는 응답 또는 `max_steps=16` | 계획 단계 실행 뒤 별도 최종 답변 요청; 단계별 `max_tool_rounds=3` |
| 오류 복구 | 도구 오류를 Observation으로 전달 | 같은 도구 오류 처리에 `OFF_PLAN` 재계획 최대 1회 추가 |
| 사람 개입 | 승인 대상 없음 | 승인 흐름 없음; 자동 재계획은 사람 개입과 별도 |

Plan v3는 한 호출로 세 단계 JSON 계획 전체를 만들고 `파일 읽기 → 받은 원문 집계 → 답 작성`
순서로 실행한다. 최초 계획 파싱 실패도 재계획 예산 1회를 사용한다. 이 설계는 앞선 조건에서
계획이 설명문으로 나오거나 한 단계가 시간대별 도구 호출을 반복한 문제를 보완한 것이다.

## 2. 측정

[results.csv](results.csv) 중 동일한 v3 설정으로 모델 응답이 완료된 첫 세 A/B 쌍의 결과다.
`iters`는 계획·단계·최종 답변을 포함한 모델 호출 횟수이고, tokens는 SDK가 보고한 입력·출력
합계다. 모델 응답을 받지 못한 시도도 삭제하지 않고 `results.csv`, `logs/`, 커밋 이력에 남겼다.

| run | harness | success | tokens | iters | interventions | note |
| --: | --- | :---: | --: | --: | --: | --- |
| 13 | react | O | 24,023 | 7 | 0 | `count_pattern` 5회 |
| 14 | plan_exec | O | 20,409 | 7 | 0 | replans=0 |
| 15 | react | O | 4,204 | 2 | 0 | `read_file` 뒤 바로 답함 |
| 16 | plan_exec | O | 19,317 | 7 | 0 | replans=0 |
| 25 | react | X | 6,784 | 4 | 0 | 출력이 깨져 최종 답 없음 |
| 26 | plan_exec | O | 20,894 | 7 | 0 | replans=0 |

| 하네스 | 성공 | 토큰 평균 ± 표본 표준편차 | 호출 평균 ± 표본 표준편차 | 개입 평균 |
| --- | --- | ---: | ---: | ---: |
| ReAct | 2/3 | 11,670.3 ± 10,775.2 | 4.33 ± 2.52 | 0 |
| Plan-then-Execute | 3/3 | 20,206.7 ± 807.7 | 7.00 ± 0.00 | 0 |

## 3. 해석

계획을 먼저 고정하면 흐름이 예측 가능하고 토큰도 줄 수 있다고 예상했지만, 이 태스크에서는
Plan-then-Execute가 세 번 모두 성공한 대신 ReAct보다 평균 1.73배의 토큰과 2.67회의 추가 호출을
사용했다. [ReAct run 15](logs/react-15.txt)는 `read_file` 한 번 뒤 두 번째 호출에서 답하고 끝났지만,
[Plan run 14](logs/plan_exec-14.txt)는 계획 생성 후 세 단계를 실행하고 이미 `14:00`을 찾은 뒤에도
별도 최종 답변 호출을 했다. Plan 세 실행은 모두 `read_file` 1회, `count_pattern` 1회, 총 7회
모델 호출, 재계획 0회여서 흐름과 비용의 분산이 작았다. 반면 ReAct run 13은 시간대별 확인을 위해
`count_pattern`을 5회 호출해 24,023토큰을 썼고, [run 25](logs/react-25.txt)는 잘못된 정규식을
Observation으로 받은 뒤 파일 읽기로 방향을 바꿨지만 출력이 깨져 실패했다. 따라서 성공률과
호출 수의 안정성은 Plan-then-Execute가 이겼고, 성공한 짧은 경로의 토큰·호출 효율은 ReAct가
좋았다. 토큰이 적다는 사실만으로 모델이 덜 고민했다고 판단할 수는 없고, 이번 차이는 계획 생성·
단계별 실행·별도 최종 답변이라는 종료 조건과 누적 컨텍스트로 설명하는 편이 로그에 더 잘 맞는다.
두 하네스 모두 사람 개입은 0회였다. 한 입력의 완료된 세 쌍만 비교했으므로 다른 태스크에도 같은
우열이 나타난다고 일반화할 수 없다.

작성 과정: 실행 조건과 해석 가설은 학습자가 결정했으며, 결과 표와 문장은 원본 코드·로그를 근거로
코딩 어시스턴트의 도움을 받아 정리했다.
