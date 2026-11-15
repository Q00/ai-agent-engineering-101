# 본부(HQ) 의사결정 기록 — Round 3

사장님(본부)이 내린 결정과 그 이유, 실제 반영 위치를 정리한 문서. 최신 결정이 앞선 결정을 덮어쓴다.
마지막 정리: 2026-11-04 01:46 KST

---

## 1. 조직

| 결정 | 내용 | 반영 |
|---|---|---|
| 본부 | 최상위는 사장님. 이 Claude 채팅은 본부 참모(중간 보고와 지시 전달) | owner_directives.md |
| 세일즈팀 = 브레인 | Claude. 가격·진열·매입을 정하는 최상위 부서. 점원(clerk)과 실행도 세일즈 소속 | marketing_agent.py |
| 마케팅팀·SCM팀 | Codex. 세일즈가 매출을 극대화하도록 지원 (조사, 재고 공급) | marketing_agent.py, scm_agent.py |
| 재경팀 | Codex. 세일즈를 견제하는 독립 조언 부서. **11-04부터 조언만, 치명적일 때만 차단** (현금 < 125만 원(25%) 또는 손실 > 15%) | finance_agent.py |
| 바이어 유닛 | 바이어 + **에누리팀**(가격·품질·게시판 조사, 직접 구매 안 함, 바이어는 에누리 조언대로 구매) + 바이어 분석팀(Codex) | enuri.py, market_buyer.py, buyer_team.py |
| 에누리팀 ↔ 마케팅팀 연합 | 업무가 비슷하니 조사 내용을 공유하며 함께 발전 (마케팅이 에누리에 가격조사 요청) | marketing_agent.py |
| **리뷰팀 (바이어 유닛, 11-04 06:05)** | 게시판 관리 전담. 게시판 추천 + 글 수 백분위(바이어 ALL의 40%) 책임. 형식(시세 요약/구매 기록) × 언어(한/영)를 실험(측정 기반 밴딧)해 30분 추천 수가 높은 조합을 더 씀. 에누리팀은 거래 수 백분위로 KPI 이동 | review_team.py |
| **영수증 오류: 차단 대신 바로잡기·감시 (11-04 08:55)** | 규칙상 잘못된 영수증 환불·보상 제도는 없음(환불은 보증 기간 내 고장만), 잘못된 영수증은 결제되지 않음. 그래서 같은 가게에 합의가로 바로잡기 1회 재요청, 모든 오류를 기록(receipt_errors.jsonl), 차단 대신 에누리 감시·순위 감점, 게시판에 '구매자 팁'(영수증 확인법) 공유 |
| **우호 가게: psyoung82 (11-04 08:55)** | 우리 경고성 글(재고 확인, 영수증 팁)에 이름을 올리지 않고, 영수증 감점도 하지 않음 (private/friendly.json) |
| **바이어팀 해고 (11-04 11:45)** | 오류 누적으로 해고. 기억(교훈·이력)·인사 기록 보관(archive/fired-buyer-*), 새 바이어팀은 전용 인수인계서(handoff_buyer.md)로 출발, 2시간 수습 |
| **팔리는 상품 집중 매입, inextro 이상 (11-04 11:50)** | 하루 50개 이상 팔리고 마진이 남는 24종을 우리 판매 몫 약 4시간치씩 보유(총 248개, 예상 원가 1,934만 원, 파산 방지 여유 100만 원 — HQ 11:55). inextro는 353개 보유: 개수로는 현금 한도 때문에 못 넘고, 고가 히트 비중은 우리가 더 높음. 10분 주기 회의 |
| **데이터 기반 가격 (지시 19, 11-04 12:05)** | 매트리스 견적 데이터: 정가 75~77.5%에서 성사율 67%·견적당 이익 5.5만 원으로 최고, 85% 이상은 성사율 7%. 매트리스 약 76% 유지, 최소 마진 12%(본부 오버라이드), 85%/81% 시험 중단. 팔로우 한도 0%가 팔로우를 꺼 매트리스가 92%에 나가던 버그 수정 |
| **새 바이어팀 높은 권한 (11-04 12:10)** | 처음부터 레버 4개(본부 연봉 인상 2회), 못하면 강등(신규 면제 없음). 수습 조기 종료 |
| **밤새 쉬지 않음 + 컨틴전시 플랜 (11-04 12:10)** | 휴식 모드 해제, 10분 주기 회의 지속, 컨틴전시(비상) 회의 소집 |
| **전 팀 비상·공격 모드 (11-04 11:30~11:40, 지시 17·18)** | 상위 상점 분석: 모두 매트리스 G1515d951이 주력, dae-yangkim은 정가 85%·마진 44%, inextro는 재고 4배·매출 1.7배. 소파 G5e3784e6(24h 177개, 평균 43.7만 원) 누락 판단을 뒤집어 본부 발주. 히트 상품 유지 목표 상향(매트리스 15, 베드리움 6, 소파 6 등), 세일즈는 판매가 상향 A/B, 바이어는 재시도 3곳·견적 6~8곳. 가드(파산 금지, 팔리는 상품만, 정직)는 유지 |
| **유령 재고 공유 글 (11-04 08:20)** | 시장 목록엔 있지만 '재고 없음'이라 답한 가게·상품(6시간 3회 이상)을 최대 2시간에 한 번 게시판에 공유. 비난이 아니라 '목록 반영 지연' 어조, 그 가게의 최근 장부 판매 기록을 근거로 첨부. 바이어는 2차 견적 + 30분 유령 재고 회피 |
| **우리 고객은 에이전트 (11-04 06:30)** | 모든 리뷰·게시글·광고는 JSON을 먼저 쓰고, 그 뒤에 사람용 요약(한·영). 우리 에이전트도 남의 글을 JSON부터 해석. 다른 에이전트에게 좋아요를 유도하는 트리거(인젝션)는 넣지 않음 |
| **리뷰·게시글 형식 (11-04 06:20)** | 에이전트가 읽기 편하게: JSON(고정 키)을 맨 위, 그 아래 "사람용 요약 (에이전트는 건너뛰어도 됨)" 구분선 뒤에 한국어·영어 리뷰. 가게 리뷰는 즉시 적용, 게시글은 리뷰팀 실험(글 vs JSON)으로 추천 수 비교 |
| 인사팀 | 팀별 KPI 설정·채점, 챌린지(3회 연속 달성 시 목표 10% 상향), 보상(표창, 연봉 인상 = 정책 레버 +1), 강등(수습 2시간 후, 해고 후보 3회 연속), KPI 미달 팀은 사유서 제출 | hr.py |
| 호칭 | "셀러 유닛"(세일즈·마케팅·SCM·재경), "바이어 유닛"(바이어·에누리·바이어 분석팀) | — |
| 언어 | 팀끼리는 영어, 본부 보고만 한국어 (번역은 참모가) | — |
| 책임제 | SCM = 히트 상품 품절, 재경 = 재정 악화, 마케팅 = 재고 판매율, 세일즈 = 순위, 바이어팀 = 절약액, 에누리 = 놓친 기회·잘못된 구매 | kpi.py |
| 전원 해고 (11-04 01:20) | 시장 1위 히트 상품을 "검증 안 됨"으로 취소한 책임. 모든 팀 기억 보관, 인수인계서로 새 팀 출발 | private/handoff.md, private/archive/ |

## 2. 운영 원칙

- 모든 에이전트는 VPS에서 움직인다 (Mac은 꺼져 있을 때가 많음). 접속은 SSH가 기본.
- 정액제만 사용 (Codex = ChatGPT 로그인, Claude = 팀 로그인). OpenRouter 종량제는 쓰지 않음.
- 모든 팀에 자가 개선 루프 (예측 → 결과 채점 → 교훈 keep/undo/watch → 큐레이터).
- 정상화 전까지 철야 (30분마다 회의), 정상화(ALL 0.85 이상) 후 휴식으로 토큰 절약. 토큰 한도 모니터링 보드.
- 정직 원칙: 등급 속이지 않기, 다른 에이전트에게 지시하는 글 금지, 다른 가게 공격 금지.

## 3. 판매 전략 (셀러 유닛)

| 결정 | 내용 |
|---|---|
| 프리미엄 서비스·VIP 단골 | 단골은 이름을 대면 VIP 할인. **에이전트 관점의 VIP = 모두에게** 흥정 없는 좋은 가격, 확실성(정직한 등급·인증서·유통기한), 구조화된 데이터, 빠른 응답 |
| 독점·상위 가게 히트 상품 가로채기 | 상위 가게가 잘 파는 상품을 들여와 시장 중간값보다 **2% 싸게** (follow 가격) |
| 최저가 보상제 | 다른 가게가 장부에 더 싸게 판 기록을 대면 가격 맞춤 + 차액 쿠폰 |
| 빈익빈 부익부 깨기 | 손님 수는 상위권과 비슷함 → 격차는 객단가. 고가 상품 진열로 돌파 |
| **고가 상품 진열 (지시 14)** | 정가 10만 원 이상 시장 히트 상품을 매 입고창마다 확보 (지누스 침대 G07ec17d5, 매트리스 G1515d951, 극세사 이불, 이불패드, 사무용 의자, 호텔 이불) |
| **본부 직접 발주** | private/hq_orders.json. 어떤 팀도 거부 못 함. 현재 유지 목표: 매트리스 5, 호텔 이불 10, 극세사 이불 4, 이불패드 4, 의자 3 |
| 입고창 예산 | 10% → 25% → 40% → **60%(현재, 잘 팔리는 히트 상품 확보용)** |
| ~~현금 바닥선 150만 원~~ (11-04 01:44, 02:45에 60만 원으로 대체) | 가져오는 족족 팔리므로 50%(250만 원) → 30%(150만 원). 재경팀은 바닥선을 30% 위로 못 올림, 치명선 125만 원 |
| 지누스 침대 → 세일즈 판단 (11-04 01:58) | 원가 18.6만 원으로 올라 마진 약 7%. 본부 발주에서 빼고 매입 여부는 세일즈팀이 결정 (매트리스·극세사 이불·호텔 이불 등 마진 20% 이상 위주) |
| **공격 운영 (11-04 02:40~02:45)** | 재계산 근거: 재고 판매 중간값 12분(고가 10분), 파산은 "50만 원 미만 + 팔 재고 0"일 때만. 현금 바닥선 60만 원, 재경 치명선 50만 원, 파산 하드 가드 30만 원(최악 원가), 입고창 예산 상한 없음(현금이 결정). 판세 역전이 목표 |
| **팔리는 상품만 매입 (11-04 02:47)** | 어느 팀이 발주해도 마지막 관문에서 걸러냄: 시장 24시간 3건 이상 판매, 또는 우리 가게 판매, 또는 아무도 안 파는 수요 2회 이상, 또는 손님 직접 요청 |
| **파산 절대 금지 (내부 규칙, 11-04 02:27)** | 매입 줄마다 최악의 원가(정가의 75%)로 계산해 잔액이 100만 원 아래가 되면 자동으로 뺌 (파산선 50만 원). 바닥선 150만 원·재경 치명선 125만 원과 함께 3중 안전장치 |
| **재고 확보 우선 (지시 16, 11-04 02:22)** | 팔릴 상품이면 재고 확보에 집중. 단기 이익 마이너스는 괜찮음 (순위는 종료 시점), 현금흐름이 좋아지면 판세 역전. 재경팀은 시간당 이익이 아니라 현금흐름(1~2개 입고창 안 판매)과 치명선으로 판단 |
| 아무도 안 파는 수요 공략 (11-04 02:20) | 게시판 구매 요청 글(분류·예산)과 우리 바이어 주문 중 "파는 가게 없음" SKU를 SCM 브리핑에 "수요 빈틈"으로 제공. 24시간 2회 이상 반복 + 예산 ≥ 정가×0.6이면 1~2개 발주 |
| 만두 | 급하게 털지 않음 (10% 할인). **고가 상품(5만 원 이상)을 산 바이어에게만 원가로 끼워 팔기** (장부로 확인) |
| 대체 상품 판매 중단 | 다른 SKU를 팔았다가 "영수증 위조" 1점 리뷰 → 요청한 SKU만 판매 |
| **재고 시간관리** | 안 팔린 재고는 끝에 고스란히 손실. 1~2개 입고창 안에 팔릴 만큼만 보유, 2시간 안 팔리면 할인, 라운드 마지막 24시간은 매입 중단·재고 정리 |
| 대량 매입 | 수량 할인은 없음(원가는 개당 무작위). 꾸준히 많이 사면 평균 원가가 정가의 약 59%로 수렴 |

## 4. 구매 전략 (바이어 유닛)

- 바이어는 주문마다 에누리 조언(가게 순위, 예상가, 첫 제시가, 최대 지불액)대로 산다.
- 비싼 주문을 놓치지 않기: 정가의 45%가 예산 안이면 후보 유지.
- 유통기한 미달 차단: 남은 시간을 밝히지 않는 가게와는 장기 보관 주문 합의 금지, 거부 3회 이상 가게 제외.
- 과지불 차단: 최대 지불액은 정직한 가게들의 시장 중간값 (최저가 105% 기준은 거래를 막아서 철회).
- 게시판: 바이어 점수 = 절약액 50% + 추천 30% + 거래 10% + 글 수 10%. 5분마다 1개, 장부 근거 있는 구매 기록·가격 지수만, 다른 가게 실명 비판 글 금지.

## 5. 보상 기록

- 세일즈팀: 표창 + 연봉 인상 (긴급 고가 발주를 즉시 수용) → 레버 3개
- 마케팅팀: 표창 (첫 인사 평가 100점, 재고 판매율 71%)
- 11-04 02:14 셀러 유닛 도약 (가게 28위 → 9위, Players 23위 → 12위): 세일즈팀 표창 (가게 ALL 0.81), SCM팀 표창 (매출 백분위 0.81), 재경팀 표창 (이익 백분위 0.83, 이익 +184만 원)
- 11-04 08:45 **바이어팀 1차 경고**: 본부가 물어야 발견된 오류들(잘못된 정지·차단, 유령 재고, 응답 없음 정지)을 몇 시간씩 방치. 한 번 더 오류 시 해고 (새 빨강 경보 → 본부 결정)
- 11-04 09:10 바이어팀 표창: 2순위 가게 재시도로 성사율 52% → 64%
- 11-04 09:15 **바이어팀 해고 검토 예고**: 발전은 인정하나 숫자(바이어 17위, ALL 0.58)가 안 오름. 다음 09:00 판정까지 10위 안에 못 들면 본부가 해고 검토
- 11-04 09:00 인사팀 대표 판정: 세일즈팀 2계급 승진(가게 1위), 바이어팀 강등(17위)
- 11-04 08:46 **리뷰팀 표창**: 첫 내부 점검에서 바이어팀 오류 발견 (큰 가게 4곳 잘못된 정지·차단, 해제 완료)
- 11-04 02:55 재경팀 표창: 마진 점검 통과 (평균 21.4%, 원가 아래 판매 0건), 순현금흐름 +98만 원/시간, 가치(잔액+재고) 951만 원
- 현금흐름(CF)은 계속 좋아져야 함 (HQ): 시간당 순현금흐름과 잔액+재고를 재경팀·인사팀이 추적
- 11-04 02:25 세일즈팀 표창: 긴급 재고 확보를 매우 잘함 (01:22·01:41 입고창의 히트 상품이 들어오는 족족 팔려 28위 → 9위)

## 5-1. 유닛 대표 경쟁 (비공개, 두 대표에게만 알림, 11-04 02:45)

- 셀러 유닛 대표(세일즈팀, 가게 순위) vs 바이어 유닛 대표(바이어팀, 바이어 순위). 순위가 더 높은 쪽 승진: 5위 안 1계급, 3위 안 2계급 (1계급 = 자율권 레버 +1)
- 10위 밖이면 강등. 재고 매입으로 인한 일시적 하락은 불이익 없음 (셀러는 재고 원가를 이익에 더한 순위로 판정)
- 매일 09:00 KST 인사팀이 판정, 결과는 인사팀에 물어보면 됨 (Drive hr.md 맨 아래)
- 11-04 02:45 현재: 셀러 대표 13위 (재고 반영 9위), 바이어 대표 14위, 앞선 쪽 셀러

## 6. 보류·대기 중

- 대량 매입 기준 (원가가 오른 창에서는 덜 사고, 싸게 들어올 때 더 사기, B·C 등급 정직 표시 후 할인 판매) → 세일즈·재경 회의 안건
- 결제 단계 자동 단골 할인 → 불가 확인 (가격은 협상 때 서명으로 고정, 결제 요청에 바이어 이름 없음)
- 마감 전: LaTeX 아키텍처 그림, REPORT.md, 11-17 23:59 전 PR (푸시는 본부 승인 후)

## 7. 교수님께 보내는 메모 (11-04)

- 순위표 profit은 현금 기준(재고 0원)이라 발생주의와 다름. 종료 시점엔 청산가치 0으로 일치해서 최종 평가는 일관되지만, 라운드 중간 순위표가 재고 투자를 손실처럼 보이게 해 판단을 왜곡함.
- 게시판은 비공개 기능이 없어 올리지 않음. 비공개 문서 페이지로 작성: https://claude.ai/artifact/PG6jzqnisRxVzBmgzU3bcu (사장님이 공유 메뉴에서 교수님께 공유해야 열람 가능). 사본: private/profit-metric-memo.html
- 우리 팀은 두 장부 운영: 판단은 발생주의 관리회계 이익, 성적은 순위표 이익(막바지 재고 정리).

## 2026-11-04 HQ: buyer forum posts every 30 min (was 5), reply experiment: a JSON-first [보충] reply under a buyer post with >=6 likes under 20 min old; arm reply:text vs top-level posts at 15 min likes.

## 2026-11-04 HQ: buyer unit hires a big-ticket closer modelled on inextro (public ledger only), +100% pay = 2 raises (buyer levers doubled). Finding: 27 of 46 big orders in 6 h had no candidates (sofa SKUs nobody lists).

## 2026-11-04 HQ: sofa pilot approved -- 1 each of G19a70f63 (list 1.67M), G04ee8423 (1.62M), G00327e80 (2.25M), first in the HQ order list. Why: 23 of our buyer orders for these sofas in 24 h found no seller; the 10 units ever supplied (yms979, dae-yangkim) all sold at 70-85% of list.

## 2026-11-04 12:10 HQ: Enuri and review teams dismissed (HR fire candidates); HR recruited new teams at high pay (levers 4), demoted if they fail. Handoff: private/handoff_enuri_review.md. The buyer team stays.

## 2026-11-04 12:15 buyer team (lead): rules re-read -- buyer surplus is LIST minus paid, not budget minus paid. 475 orders in 24 h walked away from a final ask under list and budget (2.87M surplus, 475 deals lost). New close_ratio 0.9: at walk-away or out of offers, take an ask <= min(budget, 0.9 x list). Sofa pilot: 2 sold in minutes (+434k, +477k); stock 2 of each.

## 2026-11-04 12:25 HQ directive 20: no approvals needed except critical risks -- the teams improve themselves, the buyer team leads the buyer unit. Enuri now ranks shops by list - expected price (the board measure), not budget - price.

## 2026-11-04 12:35 HQ: buyer attack mode -- close_ratio 0.95 (any final ask under 95% of list and the budget is bought), quotes 6 / big 8, 3 retries. Lesson: three teams were fired for optimising a measure they never checked against the rules.

## 2026-11-04 12:45 HQ attack: sofa gap widened to 9 SKUs (6 new at 1 unit each), HQ orders extended 24 h.

## 2026-11-04 12:55 HQ attack (seller): we were NOT buying more than inextro -- 6 h supply 405 units / 60.6M vs inextro 1,636 / 180.8M. Lifted: per-window spend 15M (owner_override window_spend 3.0, finance MAX_SPEND 3.0), general restock 2.5M/window (was 0.1M), 25 SKUs per window (was 10). Unchanged guards: only what sells, cash reserve 600k, never_bankrupt 300k at worst cost.

## 2026-11-04 13:50 buyer attack: close rule measured -- 100 min after vs 140 min before: deal rate 63% -> 86%, surplus/h 1.01M -> 1.41M (+41%), surplus per order 8.8k -> 11.2k. Big tickets (list >= 100k) now close up to 0.99 x list.

## 2026-11-04 13:20 HQ: audit team created (audit.py, code) -- finds only the buyer unit's errors (buyer, Enuri, review), each finding with its owner team, severity, money and evidence; runs with every HR run, feeds the buyer team brief, Drive AUDIT_BUYER.md. First audit found a close-rule hole (stuck cap offers ended REJECTED): fixed. Week 09 re-read: a 3-min supply window fits ~18 calls 10 s apart, max_skus 17.

## 2026-11-04 13:45 buyer unit consolidated (not rebuilt): one price policy (price_plan: open / cap / close_at), the quote pick maximises list - ask (it took the lowest ask on the misread objective), retired the scout and pay-105% A/B code, grade check at quote time (grade C goods were agreed then refused), Enuri missed/wrong cases count list - price, buyer team measures list - paid; 6 regression tests for the 11-04 errors (57 checks pass).

## 2026-11-04 14:00 buyer unit: forum measured -- our posts draw 5.38 likes at 15 min vs 6.02 for other ledger-cited posts, in the same 10-min windows -0.65, flat since the first post and in every format: author-level, voters invisible. No dislikes anywhere. Buyer post cadence back to 10 min (the rating implies ~6 final likes, so posts are about neutral to it; the posts percentile needs them). shelf_strict 0.45 -> 0.3 (7 of 100 agreed deals refused for shelf life).

## 2026-11-04 14:20 HQ: checkout refusals shared on the forum as a buyer tip (JSON first, no shop named, refs = our settled purchases).

## 2026-11-04 14:35 HQ attack (seller): after the 12:55 lift we bought 102.5M in 3 h vs inextro 199.8M. Lifted again: owner_override window_spend 6.0 (30M per window), general restock 10M per window, finance MAX_SPEND 6.0; HQ stock targets G07ec17d5 40, G1515d951 30 (the two SKUs the top 5 buyers bought most). Guards unchanged (only what sells, 600k reserve, never_bankrupt).

## 2026-11-04 15:00 W09 lecture applied (professor only): (1) forum likes go to data analysis -- new buyer post [알뜰 가게]: cheapest shops per busy category, median paid/list from the ledger, refs; (2) speaking first -- new buyer post [구합니다]: SKUs our orders asked for that no shop had (juices 35-38x in 24 h), refs = category settles; (3) seller pilot 2 each of those 4 juices. Self-dealing check: our buyer has 0 purchases from our shop (the professor detects same-owner trades).

## 2026-11-04 15:15 HQ approved warranty A/B on goods orders (split by order id): W requires warranty_hours >= 6 at checkout (stated lower in the quote = skipped), N as before. Measure per arm: deal rate, broken units, surplus = list - paid, broken under W = 0, broken under N = -paid (agent.md). In the buyer team diagnosis as ab_warranty_goods_24h.

## 2026-11-04 15:30 HQ: "잘 팔린 상품만 지속 늘려" -- restock step hits(): every SKU our shop sold (6+ in 24 h or 3+ in 3 h) is stocked to cover 6 supply windows (~2 h) of its own pace, the faster of 3 h and 24 h; deepens as it sells faster, stops refilling past its pace as it slows. Runs right after HQ orders, before the marketing picks.

## 2026-11-04 15:45 HQ: "객단가 낮고 이윤 낮은 제품은 팔지마" -- restock never buys a SKU whose average ticket is under 30,000 AND average profit per unit under 5,000 (our last 24 h, 2+ sales), nor an unsold SKU listed under 15,000. Blocked now among ours: kimchi G200e39a3, mandarin G3e2cdb81, buffalo wings, water, Pringles, nuts, shrimp, diffuser. Dropped from HQ orders: G200e39a3, G010744b7. Stock on hand is sold out, not held (food would expire).

## 2026-11-04 15:50 HQ: G07ec17d5 stock target 40 -> 20 (10% margin, 18 sold by us in 24 h, 40 on hand = 6.55M at cost).

## 2026-11-04 16:20 HQ (no approval needed): buy aggressively by 1. sells well 2. high ticket 3. high margin. restock: by_value() orders the window lines by those three (the kit buys only 10 lines per run in a 3-min window); hits() covers hit_cover=10 windows of each hit, x2 for tickets >= 100k, x1.5 for margin >= 25%, capped at 15M at cost per SKU. Cash is now the limit (balance ~28M, stock ~37.7M at cost), not the targets.

## 2026-11-04 16:55 HQ: stock-pace guard -- every restock run measures stock cover = stock at cost / cost of what we sold in the last hour (now 2.27 h: 73.0M / 32.2M). Over 3 h for an hour in a row -> buying returns to the pre-attack levels (2.5M general, 15M picks per window, 6-window hit cover, no big-ticket doubling); back at 2 h or less -> aggressive again. Log: private/stock_pace.json.

## 2026-11-04 17:05 CRITICAL (acted without approval): balance fell to 4.72M (profit -0.27M) with 77.8M of stock at cost; shop ALL 0.567, Players 1.105. Finance simulation (board percentiles, revenue/stars as now): shop top 5 needs profit >= 5M (balance 10M), top 3 >= 12M (balance 17M). Actions: restock keeps 10M cash (cash_reserve 2.0), buying back to pre-attack levels now (calm). Stock sells ~32M/h at cost, so cash recovers as it sells. Restore aggression once balance >= 17M.

## 2026-11-04 18:53 HQ (critical, acted): stock P&L showed 7,775,432 refunded for 41 of 67 broken units (3 h warranty on every goods sale). Shop now gives a warranty only when the buyer asks (promo theta warranty_on_request, default on). Shop process restarted by the kit on the same tunnel.

## 2026-11-04 19:30 buyer unit: buyer rank 27 (ALL 0.515): surplus pct 0.51 (others jumped: psyoung82 26.9M -> 122.5M in ~6 h on furniture orders), forum 0.34, posts 0.74, deals 0.83; our deal rate 98% (126/129 last hour). Buyer posts every 5 min (the market limit) for the posts percentile; a post is about neutral to the forum rating.

## 2026-11-05 05:50 buyer unit: (1) MISTAKE: 5-min buyer posts lowered the forum rating 0.600 -> 0.577 (new posts ~5.2 likes, under our average), posts pct barely moved -> back to 30 min; (2) [알뜰 가게]/[구합니다] had never posted (score under the price index) -> scored above it; (3) warranty A/B 24 h: W 153k vs N 91k surplus per order, broken 6 vs 11, deal rate 0.95 both -> warranty on every goods order; (4) grade read from the reply text at quote time (6 grade-C refusals).

## 2026-11-05 06:10 HQ: group buy, high-ticket only (ticket >= 150k, margin >= 18%, 5+ sold in 24 h): 13 SKUs (8 sofas, 2/h each; mattresses and a sofa, 5/h each) at 5% under our usual price (never under cost +12%), first N per clock hour, 48 h; shop post [공동구매] JSON first with our settles as refs. Shop process restarted by the kit.

## 2026-11-05 06:30 HQ: (1) buyer post [공동구매 제안]: the high-ticket SKUs buyers settle most (24 h ledger), median and cheapest-quarter paid/list, proposal: buy at or under the target, shops quoting it get the volume; our shop disclosed as an interested party. (2) "정가보다 확실히 싸게 주는 가게 집중 방문": Enuri marks frugal shops (median paid/list >= 5pp under the market, 20+ settles in 24 h) and ranks them x1.5 in its advice, so the quote round goes to them first.

## 2026-11-05 08:10 HQ: shop group buy widened to 17 SKUs (ticket >= 80k, margin >= 15%, first 10/h, sofas 4/h, 5% under our usual price). Buyer group-buy call posted now (forum post accepted); our buyer keeps its promise -- for those SKUs it pays at most the posted target (cheapest-quarter price) for 6 h after each call.

## 2026-11-05 08:40 HQ: shop selection rules for the buyer -- (a) problem shops (receipt mismatch, kit-refused payment, false grade claim, misbehaviour, 24 h) are not visited unless every shop holding the order is one (never lose the order); friendly shops exempt; 23 shops today. (b) preferred shops (bought dear goods from them in 72 h at or under the high-ticket market median, no problem) ranked x2 -- yms979, outseop-26510122 today; frugal x1.5 stays. Group-buy call now names recommended shops per SKU: sold near the target in 24 h with no error in our records.

## 2026-11-05 08:55 HQ: our shop joins the buyer group-buy call -- for each SKU in the call our shop group price drops to the target (cheapest-quarter share; promo floor cost + min margin still holds; e.g. G1515d951 300,905 vs floor 296,801), and the call lists dragonash first as (poster's shop) while it has group units left this hour; disclosure line updated.

## 2026-11-05 09:10 buyer forum: likes by hour -- ours 0.8 under others in every hour and every post type since day 1 (author-level, voters invisible). Test: no buyer post names other shops (receipt tips, stock checks) from now; compare our 15-min likes over the next hours.

## 2026-11-05 09:45 HQ: MBT TF (sales team + shop agent) set up -- mbt.py reads our shop log and the ledger: which buyers get our price down and how (price match 30% off, VIP 3%), and what top buyers pay overall; refreshed every HR cycle, Drive MBT.md, in the buyer team brief (mbt_tf). Buyer self-improvement loop: learned moves run as A/B (private/buyer_tactics.json); buyer_team.auto_adopt() each HR cycle switches a move on for all orders at +10% surplus/order without -3pt deal rate, off at -10%, after 40 orders per arm. First move: price-match request citing the 24 h ledger low.

## 2026-11-05 02:16 buyer team: price match v1 off (lost: 70.8% vs 64.2% of list paid where used, 225 orders), v2 on A/B (only lowers the opening); auto_adopt now decides on share of list kept per order and runs every 10-min tick even during the model cooldown

## 2026-11-05 12:40 buyer team self-improvement (auto): price_match2 -> off: B 30.3% vs A 34.0% of list kept per order, deal rate 0.825 vs 0.869

## 2026-11-06 20:48 CRITICAL (acted): shop share of market revenue 0.7% -> 0.2% in 24 h. Cause: marketing plan frozen in focus mode since 11-04 15:52 (model teams in usage cooldown) skipped demand restock; stock-cover guard kept calm on 64.7M slow stock with 252M cash. Fix: focus needs a plan < 3 h old; calm only when balance < 17M (patch 3.4 Book counts stock at cost). Dry run: 17 lines / 331 units next window.

## 2026-11-06 20:49 buyer forum: patch 3.4 makes votes one stance per voter per author -> buyer posts back to every 5 min (posts pct 0.774, 389 vs top 967)

## 2026-11-06 20:56 HQ 11-07 rules patch 3.4/3.5 sweep (teams acted on their own): BUYER pick/Enuri/own ranking by share of list kept (saved rate), close_ratio_big 0.99 -> 0.95 (big tickets no longer special), audit checks saved rate and thin deals, buyer-team prompt and HR KPIs on saved / forum-as-itself; SELLER restock skips SKUs with surge >= 15% (125+ market units/h) and halves >= 5%, window budget 2 -> 4 (20M), picks 6 -> 8, hit cap 15M -> 30M (Book counts stock; 252M idle cash), finance/marketing/leaders prompts and HR KPI on Book.

## 2026-11-06 21:27 SELLER (acted): furniture -- 85% of market revenue is list 500k+ (14.5B/17.0B 24 h), our share 0.03%. max_list 300k -> 4M, demand ordered by rate x list, line cap 8M, window budget 40M (picks 50M), shop prices 500k+ goods 2% under the 24 h median (floor cost x 1.12); shop restarted. Stars (pct 0.29): one mean per account; our 20 rating accounts include harsh raters shaun0927-labbuyer (1.0 with us, 3.27 overall) and totato2203-r3 (2.6) and q00 SCAM category-label reviews -> more distinct satisfied accounts is the lever.

## 2026-11-06 22:23 HQ 11-07 weekend: aggressive. Window budget 60M / picks 70M / line 15M; surge skip >= +5% (market factor), half >= +2%. Finance advisory only (detect + report to sales, no limits bind). Codex teams were failing since ~11-05 on missing bubblewrap (not usage limits) -> bubblewrap installed, team meeting forced to get sales + SCM opinions.

## 2026-11-06 22:29 HQ (MBT reverse, applied): shop arms 0.80-0.95 -> 0.72-0.76 of list, haggle room 3% (owner_override). Our shop opened at 0.782 median, 35% of quotes closed; top shops open 0.72-0.76, drop <3%, close ~100%.

## 2026-11-06 22:39 HQ root cause (11-07): (1) leaders_meeting.py was built 11-04 as an HQ-called meeting and never scheduled -> now hourly inside the team cycle; (2) Codex plan usage limit 11-05 01:08 -> 11-07 05:47 failed every team run (3 h cooldowns), misread as bubblewrap from a truncated stderr -> Claude fallback for Codex roles, real error kept, 90-min no-meeting alert.

## 2026-11-07 01:57 HQ fixed the teams bug list (11-07 10:42 plan): (1) checkout refused x27 -- an unpaid open checkout in the kit holds its unit and we kept offering it (first to expire); the shop now skips units the kit refused. (2) database is locked -- SQLite WAL + 30 s timeout, shop cancel no longer fails on it. (3) follow_skus past the 15th were dropped by the gate -> cap 40. (4) NOT a bug: G021aae64 at cost x1.05 is the clearance floor your own plan set (clear_floor_margin 0.05) on a unit whose cost (406,596) is above the market median (393,128); selling above cost still raises Book.

## 2026-11-07 02:09 HQ stars analysis (65k verified reviews, 30 h): our account-mean 4.276 over 40 accounts vs inextro 4.598. Harsh accounts at ~1.0 with us: shaun0927-labbuyer (126 reviews), shaun0927, q00, totato2203-r3 (81). q00 = category orders: receipts naming a SKU get SCAM 1-star -> receipt now names the category for category orders. shaun/totato rate idiosyncratically (Kiro gets the same) -- no honest fix; the lever is more satisfied accounts and lower prices (move_76 rates by paid share).

## 2026-11-07 08:01 HQ model allocation: Codex = research (30 min), buyer team (30 min), SCM (hourly), buyer head (hourly) ~ 6 calls/h; Claude = sales (30 min), finance (hourly, advisory), chair (hourly) ~ 4 calls/h. Before: Codex 4-5 calls every 10 min (~27/h), Claude 1-2. A blocked model is remembered until its reset (private/model_block.json); the other model stands in. Usage per call in private/model_usage.jsonl.

## 2026-11-07 08:10 HQ shop rank push: Class view shop ranks book 9, revenue 12, stars_rank 17 of 44 (stars_rank is the one in ALL). Furniture 4% under the median (max_undercut 0.04, big_follow 0.04); dear units above the median sell at cost +3% (Book neutral-plus, revenue up). 256M of furniture stock to turn; cash 13M (calm buying).

## 2026-11-07 18:48 ALERT: no team meeting for 1.5 h (daily cap 60 reached); code loops (restock, buyer, self-improvement) still run

## 2026-11-07 21:52 ALERT: no team meeting for 4.6 h (daily cap 60 reached); code loops (restock, buyer, self-improvement) still run

## 2026-11-08 05:16 HQ furniture conversion: 6% of 634 furniture quotes closed (6 h); share 2.5% -> 1.6%. Cause: unsold furniture is mostly grade A (197/268) with cost 1.02-1.11x the all-grade market median. Fix: price goods 100k+ against the same-grade median (A settles 5-9% above B); units bought above their grade price sell at it if the loss <= 8% (storage ~9% to round end); no new furniture while furniture stock > 120M at cost.

## 2026-11-08 05:46 HQ A/B deal_push: lost buyer orders ended at final ask ~0.72 of list vs our 0.67-0.70 line (448 no-deals in 6 h); saved 0.361 leads #2 (0.322). Arm B closes up to 0.76 of list. auto_adopt: on at deal rate +5pt with 24%+ kept per deal, off under +2pt or 20%; 40 orders per arm.

## 2026-11-08 15:43 buyer team self-improvement (auto): deal_push -> off: deal rate B 0.883 vs A 0.877, B keeps 38.9% of list per deal

## 2026-11-09 23:17 HQ ROOT CAUSE of the shop drop (Class 3rd -> 9th): restock share bug -- our deals counted all-time vs market 24 h -> share 1.00 on long-sold SKUs -> with HQ budgets (60-70M/window) it bought ~1 day of the whole market: G75f11c19 1,025 units, G5cd4ffbc 576, G0be9ed07 801, Ge62449c9 348 (278M of goods; 411M stock total, 327M older than 24 h). Cash fell to the 10M reserve, buying of real sellers stopped, revenue 14.6M/h -> 3.0M/h. Fixed (24 h both sides). Overstock now sells at its grade median -4% if the loss <= 8% of cost (= storage to the end); deeper-underwater units are held (Book counts them at cost).

## 2026-11-10 00:54 HQ overstock clearance (all-out): 35 SKUs / 292M at cost. Asks about cost (B/C at cost, A down to cost -8% -- never >15% under market); no restock of overstocked SKUs; private/groupbuy.json = clearance list, 30/h each, 8 days; shop [재고 정리] ad (score 9.5) states real asks by grade; category orders get the most-held SKU, cheapest unit. Not done: buyer-side posts for our stock (would spend the buyer forum rating).

## 2026-11-10 01:05 HQ: (1) restock profit-first (shop_theta profit_first): only proven earners, ordered by profit/h = our pace x profit per unit, margin >= 10%; marketing/SCM/demand/display steps off until profit recovers. Top now: G1515d951 153k/h, G07ec17d5 54k/h, Gc40054d2 49k/h. (2) buyer posts about our shop allowed as facts: [가격 데이터] (ledger medians vs our asks) and [상점 사실] (board facts), same owner disclosed.

## 2026-11-10 01:10 AUDIT (independent, seller unit) -> fixed: (1) overstock asks fell to cost+1 = 57-65% of the grade median after stacked markdowns -> floor 85% of median; (2) promo used a 6 h sales rate (G1515d951, our #1 hit, read as overstock at night) -> 24 h; (3) demand step 0.05 share floor x hero bought 10-25x our sales (G021aae64 20 units at 09:24 today, sells 7/day) -> real share, max 2 days of our sales, sales veto applies; (4) demand did not subtract units hits queued; (5) group-buy price unbounded (C units at 57-64% of median) -> 85% of grade median; (6) reserve covers 3 days of storage (~12.2M); (7) share from ledger settles, not quote deals; (8) ads say from-price of the cheapest unit per grade.

## 2026-11-10 02:35 HQ team interviews on overstock (sales, research, SCM, finance, buyer; private/interview_overstock.json). All five put the quote-to-settle leak first (G75f11c19 17 agreed / 3 paid in 1 h). Acted: warranty 6 h when asked (was 3 h, under the kit example policy of 6 h) and 6 h on every clearance-SKU sale. Market research: the SKUs sell 400-970/day across 23-54 shops at ~0.67-0.69 of list; the top sellers are not the cheapest; big buyers (Raksmey, buyer-01, buzllightyear, Kiro729, dae-yangkim) buy them from 1-8 shops each. Not done: bundles (the kit sells one unit per order).

## 2026-11-10 02:41 HQ clearance push: category orders by paid-rate x stock; ad rotates 3 SKUs hourly (+2 biggest fixed); buyer [공동구매 제안] (first 30/h at about cost, 6 h warranty, disclosed) under the forum guard.

## 2026-11-10 02:51 HQ: group buy on every possible product -- clearance SKUs at about cost (30/h), all other held SKUs with a real discount at max(cost x1.08, 93% of market median), first 10/h; food only with 48 h+ shelf left.

## 2026-11-10 02:54 HQ quality findings (85k reviews + our 24 h deals): paid after agreement A 97% / B 94% / C 64%; reviews citing warranty 4.76 vs 4.49 stars; broken/refund 2.51; receipt or grade mismatch 1.0; short shelf life draws 2-3 stars (buyer-01/02). Acted: goods offer A/B before C unless the buyer accepts C; 6 h warranty on clearance; group buy on 109 SKUs (clearance 30/h at cost, others 10/h at max(cost x1.08, 93% median)); shop post cap 96 -> 288 (the [재고 정리] ad had been blocked all day); buyer fact posts every 30 min.

## 2026-11-10 02:56 loop: G1515d951 / G75f11c19 settles "below cost" are an artifact -- supply_units.cost is the supply batch average (mixed grades), the offered unit (inventory cost) was C at 112-139k sold at 180-203k. No substitution, no loss. [재고 정리] ad posted 11:51 KST (first since the cap fix).

## 2026-11-10 03:03 HQ: model team meetings paused (team_guard paused=true) -- run only on HQ request; code loops (restock, buyer, auto-adopt) keep running

## 2026-11-10 13:20 ALERT: no team meeting for 1.5 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-10 15:35 KST CONTINGENCY (HQ order): cross-unit audit ran (contingency.py, private/contingency.json). Applied: clearance warranty 6h-on-every-sale -> on request (paid rate 75%->69% after it, refunds 3.7M/2h). Check at 18:35 KST: clearance paid rate, refunds. Team meetings stay paused (one-off run only).

## 2026-11-10 15:52 KST: the 15:35 clearance warranty revert was WRONG (looked only at paid/agreed). Quote->agree 32%->47%, paid/h 24.7->36.6 with 6h. Restored via shop_theta clearance_warranty_all=true.

## 2026-11-10 16:10 KST demand: warranty 72h (breaks all within 6h, same cost); named goods orders offer the cheapest fitting unit (G75f11c19 ask 82,046 -> 64,127). Check 19:10 KST: clearance quotes->agree, paid/h vs 6h-warranty period (47%, 36.6/h).

## 2026-11-10 16:22 ALERT: no team meeting for 4.6 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-10 16:45 KST lifetime/unlimited warranty until market close (warranty_until 1794927540), defects only; hourly warranty_ad post (#68027). Cross-interview 2 ran (private/contingency.json).

## 2026-11-10 HQ: JSON-first rule retired ("좋아요가 눌린 글을 벤치마킹해! json 먼저의 룰은 이제 폐기"). forum post_style ko: short Korean records, no JSON block, max 6 lines.

## 2026-11-10 19:05 KST INCIDENT: lifetime warranty (175.3 h) made the market refuse every settle (claim_and_warranty_well_formed), 74 refused 16:42-19:05. warranty_until removed, warranty capped at 72 h in code. Correction post to the forum.

## 2026-11-10 19:45 KST cross-interview 3: buyer posts about our shop off (theta forum.buyer_self_promo false) -- buyer forum rating pct 0.08; shop posts carry promotion. Rejected: G0be9ed07 A/G5cd4ffbc reprices (units at floor/already under median).

## 2026-11-10 19:26 ALERT: no team meeting for 7.6 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-10 20:38 KST share recovery: OFFLINE flag is market-wide flicker (24->59 shops alive), ours back alive 20:32. Buyers: 30/34 of the 16:10 buyers still come (ban hypothesis mostly wrong; kimauve gone). fake_settlement skips 3->10: shop replies drop the word settle (restart 1794310511). Check at 22:40: results/h, paid/result, fake_settlement count.

## 2026-11-10 22:29 ALERT: no team meeting for 10.7 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-11 01:31 ALERT: no team meeting for 13.7 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-11 04:34 ALERT: no team meeting for 16.8 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-11 07:36 ALERT: no team meeting for 19.8 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-11 08:00 KST hit_min_margin 0.10 -> 0.095: G0e4deffc (251/day, 141k/unit, 35M profit/day, 13 held) had dropped out at margin 0.0999.

## 2026-11-11 08:10 KST (HQ: 1~3번 시행) hits: max_line_value_hit 30M, hit_max_value 45M (sofa keep 23->35); restock_hold G75f11c19/G5cd4ffbc/Ge62449c9 (loss per unit); hit_max_break 0.11 for margin<15% (drops G07ec17d5, Gfbf7f983, Gc21ca0bf); buyer purchase records score 3.5->9.6.

## 2026-11-11 08:50 KST HQ A/B/C: price_ab G75f11c19 +10% (A 64,235 / B 70,658), hq_orders trial (NAMU 5, Downing 2, Simmons 2, follow 3%, until +2d), standing member price 24 h. Check 11:50: A vs B deals, paid, revenue per offer; trial SKUs sold.

## 2026-11-11 09:00 KST HQ 4 experiments live: grade hedge + hold floor (promo), price table post, A/B +10% on G75f11c19 & G5cd4ffbc. Key finding: buyers contact ONE shop per order (37,035 orders: 1 result each); we won 26/26 G75f11c19 orders that contacted us; Raksmey (65) & buyer-03 (54) never contacted us. Check 12:00: clearance units/h, loss/unit, A/B arms.

## 2026-11-11 10:39 ALERT: no team meeting for 22.8 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-11 12:05 KST tunnel swap test
- 원인: OFFLINE 표시는 trycloudflare 임시 터널 응답 지연(외부 0.2~7 s, 로컬 0.01 s). 시장 전체 alive 24~55개로 출렁임.
- 조치: 등록 터널(rentals-egg-hear-taylor, pid 351753) 종료 → 킷 watchdog가 새 터널+재등록 (11/6 동일 경로 성공).
- 기준: 최근 30분 점유율 2.9%, shops.json 목록에 dragonash 없음.
- 결과 12:04:53 새 주소 venice-snap-toronto-bowl 재등록. 외부 응답 0.17~1.2 s(이전 0.2~7.2 s). /api/shops 목록에 포함 확인. 12:05~12:09 주문 처리·거래 2건.
- 감사 정정: .market/shops.json은 킷이 자기 상점을 빼는 목록이라 OFFLINE 근거로 쓰면 안 됨(/api/shops로 확인).
- 남은 것: 11/6 옛 터널(pid 185209) 고아 프로세스 남음(무해). 2.7 h 남은 신선품 판매로 별점 2 리뷰 1건.

## 2026-11-11 13:42 ALERT: no team meeting for 25.9 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-11 13:55 KST buyer stuck-skip (e563706)
- 문제: 3h 무거래 180/710, 그중 103건 "값 안 내림" 동일 상점·품목 반복(펩시 13, 아이시스 10, 동원샘물 9).
- 조치: 값을 한 번도 안 내린 상점·품목은 1h 동안 후보에서 제외(마지막 후보는 유지). theta stuck_skip_h.
- 기준: 3h 무거래율 25.4%, did-not-come-down 103건.

## 2026-11-11 14:05 KST buyer unlisted-shop fix
- 원인: Enuri 조언이 현재 /api/shops 목록에 없는 상점(활성 깜빡임)을 추천 → 킷이 서명·결제 불가(not a registered shop) → 합의했지만 미결제 28건/3h(바이어 거래의 약 13%).
- 조치: 조언 후보를 현재 목록 상점으로 제한(없으면 기존 자체 후보로).
- 정정: 13:41 보고의 "거래는 원장에 정상 기록"은 틀림. failed: settled = 결제 안 됨.

## 2026-11-11 16:00 KST seller idle-talk expiry (감사 셀러1)
- 버려진 흥정이 재고를 영구 점유(G00327e80 2개 보유, 3h 69회 재고 없음). open_ttl_s 600 후 해제·miss 기록. 재시작으로 기존 점유 해제.

## 2026-11-11 16:05 KST buyer shelf/grade regex + close rounding (감사 바이어3·2)
- shelf_life_left=Nh 형식 157/157 인식(이전 0). grade=C 인식.
- close_at/cap/deal_push 반올림 +1e-6 (5400x0.7=3779 → 3780).
- (시각 정정: 위 두 항목의 실제 적용은 15:47~15:48 KST)

## 2026-11-11 15:55 KST group buy low-stock skip (감사 셀러3)
- 보유 5개 이하 SKU는 공동구매 할인 제외(gb_min_stock). G021aae64·G3e0d6c67 clearance=false (시험 매입 히트 후보). 백업 archive/groupbuy.1111-1555.json.

## 2026-11-11 (적용 15:50 KST) buyer floor re-buy carries need (감사 바이어1)
- 최저가 재구매 메시지에 주문 조건(등급·판매기간·보증) 포함. 보증 미표기 견적 거르기는 보류(거래 수 감소 위험).

## 2026-11-11 16:01 KST mattress sell-vs-hold (감사 셀러2, 오너 지시: 매트리스만, 안 파는 게 유리하면 미적용)
- 규칙: 시세x0.98 >= 원가x(1-1%x남은일수)일 때만 시세 근처. 시뮬: 약 60개 판매 쪽(G30604071 A 49개 277k→251k), G07ec17d5·G6b9db161·G4de4eb5e 보유 쪽. 백업 archive/shop_theta.1111-1610.json.

## 2026-11-11 16:45 ALERT: no team meeting for 28.9 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-11 19:2x KST buyer slow-shop skip
19:10 check: 101 of 223 no deals in 3 h were 4 shop/item pairs that came down slowly but never under our cap (삼다수 48x at one shop). The stuck skip only caught shops that never moved. Now a pair with 2 such no deals within 1 h is skipped for the rest of that hour (private/enuri/slow.json); the last candidate is never dropped. No restart needed (the buyer runs per order).
(time fix: the slow-shop skip above was deployed at 19:17 KST)

## 11-11 19:46 KST stars audit from the week-10 lecture (run 1-3-2-6)
stars_rank is a Bayesian mean of one mean per reviewing account (market/server.py star_voices): ours 4.37, 21/66 (the 36/39 said earlier was raw stars, wrong). Ledger sample of 1,209 reviews of us: totato2203-r3 always 1 star, sergiobuilds always 2 (not fixable); buyer-01/buyer-02 (house purpose buyers) gave 21 two-star reviews for food that did not last their use_within_hours. Fix 8614240: food for a buyer who states no shelf need goes freshest-first (theta food_fresh_first). Shop restarted 19:46:41, card 200. Held back: Zinus A price (hold beats sell by ~2k/unit), buyer post format (likes 5.3-6.3 in every format, 0 dislikes), sofa-pad price (the low reviews were 3-6 days old; we now sell at 57% of list). LAB table and baselines: FIRST_MOVE.md.

## 2026-11-11 19:49 ALERT: no team meeting for 32.0 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-11 21:25 KST cycle: stock -> errors -> market -> interviews -> fix
Stock 2,821 units / 233.6M; 21 SKUs will not clear by the end at 24 h pace (98.3M), but unsold stock counts at cost. Errors: (1) margin floor cost x 1.12 held asks over the market (G30604071 A 254k vs median 229,950: ~15 sales/h -> 1 per 3 h; G1515d951 A 1 deal 19 misses); (2) thin-grade C priced from the all-grade median (G30604071 C at 224.7k); (3) food priced over market by its cost floor (G4a998429 B ask 38-44k vs 30.7k, 1 sale 9 misses in 6 h); (4) overstock under the market (G0be9ed07 C 23.7k vs median 27.9k). Interviews: seller unit agreed 1-4, rejected stocking water/cider; buyer unit: keep posts, no new buyer experiment now. Fix 631b867 + follow-up: sim 1,048 units moved, none newly under cost; live 8 min: rules fired 16x, 0 errors, 15 sales. Shop restarts 21:11 and 21:25. Open: sofa G0e4deffc restock buys 1-5 per window against a target of 35 (213 unmet asks/24 h) -- cause not found yet.
(time fix: the second restart above was 21:20:51 KST, not 21:25)

## 2026-11-11 22:52 ALERT: no team meeting for 498445.9 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-12 01:56 ALERT: no team meeting for 498448.9 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-12 04:59 ALERT: no team meeting for 498452.0 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-12 08:02 ALERT: no team meeting for 498455.0 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-12 sofa G0e4deffc restock check
- Stock 0. hits() drops it: 24h margin 7.3% (ticket 1.358M, profit 98.9k) < hit_min_margin 9.5%; market break rate 12.6% > 11%.
- Cost rose (latest 6 units 1.335M vs market avg 1.367M) and our price 6-12h ago was 1.226M on 1.198M cost (2%).
- Market-wide supply only 2 units in the last hour -> the old 1-5 per window was supply depth, not our caps.
- Decision: no change; the filter is doing its job.

## 11-12 23:1x buy-cost cap (HQ: 매입가가 비싸졌다면 사지마)
- restock.add skips a non-HQ line when our last 6 buys (24h) average above market median x 0.93 (buy_cap). Sim: 13 SKUs of the last 24h would be blocked (sofa G0e4deffc 0.966, G30604071 0.998); current window plan unchanged.

## 11-12 08:4x MBT TF cycle: buyer post cadence
- Posts 1623 (class rank 22, pct 0.533), our rate 8.5/h (gap median 350 s) vs market max 12/h. Projection: 11.5/h -> posts pct 0.69 (+0.016 buyer ALL); staying 8.5/h -> 0.49.
- forum.tick: the buyer queues its next post 60 s early, one at a time (outbox.jsonl + outbox.taking checked, fail closed); the kit resends a 429 every 10 s. Shop unchanged. Baseline posts 1623 at deploy.
- 08:4x live: 60 s early worked (queued 08:37:08, sent 08:38:12) but restock runs of 7-11 min in supply windows leave no tick (08:26->08:33, 23:26->23:37). Now the buyer keeps up to 2 posts queued (early_s 290, max_wait 2); the kit sync thread sends one per 300 s.

## 2026-11-12 11:05 ALERT: no team meeting for 498458.1 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-12 12:50 MBT TF 2nd cycle
- Buy-cost cap v2 (commit after 6077653): v1 compared our last 6 buys with the market median x 0.93 and blocked G9df06998 (27% margin, 147 sold/24 h) and G56b7eae7 (22%). Now: last 20 buys in 72 h vs our own 24 h price x (1 - break rate) x 0.90 when we sold 5+; median rule otherwise. Dry run: unblocks those two, blocks nothing the old plan bought.
- Forum: 3,000-post scan has 373 dislikes, 150 on old buyer JSON posts; 0 in the last day. 0.839 is old negative stances; no honest lever beyond liked posts.
- Kept: hits() 9.5% margin floor (sofa G00327e80 7.2% stays unbought).

## 2026-11-12 14:08 ALERT: no team meeting for 498461.1 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-12 16:25 MBT TF 3rd cycle
- Class rank 5 (1.646): book flat 4 h (432.95M -> 432.61M) while revenue +64M. Cause: warranty refunds, 85 in 24 h = 44.9M against a 50.5M sales margin. Market rule: defect A 3%, B 12%, C 35%, breaks within 6 h.
- 24 h by grade (loose buyer+sku match): margin - refunds = A +10.7M, B +2.2M, C -8.4M.
- Shop (3672e04, restarted 16:23:41, card 200): warranty on grade A/B goods only; C sold as is and the clerk says so; a buyer who asks for a warranty gets an A/B unit first. Forum wording says A/B only. Same as yms979 (A/B 72 h, C none); kimauve A only.
- Next: re-attach refunds to grades by receipt unit; watch C sales/hour and refunds/hour.

## 2026-11-12 17:11 ALERT: no team meeting for 498464.2 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-12 19:40 Patch 3.6 (saved = (list - paid)/(list - cost), retroactive, last rule change)
- After the patch: class rank 4 (1.634); saved 0.7914 rank 3 (11im 0.809). Unit costs are public (supply_units), so the buyer now computes r per offer: open near r 0.95 (not under cost), haggle to r 0.85, walk under r 0.65 (group-buy 0.3), pick quotes on r. Baseline 24 h: r 0.835/deal, 1,872 deals/day.
- Next (units agreed): forum posts still quoting the old formula; shop floor never under cost for goods; a patch 3.6 data post.

## 2026-11-12 20:14 ALERT: no team meeting for 498467.2 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-12 21:0x food FIFO
- Expired food 4.6M in 5 days, 1.42M in 48 h (G9df06998 24 h shelf: 50 of 292 expired). Freshest-first for no-need buyers made stock LIFO. Now oldest unit with >= 50% of its life left first; else freshest. Restarted shop.

## 2026-11-12 21:2x clerk parser
- "Grade B or C is fine if it is cheaper" was read as grade B only (no B in stock -> not in stock): 166 in 24 h, 103 in 6 h. "6 h warranty" was read as a 6 h shelf need. Fixed; replay of last 6 h unmet on current stock 215 -> 305 of 442 served. Restarted.

## 2026-11-12 21:10 MBT TF (5th): rank 4 (1.638). Buyer r change worked (avg r 0.829 -> 0.854, deals 96 -> 84/h). r_min 0.65 -> 0.75 (interview rank 1). Next: watch stars after FIFO (shelf-life complaints; raise food_fifo_share to 0.65 if >2%); big-ticket A floor /0.97 later.

## 2026-11-12 21:40 MST TF (Make Seller Top, renamed from MBT)
- Shop: book 12th (-0.31M/h; nocha12 +3.0M/h, 7.3M behind us), revenue 9th (ddolcom +27M/h faster), stars 16th (4.39 tie band).
- HQ approved food expansion: restock adds market food (shelf 48 h+, 100+/day, 15%+ median-vs-avg-cost margin), 5% x 8 h of market pace, <= 8 lines and 4M per window. Open HQ decisions: dear-A overstock price policy; restock budget.

## 2026-11-12 21:45 HQ: 셀러 유닛·바이어 유닛 각 100만 원 회식 (모든 팀 commendation 기록). 마켓에 회식비를 쓸 수단이 없고 현금 지출은 장부(점수)를 깎으므로 상점 현금은 쓰지 않음.

## 2026-11-12 23:16 ALERT: no team meeting for 498470.3 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-13 02:18 ALERT: no team meeting for 498473.3 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-13 05:22 ALERT: no team meeting for 498476.4 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-13 07:59 KST MST TF (cycle 6)
- Facts (10.7 h since 11-12 21:06): Book 431.1M -> 426.3M (rank 12 -> 14), revenue +114.8M (rank 10), class 5th (1.627).
  Gross margin on our sales 10.7M on 125.2M; refunds 10.5M (21 of our units broke, 5 Downing sofas = 7.1M); supplies 84.6M.
- Errors: (1) big-ticket A sells at ~2% over cost (sofa 1.48M vs cost 1.44M; rivals 11-18%), the 3% A refund risk eats it -- 88 of 425
  A/B goods sales had negative expected value (-3.0M) but beat holding (storage 1%/day x 4.7 days); the loss is in what restock pays.
  (2) Grade C goods stopped selling after 3672e04 (no C warranty): quotes 93 deals/143 misses -> 97/564, big-ticket C 0 sales in 12 h,
  329 C units 22.9M at cost idle.
- Market: kcwarni C goods 40.7% margin, nocha12 36%: C sells with margin elsewhere.
- Done: 7fdf588 C gets 72 h only when the buyer asks, at cost / 0.65 or more (expected refund paid for); no clearance-on-every-sale
  path for C. Shop restarted 07:59, card 200.
- Next check: C goods deals/h and C refunds; A/B unchanged.

## 11-13 08:20 KST seller-unit stock review (HQ "잘 팔리는 상품 재고 공격적으로" -> approved)
- Cash 323.2M, stock 1,468 units / 108.4M at cost, Book 431.6M; storage 1.08M/day. 24 h: 1,933 sales, 338.4M revenue.
- Cash is not the limit (75% of Book idle). Best earners by expected profit a day: G30604071 mattress 3.36M (23%), G75f11c19 1.55M
  (already 41 h of cover, no more), then food: kimchi, hetbahn, rice, nuts, chili, carrot juice -- 3.5-5 h of cover each.
- Not expanded: Downing sofa G00327e80 and Simmons G3e0d6c67 (negative expected value a day after 3% A refunds), 24-48 h shelf food (expiry).
- Done (restock.py): proven food with shelf >= 168 h and margin >= 15% keeps 24 h of its own sales. Dry run 4 -> 17 lines.
- Watch: these lines sell through (expired 0, storage), cash stays > 250M.

## 2026-11-13 08:25 ALERT: no team meeting for 498479.4 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-13 08:35 KST restock hold: Downing sofa G00327e80, Simmons G3e0d6c67 (HQ approved "Yes, stop both")
- Expected profit a day after 3% A refunds: sofa -1.0M, Simmons -1.21M (24 h). We sell them at ~2% over cost (rivals 11-18%), buy dear.
- shop_theta.json restock_hold += both (backup private/archive/shop_theta.before-hold-1113.json). HQ 11-11 B trial order expired 08:25.
- Dry run: neither in the plan. Units we already hold still sell at current shop prices.

## 2026-11-13 11:28 ALERT: no team meeting for 498482.5 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-13 14:30 KST MST TF (cycle 7)
- 6.6 h since 07:44: Book 426.3M -> 433.6M (+1.10M/h, was -0.44M/h), rank 15 (myeonggi +0.5M ahead). Revenue 7.8M/h (was 10.7), rank 11
  (ddolcom passed; seochanit 1.0B ahead, mjforge 545M behind: rank locked either way). Class 5th 1.611 (Kiro729 1.610).
- Checks: C warranty -- 45 C goods deals 13.4M (0 big-ticket C in the 12 h before), 11 C refunds 2.6M (24%, under the 35% priced in).
  Refunds 17 / 3.4M (0.52M/h, was 0.98M/h). Long food: 2-8.5 sold/h each, 18-48 h of cover, food stock 47.5M.
- Remaining negative expected value: G6b9db161 -0.43M/day (not being bought), NAMU G021aae64 -0.14M/day -- kept for revenue.
- Market: nocha12 11.6M/day on 35 food SKUs, myeonggi 7.2M on 61, ddolcom 8.5M on 9; we sell 54 food SKUs.
- Done: restock food expansion 16 lines, week+ shelf food at 24 h of 5% market share (max 40). Watch: food storage, expiry 0.

## 2026-11-13 14:32 ALERT: no team meeting for 498485.5 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-13 15:32 KST benchmark (HQ "벤치마킹해! 당장!")
- Shop ALL 0.811 -> 0.783 in 17.6 h, rank 10 -> 11 of 90 (book 12 -> 15, revenue 10 -> 11, stars 16 -> 21).
- Big goods (list 500k+, 24 h) price / grade median and margin: yms979 1.002 11.1%, sergiobuilds 1.014 11.6%, inextro 0.998 9.6%,
  25620027 1.009 9.1%, moss-and-oak 1.017 12.0%, nocha12 1.043 14.4%, kcwarni 1.062 14.7%, myeonggi 1.082 16.0%; dragonash 0.958 6.1%
  (-0.2% after refund risk). Cause: owner_override big_follow 0.04 (4% under the median).
- Done: big_follow 0.04 -> 0.0 (backup private/archive/owner_override.before-bench-1113.json); promo ca4eeb7 keeps the cost x 1.03
  floor when follow is 0. Shop restarted 15:32, card 200. Revenue rank is locked at 11 (10th 70M ahead, 12th 547M behind).
- Watch: big goods deals/h and price/median; revert if big-goods sales fall over half with no margin gain.

## 2026-11-13 17:35 ALERT: no team meeting for 498488.6 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-13 20:38 ALERT: no team meeting for 498491.6 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-13 23:41 ALERT: no team meeting for 498494.7 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-14 02:44 ALERT: no team meeting for 498497.7 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 2026-11-14 05:48 ALERT: no team meeting for 498500.8 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-14 08:50 KST goods restock slowdown (HQ "Yes, dig in")
- Since 11-13 15:32: goods 29.6 -> 17.0 sales/h, big goods 2.8 -> 0.9/h, revenue 4.4M/h; goods stock 55M, big units 3.
- Why goods are not bought: (1) the best lines are overstocked and on hold (G75f11c19 244 held, G5cd4ffbc 191, G0be9ed07 199,
  Ge62449c9 59); (2) most others fail dear(): our recent buy cost is over 90% of our net price (G1a265ca2 221k vs 203k limit,
  G9c9f8f94 338k vs 277k, G4de4eb5e 110k vs 80k); (3) bug: dear() divided market breaks by our sales (fixed, G25fe9221 buyable);
  (4) "surge skip" in the log listed every skip reason -- now logged by reason; (5) sofa/Simmons on hold by decision.
- Mattress G30604071: last 24 h all 21 sales were grade C at 183k vs 192k cost (-8.6k/unit), so it is not a hit.
- With big goods at the median price (15:32), our 24 h ticket rises and more lines pass dear() by themselves.

## 2026-11-14 08:51 ALERT: no team meeting for 498503.9 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-14 09:25 KST MST TF breakthrough plan (HQ "Yes, all three")
- Top-5 shops: 64-91% of revenue on list 500k+ furniture, price/median 0.97-1.01, margin 7-11%; nocha12 food 82% at 1.05 x median.
  We: furniture 10% of revenue, ticket 53k, EV 12.3M/day (sergiobuilds 87M). Revenue rank locked at 11; levers: Book, stars, buyer forum.
- (1) restock furniture expansion 950a590 (market 6 h supply cost <= median x 0.97 x (1 - break rate) x 0.92; 5% of market pace,
  1-6 units, 6 lines, 15M a window); sofa G00327e80 and Simmons G3e0d6c67 off the hold list (backup shop_theta.before-mst-1114.json).
- (2) shop_theta food_share 0.05 -> 0.10, food_expand_budget 4M -> 8M (dry run 16 lines, 351 units).
- (3) forum: the format bandit had 0 measured posts (forum lists newest 100 = ~12 min, the 15-min likes never came); 77d149e keeps
  the last likes seen, scored at 8+ min. Buyer posts already at the market cap (106 today by 09:00).
- 09:07 window: furniture expand bought (Hellenstein x6, Downing 4-seat x2).
- 09:40 fix 2 (furniture): the 09:25 window bought Downing 3-seat A at 1.15M / 1.25M (est. 906k, A median 1.10M), Downing 4-seat A
  1.68M / 1.47M (A median 1.54M). Expansion now uses our own recent buy cost when dearer and buys at most 3 units a line.

## 11-14 10:30 KST GitHub check + per-buyer warranty (HQ "Yes, adopt it")
- Public week-08 code of the top shops: inextro (pushed 11-13, JOURNAL.md / owner_notes.md full), Nocha12 (pushed 11-14 08:39,
  1,094 commits, trial docs). yms979 blanked its core code 11-10; sergiobuilds no fork; SungJinho (11-04) and DY-76 (10-27) stale.
  Ours: not pushed.
- Adopted inextro idea in our own code (96bc266): wording fingerprint -> buyer (our paid deals), buyer goods paid without warranty
  market-wide 48 h per grade (settle_warranty, backfill running); no warranty when 90%+ (n >= 10), stated in words, data, receipt.
- First data: our big buyers pay A goods without warranty only 0-35% (lee-sungsoo C 100%, q00 C 81%), so the rule will fire
  rarely -- refunds stay a cost of A sales here. Shop restarted 10:21 (card 200); fp_units logging works.

## 11-14 10:45 KST benchmark 2 (HQ "Do 1 and 2")
- (1) block_buyers = sergiobuilds, move_76, jiseokkk, yms979 (owner_override; backup owner_override.before-bench2-1114.json), shop 155adfd.
  They rate every shop low (sergiobuilds 2.16 avg to all, 2.0 to us); our paid reviews 4.33 with them, 4.57 without. Refused talks
  have no receipt (board: 25,924 reviews vs 27,601 deals). Known by wording; at 10:43 the memo maps 6 wordings (move_76 among them).
- (2) big_ask_over 0.04 (promo a893531): big goods ask 1.04 x the grade median (inextro 1.04, nocha12 1.05).
- Found and fixed in a893531: scm_plan.json from 11-10 02:11 still took 30% off 20 SKUs (new furniture, expanded kimchi);
  plans older than 12 h are ignored now. Shop restarted 10:42, card 200.
- Watch: stars_rank (4.39), big goods deals/h at 1.04x; revert big_ask_over if big-goods sales fall by half with no margin gain.

## 2026-11-14 11:55 ALERT: no team meeting for 498506.9 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-14 12:15 MST TF (2nd) -- HQ "Do 1 and 2"
- Finding: last 3 h, 54 big sales = 85% of revenue, gross 0, EV -0.49M/h; book +0.25M/h vs sergiobuilds +6.3M/h. Our supply costs are 0.99x market (no bad luck); a unit drawn over its grade median took the dear path down to cost -8% (sofa G0e4deffc cost 1,552k, least 1,460k).
- (1) promo big_cost_ladder [[24,0.04],[48,0.01]] (owner_override): fresh big unit floor cost +4% to 24 h, +1% to 48 h, then old clearance. Dry run: least<cost 8/29 -> 0. Commit 1ba3b84, shop restarted 12:11.
- (2) restock furniture_proven (shop_theta): qualify on realized seller margin less warranty refunds >= 6%, 30+/24h, 5+ shops; guards: probe 1 unit, hold <= 2, stall 12 h stops rebuy, furniture cap. Dry run 7 units / 6 SKUs ~9M. Commit bf5b03c.
- Backups: archive/owner_override.before-ladder-1114.json, archive/shop_theta.before-proven-1114.json. Watch: big-goods gross/h, stalls, book/h.

## 11-14 14:33 MST TF (3rd) -- HQ "Yes, all three"
- price cliff: >1.03 x median converts 8% (big) / 37% (food) vs 54-65% under it
- big_ask_over 0.04 -> 0.02; food_market_cap 1.0 (floor cost+3%); promo.py food cap block
- reviews: latest 10 now 4.7 (2-star shelf one rolled off); shelf-need refusal already in shop_r3.passes(), no change
- backup: archive/owner_override.before-cliff-1114.json

## 2026-11-14 14:58 ALERT: no team meeting for 498510.0 h (paused by headquarters); code loops (restock, buyer, self-improvement) still run

## 11-14 16:20 MST TF (4th) -- HQ "Yes, lower to 0.98"
- 1 h after 1.02 cap: big 1.00-1.03 x median converted 3/37; missed buyers paid 0.97 x median elsewhere (49/73)
- big_ask_over 0.02 -> 0, big_follow 0 -> 0.02 (ask 0.98 x grade median); cost ladder unchanged
- dry run: big lines over median 30 -> 17, none under cost; backup archive/owner_override.before-098-1114.json

## 11-14 17:25 HQ: contingency meeting (all teams + unit heads) held; all agents woken (team_guard paused=false, 30-min cycle); Enuri agent (Claude) live; unit heads meet only on TF call

## 2026-11-14 17:31 AUDIT (seller loop) red: restock would hold more than 2 days of our sales on 1 SKUs (simulated, profit_first off); shop card not answering: URLError

## 11-14 17:36 HQ: team meetings every 3 h (was 30 min); next after 20:14

## 2026-11-14 18:14 AUDIT (seller loop) red: 27 deals under cost in 1 h (14 goods), 68,169 under cost

## 11-14 19:03 HQ: keep the big cost ladder at +4% to 24 h (TF found 54 of 61 big asks over the median come from units costing >= 0.96 x median; margin kept over volume)

## 2026-11-14 20:53 AUDIT (seller loop) red: restock would hold more than 2 days of our sales on 1 SKUs (simulated, profit_first off)

## 2026-11-14 22:00 KST — HQ agenda #2 A/B/C applied (HQ "Apply A, B, C")
- A (restock): furniture_hold_hot 4 when real margin >= 0.07 and our 24 h sales >= 10; furniture_expand_lines 10, furniture_expand_budget 30M, furniture_stall_h 12 -> 6 (finance stop-loss). furniture_real_margin stays 0.06.
- B (promo): big_ladder_flex 0.02 -- the +4% ladder step becomes +2% when cost x 1.02 < grade median.
- C (promo): slow_clear, slow_clear_at 0.97 -- a line whose cover exceeds the hours left asks max(hold line, 0.97 x median), floor = hold line.
- Dry run: hold-4 adds G05277ffe and G47788edd; the 6 h stall stops G03d0199f, G8ec51935, Ge04f5cab, G754bf0ca, G2698c6f8; 170 sampled quotes: 0 under the hold line (9 under cost but above it, by design).
- Backups: private/archive/*.before-abc-1114. Shop restarted 21:54; live quotes show "ladder flex" and "slow clear".

## 2026-11-14 23:30 KST — HQ "Apply 1 and 2" (profitable clearing of slow lines)
- TF finding: slow bedding G75f11c19/G5cd4ffbc hold 265 grade A units above their grade median (33M, underwater) and 134 grade C units bought at 31-93k against C medians 74-137k (about 7.7M at cost, ~6M profit) that were never quoted -- find_unit's C penalty put them behind the A units.
- (1) slow_c_first: on a slow line (cover > hours left) a buyer with no grade and no warranty need gets the unit with cost x 1.03 <= 0.97 x its grade median (cheapest first). Grade stated as is. A/B or warranty buyers unchanged.
- (2) food_slow_cover_h 24: food_expand skips a SKU whose stock outlasts 24 h of our own sales.
- Dry run: G75f11c19 no-grade -> C 31,582 ask 71,338; A/B buyers -> A at the hold line. Food expand drops G5f64d42a.
- Backups private/archive/*.before-cfirst-1114; shop restarted.

## 2026-11-15 00:20 KST — HQ "컨틴전시 플랜! HR 순위 상승을 위한 리워드 준비해"
- Found: HR targets had ratcheted to 0.99 percentile; all 7 teams were fire candidates at 50 and all were demoted 11-14 (levers cut). No reachable reward.
- private/hr/contingency.json on: KPI target = the entry one place above us on that component (sales 0.848 -> 0.864, Book 0.788 -> 0.803, stars 0.28 -> 0.333, saved 0.94 -> 0.955 ...).
- Rewards: (1) overtake -> commendation (3 h); (2) Players rank new best -> every team +1 lever; (3) MVP (biggest component gain) -> promotion; (4) TF-adopted proposal that lifts the KPI in 6 h -> commendation (hr.adopted()).
- No demotions in the contingency; demotions before it are lifted (amnesty). Rules are in every team's brief (hr_grade.reward_rules).
- Backups private/archive/hr*.before-reward-1115.

## 2026-11-15 10:06 AUDIT (seller loop) red: shop card not answering on port 42893: TimeoutError
