# Week 04 — Speech Acts in Practice: Free, Tagged, and Structured Negotiation

## 0. 과제 구조를 어떻게 바꿨는가 — 브로커 에이전트 추가

이 과제는 buyer와 seller만 두는 기본 틀에서, 세 번째 에이전트인 브로커(broker)를 하나 끼워 넣는 방향으로 조금 변형했다. 필수 3조건(free/tagged/structured)은 브로커 없이 원안 그대로 돌려서 `results.csv`에 기록했고, `check_week04.py`가 `condition` 값을 free/tagged/structured 셋으로 검사하는 기본 구조에 추가하여, 브로커는 4번째 조건(`structured + broker`)으로 `results_extension.csv`에 따로 기록했다. 필수 조건은 건드리지 않으면서 "기본 틀 위에 브로커를 얹으면 뭐가 달라지는가"만 별도로 비교해보고자 했다.

### 왜 브로커인가

정부-민간 조달 같은 실제 계약에서는 구매자와 판매자가 직접 붙기보다 조달 대행사나 브로커가 중간에서 가격을 조율하고 계약이 어느 한쪽에 부당하지 않은지 확인해주는 경우가 많다. 3주차 Contract Net에서 "낙찰받은 contractor가 일을 더 잘게 쪼개면 그 순간 하위 매니저가 된다"고 배운 것과 비슷한 맥락이라고 생각했다 — 협상 참여자가 하나 늘어도 propose/accept-proposal 같은 프로토콜의 기본 틀은 그대로 유지된다는 것을 확인해보고자 했다.

### 브로커가 하는 일 (`agents.py`, `negotiate.py`)

역할은 세 가지로 나눴다.

1. **중계(relay)** — 평범한 propose/reject-proposal 메시지는 그대로 상대에게 넘긴다. 모델 호출 없음.
2. **교착 중재(mediate)** — 한쪽이 연속 2번 거절만 하고 자기 가격을 한 번도 안 내면(코드에서는 `reject_streak`로 이를 "교착"이라 정의했다), 그때만 브로커의 LLM을 부른다. 브로커는 buyer/seller 둘 다 모르는 서로의 진짜 한도(reserve, budget)를 알고 있으니, 그 사이 타협가 하나를 제안한다.
3. **검증/거부권(veto)** — accept-proposal이 나오면 그 가격이 seller의 reserve보다 낮거나 buyer의 budget보다 높은지 코드로 직접 계산한다. 위반이면 거래를 거부하고 협상을 계속시킨다. LLM 호출이 아니라 그냥 숫자 비교다.

### 이렇게 짠 이유

중재는 LLM에게, 검증은 코드에게 맡긴 건 3주차에서 배운 원칙("확인 가능한 사실을 LLM 판단에 맡기지 말라") 그대로다. 거래가가 한도 안에 있는지는 브로커가 이미 두 숫자(reserve, budget)를 알고 있으니 계산 한 번으로 100% 정확하게 판정할 수 있다. 반대로 "뭐가 공정한 타협가인가"는 정해진 답이 없는 상황 판단이라 LLM에게 맡겼다.

브로커가 모든 메시지에 개입하지 않고 교착일 때만 끼어들게 한 것도 같은 맥락이다. 매번 개입하면 모델 호출이 최소 2배로 늘어나는데, 협상이 잘 진행되고 있을 땐 브로커가 할 일이 없다. 중재가 실제로 필요한 순간에만 비용을 쓰자는 판단이었다.

수수료(commission)는 계산만 하고 브로커의 판단에 영향을 안 주도록 시스템 프롬프트에 못 박아뒀다. 수수료를 더 받으려는 유인이 있는 중개인은 판단이 편향될 수 있는데, 여기서는 "이해관계 없는 공정한 중재자"를 전제로 두고자 했다.

## 1. 설정

- **Provider**: Anthropic (`ANTHROPIC_API_KEY`)
- **Model**: `claude-haiku-4-5`, **Temperature**: `0` (`model.py`)
- **Turn limit**: 8 (`MAX_TURNS`, `negotiate.py`)
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
| structured + broker (확장) | 18 | 0 | 6.5 | 0 | 0 |

### 시나리오: 어떤 계약 상황을 만들었나 (`scenarios.json`, 억원 단위)

협상 소재는 정부 부처(조달 담당자, buyer)와 건설 시공사(seller) 사이의 데이터센터 구축 계약으로 정했다. 이유는 두 가지다. reserve/budget 구조가 억지스럽지 않다는 것 — 시공사는 "이 밑으로는 손해라 못 받는다"는 최저 수주가가, 정부는 "이 이상은 예산 초과라 못 쓴다"는 예산 상한이 실제로 존재하는 협상이다. 그리고 프로젝트 성격(규모, 보안 등급, 입지)을 바꾸면 서로 다른 난이도의 협상을 자연스럽게 만들 수 있다는 것.

6개 시나리오는 "여유/팽팽함 × 가능/불가능"을 섞어서 짰다.

| id | 프로젝트 | reserve(억) | budget(억) | 거래 가능? | 이 시나리오를 넣은 이유 |
|---|---|---|---|---|---|
| 1 | 지방 거점 엣지 데이터센터 | 300 | 380 | 가능 (여유 80) | 가장 쉬운 대조군 — 기본 협상이 정상 작동하는지 확인용 |
| 2 | 수도권 AI GPU 클러스터 데이터센터 | 1200 | 1250 | 가능 (여유 50, 전체 대비 4%) | 규모는 크면서 마진은 팽팽한 경우 — 좁은 구간으로 수렴하는지 보고 싶었음 |
| 3 | 국방부 보안등급 데이터센터 | 900 | 750 | 불가능 (150 차이) | 보안 등급 때문에 시공비는 비싼데 정부 예산은 고정된 상황 — 애초에 거래가 안 되는 것을 스스로 알아채는지 시험 |
| 4 | 재해복구(DR) 백업센터 | 400 | 380 | 불가능 (근소한 차이 20) | 3번과 달리 격차가 작은 "거의 되는데 안 되는" 케이스 — 애매하게 가까운 숫자에서도 정확히 판단하는지 시험 |
| 5 | 클라우드 리전 데이터센터 | 600 | 900 | 가능 (여유 300) | 가장 여유로운 경우 — 빨리 합의되는지 확인 |
| 6 | 탄소중립 친환경 데이터센터 | 750 | 770 | 가능 (여유 20, 가장 좁음) | 6개 중 성사 가능 구간이 제일 좁은 경우 — 이 좁은 창을 실제로 찾아내는지가 진짜 시험대라고 생각했음 |

이렇게 섞어둔 덕분에, 4절에서 "structured 조건이 팽팽한 사례(2, 6)에서 유독 open으로 끝난다"처럼 어떤 조건이 어떤 시나리오에서 실패하는지를 시나리오 설계만으로도 구분해볼 수 있었다.

에피소드 72개 전체(필수 54 + 확장 18)는 `results.csv`, `results_extension.csv`에 있다. 죽은 에피소드는 없었다(`note`에 전부 `tokens=` 형태로 정상 기록됨).

### 축소판: structured, 브로커 없음 vs 있음 (시나리오별 1회차)

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
| sincerity를 보장하는 것 | 규격상 전제만 함, 강제 없음 (SC00037J 3.5: "beyond scope") | 없음 | 없음 | 없음 | accept 가격만 코드로 사후 검증 — 발화 자체의 진실성은 여전히 못 봄 |
| 메시지 하나 읽는 비용 | 정의되지 않음(구현 나름) | reader 호출 1회/메시지 | propose/reject만 호출 | 0 (파싱만) | 0 + 교착시에만 브로커 호출 1회 |
| 실패하는 방식 | ontology 불일치, 의미론 검증 불가(Wooldridge 1998) | 질문을 refuse로 오독, 숫자 오독 | 태그 뒤 역제안 못 읽음(가격 갱신 안 됨) | JSON 뒤 문장 놓침 | 위 실패들 그대로, 단 accept 시점의 숫자 위반만 코드로 막힘 |

## 4. 해석

free는 correct가 제일 높게(6/18) 나오는데, 협상을 잘해서가 아니다. 로그(`logs/free-1-*.txt`)를 보면 buyer가 매번 정확히 같은 질문("...what's your initial asking price for this project?")으로 열었다 — temperature=0이니 6개 시나리오 전부 똑같았다. reader는 4가지 화행 중 하나를 골라야 했기 때문에 이 질문을 `refuse`로 라벨링했고, 18개 에피소드 전부가 `mean_turns=1.0`으로 1턴 만에 끝났다. correct 6건은 정확히 거래 불가능한 시나리오(id 3, 4) × 3회 반복과 일치한다. 결렬이 정답인 시나리오에서 우연히 결렬로 끝난 것이지, 판단이 맞은 것은 아니다. 강의 자료의 참조 실행("free의 correct 11건 중 8건이 이것")과 똑같은 현상이었다.

tagged는 오히려 제일 나빴다(4/18, violation 13건). 처음엔 "reader가 태그 뒤 가격을 놓친다"고 생각했는데, 확인해보니 아니었다 — reader는 `(reject-proposal) ... Let me propose 325 as a middle ground` 같은 문장에서 325를 정확히 읽어냈다(`protocol.py`의 tagged reader는 propose/reject-proposal 둘 다에서 가격을 읽도록 짜뒀다). 진짜 원인은 `negotiate.py`가 `reject-proposal`일 땐 `last_price`를 갱신하지 않고 `propose`일 때만 갱신하도록 짠 것이었다. `logs/tagged-2-*.txt`(run 5, scenario 1)를 보면 buyer의 유일한 진짜 `propose`는 1턴째의 280이었고, 이후 네 번의 `(reject-proposal) ... 310 / 325` 같은 역제안은 전부 가격이 안 갱신됐다. 결국 seller가 325에 합의하려고 `(accept-proposal)`을 보냈는데도, 코드는 여전히 1턴째의 280을 buyer의 "마지막 제안"으로 기억하고 있어서 거래가를 280으로 확정했다. reserve가 300이니 280 < 300이 되면서 `results.csv`에 `run=5,scenario=1,outcome=deal,price=280,violation=1`이 찍혔다. 양측은 실제로 한도를 안 어겼는데, 기록 로직의 결함이 가짜 위반을 만든 셈이다. tagged의 violation 13건 중 상당수가 이 패턴일 가능성이 높지만, 13건 전수를 다 확인하지는 못했다 — 시간상의 한계로 남겨둔다.

structured는 violation을 완전히 없앴다(0건). 가격이 숫자 필드로 분리돼 있으니 tagged의 실패 패턴 자체가 애초에 생길 수 없다. 대신 correct는 12/18에 그쳤는데, `logs/structured-2-*.txt`를 보면 팽팽한 시나리오(2, 6)에서 양쪽이 8턴 동안 가격을 좁히지 못하고 `open`으로 끝나는 경우가 있었다. 형식 문제가 아니라 협상 자체가 교착 상태에 빠진 것이었다.

브로커를 얹자 정확히 이 교착 문제가 풀렸다(12/18 → 18/18). `logs/structured-broker-*.txt`의 시나리오 2를 보면, seller가 두 번 연속 거절만 하자(`reject_streak` 조건 충족) 브로커가 끼어들어 `{"performative": "propose", "content": {"price": 1225}}`를 제안했고, buyer가 바로 수락해서 거래가 성사됐다(1225는 [1200,1250] 안). 15번의 중재 전부 유효 범위 안의 가격을 냈고, 거부권(veto)은 한 번도 발동하지 않았다. 이는 이번 시나리오 세트에서 에이전트들이 한도 자체를 어기는 accept를 내지 않았다는 뜻이지, 거부권 로직이 필요 없다는 뜻은 아니다. 그리고 브로커가 고친 건 tagged/free 쪽의 "읽기 오류"가 아니라 structured의 "교착"이라는 점도 짚어두고자 한다 — 하나의 확장이 모든 실패를 다 고쳐주지는 않았다.
