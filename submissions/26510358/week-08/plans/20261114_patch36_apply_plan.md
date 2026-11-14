# 3.6 패치 대응 v2 — 남은 항목 적용 계획

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (또는 subagent-driven-development). 체크박스(`- [ ]`)로 진행 상황을 표시.

**Goal:** `20261113_market_strategy_analysis_v2.md` 4장 항목 중 아직 라이브에 없는 것만 넣어서, 남은 약 72시간 동안 total을 올린다.

**Architecture:** 이미 돌고 있는 buyer/shop/forum 루프는 그대로 둔다. 상수와 순수 함수(`r3lib/pricing.py`, `r3lib/config.py`)만 바꾸고, 새 동작은 fan-out 때처럼 주문 해시 canary로 먼저 켠다. 배포마다 2시간 뒤 `logs/r3/kpi.jsonl`로 판정한다.

**Tech Stack:** Python 3, pytest, 기존 r3lib.

**Spec:** `20261113_market_strategy_analysis_v2.md` (3.6 반영 4장·5.6)

## 지금 상태 (2026-11-14 23:38 KST, `logs/r3/kpi.jsonl`) [관측]

- 순위 14/71, total 1.535 (1위 1.811). 마감 11/17 23:59 KST, 약 72시간 남음
- Shop 0.791: book pct 0.806, revenue pct 0.791, stars 4.40 (pct 0.657)
- Buyer 0.744: saved 0.7318 (pct 0.618), forum 0.888, deals pct 0.882, posts 252/24h (pct 0.735)
- 재고 원가 222M > 예산 187M, premium_budget 0. 3일 넘게 남은 goods 142개, 28.6M

## v2 항목별 적용 여부

| v2 # | 항목 | 상태 | 이 계획 |
|---|---|---|---|
| 1 | 구매자 기준을 마진 점유율로 | **완료** `4d15d1eb` (COST_RATIO, theta 0.72–0.85) | 없음 |
| 2 | fan-out 100% | **완료** `ace832ef` | 없음 |
| 3 | 고가 회전 | 일부. markdown `cost` 팔(원가×1.08)은 이미 3.6 구매자 saved ≈0.87 수준. bandit 팔 1.1은 그대로 | Task 2 |
| 4 | 별점 taste | **완료** `a40b5cf3` | 없음 |
| 5 | 글 288/24h | 252/24h. 원인 미확인 | Task 3 |
| 6, 5.6 | 종료 직전 정책 | 식품 72h 재입고 중단·36h clearance·goods 원가 바닥·12h goods 중단 모두 있음. 구매자 쪽 "원가 아래 추가 흥정 없음"만 없음 | Task 1, Task 4 |
| 7 | 같은 정가비율이면 goods 우선 | **완료** (`candidates`가 margin_share로 정렬) | 없음 |
| (신) | 원가 이하 추가 할인 중단 | 없음. 첫 제안이 정가 0.55 고정이라 A(원가 0.618)·goods(0.594)는 원가 아래에서 시작함 | Task 1 |

## Global Constraints

- 상수 변경이 기존 값 대비 20%를 넘으면 사람 승인 후에만 배포 (v2 4장 머리말)
- 코드는 `submissions/26510358/week-08/` 안에서만 수정 (AGENTS.md)
- 커밋은 작업 단위마다, 실패도 한 줄로 남김. 로그 파일은 고치지 않음
- 배포·재시작은 사람이 확인한 뒤. 배포 노트는 기존 형식대로 `logs/r3/deploy-<UTC>-<name>.txt`
- 마감 12시간 전(11/17 11:59 KST) 이후에는 새 배포 없음

## Review Focus

1. 첫 제안을 원가로 올렸을 때 `ask`가 이미 그보다 낮은 경우 → `min(max_pay, first, ask)`가 유지돼 ask보다 높게 부르지 않아야 함 (Task 1 테스트)
2. `max_pay`가 추정 원가보다 낮은 주문 → 첫 제안이 max_pay를 넘으면 안 됨 (Task 1 테스트)
3. grade가 아직 정해지지 않은 goods → `cost_ratio("goods", None)` 0.594 사용 (Task 1 테스트)
4. bandit 메모리에 이미 1.1 팔의 통계가 남아 있음 → 팔 목록에서 빠진 팔을 choose가 돌려주지 않아야 함 (Task 2 테스트)
5. canary 해시가 fan-out과 같은 해시를 쓰면 두 실험이 겹침 → 다른 salt 사용 (Task 1 테스트)

---

### Task 1: 구매자 첫 제안을 추정 원가 위로 (canary 50%)

v2 "(신) 원가 이하 추가 할인 중단". 3.6에서 원가 아래 가격은 saved가 1로 상한이라, 원가 아래에서 여는 것은 점수 이득 없이 흥정 턴과 결렬 위험만 늘림. 다만 낮은 앵커가 최종가를 낮출 수도 있어서 canary로 비교함.

**Files:**
- Modify: `r3lib/pricing.py:204-221` (`buyer_next_offer`)
- Modify: `buyer_r3.py:225` 근처 호출부, `buyer_r3.py:290-293` 옆에 canary 함수
- Modify: `r3lib/config.py` 구매자 블록에 `BUYER_COST_OPEN_SHARE = 0.5`
- Test: `tests/test_buyer_cost_open.py`

**Interfaces:**
- Produces: `buyer_next_offer(list_price, max_pay, ask, last_offer, n, opening=None, final=False, open_floor=None)` — `open_floor`는 정가 대비 비율. 첫 제안 = `max(list×FIRST_OFFER_RATIO, list×open_floor, opening)`, 그 뒤 기존처럼 `min(max_pay, first, ask)`
- Produces: `buyer_r3.cost_open_arm(order_id) -> bool` — `sha256(f"cost_open:{order_id}")` 기반, `BUYER_COST_OPEN_SHARE` 비율로 True. 협상 로그에 `arm: "cost_open"|"base"` 기록

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_first_offer_opens_at_estimated_cost():
    act, p = pricing.buyer_next_offer(1_000_000, 900_000, None, None, 0, open_floor=0.618)
    assert (act, p) == ("offer", 618_000)

def test_open_floor_never_above_max_pay_or_ask():
    assert pricing.buyer_next_offer(1_000_000, 600_000, None, None, 0, open_floor=0.618)[1] == 600_000
    assert pricing.buyer_next_offer(1_000_000, 900_000, 580_000, None, 0, open_floor=0.618) == ("accept", 580_000)

def test_no_open_floor_keeps_old_opening():
    assert pricing.buyer_next_offer(1_000_000, 900_000, None, None, 0)[1] == 550_000

def test_cost_open_arm_share_and_independent_of_fanout():
    ids = range(20_000)
    picks = [buyer_r3.cost_open_arm(i) for i in ids]
    assert abs(sum(picks) / len(picks) - config.BUYER_COST_OPEN_SHARE) < 0.03
    both = sum(a and buyer_r3.fanout_arm(i) for i, a in zip(ids, picks))  # 기존 fan-out 판정 함수 이름에 맞출 것
    assert abs(both / len(picks) - config.BUYER_COST_OPEN_SHARE * config.BUYER_FANOUT_SHARE) < 0.03
```

- [ ] **Step 2:** `pytest tests/test_buyer_cost_open.py -v` → FAIL (`open_floor` 인자 없음)
- [ ] **Step 3:** `buyer_next_offer`에 `open_floor` 추가. 호출부에서 canary면 `open_floor=pricing.cost_ratio(c["kind"], c.get("grade"))`, 아니면 None
- [ ] **Step 4:** `pytest tests/test_buyer_cost_open.py tests/test_buyer_final.py tests/test_buyer_fanout.py tests/test_buyer_ev.py -v` → 모두 PASS
- [ ] **Step 5:** 커밋 `buyer: open at the estimated cost for a 50% canary (3.6 caps saved at 1 below cost, so lower openings only add turns)`
- [ ] **Step 6 (사람 확인 후):** buyer 재시작, 배포 노트 작성·커밋
- [ ] **Step 7 (+2h):** 팔별 주문당 거래, 평균 margin share, 평균 턴 수 비교. 판정: `cost_open`이 주문당 거래 ≥ base이고 margin share 차이 −0.02 이내면 `BUYER_COST_OPEN_SHARE = 1.0`, 아니면 0.0. 결과를 한 줄로 커밋

### Task 2: 고가(premium) bandit 1.1 팔 중지

v2 3(e). premium 재고가 쌓여 있고(goods_over_3d 28.6M) 3.6 구매자는 마진 점유율로 거르므로, 정가 대비 1.1 위로 부르는 팔은 결렬만 늘림.

**Files:**
- Modify: `r3lib/config.py:170` `PREMIUM_ASK_ARMS = (0.9, 1.0)`
- Test: `tests/test_shop_ask_bandit.py`에 추가

**Interfaces:**
- Consumes: `bandit.Bandit("ask_premium", config.PREMIUM_ASK_ARMS, ...)` (`shop_r3.py:198`)

- [ ] **Step 1: 테스트 작성**

```python
def test_premium_arms_have_no_raise():
    assert max(config.PREMIUM_ASK_ARMS) <= 1.0

def test_retired_arm_never_chosen(tmp_mem):   # 기존 테스트의 메모리 fixture 사용
    b = bandit.Bandit("ask_premium", (0.9, 1.0, 1.1), 1.0, mem=tmp_mem)
    for _ in range(30):
        b.update(("premium",), 1.1, 10.0)          # 1.1에 높은 보상 기록
    b2 = bandit.Bandit("ask_premium", config.PREMIUM_ASK_ARMS, 1.0, mem=tmp_mem)
    assert all(b2.choose(("premium",))[0] != 1.1 for _ in range(200))
```

- [ ] **Step 2:** 실행해 첫 테스트 FAIL 확인. 두 번째가 FAIL이면 `Bandit.choose`가 저장된 팔을 그대로 쓰는 것이므로 `arms`에 없는 팔을 거르도록 수정
- [ ] **Step 3:** 상수 변경, 필요하면 `r3lib/bandit.py` 수정
- [ ] **Step 4:** `pytest tests/test_shop_ask_bandit.py tests/test_bandit.py tests/test_premium_pilot.py -v` → PASS
- [ ] **Step 5:** 커밋. 사람 확인 후 shop 재시작, 배포 노트
- [ ] **Step 6 (+2h):** premium 판매 수·매출, `goods_over_3d` 변화를 배포 노트에 추가

### Task 3: 글 수 252 → 288/24h 원인 찾기 후 수정

v2 5. 간격(`REPORT_GAP_MIN 4.5`)은 이미 5분 미만이라 상한(288)을 막는 것은 다른 이유임. 원인을 먼저 숫자로 확인한 뒤 고친다.

**Files:**
- Read: `logs/r3/decisions.jsonl`, `forum_r3.py:360-` (`scheduled_report`)
- Modify: 원인에 따라 `forum_r3.py` 또는 `r3lib/config.py` (`REPORT_DEDUPE_H`, 실행 주기)
- Test: `tests/test_forum_order.py`에 추가

- [ ] **Step 1:** 최근 24h `scheduled_report` 결정을 `why`별로 집계 (scratchpad 스크립트, 로그는 읽기만). 기대 출력: `report gap / dislike pause / dedupe / no candidate / 429 / 실행 안 됨(빈 5분 칸)` 건수
- [ ] **Step 2:** 가장 큰 원인 하나만 고침. 예: dedupe라면 `REPORT_DEDUPE_H 6 → 5`(20% 이내), 빈 칸이 크면 forum 실행 주기를 5분 경계에 맞춤
- [ ] **Step 3:** 그 원인을 재현하는 테스트 하나 추가 → FAIL → 수정 → PASS (`pytest tests/test_forum_*.py -v`)
- [ ] **Step 4:** 커밋(원인 숫자를 메시지에 포함). 사람 확인 후 재시작
- [ ] **Step 5 (+6h):** 24h 환산 글 수와 dislike 비율. dislike 비율이 0.05를 넘으면 되돌림

### Task 4: 종료 직전 동작 리허설 (코드 변경 없음이 목표)

v2 6·5.6. 규칙은 이미 있으니, 실제 남은 시간 값에서 기대대로 움직이는지만 확인한다.

**Files:**
- Test: `tests/test_round_end_goods.py`에 추가

- [ ] **Step 1: 테스트 작성**

```python
@pytest.mark.parametrize("hl", [71, 35, 11])
def test_goods_floor_never_below_cost_near_end(hl):
    u = {"kind": "goods", "cost": 500_000, "list_price": 800_000, "supplied_at": 0}
    assert pricing.unit_floor(u, now=1, hours_left_round=hl) >= 500_000

def test_food_restock_closed_and_goods_open_at_60h(monkeypatch):
    # R3_END를 now+60h로 둔 dry-run: food 라인 0, goods 라인 허용
    ...  # restock_r3의 기존 dry-run 헬퍼로 plan을 만들고 lines의 kind만 확인
```

참고: slow SKU(48h 이상 보유) goods는 `cost × (1 − 0.01×남은 일수)`까지 내려감. 의도된 규칙(보관비보다 지금 파는 것이 낫다)이므로 첫 테스트는 `slow=False` 기준.

- [ ] **Step 2:** `pytest tests/test_round_end_goods.py tests/test_slow_floor.py -v`. 통과하면 커밋만 하고 끝. 실패하면 그 실패를 별도 커밋으로 남기고 고칠지 사람에게 물음
- [ ] **Step 3:** 11/16 23:59 KST(마감 24h 전)에 kpi.jsonl로 `expiring_6h` 원가와 food 재고를 확인. food 재고 원가가 1M을 넘으면 그때 처분 속도를 판단

### Task 5: 결과 정리

- [ ] v2 문서 끝에 "적용 결과" 절을 추가: 각 Task의 배포 시각, +2h 판정, 유지/되돌림. 커밋

---

## 순서와 기대 효과 [추정]

| 순서 | Task | 올라가는 항목 | 기대 효과(total) | 비고 |
|---|---|---|---|---|
| 1 | Task 1 구매자 첫 제안 | saved, deals | +0.005–0.015 | 판정 후 100% 또는 0% |
| 2 | Task 2 premium 1.1 중지 | revenue, book | +0.003–0.01 | 상수 하나 |
| 3 | Task 3 글 수 | posts | +0.003–0.008 | 원인 확인 먼저 |
| 4 | Task 4 리허설 | 손실 방지 | — | 코드 변경 없을 가능성 큼 |

v2의 큰 항목(1·2·4)은 이미 들어가 있어서, 남은 것만으로는 10위 컷까지 크게 움직이기 어렵다 [추론]. saved는 3.6 구매자 신규 거래가 0.83–0.92라 시간이 갈수록 누적값이 오르고(72h 뒤 약 0.76 [추정]), 이 계획은 그 위에 작은 몫을 더하는 것이다.
