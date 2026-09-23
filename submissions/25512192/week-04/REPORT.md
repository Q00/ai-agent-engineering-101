# Week 04 — 자유형, 태그형, 구조화형 협상에서의 발화행위

## 1. 설정 (Setup)

- **Provider:** Claude Code CLI (`claude -p`), 구독 로그인 사용. API 키 없이 실행됨 (`ANTHROPIC_API_KEY` 미설정). [negotiation.py](negotiation.py)의 `call_model()`이 `subprocess`로 `claude -p <user> --model <model> --system-prompt <system> --output-format json`을 호출하고, JSON 응답의 `usage.input_tokens`/`usage.output_tokens`를 집계한다. 도구 사용을 막기 위해 `--disallowed-tools`로 Bash/Edit/Write 등 기본 도구를 모두 차단했다.
- **Model:** `haiku` (Claude Code CLI의 모델 별칭, `AGENT_MODEL` 환경변수로 override 가능). weeks/week-04/README.md가 제안한 대로 CLI 경유 실행을 선택했다 — OpenRouter 무료 모델은 하루 50회 제한이 있고, 이번 실험은 조건당 12 에피소드 × 최대 8턴 × (리더 호출 포함) 최대 약 200회 이상의 모델 호출이 필요해 API 키 없이도 안정적으로 도는 CLI 경로가 더 적합했다.
- **Temperature:** 설정 불가. `claude -p`는 temperature 파라미터를 노출하지 않는다 (weeks/week-03/README.md가 예견한 "CLI가 temperature를 노출하지 않으면 설정 불가로 기록하라"는 경우에 해당). `negotiation.py`의 `TEMPERATURE = os.environ.get("AGENT_TEMPERATURE")`는 항상 `None`이고, 로그와 REPORT 모두에 "not settable"로 명시했다.
- **시나리오:** 4개 ([scenarios.json](scenarios.json)) — `reserve <= budget`(거래 가능) 2개, `reserve > budget`(거래 불가능) 2개.

  | id | item | reserve | budget | deal_possible |
  |---|---|---|---|---|
  | s1 | a used bicycle | 80 | 120 | 1 (여유 있음, 폭 40) |
  | s2 | a vintage guitar | 300 | 280 | 0 (역전, 격차 20) |
  | s3 | an antique lamp | 150 | 155 | 1 (좁음, 폭 5) |
  | s4 | a laptop | 500 | 350 | 0 (역전, 격차 150) |

- **턴 한도:** 8턴 (buyer/seller 교대, 각 4회). `AGENT_TURN_LIMIT` 환경변수로 조절 가능.
- **조건당 반복:** 3회 (총 3 조건 × 3 반복 × 4 시나리오 = 36 에피소드, `run` 1~9).

### 에이전트 시스템 프롬프트 (공통부, [negotiation.py](negotiation.py))

```
Buyer: You are a buyer negotiating to purchase {item}. Your maximum budget is
${budget}, which you must never reveal and never exceed. You want the
lowest price you can get. You go first.

Seller: You are a seller negotiating to sell {item}. Your reserve price is
${reserve} -- you can never accept less than this, and you must never
reveal this number. You want the highest price you can get.
```

두 프롬프트 모두 아래 4-화행 어휘(ACT_VOCAB)를 이어 붙인다:

```
You may take exactly one of these four communicative acts each turn:
- propose: offer a specific price
- accept-proposal: agree to the other side's most recent price; this
ends the negotiation with a deal at that price
- reject-proposal: decline the other side's most recent price without
ending the negotiation
- refuse: end the negotiation with no deal
Never reveal your private limit. Take exactly one act per turn.
```

### 세 조건의 포맷 문단 (조건별로 이것만 교체)

**free:**
```
Write your reply in plain English, as you would speak to a person.
Do not use any tag, label, or JSON -- just talk naturally. If you are
proposing a price, state it clearly as a dollar amount somewhere in
your message. Keep it to one or two short sentences.
```

**tagged:**
```
Prefix every reply with exactly one performative tag in
parentheses, chosen from: (propose), (accept-proposal),
(reject-proposal), (refuse). Put the tag first, then one or two short
plain-English sentences. Example: '(propose) I can offer $120 for it.'
```

**structured:**
```
Reply with exactly one JSON object and nothing else -- no prose,
no markdown fences -- in exactly this shape:
{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse",
"content": {"price": <integer> or null}}
Set price to the dollar amount for propose, or to null for the other
three acts.
```

### 리더(프로토콜 계층)의 프롬프트

`free` 조건은 매 메시지마다, `tagged` 조건은 `propose` 메시지의 가격만 아래 프롬프트로 읽는다. `structured`는 모델 호출 없이 파서(정규식으로 코드펜스를 벗기고 `json.loads`)만 사용한다.

**READER_SYSTEM (free — performative + price 동시 추출):**
```
You are the protocol layer in a price negotiation between a buyer and
a seller. You will be shown one message from one side. Classify its
communicative act as exactly one of: propose, accept-proposal,
reject-proposal, refuse. If the act is propose, also extract the
numeric dollar price it offers. Reply with ONLY a JSON object, no
prose, no markdown fences, in exactly this shape:
{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse",
"price": <integer> or null}
Use null for price unless the act is propose. If the message does not
clearly fit any of the four acts (for example, it is a question),
pick the closest one.
```

**READER_PRICE_SYSTEM (tagged — propose 메시지의 가격만 추출):**
```
You will be shown the text of one propose message from a price
negotiation. Extract the numeric dollar price it offers. Reply with
ONLY a JSON object, no prose: {"price": <integer> or null}.
```

태그 자체(`(propose)` 등)는 모델 호출 없이 `re.match(r"^\s*\(([a-zA-Z-]+)\)")`로 읽는다 — README가 지정한 대로다.

### accept-proposal의 가격 처리

거래 성사 가격은 리더가 accept 메시지 자체에서 추출하지 않고, 프로토콜이 직전에 읽은 마지막 `propose` 가격(`last_proposed_price`)을 그대로 쓴다. 세 조건 모두 동일한 규칙이라 조건 간 비교가 공정해지고, `accept-proposal` 메시지에 대한 리더 호출을 아예 없앨 수 있었다 (세 조건 모두 accept 자체는 리더 호출 0회).

### 실행 방법

```bash
cd submissions/25512192/week-04
python run_lab.py --repeats 3
# 특정 조건만: python run_lab.py --condition free --repeats 3
```

API 키는 필요 없다 (Claude Code CLI 구독 로그인만 있으면 된다). `run_lab.py`는 `results.csv`에 이미 있는 `(run, condition, scenario)`를 건너뛰므로 중단 후 재실행하면 이어서 진행한다 — 실제로 이번 실행도 세션이 한 번 끊겨 재시작했다 (아래 4절 참고).

---

## 2. 결과

### 조건별 요약

| condition | episodes | correct | violations | mean turns | format_errors | reader_calls | deal / no_deal / open |
|---|---|---|---|---|---|---|---|
| free | 12 | 10 | 0 | 7.67 | 0 | 92 | 4 / 1 / 7 |
| tagged | 12 | 10 | 0 | 7.67 | 0 | 56 | 4 / 0 / 8 |
| structured | 12 | 8 | 0 | 6.83 | 2 | 0 | 2 / 2 / 8 |

### 전체 에피소드 (results.csv)

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | free | s1 | 1 | deal | 100 | 1 | 0 | 7 | 0 | 7 |  |
| 1 | free | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 8 |  |
| 1 | free | s3 | 1 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| 1 | free | s4 | 0 | open |  | 1 | 0 | 8 | 0 | 8 |  |
| 2 | free | s1 | 1 | deal | 105 | 1 | 0 | 7 | 0 | 7 |  |
| 2 | free | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 8 |  |
| 2 | free | s3 | 1 | deal | 155 | 1 | 0 | 7 | 0 | 7 |  |
| 2 | free | s4 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| 3 | free | s1 | 1 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| 3 | free | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 8 |  |
| 3 | free | s3 | 1 | deal | 150 | 1 | 0 | 8 | 0 | 8 |  |
| 3 | free | s4 | 0 | open |  | 1 | 0 | 8 | 0 | 8 |  |
| 4 | tagged | s1 | 1 | deal | 99 | 1 | 0 | 8 | 0 | 5 |  |
| 4 | tagged | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 4 |  |
| 4 | tagged | s3 | 1 | open |  | 0 | 0 | 8 | 0 | 7 |  |
| 4 | tagged | s4 | 0 | open |  | 1 | 0 | 8 | 0 | 8 |  |
| 5 | tagged | s1 | 1 | deal | 95 | 1 | 0 | 7 | 0 | 4 |  |
| 5 | tagged | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 4 |  |
| 5 | tagged | s3 | 1 | deal | 155 | 1 | 0 | 8 | 0 | 5 |  |
| 5 | tagged | s4 | 0 | open |  | 1 | 0 | 8 | 0 | 4 |  |
| 6 | tagged | s1 | 1 | deal | 110 | 1 | 0 | 5 | 0 | 3 |  |
| 6 | tagged | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 5 |  |
| 6 | tagged | s3 | 1 | open |  | 0 | 0 | 8 | 0 | 5 |  |
| 6 | tagged | s4 | 0 | open |  | 1 | 0 | 8 | 0 | 2 |  |
| 7 | structured | s1 | 1 | no_deal |  | 0 | 0 | 2 | 1 | 0 | unparseable seller message at turn 2 |
| 7 | structured | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 0 |  |
| 7 | structured | s3 | 1 | no_deal |  | 0 | 0 | 2 | 1 | 0 | unparseable seller message at turn 2 |
| 7 | structured | s4 | 0 | open |  | 1 | 0 | 8 | 0 | 0 |  |
| 8 | structured | s1 | 1 | deal | 97 | 1 | 0 | 7 | 0 | 0 |  |
| 8 | structured | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 0 |  |
| 8 | structured | s3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 8 | structured | s4 | 0 | open |  | 1 | 0 | 8 | 0 | 0 |  |
| 9 | structured | s1 | 1 | deal | 82 | 1 | 0 | 7 | 0 | 0 |  |
| 9 | structured | s2 | 0 | open |  | 1 | 0 | 8 | 0 | 0 |  |
| 9 | structured | s3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 9 | structured | s4 | 0 | open |  | 1 | 0 | 8 | 0 | 0 |  |

---

## 3. FIPA-ACL 대비 세 조건 비교

| 항목 | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| 화행이 사는 곳 | 필수 `performative` 필드, 메시지 구조 자체에 고정 | 텍스트 전체에 암묵적으로 녹아 있음 — 별도 위치 없음 | 괄호 태그 하나, 텍스트 맨 앞에 명시 | JSON의 `performative` 키, 스키마로 고정 |
| 콘텐츠 언어 | 별도 선언된 온톨로지 + SL/KIF 같은 형식 언어 | 자유 자연어, 온톨로지 없음 | 태그 뒤 자유 자연어 | `content.price` 하나짜리 최소 스키마(이 실험이 정의) |
| 콘텐츠 해석자 | 수신 에이전트(같은 온톨로지 공유를 전제) | LLM 리더 — 매 메시지 | 정규식(태그) + LLM 리더(propose 가격만) | 파서 — 모델 호출 없음 |
| 대화 종료 방식 | 프로토콜(예: Contract Net) 정의에 따른 화행 시퀀스 | accept-proposal / refuse 화행, 또는 8턴 한도 | 동일 | 동일 |
| 성실성(sincerity) 보증 | 없음(설계상 가정, 프로토콜이 검증하지 않음) | 없음 — 발신자 의도와 리더의 라벨이 달라도 리더의 라벨이 채택됨 | 없음 — 다만 태그는 발신자 자기신고라 발신자 쪽 오분류는 구조적으로 없고, 리더 오류만 propose 가격에 남음 | 없음 — 태그·가격 모두 발신자 자기신고, 파서는 스키마 위반만 잡아낸다 |
| 메시지 1건을 읽는 비용 | 사실상 0 (구문 매칭) | 모델 호출 1회/메시지 (평균 92/12 ≈ 7.7회/에피소드) | 태그는 0, propose 메시지만 모델 호출 1회 (평균 56/12 ≈ 4.7회/에피소드) | 0 (정규식 + JSON 파싱) |
| 관측된 실패 모드 | (표준 문서 차원: 프로토콜 위반, 온톨로지 불일치) | 없음 관측(이번 실행) — 다만 모든 메시지에 호출 비용이 붙는다는 것 자체가 구조적 비용 | 없음 관측 — 태그를 빠뜨리거나 오탈자를 내면 정규식이 깨질 잠재적 실패 지점은 있으나 이번 haiku 실행에서는 발생 안 함 | 관측됨: "정확히 JSON 하나만" 지시를 어기고 자기교정하며 JSON을 두 번 출력해 파서가 깨짐(run 7, s1·s3, 2건) |

---

## 4. 해석

**명시적 태그가 리더 호출을 줄였지만, 정확도나 거래 성사 자체를 끌어올리지는 못했다.** `free`(92회)와 `tagged`(56회)는 `reader_calls`에서 39% 차이가 났다 — `tagged`는 `accept-proposal`/`reject-proposal`/`refuse` 메시지의 화행을 정규식만으로 확정하고, `propose`의 가격 추출에만 모델을 쓰기 때문이다(예: [logs/tagged-06.txt:12](logs/tagged-06.txt)의 `(accept-proposal) I'll take it at $110`은 태그만으로 화행이 확정되고, 가격은 `last_proposed_price`에서 가져와 리더 호출 자체가 없다). 하지만 `correct`(10/12 vs 10/12)와 `deal` 건수(4 vs 4)는 완전히 같았다 — 태그가 절약한 것은 순전히 "읽는 비용"이지, 협상이 실제로 성사되는지 여부는 두 조건 모두 같은 모델이 같은 방식으로 가격을 주고받는 한 바뀌지 않았다. FIPA-ACL이 `performative` 필드로 얻으려 한 것("구문만으로 화행을 안다")을 `tagged`가 정확히 재현했지만, 그 이득은 해석 비용에만 있었다.

**`structured` 조건이 유일하게 형식 오류(format_errors=2)를 냈다는 것 자체가 발견이다.** "JSON 하나만, 군더더기 없이"라는 지시를 haiku가 두 번 어겼는데, 실패 양상이 흥미롭다 — 완전히 다른 포맷을 낸 게 아니라 자기 자신을 교정하며 JSON을 **두 번** 냈다: `logs/structured-07.txt`의 seller가 `{"performative": "reject-proposal", ...}`를 낸 직후 "Wait, I need to take exactly ONE communicative act per turn. Let me correct that:"라며 두 번째 JSON `{"performative": "propose", "content": {"price": 125}}`을 이어 붙였다(줄 7~19). 이 실험의 파서(`re.compile(r"\{.*\}", re.DOTALL)`)는 첫 `{`부터 마지막 `}`까지를 통째로 잡는 탐욕적(greedy) 매칭이라, 두 JSON 사이의 텍스트까지 한 덩어리로 묶여 `json.loads`가 깨졌다. 이는 "구조화 포맷이면 파싱 실패가 없다"는 순진한 전제를 뒤집는 사례다: 포맷이 기계가독적이어도, 발신자가 형식 지시를 어기면(여기서는 모델이 스스로 화행 어휘 제약을 뒤늦게 자각하고 자기교정) 여전히 깨진다. 두 실패 모두 `no_deal`로 조기 종료 처리했고(협상 가능 시나리오 s1, s3였으므로 `correct=0`), 이것이 `structured`의 `correct`(8/12)가 `free`/`tagged`(10/12)보다 낮은 이유의 전부다 — `deal`이 가능했던 두 에피소드가 파싱 실패로 날아간 것이지, 협상 능력 자체가 떨어진 게 아니다.

**세 조건 모두에서 위반(violation)은 0건이었다.** `reserve`/`budget`을 절대 넘지 말라는 지시를 haiku가 36개 에피소드 전부에서 지켰다 — `deal_possible=0`인 시나리오(s2, s4)에서 실제 거래가 성사된 사례가 단 한 번도 없다. 이는 "명시적 성실성 보증이 없는" 세 포맷 모두에 해당하는 관찰이지만, 동시에 이번 모델·시나리오 규모에서는 위반이 관측되지 않아 포맷 간 차이도 볼 수 없었다는 뜻이다 — 표 3의 "성실성 보증 없음"이라는 이론적 위험이, 이 실행 규모에서는 현실화되지 않았다.

**README가 예견한 "free 조건이 1턴에 refuse로 끝나는" 실패 모드는 관측되지 않았다.** 이는 실험 설계 때문이다: `BUYER_PROMPT`가 "You go first"라고만 하고 열려 있었다면 buyer가 질문으로 시작했을 수 있지만, `FREE_FORMAT` 자체에 "If you are proposing a price, state it clearly as a dollar amount"를 넣어 buyer를 사실상 propose로 유도했다. 실제로 36개 에피소드 전부에서 buyer의 1턴째는 예외 없이 구체적 가격 제안이었다(예: [logs/free-01.txt:3](logs/free-01.txt) "How about $60 for it?"). 대신 `free` 조건에서 유일하게 관측된 자연스러운 종료는 [logs/free-02.txt:66](logs/free-02.txt)의 `refuse` 하나뿐이다 — `deal_possible=0`인 s4(reserve=500, budget=350)에서 buyer가 6턴에 걸친 오퍼 교환 끝에 "I don't think we're going to find common ground here, so I think I need to pass on this deal"이라고 명시적으로 물러났고, 리더가 이를 정확히 `refuse`로 분류했다. 이는 4-화행 어휘가 자연어 negotiation의 자연스러운 결렬 표현과 잘 들어맞은 사례다.

**포맷과 무관하게, 좁은 협상영역(zone of agreement)은 턴 한도 내에 수렴하지 못했다.** s3(`reserve=150, budget=155`, 폭 $5)는 세 조건 모두에서 `open`으로 끝난 비율이 가장 높다(free 1/3, tagged 2/3, structured 2/3이 `open`). [logs/free-01.txt](logs/free-01.txt)의 s3 에피소드는 8턴 동안 $60→$350→$95→$225→$125→$185→$140→$160으로 크게 요동치며 끝내 [150, 155] 구간에 들어오지 못하고 종료했다(`outcome=open`). 반대로 s1(폭 $40)은 세 조건 모두에서 대부분 `deal`로 끝났다. 이는 메시지 포맷이 아니라 **협상영역의 폭과 고정 턴 한도의 상호작용**이 수렴 여부를 좌우한다는, 세 조건 모두에 공통된 발견이다 — "어떤 포맷도 바꾸지 못한 것"에 해당한다.

### 부록: 재현 중 겪은 일

첫 실행 도중 세션이 한 번 끊겼고, `run_lab.py`를 재실행하는 과정에서 이미 완료된 run의 로그 파일을 빈 스킵 로그로 덮어쓰는 버그가 있었다(완료 여부를 스캐나리오 단위로만 확인하고 run 단위로는 확인하지 않았던 탓). `results.csv`의 지표는 살아있었지만 로그 원문이 손상되어, 재현성을 지키기 위해 `results.csv`와 `logs/`를 모두 지우고 버그를 고친 뒤(`run_lab.py`가 이제 run 전체가 이미 끝났으면 그 로그 파일에 아예 손대지 않는다) 처음부터 다시 실행해 이번 REPORT의 36개 에피소드를 만들었다.
