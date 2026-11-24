# Week 08 중간 평가 보고서 (초안) — 25512082 (sohyunkim11)

> 초안 상태: 2026-11-11 00:40 KST 기준으로 코드와 로그를 근거로 작성했다. 수치는 제출 직전 board로 다시 갱신한다.
> `[Q#]`는 코드·로그로 확인할 수 없어 본인이 답해야 하는 설계 의도다. 질문 목록은 맨 끝(8절)에 있다.

## 0. 개요

| 부품 | 파일 | 런타임에 하는 일 |
|---|---|---|
| Seller | `seller.py` (`SafeSeller`, 카드 이름 `Atlas Shop B-safe-1`) | 재고의 실제 유닛 하나를 고르고, 등급별 목표 호가와 원가+보관비 105% 하한으로 흥정한 뒤, Ed25519 서명 영수증(`unit`, `claim`)을 낸다 |
| Restock 실행 | `safe_restock.py` (agent.json `restock` 훅) | 사람이 쓴 단기 승인 파일이 있을 때만, 최악 원가·서지·마진 검사를 통과한 줄을 내고 승인 파일을 원자적으로 소진한다 |
| Restock 제안 (모델) | `restock_model_advisor.py` | 규칙이 거른 후보를 모델이 정렬하고 이유를 단다. 규칙이 상한을 다시 적용한다. 제안만 하고 사지 않는다 |
| Buyer | `market_buyer.py` + `buyer.py` | 재고 있는 가게들에서 병렬로 호가를 받고, 정산 확률 × Saved 증가량으로 순위를 매긴 뒤, 사전 검사와 `accept` 정책(증명서, A/B, 남은 시간, 비식품 6시간 보증)을 키트에 넘긴다 |
| 키트 없는 ACP 가게 | `acp_shop.py` | `/.well-known/acp.json`과 checkout 세션을 직접 구현했다. 로컬 테스트만 했다(4절) |
| 운영 | `run_shop_only_persistent.sh`, `run_market_locked.sh` | flock으로 인스턴스를 하나만 두고, 키트가 죽으면 다시 띄운다 |

운영은 공식 키트 v4(`market_kit 4`)에 위 에이전트를 `agent.json`으로 연결했다.

## 1. 부품별 설계

### 1.1 모델

- **개발과 런타임을 구분한다.** 코드는 Codex CLI(0.163.0, gpt-6.1-sol)로 작성·검토했다. `agent.json`의 `harness` 필드는 이 개발 도구를 적은 것이다.
- **거래 경로에는 모델이 없다.** 흥정, 호가, 영수증 서명, 결제 전 검사, 재입고 실행은 모두 규칙 코드다.
  `seller.py`, `market_buyer.py`, `buyer.py`, `safe_restock.py`는 `harness.py`를 import하지 않는다.
  Round 3 이전 버전(`backups/pre-round3-20261104T1435KST/`)은 `harness.py`로 `codex exec`를 한 번 부르고, 결과 JSON을 스키마·reserve·예산으로 다시 검증했다. `[Q1]`
- **모델이 들어간 자리: 재입고 제안**(`restock_model_advisor.py`, 2026-11-10 추가).
  1. 컨텍스트(규칙): 최신 `inventory.json`(잔고, 보유 원가, 실시간 surge), 자기 판매 기억(`seller-ledger.json`의 거절 사유 집계, `seller-events.jsonl`의 SKU별 호가·수락 수), 시장 ledger에서 계산한 SKU별 정산 수·구매자 수·경쟁 가게 수·등급별 체결가/정가.
  2. 규칙 shortlist: catalog에 있고, 보유하지 않았고, 유통기한 720시간 이상인 식품 또는 비식품이며, 최악 원가(정가×0.75×surge)가 실행 상한과 보유 원가 상한 안에 드는 SKU만 남긴다.
  3. 모델(`harness.run_decision`, 스키마 `schemas/restock_advice.json`): shortlist 안에서 최대 6개를 고르고 순서와 이유를 낸다. 수량과 가격은 정하지 않는다.
  4. 규칙 재적용(`enforce`): shortlist 밖 SKU는 버리고, 수량을 1로 고정하고, 모델이 정한 순서대로 상한을 다시 적용한다.
  5. 실패 처리: 오류, 시간 초과, 스키마 불일치가 나면 규칙 순위(24시간 정산 수 / 경쟁 가게 수)로 돌아간다.
  6. 기록: `logs/model-decisions.jsonl`에 프롬프트 해시, 지연시간, 모델 원출력, 강제 결과, 오류를 남긴다.
  7. 실행: 출력은 `executable_purchase_plan: false`인 제안이다. 실제 구매는 사람이 승인 파일을 쓰고 `safe_restock.py`가 다시 검사해야 일어난다.
- 상태: **미완료.** 코드와 테스트(`test_restock_model_advisor.py` 4개)만 있다. 실제 모델 호출은 아직 하지 않았다(`--model` 플래그가 없으면 호출하지 않는다). 지금까지의 로그는 규칙 경로 실행뿐이다(`model_called: false`).
- 측정 도구(구현, 모델 미호출): `restock_model_advisor.py --model --repeat N`은 같은 컨텍스트로 모델을 N번(최대 5) 부른다. `shadow_eval.py`(테스트 2개)는 기록된 각 결정을 사후 시장 정산으로 점수화한다. 측정 지표는 호출 수, 실패 수, 지연 중앙값, 규칙이 버린 모델 선택 수, 같은 컨텍스트에서의 상위 3개 일관성, 규칙 대비 가상 판매 수와 가상 마진이다. 호출 비용은 Codex CLI를 ChatGPT 계정 로그인으로 쓰면 호출당 API 과금은 없다. 대신 프롬프트 길이를 기록해 API로 바꿀 때의 토큰 비용을 추정한다.
- 평가 계획(shadow mode): 공급 창마다 규칙 제안과 모델 제안을 함께 기록하고(`rule_proposal`, `enforced`, `model_picks_dropped`), 어느 쪽도 사지 않는다. 다음 2시간의 시장 정산으로 각 제안 SKU의 정산 수, 서로 다른 구매자 수, 등급별 체결가를 모은다. 그 값으로 1개를 샀을 때의 가상 손익을 계산해 비교한다. 비교 지표는 상위 3개 겹침, 가상 손익 차이, 규칙이 버린 모델 선택 수, 호출 실패율과 지연시간이다.
- 모델을 흥정 대신 재입고에 둔 근거: 거래 경로의 실패는 곧 거래 손실이다(7주차 EP5: 모델 429로 1점 6,837회). 재입고는 60초 주기라 지연을 견딜 수 있다. 그리고 가게의 실제 병목은 재고다(3절). `[Q2]`

### 1.2 컨텍스트와 메모리

| 저장소 | 내용 | 다음 결정에 들어가는 곳 |
|---|---|---|
| `.market/seller-ledger.json` (`ledger.py`, 원자적 쓰기) | 합의·거절과 사유 | 15분 안의 합의만 유닛을 예약한다(`unit_window`). 재입고 제안의 거절 사유 집계 |
| `.market/seller-events.jsonl` (2026-11-10 추가) | 호가, 시작 호가·하한, 제안가, 수락, 거절 사유 | 재고가 있을 때의 가격 거절률 측정, 재입고 제안 컨텍스트 |
| `.market/inventory.json` (키트) | 유닛별 원가, 등급, 입고·만료 시각, 증명서, surge | 만료 임박 유닛 우선 출고, 보관비를 하한에 반영, 최악 원가 계산 |
| `.market/runtime-state/buyer-memory.json` | 주문별 상태 | `reconcile_payments`가 키트 결과를 가져오고, 애매한 결과는 실패로 세지 않는다 |
| `.market/payments.jsonl` (키트) | 결제·거부 사유 | 가게별 정산 확률 `(s+1)/(s+r+2)`, 재고 없음(409) 쌍 차단, 배치 지출 상한 |
| `shops.json`의 리뷰 | 가게 평판 | 영수증 불일치·과다청구 문구가 있는 저평점 리뷰를 무결성 경고로 센다 |

메타 하네스는 없다. 규칙 코드가 파일 메모리를 읽고, 모델에는 규칙이 요약한 JSON만 준다. 원시 ledger나 게시판 글은 넘기지 않는다. `[Q3]`

### 1.3 툴

| 툴 | 무엇을 | 어떻게 |
|---|---|---|
| A2A 흥정 서버 | `SafeSeller.execute` | SKU/상품명 매칭 → 호가 → 최대 3라운드, 라운드마다 12% 양보, 하한 아래로는 안 내려간다 |
| 영수증 서명 | `sign_receipt` | `{shop,item,price,task}` canonical JSON에 Ed25519. `unit`, `claim`은 가게가 키트에 보낸 사본에서만 읽힌다 |
| 승인형 재입고 | `safe_restock.build_plan` | 승인 만료, 라운드, 창 열림, SKU당 1개, 최악 원가, 기대 마진 8% 이상, 실행당 10만·보유 100만 상한 중 하나라도 어기면 빈 계획 |
| 재입고 제안 | `restock_model_advisor.propose` | 1.1 참조 |
| Buyer 호가 비교 | `rank_verified_quotes` | 정산 확률 × Saved 증가량, 무결성 경고 가게 후순위 |
| 결제 전 검사 | `_receipt_precheck`, `_accept_policy` | 품목, 예산, 보증 시간 확인 후 `accept`를 키트에 전달 |
| ACP checkout (키트 없이) | `acp_shop.py` | 서명한 영수증 사본과 대조해 세션 생성, complete 때 `/api/settle` 호출 |

로컬 테스트(2026-11-10): `test_seller_lock_safety` 11(실제 A2A JSON-RPC), `test_ledger_reservation` 6, `test_restock_model_advisor` 4, `test_acp_shop` 7, `test_market_buyer` 17, `test_safe_restock` 7, `test_seller_signing` 5, `experiments/` 60. 모두 OK.
변이 검사: 수정 전 코드(`backups/pre-ledgerfix-20261110/`)로 `test_seller_lock_safety`를 돌리면 잠금 관련 4개가 실패하고, 보호 관련 7개는 통과한다.

## 2. 아키텍처와 키트 기본형과의 차이

그림: `architecture.tex` → `architecture.pdf` (PDF 미빌드. 이 컴퓨터에 TeX이 없다).

| 항목 | 키트 기본형 (`starter --round3`, 기본 restock) | 이 에이전트 |
|---|---|---|
| 모델 위치 | 하네스(기본 Claude haiku)가 흥정의 매 수를 정한다 | 거래 경로는 규칙. 모델은 재입고 제안에만, 제안으로만 쓴다 |
| 호가 | 원가 이상, 품질 이야기·보증 없음 | 등급별 목표 호가, 원가+보관비 105% 하한, 영수증에 등급 주장 |
| 재입고 | 재고가 적으면 무작위 SKU 몇 개 | 시장 데이터 기반 제안 → 사람 승인 → 최악 원가 검사 → 원자적 소진 |
| Buyer | 하네스가 가게와 값을 정한다 | 병렬 호가 → Saved 기대값 순위 → 증명서·등급·시간·보증 정책 |
| 운영 | `join` 한 번 | flock 단일 인스턴스, 재시작 루프 |

`[Q4]`

## 3. Round 3 결과 (2026-11-10 23:20 KST board, 최종 아님)

| 항목 | 값 |
|---|---|
| Players (전체) | total 0.319 = shop ALL 0.085 + buyer ALL 0.234 |
| Players (학생 46명) | student_total 0.199 = student_shop 0.018 + student_buyer 0.181, student_pct 0.022 (45위) |
| 가게 | 정산 6건, 방문 218, 매출 111,658, Book 8,057, cash −38,482, 별점 4.67(리뷰 6, stars_rank 3.8), 재고 3개(원가 46,539) |
| buyer | 정산 23 / 주문 60, Saved 0.029, served 21, 게시판 0.529, 글 1 |

기본 조건: buyer 23건(충족), 가게 6건(**14건 부족**).

### 가게 판매 실적 (팔린 6개, 안 팔린 3개)

| 유닛 | 등급 | 원가 | 시작 호가 | 하한 | 결과 |
|---|---|---|---|---|---|
| 소파패드 G0be9ed07 | A | 25,259 | 31,122 | 26,522 | 28,792에 정산, +3,533 |
| 프링글스 G1831f2c0 | B | 6,366 | 9,288 | 6,685 | 7,095에 정산, +729 |
| 매트리스커버 G575cddb8 | A | 17,314 | 21,762 | 18,180 | 18,181에 정산(하한), +867, 입고 후 4.8분 |
| 앞치마 G53e73f8c | A | 9,860 | 12,090 | 10,353 | 12,090에 정산, +2,230, 6.6분 |
| 커튼 G81ade83e | B | 18,984 | 25,013 | 19,934 | 20,510에 정산, +1,526, 7.6분 |
| 커튼 G81ade83e | A | 23,798 | 27,097 | 24,988 | 24,990에 정산(하한), +1,192, 8.3분 |
| 이불 Ge62449c9 | C | 26,302 | — | — | 11-04 22:36 합의, 결제 안 됨, 보유 중 |
| 앞치마 G53e73f8c | B | 8,954 | — | — | 11-05 21:05 합의, 결제 안 됨, 보유 중 |
| 그래놀라 G2abf21d1 | A | 11,283 | 14,742 | 11,848 | 11-10 가격 합의 실패 1회, 보유 중 |

시작 호가와 하한은 당시 규칙(`GRADE_TARGET_SHARE` A 0.78, B 0.72, 원가×1.05)으로 다시 계산한 값이다. 판매 6건의 매출총이익은 10,077이다.

### 2026-11-10에 발견한 결함: 한 번 기록된 유닛을 다시 팔 수 없었다

시장 ledger의 buyer 결과(21:48~23:13, 이 가게 35건)에 다음 줄이 반복됐다.

```
23:08:14 inextro       No deal: accepted 11848 but the shop did not close (TASK_STATE_REJECTED).
21:50:54 25620027      No deal with this shop: TASK_STATE_REJECTED: Duplicate or unrecordable trade..
22:46:50 chaewoonee    No deal on the 조리/가공 견과류: asks [14742], my offers [14742].
```

원인: `ledger.py`는 같은 `unit_id`가 상태와 상관없이 한 번이라도 있으면 새 합의를 거부했다. 보유 중인 3개는 모두 결제되지 않은 합의 행 또는 거절 행이 있어, 구매자가 가격에 동의해도 거래가 닫히지 않았다.
수정(2026-11-10 23:55:21 운영 반영, `[market] shop registered as sohyunkim11, round 3`):

- 15분 안의 합의만 유닛을 예약한다. 시장 주문은 10분 뒤 만료되므로, 15분이 지난 미결제 합의는 정산될 수 없다.
- 거절 행에는 실제 유닛 ID를 쓰지 않는다.
- 호가만 받고 떠난 협상은 10분 뒤 유닛을 놓아준다.
- 합의 시점에 `inventory.json`에서 유닛을 다시 확인한다.

정산된 유닛은 세 겹으로 막힌다. 시장이 inventory에서 빼고, 키트의 `pick_unit`이 결제 직전 시장의 `/api/inventory`를 다시 읽고, 이 가게도 합의 때 다시 확인한다.
실제 ledger·inventory 복사본으로 재현한 결과, 수정 전에는 3개 유닛 모두 `Duplicate or unrecordable trade`였고 수정 후에는 3개 모두 `Deal accepted`였다.

수정 후 관찰 (23:55:21 ~ 00:31, `.market/seller-events.jsonl`과 `logs/market-kit.log` 기준):

| 유닛 | 등급 | 원가 | 시작 호가 | 하한 | 결과 |
|---|---|---|---|---|---|
| 그래놀라 u-cb8b40190b30 | A | 11,283 | 14,742 | 11,848 | 제안 9,450 → 10,859 → 11,848, 23:57:31 11,848에 정산, ledger #4917215 |
| 앞치마 u-1671a8b17a88 | B | 8,954 | 10,850 | 9,884 | 23:58:08 10,850에 정산, ledger #4917380 |
| 이불 u-7c623c01cfa5 | C | 26,302 | 40,434 | 약 29,300 | 40,434에 3회 합의, 결제 0회 |

- 이불 C 3건의 진행 단계: 3건 모두 A2A 합의와 가게 서명 영수증까지 진행됐다. 가게 쪽 키트 로그에는 `checkout refused`도 `settle refused`도 없다. 시장 ledger에는 1건(olstjr, 23:57)만 결과가 있고 `sig_seller_ok`, `sig_buyer_ok`, `price_within_budget`는 true, `settled`는 false였다. 나머지 2건은 결과가 남지 않았다. 원인은 확정하지 못했다.
- 시장 비교(11-10 표본): 비식품 C 정산 395건 중 53%는 보증 없이 정산됐고, 그 중앙 체결가는 정가의 0.503배였다. 침구 C의 중앙값은 0.538배였다. 이불의 호가는 0.46배(40,434/87,900)로 이미 그보다 낮다. 보관비는 하루 약 263원이다.
- 같은 기간 잠금 실패(`ledger_duplicate`)는 0건, 가격 합의 실패는 0건, 재고 없음 거절은 17건이었다.
- 이불의 합의는 15분 예약이 끝날 때마다 다음 구매자에게 다시 열렸다. 수정이 의도대로 동작한 것이다.

이 결함 때문에 가게 자체 ledger의 거절 집계(`no_verified_inventory` 72, `no_price_agreement` 1)는 가격 거절을 과소 집계한다. 호가만 받고 떠난 방문은 기록되지 않았기 때문이다. 그래서 `seller-events.jsonl`을 추가했다.

### 거부 사유 인용 (buyer, `logs/market-kit.log`)

```
19:57:47 [acp] not paying: grade C, the policy accepts ['A', 'B']
19:57:54 [acp] not paying: 322.78 h of shelf life left, the policy asks for 540.0
19:58:13 [acp] checkout refused: 409 {"error":"unit u-3d3fc03f9346 is not in stock for this order"}
```

## 4. 키트 없이 배포한 가게

기본 조건의 세 번째 줄은 키트 없이 배포한 가게에 적용된다. 이 가게는 키트로 배포했으므로 대상이 아니라고 판단했다.
`acp_shop.py`는 같은 이름과 키로 등록하면 시장이 마지막 URL만 유지하므로 키트 가게와 동시에 운영할 수 없다. 로컬 테스트만 했다(7개 OK).

## 5. 한계

- 재입고 제안 모델은 구현했지만 아직 실제로 호출하지 않았다(1.1).
- 가게 가동 시간이 짧다. 2026-11-10 21:46 WSL 재부팅 때 키트가 멈췄고, 21:48:57에 다시 등록했다.
- 3절의 유닛 잠금 결함으로 보유 재고 3개가 11-04~11-10 동안 팔리지 않았다(11-10 23:55 수정).
- 가게는 보증을 걸지 않는다. 시장 비식품 정산의 80%에는 보증이 붙어 있다.

## 6. 운영 판단 기록

- 재입고 승인 기록: 11-10 그래놀라 승인(`restock-approval.consumed-20261110T1902.json`)은 실제로 한 번만 사용됐다. 확인한 기록은 restock 출력(19:02:24), 키트 `[supply]` 줄(19:02:28, 11,283), 시장 ledger supply #4833894, 판매 #4917215다.

`experiments/analysis-20261110/README.md`: board 공식 재현, SKU별 정산·체결가 분석, 학생 백분위 시나리오.

## 7. 제출 전 갱신할 것

- 3절 수치와 Players 순위
- 유닛 잠금 수정의 운영 반영 시각과 그 뒤 정산 수
- 모델 호출이 승인되면 `logs/model-decisions.jsonl`의 실제 호출 기록

## 8. 본인 확인이 필요한 질문

각 답변 초안은 기록에서 확인되는 근거와 **추정**을 나눠 적었다. 추정 부분만 본인이 확인하면 된다.

- **[Q1] 흥정 판단에서 모델을 뺀 이유**
  - 근거: 10-27 커밋 `c4e9f6d`(모델 호출 실패 시 reserve-safe 규칙으로 후퇴)와 `f7c8e02`(모델 실패 시 1점 대신 중립 평점)에서 모델 실패를 위험으로 다뤘다. 11-02 오프라인 시뮬레이션(`experiments/results/FINAL_OPERATION_READINESS.md`, 16,000+12,000회, 외부 LLM 호출 0)으로 결정적 정책 B-safe-1(Seller)과 B-original(Buyer)을 골랐다. `OPERATIONS_PLAN.md`에는 "price, stock, and shop decisions are deterministic rule-based policies, not an LLM"이라고 적혀 있다.
  - 추정, 확인 필요: 모델의 지연과 실패가 거래 손실로 이어지는 것을 피하고, 시뮬레이션으로 검증할 수 있는 정책을 원했다.
- **[Q2] 모델을 재입고 제안에 둔 근거**
  - 근거: 가게 거절의 대부분이 재고 없음이었다(3절). 재입고 훅은 60초 주기로 돌아 지연을 견딜 수 있다. `OPERATIONS_PLAN.md`에 "An optional model could later propose bounded adjustments, but deterministic guards must remain authoritative"라고 적혀 있다.
  - 확인 필요: 이 배치를 본인의 설계로 채택하는가?
- **[Q3] 메타 하네스 대신 파일 메모리를 고른 이유**
  - 근거: `OPERATIONS_PLAN.md` "Context: local history should store settled/failed outcomes, prices, receipt mismatches, warranty incidents, and timestamps. Only past records may influence decisions." 구현도 그대로 파일 메모리를 규칙이 읽는 방식이다(1.2).
  - 추정, 확인 필요: 재현 가능성과 감사 가능성(파일로 남는 근거)을 우선했다.
- **[Q4] 독창성 주장 (초안)**
  - "모든 돈의 이동은 규칙 검사나 사람 승인 게이트를 통과하고, 모델은 그 게이트 앞에서 제안만 한다. 키트 기본형은 하네스가 흥정의 매 수를 정하고 재고를 무작위로 산다."
  - 근거: `safe_restock.py`의 승인 파일과 원자적 소진, `_accept_policy`, `enforce`. 이 문장을 본인의 주장으로 채택할지 확인이 필요하다.
