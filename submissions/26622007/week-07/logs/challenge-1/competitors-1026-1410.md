# 경쟁 가게 분석 1026-1410 (Round 2, 11:07 ~ 14:07)

데이터: `/api/ledger` 지난 3시간(결과 9,811건, 검증된 거래 6,800건), `/api/board`(Round 2 누적, 가게 62곳), `/api/shops`.

## 1. nocha12는 3시간 동안 거래 0건

| 시각 (15분 단위) | 11:07 ~ 13:37 | 13:52 | 14:07 |
|---|---|---|---|
| nocha12 검증 거래 | 0 | 1 | 1 |
| 시장 검증 거래 | 15분마다 520 ~ 590 | 631 | 36 (진행 중) |

노트북 키트가 꺼져 있던 동안 가게가 응답하지 않았다. 14:02에 Render에서 다시 등록했다. Round 2 누적 점유율은 2.3%(13위)이다.

## 2. 상위 가게 (지난 3시간)

| 가게 | 성사 | 점유율 | 가능 방문 성사율 | ★ | lamp | chair | bookshelf | 단골 buyer |
|---|---|---|---|---|---|---|---|---|
| dragonash | 1,072 | 15.8% | 0.91 | 3.89 | 100.6 | 181.1 | 262.1 | buyer-01 347, outseop 222, myeonggi 110 |
| ggari-shop | 813 | 12.0% | 0.97 | 4.06 | 102.0 | 181.5 | 263.4 | fair-furniture 302, cheapest-in-town 159 |
| cheapest-in-town | 651 | 9.6% | 0.98 | 3.77 | 100.0 | 180.0 | 260.0 | 11im 317, dariaklt 240 |
| 11im | 583 | 8.6% | 0.97 | 4.24 | 103.7 | 188.1 | 276.4 | kcwarni 263 |
| mjforge | 551 | 8.1% | 1.00 | 3.47 | 100.3 | 181.0 | 261.1 | lee-sungsoo 287, buyer-03 237 |
| corner-store (강사) | 512 | 7.5% | 0.91 | 4.07 | 105.7 | 195.0 | 285.2 | choo-chu 231 |
| yms979 | 345 | 5.1% | 1.00 | 3.80 | 102.3 | 184.3 | 265.8 | buyer-02 339 |
| kcwarni | 297 | 4.4% | 1.00 | 4.24 | 101.0 | 182.1 | 262.1 | psyoung82 237 |
| psyoung82 | 282 | 4.1% | 0.82 | 4.39 | 105.0 | 185.0 | 270.0 | myeonggi 125, YAHO1212 83 |
| lee-sungsoo | 257 | 3.8% | 1.00 | 3.90 | 100.1 | 180.0 | 260.1 | psyoung82 119 |

하한가는 lamp 100, chair 180, bookshelf 260이다. 상위 가게는 모두 하한가에서 +0 ~ 2% 안에 판다. 가격으로는 더 차별할 여지가 거의 없다. corner-store, psyoung82, 11im만 하한가보다 3 ~ 10% 높게 받는다.

## 3. 점유율은 단골 buyer가 정한다

buyer 한 명은 3시간에 약 300건을 사고, 대부분을 한두 가게에서 산다.

| buyer | 3시간 구매 | 가게별 |
|---|---|---|
| buyer-01 (강사) | 347 | dragonash 347 (100%) |
| buyer-02 (강사) | 339 | yms979 339 (100%) |
| buyer-03 (강사) | 305 | mjforge 237, jiseokkk 68 |
| 11im | 344 | cheapest-in-town 317 |
| kcwarni | 263 | 11im 263 (100%) |
| fair-furniture | 320 | ggari-shop 302 |
| lee-sungsoo | 351 | mjforge 287 |
| choo-chu | 279 | corner-store 231 |

buyer는 약 25명이다. 한 명을 단골로 잡으면 시장의 약 4 ~ 5%가 된다. 10%를 넘은 dragonash와 ggari-shop은 단골이 둘 이상이다. yms979의 5.1%는 거의 buyer-02 한 명에게서 나온다. 17:00 정가 전환 뒤 nocha12가 7.8%까지 오른 것도 buyer-01을 잡았기 때문이었다(`hourly-1021-1725.md`). 그 buyer-01은 지금 dragonash에서만 산다.

## 4. 별점은 가게보다 buyer의 예산 여유를 따른다

상위 가게의 낮은 후기는 대부분 "Paid 180 of 185"(예산이 하한가 바로 위)와 "Could not agree on a price"(예산이 하한가보다 낮은 주문)이다. 높은 후기는 "Paid 260 of 360"처럼 예산이 넉넉할 때 나온다. 하한가에 파는 가게끼리 별점 차이(3.5 ~ 4.4)는 어떤 buyer가 단골인지에 따라 크게 달라진다.

## 5. 카드에 쓴 전략

| 가게 | 카드의 주장 |
|---|---|
| dragonash | "one message closes the deal", 시장 평균보다 낮은 호가, 더 싼 견적이 있으면 하한가까지 맞춰 줌, 두 번째 제안부터 하한가 이상이면 수락 |
| ggari-shop | 매시간(그리고 다른 가게 점유율이 뛰면 즉시) 시장 거래가를 스캔해 첫 호가를 맞춤, 규칙으로 ms 단위 응답 |
| cheapest-in-town | 1시간 안에 방문 코드를 들고 돌아오면 같은 가격이나 더 낮은 가격(하한가 한도), 하한가 미만 제안에는 "Premium AS digital care" |
| 11im | "Tired of shops that sneak +15 or +35 onto your receipt?" 다른 가게의 영수증 부풀리기를 겨냥 |
| yms979 | 첫 호가는 하한가 +30% 이하(한산할 때 +2%), 두 번째 제안부터 하한가 이상 수락 |
| lee-sungsoo | 정가, 코드가 한 번에 답함, 서명 영수증 |
| psyoung82 | 고정 최종가(105/185/270), `/.well-known/quality-guarantee.json` |
| kcwarni | "Claim a 100% refund within one calendar month ... regardless of rating ... External manual payout" |

공통점은 세 가지다. 하한가 근처 고정가, 코드가 즉시 답함(LLM 지연 없음), 서명 영수증 강조. nocha12의 셀러는 매 턴 하네스(LLM)를 부른다.

## 6. 도전 2 관련

- cheapest-in-town의 buyer가 쓰는 광고 후기("... try the store 'Cheapest in Town'")가 nocha12만이 아니라 dragonash(66건), ggari-shop(41 + 37 + 28건)에도 달렸다. 경쟁 가게 여러 곳을 상대로 같은 문장을 반복하는 행동이다.
- kcwarni 카드는 등급과 관계없는 100% 환불과 "external manual payout"을 약속한다. 이 시장의 가구는 가상이고(cheapest-in-town 카드도 "Fictional furniture"라고 밝힌다) 환불을 확인할 경로가 없다. 지킬 수 없는 약속으로 buyer를 끌려는 문구다. nocha12의 결렬 메시지("5-star review면 다음에 더 좋은 가격")도 같은 종류의 약속이다.

## 7. 다음에 할 수 있는 것 (사용자 결정 필요)

- 가장 큰 차이는 가게가 꺼져 있던 3시간이다. Render Free는 15분 동안 요청이 없으면 잠든다.
- 셀러가 하한가에서 LLM 없이 즉시 답하게 하면 상위 가게와 같은 조건이 된다. 사용자가 셀러를 그대로 두라고 했으므로 바꾸지 않았다.
