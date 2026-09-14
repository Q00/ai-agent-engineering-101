## What I built

하네스 A/B 실험을 수행했다. 모델, 태스크, 도구를 상수로 고정하고
하네스만 독립변수로 바꾸어 ReAct형과 Plan-then-Execute형을 각각
3회씩, 총 6회 실행했다.

태스크는 `app.log`에서 ERROR가 가장 많이 발생한 시간대
(HH:00)를 찾는 것이며, 정답은 `14:00`이다. 두 하네스 모두
동일한 `read_file`과 `count_pattern` 도구를 사용했다.

ReAct는 모델이 tool 실행 결과를 관찰한 뒤 다음 행동을 동적으로
결정하는 방식이고, Plan-then-Execute는 먼저 전체 실행 계획을
생성한 뒤 그 계획에 따라 작업을 수행하는 방식이다.

실험 결과는 다음과 같다.

| Harness | 성공 | 평균 Tokens | 평균 Iterations | 평균 Interventions |
|---|---:|---:|---:|---:|
| ReAct | 3/3 (100%) | 4,194 | 2.33 | 0 |
| Plan-then-Execute | 1/3 (33.3%) | 7,177 | 3.00 | 0 |

ReAct는 3회 모두 성공했다. Run 1과 Run 2에서는 `read_file`로
`app.log`를 확인한 뒤 2번의 모델 호출만으로 `14:00`을 답했다.
Run 3에서는 추가적인 `count_pattern` 호출을 사용해 ERROR 개수와
특정 시간대의 결과를 확인했으며, 3번의 모델 호출로 성공했다.

Plan-then-Execute는 3회 중 1회만 성공했다. 성공한 Run 4에서는
`14:00`에 ERROR가 가장 많다는 사실을 확인한 뒤에도 계획에 포함된
추가 검증 작업이 계속 실행되어 7 iterations와 19,904 tokens를
사용했다.

실패한 Run 5와 Run 6에서는 모델이 요구된 JSON plan 형식을 제대로
생성하지 못했다. 따라서 계획을 실제로 실행하기 전에 JSON parsing이
실패했고, 각각 1 iteration에서 종료되었다.

이번 실험에서는 **종료 조건과 오류 복구 방식에서 가장 큰 차이가
나타났다.** ReAct는 각 observation 이후 다음 행동을 다시 결정할
수 있어 필요한 작업이 끝나면 바로 종료할 수 있었다. 반면
Plan-then-Execute는 먼저 생성한 계획을 실행하는 구조이므로 이미
정답을 확인한 이후에도 계획에 남아 있는 작업을 계속 수행할 수
있었다.

또한 Plan-then-Execute는 계획 자체가 유효한 JSON 형식이어야 실행
단계에 진입할 수 있기 때문에, Run 5와 Run 6처럼 계획 생성 단계에서
실패하면 tool 실행이나 replan 단계까지 도달하지 못하는 문제가
발생했다.

따라서 이번 태스크와 구현에서는 ReAct가 Plan-then-Execute보다
성공률과 평균 토큰 사용량, 평균 반복 횟수에서 모두 더 좋은 결과를
보였다.

## What I tried and discarded

- **계획 생성과 실행을 분리하는 Plan-then-Execute 구조를 그대로
  사용했다.** 계획을 먼저 생성하면 실행 흐름을 명확하게 만들 수
  있을 것으로 예상했지만, 실제 실행에서는 계획의 JSON 형식이
  추가적인 실패 지점이 되었다.

- **Plan-then-Execute의 실패 결과를 제거하지 않았다.** Run 5와
  Run 6은 계획 parsing 단계에서 실패했지만, 이 결과 역시 하네스의
  실제 동작을 보여주는 실험 결과이므로 `results.csv`와 실행 로그에
  그대로 보존했다.

- **추가적인 human intervention은 사용하지 않았다.** 모든 실행에서
  `interventions=0`으로 유지하여 사람의 개입이 실험 결과에 영향을
  주지 않도록 했다.

- **두 하네스의 도구는 동일하게 유지했다.** `read_file`과
  `count_pattern`을 공통 도구로 사용하여 tool 자체의 차이가
  결과에 영향을 주지 않도록 했다.

- **`app.log`의 정답은 실행 전에 확인했다.** `app.log`를 직접
  집계하여 `14:00`에 ERROR가 6개로 가장 많고, 다음으로 많은
  `12:00`은 3개임을 확인했다. 이를 바탕으로 `TASK.md`의
  `expected: 14:00`을 실행 전에 고정했다.

## How to run

```powershell
$env:OPENAI_BASE_URL="https://openrouter.ai/api/v1"
$env:OPENAI_API_KEY="<API_KEY>"
$env:AGENT_MODEL="<실험에 사용한 모델명>"```

cd submissions\26512074\week-02
python run_ab.py --runs 3
Provider: OpenRouter (OpenAI-compatible API)
Model: AGENT_MODEL 환경변수에 설정한 모델
Tools: read_file(path), count_pattern(path, pattern)
Input: app.log
Task: TASK.md
Runs: 각 harness 3회

API key는 환경변수로만 사용하며 제출물에 포함하지 않았다.

Checklist
 TASK.md에 task:와 expected:를 실행 전에 고정
 ReAct 3회 실행
 Plan-then-Execute 3회 실행
 Run logs를 logs/에 실행별로 저장
 results.csv에 6개 실행 결과 기록
 두 harness에서 동일한 모델과 도구 사용
 Human intervention 0회
 실패한 실행 결과를 삭제하지 않음
 API key를 제출물에 포함하지 않음
 app.log를 수정하지 않음
 python scripts/check_week02.py submissions/26512074/week-02 통과 확인