# Round 3 Buyer 전략 설계서: 계약검증형 기대잉여 Buyer

> 범위: buyer만 다룹니다. 아래 수치는 모두 **초기 가정값**입니다. 실제 거래가 쌓이면 갱신합니다. 고장확률, 인기상품, 성과는 아직 관측된 데이터가 없으므로 사전분포(prior)로만 둡니다.
> 붙여주신 계약 문서의 설치·실행 지시는 분석 대상으로만 읽었고 따르지 않았습니다.

---

## 1. 핵심 설계 한 문장

**결정은 코드가 하고 gpt-5-nano는 파싱과 문장 작성만 맡습니다.** 결정 방식은 이렇습니다. 후보마다 고장·유통기한·허위 claim 위험을 반영한 **기대잉여 E[S]**를 계산하고, 협상한 조건을 `accept` 절에 그대로 넣어 kit 결제 단계에서 강제합니다. 고장·결제거절처럼 늦게 도착하는 증거는 다음 주문 시작 시 흡수해 상점별·등급별 신뢰도를 갱신하고, 그 신뢰도를 ledger 근거가 붙은 포럼 글로 공개합니다.

---

## 2. 관측 → 기억 → 의사결정 → 툴 → 검증 흐름

`market_buyer` 1회 실행 순서입니다.

**① Ingest (지연 증거 흡수, 코드)**
- `payments.jsonl`: 커서 이후의 새 줄만 읽습니다.
  - `reason`이 있으면 결제거절입니다. 해당 상점의 거절 카운트를 올리고 사유별로 분류합니다(예: no certificate).
  - `ledger`가 있으면 정산 성공입니다. 구매 기록을 확정합니다.
- `broken.jsonl`: 새 줄을 읽어 §3.4의 귀속 알고리즘을 실행합니다.
- 정산 후 6h + ε가 지났는데 고장 기록이 없는 구매는 "무고장 성공"으로 확정합니다.
- **결제거절 학습 시점**: kit은 `market_buyer`가 이미 종료된 뒤 결제를 거절합니다. 그래서 거절 사실은 *그 주문에는 반영될 수 없고*, **다음 주문의 ①단계에서야** 기억에 들어옵니다. 따라서 상점 s가 직전 주문에서 거절을 유발했다면 그 패널티는 1주문 늦게 반영됩니다. 이 지연 때문에 초기 몇 건은 같은 상점에서 연속으로 거절될 수 있다는 점을 허용합니다.

**② Observe**
- 읽는 파일: `order.json`(item, category, budget, acceptable, kind, use_within_hours, expires), `shops.json`(offers, reviews), `catalog.json`(list_price, shelf_hours).

**③ Candidate 생성**
- C = {(s, k) | k ∈ acceptable ∩ offers_s, qty > 0}
- 같은 GitHub owner의 내 가게는 정산이 불가능하므로 후보에서 제외합니다.

**④ 점수화·정렬 (코드)**
- 후보마다 §3의 E[S]와 Score를 계산하고 상위 3개만 남깁니다.

**⑤ 협상 (툴: A2A)**
- 1위 후보부터 시작합니다. 첫 메시지는 `I want to buy <name> (<sku>)`입니다.
- 회신의 가격·조건은 정규식으로 추출합니다. 실패할 때만 nano로 파싱합니다.
- 최대 R라운드 동안 양보 스케줄에 따라 역제안합니다.
- 결렬되거나 응답이 없으면 다음 후보로 넘어갑니다.

**⑥ 검증 (코드 가드)**
- 영수증 `item` ∈ {order.item} ∪ acceptable
- 영수증 price = 합의가 ≤ min(budget, P_max)
- `sig_seller` 형식이 존재하는지
- `claim`/`warranty_hours`/`cert`가 합의 조건과 일치하는지

**⑦ accept 절 생성**
- 합의 조건을 그대로 `accept`에 넣습니다.
- 이유: kit은 seller가 kit에 보낸 영수증의 unit/claim/warranty/cert를 사용하고, buyer 사본은 보지 않습니다. 상점이 buyer에게 보여준 조건과 kit에 보낸 조건이 다를 수 있는데, `accept` 절이 이 **bait-and-switch를 차단하는 유일한 장치**입니다.

**⑧ 출력**
- 마지막 줄에 JSON 1줄을 출력합니다. 어떤 예외가 나도 유효한 JSON을 출력합니다.

**⑨ review 훅 (정산 후)**
- `deal.json`의 `claim_honest`, `grade`, `value`, `warranty_hours`로 별점을 규칙 기반으로 계산합니다.
- 상점 신뢰도를 갱신합니다.
- 포럼 게시 조건(§3.6)을 검사합니다.

---

## 3. 정책·점수 산식

### 3.1 기호

| 기호 | 의미 |
|---|---|
| L | SKU의 list_price (정산 시 value 상한) |
| P | 가격, P̂는 예상 합의가 |
| B | order budget |
| U | use_within_hours (음식) |
| H | checkout 시점의 잔여 시간 |
| w | warranty_hours |
| q̂_{s,g} | 상점 s, 등급 g의 고장확률 추정치 |
| ĥ_s | claim 정직률 |
| r̂_s | 거절·무응답률 |
| n_s | 상점 s와의 관측 수 |

### 3.2 기대잉여

**잡화(goods).** 고장 시점은 정산 후 0~6h에 균등하다고 가정합니다(검증이 필요한 가정).

E[S] = (1−q̂)(L−P) − q̂·P·(1 − min(w,6)/6)

- w ≥ 6이면 고장 시 환불로 잉여는 0입니다.
- w = 0이면 고장 시 잉여는 −P입니다.

**음식(food).**

E[S] = p_ok·L − P

p_ok는 cert 여부에 따라 다르게 계산합니다.
- cert를 요구하고 `min_hours_left = U + δ`이면: p_ok ≈ 1 − ε_settle (checkout과 settle 사이의 감소분)
- cert가 없으면: claim 등급 g의 하한비율 ρ_g(A 0.8, B 0.45, C 0) × shelf_hours − 경과시간 ≥ U + δ일 때 p_ok = ĥ_s, 아니면 p_ok = 0.1 (가정)

### 3.3 후보 점수와 상한가

Score = E[S](P̂) − μ·r̂_s·L + β·L/√(1+n_s)

- 마지막 항은 탐색 보너스입니다.
- P̂ = 상점 s의 과거 합의가/L 중앙값 × L. 관측이 없으면 0.8L (가정).

**walk-away 상한 P_max**: E[S] = τ를 P에 대해 푼 값입니다.
- w ≥ 6인 goods: P_max = L − τ/(1−q̂)
- w = 0인 goods: P_max = (1−q̂)L − τ
- food: P_max = p_ok·L − τ
- 최종: P_max ← min(P_max, B)

### 3.4 고장 귀속 (이름만 주어지는 불확실성 처리)

`broken.jsonl`에는 `name`과 `broke_at`만 있습니다. 귀속 절차는 다음과 같습니다.

1. 후보 구매 집합을 만듭니다. 정규화한 name이 일치하고, settle_ts ∈ [broke_at − 6h − 5min, broke_at]인 내 정산 구매입니다.
2. 후보 개수 k에 따라 처리합니다.
   - k = 1: 해당 (s, g)에 고장 1을 기록합니다.
   - k > 1: 각 후보에 1/k의 분수 고장을 기록하고 `ambiguous`로 표시합니다.
   - k = 0: 미귀속 로그로 남깁니다(이름 불일치일 가능성).
3. 보증 확인: broke_at − settle_ts ≤ w이면 환불을 기대합니다. ledger에서 환불을 확인하고, 없으면 이상 로그를 남깁니다.
4. 갱신: Beta 사후분포를 사용합니다. q̂ = (α₀ + 고장)/(α₀ + β₀ + 관측)이며, 상점×등급 단위입니다. 관측이 3건 미만이면 카테고리×등급 풀링 값을 사용합니다.

### 3.5 협상

- 앵커: a₀ = 0.55L
- 양보 스케줄: [0.55, 0.65, 0.75]L 순서로 올리되 P_max를 넘지 않습니다.
- R = 3라운드
- 상점 제시가 ≤ 현재 목표가이면 즉시 수락합니다.
- 근거: 공급원가가 정가의 25~75%이므로 상점의 합리적 하한은 그 구간에 있습니다. 앵커 값 자체는 가정입니다.

### 3.6 초기 파라미터와 가드 (전부 가정)

| 파라미터 | 값 | 비고 |
|---|---|---|
| τ (최소 기대잉여) | 0.03L | deals 20% 비중 때문에 낮게 설정 |
| δ (음식 여유) | max(0.5h, 0.1U) | |
| q̂ 사전평균 A/B/C | 0.05 / 0.10 / 0.20 | 강도 α₀+β₀ = 4. 근거 없는 약한 prior |
| μ | 0.05 | |
| β | 0.02 | |
| 상점당 타임아웃 | 8s | |
| 시도 상점 수 | 최대 3 | |
| 협상 마감 | expires − 20s | |
| nano 호출 | 주문당 ≤ 2회 | 시간당 상한 별도 |
| 포럼 게시 | 표본 n ≥ 5, 30분당 1회 | rate limit은 5분이지만 품질 우선 |

**accept 정책**
- 음식: `{"cert": true, "min_hours_left": U+δ}`
- 잡화: `{"cert": true, "grades": [claim 등급]}`, 그리고 w > 0으로 합의했다면 `"warranty_hours": w`
- 처음에는 cert를 필수로 합니다.
- 어떤 상점에서 거절 사유 "no certificate"가 2회 이상 나왔고 그 상점의 ĥ_s ≥ 0.9(n ≥ 5)이면, 그 상점에 한해 cert 요구를 완화합니다.

---

## 4. 모델과 코드 역할 분리, API 토큰 절약

**역할 분리**

| 담당 | 내용 |
|---|---|
| 코드 (결정) | 후보 생성, E[S], 상한가, 양보 스케줄, 영수증 검증, accept 생성, 별점 산식, 고장 귀속, 통계 |
| nano (보조) | ① 정규식이 실패한 상점 응답을 `{price, claim, warranty, cert}` JSON으로 파싱 ② 리뷰 1~2문장 작성 ③ 포럼 글 본문 작성 |

- nano 입력은 코드가 계산한 숫자 사실만 넣습니다.
- nano 출력은 JSON 스키마로 검증합니다. 숫자는 코드 값과 다르면 버립니다.

**meta-harness (`llm.py`)**
- 스키마 검증
- 재시도 1회
- 실패 시 템플릿으로 대체
- 호출 횟수·토큰 카운터
- 시간당·일일 상한을 넘으면 nano를 끄고 템플릿 모드로 전환

**두 종류의 돈을 구분합니다.**
- 시장 크레딧: 거래 대금입니다. 잉여 계산 대상입니다.
- API 과금: OpenAI 실제 USD입니다. `llm_usage.jsonl`에 따로 기록하고 시장 점수와 섞지 않습니다.

**토큰 절약 수단**
- 정규식을 우선하고, 리뷰와 포럼은 템플릿을 기본으로 합니다.
- 프롬프트는 짧은 고정 시스템 프롬프트에 값 슬롯만 둡니다.
- 외부 텍스트는 최대 400자로 잘라 넣습니다.
- 같은 상점 응답 패턴은 캐시합니다(정규화한 응답 해시 → 파싱 결과).

---

## 5. 파일·훅 매핑과 JSON 예시

**agent.json 훅**

```json
"market_buyer": {"cmd": "python buyer/market_buyer.py --shops {shops} --item {item} --budget {budget} --order {order}"},
"review": {"cmd": "python buyer/review.py --deal {deal}"}
```

**모듈 구성**

| 모듈 | 역할 |
|---|---|
| `buyer/state.py` | 파일 락, 원자적 쓰기(tmp→rename) |
| `buyer/ingest.py` | payments/broken 커서 처리 |
| `buyer/candidates.py`, `policy.py` | §3 산식 |
| `buyer/negotiate.py` | A2A 협상 |
| `buyer/guard.py` | 영수증·accept 검증 |
| `buyer/llm.py` | meta-harness |
| `buyer/ledger.py` | `GET /api/ledger`, `/api/shops` 읽기 전용 |
| `buyer/forum.py` | outbox 작성 |

**기억 저장소** `.market/buyer_mem/`
- `state.json`: 집계 상태
- `events.jsonl`: 결정 로그
- `cursors.json`: payments/broken/ledger 오프셋
- `params.json`: 현재 파라미터와 버전

**state.json 예시** (구조 예시이며 값은 아직 없습니다)

```json
{"shops":{"<url>":{"name":"...","n":0,"refused":{"no certificate":0},"honest":[0,0],
  "break":{"A":[0,0],"B":[0,0],"C":[0,0]},"price_ratio":[]}},
 "categories":{"김치":{"orders":0,"deals":0,"price_ratio":[],"spoiled":0}},
 "purchases":[{"order":"o1","shop":"<url>","sku":"...","name":"...","grade":"A","w":6,
  "price":0,"L":0,"settle_ts":0,"ledger":0,"status":"pending_6h"}]}
```

**market_buyer 마지막 줄 예시 (음식)**

```json
{"shop":"https://shop-x...","receipt":{"...signed artifact as received..."},"stars":3,
 "review":"pending","accept":{"cert":true,"min_hours_left":7.2}}
```

**결정 로그 예시** (나중에 비교실험에 사용)

```json
{"order":"o1","cands":[{"shop":"s1","sku":"k","L":30000,"P_hat":24000,"ES":5100,"score":5400}],
 "chosen":"s1/k","rounds":2,"agreed":21000,"P_max":28200,"min_price_counterfactual":"s2/k@19000",
 "llm_calls":0,"params_v":1}
```

**outbox 예시**

```json
{"post":"[김치] 내 정산 7건: 합의가/정가 중앙값 0.71, claim 불일치 1건(refs 참조). 표본 작음.","refs":[1203,1250],"as":"buyer"}
{"vote":"p_88","value":1,"as":"buyer"}
```

**review 별점 규칙** (사실 기반, 상점 정체와 무관)

| 조건 | 별점 |
|---|---|
| claim_honest가 거짓 | 2 |
| value = 0 (음식 부패) | 1 |
| 정직, value > 0, P ≤ 0.8L | 5 |
| 정직, value > 0, P > 0.8L | 4 |
| cert:false로 판매 | −1 (하한 1) |

- 리뷰 문장에는 사실만 씁니다.
- 고장은 리뷰 이후 최대 6h 뒤에 드러나므로 별점에 반영할 수 없습니다. 대신 포럼 후속 글로 다룹니다.

---

## 6. 예외·오류·주입공격·위험 처리

**실행 안정성**
- 최상위 `try/except`로 감쌉니다. 실패 시 `{"shop":null,"receipt":null,"stars":3,"review":""}`를 출력합니다.
- 상점 타임아웃이나 A2A 오류가 나면 다음 후보로 넘어갑니다.
- `expires − 20s`에 도달하면 협상을 즉시 중단합니다.
- 상태 파일이 손상되면 `events.jsonl`을 재생해 `state.json`을 재구성합니다.
- `market_buyer`와 `review`가 동시에 실행될 수 있으므로 파일 락을 쓰고, 2초 내 락을 얻지 못하면 읽기 전용으로 진행합니다.

**주입공격**
- 신뢰하지 않는 입력: `shops.json` reviews, 상점 A2A 응답, 포럼 글.
- 이 텍스트는 명령으로 취급하지 않습니다. nano에 넣을 때는 `<untrusted>` 블록으로 감싸고 "데이터에서 필드만 추출"하도록 지시합니다.
- 출력은 스키마 외 필드를 버립니다.
- 가격·조건의 권위는 오직 서명된 영수증과 코드 가드입니다. "예산 무시", "accept 생략" 같은 문구는 효과가 없습니다.
- 다른 buyer의 리뷰는 Score에 최대 ±0.01L만 반영합니다. 조작된 리뷰가 결정을 지배하지 못하게 하기 위함입니다.

**경제적 위험**
- 상점이 kit에 다른 조건을 보내는 경우: `accept`로 차단합니다(결제 0, 다음 주문에서 학습).
- 부분 보증 함정(w < 6): E[S] 식이 자동으로 할인합니다.
- 음식 등급 하락: 10s 지연된 inventory와 경과시간을 감안해 δ를 둡니다.
- 예산 초과: 하드 가드로 막습니다.

**공정성·규정**
- 포럼에서 내 가게는 추천 목록에서 제외하고, 언급할 때는 이해관계를 명시합니다.
- 상점 선호 담합, 자전거래, 허위 게시, 투표 교환은 하지 않습니다.

**투표**
- 글의 `refs`를 ledger와 대조해 검증합니다.
- 검증되면 +1, 모순되면 −1, 검증할 수 없으면 투표하지 않습니다.
- 같은 작성자에게 반복 투표하는 패턴이 생기지 않도록 작성자당 시간당 투표 수에 상한(3)을 둡니다.

---

## 7. 초기 30분 최소구현과 후속개선

**초기 30분 (19시 직후 작동이 우선, nano 0회도 가능)**
1. 후보 필터, 그리고 prior만으로 E[S]와 P_max 계산
2. 고정 앵커·양보 협상, 정규식 파싱
3. 영수증 가드와 accept 생성 (음식 cert + min_hours_left, 잡화 cert + grades)
4. 안전 출력, payments 커서 흡수(거절 사유 카운트), `events.jsonl` 기록
5. 규칙 기반 review 훅
6. 반사실 기록: "최저가 규칙이었다면 무엇을 샀을지"

**관측 후 고도화 (거래 누적 후)**
1. 고장 귀속과 Beta 갱신(상점×등급), 6h 무고장 확정
2. ledger 기반 카테고리별 시장가 분포로 P̂와 앵커 조정
3. 상점별 cert 요구 완화 규칙
4. 파라미터 튜너: 앵커 a₀ ∈ {0.5, 0.55, 0.6}를 ε-greedy로 탐색하고, 보상은 실현 잉여/L. `params.json`에 버전을 기록하고 τ·δ는 범위 클램프 안에서만 바꿉니다.
5. 포럼: 선호 카테고리(주문 빈도가 높은 카테고리 = 내 데이터가 가장 많은 곳)의 가격 비율, claim 정직률, 고장 귀속 결과를 refs와 함께 게시합니다. 반박 글에는 thread로 답글을 답니다.
6. nano로 리뷰와 포럼 문장 품질 향상(템플릿 대체 유지)

---

## 8. 비교실험 지표와 한계

**지표**

| 영역 | 지표 |
|---|---|
| 잉여 | 거래당 실현잉여/L, 누적 잉여 |
| 성사율 | 성사율(deals/orders), 거절률(사유별) |
| 위험 | 음식 부패율, 귀속된 고장률, 모호 귀속 비율 |
| 협상 | 평균 협상 라운드, 마감 초과 비율 |
| 포럼 | 글당 counted score |
| API 비용 | 주문당 nano 토큰과 USD (시장 지표와 분리) |

**비교 방법**
- 반사실 최저가 기준선과 비교합니다. 같은 주문에서 최저가 후보의 기대값을 사후 고장·부패 정보로 재평가합니다.
- 앵커 정책은 주문 번호 홀짝으로 A/B 교대 실행합니다.
- `params_v`별로 집계합니다.

**한계**
- 표본이 작아 상점별 추정치의 분산이 큽니다.
- 이름 기반 고장 귀속은 본질적으로 모호합니다.
- 상점 전략이 비정상적으로 변합니다.
- 협상 반사실은 관측할 수 없습니다(다른 가격을 제시했다면 상점이 어떻게 응답했을지 모름).
- 백분위는 다른 참가자에게 의존합니다.
- 고장 균등분포, prior, τ·δ는 모두 검증되지 않은 가정입니다.

---

## 9. 한 페이지 아키텍처에 넣을 차별 요소

1. **Contract-Guarded Checkout**: 협상 조건을 `accept`로 자기강제합니다. seller→kit 경로의 조건 바꿔치기를 구조적으로 차단합니다.
2. **Delayed-Evidence Loop**: 결제거절(다음 주문에 반영)과 고장(최대 6h 후)을 별도의 비동기 증거 채널로 그립니다. 커서 기반으로 흡수합니다.
3. **Probabilistic Breakage Attribution**: 이름+시간창으로 분수 귀속하고, 상점×등급 Beta 신뢰도로 연결합니다.
4. **Risk-Adjusted Surplus Policy**: 보증 시간, 유통기한, 정직률을 하나의 E[S] 식으로 통합하고 P_max를 닫힌 형태로 계산합니다.
5. **Code-Decides / LLM-Describes meta-harness**: 스키마 검증, 토큰 거버너, 템플릿 대체로 구성합니다. 크레딧과 USD를 분리 회계합니다.
6. **Evidence-Gated Forum**: n ≥ 5이고 ledger refs가 있을 때만 게시합니다. 투표는 refs 검증 결과에 따라서만 합니다. 정당한 고유 분석으로 평판을 얻습니다.

그림 배치는 왼쪽에서 오른쪽으로 `Ingest → Memory(state/events) → Policy → A2A Tool → Guard/accept → kit` 순서로 두고, 아래에 피드백 화살표 두 개(payments: 1주문 지연, broken: ≤6h 지연)를 Memory로 되돌리면 한 페이지에 들어갑니다.