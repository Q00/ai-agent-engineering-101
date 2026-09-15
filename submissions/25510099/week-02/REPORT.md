# REPORT.md — ReAct형과 Plan-then-Execute형 하네스를 같은 태스크에서 비교한 결과

모델과 태스크와 도구를 고정하고 하네스만 바꾸어, `app.log`에서 ERROR가 가장 많은
시간대를 찾는 태스크를 두 하네스로 각각 실행하였습니다. 모델은 OpenRouter의
`nvidia/nemotron-3-super-120b-a12b:free`이고, 도구는 `read_file(path)`와
`count_pattern(path, pattern)` 두 개이며, 두 하네스가 `tools_shared.py`에서 같은
것을 import합니다. 성공 판정은 최종 답변에 `14:00`이 들어 있는지로 하였고, 이
기준은 실행 전에 `TASK.md`에 적어 커밋하였습니다.

먼저 확인해 둘 것이 하나 있습니다. `app.log`는 3,022바이트라서 `read_file`의
4,000자 상한에 걸리지 않습니다. 즉 `read_file`을 한 번 부르면 로그 60줄이 통째로
들어옵니다. 이 태스크는 도구 호출 한 번으로 풀리므로, 두 하네스의 차이는 "풀 수
있는가"가 아니라 "답을 알고 난 뒤에도 얼마나 더 도는가"로 나타나게 됩니다.
아래의 해석은 이 점을 전제로 하였습니다.

## 1. 변형 정의

다섯 요소 중 두 하네스가 다르게 잡은 것은 3가지입니다.

- 첫째, **컨텍스트 관리**가 다릅니다. ReAct형은 `Chat` 객체 하나에 전체 히스토리를
쌓아서 매 호출이 지금까지의 Thought와 Observation을 전부 봅니다. Plan-then-Execute형은
`planner`와 `executor`를 별도의 `Chat`으로 나누고, 플래너는 `tools=False`로 만들어져
도구 결과를 한 번도 보지 못한 채 계획을 씁니다. 재계획을 할 때에도 300자로 자른
실패 메시지만 받습니다.

- 둘째, **종료 조건**이 다릅니다. ReAct형은 모델이 도구를 부르지 않고 답하면
끝나고, 그렇지 않으면 `max_steps=8`에서 잘립니다. 즉 모델이 종료 시점을 정합니다.
Plan-then-Execute형은 계획의 길이가 종료를 정합니다. 스텝마다 모델을 한 번씩 부르고
계획을 끝까지 소진한 뒤에야 최종 답변을 요청합니다.

- 셋째, **에러 복구**가 다릅니다. 도구가 예외를 던졌을 때 두 하네스 모두 `error: ...`를
Observation으로 되돌려 주는 것은 같습니다. 그러나 계획 수준의 실패는
Plan-then-Execute형에만 있습니다. 모델이 `OFF_PLAN:`을 선언해야 재계획이 일어나고
그것도 1회뿐이며, 플래너가 JSON 리스트를 내놓지 못하면 `parse_plan`이 `None`을
돌려주면서 그 자리에서 중단됩니다.

**도구 granularity**는 일부러 고정하였습니다. 두 하네스가 같은 `tools_shared.py`를
import하므로 지표 차이를 도구 탓으로 돌릴 수 없습니다. CI가 이 import를 검사하는
것도 같은 이유로 생각하였습니다.

**인간 개입 지점**은 이 설계에서 측정할 수 없었습니다. 스타터 도구가 둘 다 읽기
전용이라 `IRREVERSIBLE`이 빈 집합이고, 따라서 `interventions`는 모든 런에서 0입니다.
이 값은 측정된 결과가 아니라 측정될 수 없었던 것입니다. 값을 얻으려면 쓰기 도구를
추가해야 하는데, 그러면 도구 집합까지 같이 바뀌어 비교가 성립하지 않게 됩니다.

실행 전에 예상한 것은 다음과 같습니다. 태스크가 도구 호출 한두 번이면 끝나므로
ReAct형은 2~3 iteration에 수렴하고, Plan-then-Execute형은 계획이 4~6스텝으로 나올
것이므로 6~9 iteration이 될 것이다. 따라서 종료 조건이 iteration과 토큰을 좌우할
것이다. Plan-then-Execute형 고유의 실패는 플래너가 JSON을 못 내놓는 경우일 것이다.

## 2. 측정 결과

`results.csv`의 내용입니다. 실패한 런도 그대로 두었습니다. rows 1~6은 스타터를
그대로 돌린 것이고, rows 7~12는 아래에 적은 재시도 패치를 넣은 뒤 돌린 것입니다.

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

인프라 문제로 죽은 런을 빼면 ReAct형은 5런 중 3번 성공했고 토큰은 4,396에서
36,592까지 퍼졌습니다. Plan-then-Execute형은 유효한 런이 2개뿐이고 그중 1번
성공하였습니다.

죽은 6런은 하네스가 아니라 provider 문제였습니다. rows 3과 5는 OpenRouter가
`{"choices": null, "error": {"code": 502, ...}}`를 반환해서
`tools_shared`의 `resp.choices[0]`이 터진 것입니다. 별도로 4번 호출해 보았을 때
1번 재현되었습니다. `_create_with_retry`로 4회까지 재시도하도록 고쳤고, 이것은
transport 수준이라 다섯 요소 중 어느 것도 바꾸지 않으며 두 하네스에 똑같이
적용되므로 비교를 기울게 하지 않는다고 생각하였습니다. rows 10~12는 무료 티어의
하루 50회 한도를 다 써서 429가 난 것이고, 이것은 재시도로 넘길 수 없었습니다.

## 3. 해석

이 태스크에서 지표를 움직인 것은 종료 조건이었고, 같은 요소가 두 하네스에서 서로
다른 모습으로 나타났습니다. Plan-then-Execute형에서는 비용으로 나타났습니다.
`logs/plan_exec-05.txt`와 `logs/plan_exec-06.txt`를 보면 두 런 모두 step 1에서 이미
`Answer: 14:00`을 냈는데, `read_file` 한 번으로 로그 전체가 들어왔으니 당연한
결과입니다. 그런데 이 하네스는 종료를 계획의 길이로 정하기 때문에 답을 이미 손에
쥔 상태에서 "Split the log into lines", "Maintain a dictionary counting occurrences
per hour" 같은 남은 스텝을 계속 실행하였고, `plan_exec-06`은 그 대가로 9 iteration에
28,689 토큰을 썼습니다. 같은 모델이 같은 첫 도구 호출로 같은 답에 도달한
`react-02`가 2 iteration에 4,396 토큰이었으므로 iteration은 4.5배, 토큰은 6.5배
차이입니다. 모델도 도구도 태스크도 같았으니 이 차이는 종료 조건에 돌릴 수밖에
없다고 생각하였습니다. 반대로 ReAct형에서는 같은 요소가 실패로 나타났습니다.
`logs/react-01.txt`에서 모델은 `count_pattern`으로 시간대별 개수를 09시 1건,
10시 2건, 12시 3건, 14시 6건까지 이미 관찰한 뒤에 `max_steps=8`에 걸려
`MAX_STEPS reached: incomplete`로 끝났습니다. 정보가 모자라서가 아니라 예산이
모자라서 틀린 것입니다. 종료 조건을 상한으로 둘 것인지 계획 길이로 둘 것인지라는
하나의 선택이, 한쪽에서는 토큰 낭비로 다른 쪽에서는 오답으로 나타난 셈입니다.

## 로그에서 추가로 확인한 것

컨텍스트를 나눈 것이 계획의 품질에 영향을 준 것으로 보입니다. 플래너는 도구를 한
번도 써보지 못한 채로 계획을 쓰는데, `logs/plan_exec-04.txt`에서 플래너는 JSON
리스트 대신 `{"path": "app.log"}`라는 도구 호출 모양의 객체를 반환하였습니다.
`parse_plan`이 `None`을 돌려주면서 288 토큰, 1 iteration 만에 중단되었고, 이것이
전체 실험에서 가장 싸게 끝난 실패입니다. 살아남은 계획들도 "Maintain a dictionary
counting occurrences per hour"처럼 주어진 두 도구로는 그대로 실행할 수 없는 스텝을
담고 있었고, executor는 그 스텝을 실제로 수행하는 대신 `Step 2 completed: log split
into lines.`처럼 말로 넘겼습니다. 도구 결과를 보지 못하는 자리에서 쓰인 계획은
도구가 실제로 할 수 있는 일과 어긋나는 것으로 생각하였습니다.

에러 복구에 대해서는 예상하지 못한 것을 하나 보았습니다. ReAct형의 "에러를
Observation으로 되돌린다"는 장치가 이 태스크에서는 거의 작동하지 않았습니다.
`count_pattern`은 정규식이 틀려도 예외를 던지지 않고 `0`을 반환하기 때문입니다.
`logs/react-09.txt`에서 모델은 이중으로 이스케이프된 `\\d`를 보내 0을 받았는데,
그것이 "해당하는 줄이 없다"인지 "패턴이 틀렸다"인지 구분할 수 없어 6스텝을 정규식
시행착오에 쓰다가 `max_steps`에 걸렸습니다. 에러 복구는 에러가 에러로 드러날 때에만
의미가 있고, 도구가 실패를 정상값으로 포장하면 그 비용은 반복 상한이 치르게 되는
것으로 생각하였습니다. 도구 granularity를 통제변수로 고정하기는 했지만, 도구가
실패를 어떻게 표현하는지가 에러 복구의 유효성을 좌우한다는 점에서 두 요소가 완전히
독립은 아니었습니다.

ReAct형의 분산이 컸다는 점도 적어 둡니다. 유효한 5런의 토큰이 4,396에서 36,592까지
8.3배 차이가 났는데, 매 스텝 무엇을 할지 다시 정하기 때문에 "통째로 읽고 세기"와
"시간대별로 정규식을 돌리기" 중 어느 쪽을 고르는지가 런마다 갈렸습니다.
Plan-then-Execute형은 계획이 이 선택을 한 번에 고정합니다.

## 승패를 단정하지 않는 이유

숫자만 보면 ReAct형이 유효 런 5개 중 3개, Plan-then-Execute형이 2개 중 1개를
성공해서 ReAct형이 나아 보입니다. 그러나 Plan-then-Execute형의 유효 런이 2개뿐이고,
무엇보다 스타터 상태의 두 하네스는 컨텍스트 관리와 종료 조건과 에러 복구를 동시에
다르게 잡고 있습니다. 위에서 종료 조건에 원인을 돌린 것은 로그를 읽어서 내린
판단이지 숫자로 분리한 것이 아닙니다.

종료 조건의 몫만 숫자로 떼어내려면 나머지를 고정한 채 종료 조건만 바꾼 런이
필요합니다. 그래서 `harness_plan_execute.py`에 `early_exit` 스위치를 넣었습니다.
기본값은 `False`라서 baseline은 스타터를 그대로 재현하고, 켜면 첫 `Answer:`에서
남은 계획을 버리고 끝냅니다. 다른 네 요소는 건드리지 않았으므로 baseline과의 차이는
종료 조건만의 몫이 됩니다. `run_ablation.py`가 이 변형을 3회 실행해 `note`에
`early_exit=1`로 기록합니다. iteration과 토큰이 ReAct형 수준으로 내려가되 성공
여부는 바뀌지 않을 것으로 예상하고 있습니다. 이 런은 하루 호출 한도가 리셋된 뒤에
추가할 예정입니다.

## 실행 방법

```bash
cd submissions/25510099/week-02
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<본인 OpenRouter 키>
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free

python run_ab.py --runs 3        # react 3회 + plan_exec 3회
python run_ablation.py --runs 3  # plan_exec 를 early_exit=True 로 3회
```

두 스크립트 모두 `results.csv`에 덧붙이고 `logs/`에 런당 한 파일을 남깁니다.
무료 티어는 하루 50회가 한도이고 ReAct형 런 하나가 최대 8회를 쓰므로,
`run_ab.py --runs 3`은 하루에 한 번 정도가 안전합니다.

구조 검사는 아래로 하였습니다.

```bash
python scripts/check_week02.py submissions/25510099/week-02
```
