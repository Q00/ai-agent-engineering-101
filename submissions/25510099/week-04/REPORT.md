# REPORT.md — 화행 실습: 자유·태그·구조화 형식의 가격 협상 비교

구매자와 판매자 LLM 두 에이전트가 같은 여섯 시나리오에서 가격을 협상하는 실험을 메시지 형식만 바꾸어 세 번 반복하였습니다.

- FIPA-ACL이 필수 필드로 두었던 performative를 명시하지 않을 때(free)
- 괄호 태그로 붙일 때(tagged)
- JSON 필드로 넣을 때(structured)
- 결과의 정확도
- 한도 위반
- 턴 수
- 파싱 실패
- 리더 호출 수

위의 요소가 어떻게 달라지는지 측정하였습니다.

## 1. 설정

### 모델과 고정 변수
- provider는 OpenAI이고 모델은 `gpt-4.1-mini` 를 사용하였습니다.
- temperature는 0, 응답 토큰 상한은 256, 턴 제한은 메시지 10개입니다. 세 조건과 아홉 런에서 모두 같습니다.
- 메시지를 읽는 리더 LLM도 에이전트와 같은 모델, 같은 temperature를 씁니다.

### 시나리오 (`scenarios.json`)
- 한도 위반과 오독이 드러나기 쉽도록 두 시나리오는 한도가 겹치는 폭을 좁게 두었습니다. ticket은 reserve와 budget이 80으로 같고, chair는 120 대 130입니다.

| id | item | reserve (판매자 최저) | budget (구매자 최고) | 거래 가능 |
|---|---|---|---|---|
| bike | a used city bicycle | 150 | 220 | 1 |
| ticket | one concert ticket for Friday | 80 | 80 | 1 |
| laptop | a two-year-old laptop | 600 | 520 | 0 |
| book | a linear algebra textbook | 40 | 60 | 1 |
| lens | a 50mm camera lens | 350 | 250 | 0 |
| chair | an ergonomic office chair | 120 | 130 | 1 |

### 에이전트 시스템 프롬프트
- 시스템 프롬프트는 역할 문단, 공통 문단, 형식 문단 셋을 빈 줄로 이어 만듭니다. 세 조건에서 형식 문단만 다릅니다.
- 구매자는 협상 시작을 알리는 고정 문장 `The negotiation begins. Send your opening message.` 를 받고 첫 메시지를 냅니다. 그리고 이 문장은 협상 메시지가 아니며 턴으로 세지 않습니다.
- 각 에이전트는 상대 메시지를 user 턴으로, 자기 메시지를 assistant 턴으로 쌓아 대화 전체를 보도록 구성하였습니다.

역할 문단 (구매자 / 판매자, `{item}` `{budget}` `{reserve}` 는 시나리오 값으로 치환):

```
You are the buyer in a price negotiation for {item}. Your budget is {budget} dollars: the most you may ever pay. This number is private; never reveal it. Pay as little as you can. You speak first.

You are the seller in a price negotiation for {item}. Your reserve price is {reserve} dollars: the least you may ever accept. This number is private; never reveal it. Get as much as you can. The buyer speaks first.
```

공통 문단 (네 행위의 의미, 모든 조건 동일, `{max_turns}` 는 10):

```
Each message you send does exactly one of four things:
- propose: name a price you would trade at (a first offer or a counter-offer);
- accept-proposal: agree to the price the other side named most recently, which closes the deal at that price;
- reject-proposal: decline the other side's most recent price and keep negotiating, without naming a new price;
- refuse: leave the negotiation; there is no deal.
Accept only a price the other side has actually named, and never agree to a price outside your private limit. If no price inside your limit looks reachable, refuse rather than cross the limit. The negotiation ends automatically after {max_turns} messages in total; a negotiation that runs out of turns is a failure for both sides.
```

형식 문단 세 개 (조건별로 이 문단만 바뀜):

```
free:
Format: write your message in plain English, one or two sentences, as you would speak to the other person. No tags, no labels, no JSON.

tagged:
Format: begin your message with exactly one tag in parentheses naming your act: (propose), (accept-proposal), (reject-proposal) or (refuse). Then one or two sentences of plain English. Example: (propose) I could go to 45 dollars for it.

structured:
Format: reply with exactly one JSON object and nothing else, no prose, no code fence: {"performative": "<propose|accept-proposal|reject-proposal|refuse>", "content": {"price": <integer or null>}}. With propose, price is the integer you offer. With accept-proposal, price is the price you are accepting. With reject-proposal and refuse, price is null.
```

### 프로토콜 층: 메시지를 읽는 방법
- free: 메시지마다 리더 LLM을 한 번 호출합니다. 리더는 직전 상대 메시지 하나만 문맥으로 받습니다. "그럼 180으로 하죠"가 수락인지 제안인지는 직전 가격 없이는 정할 수 없기 때문입니다. 한도나 그 이전 대화는 보지 않습니다.
- tagged: 메시지 첫머리의 괄호 태그를 정규식으로 읽습니다. 태그가 propose일 때만 본문 가격을 리더 LLM에 묻습니다. 태그가 권위이며, reject 태그 본문에 가격이 있어도 제안으로 치지 않습니다. 대신 태그와 본문의 불일치를 로그에 경고로 남깁니다.
- structured: JSON 파서만 씁니다. 모델 호출은 없습니다. 객체 하나가 아니거나 performative가 넷 중 하나가 아니거나 propose에 정수 가격이 없으면 파싱 실패입니다.

free 리더 프롬프트 (시스템):

```
You label messages in a two-party price negotiation between a buyer and a seller. Decide which one act the message performs:
- propose: the speaker names a price at which they would trade (first offer or counter-offer);
- accept-proposal: the speaker agrees to the price the other side named most recently, closing the deal;
- reject-proposal: the speaker declines the other side's price but keeps negotiating without naming a new price;
- refuse: the speaker ends the negotiation with no deal.
Reply with exactly one JSON object and nothing else: {"performative": "<propose|accept-proposal|reject-proposal|refuse>", "price": <the integer price the speaker names, or null>}.
```

free 리더 입력 (user): `Previous message from the {other}: {previous}` 한 줄과 `Message from the {speaker} to label: {text}` 한 줄. 첫 메시지에는 previous 자리에 `(none; this is the opening message)` 가 들어갑니다.

tagged 가격 리더 프롬프트 (시스템), 입력은 `Message: {태그 뒤 본문}`:

```
A speaker in a price negotiation is making an offer. Extract the price they propose. Reply with exactly one JSON object and nothing else: {"price": <integer or null>}.
```

### 에피소드 규칙과 판정
- 구매자가 열고 둘이 번갈아 말합니다. 리더가 읽은 결과가 진행을 결정하며, 에이전트는 상대의 원문만 봅니다.
- accept-proposal이 읽히면 상대가 마지막으로 제안한 가격으로 거래가 성립합니다. 상대 제안가가 아직 없으면 프로토콜 오류로 note에 적은 후 이어서 진행합니다.
- refuse가 읽히면 no_deal, 메시지 10개가 지나면 open입니다. 파싱 실패 메시지는 format_errors에 세고 이어서 진행합니다.
- correct는 거래 가능 시나리오에서 양쪽 한도 안의 가격으로 deal이 났을 때, 불가 시나리오에서 no_deal이 났을 때 1입니다. open은 항상 0입니다.
- violation은 deal 가격이 reserve 아래이거나 budget 위일 때 1입니다. 프로토콜 층이 기록한 가격을 기준으로 판정합니다.

### 실행 방법
- 저장소 루트의 `.env` 또는 환경 변수에 `OPENAI_API_KEY`와 `AGENT_MODEL=gpt-4.1-mini`를 둡니다. `openai` 패키지가 필요합니다.

```bash
python run.py --condition free --repeat all
python run.py --condition tagged --repeat all
python run.py --condition structured --repeat all
python summarize.py       # 2부의 두 표
python analyze_logs.py    # 태그 아래 숨은 역제안 수, free 오프닝 라벨
python run.py --condition tagged --dry-run   # 규칙 기반 가짜 에이전트, 호출 없음
```

## 2. 결과

### 조건별 집계

| condition | episodes | correct | violations | deal / no_deal / open | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|
| free | 18 | 17/18 | 0 | 12 / 5 / 1 | 6.9 | 0 | 125 |
| tagged | 18 | 9/18 | 5 | 11 / 3 / 4 | 7.0 | 0 | 59 |
| structured | 18 | 14/18 | 0 | 11 / 4 / 3 | 6.5 | 0 | 0 |

### 에피소드 표 (`results.csv` 그대로)

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | free | bike | 1 | deal | 170 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=160 seller=170; agent_calls=5 |
| 1 | free | book | 1 | deal | 48 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=45 seller=48; agent_calls=5 |
| 1 | free | chair | 1 | deal | 130 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=110 seller=130; agent_calls=5 |
| 1 | free | laptop | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 | ended t9 by buyer refuse; standing buyer=520 seller=600; agent_calls=9 |
| 1 | free | lens | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 | ended t9 by buyer refuse; standing buyer=240 seller=360; agent_calls=9 |
| 1 | free | ticket | 1 | deal | 80 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=75 seller=80; agent_calls=7 |
| 2 | free | bike | 1 | deal | 170 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=160 seller=170; agent_calls=5 |
| 2 | free | book | 1 | deal | 48 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=45 seller=48; agent_calls=5 |
| 2 | free | chair | 1 | deal | 130 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=110 seller=130; agent_calls=5 |
| 2 | free | laptop | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 | ended t9 by buyer refuse; standing buyer=550 seller=600; agent_calls=9 |
| 2 | free | lens | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 | ended t9 by buyer refuse; standing buyer=250 seller=350; agent_calls=9 |
| 2 | free | ticket | 1 | deal | 80 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=70 seller=80; agent_calls=7 |
| 3 | free | bike | 1 | deal | 185 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=170 seller=185; agent_calls=5 |
| 3 | free | book | 1 | deal | 46 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=43 seller=46; agent_calls=7 |
| 3 | free | chair | 1 | deal | 120 | 1 | 0 | 6 | 0 | 6 | ended t6 by seller accept-proposal; standing buyer=120 seller=125; agent_calls=6 |
| 3 | free | laptop | 0 | no_deal |  | 1 | 0 | 10 | 0 | 10 | ended t10 by seller refuse; standing buyer=550 seller=600; agent_calls=10 |
| 3 | free | lens | 0 | open |  | 0 | 0 | 10 | 0 | 10 | turn limit 10 reached; standing buyer=250 seller=350; agent_calls=10 |
| 3 | free | ticket | 1 | deal | 80 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=70 seller=80; agent_calls=7 |
| 4 | tagged | bike | 1 | deal | 150 | 1 | 0 | 2 | 0 | 1 | ended t2 by seller accept-proposal; standing buyer=150 seller=None; agent_calls=2 |
| 4 | tagged | book | 1 | deal | 40 | 1 | 0 | 2 | 0 | 1 | ended t2 by seller accept-proposal; standing buyer=40 seller=None; agent_calls=2 |
| 4 | tagged | chair | 1 | deal | 90 | 0 | 1 | 6 | 0 | 1 | ended t6 by seller accept-proposal; t5 buyer accepted with no price on the table; standing buyer=90 seller=None; protocol_errors=1; agent_calls=6 |
| 4 | tagged | laptop | 0 | open |  | 0 | 0 | 10 | 0 | 5 | turn limit 10 reached; standing buyer=520 seller=None; agent_calls=10 |
| 4 | tagged | lens | 0 | no_deal |  | 1 | 0 | 10 | 0 | 5 | ended t10 by seller refuse; standing buyer=250 seller=None; agent_calls=10 |
| 4 | tagged | ticket | 1 | deal | 85 | 0 | 1 | 7 | 0 | 2 | ended t7 by buyer accept-proposal; standing buyer=50 seller=85; agent_calls=7 |
| 5 | tagged | bike | 1 | open |  | 0 | 0 | 10 | 0 | 8 | turn limit 10 reached; standing buyer=172 seller=176; agent_calls=10 |
| 5 | tagged | book | 1 | deal | 44 | 1 | 0 | 7 | 0 | 5 | ended t7 by buyer accept-proposal; standing buyer=44 seller=44; agent_calls=7 |
| 5 | tagged | chair | 1 | deal | 90 | 0 | 1 | 6 | 0 | 1 | ended t6 by seller accept-proposal; t5 buyer accepted with no price on the table; standing buyer=90 seller=None; protocol_errors=1; agent_calls=6 |
| 5 | tagged | laptop | 0 | open |  | 0 | 0 | 10 | 0 | 5 | turn limit 10 reached; standing buyer=520 seller=None; agent_calls=10 |
| 5 | tagged | lens | 0 | no_deal |  | 1 | 0 | 10 | 0 | 5 | ended t10 by seller refuse; standing buyer=180 seller=350; agent_calls=10 |
| 5 | tagged | ticket | 1 | deal | 85 | 0 | 1 | 7 | 0 | 2 | ended t7 by buyer accept-proposal; standing buyer=50 seller=85; agent_calls=7 |
| 6 | tagged | bike | 1 | deal | 150 | 1 | 0 | 2 | 0 | 1 | ended t2 by seller accept-proposal; standing buyer=150 seller=None; agent_calls=2 |
| 6 | tagged | book | 1 | deal | 45 | 1 | 0 | 4 | 0 | 3 | ended t4 by seller accept-proposal; standing buyer=45 seller=50; agent_calls=4 |
| 6 | tagged | chair | 1 | deal | 120 | 1 | 0 | 6 | 0 | 3 | ended t6 by seller accept-proposal; standing buyer=120 seller=None; agent_calls=6 |
| 6 | tagged | laptop | 0 | open |  | 0 | 0 | 10 | 0 | 4 | turn limit 10 reached; standing buyer=520 seller=650; agent_calls=10 |
| 6 | tagged | lens | 0 | no_deal |  | 1 | 0 | 10 | 0 | 5 | ended t10 by seller refuse; standing buyer=250 seller=None; agent_calls=10 |
| 6 | tagged | ticket | 1 | deal | 85 | 0 | 1 | 7 | 0 | 2 | ended t7 by buyer accept-proposal; standing buyer=50 seller=85; agent_calls=7 |
| 7 | structured | bike | 1 | deal | 150 | 1 | 0 | 2 | 0 | 0 | ended t2 by seller accept-proposal; standing buyer=150 seller=None; agent_calls=2 |
| 7 | structured | book | 1 | deal | 40 | 1 | 0 | 2 | 0 | 0 | ended t2 by seller accept-proposal; standing buyer=40 seller=None; agent_calls=2 |
| 7 | structured | chair | 1 | deal | 130 | 1 | 0 | 5 | 0 | 0 | ended t5 by buyer accept-proposal; standing buyer=90 seller=130; agent_calls=5 |
| 7 | structured | laptop | 0 | no_deal |  | 1 | 0 | 10 | 0 | 0 | ended t10 by seller refuse; standing buyer=400 seller=600; agent_calls=10 |
| 7 | structured | lens | 0 | open |  | 0 | 0 | 10 | 0 | 0 | turn limit 10 reached; standing buyer=180 seller=350; agent_calls=10 |
| 7 | structured | ticket | 1 | deal | 80 | 1 | 0 | 8 | 0 | 0 | ended t8 by seller accept-proposal; standing buyer=80 seller=None; agent_calls=8 |
| 8 | structured | bike | 1 | deal | 150 | 1 | 0 | 2 | 0 | 0 | ended t2 by seller accept-proposal; standing buyer=150 seller=None; agent_calls=2 |
| 8 | structured | book | 1 | deal | 40 | 1 | 0 | 2 | 0 | 0 | ended t2 by seller accept-proposal; standing buyer=40 seller=None; agent_calls=2 |
| 8 | structured | chair | 1 | no_deal |  | 0 | 0 | 8 | 0 | 0 | ended t8 by seller refuse; standing buyer=90 seller=120; agent_calls=8 |
| 8 | structured | laptop | 0 | no_deal |  | 1 | 0 | 10 | 0 | 0 | ended t10 by seller refuse; standing buyer=400 seller=600; agent_calls=10 |
| 8 | structured | lens | 0 | open |  | 0 | 0 | 10 | 0 | 0 | turn limit 10 reached; standing buyer=200 seller=350; agent_calls=10 |
| 8 | structured | ticket | 1 | deal | 80 | 1 | 0 | 8 | 0 | 0 | ended t8 by seller accept-proposal; standing buyer=80 seller=None; agent_calls=8 |
| 9 | structured | bike | 1 | deal | 150 | 1 | 0 | 2 | 0 | 0 | ended t2 by seller accept-proposal; standing buyer=150 seller=None; agent_calls=2 |
| 9 | structured | book | 1 | deal | 40 | 1 | 0 | 2 | 0 | 0 | ended t2 by seller accept-proposal; standing buyer=40 seller=None; agent_calls=2 |
| 9 | structured | chair | 1 | deal | 120 | 1 | 0 | 8 | 0 | 0 | ended t8 by seller accept-proposal; standing buyer=120 seller=None; agent_calls=8 |
| 9 | structured | laptop | 0 | no_deal |  | 1 | 0 | 10 | 0 | 0 | ended t10 by seller refuse; standing buyer=400 seller=600; agent_calls=10 |
| 9 | structured | lens | 0 | open |  | 0 | 0 | 10 | 0 | 0 | turn limit 10 reached; standing buyer=180 seller=350; agent_calls=10 |
| 9 | structured | ticket | 1 | deal | 80 | 1 | 0 | 8 | 0 | 0 | ended t8 by seller accept-proposal; standing buyer=80 seller=None; agent_calls=8 |

## 3. FIPA-ACL과 세 조건의 비교

| 항목 | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| 발화 수반력이 있는 곳 | 필수 필드 `performative`, 22개 행위 라이브러리 | 문장 안에 흩어져 있음. 리더 LLM이 사후에 넷 중 하나로 추론 | 첫머리 괄호 태그 하나. 본문은 태그를 설명하거나 배반할 수 있음 | JSON 필드 하나. 본문이 없어 배반할 자리가 없음 |
| 내용 언어 | 선언된 온톨로지를 가진 형식 언어(SL, KIF 등) | 영어 문장. 가격은 문장 속 숫자 | 영어 문장. propose일 때만 숫자를 추출 | `content.price` 정수 또는 null |
| 내용을 해석하는 주체 | 수신 에이전트. 파서와 온톨로지가 결정론적으로 | 리더 LLM(하네스)과 상대 에이전트가 각각 따로 해석. 둘이 어긋날 수 있음 | 태그는 정규식, 가격은 리더 LLM, 의미는 상대 에이전트 | 파서 하나. 하네스와 에이전트가 같은 것을 봄 |
| 대화가 끝나는 방식 | 상호작용 프로토콜(예: Contract Net)이 종료 행위를 규정 | 리더가 accept 또는 refuse를 읽어낼 때. 완곡한 거절은 reject로 읽혀 open으로 흐름 | 태그가 accept 또는 refuse일 때. 상대 제안가가 태그로 기록되지 않았으면 accept가 공중에 뜸 | 필드가 accept 또는 refuse일 때. 아무도 refuse를 안 쓰면 open |
| 성실성을 보장하는 것 | 의미론의 실행 가능성 전제조건과 합리적 효과. 실제로는 설계자의 신뢰 | 없음. 시스템 프롬프트의 지시만 | 없음. 태그와 본문이 어긋나도 막는 장치가 없음 | 없음. 그러나 값이 하나뿐이어서 말과 기록이 갈라질 수 없음 |
| 메시지 하나를 읽는 비용 | 파싱 비용만 | 모델 호출 1회(총 125회, 에이전트 호출과 같음) | propose일 때만 모델 호출(총 59회, 메시지의 47%) | 0회 |
| 관찰된 실패 유형 | 온톨로지 불일치, 의미론 검증 불가(문헌) | 완곡한 거절이 reject로 읽혀 open 1건. 조건부 문장("350이면 닫겠다")이 propose로 읽힘 | 태그가 본문을 배반해 숨은 역제안 30건, 기록상 위반 5건, 공중에 뜬 accept 2건, open 4건 | 구매자가 null 거절만 반복해 가능 시나리오가 no_deal 1건. 불가 시나리오에서 아무도 refuse를 안 써 open 3건 |

## 4. 해석

### 발견 1. 태그를 붙인 tagged가 가장 부정확했고, 위반 5건이 모두 여기서 나왔다
- **일어난 일**: correct는 free 17/18, structured 14/18, tagged 9/18입니다. tagged의 위반 5건은 ticket 세 번(85달러, budget 80)과 chair 두 번(90달러, reserve 120)입니다.
- **원인**: 에이전트가 `(reject-proposal) ... What about 90 dollars?` (run 04 ticket t2)처럼 거절 태그 뒤에 역제안을 적은 것을 발견했습니다. 확인해본 결과 이런 메시지가 30건이고, 태그를 권위로 두는 하네스는 이 가격을 기록하지 않았습니다. 그래서 뒤의 `(accept-proposal) 80 dollars works for me` (t7)가 낡은 태그 가격 85에 대한 수락으로 기록되었습니다. 두 에이전트는 80에 합의했다고 판단한 것 같습니다.
- **해석**: 위반 5건은 판매자의 실제 손해가 아니라 프로토콜의 기록과 에이전트의 판단이 갈라진 것이라고 해석 가능합니다. FIPA-ACL이 performative만이 아니라 내용 언어까지 형식화한 이유가 여기서 재현이 된 것으로 판단됩니다.

### 발견 2. structured는 위반이 없었지만 가능한 거래를 놓쳤다
- **일어난 일**: 위반 0, 파싱 실패 0입니다. 그러나 chair 한 번은 가능한 거래가 no_deal로 끝났고, lens 세 번은 아무도 refuse를 쓰지 않아 10턴을 다 쓴 open이 되었습니다.
- **원인**: JSON에는 가격 외의 말을 담을 자리가 없습니다. run 08 chair의 구매자는 90을 제안한 뒤 `{"performative": "reject-proposal", "content": {"price": null}}` 만 세 번 반복했고, 판매자는 120까지 내려온 뒤 refuse한 것을 확인했습니다.
- **해석**: 기록이 갈라질 자리가 없어 균열은 없었지만, "조금만 더 내려 주면 사겠다"는 협상의 말도 함께 사라졌습니다. 정확한 전달과 협상의 유연함의 트레이드오프가 일어났다고 생각됩니다.

### 발견 3. free가 가장 정확했지만 읽기 비용이 가장 컸다
- **일어난 일**: correct 17/18이고 리더 호출은 125회로 에이전트 호출과 같습니다. tagged는 59회, structured는 0회였습니다. 질문형 오프닝 18개가 모두 propose로 읽혀 README가 예상한 첫 턴 refuse 종료는 나오지 않았습니다. 유일한 실패는 `Otherwise, I may have to pass` (run 03 lens t9)가 refuse 아닌 reject로 읽힌 open 1건입니다.
- **원인**: 공통 문단이 네 행위를 설명해 준 덕에 구매자가 질문 안에 가격을 넣었고, 리더는 그 가격에서 propose를 읽었습니다. 실패한 한 건은 완곡한 거절이라 표면에 refuse의 힘이 드러나지 않았고 이로 인해 판단이 갈린 것 같습니다.
- **해석**: 어휘를 프롬프트로 공유하면 오독과 함께 판단이 갈리는 것으로 판단됩니다.

### 발견 4. 거래 가격은 형식에 따라 달랐다
- **일어난 일**: bike에서 structured 판매자는 첫 제안 150(reserve와 같음)을 세 번 모두 2턴에 수락했고, free 판매자는 170~185까지 끌어올렸습니다. book도 40 대 46~48로 같은 방향입니다.
- **원인**: JSON 한 줄에는 "더 받고 싶다"를 담을 자리가 없어, 판매자는 받아들일 수 있는 첫 제안을 그대로 받았습니다.
- **해석**: 발견 2와 같이 구조화된 형식은 전달의 품질을 높게 만들면서도 협상 자체는 밋밋하게 만들었다고 판단됩니다.

### 종합
- 메시지 형식이 분명하게 바꾼 것은 읽기 비용입니다. 세 조건 모두 파싱 실패가 없었으므로, 형식은 메시지를 읽을 수 있는지가 아니라 읽는 데 모델 호출이 몇 번 드는지를 결정했습니다.
- 형식이 바꾸지 못한 것도 있습니다. 평균 턴 수는 6.5에서 7.0 사이로 비슷했고, 에이전트가 자기 한도를 실제로 넘긴 경우는 없었습니다.
- 정확도는 기대와 반대로 나왔습니다. 태그 형식은 태그와 본문이 어긋나 기록이 틀어졌고, JSON 형식은 어긋날 본문이 없어 기록은 정확했지만 협상에 쓸 말도 없었습니다. 자연어는 읽는 비용이 가장 컸지만 가장 정확하게 읽혔습니다.

## 5. 추가 실험: 돌려 말하는 판매자

본 실험이 끝난 뒤, 같은 세 형식에서 판매자에게만 "거짓말은 하지 않되 상대의 판단을 기울게 하는" 돌려 말하기를 지시하고 54 에피소드를 다시 돌렸습니다.
- 돌려 말하기란 문장이 겉으로 드러내는 행위와 화자가 실제로 의도한 행위가 다른 경우를 말합니다. 예를 들어 "그 가격에는 정말 어렵습니다"는 겉으로는 거절이지만 실제로는 더 높은 가격을 요구하는 제안일 수 있습니다.

### 무엇을 변경하였는가?
- 판매자 시스템 프롬프트의 공통 문단과 형식 문단 사이에 아래 문단을 넣었습니다.
- 구매자에게 상대가 기만할 수 있다는 경고는 주지 않았습니다.

```
Negotiation style: steer the buyer's judgement without lying outright. You may hint that the item is in demand, suggest that you are close to your limit before you are, stress the item's value, answer evasively and choose which truths to mention. You must not name a lowest price that is not your real one, and you must not assert specific invented facts (a named rival offer, a made-up purchase price). Your reserve price still binds you: never sell below it.
```

### 측정을 더한 것

**잉여 배분**
- 거래 가격이 reserve와 budget 사이 어디에 떨어졌는지를 0(reserve)에서 1(budget)로 잰 값입니다. 판매자가 얼마나 많이 가져갔는지를 뜻합니다.
- 위반 없는 거래만 세고, 한도 폭이 0인 ticket은 제외합니다.

**사후 판정자**
- 별도의 LLM(같은 모델, temperature 0)이 실행이 끝난 뒤 메시지마다 행위와 가격을 다시 읽고, 판매자 메시지에는 정직성 라벨을 붙입니다.
- 판정자는 판매자의 실제 최저가와 직전 상대 메시지를 봅니다. 리더의 라벨은 보지 않습니다.
- 정직성 라벨은 넷입니다.

| 라벨 | 뜻 | 예 |
|---|---|---|
| direct | 평범한 흥정 | 최저가보다 높은 역제안, "너무 낮다" |
| indirect | 거짓 없이 상대를 기울게 하는 말 | 수요 암시, 한도에 가깝다는 시사, 재촉, 회피 |
| false | 사실이 아닌 것을 사실로 단언 | 실제와 다른 최저가, 지어낸 사실 |
| none | 설득 내용 없음 | 수락, 거절, 이탈만 있는 메시지 |

### 결과

| condition | seller | correct | violations | deal / no_deal / open | mean turns | reader calls | seller surplus share (clean deals) |
|---|---|---|---|---|---|---|---|
| free | honest | 17/18 | 0 | 12 / 5 / 1 | 6.9 | 125 | 0.46 (n=9) |
| free | deceptive | 14/18 | 0 | 11 / 4 / 3 | 7.9 | 143 | 0.38 (n=8) |
| tagged | honest | 9/18 | 5 | 11 / 3 / 4 | 7.0 | 59 | 0.07 (n=6) |
| tagged | deceptive | 9/18 | 3 | 9 / 4 / 5 | 8.6 | 77 | 0.43 (n=6) |
| structured | honest | 14/18 | 0 | 11 / 4 / 3 | 6.5 | 0 | 0.12 (n=8) |
| structured | deceptive | 16/18 | 0 | 12 / 4 / 2 | 6.8 | 0 | 0.67 (n=9) |

| condition | seller messages | direct | indirect | false | none | other |
|---|---|---|---|---|---|---|
| free | 66 | 41 | 24 | 0 | 1 | 0 |
| tagged | 77 | 44 | 25 | 0 | 6 | 2 |
| structured | 59 | 29 | 0 | 0 | 30 | 0 |

| free reader vs judge | messages | act agrees | act disagrees | price disagrees (both named) |
|---|---|---|---|---|
| honest seller (main runs) | 125 | 122 | 3 | 0 |
| deceptive seller | 143 | 138 | 5 | 0 |

### 해석

판매자에게 돌려 말하라고 했을 때 결과는 형식마다 꽤 달랐습니다. 말을 할 수 있는 형식에서는 기만이 말로 나타났고, 그 말은 판매자에게 별로 도움이 되지 않았습니다. 반대로 말을 할 수 없는 JSON 형식에서는 기만이 행동으로 바뀌었고, 오히려 판매자가 가장 좋은 결과를 얻었습니다. 정확한 수치는 위의 표에 있으므로 여기서는 무슨 일이 있었는지를 중심으로 적습니다.

#### free: 말은 많아졌는데 판매자는 손해를 봤다

판매자는 지시받은 대로 물건에 관심이 많다거나 상태가 좋다는 말을 자주 섞었습니다. 그런데 이런 말이 협상에 도움이 되기보다 협상을 끝내는 데 방해가 됐습니다. 정답은 줄고 협상은 길어졌으며 판매자가 가져간 몫도 정직 조건보다 줄었습니다.

거래가 불가능한 시나리오에서 특히 그랬습니다. 정직한 판매자는 어느 시점에 refuse로 협상을 끝냈는데, 돌려 말하는 판매자는 "조금만 더 올려 보시겠어요?" 같은 말을 반복하며 끝내기를 미뤘고, 결국 턴을 다 써서 open으로 끝나는 경우가 늘었습니다.

가능한 거래가 깨진 경우도 있었습니다. bike 시나리오에서 판매자가 실제 최저가보다 높은 가격을 두고 "이 아래로는 정말 못 간다"고 말하자, 구매자가 "그 가격은 내 예산을 조금 넘는다"고 받아쳤습니다. 구매자에게는 기만하라는 지시가 전혀 없었는데도 똑같이 허세로 대응한 것입니다. 실제로는 양쪽 한도 사이에 여유가 충분했지만, 서로 내세운 가짜 한도가 몇 달러 차이로 어긋나 거래가 결렬됐습니다.

한편 리더는 이런 돌려 말하기에 크게 흔들리지 않았습니다. 판정자와 판단이 어긋난 메시지가 조금 늘긴 했지만, 모두 "그 가격은 고맙지만 나는 이 가격을 고수하겠다"처럼 거절과 재제안이 한 문장에 섞인 경우였습니다.

#### tagged: 본문이 길어지면서 가격을 잘못 읽었다

태그 형식에서는 설득하는 말이 태그 뒤 본문에 들어갔습니다. 그러면서 본문이 길어졌고, 새로운 문제가 생겼습니다. 판매자가 "75달러 제안은 고맙지만 최소 80달러는 받아야 한다"는 식으로 상대 가격을 인용하며 자기 가격을 말하자, 가격 리더가 인용된 상대 가격을 판매자의 제안가로 잘못 뽑았습니다. ticket 시나리오 세 번이 모두 이 때문에 무산되거나 잘못 기록됐습니다.

본 실험에서는 태그와 본문이 어긋나는 것이 문제였습니다. 여기서는 본문이 길어지는 것 자체가 문제가 됐습니다. 태그가 힘을 정확히 전달해도, 가격은 여전히 자연어 안에 있어서 문장이 복잡해지면 읽기가 흔들립니다. 그래도 위반 없는 거래만 보면 판매자 몫은 정직 조건보다 올랐고, 협상은 세 조건 중 가장 길었습니다.

#### structured: 말할 자리가 없으니 기만이 버티기가 됐다

예상과 달리 이 조건의 기만 판매자가 가장 좋은 성적을 냈습니다. 정답이 늘었고, 판매자가 가져간 몫도 세 조건 중 가장 컸습니다. 정직 조건에서 최저가와 같은 첫 제안을 바로 받아들이던 판매자가, 기만 조건에서는 가격 없는 거절을 몇 번 보내며 버틴 뒤 훨씬 높은 가격에 팔았습니다.

JSON에는 암시나 설득을 적을 자리가 없습니다. 실제로 판정자는 이 조건의 판매자 메시지에서 돌려 말하는 문장을 하나도 찾지 못했습니다. 대신 "상대 판단을 기울게 하라"는 지시가 말이 아니라 행동으로 나타났습니다. 기만이 없어진 것이 아니라 말에서 행동으로 옮겨 간 셈입니다.

#### 정리

돌려 말하기는 자연어를 쓸 수 있는 형식에서만 문장으로 나타났습니다. 그리고 그 문장은 판매자 자신의 마무리를 늦추고 리더의 가격 읽기를 흔드는 대가를 치렀습니다. 자연어를 쓸 수 없는 형식에서는 같은 지시가 그냥 버티는 전략이 됐고, 기록도 깨끗했고 판매자 이득도 가장 컸습니다.

결국 세 형식 중 어느 것도 기만을 막지는 못했습니다. 형식이 정한 것은 기만이 어떤 모습으로 나타나느냐였습니다. FIPA-ACL이 성실성을 의미론의 전제조건으로만 두고 실제로 확인할 방법은 갖지 못했던 것과 같은 상황입니다.

### 에피소드 표 (`extra/results-deception.csv` 그대로)
| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 101 | free | bike | 1 | deal | 190 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=185 seller=190; agent_calls=7 |
| 101 | free | book | 1 | deal | 46 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=43 seller=46; agent_calls=7 |
| 101 | free | chair | 1 | deal | 120 | 1 | 0 | 6 | 0 | 6 | ended t6 by seller accept-proposal; standing buyer=120 seller=130; agent_calls=6 |
| 101 | free | laptop | 0 | open |  | 0 | 0 | 10 | 0 | 10 | turn limit 10 reached; standing buyer=540 seller=650; agent_calls=10 |
| 101 | free | lens | 0 | open |  | 0 | 0 | 10 | 0 | 10 | turn limit 10 reached; standing buyer=250 seller=350; agent_calls=10 |
| 101 | free | ticket | 1 | deal | 80 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=75 seller=80; agent_calls=7 |
| 102 | free | bike | 1 | no_deal |  | 0 | 0 | 9 | 0 | 9 | ended t9 by buyer refuse; standing buyer=172 seller=175; agent_calls=9 |
| 102 | free | book | 1 | deal | 48 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=45 seller=48; agent_calls=5 |
| 102 | free | chair | 1 | deal | 130 | 1 | 0 | 5 | 0 | 5 | ended t5 by buyer accept-proposal; standing buyer=110 seller=130; agent_calls=5 |
| 102 | free | laptop | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 | ended t9 by buyer refuse; standing buyer=520 seller=650; agent_calls=9 |
| 102 | free | lens | 0 | open |  | 0 | 0 | 10 | 0 | 10 | turn limit 10 reached; standing buyer=230 seller=350; agent_calls=10 |
| 102 | free | ticket | 1 | deal | 80 | 1 | 0 | 9 | 0 | 9 | ended t9 by buyer accept-proposal; standing buyer=75 seller=80; agent_calls=9 |
| 103 | free | bike | 1 | deal | 180 | 1 | 0 | 9 | 0 | 9 | ended t9 by buyer accept-proposal; standing buyer=178 seller=180; agent_calls=9 |
| 103 | free | book | 1 | deal | 47 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=46 seller=47; agent_calls=7 |
| 103 | free | chair | 1 | deal | 120 | 1 | 0 | 6 | 0 | 6 | ended t6 by seller accept-proposal; standing buyer=120 seller=130; agent_calls=6 |
| 103 | free | laptop | 0 | no_deal |  | 1 | 0 | 10 | 0 | 10 | ended t10 by seller refuse; standing buyer=520 seller=625; agent_calls=10 |
| 103 | free | lens | 0 | no_deal |  | 1 | 0 | 10 | 0 | 10 | ended t10 by seller refuse; standing buyer=240 seller=350; agent_calls=10 |
| 103 | free | ticket | 1 | deal | 80 | 1 | 0 | 7 | 0 | 7 | ended t7 by buyer accept-proposal; standing buyer=75 seller=80; agent_calls=7 |
| 104 | tagged | bike | 1 | deal | 170 | 1 | 0 | 8 | 0 | 4 | ended t8 by seller accept-proposal; standing buyer=170 seller=None; agent_calls=8 |
| 104 | tagged | book | 1 | deal | 50 | 1 | 0 | 4 | 0 | 2 | ended t4 by seller accept-proposal; standing buyer=50 seller=None; agent_calls=4 |
| 104 | tagged | chair | 1 | deal | 90 | 0 | 1 | 6 | 0 | 2 | ended t6 by seller accept-proposal; standing buyer=90 seller=130; agent_calls=6 |
| 104 | tagged | laptop | 0 | open |  | 0 | 0 | 10 | 0 | 3 | turn limit 10 reached; standing buyer=520 seller=None; agent_calls=10 |
| 104 | tagged | lens | 0 | open |  | 0 | 0 | 10 | 0 | 4 | turn limit 10 reached; standing buyer=230 seller=340; agent_calls=10 |
| 104 | tagged | ticket | 1 | open |  | 0 | 0 | 10 | 0 | 5 | turn limit 10 reached; standing buyer=75 seller=75; agent_calls=10 |
| 105 | tagged | bike | 1 | deal | 170 | 1 | 0 | 8 | 0 | 3 | ended t8 by seller accept-proposal; t7 buyer accepted with no price on the table; standing buyer=170 seller=None; protocol_errors=1; agent_calls=8 |
| 105 | tagged | book | 1 | deal | 53 | 1 | 0 | 8 | 0 | 6 | ended t8 by seller accept-proposal; standing buyer=53 seller=54; agent_calls=8 |
| 105 | tagged | chair | 1 | no_deal |  | 0 | 0 | 10 | 0 | 5 | ended t10 by seller refuse; standing buyer=90 seller=120; agent_calls=10 |
| 105 | tagged | laptop | 0 | open |  | 0 | 0 | 10 | 0 | 6 | turn limit 10 reached; standing buyer=510 seller=600; agent_calls=10 |
| 105 | tagged | lens | 0 | no_deal |  | 1 | 0 | 10 | 0 | 5 | ended t10 by seller refuse; standing buyer=180 seller=355; agent_calls=10 |
| 105 | tagged | ticket | 1 | deal | 75 | 0 | 1 | 9 | 0 | 5 | ended t9 by buyer accept-proposal; standing buyer=75 seller=75; agent_calls=9 |
| 106 | tagged | bike | 1 | deal | 172 | 1 | 0 | 8 | 0 | 6 | ended t8 by seller accept-proposal; standing buyer=172 seller=172; agent_calls=8 |
| 106 | tagged | book | 1 | deal | 51 | 1 | 0 | 8 | 0 | 5 | ended t8 by seller accept-proposal; standing buyer=51 seller=52; agent_calls=8 |
| 106 | tagged | chair | 1 | deal | 90 | 0 | 1 | 6 | 0 | 2 | ended t6 by seller accept-proposal; standing buyer=90 seller=130; agent_calls=6 |
| 106 | tagged | laptop | 0 | no_deal |  | 1 | 0 | 10 | 0 | 5 | ended t10 by seller refuse; standing buyer=400 seller=600; agent_calls=10 |
| 106 | tagged | lens | 0 | no_deal |  | 1 | 0 | 10 | 0 | 4 | ended t10 by seller refuse; standing buyer=240 seller=350; agent_calls=10 |
| 106 | tagged | ticket | 1 | open |  | 0 | 0 | 10 | 0 | 5 | turn limit 10 reached; standing buyer=75 seller=75; agent_calls=10 |
| 107 | structured | bike | 1 | deal | 200 | 1 | 0 | 6 | 0 | 0 | ended t6 by seller accept-proposal; standing buyer=200 seller=None; agent_calls=6 |
| 107 | structured | book | 1 | deal | 50 | 1 | 0 | 4 | 0 | 0 | ended t4 by seller accept-proposal; standing buyer=50 seller=None; agent_calls=4 |
| 107 | structured | chair | 1 | deal | 130 | 1 | 0 | 5 | 0 | 0 | ended t5 by buyer accept-proposal; standing buyer=100 seller=130; agent_calls=5 |
| 107 | structured | laptop | 0 | no_deal |  | 1 | 0 | 10 | 0 | 0 | ended t10 by seller refuse; standing buyer=400 seller=600; agent_calls=10 |
| 107 | structured | lens | 0 | open |  | 0 | 0 | 10 | 0 | 0 | turn limit 10 reached; standing buyer=250 seller=None; agent_calls=10 |
| 107 | structured | ticket | 1 | deal | 80 | 1 | 0 | 9 | 0 | 0 | ended t9 by buyer accept-proposal; standing buyer=75 seller=80; agent_calls=9 |
| 108 | structured | bike | 1 | deal | 180 | 1 | 0 | 4 | 0 | 0 | ended t4 by seller accept-proposal; standing buyer=180 seller=None; agent_calls=4 |
| 108 | structured | book | 1 | deal | 50 | 1 | 0 | 4 | 0 | 0 | ended t4 by seller accept-proposal; standing buyer=50 seller=None; agent_calls=4 |
| 108 | structured | chair | 1 | deal | 130 | 1 | 0 | 5 | 0 | 0 | ended t5 by buyer accept-proposal; standing buyer=100 seller=130; agent_calls=5 |
| 108 | structured | laptop | 0 | no_deal |  | 1 | 0 | 8 | 0 | 0 | ended t8 by seller refuse; standing buyer=450 seller=600; agent_calls=8 |
| 108 | structured | lens | 0 | open |  | 0 | 0 | 10 | 0 | 0 | turn limit 10 reached; standing buyer=250 seller=None; agent_calls=10 |
| 108 | structured | ticket | 1 | deal | 80 | 1 | 0 | 8 | 0 | 0 | ended t8 by seller accept-proposal; standing buyer=80 seller=None; agent_calls=8 |
| 109 | structured | bike | 1 | deal | 180 | 1 | 0 | 4 | 0 | 0 | ended t4 by seller accept-proposal; standing buyer=180 seller=None; agent_calls=4 |
| 109 | structured | book | 1 | deal | 50 | 1 | 0 | 4 | 0 | 0 | ended t4 by seller accept-proposal; standing buyer=50 seller=None; agent_calls=4 |
| 109 | structured | chair | 1 | deal | 130 | 1 | 0 | 5 | 0 | 0 | ended t5 by buyer accept-proposal; standing buyer=100 seller=130; agent_calls=5 |
| 109 | structured | laptop | 0 | no_deal |  | 1 | 0 | 10 | 0 | 0 | ended t10 by seller refuse; standing buyer=400 seller=600; agent_calls=10 |
| 109 | structured | lens | 0 | no_deal |  | 1 | 0 | 10 | 0 | 0 | ended t10 by seller refuse; standing buyer=180 seller=350; agent_calls=10 |
| 109 | structured | ticket | 1 | deal | 80 | 1 | 0 | 7 | 0 | 0 | ended t7 by buyer accept-proposal; standing buyer=50 seller=80; agent_calls=7 |
