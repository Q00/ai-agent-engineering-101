# Round 3 물류와 Market A2A 조직 (2026-11-04)

## 1. 대형 유통사는 어디서 마진을 남기는가 (웹 조사, 2차 자료 위주라 수치는 근사치)

| 회사 | 마진 구조 | 이 시장으로 옮기면 |
|---|---|---|
| Costco | 상품 마크업은 약 15% 상한, 이익의 대부분은 회원비. SKU 약 4,000개로 회전율을 높임 | 팔리는 카테고리에 집중하고 빨리 회전시킴. 회원 코드(단골 할인) |
| Walmart | EDLP와 크로스도킹(24시간 이내 출고)으로 재고·물류비를 낮춤 | 다음 공급 창까지 버틸 만큼만 보유하고, 창 시간에 맞춰 매입 |
| Amazon | 리테일 마진은 한 자릿수 초반, 이익은 광고·셀러 서비스. 재고 대금보다 판매 대금이 먼저 들어옴 | 판매가 매입보다 먼저 정산되므로 매입 타이밍이 중요함 |
| 쿠팡 | 로켓배송 8년 만인 2022년 3분기 첫 흑자. 물류 자동화와 직접 물류망이 원동력, 와우 멤버십으로 고객을 묶음 | 주문 → 재고 → 출고 파이프라인 자체를 경쟁력으로 만듦 |

출처: [Costco (The Economist 경유)](https://thenightly.com.au/business/the-economist-why-costco-is-so-loved-not-just-by-shoppers-but-wall-street-too-c-13659120),
[Costco 회전율 (Management Consulted)](https://managementconsulted.com/?p=387541),
[Walmart 공급망 (SupplyChain247)](https://www.supplychain247.com/article/incredibly_successful_supply_chain_management_how_does_walmart_do_it),
[Walmart 원가 우위 (Pricefy)](https://www.pricefy.io/articles/walmart-cost-leadership-strategy),
[Amazon 리테일과 광고 (Marketplace Pulse)](https://www.marketplacepulse.com/articles/amazon-is-now-just-40-retail),
[Amazon 셀러 서비스 2025 (Modern Retail)](https://www.modernretail.co/operations/marketplace-briefing-amazon-sellers-face-cash-crunch-as-fees-policy-changes-spur-order-delays-price-hikes-and-supplier-renegotiations/),
[Amazon 1998 현금전환주기 (Motley Fool)](https://g.foolcdn.com/eveningnews/foth/1998/foth981030.htm),
[쿠팡 첫 흑자 (MTN)](https://news.mtn.co.kr/news-detail/2022111008350924112),
[쿠팡 2023 실적 (MTN)](https://news.mtn.co.kr/news-detail/2023030110490222166).

공통점: 같은 가격이라면 **물류에서 덜 새는 쪽**이 이김.

## 2. Round 3에서 물류는 중요한가 (공개 원장 16시간, 우리 메모리 DB)

| 항목 | 우리 | 비교 |
|---|---|---|
| 매출 / 매입원가 | 1.14배 | dae-yangkim 1.36배, chaewoonee 1.22배 |
| 보증 환불 | 3.14M (매입원가의 4.2%) | dae-yangkim 0.8% |
| 보증 판매 비중 | 96% | dae-yangkim 65% |
| 등급별 파손률 (시장 전체) | A 2.4%, B 9.1%, C 27.1% | |
| 재고 부족으로 돌려보낸 주문 | 374건 (거래 2,182건 대비) | 매트리스 113건, 찌개 45건, 육류 41건, 견과 31건 |
| 공급 창이 닫혀 거절된 매입 | 505건 (성공 634건) | |
| 식품 폐기, 보관비 | 거의 0 / 7k | 문제 아님 |

결론: 물류는 중요함. 갈리는 지점은 **환불(품질·보증), 재고 부족(충족률), 매입 타이밍** 세 가지임.

## 3. 바로 적용한 물류 개선 (커밋 9797189, 13:42 배포)

1. C 등급에는 보증을 주지 않음. 보증 상품은 기대 환불(p_break × 호가)을 호가에 포함하고(정가 상한), 파손률은 데이터가
   충분하면 등급 × 카테고리로 계산함.
2. 공급 창이 열려 있거나 60초 안에 열릴 때만 매입 목록을 냄.
3. 재고가 없어 돌려보낸 요청을 기록해서 "우리에게 온 수요"로 재고 목표에 더함(6시간 창).

## 4. Market A2A 조직 (r3lib/org)

부서마다 내부 A2A 에이전트를 하나씩 둠(127.0.0.1에만 열고 터널 뒤로는 노출하지 않음). 부서 간 호출은 Ed25519 서명
봉투로 하고, 답은 서명 영수증으로 받음. 위조, 재전송, 수신자 불일치, 오래된 요청은 거절함(fail-closed).
판매(shop_r3)는 지금처럼 ms 단위로 프로세스 안에서 처리하고, 부서들은 60초 이상 걸리는 느린 경로만 맡음.
서버가 죽으면 같은 스킬을 프로세스 안에서 그대로 실행하므로 재고 보충이 멈추지 않음.

| 부서 | 스킬 | 기존 코드 |
|---|---|---|
| ceo | tick (재고 보충 한 주기), kpi (board 백분위와 부서 보고서 → logs/r3/kpi.jsonl) | restock_r3.main |
| purchasing | propose | restock_r3.plan |
| logistics | window, approve (현금 예비금·10줄 제한), health (임박 식품·오래된 상품·재고 부족) | 새로 작성 |
| quality | warranty_policy, harsh, refunds | pricing, refresh_harsh_styles |
| marketing | stats (글, 표, 회원 코드) | memory |

호출 기록: `logs/r3/org-calls.jsonl` (via a2a/local, req 해시, 소요 ms). 이 구조는 12~13주차 MeshKit의
find / call / proof / check와 같은 모양임.
