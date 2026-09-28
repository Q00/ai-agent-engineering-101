# Week 04 — Speech acts in practice: free, tagged, structured

학번 26512070

---

## 1. 설정

### 제공자와 모델

| 항목 | 값 |
|---|---|
| 제공자 | OpenRouter (OpenAI 호환 엔드포인트, `openai` 패키지) |
| 모델 | `nvidia/nemotron-3-super-120b-a12b:free` (README 권장 모델) |
| temperature | 0.7 (`AGENT_TEMPERATURE`) |
| max_tokens | 900 |
| reasoning | 끔 (`extra_body={"reasoning": {"enabled": False}}`, README 안내) |
| 요청 타임아웃 / 재시도 | 90초, 최대 4회, 4·8·16초 백오프 |
| 턴 제한 | 메시지 10개 (양쪽 합계). 도달하면 `open` |
| 반복 | 조건당 3회(r1–r3) × 시나리오 6개 = 조건당 18 에피소드, 총 54 |
| 실행일 | 2026-09-28, 18:45–19:13 (약 28분) |

temperature를 0이 아닌 0.7로 둔 이유는 week-03과 같다. 0에서는 반복 3회가 같은 전사로 겹쳐 반복이 아무것도 재지 못한다. 대가로 전사 자체는 재현되지 않으며(시드를 받지 않는 API), 여기서 재현성은 **추세**를 뜻한다.

**호출 비용 (r1–r3 로그의 RESULT 줄 합계)**

| 조건 | 에이전트 호출 | 판독기 호출 | 합계 | 토큰 |
|---|---|---|---|---|
| free | 129 | 129 | 258 | 124,621 |
| tagged | 141 | 124 | 265 | 130,895 |
| structured | 136 | 0 | 136 | 71,421 |
| **합계** | 406 | 253 | 659 | 326,937 |

재시도는 214회 있었다. 분당 무료 모델 한도(HTTP 429) 107회, Nvidia 업스트림 과부하(503) 107회였고, 모두 재시도로 복구되어 크래시는 0건이다.

### 시나리오 (`scenarios.json`, 실행 전 커밋)

| id | 물건 | reserve (판매자 하한) | budget (구매자 상한) | 거래 |
|---|---|---|---|---|
| `bike` | a used road bicycle | 120 | 200 | 가능 |
| `laptop` | a two-year-old laptop | 540 | 560 | 가능 |
| `camera` | a mirrorless camera body | 300 | 300 | 가능 |
| `guitar` | an acoustic guitar | 410 | 390 | 불가능 |
| `sofa` | a leather three-seat sofa | 800 | 450 | 불가능 |
| `lamp` | a vintage brass desk lamp | 95 | 80 | 불가능 |

`camera`는 reserve = budget이라 정확히 $300 한 점에서만 합의할 수 있다. `laptop`은 여유 폭이 $20, `guitar`는 $20 모자란다.

### 프로토콜 규칙 (직접 정한 것)

1. **첫 메시지는 구매자의 `propose`이다.** 질문이나 인사로 시작하지 않는다.
2. **이후 모든 메시지는 상대의 최근 가격에 대한 응답이다.** `accept-proposal`이거나, `reject-proposal`에 **같은 메시지 안의 역제안 가격**을 붙인 것이다. README의 JSON 계약(`performative` 필드 하나)을 지키기 위해, 역제안 가격은 `content.price`에 담는다. 두 act를 한 메시지에 쓰는 방식은 쓰지 않았다.
3. **`refuse`는 자신이 역제안을 3번 보낸 뒤,** 상대 가격이 합당하게 움직이지 않을 때만 허용한다.

이 규칙에 따른 실행기 동작:

- `reject-proposal`은 가격이 있어야 유효하고, 그 가격이 새 제안으로 기록된다. `accept-proposal`은 상대의 마지막 유효 가격으로 거래를 성사시킨다.
- **tagged 판독기는 `propose`와 `reject-proposal` 둘 다에서 가격을 읽는다.** README는 "propose 안의 가격만"이라고 적었지만, 이 프로토콜에서는 역제안 가격이 reject에 있기 때문이다.
- **판독기는 메시지 하나만이 아니라 발신자와 그때까지의 대화 전체를 본다.** "맥락과 역할로 판정하라"는 지시가 성립하려면 맥락이 주어져야 한다. 호출 수는 같고 호출당 토큰만 늘어난다.
- **형식 오류:** 파싱이 안 되거나, act가 없거나, 가격이 필요한 act에 가격이 없거나, 답할 상대 제안이 없는데 accept·reject를 쓴 경우다. 오류가 난 메시지도 원문은 상대 에이전트에게 그대로 전달되지만, 협상 상태(가격, 종료)는 바꾸지 않는다.
- **첫 메시지 이후의 `propose`:** 규칙 위반이지만 읽을 수는 있으므로 유효하게 처리하고 `note`에 기록한다(실제 발생 0건). 3번 규칙(이른 refuse)은 실행기가 강제하지 않는다.
- **정답(`correct`):** 거래 가능 시나리오에서는 두 한계 안의 가격으로 성사된 `deal`이다. 거래 불가능 시나리오에서는 `deal`이 아닌 모든 결과(`no_deal`, `open`)다. **위반(`violation`):** reserve 미만이나 budget 초과로 성사된 `deal`이다.

### 역할 프롬프트 (세 조건 공통, `negotiate.py`)

```
You are the buyer in a one-on-one price negotiation for {item}. Your budget is ${budget}: you must never pay more than that. Your budget is private; do not state it. Pay as little as you can. The negotiation ends after at most {limit} messages in total.
```
```
You are the seller in a one-on-one price negotiation for {item}. Your reserve price is ${reserve}: you must never sell for less than that. Your reserve price is private; do not state it. Sell as high as you can. The negotiation ends after at most {limit} messages in total.
```
`{limit}`에는 10이 들어간다. 구매자의 첫 호출에는 대화 대신 다음 한 줄이 user 메시지로 들어간다(세 조건 공통): `The seller is listening. You speak first.`

system prompt = 역할 프롬프트 + 빈 줄 + 해당 조건의 형식 문단.

### 형식 문단 (`protocol.py`)

세 조건 모두 아래 공통 규칙(`ACT_RULES`)으로 시작하고, 그 뒤에 조건별 형식 지시가 붙는다. 조건 간 차이는 형식 지시뿐이다.

**공통 규칙 (`ACT_RULES`)**
```
Every message you send performs exactly one of these four acts:
- propose: offer a price for the item. Only the buyer's opening message is a propose.
- accept-proposal: agree to the other side's most recent price. The negotiation ends with a deal at that price. Do not name a new price.
- reject-proposal: decline the other side's most recent price AND, in the same message, offer your own new price. A rejection always carries a counter-offer. The negotiation continues.
- refuse: leave the negotiation for good. It ends with no deal.

Order of play:
1. The buyer opens with a propose. Nothing comes before it: no greeting-only message and no question.
2. Every later message answers the other side's most recent price with either accept-proposal, or reject-proposal with your new price.
3. Only after you have sent reject-proposal with a counter-offer three times, and the other side's price is still not moving by a reasonable amount, may you refuse.
Prices are whole numbers of US dollars.
```

**free**
```
Message format: write plain English only. Do not use tags, labels, brackets, or JSON, and do not name the act by its label. Your words alone must make it impossible to mistake which of the four acts you perform:
- a proposal states exactly one price of yours;
- an acceptance clearly agrees to the other side's price and names no new price;
- a rejection clearly declines the other side's price and states exactly one new price of yours;
- a refusal clearly says you are leaving without a deal.
Do not write anything that could be read as a different act: no questions, no conditional or ranged offers, and if you mention the other side's price, make clear it is theirs, not yours.
```

**tagged**
```
Message format: begin every message with exactly one tag in parentheses that names its act -- (propose), (accept-proposal), (reject-proposal) or (refuse) -- with nothing before it. After the tag, write plain English. With (propose) and (reject-proposal) the English must state your price as a number, like $150. The English must agree with the tag and must not be readable as a different act: no questions, no conditional or ranged offers, and if you mention the other side's price, make clear it is theirs, not yours.
Example: (reject-proposal) $190 is more than I will pay. I can offer $150.
```

**structured**
```
Message format: write every message as exactly one JSON object and nothing else -- no words before or after it and no code fences. Shape:
{"performative": "<act>", "content": {"price": <integer>}}
<act> is one of propose, accept-proposal, reject-proposal, refuse. For propose and reject-proposal, price is your price as a whole number. For accept-proposal and refuse, write "content": {}.
Example: {"performative": "reject-proposal", "content": {"price": 150}}
```

### 판독기 프롬프트 (`READER_PROMPT`)

free에서는 act와 가격을 모두 판독기의 답에서 가져오고, tagged에서는 가격만 가져온다. structured에서는 판독기를 쓰지 않는다.

```
You label messages from a price negotiation between a buyer and a seller. You are given who sent the message, the conversation before it, and the message itself. Reply with only one JSON object and nothing else:
{"performative": "<act>", "price": <integer or null>}

<act> is exactly one of these four:
- propose: the speaker offers a price. In this negotiation only the buyer's opening message is a propose.
- accept-proposal: the speaker agrees to the other side's most recent price. price is null.
- reject-proposal: the speaker declines the other side's most recent price and offers a new price of their own. price is that new price.
- refuse: the speaker leaves the negotiation with no deal. price is null.

Rules:
1. A message that declines the other side's price and states a new price is reject-proposal, and price is the new price: the speaker's counter-offer.
2. If the message mentions more than one number, price is the speaker's own price, never a price the other side named.
3. Judge from the conversation so far and the speaker's role (buyer or seller) which act the message actually performs and whose price each number is.
price is a whole number of US dollars, or null.
```

판독기의 user 메시지 예 (`protocol.reader_input`):
```
Speaker: buyer

Conversation so far:
buyer: (propose) $120 for the bike.
seller: (reject-proposal) $120 is too low. I can offer $190.

Message to label (sent by the buyer):
(reject-proposal) $150 is my counter.
```

### 실행 방법

```bash
pip install openai
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your openrouter key>
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free
# optional, defaults shown: AGENT_TEMPERATURE=0.7 AGENT_TIMEOUT=90 AGENT_RETRIES=4

cd submissions/26512070/week-04
python run.py --condition free --repeat 1 --fake     # API 없이 파이프라인 검증 (_fake/에 기록)
for r in 1 2 3; do for c in free tagged structured; do
  python run.py --condition $c --repeat $r            # 실행 1회 = 로그 1개 (logs/<c>-r<r>.txt)
done; done
python summarize.py                                   # 아래 2부 표
```

- 에피소드가 끝날 때마다 `results.csv`에 한 줄씩 추가된다. 이미 있는 `(run, scenario)`는 건너뛰므로, 중단되면 같은 명령으로 이어서 돌리면 된다.
- 에이전트나 판독기의 응답을 읽지 못한 경우는 재시도하지 않는다. 그것이 측정 대상이기 때문이다. 전송 실패(429, 503, 타임아웃)만 재시도한다.
- `r0`은 본 실행 전에 bike 시나리오 하나로 돌린 시험 실행이다. 로그(`logs/*-r0.txt`)와 `results.csv` 행은 과정 기록으로 남겼지만, 아래 표에서는 제외했다(`python summarize.py --all`로 포함 가능).

---

## 2. 결과

### 조건별 요약 (r1–r3, 조건당 18 에피소드)

| condition | episodes | correct | violations | deal / no_deal / open | crashed | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|---|
| free | 18 | 16/18 | 0 | 7 / 8 / 3 | 0 | 7.2 | 1 | 129 |
| tagged | 18 | 14/18 | 1 | 7 / 8 / 3 | 0 | 7.8 | 5 | 124 |
| structured | 18 | 14/18 | 0 | 5 / 10 / 3 | 0 | 7.6 | 2 | 0 |

**거래 가능 (reserve ≤ budget)**

| condition | episodes | correct | violations | deal / no_deal / open | crashed | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|---|
| free | 9 | 7/9 | 0 | 7 / 2 / 0 | 0 | 6.1 | 0 | 55 |
| tagged | 9 | 6/9 | 0 | 6 / 3 / 0 | 0 | 7.3 | 0 | 57 |
| structured | 9 | 5/9 | 0 | 5 / 4 / 0 | 0 | 6.9 | 2 | 0 |

**거래 불가능 (reserve > budget)**

| condition | episodes | correct | violations | deal / no_deal / open | crashed | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|---|
| free | 9 | 9/9 | 0 | 0 / 6 / 3 | 0 | 8.2 | 1 | 74 |
| tagged | 9 | 8/9 | 1 | 1 / 5 / 3 | 0 | 8.3 | 5 | 67 |
| structured | 9 | 9/9 | 0 | 0 / 6 / 3 | 0 | 8.2 | 0 | 0 |


### 에피소드별 결과 (`results.csv`, r1–r3)

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-r1 | free | bike | 1 | deal | 120 | 1 | 0 | 2 | 0 | 2 |  |
| free-r1 | free | laptop | 1 | deal | 540 | 1 | 0 | 10 | 0 | 10 |  |
| free-r1 | free | camera | 1 | deal | 300 | 1 | 0 | 9 | 0 | 9 |  |
| free-r1 | free | guitar | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r1 | free | sofa | 0 | open |  | 1 | 0 | 10 | 0 | 10 |  |
| free-r1 | free | lamp | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| tagged-r1 | tagged | bike | 1 | no_deal |  | 0 | 0 | 7 | 0 | 6 |  |
| tagged-r1 | tagged | laptop | 1 | deal | 560 | 1 | 0 | 7 | 0 | 6 |  |
| tagged-r1 | tagged | camera | 1 | deal | 300 | 1 | 0 | 9 | 0 | 8 |  |
| tagged-r1 | tagged | guitar | 0 | no_deal |  | 1 | 0 | 7 | 0 | 6 |  |
| tagged-r1 | tagged | sofa | 0 | no_deal |  | 1 | 0 | 8 | 0 | 7 |  |
| tagged-r1 | tagged | lamp | 0 | no_deal |  | 1 | 0 | 7 | 1 | 6 |  |
| structured-r1 | structured | bike | 1 | deal | 120 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r1 | structured | laptop | 1 | deal | 560 | 1 | 0 | 7 | 0 | 0 |  |
| structured-r1 | structured | camera | 1 | no_deal |  | 0 | 0 | 7 | 0 | 0 |  |
| structured-r1 | structured | guitar | 0 | no_deal |  | 1 | 0 | 7 | 0 | 0 |  |
| structured-r1 | structured | sofa | 0 | no_deal |  | 1 | 0 | 7 | 0 | 0 |  |
| structured-r1 | structured | lamp | 0 | no_deal |  | 1 | 0 | 7 | 0 | 0 |  |
| free-r2 | free | bike | 1 | deal | 135 | 1 | 0 | 7 | 0 | 7 |  |
| free-r2 | free | laptop | 1 | deal | 550 | 1 | 0 | 8 | 0 | 8 |  |
| free-r2 | free | camera | 1 | no_deal |  | 0 | 0 | 6 | 0 | 6 |  |
| free-r2 | free | guitar | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r2 | free | sofa | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r2 | free | lamp | 0 | no_deal |  | 1 | 0 | 7 | 1 | 7 |  |
| tagged-r2 | tagged | bike | 1 | no_deal |  | 0 | 0 | 7 | 0 | 6 |  |
| tagged-r2 | tagged | laptop | 1 | deal | 540 | 1 | 0 | 8 | 0 | 7 |  |
| tagged-r2 | tagged | camera | 1 | no_deal |  | 0 | 0 | 7 | 0 | 6 |  |
| tagged-r2 | tagged | guitar | 0 | no_deal |  | 1 | 0 | 8 | 0 | 7 |  |
| tagged-r2 | tagged | sofa | 0 | open |  | 1 | 0 | 10 | 0 | 10 |  |
| tagged-r2 | tagged | lamp | 0 | open |  | 1 | 0 | 10 | 1 | 10 |  |
| structured-r2 | structured | bike | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r2 | structured | laptop | 1 | deal | 550 | 1 | 0 | 7 | 0 | 0 |  |
| structured-r2 | structured | camera | 1 | no_deal |  | 0 | 0 | 7 | 1 | 0 |  |
| structured-r2 | structured | guitar | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |
| structured-r2 | structured | sofa | 0 | no_deal |  | 1 | 0 | 7 | 0 | 0 |  |
| structured-r2 | structured | lamp | 0 | no_deal |  | 1 | 0 | 7 | 0 | 0 |  |
| free-r3 | free | bike | 1 | deal | 120 | 1 | 0 | 2 | 0 | 2 |  |
| free-r3 | free | laptop | 1 | deal | 560 | 1 | 0 | 8 | 0 | 8 |  |
| free-r3 | free | camera | 1 | no_deal |  | 0 | 0 | 3 | 0 | 3 |  |
| free-r3 | free | guitar | 0 | open |  | 1 | 0 | 10 | 0 | 10 |  |
| free-r3 | free | sofa | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r3 | free | lamp | 0 | open |  | 1 | 0 | 10 | 0 | 10 |  |
| tagged-r3 | tagged | bike | 1 | deal | 135 | 1 | 0 | 7 | 0 | 6 |  |
| tagged-r3 | tagged | laptop | 1 | deal | 550 | 1 | 0 | 7 | 0 | 6 |  |
| tagged-r3 | tagged | camera | 1 | deal | 300 | 1 | 0 | 7 | 0 | 6 |  |
| tagged-r3 | tagged | guitar | 0 | deal | 420 | 0 | 1 | 7 | 0 | 6 |  |
| tagged-r3 | tagged | sofa | 0 | no_deal |  | 1 | 0 | 8 | 0 | 7 |  |
| tagged-r3 | tagged | lamp | 0 | open |  | 1 | 0 | 10 | 3 | 8 |  |
| structured-r3 | structured | bike | 1 | deal | 170 | 1 | 0 | 9 | 1 | 0 |  |
| structured-r3 | structured | laptop | 1 | no_deal |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-r3 | structured | camera | 1 | no_deal |  | 0 | 0 | 7 | 0 | 0 |  |
| structured-r3 | structured | guitar | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |
| structured-r3 | structured | sofa | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |
| structured-r3 | structured | lamp | 0 | no_deal |  | 1 | 0 | 9 | 0 | 0 |  |

---

## 3. FIPA-ACL과 세 조건 비교

| | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| **발화의 힘(force)이 있는 곳** | 필수 필드 `:performative`, 메시지당 하나 | 영어 문장의 표현 속에 암묵적으로 있다. 판독기 LLM이 대화 맥락과 함께 추론한다 | 맨 앞의 괄호 태그. 정규식이 읽는다. 뒤의 영어가 태그와 일치하는지는 아무도 검사하지 않는다 | JSON의 `performative` 필드. 파서가 읽는다 |
| **내용 언어** | `:language`(예: FIPA-SL)와 `:ontology`를 메시지마다 선언한 형식 언어 | 영어. 가격은 문장 속 숫자다. "정수 달러"라는 온톨로지는 메시지가 아니라 프롬프트(`ACT_RULES` 마지막 줄)에만 있다 | 태그 뒤는 free와 같은 영어 | 필드 하나(`content.price`, 정수)짜리 JSON. 온톨로지는 키 이름과 프롬프트에 암묵적으로 있고, 메시지에는 선언되지 않는다 |
| **내용을 해석하는 주체** | 수신 에이전트가 선언된 온톨로지로 해석한다. 해석하는 쪽과 행동하는 쪽이 같다 | **해석이 둘로 갈라진다.** 협상 상태는 판독기 LLM(act와 가격)이 정하고, 상대 에이전트는 원문을 따로 읽는다 | act는 정규식, 가격은 판독기 LLM이 해석하고, 상대 에이전트는 원문을 따로 읽는다 | 파서가 해석하고, 상대 에이전트는 원문을 따로 읽는다 |
| **대화가 끝나는 방식** | 상호작용 프로토콜이 종료 act를 정하고, `:conversation-id`, `:in-reply-to`, `:reply-by`로 응답을 묶는다 | `accept-proposal`, `refuse`, 턴 제한(10) 중 하나. "무엇에 대한 응답인가"는 필드가 아니라 "상대의 최근 가격"이라는 규칙으로만 정해진다. `open` 3/18 | 같음. `open` 3/18 | 같음. `open` 3/18 |
| **성실성(sincerity)을 보장하는 것** | 형식 의미론의 전제 조건(발신자는 내용을 믿는다)을 **가정할 뿐** 검증하지 않는다 | 없음. 한계는 역할 프롬프트의 지시로만 지켜진다 | 없음. 조건 전체에서 유일한 한계 위반이 여기서 나왔다(tagged-r3 guitar, budget $390, 거래 $420) | 없음. 파서는 형식만 검사한다 |
| **메시지 하나를 읽는 비용** | 결정론적 파싱. 모델 호출 없음 | 메시지마다 판독기 1회(129회). 메시지당 약 970 토큰 | 가격이 있는 act에서만 판독기 호출. 그런데 이 프로토콜에서는 거의 모든 메시지가 가격이 있는 act라 124/141 메시지(88%)에서 호출했다. 메시지당 약 930 토큰으로 free와 거의 같다 | 판독기 0회. 메시지당 약 530 토큰(free의 54%) |
| **나타난 실패 유형** | 읽을 수 없는 메시지에는 수신자가 `not-understood`로 응답한다. 실패가 대화 안에서 드러난다 | 판독기가 의도를 잘못 읽는다(최후통첩을 refuse로 판정). 판독기 자신의 JSON이 깨진다. `not-understood`가 없어서 둘 다 상대에게 알려지지 않는다 | 판독기 JSON 깨짐 2건. 한 메시지에 태그 두 개. 협상이 끝났다고 믿은 에이전트의 태그 없는 소감문 2건. 형식 오류 5건이 모두 lamp에서 나왔다 | 가격 없는 `reject-proposal` 2건. 시험 실행에서는 깨진 JSON 때문에 수락 하나가 사라졌다. 어느 쪽이든 발화 전체가 무효가 된다 |

**표의 근거 (로그 위치)**

- **해석이 둘로 갈라진 사례 2건.** 협상 상태와 두 에이전트의 믿음이 어긋났다.
  - `logs/structured-r0.txt` bike t06: 판매자가 구매자의 $120을 수락하려고 보낸 메시지가 `{"performative": "performative": "accept-proposal", "content": {}}`로 깨졌다. 협상 상태는 그대로였고, 이어서 구매자가 수락하자 판매자의 이전 제안 **$150**으로 거래됐다.
  - `logs/tagged-r3.txt` lamp t08: 판매자가 `(reject-proposal) … I must refuse …`에 이어 `(refuse) Thank you for the negotiation…`을 한 메시지에 썼다. 정규식은 첫 태그만 읽었고, 판독기는 가격 없음(refuse)이라고 답했다. 그래서 형식 오류로 처리됐고 협상 상태는 계속 열려 있었다. 두 에이전트는 t09와 t10에 "The seller has refused, ending the negotiation with no deal"이라고 쓰며 끝났다고 믿었지만, 결과는 `open`이다. 이 t09에서 구매자는 "my budget of $80"라고 **비공개 한계를 밝혔다.**
- **판독기의 오판.** `logs/free-r3.txt` camera t03, "$180 is my final offer; if you cannot meet it, I will have to walk away." → `refuse`로 판정되어 3턴 만에 무산됐다. 조건부 위협이 네 act 중 가장 가까운 것으로 떨어졌다.
- **판독기 JSON 깨짐.** free-r2 lamp t06, tagged-r1·r2 lamp에서 판독기가 `{"performative": "performative": "reject-proposal", …}`를 돌려줬다. 에이전트 메시지는 올바랐다("I reject your offer of $50 and counter with $130."). 같은 모델이 structured의 에이전트 출력(r0)에서 보인 것과 같은 버릇이다. 따라서 free의 형식 오류 1건과 tagged의 2건은 **판독기가 원인**이다.
- **종료 규칙은 프롬프트에만 있다.** `refuse` 26건 중 20건이 "자신이 역제안을 3번 보낸 뒤" 규칙보다 일렀다(free 7/8, tagged 5/8, structured 8/10). 구매자의 이른 refuse는 대부분 t07이었다. 즉 첫 `propose`를 역제안 한 번으로 셌다. 세 형식 모두 이 위반을 막지 못했다. 파서, 정규식, 판독기는 act의 형식만 볼 뿐 대화 규칙은 보지 않는다.
- **한계 위반.** `logs/tagged-r3.txt` guitar: t03에서 "$480 exceeds my budget"라고 한 구매자가 t07에서 "(accept-proposal) $420 is acceptable."이라고 썼다. 태그는 정확히 읽혔고, 문제는 형식이 아니라 성실성이다.
- **비용.** 수치는 1부의 호출 비용 표에 있다. tagged가 free보다 싸지 않은 이유는 "reject에 항상 역제안 가격을 붙인다"는 규칙 때문이다. 이 규칙으로 가격을 읽어야 하는 메시지가 대부분이 되었다.

---

## 4. 해석

<!-- TODO(26512070) -->
