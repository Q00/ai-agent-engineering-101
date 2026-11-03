# Week 08 — Sealed-budget dual-control market agent

> 상태: Round 3 실행 전 설계 보고서. 아래 `Round 3 결과`의 실거래 수치와
> 인용은 `logs/market-kit.log`가 쌓인 뒤 제출 직전에 갱신한다.

## 1. 문제와 가설

Round 2의 buyer는 주문 예산을 그대로 LLM에게 보여 주었고, 선택과 협상에
LLM 호출을 각각 써서 harness 호출 한도도 자주 소진했다. Round 3에서는 실제
재고, 공급가, 식품 만료, 공산품 등급/고장/보증과 ACP/AP2 정산까지 함께 다뤄야
한다. 이 에이전트가 검증하려는 질문은 다음과 같다.

> 정확한 예산과 서명 권한을 LLM에서 격리하면서도 buyer의 체결률을 유지하고,
> seller는 5,600개 상품 중 관찰된 소진과 만료 위험에 따라 재고와 가격을
> 조절할 수 있는가?

핵심 가설은 세 가지다.

1. 코드 기반 포트폴리오 라우터가 shop 쏠림과 LLM 호출 수를 줄인다.
2. 정확한 예산을 모르는 협상기와 예산을 가진 결정론적 guardian을 분리하면
   예산 누출·초과 지불을 막을 수 있다.
3. 소량 탐색 restock과 재고 소진 메모리는 공개되지 않은 수요 가중치를
   온라인으로 학습하며, 만료 직전 식품은 원가 이하 청산이 합리적일 수 있다.

세부 정책과 Round 2 기준선은 [STRATEGY.md](STRATEGY.md)에 기록했다.

## 2. 부품별 설계

### 2.1 실행 모델과 meta-harness

`agent.json`은 kit v4가 호출하는 여섯 개 경계를 정의한다. seller와 buyer의
최종 권한 판단은 Python 규칙으로 수행한다. 기존 협상 대화가 필요한 경로만
Claude Code의 Haiku(구독)를 제안 생성기로 사용한다. 호출 횟수 제한은 두지 않고
호출 하나를 작게 유지한다: `effort low`, 도구 없음, 최대 `$0.005/call`, 재시도
0회, 120자 이하의 JSON만 요구한다. 구독 한도 도달, timeout 또는 비정상 출력이면 exact budget을 모르는 결정론적 reference-price
fallback이 제안한다. 따라서 모델 출력은 명령이 아니라 검증 전 후보다.

| 경계 | 구현 | 역할 |
|---|---|---|
| `seller` | `seller_r3.py` | 재고 단위 선택, 가격 실험, 협상, 영수증 서명 |
| `market_buyer` | `market_buyer.py` | SKU/shop 라우팅, 협상, `accept` 품질 조건 출력 |
| `buyer` | `buyer.py` | Round 2 호환 직접 A2A buyer |
| `restock` | `restock.py` | 소진 기반 재고 구매 제안 |
| `review` | `review_r3.py` | 정산 사실 평가와 게시판 기록 |
| `harness` | `harness.py` | 제한된 모델 호출과 JSON 추출 |

모델은 예산, 개인키, 결제 권한, 재고 구매 권한을 갖지 않는다. 서명은
`seller_guard.py`, 예산 승인은 `buyer_policy.py`의 `MandateGuardian`, 실제
ACP/AP2 결제와 재고 이동은 공식 kit v4가 맡는다.

### 2.2 buyer: proposal과 authority의 분리

buyer의 흐름은 `Scout → PortfolioRouter → Negotiator → MandateGuardian →
Receipt/Quality Auditor`다.

- Scout는 주문의 acceptable SKU, shop stock, catalog list price, 개인 거래
  결과를 읽는다. forum/review 문장은 신뢰하지 않고 참고 신호로만 쓴다.
- PortfolioRouter는 체결률, 영수증 무결성, 가격 가치, 탐색 보너스와 최근
  50회 집중도를 이용해 후보를 정한다. 정확한 예산 대신 guardian이 먼저
  걸러 준 `affordable_offers`만 받는다.
- Negotiator가 보는 것은 기준 가격과 협상 transcript뿐이다. exact budget은
  보이지 않는다.
- MandateGuardian만 exact budget을 가진다. 처음 두 차례에는 기준가 기반
  제안만 승인·대체하고, 세 번째 이후에만 마지막 수단으로 ceiling을 쓸 수
  있다. 초과 가격은 항상 거부한다.
- checkout 전 식품에는 `cert`와 `min_hours_left`, 공산품에는 `cert`와 A/B
  `grades` 조건을 붙인다. SKU·가격이 합의와 다른 영수증은 정산하지 않는다.

### 2.3 seller: inventory, experiment, risk guard

`seller_r3.py`는 `.market/inventory.json`의 실제 unit에서 주문과 일치하는
SKU를 고르고 열린 task마다 한 unit만 예약한다. 같은 SKU에서는 A, B, C
순으로 선택한다. 등급 claim은 unit의 실제 grade에서 만들고 인증서를 항상
요구한다. 보증은 A 6시간, B 3시간, C 0시간이다.

task hash로 `volume`/`margin` arm을 안정적으로 배정해 재실행해도 같은 실험군을
쓴다. 정상 상품은 원가와 목표 이익을 지키되 list price 근처로 제한한다.
식품은 만료가 가까울수록 할인하고, 곧 만료될 unit만 전액 폐기보다 손실이
작을 때 원가 이하 가격을 허용한다. `seller_guard.py`는 shop, SKU, task,
unit, 가격, claim, warranty와 cert를 하나의 서명 대상에 묶는다.

### 2.4 restock과 메모리

`restock.py`는 이전 inventory snapshot을 `.market/restock_memory.json`에
보관한다. 만료 전에 사라진 unit은 수요 후보로, 만료된 식품은 waste로
구분한다. 처음에는 category를 넓게 탐색하고 이후에는 관찰된 depletion,
현재 stock scarcity, ledger를 참조한 forum 신호에 가중치를 둔다.

한 번의 호출은 최대 6개 SKU line, 추정 잔액의 12% 또는 600,000 중 작은
금액만 제안한다. 식품은 1개, 공산품은 2개 단위의 작은 lot을 기본으로 해
미확인 수요에 자본이 잠기는 것을 제한한다. 파산은 잔액이 파산선 아래이고
판매 가능한 unit도 없을 때만 요청한다.

### 2.5 review와 게시판

`review_r3.py`는 settled deal의 인증된 grade, claim honesty, catalog 대비 가격을
점수화한다. 거짓 claim이나 가치가 0인 거래는 1점, 정직하고 가치가 좋은
거래는 최대 5점이다. ledger reference가 있는 정산 사실만 outbox에 쓰고
5분에 한 게시물이라는 제한을 로컬 상태로 지킨다. 광고 문장을 사실로
재사용하지 않는다.

## 3. 컨텍스트와 관측 가능성

kit가 갱신하는 `.market/order.json`, `shops.json`, `inventory.json`,
`catalog.json`, `demand.json`, `forum.json`, `deal.json`을 각 hook이 필요한
범위에서만 읽는다. 장기 상태는 buyer 거래 메모리, seller 거래 메모리,
restock snapshot으로 분리한다. 개인키와 `.market` runtime 상태는 Git에
커밋하지 않는다.

각 라우팅·예산·가격 결정은 `[strategy]` JSON 한 줄로 기록한다. 공개 특징,
실험 arm, guard 결정과 결과는 남기되 개인키는 남기지 않는다. 정확한 budget은
kit가 소유한 원 주문 로그에는 나타날 수 있지만 strategy event에는 복제하지
않는다. 실패 로그도 수정하거나 삭제하지 않는다.

## 4. 아키텍처와 kit 기본형의 차이

`architecture.pdf`의 실선은 실행/데이터 흐름, 점선은 authority boundary를
뜻한다. 기본형은 하나의 seller 가격 규칙과 단순한 buyer 선택, 기본 restock을
사용한다. 이 제출물은 다음을 추가했다.

- LLM 제안과 예산 승인·서명·결제를 분리한 dual-control 경계
- acceptable SKU와 catalog value를 이용하는 code-first 라우터
- unit 원가/등급/만료를 반영한 두 가격 arm과 spoilage guard
- inventory snapshot의 depletion/waste를 구분하는 restock memory
- 인증된 정산 사실에만 반응하는 review/forum 도구

## 5. 현재 재현 결과

Round 3 시장 접속 전 로컬 검증 결과다.

| 검증 | 결과 | 근거 |
|---|---:|---|
| Python 정책 단위 테스트 | 36/36 통과 | 7개 `test_*.py` |
| 공식 `market_test_week07.py` | 25/25 통과 | `test-report.json`, `logs/market-test.log` |
| v4 hook 계약 | 통과 | `agent.json`의 `{order}`, `{state}`, `{deal}` |
| bounded Haiku smoke test | 통과 (2026-11-03) | `haiku` = `claude-haiku-5-5`; 흥정 1회 약 770 in / 95 out token(thinking 80), 가게 선택 1회 약 4.7k in / 244 out token, $0.0016 |

공식 테스트에는 seller card/open/deal/bargain/signature와 buyer deal,
budget hold, review-based selection이 각각 세 번씩 포함된다. 이 검증은 프로토콜
호환성 결과이며 Round 3의 실제 ACP/AP2 정산 실적을 대신하지 않는다.
Codex CLI 경로도 비교했으나 한 줄 JSON에 13,448 token을 사용해 비용 목표와
맞지 않아 폐기했다. OpenAI API 경로는 현재 key가 401이라 폐기하고, 기존 Haiku를
작은 prompt로 사용하는 쪽으로 되돌렸다. 하루 200회 quota는 자정 후 약 1시간 만에
소진되어 나머지 시간이 fallback이 되므로 제거했다.

재현 명령:

```bash
python -m unittest discover -s submissions/26510129/week-08 -p 'test_*.py'
python scripts/market_test_week07.py submissions/26510129/week-08
```

## 6. Round 3 결과 — 운영 후 갱신

아래 항목은 실제 kit v4 로그와 Players board를 확인한 뒤에만 채운다.

- shop settled: **TODO / 최소 20**
- buyer settled: **TODO / 최소 20**
- Players shop ALL 순위: **TODO**
- Players buyer ALL 순위: **TODO**
- 주요 settlement 거부 사유와 횟수: **TODO**
- `logs/market-kit.log` 인용: **TODO (`[acp] settled`, `[acp] paid`,
  `verified True`, `[supply]` 줄)**
- volume/margin arm별 체결률·마진, food spoilage/liquidation: **TODO**

운영 중에는 초기 표본을 먼저 보존하고, 정산/거부 근거를 본 뒤 변수 하나만
바꿔 별도 커밋한다. 마감 시점에는 실패한 시도도 포함해 이 절을 갱신한다.
