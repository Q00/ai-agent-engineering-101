# Week 06 — 하네스를 A2A 뒤에 두기: 가게와 buyer

학번 26520057 · trade test 21/21 통과 (마지막 실행 `b301c85`, 5분 22초, 하네스 호출 41번: 가게 23, buyer 18)

---

## 1. 하네스, 모델, 명령

| 항목 | 값 |
|---|---|
| 하네스 | Claude Code 2.1.241, 비대화형 모드 |
| 모델 | haiku. 가게와 buyer가 같은 모델을 쓴다 |
| 명령 | `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence` |
| 부르는 곳 | [`harness.py`](harness.py)의 `ask(prompt)`. 프롬프트를 표준 입력으로 넣고 표준 출력을 답으로 읽는다 |
| 실행 위치 | 호출마다 새로 만든 빈 임시 폴더. 프로젝트의 지시 파일을 읽지 않게 하려는 것이다 |
| 기억 | 없다. 호출 하나가 새 세션이다. 지금까지 오간 말은 코드가 매번 프롬프트에 넣는다 |
| A2A | `a2a-sdk` 1.2.2, JSON-RPC, v1.0. Python 3.12 |

`agent.json`의 명령은 `python seller_agent.py --port {port}`와 `python buyer.py --url {url} --item {item} --budget {budget}`이다.

## 2. 각 에이전트가 정하는 방법

### 가게 ([`seller_agent.py`](seller_agent.py))

실습의 규칙 가게(`ee415e4`)에서 값을 정하던 세 곳(첫 호가, 받아들일지, 역제안과 거절)을 하네스 호출 하나로 바꿨다. task를 만들고 잇는 부분은 그대로다.

프롬프트 (매 턴 같은 틀, `{situation}`만 바뀐다):

```
You are the seller in a price negotiation. You are selling one {item}.
Your reserve is {reserve}: the lowest price you may ask for or accept.

The negotiation so far:
{history}

{situation}

Answer with one JSON object and nothing else:
{"action": "counter" or "accept" or "reject", "price": <integer>}
```

- `{situation}`은 첫 메시지에서 `A buyer wants this item. Name your asking price (action: counter).`이고, 그 뒤로는 `The buyer now offers {offer}.`이다.
- `{history}`는 `seller: asking 150`, `buyer: offers 60` 같은 줄이다.
- 프롬프트에는 사실만 있다. 얼마씩 내릴지, 언제 받을지 같은 전략 문장은 넣지 않았다.
- 답은 JSON 하나다. 코드가 답에서 `{...}`를 찾아 읽는다. 읽지 못하면 첫 호가는 실습의 규칙(reserve × 1.3)으로, 그 뒤로는 직전 호가를 그대로 둔다.

**reserve는 프롬프트와 코드 두 곳에 있다.** 코드가 하는 일은 둘이다.

- 하네스가 reserve보다 낮은 호가를 내면 reserve로 올려서 보낸다.
- 하네스가 reserve보다 낮은 제안을 `accept`하면 받지 않고 reserve로 역제안한다.

두 경우 모두 가게 로그에 `[guard]` 줄이 남는다. 영수증의 `price`는 하네스가 낸 숫자가 아니라 buyer가 보낸 제안값이다.

### buyer ([`buyer.py`](buyer.py))

카드를 읽고, 카드의 `supportedInterfaces[0].url`로 `SendMessage`를 보낸다. 모든 요청에 `A2A-Version: 1.0`을 붙이고, 두 번째 메시지부터 `taskId`를 넣는다.

프롬프트:

```
You are the buyer in a price negotiation. You want to buy one {item}.
Your budget is {budget}: the most you may pay.

How you negotiate (find the seller's floor, then buy at it):
1. Your first offer is about half of the seller's asking price.
2. When the seller's counteroffer is within your budget, do not accept it at once: offer a little less, one time.
   When the seller repeats the same counteroffer, that price is the seller's floor: accept it if it is within your budget.
3. If the floor is above your budget, walk. The seller turns down at most 4 offers: after 4, accept the last
   counteroffer if it is within your budget, or walk.

The negotiation so far:
{history}

Answer with one JSON object and nothing else:
{"action": "offer" or "walk", "price": <integer>}
To accept the seller's price, offer exactly that price.
```

- 가운데의 전략 세 줄은 trade test 3까지는 없었다. 3-4에 넣은 이유와 결과를 적는다.

- `{history}`에는 가게가 보낸 문장이 그대로 들어간다 (`seller: lamp: asking 130. Send your offer as a number.`).
- `walk`이거나 답을 읽지 못하면 `CancelTask`를 보내고 끝난다.
- 한 번에 최대 10턴이다 (300초 안에 끝나게 하려는 것이다).

**budget도 프롬프트와 코드 두 곳에 있다.** 처음에는 프롬프트에만 두었고, 아래 3-3의 실패를 본 뒤 코드에 넣었다. 하네스가 budget보다 큰 값을 내면 코드가 budget으로 낮춰 보내고 `[guard]` 줄을 남긴다.

## 3. 통과하기까지 깨진 것과 바꾼 것

| 실행 | 커밋 | 결과 | 깨진 것 |
|---|---|---|---|
| 하네스 시험 호출 5번 | `6ba50df` | — | 5번 모두 reserve보다 낮은 값 |
| trade test 1 | `ee06177` | 12/21 | buyer 없음 (9개), 가게 답 2개를 읽지 못함 |
| trade test 2 | `f49db7d` | 20/21 | buyer가 budget을 넘겨서 삼 |
| trade test 3 | `7ad2ace` | 21/21 | — (buyer가 lamp를 평균 115에 삼) |
| trade test 4 | `b301c85` | 21/21 | — (buyer 프롬프트에 전략을 넣음. lamp를 100에 삼) |

### 3-1. "최저가는 100"이라고 말해도 하네스는 그 아래를 부른다

가게를 만들기 전에 하네스만 다섯 번 불렀다 ([`logs/probe-harness-1014-1019.txt`](logs/probe-harness-1014-1019.txt)).

```
prompt: You are a shop. A buyer offers 80 for a lamp. Your lowest price is 100. Reply with one number only: your counteroffer.
--- call 1
90
--- call 2
95
```

다섯 번의 답은 90, 95, 90, 95, 95였다. 그래서 reserve를 프롬프트와 코드 두 곳에 두기로 했다. trade test 1에서 이 방어가 한 번 걸렸다 (`ee06177`의 `logs/seller-under-test.log`).

```
[harness] bookshelf | The buyer now offers 140. -> counter 250
[guard] counter 250 < reserve 260 -> 260
```

이 줄이 없었다면 `seller_holds_reserve`가 실패했을 것이다. `[guard]` 줄은 trade test 2와 3에서는 한 번도 나오지 않았고, trade test 4에서는 두 번 나왔다 (240과 250을 260으로 올림). 방어가 필요한 답은 가끔 나온다.

### 3-2. `price`가 null인 답을 읽지 못했다

같은 실행에서 가게가 하네스의 답 두 개를 읽지 못했다.

```
[harness] The buyer now offers 200. -> unreadable: TypeError("int() argument must be a string, a bytes-like object or a real number, not 'NoneType'")
```

답 형식에 `"price": <integer>`라고 적었지만 하네스는 `price`를 null로 보냈다. 코드는 읽지 못한 답을 "직전 호가 유지"로 처리했기 때문에, 테스트는 통과했어도 하네스의 결정이 버려졌다. 그 답의 `action`이 무엇이었는지는 로그에 남기지 않아서 알 수 없다. `price`가 null이면 직전 호가를 쓰도록 고쳤다 (`6985643`).

### 3-3. buyer가 budget 230으로 책장을 270에 샀다

프롬프트에만 budget을 둔 buyer로 돌린 trade test 2의 `logs/buyer-under-test-buyer_holds_budget-1.log` (`f49db7d`):

```
<- TASK_STATE_INPUT_REQUIRED | bookshelf: asking 338. Send your offer as a number.
[harness] offer 270
[note] offer 270 > budget 230 (not stopped: the budget is prompt-only)
-> 270
<- TASK_STATE_COMPLETED | Deal: bookshelf at 270.
```

```
  FAIL  buyer_holds_budget #1: deals [270] (budget 230 < reserve 260)
```

같은 프롬프트로 나머지 두 번은 185 → 215 → 230, 195 → 225 → 230으로 올린 뒤 그만두었다. 세 번 가운데 한 번만 넘겼다. budget을 코드에도 넣었고 (`cb9eccc`), trade test 3에서 21/21이 됐다. trade test 3과 4에서 buyer의 제안은 모두 budget 이하여서 buyer의 `[guard]` 줄은 나오지 않았다.

### 3-4. 통과는 했지만 buyer가 비싸게 샀다

trade test는 값이 한도 안인지만 본다. 통과한 실행의 거래가를 따로 세어 보니 buyer가 덜 깎고 있었다.

| | 상대 | 거래가 | 가장 유리한 값 |
|---|---|---|---|
| 가게 (reserve 100) | trade test의 고정 buyer (budget 120) | 평균 115.6 (trade test 1~3, 9번) | 120 |
| buyer (budget 120), 전략 없음 | trade test의 규칙 가게 (reserve 100) | 평균 115 (trade test 2~3, 6번) | 100 |
| buyer (budget 120), 전략 있음 | 같은 가게 | 100, 100, 100 (trade test 4) | 100 |

전략이 없는 프롬프트의 buyer는 가게가 130을 부르면 첫 제안으로 110~120을 내고 바로 샀다 (`7ad2ace`의 `logs/buyer-under-test-buyer_deal-1.log`).

```
<- TASK_STATE_INPUT_REQUIRED | lamp: asking 130. Send your offer as a number.
[harness] offer 115
-> 115
<- TASK_STATE_COMPLETED | Deal: lamp at 115.
```

프롬프트에 "낮게 시작해서 가게의 역제안이 멈추는 값을 찾고 그 값에 산다"는 전략 세 줄을 넣었다 (`14c47fa`). trade test 4에서 세 번 모두 같은 순서로 100에 샀다.

```
[harness] offer 65
-> 65
<- TASK_STATE_INPUT_REQUIRED | 65 is too low. Counteroffer: 100.
[harness] offer 95
-> 95
<- TASK_STATE_INPUT_REQUIRED | 95 is too low. Counteroffer: 100.
[harness] offer 100
-> 100
<- TASK_STATE_COMPLETED | Deal: lamp at 100.
```

책장에서는 169를 낸 뒤 한 번 더 올리고(210, 215, 230), 역제안이 260에서 되풀이되자 그만두었다. 하네스 호출은 12번에서 18번으로 늘었다.

이 전략은 trade test의 규칙 가게가 어떻게 움직이는지 알고 쓴 것이다. 그 가게는 역제안을 reserve에서 멈추고, 낮은 제안이 다섯 번 오면 거래를 끊는다. 전략의 "4번"은 여기에 맞춘 숫자다. 다른 규칙이나 모델로 움직이는 가게에서 같은 결과가 나오는지는 확인하지 않았다. 우리 가게와 우리 buyer를 서로 붙여 본 적도 없다.

### 3-5. 실패가 아닌 것: 가게 로그의 traceback

모든 실행의 `logs/seller-under-test.log`에 `Failed to detach context` … `GeneratorExit` … `Task was destroyed but it is pending!`이 두 번 있다. `seller_terminal`이 끝난 task에 메시지를 보낼 때 a2a-sdk 안에서 나는 것이다. 하네스가 없는 실습의 규칙 가게(`ee415e4`)로 따로 확인했다. 거래만 15번 하면 0건이고, 끝난 task에 메시지를 한 번 보낸 뒤에는 2건이 나온다. 요청에는 규격대로 오류(`-32004`, terminal state)가 돌아가고 테스트 결과는 바뀌지 않는다.

## 4. A2A가 정해 준 것과 정해 주지 않은 것

가게와 buyer를 만들면서 본 것:

- A2A가 정해 준 것: 카드의 위치와 주소 칸, `SendMessage`와 `CancelTask`, task의 상태 이름, `taskId`로 잇는 방법, 끝난 task에 보낸 메시지가 오류가 된다는 것, 영수증을 artifact에 싣는 자리.
- 따로 맞춰야 했던 것:
	- 물건을 고르는 말. 가게는 첫 메시지의 글에서 `lamp`라는 낱말을 찾는다.
	- 호가를 적는 자리. trade test는 `input-required` 메시지의 **마지막 숫자**를 호가로 읽는다. 그래서 가게의 문장을 `60 is too low. Counteroffer: 110.`처럼 호가가 끝에 오게 코드에서 만든다.
	- 받아들인다는 표시. buyer는 "수락"이라는 말 대신 가게가 부른 값과 같은 숫자를 보낸다. 프롬프트의 마지막 줄이 이것이다.
	- 영수증의 열쇠 이름 `price`와 숫자라는 것. 화폐 단위는 어디에도 없다.

A2A가 정해 준 것은 말을 주고받는 틀이다. 가게가 어디에 있는지(카드), 흥정이 열려 있는지 끝났는지(task 상태), 같은 흥정을 어떻게 이어 가는지(`taskId`), 결과물을 어디에 싣는지(artifact)는 내가 정하지 않아도 됐다. 그래서 `a2a` CLI, trade test의 고정 buyer, 내 buyer가 같은 가게에 코드를 바꾸지 않고 말을 걸 수 있었다. 정해 주지 않은 것은 그 틀 안에 적는 내용이다. 값은 문장 속의 숫자로만 오가는데, 어느 숫자가 값인지는 양쪽이 따로 맞춰야 했다. trade test는 가게 문장의 마지막 숫자를 호가로 읽고, 규칙 가게는 buyer 문장의 첫 숫자를 제안으로 읽는다. 받아들인다는 표시도 규격에 없어서 "가게가 부른 값과 같은 숫자를 보낸다"로 정했다. buyer가 lamp를 115가 아니라 100에 산 것도 A2A 덕이 아니었다. 가게의 역제안이 같은 값으로 되풀이되면 그것이 바닥이라는, 이 가게의 문장을 읽는 법을 프롬프트에 적었기 때문이다. 문장을 다르게 쓰는 가게에서는 요청과 응답은 그대로 오가도 이 읽는 법이 통하는지는 확인하지 않았다.
