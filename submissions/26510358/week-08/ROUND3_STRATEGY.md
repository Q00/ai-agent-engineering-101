# Round 3 전략과 구조

2026-11-03 작성. 규칙 출처: 시장 `/agent.md`의 Round 3 절, week-08 ECONOMY 표.

## 목표

중간평가 순위 = 가게 ALL + buyer ALL (각 지표 백분위).

- 가게 ALL = 0.5 순이익 + 0.3 매출 + 0.2 별점
- buyer ALL = 0.5 잉여 + 0.3 게시판 평점 + 0.2 거래 수
- 기본 조건: 가게, buyer 각각 정산 20건 이상

## 규칙에서 끌어낸 판단

| 사실 | 판단 |
|---|---|
| 보관비는 비식품만, 하루 원가 1% | 720 h 식품은 라운드(14일) 안에 상하지 않고 보관비도 없음 → 가장 안전한 재고 |
| 재고가 없으면 `shops.json` 후보에 안 들어감 | 근거 없는 카테고리에도 1개씩 깔아 둠(탐색 예산) |
| 주문은 기준가 0.5~1.5배 SKU만 허용 | 카테고리 `median_price`에 가까운 SKU가 가장 자주 허용됨 |
| 잘 팔리는 상품·buyer 취향은 비공개, ledger settle에 SKU가 남음 | ledger를 메모리에 쌓아 카테고리별 정산 속도를 추정 |
| 순이익 = 잔고 기준, 안 팔린 재고는 전부 손실 | 마지막 72 h 매입 중단, 36 h부터 원가 아래까지 단계적 할인 |
| buyer 가치 = 정가, 고장·기한 초과는 0 | buyer는 `accept`로 증명서·남은 시간·등급을 결제 전에 강제 |
| `claim_honest`가 ledger에 공개 | 가게는 항상 실제 등급을 주장(별점·평판 보호) |
| 보증 고장 시 가게가 전액 환불 | 시장 전체의 등급별 고장률로 기대 비용을 계산해 쌀 때만 보증 |
| 게시판 표는 다른 주인·정산 5건 이상 투표자만 셈 | 확인 가능한 사실(ledger refs)을 담은 buyer 리포트로 좋아요를 모음 |

## 구조

```
                 공개 시장 (ledger, /api/shops, forum)        키트 v4 (.market/*.json, 훅 실행)
                              │                                       │
                              ▼                                       ▼
   ┌──────────────────────── r3lib/memory.py (SQLite) ────────────────────────┐
   │ 1층 공개 사실: settles, breaks, 가게별 재고                               │
   │ 2층 내 기록: purchases(결제·고장 연결), negotiations                       │
   │ 3층 게시판: forum posts + LLM 분류 신호(약한 증거)                        │
   │ 뷰: category_demand · price_ratio · break_rate · shop_reputation          │
   └───────┬──────────────┬──────────────┬──────────────┬─────────────────────┘
           ▼              ▼              ▼              ▼
     restock_r3     shop_r3 (A2A)   buyer_r3        forum_r3 (10분마다)
     수요×몫×12h    시장가 기반 호가  기대 잉여×신뢰도  분류·투표·리포트
     + 탐색 예산    정직 등급·보증   accept 정책      (문장만 LLM)
           │              │              │              │
           └──────── r3lib/pricing.py: 돈에 관한 결정은 전부 결정적 함수 ────────┘
                         r3lib/llm.py: 말에 관한 일만 (실패 시 규칙으로 대체)
```

키트 기본형과 다른 점:

- 기본형은 무작위 매입, 원가 하한, 품질·보증 없음, LLM이 가격을 정함.
- 이 구조는 하나의 메모리가 가게, buyer, 게시판을 모두 먹이고, 돈은 코드가, 말은 LLM이 맡음.

## 모델, 컨텍스트, 툴

- **모델**: Codex CLI `gpt-6-luna`, reasoning low. 쓰는 곳은 세 군데뿐임.
  - 가게 답장에서 정규식이 가격을 못 찾을 때
  - 게시판 글 분류(구매 수요, 주입 시도)
  - 게시판 글 작성
  - 호출은 모두 `logs/r3/llm-calls.jsonl`에 남음.
- **컨텍스트**: LLM에는 필요한 사실만 JSON으로 넣고, 외부 문장은 DATA로 격리함. 판단 근거는 SQL 뷰에서 나옴.
- **툴/훅**: `restock`, `review`, `market_buyer`, `seller`, 게시판 outbox.

## 결정 로그 (제출 증거)

- `logs/r3/buyer-decisions.jsonl`: 주문별 후보 점수, 흥정 과정, 성사 또는 실패 이유
- `logs/r3/restock.jsonl`: 매입마다 수요 근거 또는 탐색 이유
- `logs/r3/forum.jsonl`: 올린 글과 표
- `logs/market-kit.log`: 키트 로그. seller의 `terms`·`deal` 이벤트, `[acp] settled` 줄이 여기에 찍힘.

## 운영 중 확인할 것

1. Round 3 개시 직후 첫 매입과 첫 정산 한 건씩 로그로 확인하기
2. settle 데이터의 실제 필드 이름(`shop`, `amount`)이 메모리 파서와 맞는지 확인하기
3. 가게 성사율(`seller_outcomes`)과 buyer의 `payments.jsonl` 거부 사유 비율 보기
4. 마감 72 h 전 매입 중단, 36 h 전 할인이 동작하는지 보기
