# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

25510099 김대양

---

## 1. 변형 정의 (variant definition)

### 고정한 것

| 상수 | 값 |
|---|---|
| provider | OpenRouter (OpenAI-compatible API, `OPENAI_BASE_URL=https://openrouter.ai/api/v1`) |
| model | `nvidia/nemotron-3-super-120b-a12b:free` (`AGENT_MODEL`) |
| task | `app.log`에서 ERROR 줄이 가장 많은 시간대 (정답 `14:00`, ERROR 6건) |
| tools | `read_file(path)`, `count_pattern(path, pattern)` — 두 하네스가 `tools_shared.py`에서 동일하게 import |
| judge | `run_ab.py`의 부분문자열 판정. `expected: 14:00`은 **실행 전에** `TASK.md`에 커밋 |

`app.log`는 3,022 bytes로 `read_file`의 4,000자 컨텍스트 가드에 걸리지 않는다. 즉
**`read_file` 한 번이면 로그 60줄 전체가 들어온다.** 이 태스크는 도구 호출 1회로
풀 수 있고, 따라서 두 하네스의 차이는 "풀 수 있느냐"가 아니라 **"답을 안 뒤에도
얼마나 더 도느냐"** 로 나타난다. 이 점이 아래 해석의 전제다.

### 다르게 잡은 것 — 다섯 축 대조

| 축 | `harness_react.py` | `harness_plan_execute.py` | 변동 |
|---|---|---|---|
| 1. 컨텍스트 관리 | `Chat` 1개. 전 히스토리 누적, 매 호출이 모든 Thought/Observation을 봄 | `Chat` 2개로 분리. `planner`는 `tools=False`로 **도구 Observation을 평생 못 봄**. 재계획 시에도 300자로 자른 실패 메시지만 받음 | **다름** |
| 2. 도구 granularity | `tools_shared`에서 import | 동일하게 import | **같음 (통제변수)** |
| 3. 종료 조건 | 모델이 tool_call 없이 답하면 종료, 또는 `max_steps=8` | **계획 길이가 종료를 결정.** 스텝당 `max_tool_rounds=3`, `max_replan=1`, 이후 최종답변 강제 호출 | **다름** |
| 4. 에러 복구 | 도구 예외를 `"error: {e}"` Observation으로 되돌림. `max_steps` 내 무제한 자가수정 | 도구 예외는 동일. 그러나 계획 레벨 실패는 모델이 `OFF_PLAN:`을 선언해야 하고 재계획은 1회. **플랜 JSON 파싱 실패는 즉시 하드 중단** | **다름** |
| 5. 인간 개입 | `IRREVERSIBLE = set()` + `ask_human()` 훅 존재하나 빈 집합 | 훅 자체 없음 | 측정 불가 (아래) |

**축 2는 의도적으로 고정한 통제변수다.** 두 하네스가 같은 `tools_shared`를
import하는지를 CI가 검사하는 이유이며, 지표 차이를 도구 탓으로 돌릴 수 없게 만든다.

**축 5는 이 설계에서 degenerate하다.** 스타터 도구가 둘 다 read-only라
`IRREVERSIBLE`이 비어 있고, `interventions`는 15행 전부 0이다. 이 컬럼은 측정된
것이 아니라 **측정될 수 없었던 것**이다. 값을 얻으려면 쓰기/삭제 도구를 추가해야
하는데, 그러면 축 2(도구 집합)까지 같이 움직여 A/B가 깨진다.

### 사전 등록 가설

실행 전에 적어둔 예측이다.

> 태스크는 도구 호출 1~2회면 끝난다. ReAct는 2~3 iteration에 수렴할 것이다.
> Plan-then-Execute는 계획이 4~6스텝으로 나오고 **스텝마다 모델 호출이 강제**되므로
> 6~9 iteration이 될 것이다. 따라서 **축 3(종료 조건)이 iteration과 token을 지배**한다.
> PE의 고유 실패 모드는 축 4의 하드 중단, 즉 플래너가 bare JSON을 못 뱉는 경우다.

---

## 2. 측정표 (measurements)

`results.csv` 원본. 실패 런은 전부 남겼다.

### rows 1–6 — 스타터 원본

| run | harness | success | tokens | iters | interv. | note |
|---|---|---|---|---|---|---|
| 1 | react | X | 26,552 | 8 | 0 | |
| 2 | react | **O** | 4,396 | 2 | 0 | |
| 3 | react | X | — | — | — | crash: TypeError (502) |
| 4 | plan_exec | X | 288 | 1 | 0 | replans=0 |
| 5 | plan_exec | X | — | — | — | crash: TypeError (502) |
| 6 | plan_exec | **O** | 28,689 | 9 | 0 | replans=0 |

### rows 7–12 — transport 재시도 패치 적용 후

| run | harness | success | tokens | iters | interv. | note |
|---|---|---|---|---|---|---|
| 7 | react | **O** | 36,592 | 8 | 0 | |
| 8 | react | **O** | 9,656 | 3 | 0 | |
| 9 | react | X | 30,921 | 8 | 0 | |
| 10 | plan_exec | X | — | — | — | crash: RateLimitError 429 |
| 11 | plan_exec | X | — | — | — | crash: RateLimitError 429 |
| 12 | plan_exec | X | — | — | — | crash: RateLimitError 429 |

### 집계 (인프라 crash 제외)

| | 유효 런 | 성공 | tokens mean | tokens 범위 | iters mean |
|---|---|---|---|---|---|
| react | 5 | 3/5 | 21,623 | 4,396 – 36,592 (**8.3배**) | 5.8 |
| plan_exec | 2 | 1/2 | 14,488 | 288 – 28,689 | 5.0 |

**핵심 대응쌍** — 같은 첫 도구 호출(`read_file('app.log')`)로 같은 정답에 도달한 두 런:

| | iters | tokens |
|---|---|---|
| react-02 | 2 | 4,396 |
| plan_exec-06 | 9 | 28,689 |
| **비율** | **4.5배** | **6.5배** |

### 인프라 실패에 대한 기록

유효 데이터를 잃은 6행은 모두 하네스가 아니라 provider 문제다.

- **502 provider_unavailable** (rows 3, 5): OpenRouter가
  `{"choices": null, "error": {"code": 502, "message": "Upstream error from Nvidia:
  Service temporarily overloaded"}}`를 반환하고, `tools_shared._send_openai`의
  `resp.choices[0]`이 `TypeError: 'NoneType' object is not subscriptable`로 터졌다.
  별도 진단 호출에서 4회 중 1회 재현됐다. `_create_with_retry`(지수 백오프 4회)로
  패치했고, **두 하네스에 동일 적용되므로 A/B를 편향시키지 않는다.** rows 1–6은
  패치 이전 코드로 실행된 것이다.
- **429 free-models-per-day** (rows 10–12): OpenRouter 무료 티어 일일 한도
  50 requests 소진 (`X-RateLimit-Limit: 50`, `Remaining: 0`). 재시도로 흡수할 수
  없는 계정 레벨 한도다.

무료 티어의 가용성 자체가 재현성의 제약이라는 점을 기록해 둔다. 같은 코드와 같은
설정으로도 하루 50호출 안에서만 재현 가능하며, ReAct 런 하나가 최대 8호출을 쓴다.

---

## 3. 해석 (interpretation)

**이 태스크에서 지표를 움직인 것은 축 3(종료 조건)이었고, 그 방향은 두 하네스에서
서로 달랐다.** Plan-then-Execute에서 축 3은 비용을 만들었다. `plan_exec-05`와
`-06`의 로그를 보면 두 런 모두 **step 1에서 이미 `Answer: 14:00`을 냈다** —
`read_file` 한 번으로 로그 전체가 들어왔으니 당연하다. 그런데 이 하네스는 종료를
계획의 길이로 정하기 때문에 답을 손에 쥔 채로 "Split the log into lines",
"Maintain a dictionary counting occurrences per hour" 같은 남은 스텝을 계속
행진했고, `plan_exec-06`은 그 대가로 react-02 대비 **4.5배 iteration과 6.5배
토큰**을 썼다. 같은 모델이 같은 도구로 같은 답을 같은 첫 호출에 얻었는데 비용이
6.5배 갈린 것이므로, 이 차이는 모델도 도구도 아닌 종료 조건에 귀속된다. 반대로
ReAct에서 축 3은 **실패를 만들었다.** `react-01`은 `count_pattern`으로 시간대별
카운트를 09→1, 10→2, 12→3, **14→6**까지 이미 관찰한 상태에서 `max_steps=8`에
잘려 `MAX_STEPS reached: incomplete`로 끝났다. 정보가 부족해서가 아니라 예산이
부족해서 실패한 것이다. 같은 축의 값 하나(상한을 둘 것인가, 계획 길이로 정할
것인가)가 한쪽에서는 토큰 낭비로, 다른 쪽에서는 오답으로 나타났다.

축 1(컨텍스트 분리)은 **PE의 계획 품질**에서 드러났다. 플래너는 `tools=False`로
도구를 한 번도 써보지 못한 채 계획을 쓴다. `plan_exec-04`에서 플래너는 JSON
리스트 대신 `{"path": "app.log"}` — **툴콜 모양의 객체**를 반환했고, `parse_plan`이
`None`을 돌려주면서 288 토큰, 1 iteration 만에 하드 중단됐다. 이것이 축 4에서
예고한 PE의 고유 실패 모드이며, 실험 전체에서 가장 싸게 끝난 실패다. 살아남은
계획들도 "Maintain a dictionary counting occurrences per hour"처럼 주어진 두
도구로는 **문자 그대로 실행할 수 없는** 스텝을 담고 있었고, executor는 그 스텝을
실제로 수행하는 대신 서술로 때웠다(`[step 2] Step 2 completed: log split into
lines.`). 도구 Observation을 못 보는 컨텍스트에서 쓰인 계획은 도구의 실제 능력과
어긋난다.

축 4에 대해서는 예상하지 못한 관찰이 하나 있다. **ReAct의 "에러를 Observation으로
되돌린다"는 장치가 이 태스크에서는 거의 작동하지 않았다.** `count_pattern`은 잘못된
정규식에 대해 예외를 던지지 않고 `0`을 반환하기 때문이다. `react-09`에서 모델은
이중 이스케이프된 `\\d`를 보내 0을 받았고, 그것이 "매치가 없다"인지 "패턴이
틀렸다"인지 구분할 수 없어 6스텝을 정규식 시행착오에 쓰다가 `max_steps`에 잘렸다.
에러 복구 축은 에러가 **에러로 표면화될 때만** 의미가 있고, 도구가 실패를 정상값으로
포장하면 축 4는 무력해지고 그 비용은 축 3(반복 상한)이 치른다. 축 2를 통제변수로
고정했지만, 도구의 실패 표현 방식이 축 4의 유효성을 좌우한다는 점에서 두 축은
독립이 아니었다.

마지막으로 **승패를 선언하지 않는 이유**를 적는다. react가 유효 런 3/5, plan_exec가
1/2로 보이지만 plan_exec의 유효 런은 두 개뿐이고, 스타터 상태의 두 하네스는 축
1·3·4를 **동시에** 다르게 잡고 있어 위 귀속은 로그의 정성적 증거에 기대고 있다.
축 3의 기여를 수치로 분리하려면 다른 축을 고정한 채 종료 조건만 바꾼 런이 필요하다.
이를 위해 `harness_plan_execute.py`에 `early_exit` 스위치를 넣어 두었다(기본값
`False`, 즉 baseline은 스타터를 그대로 재현한다). 켜면 첫 `Answer:`에서 남은 계획을
버리고 종료하며, 다른 네 축은 건드리지 않는다. `run_ablation.py`가 이 변형을 3회
실행해 `note=early_exit=1`로 기록한다. 예측은 **iteration과 token이 react 수준으로
내려가되 성공 여부는 바뀌지 않는다**이다. 이 런은 일일 쿼터 리셋 이후에 추가한다.

---

## 재현 방법

```bash
cd submissions/25510099/week-02
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your openrouter key>     # 키는 커밋하지 않는다
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free

python run_ab.py --runs 3        # baseline: react 3회 + plan_exec 3회
python run_ablation.py --runs 3  # 축 3 ablation: plan_exec early_exit=True 3회
```

두 스크립트 모두 `results.csv`에 **append**하고 `logs/`에 런당 1파일을 남긴다.
일일 무료 한도 50 requests를 넘기면 429로 런이 통째로 실패하므로, 하루에
`run_ab.py --runs 3`은 한 번 정도가 안전하다.

구조 검사:

```bash
python scripts/check_week02.py submissions/25510099/week-02
```

## 파일

| 파일 | 내용 |
|---|---|
| `tools_shared.py` | 도구 2종, provider 중립 모델 호출, `Meter`, transport 재시도 |
| `harness_react.py` | ReAct 하네스 (스타터 그대로) |
| `harness_plan_execute.py` | Plan-then-Execute 하네스 + `early_exit` ablation 스위치 |
| `run_ab.py` | baseline A/B 러너 (스타터 그대로) |
| `run_ablation.py` | 축 3 ablation 러너 |
| `TASK.md` | 태스크와 성공 판정 기준 (`task:`, `expected:`) |
| `app.log` | 참조 입력, 변경 없음 |
| `results.csv` | 런당 1행 |
| `logs/` | 런당 콘솔 캡처 1파일 |
