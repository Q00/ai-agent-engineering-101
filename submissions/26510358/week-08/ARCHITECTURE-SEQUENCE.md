# Round 3 전체 흐름 시퀀스 다이어그램 (2026-11-16 17시 KST 기준)

우리 시스템이 시장에서 **물건을 사 오고(매입) → 팔고(판매) → 다른 가게에서 사고(구매) → 포럼에 쓰고 → 결과로 배우고
코드를 고치는(학습·배포)** 한 바퀴를 한 장으로 그린 것임. 11-11 판과 11-05 판(에이전트별 6장)은 git 이력에 있음.

- 점수: 가게 = 0.5 book + 0.3 매출 + 0.2 별점, 구매자 = 0.5 saved + 0.3 포럼 + 0.1 거래 수 + 0.1 글 수
- saved(patch 3.6) = 거래마다 (정가 − 산 값) / (정가 − 원가), 최대 1. 원가 아래로 사도 1점이라 원가보다 더 깎을 이유가 없음
- book = 현금 + 안 팔린 재고(원가). 재고는 안 팔려도 원가만큼은 점수이고, 손해는 하루 1% 보관료와 만료뿐임
- 모든 돈 결정(가격, 하한, 매입 수량, 구매 상한)은 코드 규칙이 함. LLM은 가게 응답 문장 읽기·재고 검토에,
  Decision API는 포럼 판단에만 씀
- 11-11 이후 바뀐 곳은 ★로 표시함

```mermaid
sequenceDiagram
  autonumber
  box rgb(235,242,255) 시장
    participant M as 시장 서버<br/>(원장·공급·주문·보드)
    participant O as 다른 학생들<br/>(구매자·가게)
  end
  box rgb(240,250,240) 우리 시스템 · 서버 한 대 · systemd 서비스 하나
    participant K as 키트<br/>(터널·결제 ACP)
    participant S as 우리 가게<br/>(shop_r3)
    participant B as 우리 구매자<br/>(buyer_r3, 주문마다 새 프로세스)
    participant G as 운영 두뇌<br/>(restock 훅 + 내부 조직)
    participant D as 기억<br/>(memory.sqlite)
  end
  participant X as 외부 모델<br/>(Codex LLM · OpenAI Decisions)

  rect rgb(255,248,230)
  Note over M,D: ① 매입 — 60초마다 점검, 공급 창구는 15~20분마다 3분만 열림
  K->>M: 재고·수요·포럼 조회 (10초마다)
  K->>G: 재입고 훅 실행 (60초마다)
  G->>M: 원장 새 항목 가져오기
  G->>D: 원장 저장 (모든 가게의 거래·공급·만료)
  G->>G: 선제 행동 (10분마다, 주문이 없어도)<br/>과잉 재고 할인 · 묵은 등급 먼저 보여주기<br/>p > C/(B+C) 일 때만 실행
  alt 공급 창구 열림
    G->>D: 카테고리 수요 · 실적(판매·만료) · 적응형 재고 상한
    Note over G: ★ 식품 매입은 11-14 23:59에 끝남 (마감 72시간 전)<br/>★ 일반 상품은 11-17 11:59까지 (마감 12시간 전)<br/>프리미엄(80만 원 이상)은 예산 따로
    G-->>K: 살 목록 (최대 10줄)
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
  S->>D: 보여줄 단위 고르기<br/>★ 이 구매자가 별점을 잘 주는 등급 우선 (taste)<br/>시장 시세 · 원가 하한 · 고장률
  S->>S: 첫 제시가 = 규칙 가격 × bandit 선택<br/>하한은 절대 안 내림
  S-->>O: 제시가 (등급 · 보증 72시간)
  loop 흥정
    O->>S: 제안가
    S-->>O: 수락 또는 역제안
  end
  S-->>O: 서명된 영수증 (등급 정직 표시)
  O->>K: 결제 (우리 ACP)
  K->>M: 정산 → 원장 기록
  S->>D: 협상 결과 + bandit 보상
  end

  rect rgb(245,240,255)
  Note over M,D: ③ 구매 — 시장이 주문을 줌. 주문 사이 최소 약 20초, 우리 주문이 끝나고 약 4초 뒤 다음 주문 (★ 16초 넘는 주문은 다음 주문을 밀어냄)
  M-->>K: 주문 (품목 후보, 예산)
  K->>B: buyer_r3 실행 (작업 트리의 코드를 그대로 씀)
  B->>D: 후보 점수 · 상한 max_pay<br/>★ 상한 = 마진 점유율 기준선 0.72~0.85 (patch 3.6 단위)<br/>C등급은 보증 없으면 안 삼
  par ★ 동시 견적 (가게 최대 8곳에 한 번에)
    B->>O: 사고 싶다고 알림 (A2A)
    O-->>B: 첫 제시가
  end
  Note over B: ★ 견적 마감 = 쓸 수 있는 4번째 견적 + 2초<br/>모두 답하면 즉시, 늦어도 20초<br/>마진 점유율 높은 순으로 정렬 (동점이면 더 싼 쪽)
  B->>X: 가격 문장이 애매할 때만 읽기 (Codex LLM)
  loop 마진 높은 가게부터 최대 3곳
    alt 가게가 "최종가"라고 함
      B->>O: 상한 이하면 바로 수락, 넘으면 다음 가게
    else ★ 역제안 (모든 주문): 첫 제시가가 마진 0.9 미만
      B->>O: 추정 원가로 한 번 역제안
      O-->>B: 다음 가격
      B->>O: 상한 이하면 수락
    else 그 외
      B->>O: 상한 이하면 수락, 넘으면 원가에서 출발해 반씩 올림 (최대 6번)
    end
    O-->>B: 서명된 영수증
  end
  B->>D: 판단 기록 (buyer-decisions.jsonl: 견적 도착 시각 · arm · 흥정 전문)
  B-->>K: 고른 영수증
  K->>O: 결제 (상대 가게 ACP)
  O->>M: 정산
  K->>M: 결과 제출 (별점 · 사실만 쓴 리뷰)
  end

  rect rgb(250,240,245)
  Note over M,X: ④ 포럼 — 키트 주기(약 6분)마다, 하루 약 254개 글
  G->>D: 새 글 저장
  G->>X: 글 분류 · 우리 글 자체 점검 (OpenAI Decisions API, gpt-6-luna, 하루 약 4,000회)
  X-->>G: 확률 (조작 · 보고 · 조언)
  G->>G: 문턱값은 코드가 적용<br/>근거가 맞는 글만 추천, 조작 글은 비추천
  G-->>K: 우리 글 · 투표 (outbox)
  K->>M: 게시
  end

  rect rgb(240,240,240)
  Note over M,X: ⑤ 학습과 배포 — 판단 단위는 "보드 점수 변화"
  G->>M: 보드 조회 (30분마다)
  G->>D: KPI 기록 (kpi.jsonl) · 점수 모델 갱신
  G->>X: 재고 검토 ReAct (30분마다, Codex LLM)<br/>도구: 계획 미리보기 · 판매율 · 재고 · 근거 · 기억<br/>LATM으로 만든 ledger_query도 주지만 아직 0회 사용
  Note over K,D: ★ 사람 + Claude Code 바깥 루프: 원본 trace(buyer-decisions.jsonl 등)를 읽고<br/>코드를 고쳐 커밋하면 바로 라이브. 새 동작은 주문 해시 arm(canary)으로 반만 켜고<br/>2시간 뒤 arm별로 비교해 넓히거나 0.0으로 롤백. 기록은 logs/r3/deploy-*.txt
  end
```

## 읽는 법

1. **매입**: 키트가 60초마다 운영 두뇌를 깨움. 두뇌는 원장을 받아 기억을 최신으로 만들고, 주문이 없어도 할 일
   (과잉 재고 할인, 안 팔리는 등급을 앞에 보여주기)을 판단함. 공급 창구가 열리면 무엇을 몇 개 살지 정해 키트에 넘김.
   식품 매입은 이미 끝났고, 일반 상품은 11-17 11:59까지 삼. 등급은 시장이 무작위로 붙임.
2. **판매**: 다른 학생의 구매자가 오면 가게가 그 구매자가 좋아하는 등급의 단위를 골라 가격을 부름. 가격은 규칙이
   정하고 bandit은 그 위에서만 실험함. 원가 하한은 학습이 건드리지 못함.
3. **구매**: 주문마다 buyer_r3가 새로 뜸. 가게 8곳에 동시에 견적을 받고, 쓸 만한 견적이 4개 모이면 2초 뒤
   마감함. 다음 주문은 최소 약 20초 뒤, 그리고 우리 주문이 끝나고 약 4초 뒤에 와서
   16초 안에 끝내야 처리량이 줄지 않음.
   마진 점유율 높은 가게부터 흥정하고, 상한(max_pay)을 넘는 값에는 사지 않음. 마진 0.9 미만인 첫 제시가에는
   한 번 깎아 봄.
4. **포럼**: 새 글을 Decisions API로 분류하고, 문턱값은 코드가 적용함. TypeSafe Jev 백엔드도 코드에는 있지만 키가
   비어 있어 쓰지 않음.
5. **학습과 배포**: 보드를 30분마다 읽어 KPI와 점수 모델을 갱신함. 코드 변경은 사람과 Claude Code가 원본 로그를
   읽고 정함. 이 서버에서는 커밋이 곧 배포라서, 새 동작은 canary로 반만 켜고 arm별로 판정함
   (Meta-harness의 "후보 코드 + 점수 + trace를 파일로 쌓고 다음 후보를 고르는" 바깥 루프를 사람이 손으로 도는 형태).

## 지금 켜진 canary와 롤백 스위치 (`r3lib/config.py`)

| 스위치 | 값 | 뜻 |
|---|---|---|
| `BUYER_FANOUT_SHARE` | 1.0 | 가게 최대 8곳 동시 견적 |
| `BUYER_COST_OPEN_SHARE` | 1.0 | 첫 제안을 추정 원가로 |
| `BUYER_QUOTE_EARLY_SHARE` | 1.0 | 4번째 견적 + 2초에 견적 마감 (`BUYER_QUOTE_MIN_WAIT_S` 8초는 11-16 실패로 0) |
| `BUYER_HAGGLE_SHARE` | 1.0 | 마진 0.9 미만 첫 제시가에 한 번 역제안 (11-16 canary 13.5시간: 0.890 vs 0.872) |

## 근거 코드

| 단계 | 파일 |
|---|---|
| 키트 (주기, 공급, 결제, 결과 제출) | `scripts/market_kit.py`, `market_runtime.py`, `kit_patches.py` |
| 운영 두뇌 · 매입 | `restock_r3.py` (`plan`), `r3lib/cashflow.py`, `r3lib/org/departments.py` |
| 선제 행동 | `r3lib/markdown.py`, `r3lib/shelf.py`, `r3lib/initiative.py` |
| 판매 | `shop_r3.py` (`find_unit`, `_terms`), `r3lib/taste.py`, `r3lib/pricing.py`, `r3lib/bandit.py` |
| 구매 | `buyer_r3.py` (`candidates`, `fanout_quotes`, `negotiate`, `run_order`), `r3lib/pricing.py` (`buyer_next_offer`) |
| 포럼 | `forum_r3.py`, `r3lib/decide.py` |
| 재고 검토 · 도구 제작 | `restock_review_r3.py`, `r3lib/react.py`, `r3lib/forge.py`, `tools/forged/ledger_query.py` |
| 기억 · 점수 | `r3lib/memory.py`, `r3lib/score_model.py`, `r3lib/measure.py` |
| 배포 | `ops/deploy_pull.py` (2분마다 hub로 push), `logs/r3/deploy-*.txt` |
