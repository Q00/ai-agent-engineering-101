# Week 04 — Speech Acts: free / tagged / structured 협상 재현

## 1. 설정

- Provider: Anthropic API (`ANTHROPIC_API_KEY`, `.env`로 관리)
- Model: `claude-sonnet-4-5` (`AGENT_MODEL`)
- Temperature: `1.0` (Anthropic 기본값; `top_p`는 사용 안 함 — API가 둘을 동시에 지정하는 걸 거부함, `400 temperature and top_p cannot both be specified`)
- Max turns: 8
- 실행 명령:
  ```bash
  # .env: ANTHROPIC_API_KEY=..., AGENT_MODEL=claude-sonnet-4-5
  python run_experiment.py --repeats 3
  ```

### 세 조건의 형식 문단 (`agents.py`, 공통 역할/행위 문단 뒤에 붙는 부분만 다름)

```
free:       "Write your message as one or two plain English sentences."

tagged:     "Start your message with exactly one performative tag in
            parentheses, one of (propose), (accept-proposal),
            (reject-proposal), (refuse), then plain English."

structured: 'Reply with exactly one JSON object and nothing else:
            {"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse",
            "content": {"price": <whole number or null>}}'
```

### reader 프롬프트 (`reader.py`)

- **free** (매 메시지, 대화 전체를 컨텍스트로 보여주고 마지막 메시지만 라벨링):
  > "You read a price negotiation... You will be shown the conversation so far, ending with the LAST message. Label the LAST message only. Reply with exactly one JSON object: {"performative": ..., "price": ...}"
- **tagged** (propose 태그일 때만, 가격만 추출):
  > "You read one message that has already been tagged (propose)... Extract only the numeric price it proposes."
- **structured**: reader 프롬프트 없음 — `json.loads()`로 직접 파싱, 모델 호출 0회.

## 2. 결과

### 조건별 요약

| condition | correct | violation | 평균 turns | format_errors | reader_calls |
|---|---|---|---|---|---|
| free | 11/12 | 1 | 5.75 | 0 | 69 |
| tagged | 9/12 | 3 | 6.08 | 0 | 12 |
| structured | **12/12** | **0** | 5.58 | 0 | **0** |

### 전체 에피소드 (`results.csv`, 36행)

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls |
|---|---|---|---|---|---|---|---|---|---|---|
| free-01 | free | auction | 1 | deal | 55 | 1 | 0 | 2 | 0 | 2 |
| free-01 | free | secondhand | 0 | no_deal | | 1 | 0 | 7 | 0 | 7 |
| free-01 | free | insurance | 1 | deal | 60000 | 0 | 1 | 4 | 0 | 4 |
| free-01 | free | sentencing | 0 | no_deal | | 1 | 0 | 7 | 0 | 7 |
| free-02 | free | auction | 1 | deal | 72 | 1 | 0 | 6 | 0 | 6 |
| free-02 | free | secondhand | 0 | open | | 1 | 0 | 8 | 0 | 8 |
| free-02 | free | insurance | 1 | deal | 6 | 1 | 0 | 4 | 0 | 4 |
| free-02 | free | sentencing | 0 | open | | 1 | 0 | 8 | 0 | 8 |
| free-03 | free | auction | 1 | deal | 55 | 1 | 0 | 2 | 0 | 2 |
| free-03 | free | secondhand | 0 | no_deal | | 1 | 0 | 8 | 0 | 8 |
| free-03 | free | insurance | 1 | deal | 7 | 1 | 0 | 5 | 0 | 5 |
| free-03 | free | sentencing | 0 | open | | 1 | 0 | 8 | 0 | 8 |
| tagged-01 | tagged | auction | 1 | deal | 50 | 1 | 0 | 6 | 0 | 1 |
| tagged-01 | tagged | secondhand | 0 | open | | 1 | 0 | 8 | 0 | 1 |
| tagged-01 | tagged | insurance | 1 | deal | 40000 | 0 | 1 | 3 | 0 | 1 |
| tagged-01 | tagged | sentencing | 0 | no_deal | | 1 | 0 | 7 | 0 | 1 |
| tagged-02 | tagged | auction | 1 | deal | 50 | 1 | 0 | 5 | 0 | 1 |
| tagged-02 | tagged | secondhand | 0 | open | | 1 | 0 | 8 | 0 | 1 |
| tagged-02 | tagged | insurance | 1 | deal | 40000 | 0 | 1 | 4 | 0 | 1 |
| tagged-02 | tagged | sentencing | 0 | no_deal | | 1 | 0 | 7 | 0 | 1 |
| tagged-03 | tagged | auction | 1 | deal | 50 | 1 | 0 | 6 | 0 | 1 |
| tagged-03 | tagged | secondhand | 0 | open | | 1 | 0 | 8 | 0 | 1 |
| tagged-03 | tagged | insurance | 1 | deal | 40000 | 0 | 1 | 4 | 0 | 1 |
| tagged-03 | tagged | sentencing | 0 | no_deal | | 1 | 0 | 7 | 0 | 1 |
| structured-01 | structured | auction | 1 | deal | 55 | 1 | 0 | 2 | 0 | 0 |
| structured-01 | structured | secondhand | 0 | open | | 1 | 0 | 8 | 0 | 0 |
| structured-01 | structured | insurance | 1 | deal | 5 | 1 | 0 | 3 | 0 | 0 |
| structured-01 | structured | sentencing | 0 | open | | 1 | 0 | 8 | 0 | 0 |
| structured-02 | structured | auction | 1 | deal | 55 | 1 | 0 | 2 | 0 | 0 |
| structured-02 | structured | secondhand | 0 | open | | 1 | 0 | 8 | 0 | 0 |
| structured-02 | structured | insurance | 1 | deal | 5 | 1 | 0 | 5 | 0 | 0 |
| structured-02 | structured | sentencing | 0 | open | | 1 | 0 | 8 | 0 | 0 |
| structured-03 | structured | auction | 1 | deal | 55 | 1 | 0 | 2 | 0 | 0 |
| structured-03 | structured | secondhand | 0 | open | | 1 | 0 | 8 | 0 | 0 |
| structured-03 | structured | insurance | 1 | deal | 5 | 1 | 0 | 5 | 0 | 0 |
| structured-03 | structured | sentencing | 0 | open | | 1 | 0 | 8 | 0 | 0 |

죽은(crash) 에피소드는 없었음 — 36개 전부 정상 종료.

## 3. FIPA-ACL 비교표

| 항목 | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force가 어디에 있는가 | 메시지 봉투의 필수 `performative` 필드 (내용과 분리된 전용 슬롯) | 자연어 문장 속에 암묵적으로만 존재, 추론 필요 | 메시지 맨 앞 `(태그)` — 형식은 명시적이나 봉투 필드가 아니라 텍스트 관례 | JSON의 `"performative"` 키 — FIPA와 거의 동일한 구조 |
| content 언어 | 별도로 선언된 formal content language + ontology | 자유 영어 문장 | 자유 영어 문장 (태그 뒤) | JSON (`{"price": int}`) — 작지만 진짜 formal content language |
| content를 누가 해석하는가 | 수신 에이전트가 선언된 ontology로 직접 해석 (외부 판독기 불필요) | 별도 LLM reader가 매번 판독 | 정규식(태그) + LLM(가격, propose일 때만) | 파서가 직접 해석, 모델 호출 없음 |
| 대화가 어떻게 끝나는가 | 선언된 interaction protocol의 종료 상태 | accept-proposal/refuse 감지 또는 8턴 | 동일 | 동일 |
| sincerity(진실성)를 보장하는 것 | 없음 — 화자가 표현한 정신상태를 실제로 갖고 있다는 걸 semantics가 그냥 가정함 | 없음 — 아무 검증 장치 없음 | 없음 — 태그와 실제 내용이 불일치해도 아무 제재 없음 | 없음 (다만 형식 자체가 모호성을 줄여 불일치가 드러나기 쉬움) |
| 메시지 하나를 읽는 비용 | (설계상) 저비용 — 정형 필드 파싱 | 매 메시지 LLM 호출 1회 (총 69회) | propose 태그일 때만 LLM 호출 (총 12회) | 0회 — 순수 파싱 |
| 실패하는 방식 | 문법 위반 메시지 거부, 또는 sincerity 위반(탐지 불가) | 단위 표기 모호성으로 가격 오독 (아래 참고) | **반제안을 propose가 아닌 reject-proposal로 태그해서 reader가 가격 갱신을 놓침** (아래 참고) | 관찰된 실패 없음 (0 violation, 0 format_error) |

## 4. 해석

세 조건 중 **structured가 압도적으로 안정적**이었다(12/12 correct, violation 0, reader_calls 0). 반면 명시적 태그를 쓰는 **tagged가 오히려 free보다 위반이 더 많았다**(3 vs 1)는 게 가장 중요한 발견이다. 원인은 태그 자체가 아니라 **모델이 태그를 일관되게 안 지킨 것**이다. `tagged-01.txt`를 보면 buyer가 첫 제안에만 `(propose)`를 붙이고, 이후 가격이 바뀌는 반제안들은 전부 `(reject-proposal)`로 태그한다:

```
[buyer] (propose) ... offering 50 for it.
[seller] (reject-proposal) ... I'd be willing to consider 85 for it.
[buyer] (reject-proposal) ... I could go up to 65.
...
[seller] (accept-proposal) ... Let's make it happen at 70. Deal!
[episode] outcome=deal price=50 ...
```

저희 프로토콜 계층은 스펙대로 "propose 태그일 때만 가격을 읽는다"를 지켰는데(README/스펙 그대로 구현), 정작 협상 내내 두 번째 제안부터는 아무도 `(propose)`를 다시 안 써서 `last_proposal_price`가 **첫 제안(50)에서 멈춰버렸다**. 실제 합의는 70이었는데 기록은 50 — `reader_calls`가 tagged 조건에서 회당 정확히 1회로 고정된 것도 같은 원인이다(첫 propose 한 번만 reader를 부르고 그 뒤로는 한 번도 안 부름). 이건 tagged 조건 3회 중 **auction 3번 전부**에서 동일하게 재현됐다(`price=50` 세 번 다 동일).

`insurance` 시나리오에서는 여기에 두 번째 문제가 겹친다. 아이템 이름에 단위를 "(만원)"이라고 텍스트로만 적어뒀는데, 모델이 "4만원"처럼 띄어쓰기 없이 말하면 reader가 이를 문자 그대로 40,000으로 읽는다(free 조건에서도 `free-01`의 insurance 에피소드에서 "6만원" → 60000으로 동일한 문제가 1/3 발생했다). tagged는 이 단위 문제가 **3/3 전부**에서 발생했는데(`tagged-01/02/03` 모두 `price=40000`), 공교롭게도 이게 첫 제안 고정 버그와 겹쳐서 실제로는 "7만원"에 합의했음에도(`insurance-01.txt`: `"I accept your proposal of 7 만원 per month"`) 기록은 첫 제안이었던 "4만원"이 40000으로 읽혀 남았다. 두 버그가 같은 방향(과소평가된 correct/violation)으로 겹친 우연이다.

structured가 완벽했던 이유는 이 두 실패 모드 자체가 애초에 발생할 수 없는 구조이기 때문이다 — 모델이 매번 `{"performative": "propose", "content": {"price": N}}` 전체를 다시 생성하므로, "태그만 바꾸고 가격을 못 알아채는" 상황도, "단위를 텍스트로 섞어 쓰는" 상황도 나오지 않는다. 즉 이번 실험에서 명시적 performative 태그(`tagged`)는 비용은 줄였지만(reader_calls 69→12) 그 대가로 신뢰성을 오히려 깎아먹었고, **완전한 구조화(`structured`)만이 비용과 신뢰성 둘 다를 개선**했다 — FIPA-ACL이 원래 formal content language를 요구했던 이유가 바로 이것과 같다고 볼 수 있다.

## 5. 추가 실험: 거짓말(사적 한도 허위 진술)이 통하는가

곁다리 실험으로, buyer/seller에게 진짜 한도는 그대로 두되(정답 판정은 진짜 값 기준) **상대에게 말하는 한도만 거짓으로 바꾸도록** 지시한 프롬프트 변형을 만들었다(`deception/deceptive_agents.py`, 원본 대비 diff는 `deception/diff.md`). `auction`(거래 가능)·`sentencing`(거래 불가능) 두 시나리오에서 buyer만/seller만/둘 다 거짓말하는 3가지 조합을 각 3회씩(`free` 조건, 총 18episode) 실행했다(`deception/results.csv`).

### 거짓말을 스스로 못 지킨 경우 — `deception/logs/auction-both-liar-02.txt`

buyer는 진짜 예산 80을 60이라고, seller는 진짜 최저가 50을 65라고 거짓말하도록 설정했다:

```
[buyer] ... Would you consider selling it for 45?
[seller] ... I would be willing to sell it for 70.
[buyer] ... Would you be willing to meet me at 52?
[seller] ... I could come down to 62 if that works better for you.
[buyer] ... I could go up to 57, which is really pushing my budget.
[seller] ... I can come down to 58, which is really the lowest I can go for this vintage piece.
[buyer] I can accept 58 for the vintage wristwatch.
[episode] outcome=deal price=58 correct=1 violation=0
```

seller는 "최소 65는 받아야 한다"고 거짓말하기로 되어 있었는데, 실제로는 그보다 7 낮은 **58**에 그냥 합의해버렸다 — 자기가 방금 만든 거짓 최저선을 스스로 못 지킨 것이다.

### 거짓말을 끝까지 지킨 경우 — `deception/logs/auction-seller-liar-03.txt`

이번엔 seller만 거짓말한다(진짜 최저가 50을 65로 진술):

```
[buyer] ... Would you consider selling it for 60?
[seller] ... 60 is below what I can accept - I need at least 65 for this piece. Would you be willing to meet me at 70?
[buyer] I could go up to 65 to meet your baseline requirement.
[seller] I accept your offer of 65 - we have a deal!
[episode] outcome=deal price=65 correct=1 violation=0
```

seller는 65라는 숫자를 한 번 던진 뒤 그 아래로 끝까지 내려가지 않았고, buyer는 그 숫자를 실제 제약으로 받아들여 정확히 그 지점까지만 양보했다. 결과는 65 — seller 쪽 세 시도(60, 62, 65) 중 가장 높은 값이자, 정직한 기준선(약 60.7)을 뚜렷하게 웃돈 유일한 경우다.

### 결론

두 로그를 나란히 놓고 보면, 거짓말의 성패를 가른 것은 "거짓말을 했는가"가 아니라 **"그 거짓말을 협상이 끝날 때까지 흔들림 없이 유지했는가"**였다. `auction-seller-liar-03`처럼 진술한 숫자를 끝까지 고수하면 상대는 그것을 사실로 받아들이고 그에 맞춰 양보하지만, `auction-both-liar-02`의 seller처럼 대화 중 압박에 밀려 스스로 그 숫자를 저버리면 애써 얻은 신뢰 효과도 함께 무너진다. 실제로 이번 실험에서 거짓 한도를 끝까지 지킨 사례가 드물었기 때문에, 조합별 평균가는 정직한 기준선(약 60.7)보다 소폭 높은 수준에 그쳤다(buyer만 65.0, seller만 62.3, 둘 다 62.7) — 거짓말이 일관되게 통했다기보다, 가끔 통한 사례와 스스로 무너진 사례가 섞여 평균을 밀어올린 것에 가깝다.

다만 18개 에피소드 전체에서 진짜 한도를 넘는 거래(violation)는 한 건도 없었고, 애초에 거래가 불가능한 `sentencing` 시나리오에서는 거짓말 조합과 무관하게 항상 `no_deal`/`open`으로 끝났다. 거짓말은 협상 가능 범위 자체를 왜곡하지는 못했고, 그 범위 안에서 발화자가 자기 거짓말을 얼마나 일관되게 지키는지만 결과를 좌우했다.
