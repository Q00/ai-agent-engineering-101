# Week 08 중간 평가 보고서 — 25512082 (sohyunkim11)

> **상태: 제출 전 갱신본(2026-11-16 19:20 KST 작성).** 3절의 수치는 모두 **임시 결과**이고 행마다 측정 시각을 적었다.
> 최종 순위는 Round 3 종료 시각(11-17 23:59 KST) board의 Class 보기로 정해진다. 이 문서는 최종 순위를 예측하지 않는다.
> 이전 초안(11-11)은 `backups/pre-submit-20261116/REPORT.md`에 보존했다.

## 0. 개요

| 부품 | 파일 | 런타임에 하는 일 |
|---|---|---|
| Seller (A2A 흥정) | `seller.py` (`SafeSeller`) | 재고의 실제 유닛 하나를 고르고, 등급별 목표 호가와 원가+보관비 105% 하한으로 흥정한 뒤, Ed25519 서명 영수증(`unit`, `claim`)을 낸다 |
| 재입고 실행 | `safe_restock.py` (agent.json `restock` 훅) | 단기 승인 파일이 있을 때만, 최악 원가·surge·한도 검사를 통과한 줄을 내고 승인 파일을 원자적으로 소진한다 |
| Seller 운영 감독 | `operations/seller/ops15.py` (이전 판 ops9~ops14) | 공식 원장으로 SKU별 판매율·이익을 검증해 공급 창마다 승인 파일을 쓰고, 안전 조건을 어기면 신규 매입을 멈춘다 |
| 호가 실험 제어 | `operations/seller/price_exp.py` + `seller.py`의 `market_price_ask` | A·B등급 시작 호가를 시장 중앙가로 바꾸는 실험. 원복 조건을 스스로 감시한다 |
| Buyer | `operations/buyer-agent/market_buyer.py` + `buyer.py` | 병렬 호가 → 정산 확률 × Saved 증가량 순위 → 결제 직전 증명서·등급·남은 시간·보증 검사(`accept`) |
| Buyer 운영 감독 | `operations/buyer/buyer_ops7.py` (이전 판 buyer_ops, buyer_ops2~6) + `stop_guard7.sh` | buyer-only 키트를 100주문 단위로 돌리고, 공식 board로 구간 성과를 검증해 계속·중단을 정한다 |
| 키트 없는 ACP 가게 | `acp_shop.py` | `/.well-known/acp.json`과 checkout 세션을 직접 구현. 로컬 테스트만 했다(4절) |
| 프로세스 관리 | `run_market_locked.sh`, `run_shop_only_persistent.sh` | flock으로 인스턴스를 하나만 두고, 키트가 죽으면 다시 띄운다 |

공식 키트 `scripts/market_kit.py`는 **수정하지 않았다**(2026-11-16 19:17 KST, `git diff upstream/main -- scripts` 결과 차이 없음).
가게는 `submissions/25512082/week-08`에서 `--shop-only`로, buyer는 별도 폴더의 `--buyer-only` 키트로 띄웠다. 두 키트는 같은 이름(`sohyunkim11`)으로 등록된다.

## 1. 부품별 설계

### 1.1 모델

**Round 3 운영 중 런타임 모델 호출은 0회다.** 흥정, 호가, 영수증 서명, 결제 전 검사, 재입고, 운영 감독은 모두 규칙 코드다.

- 근거: `seller.py`, `market_buyer.py`, `buyer.py`, `safe_restock.py`, `operations/` 아래 감독 스크립트는 `harness.py`를 import하지 않고 모델 API도 부르지 않는다. `logs/model-decisions.jsonl`에는 1줄만 있으며 `model_called: false`(11-10 23:24 KST 규칙 경로 시험)다.
- 모델 자리로 설계한 것: `restock_model_advisor.py`. 규칙이 거른 후보 SKU를 모델이 정렬하고, 규칙이 상한을 다시 적용하며(`enforce`), 실패하면 규칙 순위로 돌아간다. 코드와 테스트(4개)만 있고 **Round 3에서 실제로 호출하지 않았다.** 대신 아래 운영 감독이 공식 원장 통계로 SKU를 고른다.
- 개발 도구로 쓴 모델: 11-11까지의 코드는 Codex CLI(0.163.0, gpt-6.1-sol)로 작성·검토했다(`agent.json`의 `harness` 필드). 11-13 이후의 운영 감독 스크립트, 원장 분석, 이 보고서 정리는 Claude Code(Claude Opus 5.5)로 작성했다. 모든 운영 범위(예산, SKU, 단가 상한, 중단 기준)는 사람이 승인했고, 스크립트는 그 범위 안에서만 동작한다.
- 모델을 거래 경로에서 뺀 근거: 7주차 운영에서 모델 오류(429)로 1점 처리가 6,837회 발생했다. 11-02 오프라인 시뮬레이션(`experiments/results/FINAL_OPERATION_READINESS.md`, 외부 LLM 호출 0)으로 결정적 정책을 골랐다. `experiments/OPERATIONS_PLAN.md`에는 "deterministic guards must remain authoritative"라고 적혀 있다.

### 1.2 컨텍스트와 메모리

메타 하네스는 쓰지 않았다. 컨텍스트는 **파일 메모리 + 공식 공개 데이터(원장, board)** 이고, 규칙 코드가 이를 읽어 다음 결정에 넣는다.

| 저장소 | 내용 | 다음 결정에 들어가는 곳 |
|---|---|---|
| `.market/seller-ledger.json` (`ledger.py`, 원자적 쓰기) | 합의·거절 | 15분 안의 합의만 유닛을 예약한다(`unit_window`). 거절 행은 유닛을 잠그지 않는다 |
| `.market/seller-events.jsonl` | 호가·제안·수락·거절 사유 | 가격 거절률 측정. 11-16 19:20 기준 quote 857, offer 982, accept 327, `no_verified_inventory` 974, `no_price_agreement` 164 |
| `.market/inventory.json` (키트) | 유닛별 원가·등급·만료·surge | 만료 임박 유닛 우선 출고, 보관비를 하한에 반영, 최악 원가 계산 |
| `.market/price-experiment.json` | SKU·등급별 최근 3시간 시장 중앙가 | `market_price_ask`가 A·B 시작 호가로 사용(유효 기간 안, 30분 안에 갱신된 경우만) |
| `ops*_state.json` / `ops*.log` (감독) | 매입 유닛, 정산, 결제 리뷰, 원장 커서, board 스냅샷 | 다음 공급 창의 매입 여부, 실현 이익, 안전 중단 판단 |
| 공식 원장 `/api/ledger` | 시장 전체 supply·settle·broken·result | SKU별 3시간 판매율(우측 검열 보정), 실현 이익 중앙값, 원가 미만 비율, 정산 수 |
| `.market/payments.jsonl`, `broken.jsonl` (buyer 키트) | 결제·거부·파손 | 가게별 정산 확률, 100주문 구간의 결제 수와 파손 수 |
| `buyer_ops*_state.json` / `.log` | 구간별 board 전후 값 | 구간 Saved 역산: Σ = saved₁(deals₁+100) − saved₀(deals₀+100) |

### 1.3 툴

| 툴 | 무엇을 | 어떻게 |
|---|---|---|
| A2A 흥정 서버 | `SafeSeller.execute` | SKU 매칭 → 호가 → 최대 3라운드, 라운드마다 12% 양보, 하한 아래로는 안 내려간다. 합의 순간 `inventory.json`에서 유닛을 다시 확인 |
| 영수증 서명 | `sign_receipt` | canonical JSON에 Ed25519. 등급 주장(`claim`)은 실제 유닛 등급. board 기준 claims 132건 모두 honest(11-16 19:19) |
| 승인형 재입고 | `safe_restock.build_plan` | 승인 만료, 라운드, 창 열림, SKU당 1개, 최악 원가(정가×0.75×surge), 실행·보유 상한 중 하나라도 어기면 빈 계획. `os.link`로 1회 소진 |
| 운영 감독(Seller) | `ops15.gate`, `sku_stats`, `stop_reasons`, `reconcile` | 공급 창마다 원장 기준 검증 → 승인 파일 작성 → 사용·미사용 기록을 `restock-approval.consumed-*.json` / `unused-*.json`으로 남긴다 |
| 호가 실험 | `price_exp.py`, `market_price_ask` | 5분마다 중앙가 파일 갱신. 원가×1.05 하한이 항상 우선. 원복 조건을 어기면 파일을 지우고 종료 |
| Buyer 호가 비교 | `rank_verified_quotes` | 정산 확률 × Saved 증가량, 무결성 경고 가게 후순위 |
| 결제 전 검사 | `_receipt_precheck`, `_accept_policy` | 품목, 예산, 보증 시간 확인 후 `accept`(cert, 등급, 남은 시간, 굿즈 보증 6시간)를 키트에 전달. 11-15부터 굿즈는 A등급만 |
| 운영 감독(Buyer) | `buyer_ops7.segment`, `block_ok`, `gate_ok` | 100주문마다 board로 정산 수·거래당 Saved·파손을 검증, 500주문 관문 |
| 종료 감시 | `stop_guard7.sh` | 종료 시각이나 주문 상한에 도달하면 감독과 키트 프로세스 그룹에 SIGTERM(이중 안전장치) |
| ACP checkout(키트 없이) | `acp_shop.py` | 서명한 영수증 사본과 대조해 세션 생성, complete 때 `/api/settle` 호출 |

테스트: 11-10에 로컬 단위 테스트 57개와 `experiments/` 60개가 모두 통과했다. 운영 감독마다 테스트 파일을 같이 두었다(`operations/seller/test_ops10~15.py`, `operations/buyer/test_buyer_ops*.py`). 네트워크 없이 판단 함수만 검사한다.

## 2. 아키텍처와 키트 기본형과의 차이

그림: `architecture.tex` → `architecture.pdf`.

| 항목 | 키트 기본형 (`starter --round3`, 기본 restock) | 이 에이전트 |
|---|---|---|
| 모델 위치 | 하네스(모델)가 흥정의 매 수를 정한다 | 런타임 모델 없음. 사람이 운영 범위를 승인하고, 그 안에서 규칙 감독이 판단한다 |
| 호가 | 원가 이상, 품질·보증 정보 없음 | 등급별 목표 호가, 원가+보관비 105% 하한, 영수증에 정직한 등급 주장. A·B는 시장 중앙가 실험 |
| 재입고 | 재고가 적으면 무작위 SKU | 공식 원장 검증(판매율·이익·원가 미만 비율) → 단기 승인 파일 → `safe_restock` 재검사 → 1회 소진 |
| Buyer | 하네스가 가게와 값을 정한다 | 병렬 호가 → 기대 Saved 순위 → 결제 직전 정책. 100주문마다 board로 성과를 검증하는 감독 |
| 안전 | 없음 | 손실·환불·파손·만료·중복 정산·평판 하락·정산 정체 시 신규 매입/주문만 멈추고 판매는 유지. 자동 재시작 없음 |
| 운영 | `join` 한 번 | flock 단일 인스턴스, 가게와 buyer 키트 분리, 시각 기반 종료 감시 |

**구조의 핵심:** 돈이 움직이는 모든 지점(공급 매입, 결제)은 규칙 게이트를 지나고, 게이트의 범위는 사람이 승인한다. 감독은 공식 원장과 board라는 외부 사실로 자기 성과를 확인한 뒤에만 다음 단계로 넘어간다.

## 3. Round 3 결과 (임시, 최종 아님)

### 3.1 board (측정: 2026-11-16 19:19:21 KST, 원본: `logs/operations/board-snapshot-20261116T1919KST.json`)

| 항목 | 값 |
|---|---|
| Players Class 보기(학생 46명) | **36위**, student_total 0.774 = student_shop 0.087 + student_buyer 0.687 |
| Players 전체(71명) | 47위, total 0.888 = shop 0.196 + buyer 0.692 |
| 가게 | 정산 136건, 매출 1,688,072, Book 327,929, 별점 4.5(리뷰 135), stars_rank 4.3, 환불 0, 파손 0, 정직 주장 132/132 |
| buyer | 주문 2,812, 정산 2,193, Saved 0.7923, Forum 0.764(글 6), served 2,154 |

기본 조건: 가게 정산 136건, buyer 정산 2,193건으로 둘 다 20건 이상이다.

### 3.2 정산 줄 (로그 근거)

| 로그 | 정산 줄 수 | 비고 |
|---|---|---|
| `logs/market-kit.log` (가게 키트) | `[acp] settled` 136줄 | board 가게 정산 136과 일치. buyer `[acp] paid` 23줄(11-04~11-10, 같은 폴더에서 운영) |
| `logs/buyer-test-market-kit.log` | `[acp] paid` 352줄 | 11-15 01:35~06:10 buyer 첫 운영(buyer_ops) 키트 로그 사본 |
| `logs/buyer-market-kit.log` | `[acp] paid` 1,818줄 | 11-15 10:15~ buyer 운영(buyer_ops2~7) 키트 로그 사본. 11-16 19:18 KST 시점 |

세 로그의 buyer 결제 합계 23 + 352 + 1,818 = 2,193은 board buyer 정산 수와 같다.

```
19:13:14 [acp] settled 바리스타룰스 디카페인 라떼 325ml 10개 at 14657, ledger #7348989, unit u-9caad4737bab grade B
19:15:35 [acp] paid 15162 through https://ax-week07.treasuresky.xyz/acp, settled at ledger #7349645: 청정원 굴소스 400-500g 3종 3개 골라담기, grade A, 654.4 h of shelf life left
```

### 3.3 거부 사유 (로그 인용)

buyer 쪽 `logs/buyer-market-kit.log`의 결제 거부 441건 중 327건이 굿즈 B등급, 나머지 대부분은 남은 유통기한 부족이다.

```
10:18:37 [acp] not paying: grade B, the policy accepts ['A']
10:22:49 [acp] not paying: 482.95 h of shelf life left, the policy asks for 540.0
10:37:18 [acp] checkout refused: 409 {"error":"unit u-a4ee3e5ee801 is not in stock for this order"}
10:39:40 [acp] payment not completed: 402 {"error":"payment refused by the market","market":{"error":""}}
```

가게 쪽 `logs/market-kit.log`:

```
19:58:13 [acp] checkout refused: 409 {"error":"unit u-3d3fc03f9346 is not in stock for this order"}
15:26:18 [market] result refused: 502
```

가게 흥정 단계의 거절은 `seller-events.jsonl` 기준 `no_verified_inventory` 974건(재고 없음), `no_price_agreement` 164건이다(11-16 19:20).

### 3.4 Seller 운영 구간 (원장·상태 파일 기준, ops15는 진행 중)

| 구간 | 기간(KST) | 매입 | 정산 | 실현 이익 | 원가 미만 판매 | 결제 리뷰 평균 | 종료 |
|---|---|---|---|---|---|---|---|
| ops9 | 11-14 04:46~07:06 | 1 | 0 | 0 | 0 | 5.0(1) | 안전 중단: 2시간 정산 없음 |
| ops10 | 11-14 17:55~22:27 | 6 | 5 | +9,363 | 0 | 4.2(5) | 안전 중단: 2시간 정산 없음 |
| ops11 | 11-14 22:53~11-15 04:01 | 10 | 7 | +14,409 | 0 | 4.75(8) | 안전 중단: 2시간 정산 없음 |
| ops12 | 11-15 10:18~11:17 | 0 | 0 | 0 | 0 | — | 운영자 중단(설정 교체) |
| ops13 | 11-15 11:23~11-16 05:45 | 60 | 58 | +137,208 | 0 | 4.51(57) | 정상 종료(한도) |
| ops14 | 11-16 10:06~17:00 | 19 | 17 | +50,516 | 0 | 4.59(17) | 정상 종료(시각) |
| ops15 | 11-16 17:06~(11-17 18:00 예정) | 13 | 12 | +15,227 | 0 | 4.47(15) | 진행 중(19:20 측정) |

안전 중단 뒤에는 자동으로 재시작하지 않았다. 다음 구간은 사람이 새 범위를 승인한 뒤 새 상태 파일로 시작했다.

### 3.5 Buyer 운영 구간 (board 역산)

| 구간 | 기간(KST) | 주문 | board 정산 | 정산률 | 거래당 Saved | 종료 |
|---|---|---|---|---|---|---|
| buyer_ops | 11-15 02:55~06:13 | 300 | 265 | 88.3% | 0.843 | 안전 중단: 파손/환불 3.5% > 2% |
| buyer_ops2 | 11-15 10:15~11:12 | 100 | 68 | 68.0% | 0.852 | 안전 중단: 시장 결제 거절 402 |
| buyer_ops3 | 11-15 11:18~14:18 | 300 | 218 | 72.7% | 0.848 | 안전 중단: 파손/환불 2.9% > 2% |
| buyer_ops4 | 11-15 17:41~11-16 10:00 | 1,400 | 1,104 | 78.9% | 0.846 | 종료 시각 도달(감시자) |
| buyer_ops5 | 11-16 10:00~11:12 | 100 | 79 | 79.0% | 0.830 | 안전 중단: board 반영 불일치(검증 불가) |
| buyer_ops6 | 11-16 14:45~17:00 | 200 | 148 | 74.0% | 0.808 | 종료 시각 도달(감시자) |
| buyer_ops7 | 11-16 17:00~(11-17 18:00 예정) | 200 | 149 | 74.5% | 0.811 | 진행 중(19:14 측정) |

파손이 기준을 넘은 뒤(buyer_ops, buyer_ops3) 원장 분석으로 굿즈 등급별 파손률을 확인하고 결제 정책을 굿즈 A등급만으로 바꿨다(사람 승인, 11-15). 그 뒤 구간의 파손은 0건이다.

### 3.6 호가 실험 (진행 중, 임시)

11-16 17:06부터 A·B등급 시작 호가를 같은 SKU·등급의 최근 3시간 시장 중앙가로 정한다(원가×1.05가 더 크면 그 값). 19:14 측정에서 실험 판매 11건의 평균 이익은 1,321, 결제 리뷰 12건의 평균은 4.58이다. 원복 조건(평균 이익 < 800, 리뷰 평균 < 4.3, 6시간 뒤 시간당 정산 < 1.82)에는 걸리지 않았다. 표본이 작아 결론을 내리지 않는다.

### 3.7 11-10에 고친 결함: 한 번 기록된 유닛을 다시 팔 수 없었다

`ledger.py`는 같은 `unit_id`가 상태와 상관없이 한 번이라도 있으면 새 합의를 거부했다. 결제되지 않은 합의나 거절 행이 있는 유닛은 구매자가 값에 동의해도 거래가 닫히지 않았다.

```
21:50:54 25620027      No deal with this shop: TASK_STATE_REJECTED: Duplicate or unrecordable trade..
```

수정(11-10 23:55 운영 반영): 15분 안의 합의만 유닛을 예약하고, 거절 행에는 실제 유닛 ID를 쓰지 않으며, 호가만 받고 떠난 협상은 10분 뒤 유닛을 놓아주고, 합의 시점에 재고를 다시 확인한다. 수정 전 코드는 `backups/pre-ledgerfix-20261110/`에 있다. 수정 후 보유 재고 2개가 3분 안에 팔렸다.

## 4. 키트 없이 배포한 가게

이 가게는 키트로 배포했으므로 기본 조건의 세 번째 줄은 적용 대상이 아니라고 판단했다. `acp_shop.py`는 같은 이름과 키로 등록하면 시장이 마지막 URL만 유지하므로 키트 가게와 동시에 운영할 수 없다. 로컬 테스트(7개)만 했다.

## 5. 한계

- 재입고 제안 모델은 구현했지만 Round 3에서 호출하지 않았다. 런타임 판단은 모두 규칙이다.
- 가게 순위의 병목은 stars_rank의 평가 계정 수다. 결제 리뷰는 많지만 서로 다른 구매자 수가 적다(ops13 58건이 16명).
- 가게는 보증을 걸지 않는다.
- 운영 감독 일부의 docstring은 이전 판에서 복사돼 실제 상수와 다르다(`ops15.py`, `buyer_ops7.py`). 동작은 파일 상단 상수(`END_AT`, `MAX_ORDERS`, `SKUS` 등)를 따른다.

## 6. 제출물 목록

- 코드: 루트의 `seller.py`, `ledger.py`, `safe_restock.py`, `market_buyer.py`(11-10 판), `buyer.py`, `acp_shop.py`, `restock_model_advisor.py` 외 테스트
- 운영 코드(실행본 사본): `operations/seller/`, `operations/buyer/`, `operations/buyer-agent/`(실제 buyer 운영에 쓴 `market_buyer.py`, `agent.json`), `operations/launchers/`
- 로그: `logs/market-kit.log`, `logs/buyer-test-market-kit.log`, `logs/buyer-market-kit.log`, `logs/operations/seller/`, `logs/operations/buyer/`, `logs/operations/board-snapshot-*.json`
- 비밀정보: `*.pem`과 `.market/`은 `.gitignore`로 제외. 키트 owner claim 값은 어느 파일에도 들어 있지 않다(문자열 검사).
