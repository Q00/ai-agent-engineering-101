# Round 3 전체 흐름 시퀀스 다이어그램 (2026-11-11 기준)

우리 시스템이 시장에서 **물건을 사 오고(매입) → 팔고(판매) → 다른 가게에서 사고(구매) → 결과로 배우는(학습)** 한 바퀴를
한 장으로 그린 것임. 에이전트별 세부 호출은 생략하고 덩어리 단위로만 그림. 2026-11-05 판(에이전트별 6장)은 git 이력에 있음.

- 점수: 가게 = 0.5 book + 0.3 매출 + 0.2 별점, 구매자 = 0.5 saved + 0.3 포럼 + 0.1 거래 수 + 0.1 글 수
- book = 현금 + 안 팔린 재고(원가). 그래서 재고는 "안 팔려도 원가만큼은 점수"이고, 손해는 하루 1% 보관료와 만료뿐임
- 모든 돈 결정(가격, 하한, 매입 수량)은 코드 규칙이 함. LLM·Decision API는 포럼 판단과 가격 문장 읽기에만 씀

```mermaid
sequenceDiagram
  autonumber
  box rgb(235,242,255) 시장
    participant M as 시장 서버<br/>(원장·공급·보드)
    participant O as 다른 학생들<br/>(구매자·가게)
  end
  box rgb(240,250,240) 우리 시스템 (서버 한 대)
    participant K as 키트<br/>(터널·결제 ACP)
    participant S as 우리 가게<br/>(shop_r3)
    participant B as 우리 구매자<br/>(buyer_r3)
    participant G as 운영 두뇌<br/>(restock 훅 + 내부 조직)
    participant D as 기억<br/>(memory.sqlite)
  end

  rect rgb(255,248,230)
  Note over M,D: ① 매입 — 60초마다 점검, 공급 창구는 15~20분마다 3분만 열림
  K->>M: 재고·수요·포럼 조회 (10초마다)
  K->>G: 재입고 훅 실행 (60초마다)
  G->>M: 원장 새 항목 가져오기
  G->>D: 원장 저장 (모든 가게의 거래·공급·만료)
  G->>G: 선제 행동 (10분마다, 주문이 없어도)<br/>과잉 재고 할인 · 묵은 등급 먼저 보여주기<br/>p > C/(B+C) 일 때만 실행
  G->>D: 할인·노출 결정 저장 (가게가 읽음)
  alt 공급 창구 열림
    G->>D: 카테고리 수요 · 우리 실적(판매·만료) · 재고 예산
    G-->>K: 살 목록 (최대 10줄, 실적이 나쁜 카테고리는 줄임)
    K->>M: 공급 요청
    M-->>K: 재고 입고 (등급은 시장이 무작위: goods A70 B20 C10)
  else 닫힘
    G-->>K: 이번엔 안 삼
  end
  end

  rect rgb(235,248,255)
  Note over O,D: ② 판매 — 다른 학생의 구매자가 우리 가게에 옴
  O->>K: 견적 요청 (A2A, 터널 주소)
  K->>S: 전달
  S->>D: 보여줄 단위 고르기 (밀어주는 등급 우선)<br/>시장 시세 · 원가 하한 · 고장률
  S->>S: 첫 제시가 = 규칙 가격 × bandit 선택 (C는 A 시세 기준)<br/>하한은 절대 안 내림
  S-->>O: 제시가
  loop 흥정 (최대 몇 차례)
    O->>S: 제안가
    S-->>O: 수락 또는 역제안
  end
  S-->>O: 서명된 영수증 (등급 정직 표시, 보증)
  O->>K: 결제 (우리 ACP)
  K->>M: 정산 → 원장 기록
  S->>D: 협상 결과 + bandit 보상 (이 판매가 보드 점수를 얼마나 올렸나)
  end

  rect rgb(245,240,255)
  Note over M,D: ③ 구매 — 시장이 우리 구매자에게 주문을 줌 (상위권 약 22초에 1건)
  M-->>K: 주문 (품목 후보, 예산)
  K->>B: 구매자 실행
  B->>D: 후보 점수 (등급별 고장률 · 보증 · 기대 할인율)<br/>saved를 떨어뜨리지 않는 기준선 이상만
  loop 점수 높은 가게 최대 3곳
    B->>O: 흥정 (A2A)
    O-->>B: 가격 · 영수증
  end
  B->>D: 판단 기록 (살지·기다릴지·말지 이유)
  B-->>K: 고른 영수증
  K->>O: 결제 (상대 가게 ACP)
  O->>M: 정산
  K->>M: 결과 제출 (별점 · 사실만 쓴 리뷰)
  end

  rect rgb(250,240,245)
  Note over M,D: ④ 포럼 — 10분마다
  G->>D: 새 글 저장
  G->>G: 글 분류 (Decision API: 조작인가 · 유용한가)<br/>근거가 맞는 글만 추천, 조작 글은 비추천
  G-->>K: 우리 글 · 투표 (outbox)
  K->>M: 게시
  end

  rect rgb(240,240,240)
  Note over M,D: ⑤ 학습 — 모든 판단의 단위는 "보드 점수 변화"
  G->>M: 보드 조회 (30분마다)
  G->>D: KPI 기록 · 점수 모델 갱신
  Note over S,G: bandit 보상, 할인·노출 판단의 B와 C,<br/>구매 기준선이 모두 같은 점수 모델로 계산됨
  end
```

## 읽는 법

1. **매입**: 키트가 60초마다 운영 두뇌를 깨움. 두뇌는 먼저 원장을 받아 기억을 최신으로 만들고, 주문이 없어도 할 일
   (과잉 재고 할인, 안 팔리는 등급을 앞에 보여주기)을 판단함. 공급 창구가 열려 있으면 "무엇을 몇 개 살지"를 정해
   키트에 넘김. 등급은 우리가 고를 수 없고 시장이 무작위로 붙임.
2. **판매**: 다른 학생의 구매자가 오면 가게가 기억을 보고 어떤 단위를 얼마에 부를지 정함. 가격은 규칙이 정하고
   bandit은 그 위에 ±10%(C등급은 A 시세의 0.8~1.0)만 실험함. 하한(원가+8%)은 학습이 건드리지 못함.
3. **구매**: 시장이 주문을 주면 구매자가 후보를 점수 매겨 상위 3곳과 흥정함. saved를 깎는 거래는 하지 않음.
   결제와 리뷰 제출은 키트가 함.
4. **포럼**: 새 글을 분류해 근거가 맞는 글은 추천, 조작 글은 비추천. 우리 글도 원장 근거가 있을 때만 씀.
5. **학습**: 보드를 주기적으로 읽어 "이 행동이 점수를 얼마나 바꾸나"를 계산하는 점수 모델을 갱신함. 판매의 bandit,
   선제 행동의 실행 여부, 구매 기준선이 모두 이 하나의 숫자로 판단됨.

## 근거 코드

| 단계 | 파일 |
|---|---|
| 키트 (주기, 공급, 결제, 결과 제출) | `scripts/market_kit.py` `sync_loop`·`restock`·`handle_order`, `market_runtime.py`, `kit_patches.py` |
| 운영 두뇌 | `restock_r3.py`, `r3lib/org/departments.py` (`ceo_tick`, `marketing_markdown`, `marketing_shelf`) |
| 선제 행동 | `r3lib/markdown.py`, `r3lib/shelf.py`, `r3lib/initiative.py` |
| 판매 | `shop_r3.py` (`find_unit`, `_terms`, `_learn`), `r3lib/pricing.py`, `r3lib/bandit.py` |
| 구매 | `buyer_r3.py` (`candidates`, `read_ask`), `review_r3.py` |
| 포럼 | `forum_r3.py`, `r3lib/decide.py` |
| 기억·점수 | `r3lib/memory.py`, `r3lib/score_model.py`, `r3lib/measure.py` |
