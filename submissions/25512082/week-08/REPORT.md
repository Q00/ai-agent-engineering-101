# Week 08 중간 평가 보고서 — 25512082 (sohyunkim11)

> **상태: 최종본 (2026-11-23 KST 작성).** Round 3는 2026-11-17 23:59 KST에 끝났다.
> 3절의 순위·정산 수는 마감 직전(11-17 23:58:37)과 마감 직후(11-18 00:00:31) board 원본이 근거이며, 둘은 `shop.alive` 하나만 다르다.
> 2026-11-23 16:10 KST에 같은 board(`/api/board`, round 3)를 다시 읽었고 내 행의 값은 같았다. 중간 평가 순위 점수는 공지대로 **Round 3 종료 시각(11-17 23:59) board의 Players Class 보기** 기준이며, 이 보고서의 순위는 그 board 값이다(별도의 성적 공지 값이 아니다).
> 11-16 19:20 임시본은 `backups/pre-final-20261123/REPORT.md`, 11-11 초안은 `backups/pre-submit-20261116/REPORT.md`에 보존했다.

## 0. 개요

| 부품 | 파일 | 런타임에 하는 일 |
|---|---|---|
| Seller (A2A 흥정) | `seller.py` (`SafeSeller`) | 재고에 실제로 있는 유닛 하나를 고르고, 등급별 목표 호가와 (원가+보관비)×1.05 하한으로 흥정한 뒤, Ed25519 서명 영수증(`unit`, `claim`)을 낸다 |
| 재입고 실행 | `safe_restock.py` (agent.json `restock` 훅) | 단기 승인 파일이 있을 때만, 최악 원가·surge·한도 검사를 통과한 줄을 내고 승인 파일을 원자적으로 소진한다 |
| Seller 운영 감독 | `operations/seller/ops15.py` (이전 판 ops9~ops14, 미실행 판 ops16) | 공식 원장으로 SKU별 판매율·이익을 검증해 공급 창마다 승인 파일을 쓰고, 안전 조건을 어기면 신규 매입을 멈춘다 |
| 호가 실험 제어 | `operations/seller/price_exp.py` + `seller.py`의 `market_price_ask` | A·B등급 시작 호가를 시장 중앙가로 바꾸는 12시간 실험. 원복 조건을 스스로 감시한다 |
| 판매 전용 감시 | `operations/seller/sell_guard_1917.py` → `sell_guard_1920.py` → `sell_guard_2355.py` | 11-17 19:00 이후 신규 매입 없이 남은 재고만 파는 구간의 안전 감시와 23:55 종료 |
| Buyer | `operations/buyer-agent/market_buyer.py` + `buyer.py` | 병렬 호가 → 정산 확률 × Saved 증가량 순위 → 결제 직전 증명서·등급·남은 시간·보증 검사(`accept`) |
| Buyer 운영 감독 | `operations/buyer/buyer_ops7.py` (이전 판 buyer_ops, buyer_ops2~6) + `stop_guard7.sh` | buyer-only 키트를 100주문 단위로 돌리고, 공식 board로 구간 성과를 검증해 계속·중단을 정한다 |
| Buyer Saved 감시 | `operations/buyer/saved_guard7.py` | 2분마다 board에서 내 Saved와 바로 아래 학생의 Saved 차이를 보고, 기준 이하이면 buyer를 멈춘다 |
| 마감 기록 | `operations/seller/finalize_20261117.py` | 23:58·00:00·00:05·00:15에 board를 저장하고 원장에서 내 정산을 다시 센다(읽기 전용) |
| 키트 없는 ACP 가게 | `acp_shop.py` | `/.well-known/acp.json`과 checkout 세션을 직접 구현. 로컬 테스트만 했다(4절) |
| 프로세스 관리 | `run_market_locked.sh`, `run_shop_only_persistent.sh` | flock으로 인스턴스를 하나만 두고, 키트가 죽으면 다시 띄운다 |

공식 키트 `scripts/market_kit.py`는 **수정하지 않았다**(2026-11-23 16:24 KST, `git diff upstream/main -- scripts/market_kit.py` 0줄, `logs/final-tests-20261123.log`).
가게는 이 폴더(`submissions/25512082/week-08`)에서 `--shop-only`로, buyer는 별도 폴더의 `--buyer-only` 키트로 띄웠다. 두 키트는 같은 이름(`sohyunkim11`)으로 등록된다.

## 1. 부품별 설계

### 1.1 모델

**Round 3 운영 중 런타임 모델 호출은 0회다.** 흥정, 호가, 영수증 서명, 결제 전 검사, 재입고, 운영 감독은 모두 규칙 코드다.

- 근거: `seller.py`, `market_buyer.py`, `buyer.py`, `safe_restock.py`, `operations/` 아래 감독·감시 스크립트는 `harness.py`를 import하지 않고 모델 API도 부르지 않는다. `logs/model-decisions.jsonl`에는 1줄만 있으며 `model_called: false`(11-10 23:24 KST 규칙 경로 시험)다.
- 모델 자리로 설계한 것: `restock_model_advisor.py`. 규칙이 거른 후보 SKU를 모델이 정렬하고, 규칙이 상한을 다시 적용하며(`enforce`), 실패하면 규칙 순위로 돌아간다. 코드와 테스트(4개)만 있고 **Round 3에서 실제로 호출하지 않았다.** 대신 운영 감독이 공식 원장 통계로 SKU를 고른다.
- 개발 도구로 쓴 모델: 11-11까지의 코드는 Codex CLI(0.163.0, gpt-6.1-sol)로 작성·검토했다(`agent.json`의 `harness` 필드). 11-13 이후의 운영 감독·감시 스크립트, 원장 분석, 이 보고서 정리는 Claude Code(Claude Opus 5.5)로 작성했다. 운영 범위(예산, SKU, 단가 상한, 중단 기준, 재시작 여부)는 매번 사람이 승인했고, 스크립트는 그 범위 안에서만 동작한다.
- 모델을 거래 경로에서 뺀 근거: 공식 8주차 강의 자료(EP 5)에 다른 참가자(chanarun)의 buyer가 자기 모델의 API 한도 초과(429)와 잘못된 API 키 때문에 가게들에 1점을 6,837번 준 사례가 있다. 이는 **우리 에이전트의 기록이 아니며**, 우리 7주차·8주차 로그에서는 429 오류를 찾지 못했다. 이 사례를 보고 10-27에 모델 호출이 실패하면 규칙이 대신 결정하도록 바꿨다(이 브랜치의 커밋 `c4e9f6d` Seller, `f7c8e02` Buyer). 그 뒤 11-02 오프라인 시뮬레이션(`experiments/results/FINAL_OPERATION_READINESS.md`, 외부 LLM 호출 0)으로 결정적 규칙 정책들끼리 비교해 운영 정책을 골랐다. 이 시뮬레이션은 LLM 정책과 비교한 것이 아니므로, 모델을 뺀 것이 성과를 높였다는 근거는 아니다. `experiments/OPERATIONS_PLAN.md`에는 "deterministic guards must remain authoritative"라고 적혀 있다.

### 1.2 컨텍스트와 메모리

메타 하네스는 쓰지 않았다. 컨텍스트는 **파일 메모리 + 공식 공개 데이터(원장, board)** 이고, 규칙 코드가 이를 읽어 다음 결정에 넣는다. 지난 거래는 원장·상태 파일로, 평판은 board의 `stars_rank`·Saved로 다음 결정에 들어간다.

| 저장소 | 내용 | 다음 결정에 들어가는 곳 |
|---|---|---|
| `.market/seller-ledger.json` (`ledger.py`, 원자적 쓰기) | 합의·거절 | 15분 안의 합의만 유닛을 예약한다(`unit_window`). 거절 행은 유닛을 잠그지 않는다 |
| `.market/seller-events.jsonl` | 호가·제안·수락·거절 사유 | 가격 거절률 측정. 11-10 23:57(원장 수정 후 새 파일)~11-17 22:53 기록: quote 1,555, offer 1,691, accept 498, `no_verified_inventory` 1,542, `no_price_agreement` 276, `invalid_offer` 2 |
| `.market/inventory.json` (키트) | 유닛별 원가·등급·만료·surge | 만료 임박 유닛 우선 출고, 보관비를 하한에 반영, 최악 원가 계산 |
| `.market/price-experiment.json` | SKU·등급별 최근 3시간 시장 중앙가 | `market_price_ask`가 A·B 시작 호가로 사용(유효 기간 안, 30분 안에 갱신된 경우만) |
| `ops*_state.json` / `ops*.log` (감독) | 매입 유닛, 정산, 결제 리뷰, 원장 커서, board 스냅샷 | 다음 공급 창의 매입 여부, 실현 이익, 안전 중단 판단. ops16은 ops15 상태 파일을 읽어 누적 한도를 이어받는다 |
| 공식 원장 `/api/ledger` | 시장 전체 supply·settle·broken·result | SKU별 3시간 판매율(우측 검열 보정), 실현 이익 중앙값, 원가 미만 비율, 정산 수, 내 buyer의 파손이 보증 환불인지 여부 |
| `.market/payments.jsonl`, `broken.jsonl` (buyer 키트) | 결제·거부·파손 | 가게별 정산 확률, 100주문 구간의 결제 수와 파손 수 |
| `buyer_ops*_state.json` / `.log`, `saved_guard7.log` | 구간별 board 전후 값, 2분 간격 Saved | 구간 Saved 역산 Σ = saved₁(deals₁+100) − saved₀(deals₀+100), 바로 아래 학생과의 Saved 차이 |

### 1.3 툴

| 툴 | 무엇을 | 어떻게 |
|---|---|---|
| A2A 흥정 서버 | `SafeSeller.execute` | SKU 매칭 → 호가 → 최대 3라운드, 라운드마다 호가×0.88, 하한 아래로는 안 내려간다. 합의 순간 `inventory.json`에서 유닛을 다시 확인 |
| 영수증 서명 | `sign_receipt` | canonical JSON에 Ed25519. 등급 주장(`claim`)은 실제 유닛 등급. 최종 board 기준 claims 215건 모두 honest |
| 승인형 재입고 | `safe_restock.build_plan` | 승인 만료, 라운드, 창 열림, SKU당 1개, 최악 원가(정가×0.75×surge), 실행·보유 상한 중 하나라도 어기면 빈 계획. `os.link`로 1회 소진 |
| 운영 감독(Seller) | `ops15.gate`, `sku_stats`, `stop_reasons`, `reconcile` | 공급 창마다 원장 기준 검증 → 승인 파일 작성 → 사용·미사용 기록을 `restock-approval.consumed-*.json` / `unused-*.json`으로 남긴다 |
| 호가 실험 | `price_exp.py`, `market_price_ask` | 5분마다 중앙가 파일 갱신. 원가×1.05 하한이 항상 우선. 원복 조건을 어기거나 12시간이 지나면 파일을 지우고 종료 |
| 판매 전용 감시 | `sell_guard_2355.py` | 승인 파일 출현, 새 재고, 원가 미만 정산, 같은 유닛 중복 정산, 환불·파손 증가, `stars_rank` < 3.93 연속 2회 중 하나면 STOP. 23:55에 합의 후 미정산 거래를 최대 120초 기다린 뒤 종료 |
| Buyer 호가 비교 | `rank_verified_quotes` | 정산 확률 × Saved 증가량, 무결성 경고 가게 후순위 |
| 결제 전 검사 | `_receipt_precheck`, `_accept_policy` | 품목, 예산, 보증 시간 확인 후 `accept`(cert, 등급, 남은 시간, 굿즈 보증 6시간)를 키트에 전달. 11-15부터 굿즈는 A등급만 |
| 운영 감독(Buyer) | `buyer_ops7.segment`, `block_ok`, `gate_ok` | 100주문마다 board로 정산 수·거래당 Saved를 검증하고, 원장에서 내 파손을 보증 환불과 환불 없는 파손(−1)으로 나눈다. 500주문 관문 |
| Saved 감시 | `saved_guard7.decide` | Saved ≤ 0.7885이거나 바로 아래 학생과의 차이 ≤ 0.0004이면 buyer 감독·키트에 SIGTERM. 자동 재시작 없음 |
| 종료 감시 | `stop_guard7.sh` | 종료 시각이나 주문 상한에 도달하면 감독과 키트 프로세스 그룹에 SIGTERM(이중 안전장치) |
| ACP checkout(키트 없이) | `acp_shop.py` | 서명한 영수증 사본과 대조해 세션 생성, complete 때 `/api/settle` 호출 |

테스트: 2026-11-23 16:24 KST에 네트워크 없이 단위 테스트 245개(루트 59, `operations/seller` 83, `operations/buyer` 43, `experiments` 60)를 돌려 모두 통과했다. 제출 폴더의 `.py` 100개는 구문 검사를 통과했다(`logs/final-tests-20261123.log`). 운영 감독마다 테스트 파일을 같이 두었고, 판단 함수만 검사한다.

## 2. 아키텍처와 키트 기본형과의 차이

그림: `architecture.tex` → `architecture.pdf`.

| 항목 | 키트 기본형 (`starter --round3`, 기본 restock) | 이 에이전트 |
|---|---|---|
| 모델 위치 | 하네스(모델)가 흥정의 매 수를 정한다 | 런타임 모델 없음. 사람이 운영 범위를 승인하고, 그 안에서 규칙 감독이 판단한다 |
| 호가 | 원가 이상, 품질·보증 정보 없음 | 등급별 목표 호가(A 0.78, B 0.72, C 0.65 × 정가), (원가+보관비)×1.05 하한, 영수증에 정직한 등급 주장. 실험 기간에는 A·B를 시장 중앙가로 |
| 재입고 | 재고가 적으면 무작위 SKU | 공식 원장 검증(판매율·이익·원가 미만 비율) → 단기 승인 파일 → `safe_restock` 재검사 → 1회 소진 |
| Buyer | 하네스가 가게와 값을 정한다 | 병렬 호가 → 기대 Saved 순위 → 결제 직전 정책. 100주문마다 board로 성과를 검증하는 감독, 순위 하락을 막는 Saved 감시 |
| 안전 | 없음 | 손실·환불·파손·만료·중복 정산·평판 하락·정산 정체 시 신규 매입/주문만 멈추고 판매는 유지. 자동 재시작 없음 |
| 운영 | `join` 한 번 | flock 단일 인스턴스, 가게와 buyer 키트 분리, 시각 기반 종료 감시, 마감 board 기록 |

**실행 흐름:** 시장 → 키트(가게용·buyer용 두 프로세스) → Seller/Buyer 규칙 코드 → 파일 메모리. 그 위에서 감독이 원장·board를 읽어 승인 파일(Seller)이나 다음 100주문 블록(Buyer)을 정하고, 감시자가 STOP 조건을 본다. 사람은 운영 구간마다 범위를 승인한다.

**구조의 핵심:** 돈이 움직이는 모든 지점(공급 매입, 결제)은 규칙 게이트를 지나고, 게이트의 범위는 사람이 승인한다. 감독은 공식 원장과 board라는 외부 사실로 자기 성과를 확인한 뒤에만 다음 단계로 넘어간다.

## 3. Round 3 결과 (최종)

### 3.1 최종 board

원본: `logs/operations/final/board_raw_pre_close_2358.json`(11-17 23:58:37), `board_raw_post_close_0000.json`(11-18 00:00:31), `board_raw_recheck_20261123T1610KST.json`(11-23 16:10). 요약: `logs/operations/final/FINAL_RESULT.json`.

| 항목 | 값 |
|---|---|
| **중간 평가 순위: Players 표 Class 보기 (모집단: 학생 46명)** | **33위 / 46**, 학생 백분위 student_pct **0.289**. student_total 0.845 = student_shop 0.167 + student_buyer 0.678 (바로 위 학생 0.993, 바로 아래 0.805) |
| (참고) Players 표 전체 보기 (모집단: 플레이어 72명) | 46위, total 0.948 = 가게 ALL 0.265 + buyer ALL 0.683 |
| 가게 | 정산 **219**건, Revenue 2,578,708, Book 472,752, 보유 재고 원가 0, 별점 4.47(리뷰 218), stars_rank 4.38, 환불 0, 파손 0, 정직 주장 215/215 |
| buyer | 주문 3,519, 정산 **2,724**, Saved 0.7884, paid 0.477, Forum 0.772(글 7), served 2,666 |
| 가게 지갑 | 시작 5,000,000 → 마감 5,472,752, 차이 +472,752 (`.market/inventory.json`의 `start`·`balance`, 사본 `logs/operations/final/FINAL_RESULT.json`) |

마감 직후 board를 기록한 `logs/operations/final/finalize.log`의 해당 줄(원문 그대로):

```
{"t": "11-18 00:00:31", "kind": "board", "label": "post_close_0000", "measured_at": "11-18 00:00:31", "round": 3, "current": 3, "shop": {"alive": false, "deals": 219, "revenue": 2578708, "profit": 472752, "book": 472752, "stars": 4.47, "reviews": 218, "stars_rank": 4.38, "refunds": 0, "broken": 0, "stock_units": 0, "stock_cost": 0, "claims": 215, "honest_claims": 215, "all": 0.265}, "buyer": {"orders": 3519, "deals": 2724, "saved": 0.7884, "forum": 0.772, "posts": 7, "all": 0.683}, "student_shop": 0.167, "student_buyer": 0.678, "student_total": 0.845, "student_rank": 33, "students": 46, "student_pct": 0.289, "neighbors": {"above": 0.993, "below": 0.805}, "players_rank": 46, "players": 72, "players_total": 0.948}
```

**두 점수 체계를 섞지 않는다.**
- 학생 기준(`student_shop`, `student_buyer`, `student_total`, `student_pct`): board가 학생 46명만을 모집단으로 계산해 주는 값이다. 평가 점수는 이 중 `student_pct`다. 학생 46명을 `student_total`로 정렬하면 나는 33위이고, (나보다 낮은 학생 수 13) ÷ (46 − 1) = 0.289로 board의 `student_pct`와 같다. `student_shop`·`student_buyer`의 계산식은 board가 주는 값을 그대로 옮겼고 직접 재현하지 않았다.
- 전체 기준(가게 ALL 0.265, buyer ALL 0.683, total 0.948): 학생이 아닌 플레이어를 포함한 72명 모집단의 값이다. 평가 점수가 아니며 참고로만 적는다.

**Book과 실현 이익의 구분.** board의 Book은 지갑 손익(마감 잔액 − 시작 잔액)에 보유 재고 원가를 더한 값으로 읽힌다. 11-17 18:05 board는 Book 469,735, 재고 원가 11,730이었고 같은 시각 지갑 차이는 458,005였다(458,005 + 11,730 = 469,735, `logs/operations/final/HANDOFF-20261117-1810.md`). 마감에는 재고가 0이라 Book 472,752와 지갑 차이 +472,752가 같아졌을 뿐, 두 지표는 정의가 다르다. 3.6의 구간별 "실현 이익"은 각 감독이 산 유닛만 대상으로 원장 정산 금액 − 매입 원가를 더한 값이라(보관비와 감독 밖 거래는 빠짐) Book과 계산 범위가 다르다. 구간 합계(ops9~15: 0 + 9,363 + 14,409 + 0 + 137,208 + 50,516 + 156,485 = 367,981)와 Book의 차이를 거래별로 나누어 검증하지는 않았다.
기본 조건: 가게 정산 219건, buyer 정산 2,724건으로 둘 다 20건 이상이다.

### 3.2 10주차 기준 숫자와 비교

10주차에 남긴 board는 `experiments/analysis-20261110/board-20261110T2155.json`(11-10 21:55) 하나다.

| 항목 | 11-10 21:55 | 11-17 23:58 (최종) |
|---|---|---|
| 학생 순위 | 45 / 46 | **33 / 46** |
| 학생 백분위 student_pct | 0.022 | 0.289 |
| student_total (학생 기준) | 0.199 (student_shop 0.018 + student_buyer 0.181) | 0.845 (student_shop 0.167 + student_buyer 0.678) |
| (참고) 가게 ALL / buyer ALL (전체 기준) | 0.070 / 0.232 | 0.265 / 0.683 |
| 가게 정산 / Book | 6건 / 8,057 | 219건 / 472,752 |
| 가게 stars_rank | 3.80 | 4.38 |
| buyer 정산 / Saved | 23건 / 0.029 | 2,724건 / 0.7884 |

### 3.3 정산 수 (로그·원장 교차 확인)

| 근거 | 가게 정산 | buyer 정산 |
|---|---|---|
| 최종 board | 219 | 2,724 |
| `logs/market-kit.log` (가게 키트, 이 폴더) | `[acp] settled` 219줄 | `[acp] paid` 23줄(11-04~11-10, 같은 폴더에서 buyer도 운영) |
| `logs/buyer-test-market-kit.log` | — | `[acp] paid` 352줄 (11-15 01:35~06:10) |
| `logs/buyer-market-kit.log` | — | `[acp] paid` 2,349줄 (11-15 10:15~11-17 03:22) |
| 합계 | 219 | 23 + 352 + 2,349 = 2,724 |

마지막 정상 정산(가게, 원장 #7876619 11-17 22:53:41 확인, `logs/operations/final/ledger_ours_after_7789219.json`):

```
22:53:40 [acp] settled 바리스타룰스 디카페인 라떼 325ml 10개 at 14747, ledger #7876619, unit u-5a9bafc674c7 grade B
```

buyer의 마지막 결제(`logs/buyer-market-kit.log`):

```
03:22:34 [acp] paid 5065 through https://sky-greg-motivation-annex.trycloudflare.com/acp, settled at ledger #7510035: 베지푸드 비건오뎅탕 230g //콩고기 비건 채식, grade A, 691.5 h of shelf life left
```

`logs/market-kit.log`의 첫 부분은 7주차 폴더에서 시작한 기록이다. 8주차 안내대로 지난주 폴더를 키와 함께 복사해 다시 join했기 때문이며, `market_kit 4, .../week-08` 줄(2,966번째 줄)부터가 Round 3 키트 v4 기록이다.

### 3.4 거부 사유 (로그 집계와 인용)

| 위치 | 사유 | 건수 |
|---|---|---|
| 가게 흥정 (`seller-events.jsonl`, 11-10 23:57~) | `no_verified_inventory` (요청 품목 재고 없음) | 1,542 |
| | `no_price_agreement` (3라운드 안에 하한 이상 합의 실패) | 276 |
| | `invalid_offer` | 2 |
| buyer 결제 전 검사 (`buyer-market-kit.log`) | 굿즈 B등급 (A만 허용) | 429 |
| | 남은 유통기한 부족 | 161 |
| | C등급 | 5 |
| buyer 결제 전 검사 (`buyer-test-market-kit.log`) | 남은 유통기한 부족 26, C등급 2 | 28 |
| 가게 측 checkout (`market-kit.log`) | 409 재고 없음 | 2 |
| 시장 오류 | `result refused` 500/502: buyer 로그 3 + 20, 가게 로그 4 / `order failed: 502` 10 / 402 결제 거절 2 | — |

```
03:19:54 [acp] not paying: grade B, the policy accepts ['A']
03:13:22 [acp] not paying: 383.85 h of shelf life left, the policy asks for 540.0
08:49:59 [acp] checkout refused: 409 {"error":"unit u-a6c46fa4fb07 is not in stock for this order"}
10:39:40 [acp] payment not completed: 402 {"error":"payment refused by the market","market":{"error":""}}
20:09:06 [market] result refused: 500 Internal Server Error
```

가게 쪽 `logs/market-kit.log`:

```
21:10:10 [acp] checkout refused: 409 {"error":"unit u-6b281c09a926 is not in stock for this order"}
19:49:29 [market] order failed: 502
```

해석: 가게 거절의 85%(1,542/1,820)는 가격이 아니라 **재고 없음**이다. 같은 기간 가게 수락은 498건인데 board 정산은 219건(이 중 6건은 11-10 이전)이라, 합의 뒤 buyer가 checkout하지 않은 거래도 많았다. buyer 거부는 대부분 스스로 정한 품질 정책(굿즈 A등급만, 유통기한) 때문이다.

### 3.5 환불·파손·STOP 기록

- 가게: 환불 0, 파손 0, 원가 미만 판매 0, 중복 정산 0 (board, ops 로그).
- buyer: 굿즈 A등급 정책 이전(11-15) `buyer_ops`·`buyer_ops3`에서 환불 없는 파손 비율이 2%를 넘어 각각 안전 중단했다. 정책을 바꾼 뒤(buyer_ops4~7)에도 파손은 있었지만, 공식 원장 기준 **29건 모두 보증 환불**이었고 환불 없는 파손(−1)은 0건이다(`buyer_ops4~7_state.json`의 `refunds_total`·`minus1_total`). 키트의 `broken.jsonl`에는 11-15 13:35~11-17 02:28 파손 29건이 기록돼 있다. (11-16 임시본의 "그 뒤 구간의 파손은 0건"은 −1 파손만 센 표현이라 이번에 바로잡았다.)
- 안전 STOP과 운영 중단 (시각 KST):

| 시각 | 대상 | 기록 | 처리 |
|---|---|---|---|
| 11-14 07:06, 22:27, 11-15 04:01 | Seller ops9·10·11 | 2시간 정산 없음 | 자동 재시작 없이 다음 구간을 새로 승인 |
| 11-15 06:13, 14:18 | buyer_ops, buyer_ops3 | 환불 없는 파손 3.5%, 2.9% > 2% | 굿즈 A등급만 결제로 정책 변경 |
| 11-15 11:12 | buyer_ops2 | 시장 결제 거절 402 | 중단 |
| 11-16 11:12 | buyer_ops5 | board가 구간 결제를 다 반영하지 않음(검증 불가) | 중단 |
| 11-17 03:23:15 | buyer (Saved 감시) | 바로 아래 학생과 Saved 차이 0.0004 | buyer 종료, 이후 재시작 안 함 |
| 11-17 18:05:02 | ops16 | 계획된 종료 스크립트가 원장 백필 중에 SIGTERM | ops16 매입 0건 |
| 11-17 19:10:12 | 판매 전용 가게 | `stars_rank 3.0 < 3.93` (재등록 직후 1회 판독, 이후 board 4.38) | 사람 승인으로 19:20에 "연속 2회" 조건으로 재시작 |
| 11-17 20:15 | 판매 전용 가게 | 계획된 일시 중단(STOP 아님) | 21:07 재개 전 원장·재고 불일치 없음 확인 |
| 11-17 23:55:03 | 판매 전용 가게 | 종료 시각 도달, 대기 중 미정산 0 | 종료, 23:58 잔여 거래 프로세스 0 |

### 3.6 Seller 운영 구간 (원장·상태 파일 기준)

| 구간 | 기간(KST) | 매입 | 구간 내 정산 | 실현 이익(정산액 − 매입 원가, 감독 매입분만) | 원가 미만 판매 | 결제 리뷰 평균 | 종료 |
|---|---|---|---|---|---|---|---|
| ops9 | 11-14 04:46~07:06 | 1 | 0 | 0 | 0 | 5.0(1) | 안전 중단: 2시간 정산 없음 |
| ops10 | 11-14 17:55~22:27 | 6 | 5 | +9,363 | 0 | 4.2(5) | 안전 중단: 2시간 정산 없음 |
| ops11 | 11-14 22:53~11-15 04:01 | 10 | 7 | +14,409 | 0 | 4.75(8) | 안전 중단: 2시간 정산 없음 |
| ops12 | 11-15 10:18~11:17 | 0 | 0 | 0 | 0 | — | 운영자 중단(설정 교체) |
| ops13 | 11-15 11:23~11-16 05:45 | 60 | 58 | +137,208 | 0 | 4.51(57) | 정상 종료(한도) |
| ops14 | 11-16 10:06~17:00 | 19 | 17 | +50,516 | 0 | 4.59(17) | 정상 종료(시각) |
| ops15 | 11-16 17:06~11-17 18:00 | 95 | 93 | +156,485 | 0 | 미집계 | 정상 종료(시각) |
| ops16 | 11-17 18:00~18:05 | 0 | 1 (18:03:32, ops15 매입분) | — | 0 | — | 계획된 종료(백필 중) |
| 판매 전용 | 11-17 19:20~23:55 | 0 | 1 (22:53:41) | — | 0 | — | 종료 시각 |

ops15의 누적 매입은 858,966원 / 95개로 승인 한도(900,000원·100개) 안이었고, 남은 2개는 18:03:32(#7789220)와 22:53:41(#7876619)에 팔려 마감 재고는 0이다.

### 3.7 Buyer 운영 구간과 Saved 하락 감시

| 구간 | 기간(KST) | 주문 | board 정산 | 정산률 | 거래당 Saved | 종료 |
|---|---|---|---|---|---|---|
| buyer_ops | 11-15 02:55~06:13 | 300 | 265 | 88.3% | 0.843 | 안전 중단: 파손 3.5% > 2% |
| buyer_ops2 | 11-15 10:15~11:12 | 100 | 68 | 68.0% | 0.852 | 안전 중단: 시장 결제 거절 402 |
| buyer_ops3 | 11-15 11:18~14:18 | 300 | 218 | 72.7% | 0.848 | 안전 중단: 파손 2.9% > 2% |
| buyer_ops4 | 11-15 17:41~11-16 10:00 | 1,400 | 1,104 | 78.9% | 0.846 | 종료 시각 도달(감시자) |
| buyer_ops5 | 11-16 10:00~11:12 | 100 | 79 | 79.0% | 0.830 | 안전 중단: board 반영 불일치 |
| buyer_ops6 | 11-16 14:45~17:00 | 200 | 148 | 74.0% | 0.808 | 종료 시각 도달(감시자) |
| buyer_ops7 | 11-16 17:00~11-17 03:23 | 900 (+10번째 블록 도중 중단) | 667 | 74.1% | 0.788 | Saved 감시 STOP |

buyer_ops7 값은 완료된 100주문 블록 9개의 합이다(`logs/operations/buyer/buyer_ops7.log`). 전체로는 주문 3,519, 정산 2,724(77.4%)다.

**Saved 하락 감시(`saved_guard7.py`, 11-16 23:20 시작):** buyer_ops7 동안 내 buyer Saved가 0.7923(11-16 20:18, 300주문 시점)에서 내려가기 시작했다. 거래당 Saved가 누적 평균보다 낮으면 거래가 늘수록 평균이 내려가므로, 바로 아래 학생과의 차이를 2분마다 보다가 0.0004 이하가 되면 buyer를 멈추게 했다.

| 시각 | 내 Saved | 바로 아래 학생 | 차이 | buyer 정산 |
|---|---|---|---|---|
| 11-16 23:20:40 | 0.7918 | 0.7882 | 0.0036 | 2,451 |
| 11-17 01:03:22 | 0.7913 | 0.7887 | 0.0026 | 2,560 |
| 11-17 03:16:48 | 0.7901 | 0.7897 | 0.0004 | 2,713 |
| 11-17 03:23:15 | 0.7902 | 0.7898 | 0.0004 → STOP | 2,722 |

- 결함과 수정: 03:16:48에 차이가 이미 0.0004였지만 부동소수점 비교(`0.7901 − 0.7897`이 0.0004보다 아주 조금 큼) 때문에 `ok`로 판정했다. 03:18:58에 4자리 반올림 비교로 고쳐 다시 시작했고(`saved_guard7.py.pre-roundfix`와 비교 가능), 03:23:15에 STOP했다.
- 결과: STOP 뒤 buyer는 다시 켜지 않았고 board 정산은 2,724에서 멈췄다. 그런데 마감 board의 Saved는 0.7884로 STOP 시점(0.7902)보다 낮다. 정산이 2건만 늘었으므로 그것만으로는 설명되지 않는다. STOP 전에 산 굿즈의 보증 환불이 뒤늦게 반영됐을 가능성이 있으나(감독 코드는 환불을 0점으로 다룬다) 원장으로 확인하지는 않았다. Buyer ALL은 0.692(11-17 03:11 board)에서 0.683(마감)으로 내려갔다. 따라서 이 감시가 buyer 순위를 지켰다고 단정할 근거는 없다. 확실한 것은 STOP 이후 추가 주문으로 평균이 더 떨어지는 일은 없었다는 점뿐이다.

### 3.8 호가 실험 (ops14 대 ops15)

11-16 17:06~11-17 05:08 동안 A·B등급 시작 호가를 같은 SKU·등급의 최근 3시간 시장 중앙가로 정했다(원가×1.05가 더 크면 그 값). 원복 조건(평균 이익 < 800, 결제 리뷰 평균 < 4.3, 6시간 뒤 시간당 정산 < 1.82)에는 걸리지 않았고 12시간 만료로 끝났다(`logs/operations/seller/price_exp.log`, 마지막 기록: 실험 판매 52건, 평균 이익 1,306, 리뷰 53건 평균 4.58).

ops14·ops15 `end` 기록의 실현 거래(`realized`)를 다시 집계했다.

| 구간 | 호가 방식 | A·B 판매 | A·B 평균 이익 | A·B 이익 중앙값 | A·B 판매까지 중앙값 | 시간당 판매(전 등급) |
|---|---|---|---|---|---|---|
| ops14 (11-16 10:06~17:00, 6 SKU) | 등급별 목표 비율 | 11 | 3,721 | 3,464 | 32.3분 | 17건 / 6.9h ≈ 2.5 |
| ops15 실험 중 (17:06~05:08, 10 SKU) | 시장 중앙가 | 40 | 869 | 650 | 9.7분 | 53건 / 12h ≈ 4.4 |
| ops15 실험 후 (05:08~18:00, 10 SKU) | 등급별 목표 비율 | 31 | 1,658 | 1,043 | 18.1분 | 40건 / 12.9h ≈ 3.1 |

- 분석 대상: ops14·ops15 감독이 산 유닛 중 각 감독 종료 전에 정산된 것(`end.realized`, ops14 17건, ops15 93건). 감독 밖에서 산 유닛과 18:00 이후 정산 2건은 빠졌다. 구간 구분은 정산 시각 기준이고, 실험 중에 팔린 A·B 유닛이 실제로 중앙가 호가로 팔렸는지는 거래별로 확인하지 않았다(`market_price_ask`는 중앙가 파일이 30분 안에 갱신됐고 해당 SKU·등급 값이 있을 때만 적용되며, 원가×1.05 하한이 우선한다). `price_exp.log`의 실험 판매 52건과 이 집계의 53건이 1건 다른 것도 같은 경계 차이로 본다.
- 관찰: 중앙가 호가는 A·B를 더 빨리, 더 많이 팔았지만 한 개당 이익은 실험 후 구간의 약 절반이었다. 실험 중 C등급(호가 방식 변경 없음)은 평균 2,707로 A·B보다 이익이 컸다.
- 한계: ops14와 ops15는 SKU 수(6→10), 매입 한도, 시간대가 달라서 이 차이를 호가 방식 하나의 효과로 볼 수 없다. 무작위 배정이 아닌 전후 비교이고 표본도 작다. "중앙가 호가가 거래 속도를 높이고 단위 이익을 낮춘 것과 함께 관측됐다"까지만 말할 수 있다.
- 순위 쪽에서 본 결과: ops15 동안 board 가게 정산이 122 → 217, Book이 311,178 → 469,187, 학생 순위가 37위 → 33위로 바뀌었다. 정산 수·Book 증가는 매입 규모 확대(ops15 95개, ops14 19개)의 영향이 크다.

### 3.9 잘 된 것과 안 된 것

잘 된 것 (근거가 있는 것만):
- 규칙 게이트: 가게 환불·파손·원가 미만 판매·중복 정산 0, 정직 주장 215/215, buyer 환불 없는 파손은 정책 변경 후 0.
- 원장 검증형 재입고: 원장 판매율·이익 조건을 통과한 SKU만 사서 ops10~ops15 매입분에서 손실 판매가 없었고, 마감 재고 0으로 끝났다.
- 굿즈 A등급 + 보증 6시간 정책: 정책 변경 뒤 파손 29건이 모두 보증 환불로 처리됐다.
- 결과: 10주차 기준 학생 45위(student_pct 0.022) → 최종 33위(student_pct 0.289).

안 된 것:
- 재고 부족: 가게 거절의 85%가 `no_verified_inventory`였다. 매입을 승인 범위 안에서 보수적으로 잡아 수요를 놓쳤다.
- 초기 Seller 구간(ops9~11)은 모두 "2시간 정산 없음"으로 멈췄다. 이 조건이 너무 빡빡했을 가능성이 있다.
- 가게 평판 병목: stars_rank는 서로 다른 평가 계정 수의 영향을 받는데, 사는 계정이 적었다(ops13 58건이 16명).
- Saved 감시: 부동소수점 비교 결함이 있었고, 감시 뒤에도 Saved가 내려갔다(3.7).
- 판매 전용 구간 감시가 재등록 직후의 일시적 `stars_rank 3.0` 판독 1회로 STOP했다(19:10). 연속 2회 조건으로 고쳤다.
- ops16은 원장 백필이 계획된 종료 시각보다 길어 한 번도 매입하지 못했다.

### 3.10 11-10에 고친 결함: 한 번 기록된 유닛을 다시 팔 수 없었다

`ledger.py`는 같은 `unit_id`가 상태와 상관없이 한 번이라도 있으면 새 합의를 거부했다. 결제되지 않은 합의나 거절 행이 있는 유닛은 구매자가 값에 동의해도 거래가 닫히지 않았다.

출처: 우리 로그가 아니라 **시장 공개 원장에 다른 buyer(25620027)가 이 가게에 대해 남긴 result 기록**이다. 공개 원장 seq #4881730(11-10 21:50:54 KST)이며, 같은 buyer의 같은 내용이 #4882794(21:54:54)와 #4882897(21:55:14)에도 있다. 기록의 가게 주소는 그 2분 전 우리 키트가 연 터널 주소와 같다(`logs/market-kit.log` 3,886번째 줄 `21:48:48 [tunnel] https://notify-ones-several-farming.trycloudflare.com, waiting until it answers`). 원장 원문 3건은 2026-11-23에 공개 원장에서 다시 조회해 `logs/operations/ledger-20261110-duplicate-rejects.json`에 그대로 저장했다. 아래는 그 파일에서 #4881730 기록의 일부를 그대로 옮긴 것이다.

```
"seq": 4881730, "ts": 1794315054.465, "kind": "result"
"buyer": "25620027", "order": "o-23d5a90bd9cd", "shop": "https://notify-ones-several-farming.trycloudflare.com"
"review": "No deal with this shop: TASK_STATE_REJECTED: Duplicate or unrecordable trade.."
```

수정(11-10 23:55 운영 반영): 15분 안의 합의만 유닛을 예약하고, 거절 행에는 실제 유닛 ID를 쓰지 않으며, 호가만 받고 떠난 협상은 10분 뒤 유닛을 놓아주고, 합의 시점에 재고를 다시 확인한다. 수정 전 코드는 `backups/pre-ledgerfix-20261110/`에 있다. 수정 후 보유 재고 2개가 3분 안에 팔렸다.

## 4. 키트 없이 배포한 가게

이 가게는 키트로 배포했으므로 기본 조건의 세 번째 줄은 적용 대상이 아니라고 판단했다. `acp_shop.py`는 같은 이름과 키로 등록하면 시장이 마지막 URL만 유지하므로 키트 가게와 동시에 운영할 수 없다. 로컬 테스트(7개)만 했다.

## 5. 한계

- 재입고 제안 모델은 구현했지만 Round 3에서 호출하지 않았다. 런타임 판단은 모두 규칙이다.
- 가게는 보증을 걸지 않는다.
- 로그 메시지와 실제 기준이 다른 곳: `buyer_ops4~7.py`의 500주문 관문은 메시지·docstring에 "정산률 70%"라고 쓰지만 실제 비교는 `seg["settle_rate"] < MIN_RATE`이고 `MIN_RATE = 0.65`(11-15 승인)다. **실제로 적용된 기준은 65%다.** 관문 6번(11-15 18:41 ~ 11-16 22:38)의 정산률은 0.724~0.82로 두 기준을 모두 넘었으므로, 이 차이로 판단이 바뀐 적은 없다. Seller 감독 `ops15.py`의 매입 조건은 메시지와 상수가 같다: 3시간 판매율 ≥ 0.70(`MIN_SELL_RATE`), 실현 이익 중앙값 ≥ 1,000(`MIN_PROFIT`), 원가 미만 비율 ≤ 0.15(`MAX_BELOW_COST`), 시장 3시간 정산 ≥ 20(`MIN_SETTLES_3H`), 표본 ≥ 15(`MIN_COHORT`), STOP 기준 `stars_rank` < 3.93(`STARS_FLOOR`), 2시간 정산 없음(`NO_SETTLE_S = 7200`).
- `saved_guard7.py`는 11-17 03:16:48에 기준(차이 ≤ 0.0004)에 닿았는데도 부동소수점 비교 때문에 멈추지 않았다. 03:18:58 수정 후 03:23:15에 멈췄다(3.7). 수정 전 코드 `saved_guard7.py.pre-roundfix`를 함께 제출한다.
- 키트 로그 줄에는 날짜가 없고 시각(HH:MM:SS)만 있다. 날짜는 운영 로그·원장 시각과 맞춰 읽었다.
- 3.8의 가격 비교는 통제 실험이 아니다.

## 6. 테스트 재현 조건

| 테스트 묶음 | 개수 | 제출 파일만으로 재현 | 필요한 조건 |
|---|---|---|---|
| 루트 `test_*.py` | 59 | 가능 | 저장소 `.venv`(키트와 같은 의존성: `cryptography` 등). 개인키는 테스트가 임시로 만든다 |
| `operations/seller/test_ops*.py` | 83 | 가능 | `ops16.py`가 import 시 같은 폴더의 `ops15_state.json`을 읽는다. 그래서 운영 상태 파일 사본을 `operations/seller/`에 함께 둔다(비밀 값 없음 확인) |
| `operations/buyer/test_*.py` | 43 | 가능 | 표준 라이브러리만 |
| `experiments/test_*.py` | 60 | 저장소 안에서 가능 | 공식 키트 `scripts/market_kit.py`를 import하므로 `PYTHONPATH=<저장소 루트>`가 필요 |

실행: 각 폴더에서 `PYTHONPATH=<저장소 루트> .venv/bin/python -m unittest discover -p 'test_*.py'`. 2026-11-23 결과는 `logs/final-tests-20261123.log`. `operations/seller/saved_audit_test.py`는 이름과 달리 공개 원장을 읽는 분석 스크립트(네트워크 필요)라 단위 테스트에 넣지 않았다. 처음 실행에서 ops16 테스트 1개(상태 파일 없음)와 experiments 9개(`scripts` 모듈 경로 없음)가 import 오류로 실패했고, 위 두 조건을 맞춘 뒤 모두 통과했다.

## 7. 제출물 목록

- 아키텍처: `architecture.tex`, `architecture.pdf`
- 코드(가게 실행본): 루트의 `seller.py`, `ledger.py`, `safe_restock.py`, `buyer.py`, `acp_shop.py`, `restock_model_advisor.py`, `shadow_eval.py`, `run_market_locked.sh`, `run_shop_only_persistent.sh`, `agent.json`과 테스트. 루트 `market_buyer.py`는 11-10 판이고, buyer 운영에 실제로 쓴 판은 `operations/buyer-agent/market_buyer.py`(굿즈 A등급 정책 2줄 추가)다. `harness.py`, `pricing.py`, `update_prices.py` 등은 7주차에서 가져온 출발점 코드다.
- 운영 코드(실행본 사본, 해시 일치 확인): `operations/seller/`(ops9~16, price_exp, sell_guard, finalize, handoff, ops16이 읽는 `ops15_state.json`), `operations/buyer/`(buyer_ops~7, stop_guard, saved_guard7), `operations/buyer-agent/`, `operations/launchers/`
- 로그: `logs/market-kit.log`(가게 키트), `logs/buyer-test-market-kit.log`, `logs/buyer-market-kit.log`(buyer 키트 원본 전체 사본), `logs/operations/seller/`, `logs/operations/buyer/`(buyer_ops4~7 상태 파일 포함), `logs/operations/final/`(마감 board 원본, FINAL_RESULT, 인수인계 기록), `logs/final-tests-20261123.log`
- 재입고 승인 기록: 감독이 쓴 승인 파일 133개(`restock-approval.consumed-*.json` 129개, `unused-*.json` 4개)를 내용 그대로 한 줄씩 묶은 `logs/operations/seller/restock-approvals-archive.jsonl`. 원본 파일은 로컬에 두고 `.gitignore`로 커밋에서 뺐다.
- 시뮬레이션: `experiments/`(정책 선택에 쓴 11-02 오프라인 실험, 재현용 시드 데이터 포함)
- 백업: `backups/pre-round3-*`, `pre-ledgerfix-*`(수정 전 코드), `pre-submit-20261116`, `pre-final-20261123`(이전 REPORT·그림)
- 커밋하지 않은 것: `*.pem`, `.market/`, `__pycache__/`, 7주차 폴더에서 함께 온 7주차 시험 로그(week-07 PR에 제출됨), 중간 스냅샷 `ops15_state.snapshot.json`. 목록은 `.gitignore`에 있다.
- 비밀정보: 키트 owner claim 값과 PEM 본문은 커밋 대상 파일 어디에도 없다(2026-11-23 문자열 대조 검사).
