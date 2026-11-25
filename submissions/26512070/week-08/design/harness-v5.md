# 하네스 v5: 순위에 맞춘 기록, 분류, 판정 (2026-11-05 02:1x, 초안, 사용자 승인 대기)

상태: 0장은 적용했다. 1장 이후는 **설계만**이다.
HARNESS.md v4는 라운드 3 전에 쓴 설계 기록으로 남기고, 실제로 도는 모습과 이 개선은 이 문서가 기준이다.

## 0. 이미 적용한 것 (11/5 01:4x `a4ee1e9`, 02:0x `23f5ac5`)

| 적용 | 왜 | 어디 | 거래 영향 |
|---|---|---|---|
| 설치 뒤 25분 보류 | 전략은 30분 주기로 바꾼다(사용자 11/5 02:0x). 다만 슬라이스 중간에 사용자가 설치하면(v66 01:12, v68 01:42) 다음 슬라이스는 그 위에 설치하지 않는다. 처음에 2시간으로 넣었다가 사용자 의견으로 줄였다 | `outer/slice.py` `HOLD_S`, `held()` | 없음 |
| 사용자 메모 6시간 유효 | 01:00 브리핑이 11/4 02:2x 메모를 지금의 사실로 인용했다 | `outer/slice.py` `recent_notes()` | 없음 |
| 전체 순위표 이력 | 최종 점수는 11/17 23:59의 백분위다. 이웃의 속도가 필요하다 | `outer/board_history.py` (별도 프로세스, 10분) → `memory/market/board-full-<날짜>.jsonl.gz` | 없음 |
| **실시간 위치** | 항목마다 등수가 보여야 위치를 안다 (사용자) | `outer/position.py` → `logs/strategy/position.md`, `memory/facts/position.json`, 전략 브리핑 입력 | 없음 |
| **아침 보고** | 밤의 변경은 모아서 아침에 한 번에 (사용자) | `outer/morning.py` → `logs/strategy/morning-<날짜>.md`, 08:00 뒤 한 번 | 없음 |
| 점수 정의 | agent.md의 정의를 전략 프롬프트에 | `core/score.py` `text()` | 없음 |

`position.md` 예 (01:47): Players 14위/56, 가게 순이익 6/52, 매출 4/52, 별점 33/52, buyer 잉여 25/49, 표 23/49, 거래 수 28/49, 글 7/49.
각 줄에 바로 위 참가자와 차이, 한 칸 오르면 ALL이 얼마나 오르는지, 1시간·6시간 전 등수가 붙는다.

### 0.1 점수 정의가 하네스에 주는 것 (agent.md, 사용자 인용 11/5 02:0x)

| 정의 | 하네스가 할 일 |
|---|---|
| 순이익 = 잔액 − 시작 자본. 안 팔린 재고는 손실 | 재고 원가(01:38 44.6M)는 팔기 전까지 순이익에서 빠져 있다. **11/17 23:59에 남은 재고는 그대로 손실** → 마감 전 재고 줄이기 계획과, 재고 원가를 뺀/더한 두 순이익을 같이 보여 준다 |
| 매출 = 정산값 − 환불 | 환불(고장 107건)은 매출에서도 빠진다. 보증 판매의 효과는 환불을 뺀 값으로 판정 |
| 별점은 후기가 적으면 3 쪽으로 | 우리는 후기 2,110개라 거의 그대로. 별점 33위가 진짜 위치. 낮은 별점의 원인 분류가 필요 ([라]) |
| 잉여 = 주문을 채운 물건만 (버틴 식품, 안 고장난 물건) | 결제 안 된 동의는 잉여 0. 식품 결제 전환 30%가 그대로 잉여 손실 ([가] 표) |
| 최종 = Players 합계 (가게 ALL + buyer ALL) | 판정과 예측의 마지막 단위는 Players 등수 |

## 1. 문제: 기록은 많은데 판정이 안 된다

목적은 하나다: **11/17 23:59의 Players 백분위 (가게 ALL + 구매자 ALL).** 각 항목은 누적 합계의 백분위다.

지금 하네스는 기록은 충분하다(원본 이벤트에 `strategy_version`, `epoch`, `order`, `counterpart` 키가 다 있다).
모자란 것은 **기록에서 결론까지 가는 길**이다.

| # | 증상 (확인한 것) | 원인 |
|---|---|---|
| 1 | 가설 H2가 계속 "아직 판단 불가", 분기 e64의 판매 0건 | 판정 단위가 전략 버전(분기)인데 버전이 30분마다 바뀌었다 |
| 2 | 구매자 `buyer.closed`의 `outcome: completed` 280건이 실제로는 결제 거절 (6시간, 아래 표) | 결과가 이벤트마다 다른 말로 흩어져 있고 하나로 정한 곳이 없다 |
| 3 | 벤치마크에서 우리 가게가 "시간당 0건" | 가게 URL→이름 매핑에서 우리 터널 URL이 빠진 것으로 추정 (`outer/benchmark.py` `shop_of`) |
| 4 | 변경의 가설·수치·판정이 커밋 메시지, `user_notes.md`, `decisions.jsonl`, HARNESS, 메모리에 흩어짐 | 실험 장부가 없다 |
| 5 | 결정 21건이 전부 "감사 동의" | 감사의 과거 판단을 결과로 채점하지 않는다 |
| 6 | 전략 브리핑이 건강 지표 2,000자 + 벤치마크 2,500자 + 요약을 통째로 받는다 | 질문이 정해져 있지 않아서 다 넣는다 |

**같은 기록으로 바로 답이 나오는 예 (구매자, 최근 6시간, 주문 994건):**

| 주문 | 건수 | 결제 | 동의했는데 결제 안 됨 (유통기한) | 거절당함 | 맞는 가게 없음 |
|---|---|---|---|---|---|
| 식품 | 888 (89%) | 263 (30%) | 280 | 222 | 114 |
| 비식품 | 106 (11%) | 86 (81%) | 7 | 8 | 3 |

주문 종류는 바꿀 수 없다(숨은 선호 카테고리). 비식품은 이미 81%를 결제한다. 그래서 구매자 잉여(남은 여지 0.255, 가장 큼)를 올릴 곳은
**식품의 결제 전환 30%**이고, 그중 가장 큰 덩어리는 동의 후 유통기한으로 결제가 거절된 280건이다. 이 표는 지금 수작업으로 만들었다.
v5는 이런 표가 항상 만들어져 있고, 전략 에이전트와 사용자가 같은 표를 보게 한다.

## 2. 설계: 네 층

```
안쪽 (거래, 그대로)  ─ 원본 이벤트 memory/raw, 키트 .market, 시장 ledger ─┐
                                                                         ▼
[가] 거래 흐름표   analysis/build.py      → memory/analysis/trades.sqlite (주문 한 줄, 방문 한 줄, 설치 한 줄)
[나] 판정          analysis/experiments.py → logs/analysis/experiments.jsonl (변경마다 전후 비교와 판정)
[다] 순위 예측     analysis/forecast.py    → logs/analysis/forecast.json (11/17 예상 백분위, 항목별 한계 이득)
[라] 질문 묶음     analysis/questions.py   → logs/analysis/answers-<시각>.md/.json (점수 항목별 고정 질문의 답)
                                                                         │
바깥 전략 브리핑 (outer/slice.py)  ← 위 셋의 요약만 받는다 ─────────────────┘
```

원칙
- **분석층은 읽기만 한다.** 안쪽 코드(seller, buyer, restock, 키트)는 바꾸지 않는다. 원본은 그대로 두고, 해석은 분석층에서 한다.
- **결과 이름은 한 곳에서 정한다** (`analysis/outcomes.py`). 원본의 애매한 표시(`completed`인데 결제 거절)는 여기서 바로잡는다.
- **판정 단위는 시간 창이다**, 분기가 아니다. 버전은 그 창의 꼬리표다.
- **질문은 점수 항목에서 나온다.** 질문마다 어느 항목(가중치)을 위한 것인지 적는다.

### [가] 거래 흐름표 `memory/analysis/trades.sqlite`

`analysis/build.py`가 원본을 커서로 읽어 새 줄만 반영한다(지우면 처음부터 다시 만든다). 바깥 루프와 별개로 5분마다 또는 손으로 돌린다.

| 표 | 한 줄 | 주요 칸 |
|---|---|---|
| `buyer_orders` | 주문 하나 | ts, order, kind(food/goods), category, budget, use_within_h, sku, list_price, shops_tried, first_shop, agreed_price, paid(0/1), paid_price, surplus_est, **final**, final_reason, strategy_version, secs |
| `shop_visits` | 가게 방문(흥정) 하나 | ts, task, sku asked, kind, opening_ask, offers, **final**, price, cost, margin, buyer(사후 확인), stars, refund, strategy_version |
| `restock_windows` | 공급 창 하나 | ts, planned lines, bought units/spend, cash, cap, refused reason |
| `installs` | 전략 설치 하나 | ts, version, by(user/auto), **diff**(앞 버전과 다른 칸), proposal_id, option, expected, targets |
| `board` | 순위표 스냅샷의 한 행 | ts, side(shop/buyer/player), name, 각 점수 칸, all, ranked(0/1) |

`final` (하나만):
- 구매자: `paid`, `refused_shelf`, `refused_other`, `rejected_by_shop`, `no_shop`, `walked`, `error`
- 가게: `sold`, `left_at_open`, `left_after_offer`, `no_stock`, `refused_by_us`, `error`

### [나] 실험 장부 `logs/analysis/experiments.jsonl`

설치 한 건이 실험 한 건이다. `analysis/experiments.py`가 `installs`에서 만든다. 지난 v1~v67도 소급해 채운다.

| 칸 | 내용 |
|---|---|
| version, ts, by, diff | 무엇을 바꿨나 |
| hypothesis, targets | 브리핑 선택지의 `expected`, `targets` (사용자 설치면 커밋 메시지의 첫 줄) |
| metrics | diff가 건드린 영역에 따라 자동 선택. 예: `playbook.buyer.*` → 식품 결제율, 결제당 잉여 / `playbook.restock.*` → 재고 없음 비율, 시간당 판매, 마진 / `shop_card`·`seller` → 개장가에서 떠난 비율, 별점 |
| before / after | 설치 전 2시간 vs 설치 후 (다음 설치 또는 2시간까지). 같은 시각의 전날 값도 같이 (시간대 효과) |
| n, verdict | 표본 수, `better` / `worse` / `unclear` (표본 30 미만이거나 차이가 표준오차 2배 미만이면 unclear) |
| overlapped | 다른 설치와 창이 겹치면 표시 (v66·v67처럼) |

감사도 여기서 채점한다: 감사가 동의한 변경의 판정 분포 = 감사의 성적.

### [다] 순위 예측 `logs/analysis/forecast.json`

지금의 위치는 0장 `position.md`가 보여 준다. 예측은 그 위에 속도를 더한다. `board` 표에서 항목마다 우리와 순위 이웃(위아래 5명)의 최근 6시간 증가 속도를 구해 11/17 23:59 값을 선형으로 늘리고, 백분위를 다시 계산한다(`core/score.py` 규칙 그대로).
결과: 항목별 예상 백분위, 그리고 **"이 항목을 하루 X만큼 더 올리면 최종 ALL이 몇 오르나"**. 이 값이 전략의 우선순위가 된다.
이력이 쌓이기 전(첫 몇 시간)에는 `board-ours.jsonl`과 지금 스냅샷으로 대신하고 "추정"이라고 표시한다.

### [라] 질문 묶음 `analysis/questions.py`

점수 항목마다 고정 질문. 답은 [가] 표에 대한 SQL 한두 줄이고, 근거 행 수를 같이 낸다.

| 항목 (가중치) | 질문 |
|---|---|
| 구매자 잉여 (0.5) | 종류별 결제 전환과 실패 이유 / 결제당 잉여(가격대별) / 동의 후 결제 거절의 SKU·가게·등급 / 상위 구매자와 차이: 주문 구성인가 전환인가 |
| 구매자 표 (0.3), 글 (0.1) | 글당 표, 표 받은 글의 종류, 숨겨진 글 |
| 구매자 거래 수 (0.1) | 시간당 결제, 주문 대비 |
| 가게 순이익 (0.5) | SKU별 마진×판매, 손실(폐기·보관·환불), 재고 회전 |
| 가게 매출 (0.3) | 재고 없음으로 놓친 요청의 SKU와 금액, 개장가에서 떠난 비율 |
| 가게 별점 (0.2) | 3점 이하의 원인 분류 (식품 유통기한, 고장, 가격), 원인별 건수 |

### 바깥 브리핑과의 연결 (outer/slice.py, tools/record.py)

- 브리핑 입력: 건강 지표·벤치마크 전문 대신 **예측 요약 + 지난 변경의 판정 + 질문 답(바뀐 것만)**. 건강 지표는 경보로만 남긴다.
- `rec.brief` 선택지에 `metric`(장부의 지표 이름)과 `window_h`(판정 시간, 기본 2)를 **필수**로. 없으면 툴이 거절한다.
- 한 선택지가 바꾸는 영역은 하나(playbook의 역할 하나 또는 restock 하나)로 제한. 동시에 여러 곳을 바꾸면 판정이 겹친다.
- 감사 입력에 자기 과거 성적([나])을 넣는다.

### 문서와 읽는 사람

| 읽는 사람 | 무엇을 | 어디 |
|---|---|---|
| 사용자 (운영) | 지금 이상한 것 | `logs/strategy/URGENT.md` (그대로) |
| 사용자·전략 에이전트 (분석) | 예측, 판정, 질문 답 | `logs/analysis/` (커밋한다) |
| 채점·REPORT | 시도와 폐기, 근거 | `logs/analysis/experiments.jsonl` → `REPORT.md` 3장 표 |
| 설계 | 실제로 도는 모습 | 이 문서 → 마지막에 `HARNESS.md` v5 (짧게) |

`user_notes.md` 형식: `# YYYY-MM-DD HH:MM — 질문` (6시간 지나면 브리핑에서 빠진다, 0장).

## 3. 고치는 범위와 순서

| 순서 | 무엇 | 파일 | 누가 읽나 / 반영 | 거래 중단 |
|---|---|---|---|---|
| 1 | 결과 이름, 흐름표 구축 | `analysis/outcomes.py`, `analysis/build.py`, 테스트 | 새 코드 | 필요 없음 |
| 2 | 실험 장부 + v1~v67 소급 | `analysis/experiments.py`, 테스트 | 새 코드 | 필요 없음 |
| 3 | 순위 예측 | `analysis/forecast.py`, 테스트 | 새 코드 | 필요 없음 |
| 4 | 질문 묶음 | `analysis/questions.py`, 테스트 | 새 코드 | 필요 없음 |
| 5 | 브리핑 입력 교체, 선택지 필수 칸, 한 영역 제한, 감사 성적 | `outer/slice.py`, `tools/record.py`, `outer/audit.py` | slice가 매번 새로 뜨므로 다음 slice부터 | 필요 없음 |
| 6 | 벤치마크 우리 줄 수정, 분석 주기를 바깥 루프에 | `outer/benchmark.py`, `outer.py` | 바깥 루프가 import → **바깥 재시작 1번** | 필요 없음 (바깥만) |
| 7 | HARNESS v5, REPORT 뼈대 | 문서 | | |

**판매·구매를 멈출 필요가 없다.** 이 설계는 안쪽 코드를 바꾸지 않는다. 바깥 재시작(6번)은 거래에 영향이 없다
(재시작 방법: `outer.py` 종료 → `ops\outer.ps1`, 메모리 [[r3-live-handoff]]). 그래도 멈추기를 원하면 5·6번을 반영하는 동안만
멈추면 된다 (키트 watchdog가 seller를 되살리므로 키트째 멈춰야 한다).

안쪽을 바꿔야 하는 일(식품 결제 전환 올리기: 유통기한 보호 v66의 효과, 가게의 식품 보존 시간 맞추기)은 하네스가 아니라 **전략**이고,
v5가 그 효과를 판정한 뒤 별도로 정한다.

## 4. 확인 방법

- 단위 테스트: 각 모듈에 작은 가짜 원본으로. 전체 `py -m unittest discover -s tests`.
- 흐름표 검산: `buyer_orders`의 6시간 결제 수 = `payments.jsonl`의 결제 수, `shop_visits`의 판매 = ledger의 우리 정산 수.
- 예측 검산: 지금 스냅샷으로 다시 계산한 백분위 = 보드의 `all` (기존 `core/score.py` 검산과 같은 방식).
- 소급 장부: v66·v67이 `overlapped`로 나오는지.

## 5. 정할 것

1. 이 설계대로 갈지 (특히 [라]의 질문 목록에 더하거나 뺄 것).
2. 판정 창: 전략이 30분마다 바뀌므로, 판정은 변경 하나가 산 시간(30분~)으로 하고 표본이 30 미만이면 "판단 불가". 같은 영역을 연달아 바꾼 것은 묶어서 판정할지.
3. 선택지가 바꾸는 영역을 하나로 제한하는 것 (전략 에이전트의 자유를 줄인다).
4. 거래를 멈추고 할지, 멈추지 않고 할지.
