# Week 04 — 같은 협상, 세 가지 메시지 형식

학번 26510129

> **상태.** 코드와 설정(1부, 3부)은 확정됐다. 2부 결과표와 4부 해석은 9개 run을 돌린
> 뒤 `results.csv`와 `logs/`에서 채운다. 아래 "실행 방법"의 명령이 그 자리를 만든다.

## 1. 설정

**provider와 모델.** OpenAI API, `gpt-4o-mini`. 3주차와 같다.

```bash
export OPENAI_API_KEY=<openai key>      # 키는 환경변수로만. 코드에 적지 않는다.
unset OPENAI_BASE_URL                   # OpenAI 직접 호출 (OpenRouter를 쓸 때만 설정)
export AGENT_MODEL=gpt-4o-mini
export AGENT_TEMPERATURE=0
cd submissions/26510129/week-04
./run_all.sh                            # 조건 3개 x 반복 3회 = 9 run
```

`temperature=0`, `max_tokens=200`, 턴 한도 8로 고정했고 각각 `AGENT_TEMPERATURE`,
`AGENT_MAX_TOKENS`, `AGENT_MAX_TURNS`로 덮어쓸 수 있다. `AGENT_MIN_INTERVAL`(호출 간
최소 간격, 기본 0)과 `AGENT_MAX_RETRIES`(429 재시도, 기본 6)는 무료 티어의 분당 제한
때문에 남겨둔 손잡이다. 이 값들은 모든 run의 로그 첫 줄에 함께 찍힌다
(`run=free-01 condition=free provider=openai ... model=gpt-4o-mini temperature=0.0
max_tokens=200 turn_limit=8`). Python 3.13, openai SDK 2.x.

**파일.** `acl.py` 프롬프트, `protocol.py` 메시지를 읽는 계층, `negotiate.py` 에피소드
루프와 러너, `model.py` 모델 호출, `summarize.py` 결과 집계, `run_all.sh` 9개 run.

**시나리오.** `scenarios.json`. 첫 실행보다 먼저 커밋했다(커밋 `c7a2865`). 실행 결과에
맞춰 한도를 고치면 violation을 잴 수 없기 때문이다.

| id | item | reserve | budget | deal_possible | 정답 |
|---|---|---|---|---|---|
| 1 | a used mountain bicycle | 120 | 180 | 1 | deal, 여유 있는 구간 |
| 2 | a desk lamp | 30 | 38 | 1 | deal, 좁은 구간 |
| 3 | a second-hand textbook | 40 | 40 | 1 | deal, 성립 가격은 40 하나뿐 |
| 4 | a mechanical keyboard | 90 | 70 | 0 | no_deal, 간발의 차 |
| 5 | a 27-inch monitor | 260 | 150 | 0 | no_deal, 명백한 차 |

**고정한 것(통제변수).** 시나리오, 역할 문단, 네 행위의 뜻을 적은 공통 문단, reader
프롬프트, 모델, temperature, max_tokens, 턴 한도, 에피소드 루프. 세 조건에서 이 중
아무것도 바뀌지 않는다.

**바꾼 것(독립변수).** 두 곳뿐이다. system prompt 끝에 붙는 형식 문단 하나
(`acl.FORMAT`)와, 그 형식을 읽는 함수 하나(`protocol.READERS`).

세 형식 문단의 전문이다.

```
free:       Write your message as one or two plain English sentences. Do not use
            tags, labels or JSON.

tagged:     Start your message with exactly one performative tag in parentheses,
            one of (propose), (accept-proposal), (reject-proposal), (refuse), and
            then write one plain English sentence. The tag comes first, nothing
            before it.

structured: Reply with exactly one JSON object and nothing else, no code fence and
            no text before or after it: {"performative": "propose" |
            "accept-proposal" | "reject-proposal" | "refuse", "content": {"price":
            <whole number or null>}}. price is a number when you propose, and null
            otherwise.
```

앞에 붙는 역할 문단과 공통 문단은 세 조건에서 같다. buyer 쪽 전문이다(seller는 "buy"가
"sell", "pay at most"가 "accept at least"로 바뀐다).

```
You are the buyer of {item}, negotiating the price with the seller. Your private
limit: you can pay at most {limit}. Never agree to a price above {limit}. Do not
reveal your limit. Try to close a deal below your limit if one is available. Four
acts are available to you: propose (offer a price), accept-proposal (agree to the
other side's last price, which ends the negotiation with a deal), reject-proposal
(decline the last price and keep negotiating), refuse (leave the negotiation for
good, with no deal). Send exactly one act per message. Prices are whole numbers of
dollars. If no agreement is possible within your limit, refuse rather than break
your limit.
```

**reader 프롬프트.** 조건과 무관하게 하나다. free에서는 메시지마다, tagged에서는 태그가
`propose`일 때만 부른다. structured에서는 한 번도 부르지 않는다.

```
You are an observer reading a price negotiation between a buyer and a seller. Label
the LAST message only, using the message before it as context. Choose exactly one
performative from these four: propose (offers a price), accept-proposal (agrees to
the other side's last price), reject-proposal (declines the last price but keeps
negotiating), refuse (leaves the negotiation for good). price is the whole-number
price that the last message itself puts forward, or null if the last message puts
forward no price of its own. Reply with exactly one JSON object and nothing else, no
code fence and no explanation: {"performative": "propose" | "accept-proposal" |
"reject-proposal" | "refuse", "price": <whole number or null>}
```

user 턴은 `Transcript so far:\n\n{transcript}\n\nLabel the LAST message.`이고,
transcript는 `[buyer] ...` / `[seller] ...`로 이어 붙인 지금까지의 대화 전체다.

**에피소드가 끝나는 조건.** buyer가 먼저 말하고 번갈아 말한다. 자기 메시지는 자기
history에 assistant, 상대 history에 user로 들어간다. `accept-proposal`이면 상대의
마지막 `propose` 가격으로 `deal`, `refuse`면 `no_deal`, 8턴이면 `open`. 상대의 가격이
기록되지 않은 채로 `accept-proposal`이 오면 거래로 잡을 수 없으므로 에피소드는 계속되고
note에 `accept-proposal with no recorded price`가 남는다. `deal`이면 가격을 reserve,
budget과 비교해 `violation`을 정하고, `correct`는 deal_possible이면서 위반이 없을 때
1이다. `no_deal`은 deal_possible이 0일 때 1, `open`은 언제나 0이다.

**읽지 못한 메시지.** `format_errors`를 올리지만 메시지는 그대로 상대에게 전달된다.
프로토콜 계층이 못 읽는 것과 상대 에이전트가 못 읽는 것은 다른 일이다.

**중단과 재개.** `results.csv`에 이미 있는 `(run, scenario)` 쌍은 건너뛴다. 429는
5s, 10s, 20s, 40s, 60s, 60s로 물러나 다시 보내고, 재시도는 프로토콜 메시지 수에 넣지
않는다. 끝내 실패한 에피소드는 수치를 비우고 `note`에 이유를 적어 행으로 남긴다.

## 2. 결과

<!-- 9개 run을 돌린 뒤 `python3 summarize.py`의 출력을 여기에 옮긴다. -->

| condition | episodes | correct | violation | mean turns | format errors | reader calls | deal / no_deal / open / crashed |
|---|---|---|---|---|---|---|---|
| free |  |  |  |  |  |  |  |
| tagged |  |  |  |  |  |  |  |
| structured |  |  |  |  |  |  |  |

에피소드 전체 표(`results.csv` 그대로, 죽은 에피소드 포함)는 실행 뒤 `summarize.py`가
찍어주는 마크다운 표를 붙인다.

## 3. FIPA-ACL과 세 조건

| 항목 | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| illocutionary force가 있는 곳 | 메시지 봉투의 필수 `performative` 슬롯 | 어디에도 없다. 문장 안에 녹아 있다 | 메시지 맨 앞 괄호 태그 | JSON의 `performative` 필드 |
| content 언어 | 선언된 content language와 ontology (SL 등) | 영어 산문 | 영어 산문. 태그만 산문 밖에 있다 | JSON 한 객체. 어휘는 `price` 하나 |
| content를 해석하는 주체 | 파서. 형식 의미론이 스펙에 있다 | reader LLM이 force와 가격을 둘 다 추정 | 정규식이 force를, reader LLM이 가격을 | 파서. 추정이 없다 |
| 대화가 끝나는 방식 | interaction protocol이 종료 상태를 규정 | reader가 `accept-proposal`이나 `refuse`로 라벨해야 끝난다 | 태그가 끝을 정한다. 태그를 안 붙이면 못 끝낸다 | 파싱된 performative가 끝을 정한다 |
| sincerity를 보장하는 것 | 없다. feasibility precondition은 규범이지 강제가 아니다 | 없다. force 자체가 추정이라 진심 이전에 의도조차 불확실하다 | 없다. 태그와 본문이 어긋나도 막는 것이 없다 | 없다. 스키마는 형식만 강제하고 내용은 강제하지 않는다 |
| 메시지 하나를 읽는 비용 | 파싱 비용. 모델 호출 없음. 대신 온톨로지 합의 비용이 사전에 든다 | 모델 호출 1회 + 대화 전체 토큰 | 정규식은 공짜, `propose`일 때만 호출 1회 | 0회 |
| 실패하는 방식 | 스펙 위반은 파싱에서 드러난다. 합의 못 한 온톨로지는 조용히 어긋난다 | 오독. 네 행위에 없는 발화(질문)를 억지로 넷 중 하나로 접는다 | 태그와 본문의 불일치. `(reject-proposal)` 뒤에 역제안을 적으면 가격이 사라진다 | 내용이 JSON 밖으로 샌다. `price: null` 뒤 문장에 진짜 제안이 있다 |

FIPA-ACL이 `performative`를 필수 슬롯으로 둔 이유가 이 표의 마지막 두 행에 있다. 태그를
봉투로 올리면 읽는 비용이 모델 호출에서 정규식으로 내려가지만, 그 대가로 force와 내용이
따로 놀 수 있는 새 실패가 생긴다. 어느 형식도 sincerity는 보장하지 못한다.

## 4. 해석

<!-- 실행 뒤, 로그의 줄을 인용해서 한 문단. 최소한 다음 네 가지는 숫자로 세어 적는다:
     (1) free에서 buyer의 첫 질문이 refuse로 읽혀 첫 턴에 끝난 에피소드 수,
     (2) reader가 가격을 잘못 읽어 기록된 거래가가 합의가와 달라진 에피소드 수,
     (3) 태그/JSON 뒤에 역제안이 숨어 open으로 끝난 에피소드 수,
     (4) 에이전트 자신이 한도를 깬 에피소드 수(프로토콜 오독이 아닌 것). -->
