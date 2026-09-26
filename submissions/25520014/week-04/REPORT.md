# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

## 1. Setup

| | |
|---|---|
| provider | Anthropic subscription via `claude -p` |
| tool | Claude Code CLI 2.1.278 |
| model | `claude-haiku-4-5-20251001` |
| temperature | **직접 설정 불가, 내부 값 미확인** |
| scenarios | `scenarios.json`, 4개, deal_possible 2:2, 실행 전 커밋 (`b50c387`) |
| turn limit | 메시지 8개, 초과 시 `open` |
| runs / episodes | 9 / 36 |

`claude -p`는 호출마다 새 세션이므로, 매 턴 지금까지의 transcript를 프롬프트 문자열로
펼쳐 넘긴다(`model.py`). 역할 문단과 공통 문단은 세 조건에서 글자 단위로 같고, 읽기 성공
판정도 세 조건에서 같은 규칙이다(`acl.py`, `protocol.py`).

**형식 문단** (`FORMAT`). 조건 사이에서 바뀌는 것은 이 문단뿐이다.

| condition | 형식 문단 |
|---|---|
| `free` | Write your message as one or two plain English sentences. |
| `tagged` | Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence. |
| `structured` | Reply with exactly one JSON object and nothing else: `{"performative": "propose" \| "accept-proposal" \| "reject-proposal" \| "refuse", "content": {"price": <whole number or null>}}` |

**reader 프롬프트** (`READER_SYSTEM`). `free`와 `tagged`의 프로토콜 계층이 부른다. reader는
비공개 한도를 모른다.

> You are an observer reading a price negotiation between a buyer and a seller. Label the LAST
> message only. Reply with exactly one JSON object and nothing else: `{"performative": "propose"
> | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}`. propose
> means the message offers a price. accept-proposal means the message agrees to the price the
> other side named last. reject-proposal means the message declines and the negotiation
> continues. refuse means the message leaves the negotiation for good. price is the number of
> currency units the LAST message itself names as its own offer, or null if the last message
> names no such number. Do not explain. Do not add any other key.

**실행 방법.**

```bash
export AGENT_MODEL=claude-haiku-4-5-20251001
python negotiate.py --all --jobs 9        # 9런 병렬, 약 6분
python ../../../scripts/check_week04.py .
```

run끼리는 독립이라 병렬로 돌아간다. `results.csv`에 이미 있는 `(run, scenario)` 쌍은
건너뛰므로 중단된 실행은 같은 명령으로 이어 돌린다.

## 2. Results

| condition | correct / 12 | deal | no_deal | open | violation | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|---|
| `free` | 8 | 6 | 2 | 4 | 0 | 6.8 | 1 | 81 |
| `tagged` | 5 | 6 | 3 | 3 | 4 | 6.7 | 0 | 33 |
| `structured` | 4 | 6 | 0 | 6 | 2 | 7.3 | 0 | 0 |

`correct`의 분모는 조건마다 12 (시나리오 4 × 리핏 3)다. 거래 불가능 시나리오에서는
`no_deal`만 정답으로 셌고 `open`은 오답이다(lab: "아니면 결렬이 정답이다"). `open`도
정답으로 치면 `free` 12, `tagged` 8, `structured` 10이 된다.

`violation`은 프로토콜 계층이 **기록한** 거래가가 한도 밖인 경우다. 에이전트가 문장으로
합의한 가격을 기준으로 하면 36 에피소드 전부 두 한도 안이었다(4부 표).

`structured` 메시지 88개는 전부 코드펜스에 싸여 왔고, 파서가 벗겨서 읽으므로
`format_errors`에는 0으로 잡힌다.

**에피소드별 결과.** 굵은 가격이 위반이다. 에피소드별 `turns`, `format_errors`, `reader_calls`는 `results.csv`에 있다.

| sc | reserve / budget | free (run 1 / 2 / 3) | tagged (run 1 / 2 / 3) | structured (run 1 / 2 / 3) |
|---|---|---|---|---|
| 1 | 120 / 150 | deal 125 / deal 135 / deal 125 | deal **160** / deal 135 / deal 120 | deal 120 / deal 120 / deal 135 |
| 2 | 40 / 40 | deal 40 / deal 40 / deal 40 | deal **32** / deal **15** / deal **55** | deal 40 / deal **25** / deal **20** |
| 3 | 90 / 70 | open / open / open | open / no_deal / no_deal | open / open / open |
| 4 | 200 / 150 | open / no_deal / no_deal | open / no_deal / open | open / open / open |

## 3. FIPA-ACL compared with the three conditions

| | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| illocutionary force가 어디에 있는가 | 메시지의 필수 `performative` 파라미터 | 아무 데도 없다. 수신 후 관찰자가 붙인다 | 메시지 맨 앞 괄호 태그 | JSON의 `performative` 필드 |
| content 언어 | 선언된 ontology를 가진 형식 언어 (SL 등) | 영어 산문 | 영어 산문 | `{"price": <정수 또는 null>}` 한 필드 |
| content를 누가 해석하는가 | 수신 에이전트가 공유 ontology로 | 하네스의 reader 모델 (메시지마다 1회) | `propose`일 때만 reader 모델 | 파서. 모델 호출 없음 |
| 대화가 어떻게 끝나는가 | interaction protocol이 종료 상태를 정의 | `accept-proposal`/`refuse` 라벨, 또는 8턴 | 같음 | 같음. 다만 `refuse`가 0건이라 실질적으로 8턴뿐 |
| sincerity를 보장하는 것 | 아무것도 보장하지 않는다. FP/RE는 발신자 내부의 믿음에 대한 규범이고 전달되는 것은 문자열뿐 | 같음. 게다가 force 자체가 발신자의 것도 아니다 | 같음. 태그와 문장이 어긋나도 아무도 막지 않는다 | 같음. `performative`와 `content.price`가 어긋나도 막지 않는다 |
| 메시지 하나를 읽는 비용 | 파싱 비용 (형식 언어) | 풀 LLM 호출 1회 (81 / 81 메시지) | 풀 LLM 호출 0.41회 (33 / 80) | 0 (0 / 88) |
| 실패하는 방식 | ontology 불일치, 프로토콜 상태 불일치 | reader의 라벨 오류 (1건) | 역제안 가격이 `(reject-proposal)` 안에 실려 와 유실 (37건) | 같은 유실 (35건). 형식 위반이 `format_errors`에 안 잡힘 (펜스 88건) |

## 4. 해석

*(한 문단. 아직 안 씀.)*

증거로 쓸 것:

**프로토콜 계층이 읽어 낸 performative의 분포.**

| condition | propose | reject-proposal | accept-proposal | refuse |
|---|---|---|---|---|
| `free` | 73 | 0 | 6 | 2 |
| `tagged` | 33 | 37 | 7 | 3 |
| `structured` | 47 | 35 | 6 | 0 |

**위반 6건. 전부 프로토콜 계층이 만들었고, 에이전트가 한도를 어긴 경우는 0건이다.**

| run | sc | 합의가 | 기록된 거래가 | 한도 | 기록가가 나온 자리 |
|---|---|---|---|---|---|
| `tagged-1` | 1 | 135 | **160** | 120–150 | seller의 첫 `(propose)` |
| `tagged-1` | 2 | 40 | **32** | 40–40 | buyer의 마지막 `(propose)` |
| `tagged-2` | 2 | 40 | **15** | 40–40 | buyer의 여는 `(propose)` |
| `tagged-3` | 2 | 40 | **55** | 40–40 | seller의 유일한 `(propose)` |
| `structured-2` | 2 | 40 | **25** | 40–40 | buyer의 여는 `propose` |
| `structured-3` | 2 | 40 | **20** | 40–40 | buyer의 여는 `propose` |

**`logs/tagged-2.txt` 시나리오 2.** 15 → 55 → 28 → 45 → 38 → 42 → 40 → 수락으로 흘렀고
`(propose)` 태그가 붙은 것은 첫 줄뿐이다.

```
[buyer]  (propose) I'd like to offer 15 for this textbook.
  [read] {'performative': 'propose', 'price': 15}
[buyer]  (reject-proposal) would you accept $40, which is genuinely my maximum limit?
  [read] {'performative': 'reject-proposal', 'price': None}
[seller] (accept-proposal) You've got a deal at $40—that works for me ...
[result] outcome=deal price=15 correct=0 violation=1
```

**`logs/structured-3.txt` 시나리오 2.** 정답 40이 JSON 안에 두 번 있었으나 둘 다 안 쓰였다.

```
  [read] {'performative': 'propose', 'price': 20}          <- 기록된 거래가
  [read] {'performative': 'reject-proposal', 'price': 40}   <- 버려짐. 거절은 제안이 아니다
  [read] {'performative': 'accept-proposal', 'price': 40}   <- 무시됨. 성사가는 last_price[other]
```

**설계의 한계.** 위반 6건은 코드가 설계와 다르게 동작해서 생긴 것이 아니다. 설계에 빈
자리가 있었다.

- 공통 문단의 `reject-proposal` 정의("decline the last price and keep negotiating")는
  거절하면서 새 가격을 말하는 것을 막지 않는다. 역제안은 `propose`로 하라는 문장이 없다.
- 프로토콜은 `reject-proposal`의 가격을 버린다. 거절은 제안이 아니라는 FIPA 의미론을
  따른 것이고(`protocol.py`), 받도록 바꾸면 `tagged`와 `structured`의 숫자가 달라진다.
- FIPA의 `accept-proposal`은 수락하는 제안을 content에 담아 가리키지만, 이 실습의
  `accept-proposal`은 아무것도 가리키지 않는다. 그래서 수락 메시지의 가격(`structured-3`의
  40)과 기록된 가격(20)이 달라도 알아챌 장치가 없다.
