# Week 08 (중간고사): Round 3 시장의 내 에이전트 `usagi`

학번 26510120 · 가게·buyer 이름 `usagi` · owner `sol0411`
Round 3: 2026-11-03 ~ 11-17 23:59 KST. 내 에이전트는 **11-06 13:04부터** 참여했다(10-21 23:38 키트가 `httpx.ConnectTimeout`으로 꺼진 뒤 다시 켜지 않았다).

구조를 한 줄로 말하면 이렇다. 모델(haiku)은 약 20분에 한 번 정책 숫자(`policy.json`)만 정한다. 주문마다 하는 일(가게 고르기, 흥정, 재고 사기, 별점)은 전부 코드가 그 숫자대로 처리한다.

---

## 1. 부품별 설계

### 1.1 모델: 어디에 쓰고 어디에 안 쓰는지

| 부품 | 파일 | 모델 호출 |
|---|---|---|
| 전략가 | `strategist.py` | **haiku 1회 / 약 20분.** `restock.py`가 `policy.json`이 20분 넘게 묵었으면 띄운다(`restock.py:34`) |
| 가게 흥정 | `seller_r3.py` | 없음 (코드가 가격을 정함) |
| buyer 고르기·흥정 | `market_buyer.py` | 없음 |
| 재고 사기 (restock 훅) | `restock.py` | 없음 |
| 별점·게시판 (review 훅) | `review.py` | 없음 |

- 모델 명령은 `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence`이다(`harness.py:18`). 빈 임시 폴더에서 돌리기 때문에 저장소의 CLAUDE.md를 읽지 않고, 세션도 남기지 않는다.
- Round 3 동안 전략가는 **788회** 불렸고, 답이 없거나 JSON이 깨진 경우는 **0회**였다.
- 이렇게 나눈 이유(11-06 설계 결정): 그때 상위 가게는 거래가 1만 건대였다. 그런데 흥정 한 수마다 `claude -p`를 부르면 한 번에 약 7.5초가 걸려서, 속도와 사용량 둘 다 맞출 수 없었다.

### 1.2 컨텍스트와 메모리

**전략가가 읽는 한 쪽.** 모델에게 원본 파일을 주지 않는다. 코드가 아래 항목을 JSON 한 덩어리로 만든다(`strategist.py` `context()`). Round 3 동안 프롬프트 길이 중앙값은 13,010자, 범위는 9,154–16,669자였다.

| 항목 | 내용 | 출처 |
|---|---|---|
| `market_2h` | 최근 2시간 정산 매출 상위 SKU·카테고리, 등급별 고장, 내 점유율 | 장부 (`market_tool.summary`) |
| `shop` | 잔고, 재고 수·원가, 유통기한 6시간 안 식품, 최근 2시간 판 값/정가·마진, 환불·폐기 | `.market/inventory.json`, `logs/seller_r3.jsonl`, 장부 |
| `buyer` | 최근 2시간 시도 결과, 최근 200건 결제·거절 사유, 깎은 비율, 고장 신고 | `buyer_memory.jsonl`, `.market/payments.jsonl`, `state/purchases.jsonl`, `.market/broken.jsonl` |
| `board` | 내 가게·buyer 항목 옆에 학생 중앙값 | `GET /api/board` |
| `forum_top` | 점수 높은 글 5개 | `.market/forum.json` |
| 지난 결정 | 지금 정책 + 지난 3번의 결정과 이유 | `policy.json`, `logs/strategist.jsonl` |

**모델의 답을 그대로 쓰지 않는다.** 모델은 `{"changes": {...}, "reason": "..."}`로 답한다. 코드는 바꿀 수 있는 키 19개를 `RANGES` 안으로 잘라 넣고, 모르는 키는 버린다(`strategist.py` `apply()`·`clamp()`). Round 3 동안 일어난 일은 이렇다.
- 바꾸자는 답: 788회 중 779회.
- 범위 밖이라 잘린 값: 1,129번. 가장 많은 건 `share_guess` 668번이다. 모델은 669번 중 668번을 하한 0.005보다 낮게 불렀다(중앙값 0.001).
- 모르는 키(`max_lines`)라 버린 것: 12번.
- 매 회차의 컨텍스트, 원래 답, 실제 적용값은 전부 `logs/strategist.jsonl`에 남는다.

**기억 파일** (누가 쓰고 누가 읽는지)

| 기억 | 파일 | 쓰는 곳 | 읽는 곳 |
|---|---|---|---|
| 시장 장부 6시간 | `state/ledger.jsonl` (+커서) | `market_tool.sync()` | restock, seller_r3, market_buyer, strategist |
| SKU별 시세 | `state/sku_stats.json` | restock | seller_r3 |
| 정책 | `policy.json` | strategist | restock, seller_r3, market_buyer |
| 지난 결정 | `logs/strategist.jsonl` | strategist | strategist (마지막 3개) |
| buyer 시도 | `buyer_memory.jsonl` | market_buyer | market_buyer (가게별 결제 확률), strategist |
| 결제·고장 | `.market/payments.jsonl`, `.market/broken.jsonl` | 키트 | market_buyer, strategist |
| 산 물건 | `state/purchases.jsonl` | review | strategist |
| 판 물건 | `logs/seller_r3.jsonl` | seller_r3 | strategist |

`state/`와 `.market/`은 `.gitignore`에 들어 있어서 제출물에 없다(키 파일도 같은 이유로 빠짐).

### 1.3 툴과 훅

- **`market_tool.py` (툴).** 시장 공개 장부 `GET /api/ledger?after=N`을 쪽 단위로 받아 6시간치를 디스크에 둔다(`KEEP_H = 6`). 이걸 SKU별 정산 수, 매출, 값/정가 중앙값으로 바꾼다.
  - 이 컴퓨터 시계가 시장보다 몇 분 느리다(NTP 없음). 그래서 `market_now()`는 장부 마지막 항목 시각으로 시장 시계를 맞추고, 공급창과 유통기한은 그 시계로 판단한다.
  - `policy()`는 `DEFAULT_POLICY` 위에 `policy.json`을 덮어쓴다. 그래서 전략가가 한 번도 안 돌았어도 실행기들은 숫자를 다 받는다.
- **`restock.py` (restock 훅, 키트가 60초마다 실행).** 공급창이 열렸을 때만 산다.
  - 고르는 법: 최근 `lookback_h` 동안 정산 매출 순으로 줄을 세운다. 판매가 적은 SKU, 정가가 `max_unit_list`를 넘는 SKU, 공급가가 `max_surge`를 넘게 오른 SKU, 유통기한이 짧은 식품은 뺀다.
  - 쓰는 돈: 창 하나에 `window_spend_frac × (잔고 − cash_reserve)`까지.
  - Round 3 동안 3,082번 돌았고, 그중 550번 실제로 사서 모두 939개를 샀다.
- **`seller_r3.py` (가게).**
  - 가격: 첫 호가 = 장부 중앙값/정가 + `ask_premium`. 최저가 = 중앙값 − `floor_below_anchor`이되 `원가 × (1 + min_margin)` 밑으로는 안 내린다. 단, 날짜가 `clearance_h` 안으로 다가온 식품은 원가 밑으로도 판다.
  - 흥정: buyer가 값을 부를 때마다 호가를 남은 거리의 40%만큼 내린다.
  - 영수증: 지금 등급을 그대로 적는다(곧 등급이 떨어질 식품은 등급을 안 적음). 상품에는 정책의 `warranty_h`만큼 보증을 붙인다.
  - 흥정 중 잡아 둔 unit은 300초 동안 말이 없으면 풀어 준다(`HOLD_S`, 11-11 수정 `051f97c`, 3.4절).
- **`market_buyer.py` (buyer).**
  - 고르기: 주문에 쓸 수 있는 (가게, SKU)마다 점수를 매긴다. 점수 = 그 가게와의 내 기록에서 나온 결제 확률 × (1 − 시세/정가).
  - 흥정: 정가의 `buyer_open`배에서 시작해 `min(예산, buyer_max × 정가)`까지 올린다.
  - 받는 조건(accept): 식품은 유통기한이 `use_within_hours + buyer_hours_margin` 이상 남아야 한다. 상품은 `buyer_grades_goods`(A·B) 등급만 받는다. 키트는 결제 전에 이 조건으로 checkout을 검사한다.
- **`review.py` (review 훅).**
  - 별점은 규칙으로 매긴다: 등급 주장이 거짓이면 2점, 시세 이하로 샀으면 5점, 그 밖에는 4점. 리뷰에는 사실만 적는다.
  - 게시판에는 5분에 1개씩 `[구매 기록]` 글을 올리고, 장부 번호를 refs에 단다.
  - 남의 검증된 글에 좋아요를 최대 3개까지 누른다. 나빠요는 보내지 않는다.

---

## 2. 아키텍처 figure와 키트 기본형과 다른 점

그림은 `architecture.tex`에 있고, `architecture.pdf`로 빌드했다(tectonic 0.17). 위쪽은 **느린 고리**, 아래쪽은 **빠른 고리**다.
- 느린 고리: 코드가 만든 컨텍스트 → `strategist.py`(haiku) → `RANGES`로 자르기 → `policy.json`. 약 20분에 한 번 돈다.
- 빠른 고리: restock, seller_r3, market_buyer, review. 모델을 부르지 않는다.

**키트 기본형과 나란히 놓은 사실표** (키트 코드: `~/agent-market/market-kit/market_kit.py`, `starter_round3/seller.py`)

| | 키트 기본형 | 내 것 |
|---|---|---|
| 모델 호출 | 가게 흥정 한 수마다 하네스 1회(`starter_round3/seller.py`: "The harness decides every move") | 전략가가 약 20분에 1회. 흥정은 코드 |
| 재고 | `default_restock`: 재고가 20개 밑이면 수요 카테고리 3개를 무작위로 골라 각각 무작위 SKU를 최대 2개, SKU당 잔고 1/10 안에서 ("It knows nothing about what sells") | 장부 매출 순위, 정책 숫자로 거르기, 창마다 쓸 돈 상한 |
| 가게 품질 표시 | 유통기한이 가장 빠른 unit을 보내고, 품질은 말하지 않고, 보증 없음 | 지금 등급을 그대로 적음, 상품에 보증 시간 붙임 |
| buyer | `market_buyer`가 없으면 재고 있는 가게 중 무작위 하나를 골라 `buyer` 명령(week-06 buyer)으로 흥정 | 기록 기반 결제 확률 × 시세 할인으로 점수, 받는 조건 검사 |
| 리뷰·게시판 | review 훅이 없으면 buyer가 낸 별점 | 규칙 별점, 구매 기록 글, 좋아요 |
| 기억 | 키트가 `.market/`에 쓰는 파일 | 위 + 장부 6시간, 정책, 결정 기록, 시도 기록 (1.2 표) |

**그림이 보여 주는 것.**
- 주황색이 모델을 부르는 곳이다. 내 구조에서는 위쪽 `strategist.py` 한 곳뿐이다. 이 전략가는 약 20분에 한 번 정책 숫자 19개만 정하고, 주문은 직접 처리하지 않는다.
- 아래쪽 파란 상자 넷은 각자 읽는 정책 숫자 이름을 달고 있다(restock 9 + seller 6 + buyer 4, review 0). 그 숫자대로 주문마다 코드가 처리한다.
- 오른쪽 회색 상자는 키트 기본형이다. 기본형에서는 흥정 한 수마다 모델이 정한다(주황 글씨).

**왜 이 구조인가 (내 판단): 사용량.**
- buyer 시도만 120,787번이었다(3.3절). 시도마다 모델을 한 번만 불러도 12만 번이 넘는다. 흥정은 한 시도에 여러 수가 오가니 실제로는 더 많았을 것이다.
- 이 구조에서는 Round 3 동안 전략가가 788번 불렸다.

**이 구조의 약점: 모델이 빠른 사고를 못 본다.**
- 전략가는 20분에 한 번, 코드가 만든 요약만 본다.
- 잠금 버그 기간(11-06 ~ 11-11)에 판매가 줄자, 전략가는 원인(잡힌 재고)을 찾지 못하고 재고 사는 돈만 줄였다.
  - `window_spend_frac` 0.5 → 0.3 → 0.15: 11-07 20:15 ~ 21:39
  - 0.1: 11-08 09:28
  - `cash_reserve` 3,000,000: 11-10 12:12 (이 뒤로 버그를 고친 다음인 11-11 02:20까지 재고를 한 번도 사지 않았다, `logs/restock.jsonl`)
- 이유 문장도 매번 "made 0 deals in 2 hours ... so more restock buying only adds storage cost" 같은 식이었다. 잠긴 재고 이야기는 한 번도 없었다(`logs/strategist.jsonl`).
- 컨텍스트(1.2절 표)에는 가게가 "재고 없음"이라고 답한 횟수가 들어 있지 않다. 버그는 내가 직접 가게에 물어봐서 찾았다(3.4절 3번).

---

## 3. Round 3 결과

### 3.1 순위 (Players 표, Class 보기)

| 받은 시각 | 학생 순위 | 학생 백분위 | 가게 ALL | buyer ALL | student_total |
|---|---|---|---|---|---|
| 11-18 02:22 (마감 2시간 23분 뒤) | **19 / 46** | .600 | .491 | .881 | 1.217 |
| 11-23 11:55 (제출일) | 20 / 46 | .578 | .491 | .881 | 1.217 |

- 내 점수는 두 시점에서 같다. 그런데 그사이 다른 학생 9명의 점수가 ±.011 움직여서, 바로 위였던 `dyishappy`가 1.212에서 1.224로 올라 나를 넘었다.
- 시장 전체 거래 합계(`totals.deals` 1,008,510)는 두 시점에서 같다. 즉 거래가 더 생긴 게 아니다. 점수가 움직인 건 가게 항목 `profit`·`book`·`stock_units` 때문이다. 시장이 멈춘 뒤에도 보관료와 유통기한 폐기로 이 값들이 계속 줄었고(내 `book`도 55,272,025 → 53,978,685), 그래서 다른 가게들의 백분위가 바뀌었다.
- 공지상 최종 순위는 종료 시각의 board로 정한다.

**week-10 LAB 기준 숫자:** 기록하지 않았다. week-10 LAB에서 기준 숫자를 적어 두지 않았고, 저장소에도 기록이 없다. 그래서 위 결과를 기준 숫자와 나란히 놓지 못한다.

11-18 02:22 board의 내 줄:
- 가게: deals 860 · 매출 698,662,471 · profit 30,647,285 · book 55,272,025 · stars 4.48 (`stars_rank` 4.33) · 정직 주장 860/860 · 환불 36 · 고장 58 · 재고 45개(원가 24,624,740) · 파산 0
- buyer: orders 42,782 · deals 23,070 · served 22,889 · saved .8107 · forum .936 · posts 2,932
- 학생 중앙값: 가게 .53 · buyer .6525 · total 1.128

### 3.2 정산 수

- **board 기준:** 가게 정산 860건, buyer 정산 23,070건. 기본조건 20+20을 넘는다.
- **로그 범위:** 키트는 로그를 20MB에서 한 번만 돌려 쓴다(`market_kit.py:89` `LOG_MAX = 20_000_000`, 이전 것은 `market-kit.log.1`). 그래서 남아 있는 `logs/market-kit.log.1` + `logs/market-kit.log`는 **11-14 18:51:18부터**다. 11-06 ~ 11-14 오후 줄은 덮여서 없다. 남은 구간에서 가게 정산 줄(`[acp] settled`)은 317개, buyer 결제 줄(`[acp] paid`)은 6,909개다.
- 가게 쪽 자체 기록 `logs/seller_r3.jsonl`은 11-06 13:36부터 11-17 23:50까지 전부 있다. 영수증 3,634장(등급 C 2,734 · A 674 · B 226, 영수증 등급 = 실제 등급 3,634/3,634, 보증 붙은 것 897). 영수증은 결제 전에 나가므로 정산 수보다 많다.

마감 직전 로그 (`logs/market-kit.log`, 이 컴퓨터 시계):
```
23:36:19 [acp] settled 에비뉴엘 4인 슈렁큰 소가죽 소파+스툴 at 1508282, ledger #7892098, unit u-2c6f51d4e7c9 grade A
23:37:03 [acp] settled 에비뉴엘 4인 슈렁큰 소가죽 소파+스툴 at 1443285, ledger #7892371, unit u-6bbca4c5091b grade A
23:50:15 [acp] paid 17722 through https://ax-week07.treasuresky.xyz/acp, settled at ledger #7896119: 국내생산 데님 맞춤 암막커튼 거실형 세로 181~250cm까지, grade B
23:50:43 [market] order failed: 503 {"error":"Round 3 ended at 2026-11-17 23:59 KST. ...
```
(이 컴퓨터 시계는 시장보다 약 8분 30초 느리다. 23:50:43 = 시장 23:59.)

### 3.3 거부 사유

**buyer가 결제하지 않은 이유** (`.market/payments.jsonl`, 결제 단계까지 간 29,510줄 중 결제 안 함 6,442줄)

| 사유 | 건수 |
|---|---|
| 유통기한이 정책보다 짧음 (`not paying: N h of shelf life left, the policy asks for N`) | 5,044 |
| checkout 거절 409: 그 unit이 이 주문에 대해 재고 없음 | 673 |
| 상품 등급 C (`not paying: grade C, the policy accepts ['A', 'B']`) | 360 |
| 유통기한을 보여 줄 인증서 없음 | 303 |
| 결제 미완료 (시장 4xx/5xx) | 45 |
| 그 밖 (5xx, ACP 주소 오류, 시간초과) | 17 |

로그 인용:
```
20:35:15 [acp] not paying: 96.87 h of shelf life left, the policy asks for 138.0
21:32:02 [acp] checkout refused: 409 {"error":"unit u-c7dd00a83017 is not in stock for this order"}
10:31:14 [acp] payment not completed: 402 {"error":"payment refused by the market","market":{"error":"Internal Server Error"}}
```

**buyer 시도 결과** (`buyer_memory.jsonl`, 11-06 13:0x ~ 마감, 시도 120,787번 / 주문 42,371건; 한 주문에 가게를 여러 곳 시도함)
- `rejected` 49,192
- `skipped` 35,544: 첫 답에 보인 유통기한 부족 34,371, 등급 C 1,173
- `deal` 29,998
- `walked` 4,977: 가게 호가가 내 상한보다 높음 4,915

### 3.4 Round 3 동안 일어난 일 (사실만, 시간순)

1. **10-21 23:38 ~ 11-06 13:04: 꺼져 있었다.** 키트가 `httpx.ConnectTimeout`으로 죽은 채였다. 그래서 Round 3 첫 3일 동안 거래가 0이었다.
2. **11-06 13:10 ~ 13:34: 받는 조건이 없던 24분.** 이 구간에 buyer가 C등급과 유통기한 짧은 식품을 받아 `−1` 거래가 17건, 정가 초과가 1건 생겼다. buyer saved는 `(deals + 100)`으로 나누기 때문에 이 손해는 끝까지 남는다. 13:34 `7e0a823`부터는 받는 조건을 검사한다.
3. **11-11 01:2x: 가게 잠금 누수 버그.** buyer가 말없이 떠나면 흥정 중 잡아 둔 unit이 풀리지 않았다. 직접 물어보니 재고 18개 중 16개가 "none of that in stock"으로 답했다. 장부에도 실제로 재고에 있는 SKU에 대해 `usagi had no stock of ...`가 찍혀 있었다. 01:59 `051f97c`로 고치고 seller만 재시작한 뒤 18/18이 열렸다. 그 뒤 1시간 동안 정산 10건이 생겼고 가게 ALL이 .477 → .522로 올랐다.
4. **11-12: 별점.** 단순 평균 별점은 4.43 → 4.50으로 올랐다. 그런데 `stars_rank`는 4.40 → 4.26으로 내려갔고, 별점 백분위가 .773 → .227로 떨어졌다. 찾아보니 한 계정(q00)이 영수증 `item`에 SKU 코드가 들어 있으면 1점을 준다. 내 영수증은 `"item": u["sku"]`(`seller_r3.py:185`)라 여기에 걸린다. **고치지 않았다.**
5. **11-17 23:59 이후:** 시장이 503으로 멈춘 뒤에도 키트가 계속 돌았다. 그래서 전략가가 **378회 더 불렸다**(11-17 23:57 ~ 11-23 11:47). 11-23 11:54에 키트, seller, 터널을 껐다.

### 3.5 해석 (내 판단)

**가게가 학생 중앙값보다 낮은 이유 (.336 vs .53): 팔 수 있던 시간을 잃었다.**
- 가게 점수는 book 0.5 + 매출 0.3 + 별점 0.2다(week-09 강의). book과 매출은 시간이 지나야 쌓인다.
- 그런데 Round 3 처음 3일 넘게 키트가 꺼져 있었다(3.4절 1번). 또 11-06 ~ 11-11 약 4일 반 동안은 잠금 버그 때문에 재고 18개 중 16개를 팔 수 없었다(3.4절 3번).
- 근거: 버그를 고친 뒤 1시간 만에 정산 10건이 생겼고, 가게 ALL이 .477 → .522로 올랐다.
- 별점 1점 문제(3.4절 4번)와 C등급 재고도 영향을 줬을 것이다. 하지만 셋 중 무엇이 가장 컸는지는 따로 실험해서 재지 않았다.

**buyer가 높은 이유 (.881 vs .6525): 받는 조건으로 나쁜 물건을 걸렀다.**
- 조건이 없던 11-06 13:10 ~ 13:34에는 C등급이나 유통기한 짧은 식품을 받아서 −1 거래가 17건 났다(3.4절 2번).
- 조건을 붙인 뒤에는 결제 단계에서 유통기한 부족 5,044건, C등급 360건에 돈을 내지 않았다. 그 앞 단계에서도 가게의 첫 답만 보고 35,544번 걸렀다(3.3절).

**다음 라운드에 바꿀 것: C등급을 덜 사거나, 처음부터 더 싸게 판다.**
- 영수증 3,634장 중 2,734장(75%)이 C등급이었다. 11-11에 쟀을 때 A·B는 거의 다 결제됐지만 C는 약 3%만 결제됐다.
- 입고 등급은 무작위라 A·B만 골라 살 수는 없다. 그래서 먼저 SKU별 C등급 비율을 세고, C가 많이 나오는 물건은 덜 산다. C는 처음부터 더 낮은 값에 내놓는다.

---

## 부록: 숫자를 어떻게 셌는지

| 숫자 | 계산법 |
|---|---|
| 전략가 788회 / 실패 0 / 바꾸자는 답 779 | `logs/strategist.jsonl`에서 `time ≤ 2026-11-17T23:50:30`(시장 23:59를 이 컴퓨터 시계로 바꾼 값)인 줄. 실패 = `applied`가 null. 바꾸자는 답 = `applied`가 비지 않음 |
| 잘린 값 1,129 / 버린 키 12 | 같은 줄들에서 `raw`의 `changes`와 `applied`를 키별로 비교. 숫자가 다르면 잘림, `applied`에 없으면 버림 |
| 프롬프트 13,010자 | 같은 줄들의 `prompt_chars` 중앙값 (최소 9,154, 최대 16,669) |
| 마감 뒤 378회 | `strategist.jsonl` 전체 1,166줄 − 788 |
| restock 3,082 / 550 / 939개 | `logs/restock.jsonl`(시장 시계)에서 `time ≤ 2026-11-17T23:59:00`. 산 횟수 = `buy`가 비지 않은 줄, 개수 = `buy[].qty` 합 |
| 영수증 3,634 | `logs/seller_r3.jsonl` 줄 수. 등급 = `grade`, 정직 = `claim == grade`, 보증 = `warranty > 0` |
| 정산 줄 317 / 결제 줄 6,909 | `market-kit.log.1`과 `market-kit.log`에서 `[acp] settled`, `[acp] paid`를 셈 |
| 로그 시작 11-14 18:51:18 | `market-kit.log.1` 첫 줄 시각을 `buyer_memory.jsonl`의 같은 시각 항목(2026-11-14T18:51:17–19)과 맞춤 |
| 결제 거절 6,442 | `.market/payments.jsonl`에서 `paid`가 false인 줄을 `reason` 문구로 묶음 |
| buyer 시도 120,787 | `buyer_memory.jsonl`에서 `2026-11-06T13:0x ≤ time ≤ 2026-11-17T23:50:30`. `result`의 첫 단어로 묶음. 주문 수 = 서로 다른 `order` |
| board 숫자 | `GET https://market.ourolabs.ai/api/board`를 11-18 02:22와 11-23 11:55에 받음. 학생 순위 = `student: true`인 46명을 `student_total`로 정렬 |
