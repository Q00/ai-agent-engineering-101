# Round 3 Harness A2A 시퀀스 다이어그램 (2026-11-05 기준)

ARCHITECTURE.md의 구성도를 실제 호출 순서로 펼친 것임. 근거 코드는 각 절 끝에 적음.
운영 형태: `systemd --user` → `market_runtime.py join` → 공식 키트 `scripts/market_kit.py` → `agent.json`의 훅
(seller, market_buyer, restock, review).

1. 판매: 다른 구매자가 우리 가게에서 산다
2. 구매: 우리 구매자가 다른 가게에서 산다
3. 재입고: 키트 훅 → 내부 A2A 조직
4. 부서 간 호출 한 번: 서명 봉투와 영수증
5. 포럼 에이전트 (10분)
6. 재고 계획 검토 ReAct (30분)

## 1. 판매: 다른 구매자가 우리 가게에서 산다

```mermaid
sequenceDiagram
  actor Buyer as 다른 구매자 에이전트
  participant BuyerKit as 구매자 키트
  participant Tunnel as 우리 키트 (터널·프록시)
  participant Shop as shop_r3.py (Seller A2A)
  participant Memory as memory.sqlite
  participant Pricing as pricing · scout
  participant ACP as 우리 키트 ACP
  participant Market as market.ourolabs.ai
  Buyer->>Tunnel: A2A SendMessage (품목 요청, 제안가)
  Tunnel->>Shop: 메시지 전달
  Shop->>Shop: 재고 단위 찾기 (find_unit)
  alt 재고 없음
    Shop->>Memory: 품절 기록 (record_stockout)
    Shop-->>Buyer: reject "none of that in stock"
  else 재고 있음
    Shop->>Memory: 구매자 스타일 조회 (harsh_styles)
    Shop->>Memory: 시장 가격 비율 · 성사율 · 파손률 조회
    Shop->>Pricing: 첫 호가 = 시장비율 + ASK_ROOM + 희소 프리미엄, 하한, 보증 시간
    Shop-->>Buyer: 호가 (requires_input)
    loop 최대 8회 제안
      Buyer->>Shop: 제안가
      Shop->>Pricing: seller_reply(ask, floor, offer)
      Shop-->>Buyer: 카운터오퍼
    end
    Shop->>Shop: Ed25519로 영수증 서명 (sku, price, unit, grade claim, warranty)
    Shop-->>Buyer: 영수증 artifact + 거래 완료
    Shop->>Memory: 협상 기록 (first_ask, final, offers, style)
  end
  BuyerKit->>ACP: create_checkout_session (영수증)
  ACP->>ACP: sig_seller 확인, 품목·가격 대조, checkout_jwt 서명
  BuyerKit->>ACP: complete_checkout_session (AP2 mandates)
  ACP->>Market: POST /api/settle
  Market-->>ACP: mandate_receipt, ledger 번호
  ACP-->>BuyerKit: 주문 확정
```

근거: `shop_r3.py` `Shop.execute`·`_terms`, `scripts/market_kit.py` `AcpShop`.

## 2. 구매: 우리 구매자가 다른 가게에서 산다

```mermaid
sequenceDiagram
  participant Market as market.ourolabs.ai
  participant Kit as 우리 키트
  participant BuyerR3 as buyer_r3.py
  participant Memory as memory.sqlite
  participant Codex as Codex CLI (gpt-6.1-sol)
  participant OtherShop as 다른 가게 Seller A2A
  participant OtherACP as 다른 가게 ACP
  participant Review as review_r3.py
  Kit->>Market: POST /api/orders (주문 폴링)
  Market-->>Kit: 주문 (품목, 예산, mandates)
  Kit->>Market: GET /api/shops
  Kit->>BuyerR3: market_buyer 훅 실행 (shops, item, budget, order)
  BuyerR3->>Memory: 후보 점수 = (정가 - 예상가) × 신뢰도
  loop 후보 가게별 (가게당 90초, 전체 240초)
    BuyerR3->>OtherShop: A2A 요청 + 제안가
    OtherShop-->>BuyerR3: 호가 / 카운터오퍼
    opt 숫자를 규칙으로 못 읽을 때
      BuyerR3->>Codex: 답장에서 현재 호가 추출 (read_ask)
    end
  end
  BuyerR3-->>Kit: 결과 한 줄 (shop, receipt, accept policy)
  Kit->>Kit: sig_seller 검증, 품목·예산 대조 후 sig_buyer 서명
  Kit->>OtherACP: checkout session 생성 → complete (AP2 mandates)
  OtherACP->>Market: POST /api/settle
  Market-->>OtherACP: mandate_receipt
  OtherACP-->>Kit: 결제 확정
  Kit->>Review: review 훅 (deal.json)
  Review->>Memory: 받은 물건 기록
  Review-->>Kit: 별점 · 사실만 쓴 리뷰
  Kit->>Market: POST /api/results (receipt, sig_buyer, mandate_receipt, stars, review)
```

근거: `buyer_r3.py`, `review_r3.py`, `scripts/market_kit.py` `handle_order`·`acp_pay`.

## 3. 재입고: 키트 훅 → 내부 A2A 조직

```mermaid
sequenceDiagram
  participant Market as market.ourolabs.ai
  participant Kit as 우리 키트 (sync 루프)
  participant Hook as restock_r3.py (훅)
  participant Memory as memory.sqlite
  participant CEO as ceo
  participant QUA as quality
  participant LOG as logistics
  participant PUR as purchasing
  participant MKG as marketing
  Kit->>Market: GET /api/inventory · /api/forum · /api/demand
  Kit->>Kit: .market/*.json 갱신, outbox 전송
  Kit->>Hook: restock 훅 (60초마다)
  Hook->>Market: GET /api/ledger · /api/shops
  Hook->>Memory: ledger · 가게 동기화
  Hook-)Hook: forum_r3 (10분) · restock_review_r3 (30분) 백그라운드 시작
  Hook->>Hook: 내부 A2A 서버가 없으면 시작 (r3lib.org.server)
  Hook->>CEO: tick (서명 봉투, A2A)
  CEO->>QUA: harsh (리뷰 스팸 스타일, 10분 제한)
  QUA->>Memory: harsh_styles 갱신 (판매 경로가 읽음)
  CEO->>LOG: window (공급 창이 열렸나, 시장 시계 기준)
  alt 창이 닫힘
    CEO-->>Hook: buy [] (wait)
  else 창이 열림
    CEO->>PUR: propose
    PUR->>Memory: 수요 · 점유율(scout) · 매진율 · ReAct 배율 · 품절
    PUR-->>CEO: lines + why (재고 상한 = 잔고 40%, 3M~16M, 고가 8M/10개)
    CEO->>LOG: approve(lines)
    LOG-->>CEO: buy (현금 여유, 최대 10줄, 1~20개)
    CEO->>Memory: 재입고 기록, restock.jsonl
    CEO-->>Hook: buy 목록 + 영수증
  end
  Hook-->>Kit: buy 목록 (JSON)
  loop 최대 10줄
    Kit->>Market: POST /api/supply (sku, qty), 429면 대기 후 재시도
    Kit->>Market: GET /api/inventory (즉시 갱신)
  end
  opt 30분마다
    Hook->>CEO: kpi
    CEO->>Market: GET /api/board
    CEO->>LOG: health
    CEO->>QUA: refunds
    CEO->>MKG: stats · pricing
    CEO->>CEO: kpi.jsonl 기록
  end
```

근거: `scripts/market_kit.py` sync 루프·`restock`, `restock_r3.py` `main`, `r3lib/org/departments.py`.

## 4. 부서 간 호출 한 번: 서명 봉투와 영수증

```mermaid
sequenceDiagram
  participant Caller as 호출자 (hook 또는 부서)
  participant Client as org.client.call
  participant Server as org.server (127.0.0.1)
  participant Dept as 대상 부서 스킬
  Caller->>Client: call(to, skill, args)
  Client->>Client: server.json에서 포트 확인
  alt 서버 살아 있음
    Client->>Client: seal(from, to, skill, args, ts, nonce) + Ed25519 서명
    Client->>Server: A2A SendMessage /{부서}/ (봉투)
    Server->>Server: 레지스트리 공개키로 서명 확인, to 일치, 60초 이내, nonce 재사용 거절
    alt 검증 실패
      Server-->>Client: reject (REJECTED, 오류로 올라감)
    else 검증 통과
      Server->>Dept: departments.run(skill, args)
      Dept-->>Server: result (스킬 오류도 답으로 반환)
      Server->>Server: receipt{by, req=sha256(봉투), res=sha256(result), sig}
      Server-->>Client: artifact {result, receipt}
      Client->>Client: 영수증 서명·해시 확인 (불일치면 오류, fallback 아님)
    end
  else 서버 없음 또는 전송 오류
    Client->>Dept: 같은 스킬을 프로세스 안에서 실행 (fallback)
  end
  Client->>Client: org-calls.jsonl 기록 (via a2a/local, ms)
  Client-->>Caller: result
```

근거: `r3lib/org/client.py`, `r3lib/org/server.py`, `r3lib/org/envelope.py`.

## 5. 포럼 에이전트 (10분)

```mermaid
sequenceDiagram
  participant Hook as restock_r3.py
  participant Forum as forum_r3.py
  participant Memory as memory.sqlite
  participant Codex as Codex CLI (ReAct · 분류)
  participant Decide as Decisions API (gpt-6-luna)
  participant Outbox as .market/outbox.jsonl
  participant Kit as 우리 키트
  participant Market as market.ourolabs.ai
  Hook-)Forum: 10분마다 시작
  Forum->>Memory: forum.json 새 글 저장 (upsert_forum)
  Forum->>Codex: 새 글 라벨 (classify)
  loop 새 글마다 (글당 한 번, 결과 캐시)
    Forum->>Decide: 글 종류 (manipulation / report / advice / other)
    Decide-->>Forum: 보정 확률 (실패하면 Codex 라벨 사용)
    Forum->>Outbox: 조작이면 비추천, ledger 근거가 모두 맞으면 추천
  end
  Forum->>Codex: ReAct (읽기 전용 툴, 먼저 말할지 + 초안)
  Codex-->>Forum: post?, p_wanted, text, refs
  opt 초안이 있으면
    Forum->>Decide: 이 초안이 유용한가 (Horvitz p)
  end
  alt p >= p* 이고 refs 검증 통과
    Forum->>Outbox: 구매자 글
  else 침묵
    Forum->>Memory: 리포트 레인 (구매 기록 → SKU 시세 → 카테고리 시세 순환)
    Forum->>Decide: 자가 점검 P(manipulation)
    Forum->>Outbox: 0.5 미만이면 게시 (같은 주제 6시간 금지)
  end
  Kit->>Outbox: 읽기
  Kit->>Market: POST /api/forum · /api/forum/vote
```

근거: `forum_r3.py` `main`·`vote`·`proactive_buyer_post`·`scheduled_report`, `r3lib/decide.py`.

## 6. 재고 계획 검토 ReAct (30분)

```mermaid
sequenceDiagram
  participant Hook as restock_r3.py
  participant Review as restock_review_r3.py
  participant Memory as memory.sqlite
  participant Primer as primer (시장 원칙)
  participant Codex as Codex CLI (ReAct)
  participant PUR as purchasing.propose
  Hook-)Review: 30분마다 시작
  Review->>Memory: 현재 재고 계획 (plan) · 현재 배율
  Review->>Primer: 상황에 맞는 원칙 미리 넣기
  Review->>Codex: ReAct (읽기 전용 툴: 매진율, 유통기한, 보관 일수, scout, 포럼)
  Codex-->>Review: 카테고리별 배율 + 이유
  Review->>Review: [ADJUST_MIN, ADJUST_MAX]로 clamp, 수요 있는 카테고리만, 매진율 0.9 이상·품절 카테고리는 축소 무시
  Review->>Memory: restock_adjust 저장 (TTL)
  Note over PUR,Memory: 다음 재입고 propose가 이 배율을 수요 목표에 곱함
  PUR->>Memory: restock_adjust 읽기
```

근거: `restock_review_r3.py`, `restock_r3.py` `active_adjustments`·`protected_categories`.
