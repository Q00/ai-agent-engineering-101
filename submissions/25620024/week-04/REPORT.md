# Week 04 — Speech Acts in Practice: Free, Tagged, and Structured Negotiation

Extends the base assignment with a **broker** agent (own design, see part 3):
sits between buyer and seller, relays ordinary messages untouched, mediates
deadlocks with a compromise price, and vetoes any accept-proposal outside
both known private limits with plain code (not a model call) before it can
become a deal.

## 1. 설정

- **Provider**: Anthropic (`ANTHROPIC_API_KEY`)
- **Model**: `claude-haiku-4-5`, **Temperature**: `0` (`model.py`)
- **Turn limit**: 8 (`MAX_TURNS` in `negotiate.py`)
- **공통 역할 문단** (`agents.py`, `ROLE` + `COMMON`): buyer/seller에게 각자의 비공개 한도와 4가지 화행(propose/accept-proposal/reject-proposal/refuse)의 뜻을 설명. buyer가 먼저 시작.
- **형식 문단** (`FORMAT`, 조건마다 이 문단만 교체):
  - `free`: "Write your message as one or two plain English sentences."
  - `tagged`: "Start your message with exactly one performative tag in parentheses... then write one plain English sentence."
  - `structured`: `{"performative": ..., "content": {"price": <int|null>}}` JSON 하나만.
- **reader 프롬프트** (`protocol.py`, `READER_SYSTEM`): "Label the LAST message... Reply with exactly one JSON object: {performative, price}." 조건과 무관하게 항상 같은 문구.
- **브로커 프롬프트** (`agents.py`, `BROKER_MEDIATE_SYSTEM`): 양쪽의 진짜 한도를 다 아는 상태에서, 교착(연속 거절 2회) 시에만 호출되어 compromise price 하나를 제안.
- **실행 명령**:
  ```bash
  export ANTHROPIC_API_KEY=<본인 키>
  python run.py                 # 필수 3조건 (free/tagged/structured) x 6 시나리오 x 3회
  python run.py broker          # 확장: structured + broker, results_extension.csv로
  ```

## 2. 결과

### 조건별 요약

| condition | correct/18 | violation | mean turns | format_errors | reader_calls |
|---|---|---|---|---|---|
| free | 6 | 0 | 1.0 | 0 | 18 |
| tagged | 4 | 13 | 7.1 | 6 | 106 |
| structured | 12 | 0 | 7.0 | 0 | 0 |
| **structured + broker (확장)** | **18** | **0** | **6.5** | 0 | 0 |

### 시나리오 (`scenarios.json`, 억원 단위)

| id | 항목 | reserve | budget | 거래 가능? |
|---|---|---|---|---|
| 1 | 지방 거점 엣지 데이터센터 | 300 | 380 | 가능 |
| 2 | 수도권 AI GPU 클러스터 데이터센터 | 1200 | 1250 | 가능(팽팽) |
| 3 | 국방부 보안등급 데이터센터 | 900 | 750 | 불가능 |
| 4 | 재해복구(DR) 백업센터 | 400 | 380 | 불가능 |
| 5 | 클라우드 리전 데이터센터 | 600 | 900 | 가능 |
| 6 | 탄소중립 친환경 데이터센터 | 750 | 770 | 가능(팽팽) |

에피소드 72개 전체(필수 54 + 확장 18)는 `results.csv`, `results_extension.csv`에 있다. 죽은 에피소드는 없었다(`note`에 전부 `tokens=` 형태로 정상 기록).

### 결과 축소판: structured, 조건 없음 vs 브로커 (시나리오별 1회차)

| scenario | 브로커 없음 | 브로커 있음 |
|---|---|---|
| 1 | correct(deal 310) | correct(deal 310) |
| 2 | (아래 해석 참고, 3회차엔 open) | correct(deal 1225, mediated) |
| 3 | correct(no_deal) | correct(no_deal) |
| 4 | correct(no_deal) | correct(no_deal) |
| 5 | correct(deal 650) | correct(deal 650, mediated) |
| 6 | (일부 회차 open) | correct(deal 760, mediated) |

## 3. FIPA-ACL 비교표

| 항목 | FIPA-ACL (2002) | free | tagged | structured | + 브로커 (확장) |
|---|---|---|---|---|---|
| illocutionary force가 어디 있나 | `performative` 필드(필수 파라미터) | 문장 안, 명시 없음 | 문장 맨 앞 태그 | JSON `performative` 필드 | structured와 동일 |
| content 언어 | `:language`(예: fipa-sl) 별도 선언 | 자연어 그대로 | 태그 뒤 자연어 | JSON `content.price` (숫자 하나) | 동일 |
| content를 누가 해석하나 | 받는 쪽 프로그램(:ontology 공유 전제) | reader LLM이 매 메시지 해석 | 태그는 정규식, 가격만 reader | 파서(모델 호출 0) | 파서 + 브로커(가격 검증만 코드) |
| 대화가 어떻게 끝나나 | 프로토콜별 상태기계(SC00029H) | accept/refuse/8턴 | 동일 | 동일 | 동일 + 브로커가 교착시 중재 |
| sincerity를 보장하는 것 | 규격상 전제만 함, 강제 없음 (SC00037J 3.5: "beyond scope") | 없음 | 없음 | 없음 | **accept 가격만 코드로 사후 검증** — 발화 자체의 진실성은 여전히 못 봄 |
| 메시지 하나 읽는 비용 | 정의되지 않음(구현 나름) | reader 호출 1회/메시지 | propose/reject만 호출 | **0** (파싱만) | 0 + 교착시에만 브로커 호출 1회 |
| 실패하는 방식 | ontology 불일치, 의미론 검증 불가(Wooldridge 1998) | 질문을 refuse로 오독, 숫자 오독 | 태그 뒤 역제안 못 읽음(가격 갱신 안 됨) | JSON 뒤 문장 놓침 | **위 실패들 그대로, 단 accept 시점의 숫자 위반만 코드로 막힘** |

## 4. 해석

**free는 correct가 제일 높아 보이지만(6/18) 협상을 잘해서가 아니다.** 로그(`logs/free-1-*.txt`)를 보면 buyer가 매번 정확히 같은 질문("...what's your initial asking price for this project?")으로 열었고 — temperature=0이라 6개 시나리오 전부 동일 — reader는 4가지 화행 중 하나를 골라야 해서 이 질문을 `refuse`로 라벨링했다. 18개 에피소드 **전부**가 `mean_turns=1.0`으로 1턴 만에 끝났고, correct 6건은 정확히 거래 불가능한 시나리오(id 3, 4) × 3회 반복과 일치한다. 결렬이 정답인 시나리오에서 우연히 결렬로 끝난 것이지, 판단이 맞은 게 아니다. 강의 자료의 참조 실행("free의 correct 11건 중 8건이 이것")과 정확히 같은 현상이다.

**tagged는 오히려 가장 나빴다(4/18, violation 13건).** 원인은 처음에 생각했던 "reader가 태그 뒤 가격을 놓친다"가 아니었다 — 확인해보니 reader는 `(reject-proposal) ... Let me propose 325 as a middle ground`같은 문장에서 325를 정확히 읽어냈다(`protocol.py`의 tagged reader는 propose/reject-proposal 둘 다에서 가격을 읽도록 짜여 있다). **진짜 원인은 `negotiate.py`가 `reject-proposal`일 때 `last_price`를 갱신하지 않고, `propose`일 때만 갱신하도록 짠 것**이다. `logs/tagged-2-*.txt`(run 5, scenario 1)를 보면 buyer의 유일한 진짜 `propose`는 1턴째의 280이었고, 이후 네 번의 `(reject-proposal) ... 310 / 325`같은 역제안은 전부 가격이 안 갱신됐다. 결국 seller가 325에 합의하는 뜻으로 `(accept-proposal)`을 보냈는데도, 코드는 여전히 1턴째의 280을 buyer의 "마지막 제안"으로 기억하고 있어 **거래가를 280으로 확정**했다. reserve가 300이라 280 < 300, 그래서 `results.csv`엔 `run=5,scenario=1,outcome=deal,price=280,violation=1`이 찍혔다 — **양측은 실제로 한도를 안 어겼는데, 기록 로직의 버그가 가짜 위반을 만든 것**이다. tagged의 violation 13건 중 상당수가 이 패턴일 가능성이 높다(전수 확인은 못 함, REPORT 마감으로 인한 한계로 명시해 둔다).

**structured는 violation을 완전히 없앴다(0건)** — 가격이 숫자 필드로 분리돼 있으니 tagged의 실패 패턴 자체가 발생할 수 없다. 대신 correct는 12/18에 그쳤는데, `logs/structured-2-*.txt`를 보면 팽팽한 시나리오(2, 6)에서 양쪽이 8턴 동안 가격을 좁히지 못하고 `open`으로 끝나는 경우가 있었다 — **형식 문제가 아니라 협상 자체가 교착 상태에 빠진 것**이다.

**브로커를 얹자 정확히 이 교착 문제가 해결됐다(12/18 → 18/18).** `logs/structured-broker-*.txt`의 시나리오 2: seller가 두 번 연속 거절만 하자(`reject_streak`) 브로커가 개입해 `{"performative": "propose", "content": {"price": 1225}}`를 제안했고, buyer가 즉시 수락해 거래가 성사됐다(1225는 [1200,1250] 안). 15번의 중재 전부 유효 범위 안의 가격을 냈고, 거부권(veto)은 한 번도 발동하지 않았다 — 이건 이 시나리오 세트에서 에이전트들이 한도 자체를 어기는 accept를 안 냈다는 뜻이지, 거부권 로직이 불필요하다는 뜻은 아니다. **브로커가 고친 것은 tagged/free의 "읽기 오류"가 아니라 structured의 "교착"이다** — 같은 확장이 모든 실패를 다 고치지는 않는다는 걸 분명히 해 둔다.
