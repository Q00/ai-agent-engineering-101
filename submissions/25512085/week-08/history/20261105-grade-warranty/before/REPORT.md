# Week 08 Report — ACP and AP2: A Market With Payments (Round 3)

- **Student ID**: `25512085`
- **Shop / Buyer Name**: `mjforge`
- **Kit Version**: `market_kit 3` (`ACP 2026-04-17` checkout + `AP2 v0.2` Open/Closed Mandates)

---

## 1. Round 3에서 내 가게와 Buyer에 일어난 일 (정산 수 및 거부 사유 분석)

### 1.1 내 가게 (`mjforge` Seller + `AcpShop`)
- **동작 흐름**:
  1. Buyer와 A2A JSON-RPC로 가격 협상을 마친 뒤 Ed25519로 서명한 영수증(`sig_seller`)을 발급한다.
  2. Buyer 키트가 `/.well-known/acp.json`을 조회하고 `POST /acp/checkout_sessions`를 호출하면, 키트의 `AcpShop`이 영수증 서명과 품목·가격 일치를 검증한 뒤 가게 개인키(`shop_key.pem`)로 서명한 `checkout_jwt`를 반환한다.
  3. Buyer가 `POST /acp/checkout_sessions/{id}/complete`로 Closed Checkout Mandate(`mandate.checkout.1`)와 Closed Payment Mandate(`mandate.payment.1`)를 제출하면, 가게 키트가 시장의 `POST /api/settle`로 전달해 정산을 완료한다(`[acp] settled <item> at <price>, ledger #<id>`).
- **세션 누수(Memory Leak) 방지 및 TTL Eviction**:
  - 구매자가 협상 도중 이탈하거나 타임아웃으로 사라졌을 때 `self.open` 딕셔너리에 잔존 작업이 영구히 누적되는 메모리 누수를 방지하기 위해, 각 세션의 생성 시각(`created_at`)을 기록하고 300초(TTL) 경과 시 자동 축출(Eviction)하도록 개선했다.
- **Round 3 정산 현황 및 로그 인용**:
  - *(Round 3 거래 누적 후 `logs/market-kit.log`의 `[acp] settled` 건수 및 `/api/settle` 거부 사유 로그 업데이트 예정)*

### 1.2 내 구매자 (`mjforge` `market_buyer.py` + `acp_pay`)
- **ACP v3 지원 가게 선별 (`check_acp_support`)**:
  - Round 3에서는 영수증 서명(`sig_seller`, `sig_buyer`)이 유효하더라도 상대 가게가 `/.well-known/acp.json`을 제공하지 않거나(구버전 키트 v2 실행 중이거나 ACP 미구현 외부 배포 가게) 체크아웃 세션을 완료하지 못하면 정산(`verified: True`)으로 인정되지 않는다.
  - 이에 따라 [`market_buyer.py`](seller.py)의 [`pick_shop()`](market_buyer.py#L204-L320)에 [`check_acp_support()`](market_buyer.py#L190-L201) 프로브를 추가하여 후보 가게의 `/.well-known/acp.json` 엔드포인트(`protocol == "acp"`, `"checkout"` capability)를 사전 확인하고 ACP-v3 준비가 완료된 가게를 우선 선택하도록 개선했다.
- **바이어 예산 누수 방지 (Budget-Decoupled Negotiation)**:
  - 기존의 `budget * 0.75`와 같은 예산 비례 제안 공식은 상점에게 구매자의 비밀 예산 한도(Open Mandate)를 역산할 수 있는 사이드 채널을 제공하는 치명적인 취약점이 있었다.
  - 이를 완전히 제거하고, 품목의 원가 하한선(`reserve`: 100/180/260)을 탐색 바닥으로 삼아 1라운드에는 예산 크기와 무관하게 원가부터 시작하여 상점 호가와의 간극에 따라 단계적으로 제안을 올리도록 개편했다. 예산(`budget`)은 오직 절대 상한선(`offer <= budget`)으로만 기능한다.
- **자발적 담합 방지 (Diversity & Cooldown)**:
  - 최근 3분 이내에 방문했거나 누적 방문이 3회 이상인 상점에는 순위 페널티를 부여하여, 특정 상점과의 상호 순환 거래나 인위적 거래 몰아주기 클러스터에 편입되지 않도록 탐색 다양성을 프로토콜 레벨에서 강제했다.
- **Open Mandate 상한 준수 및 가격 파싱 보정**:
  - 협상 루프의 모든 라운드에서 `offer = min(budget, offer)` 가드레일을 강제하고, 영수증 검증 단계에서도 `rec_price > budget`인 경우 즉시 파기한다.
  - 상대 가게가 응답 메시지에 `"100% satisfaction guarantee"` 등 퍼센트 숫자를 포함할 때 마지막 숫자(`100`)가 실제 호가를 덮어쓰던 문제를 정규식(`re.sub(r"\d+(?:\.\d+)?\s*%", " ", text)`)으로 제거했다.

---

## 2. 예산이 가려진 뒤 바꾼 호가 전략과 결과

### 2.1 7주차(Round 2) 전략의 한계
- 7주차 공개 원장(`/api/ledger`)의 `kind == "order"` 항목에는 주문마다 구매자의 예산(`budget`)이 평문으로 노출되어 있었다.
- 또한 7주차 후반의 평점 복구 모드에서는 1라운드 거절로 인한 2점 후기를 피하기 위해 첫 호가를 하한가(`RECOVERY_OPEN_MULT = 1.00`, `lamp=100, chair=180, bookshelf=260`)로 즉시 제시하고 1라운드에 하한가 이상의 모든 제안을 즉시 수락했다.
- 그 결과 체결률은 높았으나 구매자의 지불 의사(Willingness to Pay)가 높은 주문에서도 잉여 마진을 전혀 확보하지 못하고 평균 거래가가 하한가에 수렴하는 한계가 있었다.

### 2.2 8주차(Round 3) 호가 전략 개편: 정산가 분위수 기반 동적 앵커링 & 2단계 양보 곡선
Round 3에서는 원장의 `order` 항목에서 `budget`이 제거되고 해시값인 `budget_commitment`만 남으며, 실제 정산이 완료된 거래만 `kind == "settle"` 항목의 `amount`로 기록된다. 이에 맞춰 [`seller.py`](seller.py)의 가격 결정 구조를 다음과 같이 전면 개편했다.

1. **Round 3 `settle` 원장 동기화 ([`background_review_sync`](seller.py#L180-L270), [`compute_item_settle_stats`](seller.py#L131-L147))**:
   - `/api/ledger`에서 `kind == "settle"`(정산 완료 금액 `amount`) 및 검증된 거래 가격을 품목별로 최근 40건까지 수집한다.
   - 품목별 최근 정산가의 **평균(`mean`)**, **중앙값(`p50`)**, **65백분위수(`p65`)**를 실시간으로 계산한다.
2. **정산가 분위수 블렌딩 첫 호가 ([`compute_round3_opening_ask`](seller.py#L85-L128))**:
   - 정산 데이터가 축적된 경우 `0.60 * P65 + 0.40 * Mean`을 블렌딩하여 시장의 실제 지불 가능 금액대를 추정하고, 이를 하한가 대비 **`[1.15x, 1.25x]`** 밴드로 클램핑하여 첫 호가(`opening_ask`)로 제시한다 (콜드스타트 기본값 `1.18x`: `lamp=118`, `chair=212`, `bookshelf=307`).
   - 재방문 고객 및 VIP(`is_repeat` / `is_vip`)에게는 5% 로열티 할인을 적용하되 최소 `1.08x`를 유지하고, 수요 급증 구간(`is_wave_active`)에는 `+3%` 프리미엄을 부여한다.
3. **2단계 수락 기준 및 단계적 양보 곡선 (`r1_target` $\rightarrow$ `reserve`)**:
   - **1라운드 수락 기준 (`r1_target`)**: `max(ceil(reserve * 1.05), round(reserve + (opening_ask - reserve) * 0.50))` (예: `lamp` 첫 호가 118일 때 1라운드 목표가 `109`).
   - 구매자가 1라운드에 `r1_target` 이상을 제시하면 즉시 체결하여 마진을 확보한다.
   - 구매자가 1라운드에 하한가 근처(`100 <= offer < r1_target`)로 탐색 제안을 던지면 즉시 하한가로 굴복하지 않고 `r1_target`(`109`)으로 역제안(Counter-offer)한다.
   - **2라운드 이후 (`rounds >= 2`)**: 구매자가 2라운드 이후에도 `offer >= reserve`를 제시하면 해당 가격에 즉시 수락하여, 실제 예산이 하한가에 딱 맞춘 구매자도 절대 놓치지 않고 100% 정산으로 연결시킨다.

---

## 3. 7주차 사건 중 Round 3이 막지 못한 것과 그 이유

### 3.1 Round 3(ACP + AP2)가 막지 못한 사건: **에이전트 간 양방향 담합 (EP 1: `fair-furniture` $\leftrightarrow$ `ggari-shop`)**
- **7주차 사건 요약**:
  - 7주차 시장에서 `fair-furniture`의 buyer는 전체 구매의 86%(12,192건)를 `ggari-shop`에서 구매했고, 반대로 `ggari-shop`의 buyer는 전체 구매의 91%(4,978건)를 `fair-furniture`에서 구매하며 상호 거래량과 평점을 인위적으로 부풀렸다.

### 3.2 왜 ACP Checkout과 AP2 Mandate로도 담합을 막을 수 없는가?
1. **검증 계층(Layer)의 불일치 — 결제 정당성 vs 거래 상대 선택의 공정성**:
   - AP2의 Open/Closed Mandate와 ACP Checkout이 검증하는 것은 **"이 결제가 주문의 품목·예산 범위(`amount_range`) 안에 있는가?"**, **"가게가 서명한 `checkout_jwt` 해시(`checkout_hash`)와 구매자가 승인한 금액이 정확히 일치하는가?"**, **"재사용(replay)되지 않은 유효한 주문인가?"**라는 **결제 실행 층(Payment Layer)의 무결성**이다.
   - 담합하는 두 에이전트는 실제로 시장에서 정상적인 Open Mandate를 발급받고, 하한가 이상·예산 상한 이하의 정상 가격으로 합의하며, 각자의 진짜 개인키(`shop_key.pem`, `buyer_key.pem`)로 `checkout_jwt`와 Closed Mandate에 서명한다.
   - 따라서 `/api/settle`의 모든 암호학적 검사(`checkout_signed_by_shop`, `checkout_mandate_signed_by_buyer`, `payment_mandate_signed_by_buyer`, `bound_to_open_mandates`, `amount_in_range`)를 100% 정상 통과한다.
2. **어느 층에서 어떻게 다뤄야 하는가?**:
   - 담합은 결제 층(Payment Layer)의 위조 문제가 아니라 **발견(Discovery) 및 기록·평판(Ledger & Reputation) 층의 그래프 편향 문제**다.
   - 이를 제어하려면:
     1. **주문/위임장 층(Order / Open Mandate Layer)**: Open Checkout Mandate의 `checkout.allowed_merchants` 제약에 직전 $K$회 연속 거래한 가게를 제외하거나 특정 가게 비중 상한(Concentration Cap)을 두는 방법.
     2. **기록·평판 집계 층(Reputation / Board Layer)**: 정산 원장(`settle` 그래프)에서 특정 `(buyer, shop)` 쌍의 상호 거래 집중도(Reciprocal Trade Ratio)나 엔트로피를 계산하여, 폐쇄적 순환 거래에 대해서는 리더보드 가중치와 평점 반영률을 감쇠(Discounting)시키는 메커니즘이 필요하다.

# 2026-11-04 셀러 Cash 성장 변경 이력

이번 변경은 `POLICY.md` 12장과 `seller_cash_policy.json`에 정의했다. 목표를 우리 예상 판매수와 환불 차감 건당 기여의 곱으로 두고, 30분 수요의 1.75배를 준비한다. 정상 성장기에만 작동하며 기존 종료 전 축소·재구매 금지·처분·서명·식품 사용기간 확인은 유지한다.

같은 SKU·등급·6시간 보증의 실제 체결가를 첫 가격 기준으로 사용하고, 새 경로의 양보를 1회·최대 1%로 제한했다. 원가가 정가를 넘더라도 정가를 부풀리거나 할인이라고 표시하지 않는다. 게시판은 현재 인증된 재고와 실제 첫 가격만 20분 간격으로 공지한다. buyer 전략은 변경하지 않았다.

수정 전 205개, 수정 후 220개 오프라인 테스트를 통과했다. 원격 buyer 수정 2개 커밋을 보존해 최신 머리에 통합한 뒤에는 새 buyer 테스트까지 포함한 227개가 통과했다. 로그와 수정 전후 파일은 `logs/seller-cash-*.log`, `history/20261104-seller-cash/`에 보존했다. GitHub 반영은 실제 VM 배포·판매 실적 개선 확인과 구분한다. 다음 기존 보고서 본문은 이전 구현 당시 기록으로 보존하며 현재 셀러 규칙은 POLICY 12장을 우선한다.

