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
건너뛰므로 중단된 실행은 같은 명령으로 이어 돌린다. 처음부터 재현하려면 `results.csv`와
`logs/`를 비우고 실행한다. 비우지 않으면 36개 쌍을 모두 건너뛰고 로그 파일에 이어 쓴다.

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
합의한 가격을 기준으로 하면 36 에피소드 전부 두 한도 안이었다(4부 (2)).

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
(1) Performative 태그는 어디에서 도움이 되었고, 어디에서 비용이 되었는가?
- Performative 태그는 메시지를 읽는 비용에서 가장 큰 도움이 되었다.

| condition | reader 호출 | reader 토큰 | 전체 토큰 중 reader 비중 |
|---|---|---|---|
| `free` | 81 (메시지 81개 전부) | 2,083,038 | 50% |
| `tagged` | 33 (`propose` 33개의 가격만) | 841,244 | 29% |
| `structured` | 0 | 0 | 0% |

토큰에는 `claude -p`의 호출당 고정 오버헤드가 포함되어 있다.

`free`는 전체 토큰의 절반을 메시지를 읽는 데 사용한 반면, `tagged`는 `free`의 약 40% 수준의 reader 토큰만을 사용하였고, `structured`는 메시지를 읽는 데 모델 호출이 필요하지 않았다.

- Performative 태그의 가장 큰 비용은 역제안 가격을 놓친다는 것이었다.
기본적으로 태그는 한 메시지에 하나의 행위만을 허용한다. 실제 시나리오 중에서 에이전트가 "제안한 가격을 거절하고 새로운 가격을 제시"하는 역제안을 `reject-proposal` 태그로 보냈으나, 프로토콜은 그 안의 가격을 폐기하였다.
그 결과 `tagged`에서 4건, `structured`에서 2건의 위반이 발생하였고, 정답이 40 하나뿐인 시나리오 2의 정답 수는 `free` 3/3, `tagged` 0/3, `structured` 1/3이었다.
추가로 원인은 알 수 없지만 `structured` 조건에서는 `refuse`가 단 한 건도 발생하지 않아, 거래 불가능한 시나리오 6건이 전부 `open`으로 종결되었다.

(2) `reader`가 역제안을 `accept`으로 읽은 에피소드, `seller`가 `reserve` 아래로 판 에피소드가 존재하였는가?
- `reader`가 역제안을 `accept`으로 읽은 에피소드는 존재하지 않았다.

| run | sc | 상대의 직전 가격 | reader가 accept로 읽은 메시지 |
|---|---|---|---|
| `free-1` | 1 | 125 | "I can work with $125—that's a fair price and we have a deal." |
| `free-1` | 2 | 40 | "I appreciate you working with me on this—I can do 40 and we have a deal." |
| `free-2` | 1 | 135 | "I think $135 is fair—you've moved to meet me halfway ... Let's go with $135." |
| `free-2` | 2 | 40 | "I can accept $40 for the textbook—that works for me. Let's finalize the deal!" |
| `free-3` | 1 | 125 | "I appreciate you meeting me closer to the middle—$125 works for me, and we have a deal." |
| `free-3` | 2 | 40 | "I appreciate your flexibility—$40 works for me, and we have a deal!" |

- `seller`가 `reserve` 아래로 판매한 에피소드는 4건이 존재하였다.
네 건(`tagged-1`, `tagged-2`, `structured-2`, `structured-3`의 시나리오 2) 모두 에이전트가 합의한 가격은 40이었다.
40이 `reject-proposal` 안에 실려 와 버려졌고, 거래가 그보다 앞선 `propose`의 가격(32, 15, 25, 20)으로 기록되었기 때문이다.

| run | 기록된 가격 | 기록된 가격을 만든 메시지 | 40에 합의한 메시지 |
|---|---|---|---|
| `tagged-1` | 32 | buyer: "(propose) ... would you consider $32 as a middle ground?" | seller: "(accept-proposal) Great, $40 works perfectly for me—let's finalize this deal for the textbook." |
| `tagged-2` | 15 | buyer: "(propose) I'd like to offer 15 for this textbook if it's in decent condition." | seller: "(accept-proposal) You've got a deal at $40—that works for me ..." |
| `structured-2` | 25 | buyer: `{"performative": "propose", "content": {"price": 25}}` | buyer: `{"performative": "reject-proposal", "content": {"price": 40}}` → seller: `{"performative": "accept-proposal", "content": {"price": null}}` |
| `structured-3` | 20 | buyer: `{"performative": "propose", "content": {"price": 20}}` | buyer: `{"performative": "reject-proposal", "content": {"price": 40}}` → seller: `{"performative": "accept-proposal", "content": {"price": 40}}` |

budget 위로 기록된 위반 2건도 같은 구조다. `tagged-1` 시나리오 1은 135에 합의했으나 seller의 첫 `(propose)`인 160으로,
`tagged-3` 시나리오 2는 40에 합의했으나 seller의 유일한 `(propose)`인 55로 기록되었다.

(3) 어떤 형식도 바꾸지 못한 것은 무엇인가?
- 거래가 불가능한 시나리오에서 에이전트가 결렬을 선언하지 않는 것은 어떤 형식도 바꾸지 못하였다.
에이전트들은 `refuse` 대신 한도 근처의 가격을 반복해서 제시하다 8턴을 채웠고, `open`이 `free` 4/6, `tagged` 3/6, `structured` 6/6이었다. `free-1` 시나리오 4는 buyer의 "I can go up to $145 as my final offer"와 seller의 "$200 is the absolute lowest I can go on this monitor"로 끝났다.
- 에이전트가 자신의 한도를 지키는 것 역시 형식과 무관하였다. 36개 에피소드 중 에이전트가 한도 밖의 가격에 합의한 경우는 존재하지 않았다.

