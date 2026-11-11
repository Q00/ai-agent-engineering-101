# Week 08 — Sealed-budget dual-control market agent

> 상태: Round 3 운영 중(11-03 20:55 개장). 6절의 수치는 11-06 12:58 기준 중간값이고,
> 제출 직전 종료 시각의 board로 갱신한다. 7절은 운영 중 생긴 사고와 고친 기록이다.

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
0회, 120자 이하의 JSON만 요구한다. 입력도 작게 유지한다: 매 round 전체 transcript를
다시 보내는 대신 제안 이력(숫자 쌍)과 가게의 마지막 메시지(240자까지)만 보낸다
(`buyer_policy.situation`). 11-03 실측으로 round 6 호출이 2.5k자/1,547 token에서
1.1k자/1,069 token으로 줄고 결정(103)은 같았다. 구독 한도 도달, timeout 또는 비정상 출력이면 exact budget을 모르는 결정론적 reference-price
fallback이 제안한다. 따라서 모델 출력은 명령이 아니라 검증 전 후보다.

시장은 무엇이 팔리는지 공개하지 않고, 공개 ledger의 정산 기록만이 수요 신호다
(9주차 노트). `ledger.py`는 `GET /api/ledger?after=-500`에서 최근 24시간 정산을
SKU별 건수·중앙값, 카테고리별 건수·정가 대비 비율로 집계해 `.market/ledger_stats.json`에
쓴다. HTTP 호출은 60초마다 도는 `restock.py`가 한 번만 하고, seller는 파일만 읽어
가격 결정이 네트워크를 기다리지 않는다. restock은 팔리는 카테고리에 가산점을 주고
팔리는 SKU를 먼저 고르며, **시장 체결가가 기대 원가(정가의 약 0.6)보다 낮은 SKU는
사지 않고** `skipped_below_cost`로 기록한다. seller의 ask는 그 SKU(없으면
카테고리)의 체결가를 따르되(volume arm 0.97배, margin arm 1.05배)
`[원가×1.03, 등급 상한]` 안에 머문다. 주문 없이 도는 restock이 이 에이전트의
pro-active한 절반이다.

2일차(11-04 17:10) ledger 4만 건(2.3시간) 조사: 시장 매출의 절반이 소파(정산
405건, 중앙값 1.7M, 591M)였고 매트리스(1,151건, 305k, 383M)가 그 다음이었다.
1위 가게는 1M짜리 lot을 2.3시간에 180M어치 샀다. 같은 시간 이 가게는 소스·곡물·
즉석식품을 lot당 1만 원에 106번 샀다. 원인은 restock이 정산 "건수"로 카테고리를
고르고 카테고리 안에서는 "제일 싼 SKU"를 골랐기 때문이다. 그래서 (1) 카테고리는
정산 매출로, 내 판매 기록도 가격 수준을 곱해 평가하고, (2) SKU는 그 카테고리가
실제로 정산되는 가격의 0.5~1.5배 띠 안(주문이 받아들이는 범위)에서 고르며, (3)
상한은 자본(잔고+재고 원가) 기준으로 올렸다: 재고 50%, 실행당 잔고의 30%, 유닛
하나는 잔고의 15% 이하, 한 카테고리는 재고 상한의 35% 이하. ledger 통계는 분당
한 번 증분으로 받아 24시간 창을 유지한다(`.market/ledger_cache.json`; 500건 한
페이지는 이 시장의 2분도 안 됐다). seller는 비식품을 3일째부터 하루 5%씩 내린다
(보관비 1%/일). 적용 직후 dry run: 매트리스 2, 2.32M 소파 1, 커튼, PC 소품.

이틀째 저녁(11-04 22:40) 첫 소파가 2분 만에 buyer 셋을 불러 1.74M에 팔린 뒤 유닛
상한을 자본의 20%로 올렸고(소파 2~3대 동시 보유), 상위 3개 매출 카테고리는 내 성사율이
50% 이상이면 재고 상한의 55%까지 둔다. 반대로 유닛당 기대 마진이 5천 원 미만인
카테고리는 탐색 한 번만 하고 보충하지 않는다(330개 재고의 대부분이 2~5천 원짜리였다).
종료는 코드에 있다(`round_clock.py`): 11-17 23:59 이후 재고는 가치가 0이므로 36시간
전부터 식품 매입, 24시간 전부터 전체 매입을 멈추고, seller는 24시간 전부터 시장가
−5%, 12시간 전부터 원가 이하(바닥 원가×0.6), 마지막 3시간은 1원 위면 판다.

전략 값 일곱 개(buyer 탐색률, 첫 제안 할인폭; seller 두 arm의 마진, B등급 상한,
volume arm 비율, 시장가 대비 마크업)는 자정에 한 번 `tune.py`가 조정한다. 코드가 전날 24시간의 buyer 기록, seller 이벤트,
내 가게 리뷰를 약 1k자로 집계해 Opus(`effort high`)에 보내고, 모델은 허용 범위
안의 변경만 JSON으로 제안한다. 코드가 범위를 검증해 `.market/strategy_params.json`에
쓰고 buyer와 seller는 주문마다 그 파일을 읽는다(재시작 없음). 호출은 하루 1회
약 1.3k 토큰이다. 예산을 쥔 MandateGuardian, 영수증 검사, 서명은 목록에 없어
모델이 AP2 경계를 옮길 수 없다. 코드 편집은 시키지 않는다: 실행 중 깨질 위험과
설명 불가능성이 비용보다 큰 문제다. 11-03 dry run(`logs/tune-dryrun-2026-11-03.txt`)에서
모델은 10/31 구독 한도 소진으로 생긴 walk-away 80%를 보고 첫 제안 할인폭을
0.15→0.10으로 줄이자고 했다. 원인은 전략이 아니라 harness 공백이었으므로, 요약에
"제안 없이 walk-away한 수"를 넣고 그 경우는 조정 근거로 쓰지 말라고 명시했다.

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
- Negotiator가 보는 것은 기준가, 제안 이력(숫자 쌍), 가게의 마지막 메시지
  (240자까지)뿐이다. exact budget은 보이지 않는다.
- 주문 시점에 재고가 없는 가게(첫 제안 전에 거절)는 `no_stock`으로 기록하고
  가게 점수에서 제외하며, 같은 주문 안에서 최대 세 가게까지 옮겨 간다. Round 3
  개장 1분차(11-03 20:55)에 선택한 가게의 재고가 shops.json보다 먼저 사라져
  주문이 그대로 끝난 것을 보고 추가했다.
- 정산 후 `review_r3.py`가 인증된 등급, claim 진위, value, 보증, ledger 번호를
  해당 주문의 메모리 줄에 써 넣는다(`memory_sync.py`). kit이 결제를 거부한 주문은
  `payments.jsonl`에서 읽어 `unpaid`로 바꾸고, 고장 난 물건은 `broken.jsonl`을
  최근 정산 구매와 이름으로 맞춰 보증 환불 여부와 함께 기록한다. 가게 integrity는
  영수증 일치뿐 아니라 거짓 claim과 무보증 고장을 센다. 메모리는 영수증이
  아니라 시장이 인증한 사실로 갱신된다.
- MandateGuardian만 exact budget을 가진다. 처음 두 차례에는 기준가 기반
  제안만 승인·대체하고, 세 번째 이후에만 마지막 수단으로 ceiling을 쓸 수
  있다. 초과 가격은 항상 거부한다. 11-03 21:09에 정가 28,900인 물건을 30,500에
  산 뒤로 한도는 `min(예산, 정가)`이고, ask보다 높은 제안은 ask로 깎는다.
  기준가는 카테고리 중앙값이 아니라 그 SKU의 ledger 체결가×0.95(정가 90% 상한)다.
- 흥정이 손해인 시장이다(주문의 45%가 못 채워지고 한 유닛에 몇 분 안에 buyer
  여럿이 붙는다). 그래서 모델 호출 없이 끝내는 경로를 두었다: ask가 기준가 수준
  (×1.05)이면 즉시 그 값, 가게가 "Lowest for this unit: N"을 말하며 닫으면 새
  task를 열어 N, 내 제안이 ask의 98% 안이면 ask, 가게가 단골 이름을 묻는
  문구("reply 'buyer: <name>' and VIP -3%")에는 이름으로 답한다. 11-05 하루
  동안 즉시 수락 244회, 갭 메우기 106회, 단골 답장 358회가 쓰였다.
- 자세(regime)는 시간이 아니라 카테고리의 희소도로 정한다. 주문은 24시간 내내
  시간당 150건으로 평평해서 바쁜 시간이 없고, 소파 주문은 66%가 어느 가게에도
  재고가 없는 반면 김치는 0%다. 최근 60회 방문 중 "재고 없음" 비율이 60% 이상이면
  즉시 수락 폭을 +10%로 넓히고 첫 제안을 ask에 붙이며, 30% 이하이면 기준가
  이하만 받고 더 깊게 깎는다.
- 가게 점수에는 shops.json이 보여 주는 재고 수량(+0.10까지)이 들어가고, 마지막
  네 번 방문이 전부 무응답(재고 없음, 첫 메시지 거절)인 가게는 다시 답할 때까지
  건너뛴다. 영수증 검사는 요청 SKU뿐 아니라 주문의 허용 SKU, 요청한 카테고리의
  SKU를 모두 정당한 영수증으로 본다(7절의 사고). 블랙리스트는 조작 메시지 1회,
  또는 부정직 2건 이상이 그 가게 거래의 10% 이상일 때만이다.
- checkout 전 식품에는 `cert`와 `min_hours_left`, 공산품에는 A/B `grades`
  조건을 붙인다(인증서 요구는 비식품에서 뺐다: value는 등급과 무관하게 정가이고
  11-04에 그 이유만으로 20건이 거부됐다). 흥정 전에 가게가 밝힌 등급·남은
  수명이 주문을 못 채우면 취소하고 다음으로 간다(하루 500여 건, 모델 호출 없음).

### 2.3 seller: inventory, experiment, risk guard

`seller_r3.py`는 `.market/inventory.json`의 실제 unit에서 주문과 일치하는
SKU를 고르고 열린 task마다 한 unit만 예약한다. 같은 SKU에서는 A, B, C
순으로 선택한다. 등급 claim은 unit의 실제 grade에서 만들고 인증서를 항상
요구한다. 보증은 A 6시간, B 3시간, C 0시간이다.

task hash로 `volume`/`margin` arm을 안정적으로 배정해 재실행해도 같은 실험군을
쓴다(비율은 tuner 값; 11-05 자정 Opus가 0.60→0.45로 내렸다: margin arm이
성사율 거의 같은데 마진 2배). ask는 ledger 시장가×arm(0.97/1.05)×마크업이고,
restock이 분마다 쓰는 `category_pressure.json`(최근 6시간 카테고리별 open·성사율)로
성사율 60% 이상이면 +5%, 40% 미만이면 −3%와 volume arm 가격을 쓴다(주스는
유닛당 buyer 4.3명인데 성사율 23%였다: 줄을 선 게 아니라 떠난 것이라 "open 수"는
신호가 아니다). 비식품은 3일째부터 하루 5%씩 내린다(보관비). 식품은 만료가
가까울수록 할인하고, 곧 만료될 unit만 전액 폐기보다 손실이 작을 때 원가 이하를
허용한다. `seller_guard.py`는 shop, SKU, task, unit, 가격, claim, warranty와
cert를 하나의 서명 대상에 묶는다.

### 2.4 restock과 메모리

`restock.py`는 이전 inventory snapshot을 `.market/restock_memory.json`에
보관한다. 만료 전에 사라진 unit은 수요 후보로, 만료된 식품은 waste로
구분한다. 처음에는 category를 넓게 탐색하고 이후에는 관찰된 depletion,
현재 stock scarcity, ledger를 참조한 forum 신호에 가중치를 둔다.

처음엔 한 번에 6 line, 잔액의 12%, 재고 원가 상한 1.25M이었다. 2일차 조사
(2.1절) 뒤 카테고리는 ledger 정산 매출로, SKU는 카테고리 체결가의 0.5~1.5배
띠 안에서 고르고, 상한은 자본 기준(재고 50%, 실행당 잔고 30%, 유닛 20%,
카테고리 35%/상위 55%)이다. 식품은 1개, 비싼 유닛(잔고의 5% 초과)은 1개, 나머지
공산품은 2개 lot이다. 파산은 잔액이 파산선 아래이고 판매 가능한 unit도 없을
때만 요청한다. 같은 60초 루프가 forum 시세 글(2.5절)도 쓴다.

### 2.5 review와 게시판

`review_r3.py`는 settled deal의 인증된 grade, claim honesty, catalog 대비 가격을
점수화한다. 거짓 claim이나 가치가 0인 거래는 1점, 정직하고 가치가 좋은
거래는 최대 5점이다. ledger reference가 있는 정산 사실만 outbox에 쓰고
5분에 한 게시물이라는 제한을 로컬 상태로 지킨다. 광고 문장을 사실로
재사용하지 않는다.

9주차의 질문("묻지 않았는데 쓴 글은 매출이 될까, 소음이 될까")에 대한 답으로,
`forum_post.py`는 아무도 묻지 않을 때도 글을 쓰되 정보가 있을 때만 쓴다:
24시간 ledger 캐시에서 매출이 큰 카테고리 하나를 골라 정산 건수, 체결가
중앙값과 정가 대비 비율, 최다 판매 SKU, ledger 번호 세 개를 한 줄로 올린다.
board에서 표를 받는 글이 정확히 이 모양이었다(11-05). 5분 제한은 review 글과
`review_state.json`으로 공유하고, 같은 카테고리는 3시간 안에 반복하지 않는다.

## 3. 컨텍스트와 관측 가능성

kit가 갱신하는 `.market/order.json`, `shops.json`, `inventory.json`,
`catalog.json`, `demand.json`, `forum.json`, `deal.json`, `payments.jsonl`,
`broken.jsonl`을 각 hook이 필요한 범위에서만 읽는다. 모델 호출은 매번 새
세션이라 모델 자체는 아무것도 기억하지 않는다. 기억은 전부 파일이고 코드가
읽는다.

| 파일 | 쓰는 곳 | 읽는 곳 | 내용 |
|---|---|---|---|
| `buyer_memory.json` | market_buyer, review hook, memory_sync | router, guardian 기준가, 흥정 기록, tuner | 주문당 1줄; 정산 후 인증 사실로 갱신 |
| `restock_memory.json` | restock | restock | 카테고리/SKU 소진, 폐기, unit snapshot |
| `seller_events.jsonl` | seller | tuner | 날짜 붙은 open/deal/reject/counter |
| `ledger_cache.json`, `ledger_stats.json` | restock (60초, 증분) | restock, seller, buyer 기준가, forum_post | 시장 24시간 정산과 요약 (캐시, 기억 아님) |
| `category_pressure.json` | restock (60초) | seller | 내 가게의 최근 6시간 카테고리별 open·성사율 |
| `strategy_params.json`, `tune_history.jsonl` | tuner | buyer, seller | 조절값 7개와 변경 이력 |
| `review_state.json` | review hook, forum_post | 둘 다 | 마지막 게시 시각, 카테고리별 시세 글 시각 |

개인키와 `.market` runtime 상태는 Git에 커밋하지 않는다.

각 라우팅·예산·가격 결정은 `[strategy]` JSON 한 줄로 기록한다. 공개 특징,
실험 arm, guard 결정과 결과는 남기되 개인키는 남기지 않는다. 정확한 budget은
kit가 소유한 원 주문 로그에는 나타날 수 있지만 strategy event에는 복제하지
않는다. 실패 로그도 수정하거나 삭제하지 않는다.

## 4. 아키텍처와 kit 기본형의 차이

`architecture.pdf`의 실선은 실행/데이터 흐름, 점선은 authority boundary를
뜻한다. 기본형은 하나의 seller 가격 규칙과 단순한 buyer 선택, 기본 restock을
사용한다. 이 제출물은 다음을 추가했다.

- LLM 제안과 예산 승인·서명·결제를 분리한 dual-control 경계
- 세 개의 시계: 턴마다 Haiku가 제안(약 900자), 분마다 코드가 ledger를 읽고
  주문 없이 restock(모델 없음), 하루 한 번 Opus meta-harness가 범위 안에서
  조절값 5개를 조정(약 1.3k 토큰)
- acceptable SKU와 catalog value를 이용하는 code-first 라우터
- 공개 ledger의 정산을 수요·가격 신호로 쓰는 ledger reader (restock과 가격이
  공유)
- unit 원가/등급/만료를 반영한 두 가격 arm과 spoilage guard
- inventory snapshot의 depletion/waste를 구분하는 restock memory
- 정산 사실이 메모리로 되돌아오는 write-back(memory_sync): 거짓 claim, 결제
  거부, 무보증 고장이 가게 점수에 반영
- 인증된 정산 사실에만 반응하는 review/forum 도구, 그리고 ledger로 먼저 말을
  거는 시세 글
- 시간이 아니라 카테고리 희소도로 바뀌는 자세(buyer: 즉시 수락 폭·첫 제안,
  seller: 성사율에 따른 ±가격)와 모델 호출 없이 끝내는 결정 경로
- 종료 시각이 코드에 있는 청산 일정(round_clock)

## 5. 현재 재현 결과

Round 3 시장 접속 전 로컬 검증 결과다.

| 검증 | 결과 | 근거 |
|---|---:|---|
| Python 정책 단위 테스트 | 91/91 통과 (11-06) | 13개 `test_*.py` |
| 공식 `market_test_week07.py` | 25/25 통과 | `test-report.json`, `logs/market-test.log` |
| v4 hook 계약 | 통과 | `agent.json`의 `{order}`, `{state}`, `{deal}` |
| nightly tuner dry run | 통과 (2026-11-03) | `tune.py --dry-run --hours 96`: Opus 5.6s, 입력 1,020자, 범위 안 제안 1건, 파일은 쓰지 않음 (`logs/tune-dryrun-2026-11-03.txt`) |
| bounded Haiku smoke test | 통과 (2026-11-03) | `haiku` = `claude-haiku-5-5`; CLI 고정 overhead 약 670 token(캐시됨), 흥정 1회 약 860자 입력, 출력 약 100-170 token(thinking 포함), 호출당 $0.0003-0.0006 |

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

## 6. Round 3 결과 — 중간 (11-06 12:58 board), 종료 시 갱신

| | 11-04 00:20 | 11-05 00:20 | **11-06 12:58** |
|---|---|---|---|
| shop 정산 / 순이익 / 매출 | 163 / +0.9M / 7.8M | 1,565 / +9.3M / 116M | **3,271 / +14.9M / 239M**, 4.71★, 21/82위 |
| buyer 결제 / surplus | 98 / 2.2M | 1,086 / 20.5M | **2,113 / 123.4M**, 30/123위 |

기본 조건(양쪽 20건)은 첫날 넘었다. 종료 시 채울 것: 최종 순위, 거부 사유 집계,
`[acp] settled`/`[acp] paid`/`[supply]` 로그 인용, arm별 체결률·마진, 청산 결과.

## 7. 운영 중 사고와 수정 (시간순, 커밋은 `git log`)

- **11-03 21:05 restock 과소비**: 10분에 500만 중 110만을 재고에. 140개
  카테고리 전부에 "재고 0이면 탐색" 보너스. → 재고 상한과 탐색 조건(2.1절).
- **11-03 21:09 정가 초과 구매**: 28,900짜리를 30,500에. 카테고리 기준가가 SKU
  정가보다 높았고 모델이 ask보다 높게 제안. → 한도 = min(예산, 정가), ask 초과는
  ask로.
- **11-04 00:00 첫 자정 tuner 인증 실패**: cron에 `CLAUDE_CONFIG_DIR`가 없어
  "OAuth session expired". 변경 없음으로 안전하게 끝남. → harness가 기본값을 넣음.
  같은 요약에서 ask로 "120g x 20팩"의 20을 읽은 쓰레기 값 발견 → 정가의 0.2~3배
  밖은 ask로 안 읽음.
- **11-04 17:10 조건형 매입 실패**: 소파가 시장 매출의 절반인데 조미료를
  lot당 1만 원에 사고 있었음(2.1절). → 매출 가중·가격 띠·자본 기준 상한.
- **11-05 00:30 buyer 거래 수 급감**: 가게가 "Lowest: N"을 말하며 닫는 111건,
  라운드 한계까지 1%씩 기어가는 흥정, 첫 메시지부터 거절하는 가게 11회 반복.
  → 즉시 수락·재개설·갭 메우기·무응답 가게 제외.
- **11-05 14:49 블랙리스트 버그(가장 큰 손실)**: ledger로 상위 buyer와 비교하니
  주문 수(582 vs 586)·가격 수준(0.73 vs 0.71)·속도(12s vs 9s)는 같은데 성사율만
  16% vs 63~84%(중앙값 44%). 원인은 영수증 검사가 "영수증 item == 내가 요청한
  것"만 정직으로 봐서, 카테고리로 요청하거나 가게가 허용 SKU로 대체한 정당한
  영수증 43건을 부정직으로 적었고, 블랙리스트가 1회 영구라 inextro(우리와
  397건), psyoung82(570건), yms979, lee-sungsoo, 25620027, Kiro729 등 1군 가게
  30곳을 1.5일 동안 제외한 것. 시장이 정산 때 확인한 거짓 claim은 43건 중 0.
  → 검사 수정, 블랙리스트 규칙 수정, 메모리 43줄에 `honest_repaired` 표시로 복구
  (백업 보관). 수정 후 첫 48건 성사율 56%, 22시간 평균 40%(매트리스 70%).
- 11-05 01:00 **"시간대별 전략" 검토 결과 기각**: 시장 정산 5~7k/시간, 내 주문
  150건/시간으로 평평. 대신 카테고리 희소도 regime(2.2, 2.3절).
- **11-10 19:30 4일 방치 점검**: shop 순이익 18.1M→20.5M인데 재고 20M→37M(장부는 +20M).
  buyer는 ledger로 상위권과 비교하니 지불가 0.70 vs 0.47~0.59, 같은 가게·SKU에서 5.5% 더
  냄, 싼 가게(0.56~0.60)로 안 감. → 가게 가격 수준을 라우팅에(+0.25), 첫 제안을 코드로,
  단계 상한. 결과 19시간: 지불가 0.69→0.625, 성사율 33→17%(가게가 4번째 제안에서
  끊음, 바닥가 문구 5종 중 1종만 읽음) → 11-11 15:31 문구 5종 파싱, 재개설 허용 1.15배,
  단계 6%.
- **11-11 15:37 가게가 시장 목록에서 빠짐**: kit·터널은 정상인데 `/api/shops`에 없고
  board alive=false, open 90/h→30/h. 재접속으로 복구(15:40). `watchdog.sh`(10분마다
  확인, 2회 연속 빠지면 재접속) 추가.
- **11-11 Round 3 패치 3.4/3.5 반영**: shop 순위가 "Book(잔고−시작+미판매 재고 원가)"이
  되어 재고가 다시 자본으로 셈 → 재고 상한 40→55%, 종료 전 원가 이하 투매 폐지(바닥
  = 원가), underwater 유닛도 원가 아래로는 안 팜. buyer Saved는 "거래별 절약 비율의 합
  ÷(거래 수+100), 쓸모없던 물건 −1" → 수명·등급 사전 검사와 보증 가게 우선이 그대로
  핵심. 같은 SKU가 시장 전체에서 1시간에 50개 넘게 공급되면 원가가 오르므로 40개를
  넘긴 SKU는 사지 않음. 나빠요는 유료(시간당 3개 초과 시 2만 원×k)인데 우리는 표를
  던지지 않음. tuner 프롬프트의 board 공식도 갱신.
- 수정 전 실패한 경로도 커밋에 남겼다: OpenAI `gpt-6-luna` harness(키 401),
  Codex CLI(한 줄에 13k 토큰), 하루 200회 quota(자정 후 1시간에 소진).
