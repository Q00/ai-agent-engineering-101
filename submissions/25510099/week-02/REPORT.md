# REPORT.md — ReAct형과 Plan-then-Execute형 하네스를 같은 태스크에서 비교한 결과

모델과 태스크와 도구를 고정하고 하네스만 바꾸어, `app.log`에서 ERROR가 가장 많은
시간대를 찾는 태스크를 두 하네스로 각각 실행하였습니다. 

모델은 OpenRouter의 **nvidia/nemotron-3-super-120b-a12b:free**이고, 도구는 **read_file(path)** 와 **count_pattern(path, pattern)** 두가지 입니다.

성공 판정은 최종 답변에 `14:00`이 들어 있는지로 설정하여 과제를 수행했습니다.

## 1. 변형 정의

다섯 요소 중 3가지를 다르게 변형하였습니다.

1. **컨텍스트 관리**
    - ReAct형은 **Chat** 객체 하나에 전체 히스토리를 쌓아서 매 호출이 지금까지의 Thought와 Observation을 전부 봅니다. 

    - Plan-then-Execute형은 **planner** 와 **executor** 를 별도의 **Chat** 으로 나누고, 플래너는 **tools=False** 로 만들어져 도구 결과를 한 번도 보지 못한 채 계획을 씁니다. 재계획을 할 때에도 300자로 자른 실패 메시지만 받습니다.

2. **종료 조건**
    - ReAct형은 모델이 도구를 부르지 않고 답하면 끝나고, 그렇지 않으면 **max_steps=8**에서 종료되도록 구성하여 모델이 종료 시점을 정하게 합니다.

    - Plan-then-Execute형은 계획의 길이가 종료를 정합니다. 스텝마다 모델을 한 번씩 부르고 계획을 끝까지 소진한 뒤에야 최종 답변을 요청합니다.

3. **에러 복구**
    - 도구가 예외를 던졌을 때 두 하네스 모두 에러를 Observation으로 되돌려 주는 것은 동일하지만, 계획 수준에서의 실패는 Plan-then-Execute형에만 있습니다.
    - 모델이 판단하여 재계획을 수행하며, 플래너가 JSON 리스트를 내놓지 못하면 None 데이터를 돌려주면서 시스템이 중단됩니다.

**도구 granularity**는 고정하였습니다.

실행 전에 예상한 것은 다음과 같습니다.
> 태스크가 도구 호출 한두 번이면 끝나므로 ReAct형은 2~3 iteration에 수렴하고, 
> Plan-then-Execute형은 계획이 4~6스텝으로 나올 것이므로 6~9 iteration이 될 것으로 예상된다. 
> 따라서 종료 조건이 iteration과 토큰을 좌우할 것이다. Plan-then-Execute형 고유의 실패는 플래너가 
> JSON을 못 내놓는 경우일 것이다.

## 2. 측정 결과

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---|---|---|---|
| 1 | react | X | 26,552 | 8 | 0 | |
| 2 | react | O | 4,396 | 2 | 0 | |
| 3 | react | X | | | | crash: TypeError (502) |
| 4 | plan_exec | X | 288 | 1 | 0 | replans=0 |
| 5 | plan_exec | X | | | | crash: TypeError (502) |
| 6 | plan_exec | O | 28,689 | 9 | 0 | replans=0 |
| 7 | react | O | 36,592 | 8 | 0 | |
| 8 | react | O | 9,656 | 3 | 0 | |
| 9 | react | X | 30,921 | 8 | 0 | |
| 10 | plan_exec | X | | | | crash: RateLimitError 429 |
| 11 | plan_exec | X | | | | crash: RateLimitError 429 |
| 12 | plan_exec | X | | | | crash: RateLimitError 429 |

- 에러가 발생한 런을 빼면 ReAct형은 5런 중 3번 성공했고 토큰은 4,396에서
36,592까지 퍼졌습니다.
- Plan-then-Execute형은 유효한 런이 2개뿐이고 그중 1번 성공하였습니다.

## 3. 해석

지표에 영향을 준 것은 종료 조건이였으며, 같은 요소가 두 하네스에서 서로 다르게 나타나는 것을 확인했습니다.

- Plan-then-Execute형에서는 비용이 되었습니다. 5번과 6번 런을 보면 두 런 모두 첫 스텝에서 이미 정답을 냈습니다. 파일을 읽는 도구 한 번에 로그 전체가 들어왔기 때문입니다. 그러나, 해당 하네스는 계획을 끝까지 소진해야 끝나기 때문에 답을 가진 상태로 줄 단위로 나누기, 시간대별로 세기 같은 남은 스텝을 계속 실행하였습니다.

- ReAct형에서는 실패가 되었습니다. 1번 런에서 모델은 09시부터 15시까지 시간대별 개수를 세었습니다. 14시가 6건으로 가장 많다는 것까지 이미 확인한 상태였습니다. 그런데 16시와 17시를 남겨둔 채 `max_steps=8`에 걸려 모델을 부를 횟수가 모자라서 틀린 결과가 나오게 되었습니다.

종료 조건을 상한으로 둘 것인가, 계획 길이로 둘 것인가. 이 하나의 선택이 한쪽에서는 토큰 낭비로, 다른 쪽에서는 오답으로 나타났습니다.

## 실행 방법

```bash
cd submissions/25510099/week-02
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<본인 OpenRouter 키>
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free

python run_ab.py --runs 3        # react 3회 + plan_exec 3회
```