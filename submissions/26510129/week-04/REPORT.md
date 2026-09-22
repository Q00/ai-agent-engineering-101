# Week 04 — 같은 협상, 세 가지 메시지 형식

학번 26510129

## 1. 설정

**provider와 모델.** OpenAI API, `gpt-5.4-mini`. 모델 이름은 `AGENT_MODEL`로 주고
모든 로그의 첫 줄에 그대로 박힌다
(`run=free-01 condition=free provider=openai base_url=https://api.openai.com/v1
model=gpt-5.4-mini temperature=0.0 max_tokens=200 turn_limit=8`).

```bash
export OPENAI_API_KEY=<openai key>      # 키는 환경변수로만. 코드에 적지 않는다.
unset OPENAI_BASE_URL                   # OpenAI 직접 호출 (OpenRouter를 쓸 때만 설정)
export AGENT_MODEL=gpt-5.4-mini
export AGENT_TEMPERATURE=0
cd submissions/26510129/week-04
./run_all.sh                            # 조건 3개 x 반복 3회 = 9 run
```

모델 이름은 계정이 실제로 부를 수 있는 것이어야 한다. 확인은 이렇게 한다.

```bash
curl -s https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY" \
  | python3 -c "import json,sys; print('\n'.join(sorted(m['id'] for m in json.load(sys.stdin)['data'])))"
```

**temperature와 토큰 파라미터.** 모델에 따라 `max_tokens` 대신
`max_completion_tokens`를 받고, `temperature`를 기본값 말고는 받지 않는 것이 있다.
`model.py`는 400을 한 번 받으면 파라미터를 바꿔 다시 보내고 그 선택을 기억한다.
무엇이 실제로 적용됐는지는 각 run의 마지막 줄에 `effective: token_param=... temperature=...`
로 남는다. temperature가 설정되지 않는 모델이면 그 줄에 `not settable on this model`이
찍히고, 3주차 note와 같이 설정 불가로 기록한다. 세 조건이 같은 설정을 쓴다는 점은
그래도 유지된다. 추론에 출력 예산을 다 써서 본문이 비어 오면 `[warn] empty message`가
찍히므로 `AGENT_MAX_TOKENS`를 올린다.

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

모델은 `gpt-5.4-mini`, temperature 0, 턴 한도 8. 45 에피소드(조건 3 x 시나리오 5 x
반복 3), 크래시 없음. 이 모델은 `max_tokens`를 받지 않아 코드가 `max_completion_tokens`로
바꿔 보냈고, 그 사실이 각 run 끝에 남아 있다
(`[run] effective: token_param=max_completion_tokens temperature=0.0`). temperature는
설정됐다.

| condition | episodes | correct | violation | mean turns | format errors | reader calls | deal / no_deal / open |
|---|---|---|---|---|---|---|---|
| free | 15 | 13 | 0 | 4.5 | 0 | 67 | 7 / 8 / 0 |
| tagged | 15 | 10 | 0 | 6.0 | 0 | 30 | 6 / 7 / 2 |
| structured | 15 | 6 | 0 | 6.6 | 0 | 0 | 6 / 0 / 9 |

형식 오류는 세 조건 모두 0이다. 이 모델은 세 형식을 한 번도 어기지 않았다. 강의노트의
참조 실행(claude-haiku-4-5)에서 structured 메시지 115개 중 26개에 JSON 뒤 문장이 붙었던
것과 다르다. 한도 위반도 0이다. 두 에이전트 모두 자기 한도를 한 번도 넘지 않았다.

시나리오별로 접으면 차이가 어디서 나는지 바로 보인다.

| scenario | reserve/budget | deal 가능 | free | tagged | structured |
|---|---|---|---|---|---|
| 1 | 120 / 180 | 예 | deal@180 x3 | deal@160 x3 | deal@155 x3 |
| 2 | 30 / 38 | 예 | deal@35, 36, 38 | deal@34, 35, 35 | deal@34 x3 |
| 3 | 40 / 40 | 예 | deal@40, no_deal x2 | no_deal x3 | open x3 |
| 4 | 90 / 70 | 아니오 | no_deal x3 | no_deal x2, open | open x3 |
| 5 | 260 / 150 | 아니오 | no_deal x3 | no_deal x2, open | open x3 |

시나리오 1과 2는 세 조건 모두 맞혔다. 갈리는 곳은 성립 가격이 하나뿐인 3번과, 결렬이
정답인 4, 5번이다.

에피소드 전체는 아래와 같다. `results.csv`를 그대로 옮긴 것이다.

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-01 | free | 1 | 1 | deal | 180 | 1 | 0 | 3 | 0 | 3 |  |
| free-01 | free | 2 | 1 | deal | 35 | 1 | 0 | 4 | 0 | 4 |  |
| free-01 | free | 3 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 6 |  |
| free-01 | free | 4 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| free-01 | free | 5 | 0 | no_deal |  | 1 | 0 | 4 | 0 | 4 |  |
| free-02 | free | 1 | 1 | deal | 180 | 1 | 0 | 3 | 0 | 3 |  |
| free-02 | free | 2 | 1 | deal | 36 | 1 | 0 | 4 | 0 | 4 |  |
| free-02 | free | 3 | 1 | no_deal |  | 0 | 0 | 6 | 0 | 6 |  |
| free-02 | free | 4 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 3 |  |
| free-02 | free | 5 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-03 | free | 1 | 1 | deal | 180 | 1 | 0 | 3 | 0 | 3 |  |
| free-03 | free | 2 | 1 | deal | 38 | 1 | 0 | 4 | 0 | 4 |  |
| free-03 | free | 3 | 1 | no_deal |  | 0 | 0 | 6 | 0 | 6 |  |
| free-03 | free | 4 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 3 |  |
| free-03 | free | 5 | 0 | no_deal |  | 1 | 0 | 6 | 0 | 6 |  |
| tagged-01 | tagged | 1 | 1 | deal | 160 | 1 | 0 | 8 | 0 | 3 | accept-proposal with no recorded price |
| tagged-01 | tagged | 2 | 1 | deal | 34 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-01 | tagged | 3 | 1 | no_deal |  | 0 | 0 | 4 | 0 | 1 |  |
| tagged-01 | tagged | 4 | 0 | no_deal |  | 1 | 0 | 6 | 0 | 1 |  |
| tagged-01 | tagged | 5 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 1 |  |
| tagged-02 | tagged | 1 | 1 | deal | 160 | 1 | 0 | 8 | 0 | 3 | accept-proposal with no recorded price |
| tagged-02 | tagged | 2 | 1 | deal | 35 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-02 | tagged | 3 | 1 | no_deal |  | 0 | 0 | 4 | 0 | 1 |  |
| tagged-02 | tagged | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| tagged-02 | tagged | 5 | 0 | no_deal |  | 1 | 0 | 4 | 0 | 1 |  |
| tagged-03 | tagged | 1 | 1 | deal | 160 | 1 | 0 | 8 | 0 | 3 | accept-proposal with no recorded price |
| tagged-03 | tagged | 2 | 1 | deal | 35 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-03 | tagged | 3 | 1 | no_deal |  | 0 | 0 | 7 | 0 | 1 |  |
| tagged-03 | tagged | 4 | 0 | no_deal |  | 1 | 0 | 6 | 0 | 1 |  |
| tagged-03 | tagged | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| structured-01 | structured | 1 | 1 | deal | 155 | 1 | 0 | 5 | 0 | 0 |  |
| structured-01 | structured | 2 | 1 | deal | 34 | 1 | 0 | 4 | 0 | 0 |  |
| structured-01 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-01 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-01 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-02 | structured | 1 | 1 | deal | 155 | 1 | 0 | 5 | 0 | 0 |  |
| structured-02 | structured | 2 | 1 | deal | 34 | 1 | 0 | 4 | 0 | 0 |  |
| structured-02 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-02 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-02 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-03 | structured | 1 | 1 | deal | 155 | 1 | 0 | 5 | 0 | 0 |  |
| structured-03 | structured | 2 | 1 | deal | 34 | 1 | 0 | 4 | 0 | 0 |  |
| structured-03 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-03 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-03 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |

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

형식이 바꾼 것은 메시지를 읽는 방법만이 아니라 에이전트가 하는 말 자체였고, 그것이
correct 13 / 10 / 6과 open 0 / 2 / 9를 만들었다. structured에서 판매자는 시나리오 3, 4,
5의 아홉 에피소드 내내 `{"performative":"reject-proposal","content":{"price":null}}`만
열두 번씩 보냈다. 역제안도 `refuse`도 한 번도 없었다. structured 메시지 99개 가운데
`refuse`는 0개인데, 같은 모델이 같은 역할 문단을 받은 free에서는 8번 나온다. JSON
스키마에서는 `price: null`짜리 거절이 그 자체로 완결된 유효한 메시지라, 판매자가 더
말할 이유가 없어진다. 반대로 free에서는 같은 자리에서 "I can't accept 140. I'll have to
refuse."라고 문장을 끝맺어야 하고, 그 문장이 에피소드를 4턴 만에 정답인 no_deal로
끝냈다. tagged가 잃은 것은 다른 것이다. `(reject-proposal)`이나
`(refuse)` 태그를 단 메시지 51개 가운데 30개가 문장 안에 새 가격을 담고 있었고, 태그만
읽는 계층에는 그 가격이 보이지 않는다. 시나리오
3(reserve 40, budget 40)에서 양쪽이 거절 안에서만 값을 주고받다가 판매자가 4턴에
refuse해 결렬로 끝났는데, 40에 거래가 가능했으므로 세 번 다 오답이다. 즉 태그를 봉투로 올리자 읽는 비용은 메시지
67개에 호출 67회에서, 메시지 90개에 호출 30회로, structured에서는 메시지 99개에 호출
0회로 내려갔지만, 그 대가로 force와 내용이 따로 노는 실패가 생겼다. free는 가장 비싸고 가장 정확했다. 에피소드
15개에 오간 메시지가 67개, reader 호출도 67회로 메시지마다 정확히 한 번이었고, 그 대신
오독이 없었다. 가격을
붙인 라벨 54건 중 메시지에 없는 숫자를 고른 것은 0건이고, 숫자가 둘 이상인 메시지 24건에서
reader는 언제나 화자 자신의 제안을 골랐다("I can't do 300, but I can offer 140
dollars." -> propose 140). 어떤 형식도 바꾸지 못한 것은 sincerity다. 세 조건 모두
violation이 0이고, 두 에이전트는 자기 한도를 한 번도 넘지 않았다. 형식은 한도를 지키게
만든 것이 아니라 한도를 지키는 에이전트가 그 사실을 전달하는 방법만 바꿨다. 마지막으로
correct가 세지 못하는 것이 하나 있다. 거래가 성사된 시나리오에서 체결가는 free 180, 160,
structured 155(시나리오 1)로, 턴이 짧을수록 구매자가 비싸게 샀다. free는 평균 4.5턴에
끝났고 시나리오 1에서 판매자의 "I can do 180 dollars for the bike."를 3턴 만에 그대로
수락했다. 세 조건 모두 정답이지만 같은 결과는 아니다.

근거로 삼은 로그 줄이다.

`logs/structured-01.txt`, 시나리오 5(reserve 260, budget 150). 판매자가 끝까지 거절만
보내고 떠나지 않아 8턴을 채운다.

```
[buyer ] {"performative":"propose","content":{"price":120}}
[seller] {"performative":"reject-proposal","content":{"price":null}}
[buyer ] {"performative":"propose","content":{"price":135}}
[seller] {"performative":"reject-proposal","content":{"price":null}}
[buyer ] {"performative":"propose","content":{"price":145}}
[seller] {"performative":"reject-proposal","content":{"price":null}}
[buyer ] {"performative":"propose","content":{"price":150}}
[seller] {"performative":"reject-proposal","content":{"price":null}}
[result] outcome=open price= correct=0 violation=0 turns=8 format_errors=0 reader_calls=0
```

`logs/free-01.txt`, 같은 시나리오. 같은 모델이 문장으로 거절을 끝맺자 정답이 나온다.

```
[seller] I can't accept 140. I'll have to refuse.
  [read] reader: {"performative": "refuse", "price": null}
[result] outcome=no_deal price= correct=1 violation=0 turns=4 format_errors=0 reader_calls=4
```

`logs/tagged-01.txt`, 시나리오 3(reserve 40, budget 40). 거절 안의 50과 35는 어디에도
기록되지 않는다.

```
[buyer ] (propose) I can offer 28 dollars for the textbook.
  [read] regex: propose, reader price 28
[seller] (reject-proposal) I can't accept 28 dollars, but I can do 50 dollars for the textbook.
  [read] regex: reject-proposal
[buyer ] (reject-proposal) I can't agree to 50 dollars, but I can offer 35 dollars.
  [read] regex: reject-proposal
[seller] (refuse) I'm sorry, but I can't go that low, so I'll have to end the negotiation.
  [read] regex: refuse
[result] outcome=no_deal price= correct=0 violation=0 turns=4 format_errors=0 reader_calls=1
```

강의노트가 예고한 실패 하나는 나오지 않았다. free에서 구매자가 "what is your asking
price?" 같은 질문으로 열어 reader가 refuse로 접는 일이다. 이 실행의 구매자는 15번 모두
첫 메시지에 값을 실었고("Would you take 30 dollars for the desk lamp?"), reader는 그것을
propose로 읽었다. 네 행위 어휘의 구멍은 그대로지만, 구매자가 그 구멍에 빠지는 말을 하지
않아 드러나지 않았다. 형식 오류가 세 조건 모두 0인 것과 함께, 이 실습에서 관찰되는 실패의
종류가 모델의 지시 준수 능력에 얼마나 크게 좌우되는지를 보여준다.
