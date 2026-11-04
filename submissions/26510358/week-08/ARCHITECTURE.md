# Round 3 시스템 아키텍처 (2026-11-04 기준)

운영 형태: `systemd --user` 서비스 `week08-myeonggi-market-v4` → `market_runtime.py join` → `market_kit.py`(키트 v4).
키트가 터널·프록시·결제(ACP)·감시(watchdog)를 맡고, 우리 코드는 `agent.json`의 훅 5개로 붙음.

## 1. 전체 구성

```mermaid
flowchart LR
  subgraph EXT[외부]
    MKT[(market.ourolabs.ai<br/>/api/ledger · shops · catalog · supply · forum)]
    BUY[다른 구매자 에이전트]
    SHOPS[다른 가게 에이전트]
    JEV[Jev / Decisions API<br/>보정된 확률]
    CODEX[Codex CLI gpt-6-luna<br/>언어·ReAct]
  end

  subgraph KIT[market_kit.py v4 · systemd]
    TUN[cloudflared 터널 + 프록시]
    WD[watchdog<br/>죽은 seller 60초 내 재시작]
    HOOK[훅 실행기]
    OUT[outbox 전송<br/>글·투표]
    PAY[ACP 결제 · accept 검사]
  end

  subgraph AG[우리 에이전트 week-08]
    SHOP[shop_r3.py<br/>A2A 가게 서버 · 상주]
    MB[buyer_r3.py<br/>주문마다]
    RS[restock_r3.py<br/>60초마다]
    RV[review_r3.py<br/>정산마다]
    FO[forum_r3.py<br/>10분마다 · 백그라운드]
    RR[restock_review_r3.py<br/>30분마다 · 백그라운드]
  end

  subgraph LIB[r3lib]
    MEM[(memory.py<br/>SQLite 3계층)]
    PR[pricing.py<br/>돈 규칙]
    OR[oracle.py<br/>시세 분포 ★]
    SC[scout.py<br/>경쟁사 정찰 ★]
    PM[primer.py + 시장학개론 16원칙]
    DC[decide.py]
    RE[react.py + llm.py]
    IN[initiative.py<br/>Horvitz gate]
  end

  BUY -- A2A 협상 --> TUN --> SHOP
  MB -- A2A 협상 --> SHOPS
  HOOK --> MB & RS & RV
  RS -. kick .-> FO & RR
  WD -. 재시작 .-> SHOP
  RS -- buy 목록 --> HOOK -- POST /api/supply --> MKT
  MB -- receipt+accept --> PAY --> MKT
  FO -- outbox.jsonl --> OUT --> MKT
  RS -- sync_ledger · sync_shops --> MEM
  MEM <-- 원장 · 재고 --> MKT

  SHOP --> PR & SC & MEM
  RS --> PR & SC & MEM
  MB --> PR & MEM
  FO --> OR & DC & RE & IN & MEM
  RR --> RE & OR & SC & PM
  DC --> JEV
  RE --> CODEX
```

★ = 2026-11-04 추가

## 2. 결정 경로 세 갈래

| 경로 | 무엇을 결정 | 구현 | 시간 |
|---|---|---|---|
| **규칙** | 가격·하한·오퍼·보증·재고 수량 (돈) | `pricing.py`, `restock_r3.plan`, `scout`, 단골 코드 | 마이크로초, LLM 없음 |
| **판단 모델** | "이 글이 조작인가", "이 초안이 유용한가"의 확률 | `decide.py` → Jev | 수백 ms, 임계값은 코드가 적용 |
| **ReAct** | 먼저 말할지, 재고 계획 배율 | `react.py` + 읽기 전용 툴 | 수십 초, 결과는 검증·clamp 후 사용 |

## 3. 메모리 (r3lib/memory.py, `.market/r3/memory.sqlite`)

```
계층 1  시장이 서명·공개한 사실   ledger, settles, breaks, shop_stock, shops      ← /api/ledger, /api/shops
계층 2  우리 기록                 purchases, negotiations(style), restocks, members ★, notes
계층 3  포럼 신호 (약한 증거)     forum(signals, p_manipulation)
          │
          ├─ 뷰: category_demand · price_ratio · break_rate · shop_reputation · sell_through
          ├─ 프로필: buyer_style_profile · shop_profile (최근성 가중) · style_stars ★
          └─ oracle ★ (SKU·카테고리 분포)   scout ★ (공급 압력 = 경쟁사 공급 / 시장 정산)
```

## 4. 2026-11-04 변경점이 붙은 자리

```
                         ┌──────────── restock_r3 (60초) ─────────────┐
 /api/ledger ─► memory ─►│ demand × share(scout) × gap(scout) × 12h    │─► buy (≤10줄)
   supply 기록 ─┘        │ EV 수량 · 예산 = 잔고 40% (3M~8M) ★          │
                         │ 상한 25%, 수요 큰 카테고리 35% ★            │
                         │ harsh_styles 갱신 (10분) ★                  │
                         └──────────────┬─────────────────────────────┘
                                        │ kick (10분)
                         ┌──────────────▼──────── forum_r3 ────────────┐
                         │ 분류 → 투표 → ReAct+Horvitz (기존)           │
                         │ 침묵이면 → 리포트 레인 ★                     │
                         │   구매 기록 → SKU 시세 → 카테고리 시세 (순환) │
                         │   Jev 자가 점검 p_manipulation < 0.5          │
                         │   같은 주제 6시간 금지, 비추천 50% 이상이면 정지 │
                         └──────────────────────────────────────────────┘
 구매자 ─A2A─► shop_r3:  style 지문 → harsh면 거절 ★
                          첫 호가 = 시장비율 + 5% + 희소 프리미엄 3% ★ (scout)
                          회원 코드 인용 시 첫 호가 −3% ★ (하한 불변)
                          상품명 속 숫자를 제안가로 읽던 버그 수정 ★
                          거래 완료 메시지에 회원 코드 발급 ★
```
