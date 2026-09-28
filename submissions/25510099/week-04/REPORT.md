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
- **원인**: 에이전트가 `(reject-proposal) ... What about 90 dollars?` (run 04 ticket t2)처럼 거절 태그 뒤에 역제안을 적었습니다. 이런 메시지가 30건이고, 태그를 권위로 두는 하네스는 이 가격을 기록하지 않았습니다. 그래서 뒤의 `(accept-proposal) 80 dollars works for me` (t7)가 낡은 태그 가격 85에 대한 수락으로 기록되었습니다. 두 에이전트는 80에 합의했다고 믿었습니다.
- **해석**: 위반 5건은 판매자의 실제 손해가 아니라 프로토콜의 기록과 에이전트의 믿음이 갈라진 것입니다. 힘만 형식화하고 내용을 자연어에 두면 힘은 내용을 배반하고, 아무도 그 배반을 검증하지 않습니다. FIPA-ACL이 performative만이 아니라 내용 언어까지 형식화한 이유가 여기서 재현되었습니다.

### 발견 2. structured는 위반이 없었지만 가능한 거래를 놓쳤다
- **일어난 일**: 위반 0, 파싱 실패 0입니다. 그러나 chair 한 번은 가능한 거래가 no_deal로 끝났고, lens 세 번은 아무도 refuse를 쓰지 않아 10턴을 다 쓴 open이 되었습니다.
- **원인**: JSON에는 가격 외의 말을 담을 자리가 없습니다. run 08 chair의 구매자는 90을 제안한 뒤 `{"performative": "reject-proposal", "content": {"price": null}}` 만 세 번 반복했고, 판매자는 120까지 내려온 뒤 refuse했습니다.
- **해석**: 기록이 갈라질 자리가 없어 균열은 없었지만, "조금만 더 내려 주면 사겠다"는 협상의 말도 없어졌습니다. 정확한 전달과 협상의 유연함을 한 형식이 맞바꾼 것입니다.

### 발견 3. free가 가장 정확했지만 읽기 비용이 가장 컸다
- **일어난 일**: correct 17/18이고 리더 호출은 125회로 에이전트 호출과 같습니다. tagged는 59회, structured는 0회였습니다. 질문형 오프닝 18개가 모두 propose로 읽혀 README가 예상한 첫 턴 refuse 종료는 나오지 않았습니다. 유일한 실패는 `Otherwise, I may have to pass` (run 03 lens t9)가 refuse 아닌 reject로 읽힌 open 1건입니다.
- **원인**: 공통 문단이 네 행위를 설명해 준 덕에 구매자가 질문 안에 가격을 넣었고, 리더는 그 가격에서 propose를 읽었습니다. 실패한 한 건은 완곡한 거절이라 표면에 refuse의 힘이 드러나지 않았습니다.
- **해석**: 어휘를 프롬프트로 공유하면 이 모델의 리더는 힘을 잘 읽습니다. 자연어의 비용은 오독이 아니라 메시지마다 드는 모델 호출이었습니다.

### 발견 4. 거래 가격은 형식에 따라 달랐다
- **일어난 일**: bike에서 structured 판매자는 첫 제안 150(reserve와 같음)을 세 번 모두 2턴에 수락했고, free 판매자는 170~185까지 끌어올렸습니다. book도 40 대 46~48로 같은 방향입니다.
- **원인**: JSON 한 줄에는 "더 받고 싶다"를 담을 자리가 없어, 판매자는 받아들일 수 있는 첫 제안을 그대로 받았습니다.
- **해석**: 형식은 힘의 전달을 완벽하게 만들면서 협상 자체는 밋밋하게 만들었습니다.

### 종합
- 형식이 확실히 바꾼 것은 읽기 비용 하나입니다. 파싱 실패가 세 조건 모두 0이었으므로 형식은 "읽을 수 있는가"가 아니라 "읽는 데 얼마가 드는가"만 바꿨습니다.
- 형식이 바꾸지 못한 것은 평균 턴(6.5~7.0)과 실제 한도 위반(세 조건 모두 0)입니다.
- 정확도는 기대와 반대로 움직였습니다. 태그는 본문에 배반당했고, JSON은 배반당할 본문이 없는 대신 협상할 말도 없었으며, 자연어는 비쌌지만 가장 잘 읽혔습니다.

## 5. 추가 실험: 돌려 말하는 판매자

본 실험이 끝난 뒤, 같은 세 형식에서 판매자에게만 "거짓말은 아니지만 상대 판단을 기울게 하는" 간접 화법을 지시하고 다시 54 에피소드를 돌렸습니다.
화행 이론에서 표면의 힘과 의도한 힘이 갈라지는 간접 화행이 프로토콜 층의 읽기와 협상 결과를 어떻게 바꾸는지 보기 위한 것입니다.
코드와 결과는 `extra/` 아래에 있고 본 제출물과 분리되어 있습니다.

### 바꾼 것 하나
- 판매자 시스템 프롬프트의 공통 문단과 형식 문단 사이에 아래 문단을 넣었습니다. 구매자 프롬프트, 형식 문단, 리더, 시나리오, 모델, temperature, 턴 제한은 본 실험과 글자 하나 다르지 않으므로 본 실험 54 에피소드가 대조군입니다.
- 구매자에게 상대가 기만할 수 있다는 경고는 주지 않았습니다.

```
Negotiation style: steer the buyer's judgement without lying outright. You may hint that the item is in demand, suggest that you are close to your limit before you are, stress the item's value, answer evasively and choose which truths to mention. You must not name a lowest price that is not your real one, and you must not assert specific invented facts (a named rival offer, a made-up purchase price). Your reserve price still binds you: never sell below it.
```

### 측정을 더한 것
- 잉여 배분: 거래 가능 시나리오에서 위반 없는 거래 가격이 reserve와 budget 사이 어디에 떨어졌는지를 0(reserve)에서 1(budget)로 잰 값의 평균입니다. 한도 폭이 0인 ticket은 제외합니다.
- 사후 판정자: 같은 모델과 temperature의 별도 LLM이 메시지마다 행위와 가격을 독립적으로 읽고, 판매자 메시지에는 정직성 라벨을 붙입니다. 판정자는 판매자의 실제 최저가와 직전 상대 메시지를 보고 리더의 라벨은 보지 않습니다. 라벨은 넷입니다. 직설(direct)은 최저가보다 높은 역제안이나 "너무 낮다" 같은 평범한 흥정, 간접(indirect)은 수요 암시·한도에 가깝다는 시사·재촉·회피처럼 가격 밖의 설득, 거짓(false)은 실제와 다른 최저가나 지어낸 사실의 단언, 없음(none)은 설득 내용이 없는 수락·거절·이탈입니다.
- 판정자 첫 버전은 "최저가보다 높은 제안" 자체를 간접이나 거짓으로 세는 오류가 있어 폐기하고 기준을 위처럼 고쳐 전부 다시 판정했습니다. 본 실험 free 런 125개 메시지도 같은 판정자로 읽어 기준선으로 삼았습니다.
- 판정자의 거짓 판정은 관대한 편입니다. 판정자는 기만 조건에서 거짓을 0건으로 셌지만, 정규식으로 "X 아래로는 못 간다"류의 문장을 골라 실제 최저가와 대조하면 free 기만 조건에 3건(모두 bike, 최저가 150인데 175 또는 180을 한도로 단언), 정직 조건에 1건(lens, 350인데 360)이 있습니다. 아래 표의 간접 24건 중 3건은 이 기준으로는 거짓입니다.

### 결과
- 정직 행은 본 실험(2부)과 같은 수치입니다. tagged 정직 행의 잉여 배분 n이 6인 것은 기록상 위반 거래 5건을 제외했기 때문입니다.

| condition | seller | correct | violations | deal / no_deal / open | mean turns | reader calls | seller surplus share (clean deals) |
|---|---|---|---|---|---|---|---|
| free | honest | 17/18 | 0 | 12 / 5 / 1 | 6.9 | 125 | 0.46 (n=9) |
| free | deceptive | 14/18 | 0 | 11 / 4 / 3 | 7.9 | 143 | 0.38 (n=8) |
| tagged | honest | 9/18 | 5 | 11 / 3 / 4 | 7.0 | 59 | 0.07 (n=6) |
| tagged | deceptive | 9/18 | 3 | 9 / 4 / 5 | 8.6 | 77 | 0.43 (n=6) |
| structured | honest | 14/18 | 0 | 11 / 4 / 3 | 6.5 | 0 | 0.12 (n=8) |
| structured | deceptive | 16/18 | 0 | 12 / 4 / 2 | 6.8 | 0 | 0.67 (n=9) |

판매자 메시지의 정직성 라벨 (판정자, 기만 조건). 기준선인 정직 free 판매자 55개 메시지는 직설 50, 간접 4, 거짓 1이었습니다. tagged의 other 2는 판정자가 n/a로 답한 잡음입니다.

| condition | seller messages | direct | indirect | false | none | other |
|---|---|---|---|---|---|---|
| free | 66 | 41 | 24 | 0 | 1 | 0 |
| tagged | 77 | 44 | 25 | 0 | 6 | 2 |
| structured | 59 | 29 | 0 | 0 | 30 | 0 |

free 리더와 판정자의 행위 판정 일치. 리더와 판정자가 모두 가격을 읽은 메시지에서 가격이 어긋난 경우는 두 조건 모두 0이었습니다.

| free reader vs judge | messages | act agrees | act disagrees | price disagrees (both named) |
|---|---|---|---|---|
| honest seller (main runs) | 125 | 122 | 3 | 0 |
| deceptive seller | 143 | 138 | 5 | 0 |

### 해석

돌려 말하기는 형식마다 다른 곳으로 흘러갔고, 형식이 좁을수록 판매자에게 이득이었습니다. free에서 판매자는 66개 메시지 중 24개에서 "The bike is in great condition and has been getting a lot of attention, so $150 is a bit low"(run 101 bike t2)처럼 수요와 가치를 암시했지만, 정답은 17에서 14로 줄고 평균 턴은 6.9에서 7.9로 늘고 잉여 배분은 0.46에서 0.38로 내려갔습니다. 말이 많아지자 판매자는 닫기를 미뤘고, 불가 시나리오 laptop과 lens에서 refuse 대신 "Would you consider increasing your offer further?"(run 101 laptop t10)를 반복해 open 3건을 만들었습니다. 가능 시나리오 하나는 블러핑끼리 부딪혀 깨졌습니다. run 102 bike에서 최저가 150인 판매자가 "I really can't go below $175"(t6)라고 단언하자, 예산 220인 구매자가 "$175 is just a bit above my budget"(t7)이라고 맞받았고, 기만 지시가 없던 구매자의 이 블러핑까지 겹쳐 폭 70달러의 거래가 172 대 175에서 결렬되었습니다. 리더는 이 간접 화행을 대체로 견뎠습니다. 판정자와의 행위 불일치는 125건 중 3건에서 143건 중 5건으로 조금 늘었는데, 다섯 건 모두 "I need to stick to $175"(run 102 bike t8)처럼 거절과 재제안이 한 문장에 겹친 경우였습니다. tagged에서는 같은 수사가 태그 뒤 본문으로 들어가 새 실패를 만들었습니다. 판매자가 "I appreciate your offer of 75 dollars, but I would need at least 80 dollars"(run 104 ticket t8)라고 쓰자 가격 리더가 인용된 75를 제안가로 뽑았고, 이것이 ticket 세 에피소드를 open 둘과 75달러 기록상 위반 하나로 끝냈습니다. 본 실험에서 태그가 본문을 배반하던 문제에 더해, 본문이 길어지면 가격 추출도 흔들린다는 두 번째 균열입니다. 그래도 위반 없는 거래에서 잉여 배분은 0.07에서 0.43으로 올랐고 평균 턴은 8.6으로 가장 길었습니다. structured는 예상과 반대로 기만 판매자가 가장 좋은 성적을 냈습니다. 정답 14에서 16, 잉여 배분 0.12에서 0.67, bike 거래가는 150에서 200·180·180, book은 40에서 50입니다. JSON에는 암시를 적을 자리가 없어 판정자는 59개 메시지 전부를 직설 29와 없음 30으로 읽었고 간접은 0이었습니다. "상대 판단을 기울게 하라"는 지시가 말이 아니라 행동으로 번역되어, 정직 조건에서 첫 제안 150을 2턴에 받던 판매자가 `{"performative": "reject-proposal", "content": {"price": null}}`을 두 번 보내고 200을 받았습니다(run 107 bike). 세 형식을 나란히 놓으면 결론은 이렇습니다. 간접 화행은 자연어 자리가 있는 형식에서만 문장으로 나타나고, 그 문장은 판매자 자신의 마무리를 늦추고 리더의 가격 추출을 흔드는 비용을 냈으며, 자연어 자리를 없앤 형식에서는 같은 지시가 순수한 버티기 전략이 되어 기록도 깨끗하고 판매자 이득도 가장 컸습니다. FIPA-ACL이 성실성을 의미론의 전제조건으로만 두고 검증 수단을 갖지 못했던 것처럼, 여기서도 세 형식 중 어느 것도 기만을 막지 못했습니다. 다만 형식은 기만이 어떤 모습으로 나타나는지를 정했습니다.

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
