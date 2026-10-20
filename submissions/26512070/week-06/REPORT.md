# Week 06 — 내 하네스를 A2A 가게와 손님으로

학번 26512070

> 작성 방식: 거래 전략, 반복 횟수, 한도를 지키는 방식, 검증 기준은 학생이 정했고, 코드는 Claude Code가 그 결정대로 구현했다. 단계마다 커밋으로 남겼다(`1cf9d85` … `8c37e33`).

## 요약

| 항목 | 값 |
|---|---|
| 하네스 / 모델 | Claude Code 2.1.296, `claude -p --model haiku` (내장 도구 끔), 우리가 만든 ReAct 루프 안에서 호출 |
| 최종 trade test | **21/21 PASS**, 236초, 하네스 호출 51번 (평균 4.2초, 최대 5.3초) |
| 첫 trade test | 21/21 PASS, 745초. 하네스 호출이 한 번 멈춰서 방어선이 1번 작동 |
| 한도 위반 | 0건 (하한가 아래 호가나 판매, 예산 초과 제안이나 구매 없음) |
| 방어선(GUARD) 작동 | 최종 실행 0번 |
| ReAct 스텝 | 51번의 결정이 모두 1스텝 안에 끝남 |
| 교차 검증 (`verify_trades.py`) | 흥정 13건, 문제 0건, 전략 어긋남 0건 |

---

## 설계 근거: 참고한 구조와 규칙

거래 시나리오와 구조는 아래 자료에서 가져왔다.

| 참고한 것 | 가져온 것 |
|---|---|
| 6주차 A2A v1.0 (노트 A3·A4, 규격 3·4·8절) | Agent Card를 `/.well-known/agent-card.json`에 공개. 흥정 하나를 task 하나로 보고, 상태는 `INPUT_REQUIRED`(흥정 중), `COMPLETED`(성사), `REJECTED`(가게가 끝냄), `CANCELED`(손님이 그만둠)로 표현. 결과는 artifact로 전달 |
| 6주차 실습 `seller_agent.py` | 가게 server의 뼈대(카드, `TaskUpdater`, task id별 거래 상태). 규칙 부분만 하네스 호출로 바꿈 |
| 6주차 trade test 규칙 | 하한가 100/180/260, 테스트 손님은 60·80·100·110·120 순서로 최대 8번, 테스트 가게는 낮은 제안 5번에 거절. 이 수치로 전략을 미리 시뮬레이션함 |
| 1주차 "에이전트 = 모델 + 컨텍스트 + 도구 루프" | 하네스는 `claude -p`(글을 넣으면 글이 나오는 모델)로만 쓰고, 루프·컨텍스트·도구는 우리 코드에 둠 |
| 1주차 "도구 설명이 곧 인터페이스" | 도구와 최종 행동마다 한 줄 설명. 필요 없는 개념은 넣지 않음 |
| 2주차 ReAct와 하네스 다섯 요소 | 생각 → 행동 → 관찰 루프, 종료 조건(최대 3스텝, 최종 행동), 에러 복구(형식 오류·규칙 위반·하네스 오류를 관찰로 돌려줌) |
| 3주차 "실제 결과를 정답으로" | 모델의 말이 아니라 상대 쪽(테스트 키트)의 기록과 맞춰 보는 교차 검증 |
| 4주차 FIPA-ACL과 화행 | A2A에는 accept-proposal 같은 행위 이름이 없음. 수락의 모양을 우리가 정해야 함 → (4)번 |
| 5주차 "한도는 프롬프트만으로 지키지 않는다" | 한도를 프롬프트, 루프 검증, 배관 방어선의 세 겹으로 지킴 |

---

## (1) 하네스, 모델, 실행 명령

- **하네스:** Claude Code 2.1.296. 모델은 haiku.
- **호출 명령** (`model.py`): 프롬프트는 표준 입력으로 넘긴다.
  ```
  claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence --system-prompt "<역할과 전략>"
  ```
  빈 임시 폴더에서 실행해서 프로젝트의 지시 파일(CLAUDE.md 등)을 읽지 않게 했다. `--tools ""`로 Claude Code의 내장 도구를 껐다. 에이전트가 쓰는 도구는 우리 루프 안에 있다.
- **`agent.json`의 명령:** `py seller.py --port {port}`, `py buyer.py --url {url} --item {item} --budget {budget}`. Windows의 `py` 런처로 실행한다.
- **패키지:** `a2a-sdk` 1.2.2, `uvicorn` 0.54.0, `httpx` 0.28.1, Python 3.14.

---

## (2) 에이전트가 결정하는 방식

### 전체 구조

```
                 A2A v1.0 (JSON-RPC, A2A-Version: 1.0)
 손님 (buyer.py) ───────────────────────────────▶ 가게 (seller.py)
   │  카드 읽기, taskId로 이어 보내기, CancelTask      │  카드 공개, task 상태, 영수증·거래기록 artifact
   ▼                                                  ▼
 buyer_brain.decide()                           seller_brain.decide()
   │  전략은 프롬프트로, 숫자는 컨텍스트로              │
   ▼                                                  ▼
 react.run()  ── 생각 → 도구 또는 최종 행동 → 관찰 (최대 3스텝) ──
   │                                                  │
   ▼                                                  ▼
 claude -p --model haiku                        claude -p --model haiku
```

코드는 세 층으로 나눴다.

| 층 | 파일 | 하는 일 | 결정을 하나? |
|---|---|---|---|
| 배관 (A2A) | `seller.py`, `buyer.py` | A2A로 말하기, 거래 상태 보관, 결정을 task 상태로 바꾸기, 최후 방어선 | 하지 않음 (방어선만 덮어씀) |
| 두뇌 | `seller_brain.py`, `buyer_brain.py`, `react.py` | 컨텍스트 조립, 도구 제공, ReAct 루프 | **하네스가 결정** |
| 도구와 기록 | `inventory.py`, `wallet.py`, `trade_record.py` | 재고·가격·예산 조회와 검사, 거래 기록 | 하지 않음 |

### 가게와 손님의 역할 (A2A 쪽)

- **가게 (A2A server):** Agent Card를 공개한다. 첫 메시지에서 물건을 찾고, 손님 메시지마다 두뇌에게 결정을 묻고, 그 결과를 task 상태로 바꾼다. 역제안은 `INPUT_REQUIRED`이고 문장 끝에 `My price: N.`을 붙인다. 성사되면 `receipt` artifact를 붙이고 `COMPLETED`, 거절이면 `REJECTED`로 끝낸다. 거래가 끝날 때마다 흥정 전체를 담은 `trade-record` artifact를 붙이고, 판매되면 재고를 1 줄인다.
- **손님 (A2A client):** 카드를 읽고 `JSONRPC 1.0`인 첫 주소를 고른다. 모든 요청에 `A2A-Version: 1.0`을 붙이고, 두 번째 메시지부터 `taskId`를 붙인다. 가게 메시지마다 두뇌에게 결정을 묻고, 그만둘 때는 `CancelTask`를 보낸다. 손님은 A2A artifact를 낼 수 없으므로, 가게의 영수증과 거래 기록을 받아 자기 기록에 붙인다.

### 하네스의 역할: 전략은 프롬프트, 숫자는 컨텍스트, 결정은 Claude

전략은 코드의 `if`문이 아니라 **system prompt**로 주었다. 하네스가 모든 결정을 해야 하기 때문이다. 코드는 숫자(정가, 중간값, 하한가, 시작가)를 계산해 컨텍스트에 넣기만 한다.

| | 가게 | 손님 |
|---|---|---|
| 목표 | 손님이 낼 수 있는 가장 좋은 값에 팔되, 하한가 아래로는 절대 안 팜 | 예산 안에서 최대한 싸게 사거나 그만둠 |
| 전략 (프롬프트) | 1) 첫 답은 정가 2) 손님 제안이 하한가 이상이면 수락 3) 아니면 중간값을 한 번 4) 그다음은 하한가 | 1) 예산의 60%로 시작 2) 가게 값이 예산 안이면 그 값으로 3) 아니면 예산값을 한 번 4) 그래도 안 내려오면 그만두기 |
| 컨텍스트 | 물건, 재고, 정가·중간값·하한가, 내 호가들, 손님 제안들, 손님 원문, 지난 방어선 기록 | 물건, 가게 카드의 설명·skill·예시, 예산, 시작가, 내 제안들, 가게 호가들, 가게 원문, 지난 방어선 기록 |
| 도구 | `get_item()`, `check_ask(price)` | `check_offer(price)` |
| 최종 행동 | `counter(price, say)`, `accept(say)`, `reject(say)` | `offer(price)`, `walk_away()` |

**답의 모양:** 매 스텝 JSON 하나 `{"thought": "...", "action": "...", "args": {...}}`를 받는다. 실제 결정 예시(최종 실행의 가게 로그):

```
[react] final after 1 step(s): {"thought": "The offer of 60 is below the reserve of 100, and the middle price of 115
has not been asked yet, so counter at the middle price.", "action": "counter", "args": {"price": 115, ...}}
```

### ReAct 루프 (`react.py`)

한 번 답할 때 최대 3스텝을 돈다. 각 스텝의 결과는 다음 스텝의 **관찰**이 된다.

| 모델의 답 | 관찰로 돌려주는 것 |
|---|---|
| 도구 호출 | 도구의 결과 (예: `(False, '90 is below the reserve 100 for lamp')`) |
| 규칙을 어기는 최종 행동 | `REJECTED BY RULES: <이유>. Choose again.` (배관 방어선과 같은 검사를 루프가 먼저 함) |
| JSON이 아닌 답 / 모르는 행동 | `FORMAT ERROR ...` / `unknown action ...` |
| 하네스 오류나 멈춤 (30초 초과) | `harness error: ...` → 다음 스텝이 재시도 |

3스텝 안에 유효한 최종 행동이 나오지 않으면 결정 없음을 돌려주고, 배관 방어선이 처리한다. 지난 턴에 방어선이 끼어든 기록은 다음 턴 컨텍스트에 "Earlier in this deal ... overrode you"로 들어가서 같은 실수를 반복하지 않게 한다. 루프는 가짜 모델로 먼저 시험했다(`test_react.py`, 8/8).

### 한도를 지키는 곳: 프롬프트만이 아니라 세 겹

| 겹 | 어디 | 막는 방법 | 최종 실행에서 막은 횟수 |
|---|---|---|---|
| ① 프롬프트 | system prompt | "하한가 아래로 절대 안 됨", "예산을 넘지 말 것" | (모델이 따름) |
| ② 루프 검증 | `react.py` + 두뇌의 `validate()` | 규칙을 어긴 최종 행동을 받지 않고 관찰로 되돌림. 손님은 예산값을 두 번째로 부르는 것도 막아 무한 반복을 방지 | 0 |
| ③ 배관 방어선 | `seller.py` / `buyer.py`의 `guard()` | 가게: 직전 호가를 유지. 손님: 그만두기. 로그에 `GUARD`로 남김 | 0 |

방어선은 협상가가 아니라 안전망이다. 하한가와 예산은 지키지만 거래를 만들지는 못한다. 그래서 테스트 통과는 ①과 ②에서 하네스가 제대로 결정하는 데 달려 있다.

### 무엇을 어디에 기록하나

| 기록 | 위치 | 내용 |
|---|---|---|
| 하네스 호출 | `logs/seller-under-test.log`, `logs/buyer-under-test-*.log` | 매 호출의 `[model 4.3s exit=0]`, 보낸 프롬프트 전체, 받은 답 전체 |
| 루프와 결정 | 같은 로그 | `[react] final after N step(s)`, `decision: {...}`, 방어선이 끼어들면 `GUARD: 이유 (brain said ...) -> 대신 한 것` |
| A2A 메시지 | `logs/trade-test.log`, 손님 로그 | 보낸 메시지(`->`), 받은 상태와 문장(`<-`), artifact |
| 거래 한 건 | `trade-record` artifact, `trades/seller.jsonl`, `trades/buyer.jsonl` | 결과(성사/거절/취소), 최종 가격, 턴별 가격과 문장, 방어선 기록, **결정마다의 ReAct 스텝(thought)** |
| 손님의 지갑 | `trades/buyer.jsonl`의 `wallet` | 예산, 부른 값들, 산 물건 |
| 테스트 결과 | `test-report.json` | trade test가 생성 |

---

## 목적과 검증: 목적을 얼마나 지켰나

### 목적
1. **하네스가 모든 결정을 한다.** 코드는 A2A로 말하고 숫자를 알려 줄 뿐이다.
2. **한도를 절대 넘지 않는다.** 가게는 하한가, 손님은 예산.
3. **정한 전략대로 움직인다.** 가게는 정가 → 중간값 → 하한가, 손님은 60% → 예산 → 그만두기.
4. **A2A 규격대로 말한다.** 카드, taskId, 헤더, task 상태.
5. **모든 결정의 근거가 남는다.** 나중에 로그만 보고 누가 왜 그렇게 했는지 가릴 수 있다.
6. **시간 안에 끝난다.** 손님 300초, 가게 답 하나당 200초.

### 확인 방법: `verify_trades.py`

trade test의 PASS만으로는 "하네스가 결정했는지", "전략을 지켰는지"를 알 수 없다. 그래서 **키트 쪽 로그와 우리 쪽 로그를 메시지 하나하나 짝지어** 맞춰 보는 검사기를 만들었다(3주차의 "실제 결과를 정답으로"). 키트가 보낸 메시지가 우리 에이전트에 그대로 도착했는지, 키트가 받은 가격이 Claude가 정한 가격과 같은지, 그 결정이 방어선이 아니라 Claude 호출에서 나왔는지를 확인한다.

### 결과 (최종 실행)

```
seller_deal #1           lamp      asks [130, 115] offers [60, 115] -> COMPLETED at 115 | Claude calls 3 | strategy kept
seller_holds_reserve #1  bookshelf asks [338, 299, 260, 260, 260, 260, 260, 260, 260] offers [140, 170, 200, 230, 230, 230, 230, 230] -> INPUT_REQUIRED | Claude calls 9 | strategy kept
buyer_deal #1            lamp      budget 120 offers [72, 101] -> deal [101.0] | Claude calls 2 | strategy kept
buyer_holds_budget #1    bookshelf budget 230 offers [138, 230] -> walked away | Claude calls 3 | strategy kept
...
GUARD overrides in this run: 0
strategy deviations: 0
problems (flow / harness / limits / protocol): 0
```

| 목적 | 근거 | 결과 |
|---|---|---|
| 1. 하네스가 결정 | 51번의 결정이 모두 Claude 호출과 `[react] final`에서 나옴, 방어선 0회 | ✅ |
| 2. 한도 | 가게 호가 최저 260(bookshelf)과 115(lamp), 판매가 115. 손님 제안 최고 230(예산 230)과 101(예산 120) | ✅ 위반 0 |
| 3. 전략 | 13건 모두 전략과 같은 수를 둠. 가게는 lamp를 하한가 100이 아니라 중간값 **115에 판매** | ✅ 어긋남 0 |
| 4. A2A | 헤더 없는 요청 0, follow-up 모두 taskId, 끝난 task에 `-32004` | ✅ |
| 5. 근거 | 결정마다 thought가 로그와 trade-record에 남음. 예: 손님 "The seller's ask of 260 is above my 230 budget, and I've already offered my full budget, so I should walk away." | ✅ |
| 6. 시간 | 전체 236초, 결정 하나 최대 5.3초 | ✅ |

**검사기 자체의 검증:** "전부 정상"이라고만 하는 검사기는 고장 났을 수 있어서 두 가지로 시험했다(scratch 복사본, 커밋 `222879a`, `8c37e33`의 메시지에 기록).
- **일부러 규칙을 어기게 만든 두뇌**(하한가 아래 호가, 낮은 값 수락, 예산 초과, 충돌)로 trade test를 돌림: 한도 위반은 0건이었고 방어선이 54번 작동했다. 검사기는 방어선이 끼어든 결정을 모두 "not a clean Claude decision"으로 잡았다.
- **로그를 조작함**(호가 115 → 95, 구매 101 → 130): 검사기가 `LIMIT: asked 95.0 below the reserve 100`, `LIMIT: bought over the budget 120: [130.0]`, 그리고 키트 기록과 우리 결정의 불일치를 모두 잡았다.

---

## (3) 실패한 것과 고친 것

**1. 하네스 호출 하나가 멈춤 → 방어선이 끼어듦 (첫 trade test)**

첫 실행은 21/21을 통과했지만 745초가 걸렸다. bookshelf 흥정에서 가게 두뇌가 시간 초과로 실패했고, 방어선이 정가 338을 그대로 유지했다. 첫 실행의 `logs/seller-under-test.log`(커밋 `c3a6d6a`):

```
GUARD: unknown decision {'action': 'brain_error', 'error': 'TimeoutError()'} (brain said {'action': 'brain_error', 'error': 'TimeoutError()'}) -> {'action': 'counter', 'price': 338, 'say': 'That does not work for us.'}
[react] out of time before step 2
[react] no valid final action in 1 step(s)
```

키트 쪽에서도 같은 흔적이 보인다. 다른 두 번과 달리 338이 두 번 나온다:

```
PASS  seller_holds_reserve #3: TASK_STATE_INPUT_REQUIRED, asks [338, 338, 299, 260, 260, 260, 260, 260, 260]
```

원인은 이랬다. 평소 4~8초 걸리는 `claude -p` 호출 하나가 멈췄는데, 호출 하나의 대기 한도가 120초라서 ReAct가 재시도할 시간도 없이 가게 답 하나의 한도(170초)를 넘겼다. 하한가는 지켜졌지만 손님은 그동안 기다렸고, 그 턴의 결정은 하네스가 아니라 방어선이 했다.

**고친 것** (`bea7d87`):
- 호출 하나의 한도를 30초로 줄였다. 멈추면 끊고 다음 스텝에서 재시도한다.
- 남은 시간이 적으면 그만큼만 기다리게 했다.
- 방어선 로그의 "unknown decision"을 "the brain failed: TimeoutError"처럼 정확한 이유로 바꿨다.
- 이 경우를 `test_react.py`에 추가했다 ("harness hangs once, retried next step").

다시 돌린 결과 **236초, 방어선 0회**였다.

**2. 손님이 예산값을 끝없이 반복할 위험 (설계 단계)**

처음 정한 손님 전략은 "가게 값이 예산 밖이면 예산값을 부른다"였다. 테스트 가게 규칙으로 시뮬레이션해 보니, bookshelf(예산 230, 하한가 260)에서 230 → 260 → 230 → 260이 끝없이 반복됐다. 프롬프트에 "예산값을 이미 불렀는데 안 내려오면 그만두기"를 넣고, 루프 검증에서도 두 번째 예산값 제안을 막았다. 최종 실행에서 손님은 `offers [138, 230]` 다음에 그만뒀다(`walked away (CancelTask) -> TASK_STATE_CANCELED`).

**3. 가게가 항상 최저가에 파는 전략 (설계 단계)**

처음 정한 가게 전략은 "하한가 아래면 바로 하한가로"였다. 테스트는 통과하지만 lamp가 항상 100에 팔린다. 중간값을 한 번 거치게 바꿨다. 테스트 손님은 호가가 예산 120 안이면 바로 그 값을 보내므로, 시뮬레이션상 메시지 수(곧 하네스 호출 수)는 같다. 실제로 lamp는 **115에 팔렸고** 흥정은 메시지 3개로 끝났다(`receipt price 115.0, asks [130, 115]`, 하네스 호출 3번).

**4. Windows에서 가게 프로세스가 남음 (관찰)**

trade test는 가게를 `shell=True`로 띄운다. Windows에서는 종료할 때 cmd만 꺼지고 그 아래의 `py`와 `python` 프로세스가 남는다. 테스트가 끝날 때마다 프로세스 목록에 `seller.py --port <포트>`가 남아 있어서 직접 정리했다. 포트가 매번 달라서 테스트 결과에는 영향이 없었지만, 남은 프로세스가 로그 파일을 잡고 있어서 검증용 복사본 폴더를 지우지 못한 적이 있다. 테스트 키트(읽기 전용)의 문제라 고치지 않고 기록만 남긴다.

---

## (4) A2A가 정해 준 것과 밖에서 맞춰야 했던 것

A2A는 **누구와, 어떤 거래를, 어떤 상태로** 주고받는지를 정해 주었다. 카드를 `/.well-known/agent-card.json`에서 받아 `supportedInterfaces`로 주소와 방식을 고르는 것, 흥정 하나가 task 하나가 되어 `taskId`로 이어지는 것, `INPUT_REQUIRED`·`COMPLETED`·`REJECTED`·`CANCELED`로 흥정의 진행과 끝을 나타내는 것, 끝난 task에 보내면 `-32004`가 오는 것, 결과를 artifact의 data Part로 담는 것, `A2A-Version` 헤더까지는 양쪽이 따로 약속할 필요가 없었다. 하지만 **메시지의 뜻**은 정해 주지 않았다. 4주차의 FIPA-ACL에는 `accept-proposal`이 있었지만 A2A에는 행위 이름이 없어서, 가게와 손님이 A2A 밖에서 맞춘 것이 많다. 물건은 자유 문장 "I want to buy a lamp" 안의 단어로 찾는다. 가격은 "메시지의 **마지막 숫자**"로 읽기로 해서, 가게는 Claude의 문장 뒤에 `My price: 115.`를 붙이고 Claude에게는 문장에 숫자를 넣지 말라고 했다. 키트 가게는 같은 뜻을 `Counteroffer: 101.`로 썼다. **수락에는 모양이 없어서** 손님은 가게의 호가와 같은 숫자를 다시 보내는 것으로 수락하고, 그 숫자가 수락인지 새 제안인지는 가게가 "하한가 이상이면 판다"는 자기 규칙으로 정한다. 영수증은 "숫자 `price` 키가 있는 첫 data Part"라는 키트의 약속을 따랐고, 그래서 우리 `trade-record` artifact에는 일부러 최상위 `price` 키를 넣지 않았다. 통화나 단위, "더는 못 내린다"는 최종 제안의 표시, 손님의 예산이나 가게의 하한가 같은 **위임 조건**을 적을 칸도 없다. 손님이 그만둘 때 `CancelTask`를 보낼지 그냥 멈출지도 각자의 선택이었다. 카드의 주장도 A2A가 확인해 주지 않으므로, 우리 카드에는 확인할 수 없는 말("가장 싼 가게") 대신 거래 후에 실제 artifact로 확인할 수 있는 약속("끝난 흥정마다 trade-record를 붙인다")만 적었다.

---

## 한계

- 반복은 테스트 규칙대로 각 3번뿐이다. 모델 답은 매번 다를 수 있어서 많이 돌리면 전략 어긋남이 나올 수 있다. 검사기로 다시 확인할 수 있다.
- 전략이 단순해서 대부분 1스텝에 결정했다. 도구(`check_ask`, `check_offer`)는 최종 실행에서 쓰이지 않았고, 루프 검증과 방어선도 작동할 일이 없었다. 더 어려운 상대(가격을 속이거나 말로 협상하는 가게)에서는 다르게 나올 수 있다.
- 거래 간 기억이 없다. `trades/*.jsonl`에 쌓이지만 다음 흥정에서 다시 읽지 않는다.
- 다른 학생의 에이전트와 교차 거래(선택 과제)는 하지 않았다.
