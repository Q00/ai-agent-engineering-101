# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

## 1. Setup

| | |
|---|---|
| provider | Anthropic subscription via `claude -p` |
| tool | Claude Code CLI 2.1.278 |
| model | `claude-haiku-4-5-20251001` |
| temperature | **직접 설정 불가, 내부 값 미확인** |
| scenarios | `scenarios.json`, 4개, deal_possible 2:2, 실행 전 커밋 (`b50c387`) |
| turn limit | 메시지 8개 (`MAX_TURNS = 8`), 초과 시 `open` |
| runs | 9, 조건마다 3 |
| episodes | 36 |

**대화 기록을 넘기는 방식.** `claude -p`는 호출마다 새 세션이라 다중 턴 대화를 그대로
넘길 수 없다. 두 길 가운데 (A)를 골랐다.

- **(A) 채택** — 지금까지의 transcript를 프롬프트 문자열로 펼쳐 매 턴 통째로 넘긴다.
  모델이 그 턴에 무엇을 봤는지가 프롬프트 문자열 그대로 남아 재현을 확인할 수 있다.
- (B) 기각 — `--resume <session_id>`로 에이전트마다 세션 유지. 기록이 `~/.claude` 세션
  파일 안에 있어 로그만으로 재현을 확인할 수 없고, 중단된 실행을 이어 돌릴 때 세션
  상태가 꼬인다.

(A)의 대가를 적어 둔다. 자기 발화가 assistant 턴이 아니라 user 프롬프트 안의 인용문으로
들어간다. 모델은 "내가 한 말"과 "남이 한 말"을 턴 구조가 아니라 `You:` / `The other
party:` 라벨로 구분한다.

**역할 문단** (`acl.py`의 `ROLE`). `{item}`과 `{limit}`만 시나리오에서 채운다.

> You are the buyer of {item}. You are negotiating the price with the seller. Your private
> limit: you can pay at most {limit}. Never agree to a price above {limit}. Keep your limit to
> yourself. Try to settle on a price you are happy with, or walk away if you cannot.

seller 문단은 같은 문장에서 `buyer`/`seller`, `pay at most`/`accept at least`,
`above`/`below`만 바꾼 것이다.

**공통 문단** (`COMMON`). 세 조건에서 같다.

> Four acts are available: propose (offer a price), accept-proposal (agree to the other side's
> last price, which ends the negotiation with a deal), reject-proposal (decline the last price
> and keep negotiating), refuse (leave the negotiation for good, no deal). Every message you
> send performs exactly one of these four acts. Send one message per turn and nothing else.

턴 한도(8)는 프롬프트에 넣지 않았다. 한도는 하네스가 강제하는 바깥쪽 규칙이고, 프롬프트에
넣으면 "곧 끝난다"를 아는 데서 오는 막판 양보가 세 조건에 섞여 `open` 집계를 흐린다.

**형식 문단** (`FORMAT`). 조건 사이에서 바뀌는 것은 이 문단뿐이다. 세 프롬프트가 이 문단
앞까지 글자 단위로 같음을 `acl.py` 조립 후에 확인했다.

| condition | 형식 문단 |
|---|---|
| `free` | Write your message as one or two plain English sentences. |
| `tagged` | Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence. |
| `structured` | Reply with exactly one JSON object and nothing else: `{"performative": "propose" \| "accept-proposal" \| "reject-proposal" \| "refuse", "content": {"price": <whole number or null>}}` |

**reader 프롬프트** (`READER_SYSTEM`). `free`와 `tagged`의 프로토콜 계층이 부른다.
reader는 하네스의 일부이므로 비공개 한도를 모른다. 한도를 알면 "이 가격은 한도 밖이니
거절이겠지" 같은 추론이 섞여 라벨이 측정이 아니라 판단이 된다.

> You are an observer reading a price negotiation between a buyer and a seller. Label the LAST
> message only. Reply with exactly one JSON object and nothing else: `{"performative": "propose"
> | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}`. propose
> means the message offers a price. accept-proposal means the message agrees to the price the
> other side named last. reject-proposal means the message declines and the negotiation
> continues. refuse means the message leaves the negotiation for good. price is the number of
> currency units the LAST message itself names as its own offer, or null if the last message
> names no such number. Do not explain. Do not add any other key.

**읽기 성공 판정.** 세 조건에서 같은 규칙을 쓴다. 조건마다 기준이 다르면 `format_errors`를
조건 간에 비교할 수 없다.

> `ok` == performative가 네 행위 중 하나이고, `propose`인 경우 가격이 정수다.

`propose`인데 가격이 없으면 실패로 센다. 행위는 알아냈지만 그 propose로는 상대의
`last_price`를 갱신할 수 없어 프로토콜이 진행되지 않기 때문이다. 읽지 못한 메시지도
원문 그대로 상대에게 간다.

**실행 방법.**

```bash
export AGENT_MODEL=claude-haiku-4-5-20251001
python negotiate.py --all --jobs 9        # 9런 병렬, 약 6분
python negotiate.py structured 1          # run 하나만
python ../../../scripts/check_week04.py .
```

에피소드 안에서는 턴을 주고받아야 하므로 호출이 직렬이지만 run끼리는 독립이다. 로그
파일이 다르고 `results.csv` append는 `flock`으로 보호되므로 9런을 동시에 돌려도 된다.
`results.csv`에 이미 있는 `(run, scenario)` 쌍은 건너뛰므로 중단된 실행은 같은 명령으로
이어 돌린다.

## 2. Results

| condition | correct / 12 | deal | no_deal | open | violation | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|---|
| `free` | 8 | 6 | 2 | 4 | 0 | 6.8 | 1 | 81 |
| `tagged` | 5 | 6 | 3 | 3 | 4 | 6.7 | 0 | 33 |
| `structured` | 4 | 6 | 0 | 6 | 2 | 7.3 | 0 | 0 |

`correct`의 분모는 조건마다 12 (시나리오 4 × 리핏 3)다.

**프로토콜 계층이 읽어 낸 performative의 분포.** 아래 표가 조건 간 차이의 대부분을
설명한다.

| condition | propose | reject-proposal | accept-proposal | refuse | 읽은 메시지 |
|---|---|---|---|---|---|
| `free` | 73 | 0 | 6 | 2 | 81 |
| `tagged` | 33 | 37 | 7 | 3 | 80 |
| `structured` | 47 | 35 | 6 | 0 | 88 |

`free`의 reader는 81개 메시지 가운데 `reject-proposal`을 **한 번도** 쓰지 않았다. 같은
협상에서 `tagged`의 에이전트는 37번, `structured`의 에이전트는 35번 그 태그를 골랐다.
그리고 `structured`의 에이전트는 `refuse`를 **한 번도** 보내지 않아, 거래 불가능
시나리오 6건이 전부 턴 한도까지 갔다(`no_deal` 0건).

**비용.** 토큰에는 `claude -p`의 고정 오버헤드(호출당 약 26,000 토큰)가 포함되어 있다.
reader 호출도 같은 오버헤드를 문다.

| condition | agent calls | reader calls | agent tokens | reader tokens | reader 비중 |
|---|---|---|---|---|---|
| `free` | 81 | 81 | 2,083,015 | 2,083,038 | 50% |
| `tagged` | 80 | 33 | 2,060,792 | 841,244 | 29% |
| `structured` | 88 | 0 | 2,263,498 | 0 | 0% |

**형식 준수와 정규화.**

| | 값 | |
|---|---|---|
| `structured` 메시지 중 코드펜스로 감싸 온 것 | **88 / 88 (100%)** | 파서가 벗겨서 읽고 `flags=fence`로 셈 |
| `structured` JSON 뒤에 문장이 붙은 것 | 0 / 88 | |
| `format_errors` 전체 | 1 | `free-2`, 아래 참조 |
| 수락할 상대 가격이 없어 성사되지 못한 `accept-proposal` | 1 | `tagged-1` 시나리오 2 |

`structured` 메시지 88개가 전부 ` ```json ` 펜스에 싸여 왔다. 형식 문단은 "exactly one
JSON object and nothing else"라고 지시했으므로 엄밀히는 88건 전부가 형식 위반이다.
파서가 펜스를 벗기고 읽기 때문에 `format_errors` 열에는 0으로 잡힌다. **`format_errors`가
0이라는 것이 형식을 지켰다는 뜻이 아니다.**

유일한 `format_errors` 1건은 `free-2`의 첫 메시지다.

```
[buyer] I'm interested in your road bike - what condition is it in, and what's your asking price?
  [read] {'performative': 'propose', 'price': None}  # propose without a whole-number price
```

네 행위에는 질문에 해당하는 것이 없다. FIPA에는 `query-ref`와 `cfp`가 있지만 이 실습의
어휘에는 없고, reader는 넷 중 하나를 골라야 하므로 가격 없는 `propose`를 냈다. 참조 실행이
보고한 첫 턴 붕괴(질문을 `refuse`로 읽어 1턴에 종료)와 같은 자리에서 난 실패지만, 이 실행
에서는 81개 메시지 가운데 1건에 그쳤다. 공통 문단의 "Every message you send performs
exactly one of these four acts"가 질문으로 여는 행동을 줄인 것으로 보인다.

**에피소드별 결과.** `results.csv`와 같은 내용이다.

| run | condition | sc | possible | outcome | price | correct | violation | turns | fmt err | reader calls |
|---|---|---|---|---|---|---|---|---|---|---|
| `free-1` | `free` | 1 | 1 | `deal` | 125 | 1 | 0 | 4 | 0 | 4 |
| `free-2` | `free` | 1 | 1 | `deal` | 135 | 1 | 0 | 5 | 1 | 5 |
| `free-3` | `free` | 1 | 1 | `deal` | 125 | 1 | 0 | 4 | 0 | 4 |
| `free-1` | `free` | 2 | 1 | `deal` | 40 | 1 | 0 | 7 | 0 | 7 |
| `free-2` | `free` | 2 | 1 | `deal` | 40 | 1 | 0 | 8 | 0 | 8 |
| `free-3` | `free` | 2 | 1 | `deal` | 40 | 1 | 0 | 6 | 0 | 6 |
| `free-1` | `free` | 3 | 0 | `open` | — | 0 | 0 | 8 | 0 | 8 |
| `free-2` | `free` | 3 | 0 | `open` | — | 0 | 0 | 8 | 0 | 8 |
| `free-3` | `free` | 3 | 0 | `open` | — | 0 | 0 | 8 | 0 | 8 |
| `free-1` | `free` | 4 | 0 | `open` | — | 0 | 0 | 8 | 0 | 8 |
| `free-2` | `free` | 4 | 0 | `no_deal` | — | 1 | 0 | 8 | 0 | 8 |
| `free-3` | `free` | 4 | 0 | `no_deal` | — | 1 | 0 | 7 | 0 | 7 |
| `tagged-1` | `tagged` | 1 | 1 | `deal` | 160 | 0 | **1** | 5 | 0 | 2 |
| `tagged-2` | `tagged` | 1 | 1 | `deal` | 135 | 1 | 0 | 4 | 0 | 3 |
| `tagged-3` | `tagged` | 1 | 1 | `deal` | 120 | 1 | 0 | 4 | 0 | 3 |
| `tagged-1` | `tagged` | 2 | 1 | `deal` | 32 | 0 | **1** | 6 | 0 | 2 |
| `tagged-2` | `tagged` | 2 | 1 | `deal` | 15 | 0 | **1** | 8 | 0 | 1 |
| `tagged-3` | `tagged` | 2 | 1 | `deal` | 55 | 0 | **1** | 7 | 0 | 2 |
| `tagged-1` | `tagged` | 3 | 0 | `open` | — | 0 | 0 | 8 | 0 | 3 |
| `tagged-2` | `tagged` | 3 | 0 | `no_deal` | — | 1 | 0 | 8 | 0 | 4 |
| `tagged-3` | `tagged` | 3 | 0 | `no_deal` | — | 1 | 0 | 7 | 0 | 2 |
| `tagged-1` | `tagged` | 4 | 0 | `open` | — | 0 | 0 | 8 | 0 | 5 |
| `tagged-2` | `tagged` | 4 | 0 | `no_deal` | — | 1 | 0 | 7 | 0 | 4 |
| `tagged-3` | `tagged` | 4 | 0 | `open` | — | 0 | 0 | 8 | 0 | 2 |
| `structured-1` | `structured` | 1 | 1 | `deal` | 120 | 1 | 0 | 5 | 0 | 0 |
| `structured-2` | `structured` | 1 | 1 | `deal` | 120 | 1 | 0 | 6 | 0 | 0 |
| `structured-3` | `structured` | 1 | 1 | `deal` | 135 | 1 | 0 | 5 | 0 | 0 |
| `structured-1` | `structured` | 2 | 1 | `deal` | 40 | 1 | 0 | 8 | 0 | 0 |
| `structured-2` | `structured` | 2 | 1 | `deal` | 25 | 0 | **1** | 8 | 0 | 0 |
| `structured-3` | `structured` | 2 | 1 | `deal` | 20 | 0 | **1** | 8 | 0 | 0 |
| `structured-1` | `structured` | 3 | 0 | `open` | — | 0 | 0 | 8 | 0 | 0 |
| `structured-2` | `structured` | 3 | 0 | `open` | — | 0 | 0 | 8 | 0 | 0 |
| `structured-3` | `structured` | 3 | 0 | `open` | — | 0 | 0 | 8 | 0 | 0 |
| `structured-1` | `structured` | 4 | 0 | `open` | — | 0 | 0 | 8 | 0 | 0 |
| `structured-2` | `structured` | 4 | 0 | `open` | — | 0 | 0 | 8 | 0 | 0 |
| `structured-3` | `structured` | 4 | 0 | `open` | — | 0 | 0 | 8 | 0 | 0 |

## 3. FIPA-ACL compared with the three conditions

| | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| illocutionary force가 어디에 있는가 | 메시지의 필수 `performative` 파라미터 | 아무 데도 없다. 수신 후 관찰자가 붙인다 | 메시지 맨 앞 괄호 태그 | JSON의 `performative` 필드 |
| content 언어 | 선언된 ontology를 가진 형식 언어 (SL 등) | 영어 산문 | 영어 산문 | `{"price": <정수 또는 null>}` 한 필드 |
| content를 누가 해석하는가 | 수신 에이전트가 공유 ontology로 | 하네스의 reader 모델 (메시지마다 1회) | `propose`일 때만 reader 모델 | 파서. 모델 호출 없음 |
| 대화가 어떻게 끝나는가 | interaction protocol이 종료 상태를 정의 | `accept-proposal`/`refuse` 라벨, 또는 8턴 | 같음 | 같음. 다만 `refuse`가 0건이라 실질적으로 8턴뿐 |
| sincerity를 보장하는 것 | 아무것도 보장하지 않는다. FP/RE는 발신자 내부의 믿음에 대한 규범이고 전달되는 것은 문자열뿐 | 같음. 게다가 force 자체가 발신자의 것도 아니다 | 같음. 태그와 문장이 어긋나도 아무도 막지 않는다 | 같음. `performative`와 `content.price`가 어긋나도 막지 않는다 |
| 메시지 하나를 읽는 비용 | 파싱 비용 (형식 언어) | 풀 LLM 호출 1회. 81 / 81 메시지 | 풀 LLM 호출 0.41회. 33 / 80 메시지 | 0. 0 / 88 메시지 |
| 실패하는 방식 | ontology 불일치, 프로토콜 상태 불일치 | reader의 라벨 오류. 이 실행에서는 어휘에 없는 질문 1건 | 역제안 가격이 `(reject-proposal)` 안에 실려 와 유실 (37건) | 역제안 가격이 `reject-proposal`의 `content.price`에 실려 와 유실 (35건). 형식 위반이 `format_errors`에 안 잡힘 (펜스 88건) |

## 4. 해석

*(직접 쓸 부분. 아래는 로그에서 확인된 사실만 모아 둔 것이다.)*

**위반 6건은 전부 프로토콜 계층이 만들었다. 에이전트가 자기 한도를 어긴 경우는 0건이다.**

| run | sc | 에이전트가 합의한 가격 | 기록된 거래가 | 한도 | 기록가가 나온 자리 |
|---|---|---|---|---|---|
| `tagged-1` | 1 | 135 | **160** | 120–150 | seller의 첫 `(propose)` |
| `tagged-1` | 2 | 40 | **32** | 40–40 | buyer의 마지막 `(propose)` |
| `tagged-2` | 2 | 40 | **15** | 40–40 | buyer의 여는 `(propose)` |
| `tagged-3` | 2 | 40 | **55** | 40–40 | seller의 유일한 `(propose)` |
| `structured-2` | 2 | 40 | **25** | 40–40 | buyer의 여는 `propose` |
| `structured-3` | 2 | 40 | **20** | 40–40 | buyer의 여는 `propose` |

여섯 건 모두 합의가가 두 한도 안에 있었다. 경계 시나리오(reserve = budget = 40)에서는
여섯 건 중 다섯이 나왔고, 합의가는 매번 정확히 40이었다.

**증거 1 — `tagged-2` 시나리오 2 (`logs/tagged-2.txt`).** 협상 전체가
15 → 55 → 28 → 45 → 38 → 42 → 40 → accept로 흘렀고 `(propose)` 태그를 단 것은 첫 줄
하나뿐이다. 나머지 역제안 여섯이 전부 `(reject-proposal)` 안에 가격을 실었다.

```
[buyer] (propose) I'd like to offer 15 for this textbook if it's in decent condition.
  [read] {'performative': 'propose', 'price': 15}
...
[buyer] (reject-proposal) ... would you accept $40, which is genuinely my maximum limit?
  [read] {'performative': 'reject-proposal', 'price': None}
[seller] (accept-proposal) You've got a deal at $40—that works for me ...
  [read] {'performative': 'accept-proposal', 'price': None}
[result] outcome=deal price=15 correct=0 violation=1 turns=8
```

**증거 2 — `structured-3` 시나리오 2 (`logs/structured-3.txt`).** 정답 40이 JSON 안에 두 번
들어 있었는데 프로토콜은 어느 쪽도 쓰지 않았다.

```
  [read] {'performative': 'propose', 'price': 20}           <- 기록된 거래가는 이것
  ...
  [read] {'performative': 'reject-proposal', 'price': 40}    <- 버려짐. 거절은 제안이 아니다
  [read] {'performative': 'accept-proposal', 'price': 40}    <- 무시됨. 성사가는 last_price[other]
[result] outcome=deal price=20 correct=0 violation=1 turns=8
```

**증거 3 — `free`가 같은 상황을 어떻게 피했는가.** reader는 81개 메시지에서
`reject-proposal`을 한 번도 쓰지 않았고 73개를 `propose`로 읽었다. "거절하고 역제안한다"를
FIPA가 쓰는 방식(새 `propose`)으로 옮겨 놓은 셈이다. 그 결과 `last_price`가 매번
갱신되었고 위반이 0건이다.

**설계 갈림길 하나.** `structured`에서
`{"performative": "reject-proposal", "content": {"price": 37}}`가 왔을 때 이 구현은 37을
버린다(`protocol.py`, 거절은 제안이 아니라는 FIPA 의미론). `content.price`를 받도록
바꾸면 `structured`의 숫자가 달라진다. 위 결과는 버리는 쪽 기준이다.

**아직 읽지 않은 것.** 거래 불가능 시나리오에서 `free` 4건 / `tagged` 3건 / `structured`
6건이 `open`으로 끝났다. `refuse`를 낸 횟수는 각각 2 / 3 / 0이다.
