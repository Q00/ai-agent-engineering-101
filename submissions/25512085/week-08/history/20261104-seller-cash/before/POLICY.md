# Week-08 운영 정책 통합본 및 중복·불일치 검토

- 기준: 브랜치 `week-08`, HEAD `a1292c2`의 작업 트리 코드(2026-11-04 정적 읽기).
- **정적 분석입니다.** 코드를 실행하지 않았고 테스트·VM·네트워크도 사용하지 않았습니다. 아래 "실제 적용값"은 코드 조건을 따라가 계산한 값이지 실행 결과가 아닙니다.
- `git show`로 개별 커밋(0691117, fcda40b, 6a88136, bb80dbb, a1292c2, a285d38, 320dc4a, 41f8e5c)의 diff를 보지 못했습니다(이 작업에서는 셸을 쓰지 않음). 현재 파일 내용만 근거로 삼았고, 커밋별 변경 내역은 **미확인**입니다. a285d38은 코드에서 해당 해시를 찾을 수 없어 seller.py의 현재 청산 로직(아래 3장)만 기술했습니다.
- 경로 표기: 별도 언급이 없으면 `submissions/25512085/week-08/` 아래 파일, `파일:줄`.
- SKU 이름은 카탈로그 파일이 없어 코드 주석에서만 가져왔습니다. 주석에 없으면 "미확인"입니다.

---

## 1. 한눈에 보기 (현재 실제로 적용되는 핵심 수치)

| 항목 | 현재 값 | 조건 / 비고 | 근거 |
|---|---|---|---|
| 현금하한(cash floor) | `min(1,500,000, max(bankrupt_line+200,000, balance//2))` | bankrupt_line 기본 500,000 → 잔액 ≥3M이면 1.5M, 잔액이 작으면 700,000까지 | store.py:275-277 |
| 구매 가능 예산 `available_budget` | `max(0, min(max_cap, stock_cost+(balance-floor)) - stock_cost)` | `max_cap`는 단계별 재고 상한(아래) | store.py:278-289 |
| 재고 상한 `max_stock_cap` | stage1 2.5M / stage2 `2.5M+이익×0.75` / stage3 `2.5M+이익×0.85` | stage3 = 총자산(현금+재고원가−주입금) ≥ 6.0M | store.py:262-270 |
| 재고목표 비율 | **0.75** (live 카탈로그 ≥500종 **그리고** 현금 ≥10M), 그 외 **0.45** | 목표는 상한이 아니라 "부족분(deficit)" 계산용 | restock.py:124, 327-329 |
| 공급창 예산 | 아래 2장 표 (200k ~ 8.5M) | 창당 1회 확정 후 창 내에서 올리지 않음 | restock.py:330-357 |
| 창 예산 절대 상한 | live & 현금 ≥20M: 8.5M / live & ≥10M: 6.5M / 그 외 3.0M | adaptive `MAX_WINDOW_BUDGET=3M`이 기본값 | restock.py:350-353, adaptive_strategy.py:13 |
| 한 번에 낼 수 있는 라인 | 최대 10개 라인, 라인당 qty ≤20 | | restock.py:533, 478, 711 |
| 주력 깊이(cap) | live & 현금부자: `LIVE_BESTSELLER_CAPS`(2~4), 현금 ≥10M이면 `MEGA_BESTSELLER_CAPS`(2~8) | 그 외 `min(active_caps, 4)`, 기본 goods 3 / food 4 | restock.py:493-499, 546-549 |
| 실적 있는 SKU 깊이 | `min(8, max(3, bestseller_cap, ceil(최근20분판매×1.25)))` | deficit>0 이고 proven일 때 | restock.py:555-559, 71 |
| 탐색(exploration) SKU 상한 | 기본 35 / 현금부자+풀카탈로그 55(adaptive expand 시 최대 60) / deficit 가산 후 최대 120 | | restock.py:121, 343-349, adaptive_strategy.py:14 |
| 탐색 units 상한 | 기본 80 / 현금부자+풀카탈로그 95 / deficit 가산 후 최대 250 | | restock.py:122, 344-349, adaptive_strategy.py:15 |
| 창당 신규 카테고리 확장 | 기본 4 / 현금부자+풀카탈로그 8 | | restock.py:123, 342 |
| 식품 제한 | 보유 식품 units ≥20이면 신규 식품 불가(단, 실적/품절신호 SKU의 10,000원 미만 건은 예외) | 식품 정가 ≤120,000, 유통기한 <96h는 비실적 SKU 금지 | restock.py:68, 523-529 |
| 고가품 규칙 | 정가 상한: 기본 450,000 / stage3 650,000 / stage3·live·현금≥10M 2,500,000. 650,000 초과는 live·현금≥10M·bestseller 목록 SKU만 cap까지, 그 외는 미보유 시 1개 | | restock.py:44-46, 457-460, 584-588 |
| 고가 1개 허용(allowance) | 현금부자, 창당 1회, 미보유 SKU의 60% 분할을 넘는 1개 구매 허용 (창 잔여 ≥50,000 남길 때) | | restock.py:609-628 |
| 구매 종료 | 라운드 종료(2026-11-17 23:59 KST) **36시간 전**부터 구매 중단 | | store.py:58-64, restock.py:259, 310 |
| 마지막 24시간 청산 | 가격이 선형으로 원가의 25%(salvage)까지 하락 | | store.py:63-70, seller.py:459-463 |
| 파산 | 잔액 < bankrupt_line 이고 파산 잔여횟수>0 이고 판매 가능 재고 0이면 `{"bankrupt": true}` 출력 | | restock.py:252-256 |

---

## 2. 매입(restock) 결정 순서

`build_restock_plan` → `plan_with_hub` 순서입니다.

1. 카탈로그·재고 파일이 없으면 구매 없음 (restock.py:246-249).
2. **파산 판정** (252-256).
3. **종료 36h 전 stop** (259). `plan_with_hub`에서 한 번 더 확인 (310).
4. 원장 조회 → 허브 갱신. 허브에 `restock_windows`가 비어 있고 창이 열려 있으면 **첫 창은 `blocked`로 희생**(배포 직후 과소집계 방지) (279-281, 317-318).
5. `apply_ledger_rows`로 자체 판매·proven 갱신 (296-303), `update_capital_stage`로 단계·현금하한·`available_budget` 산출 (304), `decide_restock`(adaptive) 호출 (305).
6. 공급창이 열려 있지 않으면 구매 없음 (`supply_window`: 열림 60초 전 ~ 닫힘, 139-151).
7. **창 예산(`eff_window_budget`) 결정 — 코드 순서대로**

   | 단계 | 계산 | 줄 |
   |---|---|---|
   | (a) 기초값 | 현금부자(`balance ≥ 5.5M`) **그리고** 풀카탈로그(`≥50종`): 잔액 ≥20M → 8.5M, ≥10M → 6.0M(`MEGA`), ≥6.2M → 1.2M(`SUPER`), 그 외 0.9M(`HIGH`). 현금부자지만 카탈로그 <50종 → 450k(`PROFITABLE`). 현금부자 아님 → 200k(`WINDOW_BUDGET`) | 330-340 |
   | (b) 상한 `effective_max_window` | live(≥500종)&잔액≥20M → 8.5M, live&≥10M → 6.5M, 그 외 `MAX_WINDOW_BUDGET`=3.0M | 350-353 |
   | (c) deficit 가산 | `eff = min(상한, max(기초값, deficit×0.35))` (deficit = `target_ratio×equity − stock_cost`) | 354 |
   | (d) 창 내 고정 | `eff = min(eff, 상한, budget_state["window_cap"])` — 해당 창의 첫 계산값이 `window_cap`로 저장되어 이후 올라가지 않음 | 356-357 |
   | (e) 이미 쓴 금액 | `used = max(창 내 실제 공급지출, baseline+reserved)`; `tick_budget = min(available_budget, eff − used)`; ≤0이면 구매 없음 | 365-369 |

   결과 요약 (코드 조건만 따라가 계산한 값):

   | 조건 | 기초값 | 창 상한 | 비고 |
   |---|---|---|---|
   | 현금 <5.5M | 200k | 3.0M (deficit×0.35로만 증가) | |
   | 현금 ≥5.5M, 카탈로그 <50종 | 450k | 3.0M | |
   | 현금 5.5~6.2M, 풀카탈로그 | 900k | 3.0M | |
   | 현금 6.2~10M, 풀카탈로그 | 1.2M | 3.0M | |
   | 현금 10~20M, **live(≥500종)** | **6.0M** | 6.5M | 사용자 신규 6M 정책이 실제로 적용되는 구간 |
   | 현금 10~20M, 50~499종 | 6.0M → (b)에서 3.0M로 잘림 | 3.0M | |
   | 현금 ≥20M, live | 8.5M | 8.5M | (a)(b) 각각 8.5M 하드코딩 |
   | 현금 ≥20M, 500종 미만 | 8.5M → 3.0M로 잘림 | 3.0M | 테스트가 이 구간만 검증 (7장 g) |

8. **탐색 상한**: exploration SKU/units cap 계산 후 deficit이 있으면 가산 (342-349). `exploration_cap = eff × max(.5, min(1, adaptive.exploration_factor))` (363).
9. **한도 계산**: `core_limit` = (현금부자&live) ? `tick_budget` : `min(tick_budget, 0.6×eff − used)` (520). 고가품 1개 allowance는 별도 (609-628).
10. **후보 정렬** (504-519): 후보 = `active_caps ∪ proven ∪ missed_skus_1h ∪ sku_recent ∪ hot_market_skus`. 정렬 키는 순서대로 ① 근거 없음 ② 실적/품절신호 카테고리 아님 ③ 이미 보유(단 bestseller 미달 예외) ④ 식품 ⑤ 근거 없음(2) ⑥ 소파 3종 우선(현금≥10M) / bestseller+시장 2건↑ ⑦ 최근 1h 품절 문의 수 ⑧ 최근 20분 자체 판매 ⑨ 시장 최근판매×정가 ⑩ sku.
11. **SKU별 필요 수량(`need`) 분기** (546-582), 위에서부터 먼저 걸리는 것이 적용:
    1. `deficit>0` 이고 proven(goods 또는 shelf≥96h): `target_depth = min(8, max(3, bestseller_cap, ceil(own20m×1.25)))`.
    2. 보유 중이며 proven 아님: `need=0` ("시장 인기만으로는 깊이 추가 안 함").
    3. `is_live_bestseller`(live·현금부자·bestseller 목록 SKU이고 proven/품절신호/자체판매/**시장 3h 2건 이상**/소파3종&현금≥10M 중 하나): `need = cap − 보유`.
    4. 보유 중: `ceil(최근20분판매×1.25) − 보유` (판매가 보유보다 클 때만).
    5. 미보유 + 정가 ≥10,000(goods 또는 shelf≥96h) + (proven/품절/자체판매/`active_caps`&시장2건/hot): `need=1`.
    6. 최근 20분 자체 판매 있음: `min(2, cap, ceil(count×1.25))`.
    7. `active_caps` & (proven 또는 시장2건) 또는 hot: `need=1`.
12. **고가 보정**: 정가 >650,000이면 (live·현금≥10M·bestseller) → `cap − 보유`, 아니면 미보유 시 1개 (584-588). 식품은 `20 − 보유식품` 이하 (591-592).
13. **탐색(experiment) 라인**: `active_caps`·proven·품절신호가 아닌 SKU는 exploration 상한(SKU 수/units/창당 카테고리 수/가격대 중복)에 걸리면 건너뜀 (593-599).
14. **라인 추가 `add_line`**: 단가 추정 `ceil(정가×0.75)`, `qty = min(need, 20, (min(tick_budget, limit) − spent_est) // 단가)` (470-489).
15. **폭(breadth) 단계**: 후보 이후 카테고리 순위대로 미커버 카테고리에서 1개씩 탐색 구매 (633-691). 정가 ≥100,000 goods 우선(현금부자) (636-639, 680-681).
16. 창 예약액 갱신, 매장 포럼 홍보글 큐잉(재고 ≥6 units, 330초 간격) (693-709). 최대 10개 라인 반환 (711).

---

## 3. 판매(seller) 가격·등급·보증·청산

### 3-1. 등급 판정
| 대상 | 규칙 | 근거 |
|---|---|---|
| goods | 재고 파일의 `grade`(A/B/C) 그대로. 영구 | seller.py:92-97 |
| food | 남은시간/정격 shelf_hours 비율 ≥0.80 → A, ≥0.45 → B, 그 미만 C. 15초 안전 버퍼. 남은 ≤0.05h면 expired | seller.py:99-113 |
| 식품 체크아웃 여유 | 요구 사용시간 + 0.25h 필요 | seller.py:117, 150-156 |

### 3-2. 가격 (`compute_unit_pricing_and_terms`, seller.py:355-477)
기준 "원가"는 재고의 실제 `cost`입니다(추정 아님).

| 구분 | open_ask | r1_target | r2_target | hard_floor |
|---|---|---|---|---|
| goods A | `max(1.24·cost, min(0.95·list, max(시장앵커, 0.90·list)))` | `cost+(open−cost)×0.50` | `cost+(open−cost)×0.18` | `max(cost+50, 1.08·cost, cost/(1−0.03)×1.05)` |
| goods B | `max(1.30·cost, 0.86·list)` | `0.75·list` | `0.68·list` | 위와 동일 형태(break 0.15) |
| goods C | `max(1.38·cost, 0.80·list)` | `0.68·list` | `0.60·list` | 위와 동일 형태(break 0.35) |
| food, 잔여 ≥48h 이고 A | `max(1.20·cost, min(0.95·list, max(앵커, 0.90·list)))` | `max(1.08·cost, cost+(open−cost)×0.42)` | `cost+(open−cost)×0.14` | `max(cost+10, 1.03·cost)` |
| food B (≥48h) | `max(1.12·cost, 0.84·list)` | `max(1.04·cost, 0.76·list)` | = floor | = cost |
| food C 또는 <48h | `0.72·list` | `0.64·list` | = floor | `0.78·cost`(C) / `0.80·cost`(<48h) |
| food <24h | `0.65·list` | `0.55·list` | = floor | `0.55·cost` |
| food <12h | `0.50·list` | `0.42·list` | = floor | `0.35·cost` |

- 시장앵커 `get_sku_market_anchor` = 체결가 2건 이상이면 `0.6·P65 + 0.4·평균`, 없으면 `0.95·list` (store.py:550-558).
- 최종 정리: `open ≥ floor`, `r1 ≤ open`, `r2 ≤ r1` (seller.py:453-455).

### 3-3. 보증(안심케어)
| 항목 | 값 | 근거 |
|---|---|---|
| goods 보증 | 6시간 환불 (`CARE_HOURS = GOODS_BREAK_WINDOW_H = 6`) | seller.py:328, store.py:53 |
| food 보증 | 없음 | seller.py:451 |
| 파손 확률 | A 3% / B 15% / C 35% | store.py:52 |
| 보증 없는 가격 | A·B만 허용. `max(min(1.10·cost, price), price×(1−break_rate))` | seller.py:331, 349-352 |
| C등급 | 항상 보증 포함 | seller.py:329-331 |

### 3-4. 협상
- 1턴: 제안 ≥ r1 수락, 2턴: ≥ r2, 3턴 이상: ≥ hard_floor (seller.py:934-938). 원가 미만 판매 금지(청산 구간 제외, 941). 4턴 이상 hard_floor 미달 시 거절 (986-989).
- 첫 메시지에서 제안가 ≥ open_ask면 즉시 체결 (728-758).

### 3-5. 청산
| 구분 | 규칙 | 근거 |
|---|---|---|
| goods 보유 8~12h | floor `max(cost+10, 1.02·cost)`, open ≤ 0.88·list, r1 = 1.06·cost | seller.py:403-407 |
| goods 보유 ≥12h | floor `max(10, 0.95·cost)`, open ≤ 0.82·list, r1 = 1.02·cost, r2 = floor | seller.py:398-402 |
| food | 보유 기간이 아니라 **잔여 유통기한**(48h/24h/12h)으로 차등 | seller.py:415-449 |
| 라운드 마지막 24h (전 상품 공통) | 모든 가격이 `salvage + (p − salvage)×f` (salvage = 0.25·cost, f는 남은 시간/24h) | store.py:63-70, seller.py:459-463 |

`age_h`는 `supplied_at`(epoch>1.7e9일 때만) 기준이며, 그렇지 않으면 0으로 계산되어 goods 청산이 적용되지 않습니다 (seller.py:365-367).

---

## 4. 구매(buyer) 가격상한·재시도·쿨다운·fallback

### 4-1. 가격상한
| 구분 | 규칙 | 근거 |
|---|---|---|
| food 기본 상한 | `min(예산, floor(정가×90%))`. 정가 모르면 보류 | market_buyer.py:51, 555-558 |
| food 비교가 상한 | 영수증 등급·만료가 확인되고 3시간 내 동일 등급·유사 잔여시간 체결 ≥3건이면 `min(기본상한, 중앙값×110%)` | market_buyer.py:53-54, 569-586 |
| goods 상한 | `min(예산, floor(정가×92%))` | market_buyer.py:52, 867 |
| goods 비교가 상한 | 체결가 ≥3건(최근 15건 중앙값)이면 `min(상한, max(0.88·정가, 중앙값×112%))` | market_buyer.py:868-877 |
| 영수증 검증 | goods 영수증가는 `min(예산, 정가−1)` 초과 시 거절 | market_buyer.py:1038-1042 |
| 보증 없는 goods | `(1−break_rate)×정가 − 가격 ≤ 0`이면 구매 안 함 (등급 주장 없으면 C로 가정) | market_buyer.py:1106-1121 |
| 상점 선택 효용 | 추정원가 `0.65·정가`가 예산 초과 시 feasibility 0.15, `0.76·정가` 초과 시 0.45. 10개 SKU에 1.35배 가중 | market_buyer.py:397-429 |

### 4-2. 재시도·시간 제한
| 항목 | 값 | 근거 |
|---|---|---|
| 상점 시도 횟수 | 최대 3회 (한 시도에 상점 1곳) | market_buyer.py:690 |
| 협상 라운드 | Round3 3라운드(그 외 5) | market_buyer.py:880 |
| 전체 시한 | 90초 또는 주문 만료 −5초 | market_buyer.py:660-662 |
| 상점당 시한 | 25초 (마지막 3초는 취소용) | market_buyer.py:701-703 |

### 4-3. 쿨다운 (adaptive 연동)
- 실패 사유 `price, rejected, timeout, network-timeout, unsigned`만 기록 (adaptive_strategy.py:95-105).
- 최근 20분 내 **서로 다른 주문 3건 이상**이 같은 (상점, 범위, 사유)면 20분 쿨다운. `network-timeout`·`unsigned`는 SKU와 무관하게 상점 전체, `unsigned`는 hard (adaptive_strategy.py:108-126).
- 후보가 전부 쿨다운이면 hard가 아니고 dishonest가 아닌 쌍 중 실패 최소 하나를 시도 (adaptive_strategy.py:129-144).

### 4-4. Gemini fallback (market_buyer.py, buyer.py)
| 항목 | 내용 | 근거 |
|---|---|---|
| 순서 | 1차 `codex exec -m gpt-5.6-sol`(market_buyer 18초 / buyer 20초) → 실패·시간초과·빈 응답이면 Gemini | market_buyer.py:150-184, buyer.py:91-125 |
| Gemini 모델 | `gemini-3.5-flash-lite` → `gemini-3.8-flash` (각 6초, 최대 64토큰) | market_buyer.py:118, buyer.py:40 |
| 키 | `GEMINI_API_KEY` 환경변수 → `~/.bashrc` | market_buyer.py:93-109 |
| 이름 존재 여부 | 위 모델 ID가 실제로 유효한지는 확인하지 않음 (**미확인**) | — |

---

## 5. 자동조정(adaptive_strategy) 판단 기준과 범위

| 항목 | 값 / 규칙 | 근거 |
|---|---|---|
| 주기 | 1200초(20분)마다 1회 판단, 이전 상태 반환 | adaptive_strategy.py:12, 45-47 |
| 판단 가능 조건 | 재고 파일 최신(120초), 원장 연속 커버리지 ≥20분, 모든 유닛에 cost·supplied_at·sku 있음. 아니면 정책 유지 | adaptive_strategy.py:50-60 |
| **recover** | 보유 원가 중 "6시간 이상 보유 **그리고** 최근 20분 자체 판매 없는 SKU"의 원가합 ≥300,000 이고 비중 ≥35% 이고 **현금 <10M** | adaptive_strategy.py:70-78 |
| recover 효과 | `exploration_factor=0.5` → 탐색 예산만 절반 (핵심 복구는 유지) | adaptive_strategy.py:77-78, restock.py:363 |
| **expand** | 현금 ≥6.2M **그리고** 최근 20분 자체 판매 존재 **그리고** (판매 ≥3건 또는 놓친 문의>0) | adaptive_strategy.py:79-82 |
| expand 효과 | `exploration_skus=60`, factor 1.0. 현금부자·풀카탈로그에서만 반영 | restock.py:345-346 |
| maintain | 그 외 | adaptive_strategy.py:83-86 |
| 범위 | adaptive는 **탐색 SKU 상한(≤60)·탐색 예산 비율(0.5~1.0)만** 조정. 창 예산 수치는 직접 바꾸지 않음. `MAX_WINDOW_BUDGET`=3.0M은 restock의 상한 폴백으로만 사용 | adaptive_strategy.py:13-15, restock.py:19-24, 350-353 |

---

## 6. 하드코딩 SKU 목록

### 6-1. 목록별 정의
| 이름 | 용도 | 위치 | 적용 조건 |
|---|---|---|---|
| `TARGET_CAPS` | 소량 기준 보유 목표 + 탐색 풀 제외 | restock.py:72-73, 497-499, 671 | 항상 포함 |
| `HIGH_VALUE_CAPS` | 고가 상위 매출 SKU 목표 | restock.py:74-81 | 현금부자 |
| `LIVE_BESTSELLER_CAPS` | live 카탈로그 상위 SKU 깊이 | restock.py:82-98 | live & 현금부자 & 현금 <10M |
| `MEGA_BESTSELLER_CAPS` | 현금 ≥10M 깊이 확대 | restock.py:99-120 | live & 현금 ≥10M |
| 소파 3종 하드코딩 `("G8ec51935","G00327e80","G03d0199f")` | 현금 ≥10M이면 증거 없이도 우선 구매 | restock.py:512, 553 | 별도 인라인 목록 |
| `bestseller_boost` 10종 | 구매자 효용 1.35배 | market_buyer.py:422-425 | 구매 측 |

### 6-2. SKU별 표 (값은 코드 그대로)
| SKU | 이름(코드 주석) | TARGET | HIGH_VALUE | LIVE | MEGA | 구매자 boost | 실제 유효 cap (live·현금부자 기준) |
|---|---|---|---|---|---|---|---|
| G97cd0437 | **미확인** | 2 | — | — | — | — | 2 (탐색풀에서 제외) |
| Gef4d3036 | Busa apple 28,900 | 2 | — | 3 | 3 | — | 3 |
| G0be9ed07 | Modal sofa pad 39,900 | 2 | — | 4 | 6 | ✔ | 4 / 6 |
| Ge62449c9 | Hotel comforter 87,900 | 2 | 3 | 4 | 6 | ✔ | 4 / 6 |
| G07ec17d5 | SS hybrid spring mattress 279,000 | 3 | 3 | 4 | 8 | ✔ | 4 / 8 |
| G1515d951 | Queen hybrid mattress 423,810 | — | 2 | 4 | 6 | ✔ | 4 / 6 |
| G2cb8041b | Office mesh chair 296,000 | — | 2 | 3 | 4 | ✔ | 3 / 4 |
| G5cd4ffbc | Microfiber comforter set 199,000 | — | 2 | 3 | 5 | ✔ | 3 / 5 |
| G200e39a3 | Kimchi 10kg 37,900 | — | 3 | 3 | 5 | ✔ | 3 / 5 |
| G8ec51935 | Jakomo 4-person sofa 2,320,000 | — | — | 2 | 3 | ✔ | 2 / 3 |
| G00327e80 | Dauning 4-person leather sofa 2,246,800 | — | — | 2 | 3 | — | 2 / 3 |
| G03d0199f | La Cloud hotel bed set 1,380,000 | — | — | 2 | 2 | ✔ | 2 / 2 |
| G75f11c19 | Modal comforter pad set Q 122,900 | — | — | 3 | 8 | ✔ | 3 / 8 |
| G6f632932 | Wood dining chair 116,000 | — | — | 2 | 2 | — | 2 / 2 |
| Gd19f1a46 | Blackout curtain 111,370 | — | — | 2 | 2 | — | 2 / 2 |
| G81ade83e | Denim blackout curtain 34,740 | — | — | 2 | 2 | — | 2 / 2 |
| Gcc06fe5c | Luxury heating mat DS 312NSF 471,000 | — | — | — | 5 | — | MEGA만 5 |
| G010744b7 | HBAF nuts 50-pack 30,900 | — | — | — | 5 | — | MEGA만 5 |
| G1831f2c0 | Pringles assorted 12,900 | — | — | — | 5 | — | MEGA만 5 |
| Gdf79cd07 | Pulmuone water 500ml 60-pack 12,900 | — | — | — | 4 | — | MEGA만 4 |
| G00b8acf0 | Montbest water 2L 6-pack 4,900 | — | — | — | 4 | — | MEGA만 4 |

겹침: 위 표에서 TARGET·HIGH_VALUE·LIVE가 함께 있는 SKU(G0be9ed07, Ge62449c9, G07ec17d5, G1515d951, G2cb8041b, G5cd4ffbc, G200e39a3, Gef4d3036)는 `{**TARGET, **HIGH_VALUE, **bestseller}` 병합 때문에 **뒤쪽(bestseller) 값이 이깁니다** (restock.py:495). 따라서 live·현금부자 환경에서는 TARGET/HIGH_VALUE 값은 사실상 무시됩니다. 단, `G97cd0437`만은 TARGET 값이 그대로 쓰입니다.

---

## 7. 검토: 중복 규칙·정의 불일치·충돌 (핵심)

판정 표기: **확정**(코드로 직접 확인) / **추정**(조건 분석상 그러함) / **미확인**.

### (a) 같은 목적의 상수·상한이 여러 곳 — 실제 승자

**현상**: 창 예산을 결정하는 값이 5곳에 흩어져 있습니다.
- restock 단계별 상수: 200k / 450k / 900k / 1.2M / 6.0M + 인라인 8.5M (restock.py:62-66, 332).
- 상한 3중: adaptive `MAX_WINDOW_BUDGET=3M`, 인라인 6.5M, 인라인 8.5M (restock.py:350-353).
- deficit×0.35 (354), 창 첫 계산값 고정 `window_cap` (356-357).
- store의 `available_budget`(재고 상한/현금하한) (store.py:289).

**실제 영향 (확정)**: 사용자 신규 6M(`MEGA_CASH_WINDOW_BUDGET`)은 **live(≥500종) & 잔액 10~20M 구간에서만** 효과가 있고, 카탈로그 50~499종이면 3.0M으로 잘립니다. 그 위 `20M+`는 8.5M으로 6M 상수와 무관한 별도 하드코딩입니다. 6.2~10M 구간은 기초값이 1.2M이라 6M으로 점프하는 불연속이 있습니다. 또 `window_cap`이 창 안에서 고정되므로 창 도중 잔액이 10M을 넘어도 다음 창까지 6M이 적용되지 않습니다.

**재고목표 0.45 vs 0.75 (확정)**: `STOCK_TARGET_RATIO=0.45`는 기본값이고, 0.75는 `live & 현금≥10M`일 때만 인라인으로 적용됩니다 (restock.py:124, 327). 상수화·명명이 없습니다.

**정리 제안**: 현금 구간표를 하나의 `BUDGET_TIERS` 표(임계 현금, 기초 예산, 상한, 목표 비율, 최대 정가)로 통합하고 8.5M·6.5M·10M·20M 인라인 값을 이름 있는 상수로 합칩니다. `MAX_WINDOW_BUDGET`은 adaptive에서 restock으로 옮기거나 삭제합니다(adaptive는 이 값을 직접 쓰지 않음).

### (b) '오래된/악성 재고' 정의가 파일마다 다름 (확정)

| 위치 | 기준 | 근거 |
|---|---|---|
| adaptive recover | 보유 ≥**6h** + **최근 20분** 자체 판매 없음, 원가합 ≥300k·비중 ≥35%·현금<10M | adaptive_strategy.py:70-78 |
| seller 청산 (goods) | 보유 ≥**8h**(하한 1.02) / ≥**12h**(하한 0.95), 판매 이력과 무관 | seller.py:398-407 |
| seller 청산 (food) | **잔여 유통기한** <48/24/12h, 보유시간 무관 | seller.py:415-449 |
| market_monitor 위험 | 보유 ≥**3h** & 시장 3h 체결 0건 (75), goods ≥**6h** (40), 자체 6h 무판매 (75), 식품 잔여<6h (85) | market_monitor.py:399-406, 450-464 |
| 마지막 24h 청산 | 전 상품 선형 하락 | store.py:63-70 |
| restock | 악성 재고 개념 없음. proven은 한 번이라도 팔렸으면 영구 | restock.py:296-303 |

**실제 영향**: 같은 유닛이 모니터에서는 3h에 "위험", adaptive에서는 6h에도 "최근 20분 판매 없음"만으로 노후로 집계(20분 안에 팔렸으면 6h 보유분도 제외), seller는 8h부터 가격 인하입니다. 6h가 adaptive/모니터에서 각각 다른 의미로 쓰이고, adaptive의 "판매 없음"은 20분 창이라 모니터의 6h 창과 일치하지 않습니다.

**정리 제안**: `STALE_AGE_H`, `NO_SALE_WINDOW_H` 같은 공용 상수를 `store.py`에 두고 adaptive·seller·monitor가 같은 이름을 참조하게 합니다.

### (c) '잘 팔리는/검증된 상품' 정의가 경로마다 다름 (확정)

| 이름 | 정의 | 유효기간 | 근거 |
|---|---|---|---|
| `restock_proven_skus` | 자체 판매가 한 번이라도 관측된 카탈로그 SKU | **영구** (삭제 로직 없음) | restock.py:296-303 |
| `own_sku_sold_ts`/`own_sale_events` | 자체 판매 시각 | 허브 보존 30분(1800초), 최대 2,000건 | store.py:361-385 |
| `sku_recent` (자체 판매 수) | 위에서 최근 `OWN_RECENT_S`=20분 | 20분 | restock.py:70, 391-392 |
| `sku_market_recent` | 시장 전체 체결 수 | `RECENT_S`=3h | restock.py:58, 393-394 |
| `hot_market_skus` | 시장 3h 체결 ≥2, goods, 35,000~max_goods_price, 현금부자 | 3h | restock.py:500-503 |
| `is_live_bestseller` | bestseller 목록 + (proven 또는 품절 신호 또는 자체 판매 또는 시장 2건 또는 소파3종&현금≥10M) | 혼합 | restock.py:550-554 |
| monitor 자체 판매 | 입고 이후/6h 윈도 | 6h | market_monitor.py:431-436 |
| adaptive `sold` | 최근 20분 own_sale_events | 20분 | adaptive_strategy.py:62-64 |

**충돌 (확정)**:
1. restock.py 모듈 docstring("Market settlements select candidates, never purchase depth", restock.py:3)과 563행 주석("Market popularity alone never justifies depth, even for bestsellers")은 **보유 중인 비실적 SKU**에만 해당합니다. **미보유** bestseller는 시장 3h 체결 2건만으로 `is_live_bestseller`가 되어 `cap`까지 깊이가 허용됩니다 (552, 565-567).
2. 소파 3종은 현금 ≥10M이면 어떤 증거도 없이 bestseller로 취급되어 cap(최대 3)까지 매입 대상이 됩니다 (restock.py:553).
3. "proven"은 영구라서 오래전 한 번 팔린 SKU도 deficit>0이면 깊이 최소 3 목표를 가집니다 (555-559).

**정리 제안**: 증거 종류를 `own_sale_recent`, `market_hot`, `ever_sold`로 명명하고, 깊이 부여는 앞의 두 가지로만 하도록 정리. 소파 3종 하드코딩은 `MEGA_BESTSELLER_CAPS`의 속성(예: `evidence_free=True`)으로 이동.

### (d) 원가 추정 불일치 (확정)

| 위치 | 추정원가율 | 근거 |
|---|---|---|
| restock `add_line`/`cost` | **0.75 × 정가** (모든 SKU) | restock.py:475, 600 |
| 테스트 | 0.75 | test_restock_equity_target_offline.py:41, 56 |
| store 현금하한 주석 | "our **65%** estimate" | store.py:274 |
| market_buyer 상점 효용 | 0.65(`est_cost_floor`), 0.76(`wholesale_floor`) | market_buyer.py:399-400 |
| market_monitor | 등급별 A 0.65 / B 0.50 / C 0.35, 평균 food 0.50, goods 0.59 | market_monitor.py:48-49 |
| 실제 재고 | 각 유닛의 `cost` 필드(실제값) | seller.py:358, store.py:251 |

**실제 영향**: restock은 0.75, 모니터의 기대원가는 0.50~0.59이므로 구매 예산 예측과 모니터 표시 원가 사이에 최대 25%p 차이가 있습니다. 현금하한 마진 주석은 65%를 가정하지만 실제 계획은 75%라 주석이 낡았습니다. 어느 쪽이 실제 시장 수치에 가까운지는 **미확인**(에이전트 설명문 agent.md 수치는 모니터 주석에만 있음, market_monitor.py:45-47).

**정리 제안**: `store.py`에 `EST_COST_RATIO`(구매 예산용, 보수치)와 `EXPECTED_COST_RATIO`(모니터용)를 구분해 명명하고, 주석을 현재 값에 맞춥니다.

### (e) store `max_stock_cap` vs 6M 창 예산 / 75% 목표 (확정, 계산 근거 포함)

`available_budget = max_cap − stock_cost`, `max_cap(stage3) = 2.5M + 0.85×(총자산 − start)` (store.py:264, 278-289). 75% 목표는 `0.75×총자산`입니다.
- `0.75E ≤ 2.5M + 0.85(E − 5M)` 은 `E ≥ 17.5M`(start=5M, injected=0 가정)일 때만 성립합니다.
- 따라서 총자산 6M~17.5M 구간에서는 **목표(0.75E)가 `max_cap`보다 큽니다.** 이 구간에서는 deficit이 항상 양수로 남고, 실제 재고 증가를 막는 것은 75% 목표가 아니라 `max_stock_cap`(및 현금하한)입니다.
- 0.45 목표는 `E ≥ 4.375M`에서 항상 `max_cap` 아래라 이전엔 충돌하지 않았습니다.
- 창 예산 6M은 `tick_budget = min(available_budget, 6M − used)` 이므로, 재고 상한이 먼저 닿으면 6M을 다 쓰지 못합니다.
- 75% 목표는 **상한이 아니라 deficit 계산용**입니다. 재고가 목표를 넘어도(`deficit=0`) 구매가 막히지 않고 그때는 `max_cap`이 상한입니다 (restock.py:329, 555-559).
- `start`·`injected` 실제 값을 모르므로 위 임계는 `start=5M` 가정입니다 (**미확인**).

**정리 제안**: `max_stock_cap`을 `STOCK_TARGET_RATIO`와 연동(예: `max_cap = max(기존식, 목표비율×equity)`)하거나 반대로 목표를 `max_cap`으로 클램프하도록 한 곳에 명시. 현재는 "75% 정책"이 숫자로는 도달 불가능한 구간이 있습니다.

### (f) 주석과 코드 값이 다른 곳 (확정)

| 위치 | 주석 | 코드 |
|---|---|---|
| restock.py:42-43 | "avoids locking 2M+ in a single sofa/PC" | 정가 상한 2,500,000 허용 (457-460) |
| restock.py:63 | PROFITABLE: "scales up to 900k-1.2M on full live catalog" | 풀카탈로그 구간은 6M/8.5M까지 확장 (330-340) |
| restock.py:3-5 docstring | "Market settlements ... never purchase depth" | 시장 3h 체결 2건으로 bestseller 깊이 허용 (552) |
| store.py:274 | "our 65% estimate" | 0.75 사용 |
| store.py:260 | Stage 2 "(up to 4.2M)" | 식은 `2.5M+이익×0.75`, stage2 진입 조건상 총자산 <6M이므로 상한은 start 의존 (store.py:265-267) |
| store.py:89 | "1: Seed (50% cap)" | 2.5M 고정 |
| buyer.py:84, market_buyer.py:143 | fallback "gemini-2.5-flash" | 코드는 3.5-flash-lite → 3.8-flash |
| buyer.py:61-79 | (코드) | `return ""` 뒤의 2.5-flash 블록은 **도달 불가능한 죽은 코드** |
| restock.py:44 | `MIN_GOODS_LIST_PRICE=12_000` | 선언만 있고 사용처 없음 (grep 확인) |
| adaptive_strategy.py:15 | "Base cash-rich cap before equity-target expansion" | 현금부자 탐색 units 95 (restock 344에서 사용) |
| seller.py:1013-1014 | — | `return`이 연속 두 번 (무해한 중복) |
| `call_gemini_api`, `get_gemini_api_key` | — | buyer.py와 market_buyer.py에 거의 동일 코드가 중복 |

### (g) 테스트가 옛 값에 묶인 곳 (grep 근거, 일부 추정)

| 테스트 | 묶인 값 | 문제 |
|---|---|---|
| test_restock_equity_target_offline.py:95 | 잔액 100M에서 `window_cap == 3_000_000` | 카탈로그가 작아(`is_live_catalog` 거짓) **8.5M 분기를 검증하지 않음**. 현재 코드에서 실 라이브는 8.5M |
| test_restock_equity_target_offline.py:122 | `STOCK_TARGET_RATIO`를 0으로 패치 | 0.75 분기(live·≥10M)는 이 테스트에서 영향 없음 |
| test_restock_assortment_offline.py:99, 187, 201 | 총비용 ≤ 450,000, 창 예약 ≤ 450,000 | 잔액 6,000,000 (24행)에서 풀카탈로그라면 기초 900k. 카탈로그가 작을 때만 450k로 성립 (**추정**; 카탈로그 규모는 전부 확인하지 않음) |
| test_restock_offline.py:164, 261 | `WINDOW_BUDGET`, `PROFITABLE_WINDOW_BUDGET` 이하 | 위와 동일 |
| test_restock_velocity_coverage_offline.py:102 | ≤ 3,000,000 | 3.0M 폴백 상한에 묶임 |
| test_adaptive_strategy_offline.py:148 | `window_cap == 900_000` | HIGH_CASH 값 고정 |
| 전 테스트 | `6_000_000`, `6_500_000`, `8_500_000` 분기, 소파 3종, `MEGA_BESTSELLER_CAPS` | 테스트 파일에서 해당 상수 grep 결과 **없음** → 최근 커밋(0691117~a1292c2)의 정책이 테스트되지 않음 |

테스트를 **실행하지 않았으므로** 통과/실패 여부는 **미확인**입니다.

### 추가 발견 (요청 항목 외)
1. **현금부자 기준 불일치**: restock `is_cash_rich`는 5.5M (restock.py:323), adaptive expand는 6.2M, restock SUPER 구간도 6.2M, recover 제외 10M, 목표/상한/cap은 10M, 상한 20M. 임계가 파일에 흩어져 있고 이름이 없습니다.
2. **현금하한과 현금부자 기준 차이**: 현금하한은 최대 1.5M인데 현금부자는 5.5M라 잔액 1.5~5.5M 구간의 구매 한도가 `available_budget`·200k 창 예산·deficit 식으로만 결정됩니다.
3. **hot_market 정의 불일치**: 정렬 키의 "근거 없음" 판단(restock.py:507, 511)과 `need` 분기(573, 579)의 조건이 서로 조금씩 다릅니다(시장 2건 조건이 `active_caps`와 결합될 때만 유효).

---

## 8. 사용자가 검토 중인 신규 정책 초안과의 관계 (**아직 미구현**)

아래는 요청에 적힌 초안 4가지를 현재 코드와 대조한 표입니다. 초안 내용 자체는 사용자가 정한 것이며 이 문서가 정책을 결정하지 않습니다.

| 초안 | 현재 코드의 대응 규칙 | 관계 | 비고 |
|---|---|---|---|
| 재고 나이 = **구매시점** | 모든 코드가 `supplied_at`(공급/입고 시각)을 나이로 사용 (seller.py:365-367, adaptive_strategy.py:56-57, market_monitor.py:418) | 이미 사실상 같음 (**추정**: `supplied_at`이 구매 시각이라는 가정. 시장 정의는 미확인) | 이름만 `purchased_at`으로 통일하면 됨 |
| 회전 4h: 비식품 4h 무판매 = 악성 | adaptive 6h+20분 무판매 / seller 8h·12h / monitor 3h·6h | **3곳과 충돌, 대체** | 위 7(b)의 세 기준을 4h 하나로 통합해야 함. seller 8h/12h 청산 시점과 monitor `STALE_H=3`, `GOODS_LONG_H=6`, `OWN_WINDOW_H=6` 모두 영향 |
| 회전 4h: 식품 = 잔여 유통기한 + 4h 무판매 | seller 식품은 잔여 유통기한 48/24/12h 단계. monitor 식품 <6h | **의미가 다름** (현재는 기한 임박 기준, 초안은 기한+무판매 조합) | seller food 구간표(415-449)와 monitor `FOOD_SOON_H` 재정의 필요 |
| 재구매 금지: 4h 무판매 | restock에는 해당 규칙 없음. proven은 영구 | **신규 규칙**, proven 영구 규칙과 충돌 | restock.py:296-303, 555-559 수정 대상 |
| 재구매 금지: 시장 2h 수요 없음 | 시장 3h 체결(`RECENT_S`=3h)을 후보 정렬·hot에 사용 | 창이 다름(3h vs 2h). `hot_market_skus`·`is_live_bestseller`의 3h 판정이 충돌 | restock.py:58, 393-394, 500-503, 552 |
| 재구매 금지: 악성 이력 | 해당 필드 없음 (`restock_proven_skus`만 존재) | **신규 상태 필요** (악성 SKU 기록) | 소파 3종 증거 없는 매입 규칙(553)과 충돌 가능 |
| 잘 팔리는 상품 예산 비중 ≥50% | 예산은 카테고리/SKU 단위 `core_limit` 60% 분할과 탐색 예산(`exploration_cap`)만 있음 | **개념이 다름**: 현재 60%는 "핵심 vs 탐색" 분할이며 "잘 팔리는 상품" 기준이 아님 | restock.py:363, 520, 602-607. 현금부자·live는 60% 분할 자체가 꺼져 있음(520) |
| (공통) 예산 구조 | 창 예산(200k~8.5M)·`max_stock_cap`·재고목표 | 초안이 비중 규칙을 추가하면 7(a)(e)의 상수 정리가 선행되어야 충돌이 줄어듦 | |

---

## 부록: 확인 범위

- 읽은 파일: restock.py(전체), adaptive_strategy.py(전체), store.py(1-110, 225-420, 535-570), seller.py(전체), market_buyer.py(30-220, 370-1135 중 가격·재시도 관련), buyer.py(30-130), market_monitor.py(40-60, 399-470), 테스트 5종의 일부.
- 읽지 못한 부분: market_buyer.py 887-1035 협상 루프 세부, store.py 나머지(장부 후반·숨김 로직), review.py, forum_writer.py, 나머지 테스트.
- 커밋 diff: 미확인 (위 머리말 참고).

---

## 9. 통합 매입 규칙 (확정안, 2026-11-04 — 아직 코드 미반영)

> 목적: 사람이 예산을 손으로 올리는 방식과 하드코딩 SKU 목록을 없애고, **시장 규모 · 우리 자본 · 인기 신호** 세 가지로만 매입을 결정한다.
> 근거: 11/04 16:39~18:43 KST 정산 6,050건 + seller 로그 24시간 분석(`Temp/opencode/pop-analysis/`), 같은 날 20시대 상위 상점 공개 지표.

### 9.1 재고 규모 — 얼마나 들고 갈 것인가

| 항목 | 규칙 | 11/04 저녁 값 |
|---|---|---|
| 자기자본 E | 잔고 + 보유 재고 원가 | 2,217만 + 1,051만 ≈ 3,268만 |
| 시장 기준 비율 R | 매출 상위 5개 상점의 재고원가 ÷ (현금+재고원가) 중앙값, **0.30~0.75**로 제한(11/04 성장기 조정: 0.60→0.75). 상위 상점 현금은 5,000,000 + 공개 순이익으로 추정 | 0.54 (상위 5개: 0.75, 0.06, 0.82, 0.19, 0.54) |
| 목표 재고 T | R × E | ≈ 1,765만 |
| 현금 하한 | 기존 store 규칙(최대 150만) + 하루치 보관료 | ≈ 160만 |
| 부족분 | max(0, T − 보유 재고 원가) | ≈ 714만 |

- 보관료(storage_per_day 0.01 = 하루 재고원가의 1%)는 4시간 회전이면 단위당 약 0.17%로 작다. 다만 **예상 마진이 보관료 2일치보다 작은 상품은 사지 않는다.**
- 시장이 커지면 R과 E가 같이 움직이므로 별도의 예산 단계는 두지 않는다.

### 9.2 공급 창 예산 — 한 번에 얼마나 살 것인가

- 창 예산 W = min(현금 하한 초과분, max(핫·인기 상품 품절 복원 비용, 부족분 ÷ 2)).
  - 성장기 조정(11/04): 부족분을 공급 창 2번(약 40분)에 나눠 채운다(이전 ÷3).
- 폭주 방지: W ≤ 자기자본의 **50%**(이전 25%).
- 코드 상수: `restock.py` RATIO_MIN/RATIO_MAX, FILL_WINDOWS, WINDOW_EQUITY_CAP.
- 같은 창 반복 실행 시 예약 예산 공유, 창 도중 상한 변경 금지(기존 규칙 유지).

### 9.3 무엇을 살 것인가 — 인기 등급(하나의 정의)

| 등급 | 조건 (최근 40분) | 목표 보유 수량 | 근거: 다음 20분 우리 문의 확률 |
|---|---|---|---|
| **핫** | 우리 상점 문의(견적+품절) 4건 이상 | min(8, 문의 수), 최소 3 | 74% |
| **인기** | 우리 문의 2~3건, 또는 우리 품절 문의 2건 이상, 또는 시장 정산 21건 이상 | 2 | 54% / 32% |
| **관찰** | 우리 문의 1건, 또는 품절 문의 1건, 또는 시장 정산 11~20건 | 1 | 21% / 11% |
| 그 외 | — | 0 (사지 않음) | 1.5~3% |

- 예산 배정 순서는 핫 → 인기 → 관찰. **핫·인기에 창 예산의 최소 50%를 먼저 배정**하고 관찰은 최대 50%. 남는 예산은 쓰지 않는다.
- 아직 보유하지 않은 상품은 우리 문의가 잡히지 않으므로(순환 문제) **품절 문의와 시장 정산**으로 판단한다.
- 같은 등급 안에서는 예상 마진액(정가 × 예상 마진율) 큰 순서. 고가 상품 특례는 두지 않는다.

### 9.4 사지 않는 것 — 재구매 금지

- 재고 나이는 구매 시점(supplied_at) 기준.
- 비식품: 입고 후 **4시간** 동안 그 SKU가 한 번도 안 팔리면 악성으로 기록하고 라운드 끝까지 재구매하지 않는다.
- 식품: 남은 유통기한으로 관리하되, 입고 후 4시간 무판매면 같은 처리.
- 최근 2시간 동안 시장 정산과 우리 문의가 모두 없는 SKU는 사지 않는다.
- 악성 기록은 재시작에도 유지한다.

### 9.5 그대로 두는 안전장치

공급 창 밖 매입 금지, 첫 배포 창 보류, 라운드 종료 36시간 전 매입 중단, 파산 처리, 식품 정가 1만~12만 및 유통기한 96시간 미만 신규 금지(핫 등급 제외), 원가 추정(실제 매입가 이력 우선, 없으면 정가 75%), 같은 창 SKU 중복 예약 방지.

### 9.6 이 규칙으로 대체·삭제되는 것

| 현재 규칙 | 처리 |
|---|---|
| 창 예산 단계(20만, 45만, 90만, 120만, 280만, 350만, 600만, 650만, 850만) | 삭제 → 9.2 |
| 재고 목표 0.45 / 0.75, store max_stock_cap | 삭제 → 9.1 |
| TARGET_CAPS, HIGH_VALUE_CAPS, LIVE_BESTSELLER_CAPS, MEGA_BESTSELLER_CAPS, 소파 특례 | 삭제 → 9.3 등급 |
| 탐색 상한(35/55/60/120종, 80/95/250개), 창당 신규 4종 | 삭제 → 9.3 관찰 등급 + 예산 비율 |
| 우리 20분 판매 ×1.25 깊이(최대 8) | 9.3 핫 등급으로 통합 |
| adaptive 확장/회복 모드 | 매입 결정에서 제외, 지표 기록만 유지 |
| '잘 팔리는 상품' 4가지 정의, '오래된 재고' 4가지 정의 | 9.3 / 9.4 하나로 통일 |

### 9.7 구현 전 확인할 것

- 우리 상점 문의를 SKU·시각 단위로 저장해야 한다. 현재 store.record_inbound_inquiry의 저장 단위 확인 필요. seller를 고치면 VM 재시작이 필요하다.
- 상위 상점 현금은 공개 순이익으로 추정한 값이다(파산 지원금 등 미반영).
- 등급 기준값은 2시간 표본으로 정했다. 하루치 데이터로 재검증 후 조정한다.

---

## 10. 라운드 종료 대비 축소 계획 (2026-11-04 확정, 변경 가능)

> 라운드 종료: **11/17 23:59 KST** (`store.ROUND_END_TS`). 종료 시 남은 재고는 가치 0이므로, **종료 4일 전부터** 재고를 단계적으로 줄인다.
> 이 표는 그때 상황에 따라 다시 바꿀 수 있도록 **코드에서 표 하나(`ENDGAME_SCHEDULE`)로만** 관리한다. 바꿀 때는 이 문서와 그 표를 함께 고친다.

| 구간 (종료까지) | 기간 (KST) | 재고 비율 R 상한 | 매입 대상 등급 | 핫 최대 깊이 |
|---|---|---|---|---|
| 96시간 초과 | ~ 11/13 23:59 | 평소 규칙(9장, 최대 0.75) | 핫·인기·관찰 | 8 |
| 96~72시간 | 11/13 23:59 ~ 11/14 23:59 | 0.50 | 핫·인기·관찰 | 6 |
| 72~48시간 | 11/14 23:59 ~ 11/15 23:59 | 0.30 | 핫·인기 (관찰 중지) | 4 |
| 48~36시간 | 11/15 23:59 ~ 11/16 11:59 | 0.15 | 핫만 | 2 |
| 36시간 이하 | 11/16 11:59 ~ | 매입 중단 (기존 규칙) | — | — |
| 24시간 이하 | 11/16 23:59 ~ | seller 청산 할인 (기존 `clearance_factor`) | — | — |

- R 상한은 9장의 시장 기준 비율과 **둘 중 작은 값**을 쓴다. 9장의 최소값 0.30이 축소 구간 상한을 다시 끌어올리지 않는다.
- 재고가 이미 목표보다 많으면 부족분은 0이므로 매입하지 않고, 판매로 자연 감소시킨다(4시간 회전 기준).
- 재구매 금지, 현금 하한, 공급 창 규칙은 그대로 적용한다.
- 진단 로그에 현재 구간 이름과 적용 상한을 함께 남긴다.

---

## 11. 재고 처분 규칙 (2026-11-04 확정)

> '악성재고'라는 말을 두 의미로 섞어 쓰던 것을 나눈다: **다시 사지 않을 품목**과 **빨리 팔아야 할 재고(할인)**.

### 11.1 재구매 제외 품목 (`no_rebuy`, 구 `dead_skus`)
- 입고(구매 시점) 후 4시간 동안 그 SKU가 한 번도 안 팔리면 라운드 끝까지 재구매하지 않는다.
- **할인하지 않는다.** 판매 가격은 11.2·11.3 규칙만 따른다.
- 배포 후 4시간 관측 유예, 재시작 유지, 상한 500개(9.4와 동일).

### 11.2 비식품 할인 (보유 시간 기준, 기존 유지)
| 보유 시간 | 최저가 | 시작가 상한 |
|---|---|---|
| 8시간 미만 | 등급별 정상 규칙 | — |
| 8~12시간 | 원가 × 1.02 | 정가 × 0.88 |
| 12시간 이상 | 원가 × 0.95 | 정가 × 0.82 |

### 11.3 식품 할인 (남은 유통기한 기준)
근거: 우리 buyer가 받은 식품 주문 993건의 사용 기간 요구(중간값 72시간). 남은 유통기한별 응할 수 있는 주문 비율 — 72h 이상 53%, 48h 39%, 24h 29%, 12h 11%, 4h 4%. 남은 시간이 요구 사용 기간보다 짧으면 가격과 무관하게 거래 불가이므로, 응할 수 있는 buyer가 남아 있을 때 할인을 시작한다.

| 남은 유통기한 | 단계 | 최저가 | 시작가 |
|---|---|---|---|
| 72시간 이상 | 정상 | 등급별 기존 규칙 | 기존 규칙 |
| **72~48시간** | **1단계 (신규)** | 원가 | 정가 × 0.80 |
| 48~24시간 | 2단계 | 원가 × 0.80 | 정가 × 0.72 |
| 24~12시간 | 3단계 | 원가 × 0.55 | 정가 × 0.65 |
| 12시간 미만 | 처분 | 원가 × 0.35 | 정가 × 0.50 |

### 11.4 식품 매입 기준
- 신규 매입은 **유통기한 180시간 이상** 식품만(이전 96시간). 근거: 주문의 42%가 180시간 이상 사용 기간을 요구.
- 9.5의 "96시간 미만 신규 금지(핫 제외)"는 "180시간 미만 신규 금지(핫 제외)"로 바꾼다.

### 11.5 라운드 종료
- 종료 24시간 전부터의 청산 할인(`clearance_factor`)은 11.2·11.3 위에 그대로 적용된다(10장).
