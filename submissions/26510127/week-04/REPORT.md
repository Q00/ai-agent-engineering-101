# Week 04 REPORT: free, tagged, structured 협상

학번 26510127 · Chaewon Lee (@chaewoonee)

## 1. 설정

| 항목 | 값 |
|---|---|
| provider | OpenRouter (`https://openrouter.ai/api/v1`, provider 자동 라우팅) |
| model | `openai/gpt-oss-20b` (agent와 reader 모두 같은 모델, 같은 설정) |
| temperature | 0 |
| reasoning | `reasoning: {effort: "low", exclude: true}` — 추론 텍스트는 메시지에 들어오지 않음 |
| 턴 한도 | 메시지 8개 (넘으면 `open`) |
| 순서 | buyer가 먼저. buyer의 첫 user 턴은 `"The seller is waiting. Write your first message to the seller."` |
| 시나리오 | `scenarios.json` 4개. 1 자전거(120/150), 2 스탠드(30/45) 거래 가능 / 3 헤드폰(60/55), 4 필름카메라(300/260) 거래 불가. 실행 전에 커밋 |
| 반복 | 조건마다 시나리오 전체 3회 = 36판 |

**system prompt = 역할 문단 + 행위 문단(세 조건 공통) + 형식 문단(조건별)** (`acl.py`)

- 역할(buyer): `You are the buyer of {item}, negotiating the price with the seller. Your private limit: you can pay at most {limit}. Never agree to a price above {limit}. Do not reveal your limit. Try to pay as little as possible, but a deal within your limit is better than no deal.` (seller는 대칭: `can accept at least {limit}`, `Never agree to a price below {limit}`)
- 행위(공통): `Four acts are available: propose (offer a price), accept-proposal (agree to the other side's last price, which ends the negotiation with a deal), reject-proposal (decline the last price and keep negotiating), refuse (leave the negotiation for good, no deal). Each message performs exactly one of these acts. The negotiation ends after 8 messages in total.`

| condition | 형식 문단 | 프로토콜 계층 |
|---|---|---|
| free | `Write your message as one or two plain English sentences.` | reader가 대화 전체를 보고 마지막 메시지의 performative와 price를 JSON으로 답함 (메시지마다 1회) |
| tagged | `Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.` | 정규식 `^\s*\((propose\|accept-proposal\|reject-proposal\|refuse)\)`. propose일 때만 reader가 가격을 읽음 |
| structured | `Reply with exactly one JSON object and nothing else: {"performative": "propose" \| "accept-proposal" \| "reject-proposal" \| "refuse", "content": {"price": <whole number or null>}}.` | JSON 파서, 모델 호출 없음. 뒤에 붙은 문장은 무시하고 `trailing_text`로 셈 |

**reader 프롬프트** (조건과 무관하게 동일)

- free: `You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only. Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}. propose = the speaker offers a price; accept-proposal = the speaker agrees to the other side's last price; reject-proposal = the speaker declines the last price and keeps negotiating; refuse = the speaker leaves for good. price is the price the speaker of the LAST message offers, or null if it offers none.`
- tagged의 가격 reader: `You are an observer reading one message from a price negotiation. The message is a proposal. Reply with exactly one JSON object and nothing else: {"price": <whole number or null>}, the price the speaker offers, or null if it offers none.`

**판정 규칙**: 읽지 못한 메시지(행위가 넷 중 하나가 아니거나 propose에 가격이 없음)는 `format_errors`에 세고 상대에게 그대로 전달한다. propose면 그 쪽의 마지막 가격을 갱신, accept-proposal이면 상대의 마지막 propose 가격으로 deal(기록된 가격이 없으면 계속), refuse면 no_deal. deal이면 reserve ≤ price ≤ budget이 아닐 때 violation. correct는 거래 가능 시나리오에서 위반 없는 deal, 불가 시나리오에서 no_deal.

**실행 명령** (Windows PowerShell, 저장소 루트)

```powershell
pip install -r submissions\26510127\week-04\requirements.txt
$env:OPENROUTER_API_KEY = "<key>"          # 커밋하지 않음
python submissions\26510127\week-04\negotiate.py --conditions free tagged structured --repeats 3
python scripts\check_week04.py submissions\26510127\week-04
```

(`AGENT_MODEL=mock`은 네트워크 없이 파이프라인만 검사하는 스크립트 응답이며 결과에는 쓰지 않았다.)

## 2. 결과

### 조건별 요약

| condition | correct / 12 | deal / no_deal / open | violation | mean turns | format_errors | reader_calls | 빈 메시지 |
|---|---|---|---|---|---|---|---|
| free | 6 | 5 / 6 / 1 | 2 | 4.08 | 0 | 49 | 6 / 49 |
| tagged | 3 | 3 / 0 / 9 | 0 | 7.42 | 25 | 50 | 25 / 89 |
| structured | 9 | 6 / 3 / 3 | 0 | 5.00 | 0 | 0 | 0 / 60 |

"빈 메시지"는 agent가 빈 문자열을 돌려준 수 / 전체 메시지 수. 결과표의 수치는 아니지만 아래 해석의 핵심이라 따로 셌다.

### 에피소드 전체 (`results.csv`; note 공통 `model=openai/gpt-oss-20b provider=openrouter temp=0.0 effort=low accept_without_price=0 trailing_text=0`)

| run | sc | possible | outcome | price | correct | violation | turns | format_errors | reader_calls | agent_calls | tokens (in+out) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-01 | 1 | 1 | no_deal |  | 0 | 0 | 4 | 0 | 4 | 4 | 2000+305 |
| free-01 | 2 | 1 | deal | 30 | 1 | 0 | 2 | 0 | 2 | 2 | 919+165 |
| free-01 | 3 | 0 | deal | 70 | 0 | 1 | 3 | 0 | 3 | 3 | 1465+379 |
| free-01 | 4 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 3 | 3 | 1414+337 |
| free-02 | 1 | 1 | no_deal |  | 0 | 0 | 4 | 0 | 4 | 4 | 1966+714 |
| free-02 | 2 | 1 | deal | 40 | 1 | 0 | 5 | 0 | 5 | 5 | 2619+419 |
| free-02 | 3 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 3 | 3 | 1452+359 |
| free-02 | 4 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 | 7 | 3837+544 |
| free-03 | 1 | 1 | no_deal |  | 0 | 0 | 4 | 0 | 4 | 4 | 2005+297 |
| free-03 | 2 | 1 | deal | 45 | 1 | 0 | 3 | 0 | 3 | 3 | 1476+273 |
| free-03 | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | 8 | 4457+936 |
| free-03 | 4 | 0 | deal | 350 | 0 | 1 | 3 | 0 | 3 | 3 | 1403+313 |
| tagged-01 | 1 | 1 | deal | 120 | 1 | 0 | 7 | 2 | 3 | 7 | 2515+463 |
| tagged-01 | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 3 | 4 | 1508+277 |
| tagged-01 | 3 | 0 | open |  | 0 | 0 | 8 | 1 | 4 | 8 | 3141+581 |
| tagged-01 | 4 | 0 | open |  | 0 | 0 | 8 | 3 | 5 | 8 | 3067+1128 |
| tagged-02 | 1 | 1 | open |  | 0 | 0 | 8 | 2 | 5 | 8 | 3126+521 |
| tagged-02 | 2 | 1 | open |  | 0 | 0 | 8 | 5 | 3 | 8 | 2823+603 |
| tagged-02 | 3 | 0 | open |  | 0 | 0 | 8 | 2 | 6 | 8 | 3242+8085 |
| tagged-02 | 4 | 0 | open |  | 0 | 0 | 8 | 4 | 3 | 8 | 2750+510 |
| tagged-03 | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 4 | 6 | 2372+287 |
| tagged-03 | 2 | 1 | open |  | 0 | 0 | 8 | 4 | 3 | 8 | 2799+511 |
| tagged-03 | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 6 | 8 | 3350+686 |
| tagged-03 | 4 | 0 | open |  | 0 | 0 | 8 | 2 | 5 | 8 | 3070+705 |
| structured-01 | 1 | 1 | deal | 130 | 1 | 0 | 4 | 0 | 0 | 4 | 1171+196 |
| structured-01 | 2 | 1 | deal | 30 | 1 | 0 | 2 | 0 | 0 | 2 | 543+82 |
| structured-01 | 3 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 0 | 8 | 2621+691 |
| structured-01 | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | 8 | 2626+412 |
| structured-02 | 1 | 1 | deal | 120 | 1 | 0 | 2 | 0 | 0 | 2 | 537+70 |
| structured-02 | 2 | 1 | deal | 30 | 1 | 0 | 2 | 0 | 0 | 2 | 548+70 |
| structured-02 | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | 8 | 2636+333 |
| structured-02 | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | 8 | 2619+374 |
| structured-03 | 1 | 1 | deal | 120 | 1 | 0 | 2 | 0 | 0 | 2 | 550+103 |
| structured-03 | 2 | 1 | deal | 30 | 1 | 0 | 2 | 0 | 0 | 2 | 535+74 |
| structured-03 | 3 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 0 | 7 | 2252+226 |
| structured-03 | 4 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 0 | 7 | 2229+831 |

crash로 끝난 에피소드는 없다.

## 3. 비교표: FIPA-ACL과 세 조건

| 항목 | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force가 어디에 있는가 | 필수 `performative` 파라미터 | 문장 속. reader가 추론. 이번 실행에서는 모델이 비어 있지 않은 메시지 43개 모두 앞에 `Propose:`, `Refuse.` 같은 행위 이름을 스스로 붙임 | 문장 맨 앞의 `(tag)` | JSON의 `performative` 필드 |
| content 언어 | SL 등 형식 content language + `:ontology` | 영어 평문 | 태그 뒤 영어 한 문장 | `{"price": 정수 \| null}` |
| content를 누가 해석하는가 | 받는 에이전트가 ontology로 | reader LLM이 행위와 가격 모두 | 정규식이 행위, reader LLM이 propose의 가격 | 파서. 모델 없음 |
| 대화가 어떻게 끝나는가 | protocol(예: contract-net)의 종료 행위 | reader가 accept/refuse로 라벨한 메시지, 또는 8턴 | 태그가 accept/refuse인 메시지, 또는 8턴 | 필드가 accept/refuse인 메시지, 또는 8턴 |
| sincerity를 보장하는 것 | 정의상 가정(FP·RE). 밖에서 확인 불가 | 없음. reader가 빈 문장에도 행위를 붙임 | 없음. 태그와 문장이 어긋나도 태그를 믿음 | 없음. 필드를 믿음 |
| 메시지 하나를 읽는 비용 | 파서 | reader 호출 1회 (49회 / 49메시지) | propose일 때 reader 1회 (50회 / 89메시지) | 0 |
| 실패하는 방식 | 의미를 밖에서 검증할 수 없음 | 빈 메시지를 reader가 accept·refuse·reject로 꾸며 읽음(6건), 에이전트의 실제 한도 위반 1건 | 빈 메시지 25건이 모두 format error, 그 여파로 open 9건. reject 태그 뒤 역제안 5건이 가격으로 기록되지 않음 | 이번 실행에서는 형식 실패 0. 남은 실패는 협상 판단(불가 시나리오에서 8턴 제안만 반복해 open 3건) |

## 4. 해석

**structured가 correct를 가장 많이 올렸고(9/12), tagged는 가장 적었다(3/12). 그런데 tagged를 끌어내린 것은 태그 자체가 아니라 모델이 태그 형식에서 빈 메시지를 낸 것이다.** tagged 메시지 89개 중 25개가 빈 문자열이었고(`[seller] ` 다음 줄 `[tag] None   (no tag)`), tagged의 format_errors 25건이 전부 이것이다. 한 번 빈 메시지가 나오면 이어서 여러 개가 나오는 경우가 많았다. `tagged-02` 시나리오 2에서는 buyer의 `(propose) How about $30 for the lamp?` 뒤로 다섯 메시지가 연달아 비어 open으로 끝났다. tagged의 open 9건 가운데 8건이 빈 메시지를 포함한다. 같은 모델이 structured에서는 빈 메시지를 한 번도 내지 않았고(0/60), free에서는 6번(6/49) 냈다. 이 차이가 형식 때문인지 OpenRouter의 provider 라우팅이나 reasoning을 제외한 설정 때문인지는 이번 로그로 가르지 못한다. finish_reason을 기록하지 않았기 때문이다. **free에서는 reader가 빈 메시지에도 라벨을 붙였다.** 6건 모두 format error가 아니라 행위로 읽혔고(refuse 3, reject-proposal 2, accept-proposal 1), `free-01` 시나리오 3에서는 buyer의 빈 메시지를 `{'performative': 'accept-proposal', 'price': None}`으로 읽어 seller의 70에 거래가 기록됐다(budget 55, violation 1). 두 에이전트는 합의하지 않았는데 프로토콜 계층이 거래를 만든 것이다. free의 correct 6건 가운데 2건(`free-01` 시나리오 4, `free-02` 시나리오 3)도 빈 buyer 메시지를 refuse로 읽어 우연히 맞은 no_deal이다. free의 나머지 violation 1건은 에이전트가 낸 진짜 위반이다. `free-03` 시나리오 4에서 buyer가 seller의 350에 `Accept-proposal.`을 보냈다(budget 260). 강의 참조 실행과 달리 free 첫 턴이 질문으로 끝난 에피소드는 없었다. 행위 문단이 네 이름을 알려 주자 모델이 free에서도 43개 메시지 전부를 `Propose: I can offer you $90 for the bicycle.`처럼 행위 이름으로 시작했기 때문이다. 즉 free도 사실상 비공식 태그를 달았고, reader는 그 이름을 읽었다. **tagged에서 태그가 비용이 된 곳은 참조 실행과 같다.** reject 태그 뒤에 새 가격을 적은 메시지가 5건이다(예: `(reject-proposal) I’m afraid $55 is still below what I can accept. I can offer $75 if that works for you.`). 계층은 이것을 거절로만 읽어 가격을 갱신하지 않았다. **어떤 형식도 바꾸지 못한 것도 있다.** 시나리오 1(자전거)에서 seller가 buyer의 110을 받고 역제안 없이 떠난 에피소드가 free에서 3번 모두 나왔다(`[seller] Refuse.`). 거래 불가 시나리오 3, 4에서 두 에이전트가 refuse 없이 8턴 동안 제안만 주고받은 open도 세 형식 모두에서 나왔다. 한도를 드러내는 문장(`I can only go up to $55.`, `$60, which is my lowest price.`)도 형식과 무관하게 나왔다. reader 비용은 free 49회, tagged 50회, structured 0회다. tagged의 에피소드가 길었고(평균 7.42턴) 그 대부분이 propose였기 때문에 tagged가 free보다 reader를 더 불렀다. 행위를 태그로 옮겨도 가격을 평문에 두는 한 reader 비용은 줄지 않았다.
