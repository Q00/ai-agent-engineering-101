# Week 07 과제 리포트: A2A 에이전트 마켓 (Round 2) 분산 자율 거래 시스템

**제출자 학번:** 25512085  
**GitHub 닉네임 / 마켓 등록명:** `mjforge` (`@MJforge`)  
**상점 배포 주소:** `https://mjforge-shop.mjforge-worker.workers.dev`  
**사용 하네스 & 모델:** OpenAI Codex CLI (`codex`) v0.147.0 (`gpt-5.6-sol`, 비대화형 파이프라인)

---

## 1. 구매자(Buyer)의 상점 선택 알고리즘 및 메모리 포맷 (Shop Selection & Memory Format)

### 1.1 30% / 50% / 20% 독점 방지 계층형 분산 선택 알고리즘
마켓 2라운드에서는 특정 1~2개 덤핑 상점(`11im`, `lee-sungsoo`)으로 거래가 과도하게 쏠리는 독점화 현상이 발생했습니다. 이에 따라 본 에이전트는 시장 생태계의 건전성과 공정한 리뷰 분산을 위해 **상위 10개 독점 상점을 원천 배제**하고, 나머지 학생 상점들을 3개 그룹으로 층화 표본 추출(Stratified Sampling)하는 알고리즘을 구현했습니다:

1. **상위 10개 상점 배제 필터링 (`top10_names` 제외)**:
   * 마켓 전체 보드 통계(`fetch_board_stats()`)를 바탕으로 리뷰 수, 거래 수, 평점이 상위 10위 안에 드는 쏠림 상점과 주최 측 더미 하우스 샵(`is_house`)을 후보 풀에서 제외합니다.
2. **3개 타깃 버킷 확률적 배분**:
   * **버킷 A (30% 확률) - 신규 상점 발굴 (Cold-Start Rescue Tier)**:
     * 조건: 거래 수(`deals == 0`), 리뷰 수(`reviews == 0`), 평점(`stars is None`)인 거래 실적 0건의 신생 샵.
     * 목적: 거래 기회를 얻지 못해 평점조차 없는 동기 학생들의 상점을 우선 방문하여 마켓 참여 기회를 제공합니다.
   * **버킷 B (50% 확률) - 중위권 우수 상점 (Mid-Share Quality Tier)**:
     * 조건: 마켓 점유율 1% ~ 5% (`0.008 <= share <= 0.055`)이며 외부 평점 3.5★ 이상인 검증된 상점.
     * 목적: 안정적인 품질과 합리적인 가격대를 유지하는 중위권 건전 상점들과의 거래를 주력으로 삼습니다.
   * **버킷 C (20% 확률) - 정직한 저평점 상점 회복 (Honest Recovery Tier)**:
     * 조건: 리뷰 수 1~5개, 평점 3.5★ 미만이지만, 거래 수와 리뷰 수가 정확히 일치(`deals == reviews`)하고 조작 징후가 없는 정직한 상점.
     * 목적: 봇의 무차별 저평점 테러 등으로 억울하게 평점이 깎인 정직한 상점을 구제합니다.
3. **방문 분산 가중치**:
   * 각 버킷 내부에서는 우리 바이어의 자체 방문 횟수(`mem_count`)가 가장 적은 상점을 우선순위로 정렬하여 특정 한 상점만 반복 방문하지 않고 40개 이상의 상점에 골고루 주문을 분산합니다.

### 1.2 유계 메모리 포맷 (`memory.json` Rolling Buffer)
과제 요구사항(Challenge 2)인 메모리 파일의 무한 팽창(Unbounded Append) 누수를 방지하기 위해 **상점당 최근 5건의 거래만 유지하는 슬라이딩 윈도우(Rolling Buffer)** 포맷을 설계했습니다:
```json
{
  "shop-name": {
    "archived_count": 12,
    "archived_avg_stars": 4.25,
    "dishonest": false,
    "last_visited_ts": 1792558210.12,
    "recent": [
      {
        "item": "chair",
        "budget": 225,
        "price": 202,
        "rounds": 1,
        "stars": 5,
        "outcome": "great-service-deal"
      }
    ]
  }
}
```
* **동작 원리**: 최근 거래(`recent`)가 5건을 초과하면 가장 오래된 거래를 큐에서 밀어내고, 그 거래의 별점을 `archived_avg_stars`와 `archived_count`에 수학적으로 누적 롤업합니다.
* **효과**: 1,400건 이상의 대규모 주문을 처리해도 `memory.json` 파일 크기가 **`32.7 KB`**로 완벽하게 일정하게 유지되며, 메모리 누수 없이 영구적으로 장기 평판을 추적합니다.

---

## 2. 하네스(Codex)에 리뷰를 노출하는 방식 (Review Feeding & Summarization)

* **리뷰 정제 및 요약 (`summarize_shop`)**:
  * 마켓 서버가 제공하는 `{shops}`의 최근 리뷰 목록에서 사기 의심 키워드(`"receipt said"`, `"higher than"`, `"overcharged"`, `"rip-off"`, `"scam"`, `"forged"`)를 카운트하여 신뢰도를 사전 필터링합니다.
* **프롬프트 주입 구조**:
  * 선택된 버킷의 상위 후보 상점들을 하네스에게 전달할 때, 장황한 텍스트 대신 **점유율, 거래 수, 평점, 본인 방문 횟수**를 표준 한 줄 포맷으로 구조화하여 전달합니다:
  ```text
  You are selecting a shop from the '50% Mid-Share Tier' bucket for a chair (budget 225).
  Pick the best candidate (prefer fewer my_visits and honest stats).
  Candidates:
  0. name=outseop-26510122 | share=1.2% | deals=85 | reviews=5 (avg=4.2) | visits=92 | my_visits=1
  1. name=seochanit | share=2.1% | deals=140 | reviews=5 (avg=4.4) | visits=155 | my_visits=3

  Reply on ONE line: PICK <index> | <short reason>
  ```
* **결과**: 비대화형 Codex CLI 하네스가 1~2초 내에 환각 없이 명확한 근거(`PICK 0 | lowest visits and clean receipt history`)를 반환하도록 설계하여 빠른 응답성과 의사결정 투명성을 확보했습니다.

---

## 3. 라운드 0, 1, 2 경과 분석 및 심층 사례 연구 (Market Trajectory & Case Study)

### 3.1 공식 지표 및 누적 성과 (실제 대시보드 및 원장 수치)
* **상점 (`mjforge` - Cloudflare Workers 24/7 가동)**:
  * **총 거래 체결 (`deals`)**: **186건** (전체 학생 48개 샵 중 17위)
  * **총 누적 매출 (`revenue`)**: **35,576원** (학생 샵 중 18위)
  * **전체 평균 평점 (`stars`)**: **`4.07★`** (4점대 완전 안착 및 지속 상승)
  * **최근 10개 리뷰 평균**: **`4.80★`** (최고점 그룹 형성)
  * **방문 대비 거래 전환율**: 297회 방문 중 186건 체결 (**61.3%**)
* **구매자 (`mjforge` - 자동 재접속 데몬 가동)**:
  * **총 주문 수 (`orders`)**: **1,463건**
  * **총 구매 성사 (`deals`)**: **1,010건** (1,000건 클럽 돌파! 학생 62명 중 12위)
  * **불가능 주문 보류 (`held / hopeless`)**: **`292 / 292` (100.0% 완벽 방어)**
  * **예산 대비 지불율 (`paid`)**: **`0.899`** (약 10.1% 할인 구매 집행)

---

### 3.2 💡 심층 사례 연구: 마케팅 홍보 문구 및 네거티브 저격이 초래한 부정적 나비효과
마켓 운영 도중 평점 급락 위기가 찾아왔을 때, *"우리 가게의 전면 개편 행사를 홍보하고, 평점을 깎는 악성 봇을 조심하라는 경고를 공용 리뷰와 셀러 메시지에 넣자"*는 아이디어를 시도했던 적이 있습니다(커밋 `20da8da`). 
그러나 이 시도는 **인간 중심의 전자상거래와 AI 자율 에이전트 마켓 간의 근본적인 메커니즘 차이**로 인해 심각한 부작용을 일으켰으며, 이를 분석하고 즉시 철회(`fd9956b`)한 과정은 이번 과제에서 가장 값진 엔지니어링 교훈이었습니다:

1. **바이어 정규식 및 문자열 슬라이싱 실패 (`nocha12` 사례, `2★` 결렬)**:
   * 인간 소비자는 긴 홍보글 속에서도 가격을 찾아내지만, 경량화된 학생 바이어 에이전트들은 메시지의 앞부분(예: `text[:120]`)만 자르거나 단순 정규식으로 가격을 추출합니다.
   * 앞에 400자 분량의 이벤트 안내 문구를 넣자, `nocha12` 바이어가 가격 숫자를 찾지 못하고 `"No price offered for bookshelf (TASK_STATE_CANCELED)"`라며 거래를 취소하고 **`2★` 불발 리뷰**를 남기는 참사가 발생했습니다.
2. **타사 바이어의 LLM 토큰 및 분당 호출 제한(Rate Limit) 초과 (`chanarun` 사례, `1★` 폭탄)**:
   * 무료/저가 티어 LLM(예: Groq/OpenAI 분당 5회 제한)을 사용하는 바이어들은 프롬프트가 길어지면 토큰 소모가 급증하고 다턴 협상 시 Rate Limit에 걸립니다.
   * `chanarun` 바이어가 우리 가게를 방문했을 때 긴 프로모션 본문으로 인해 5번째 턴에서 바이어 측 LLM 호출 한도가 초과되며 결제 직전 `RuntimeError: 429 Rate limit reached`로 튕겼고, 결국 우리 가게에 **`1★` 시스템 에러 리뷰**가 찍혔습니다.
3. **역효과를 낳은 협상 줄다리기 (`11im` 사례, `3★` 감점)**:
   * 안내문에 적힌 *"최대 33% 할인"*이라는 단어를 본 `11im`의 LLM이 협상 여지가 많다고 판단하여 첫 턴에 결제하지 않고 `200 → 220 → 240 → 260`으로 4턴 동안 가격을 찔러보았고, 턴수가 길어졌다는 자체 규칙에 따라 기계적으로 **`3★`**로 감점했습니다.
4. **마켓 구조와의 불일치**:
   * 에이전트 마켓의 바이어들은 후기 텍스트를 읽고 자발적으로 해당 상점을 검색해 찾아오는 알고리즘을 갖추고 있지 않습니다. 후기 공간에 광고를 넣는 것은 유입 효과는 전혀 없이 프로토콜 버퍼만 오염시키는 무의미한 행위였습니다.
5. **피벗 및 극적인 반등**:
   * 이러한 실패 데이터를 확인한 즉시, 모든 광고성 배너를 전면 삭제(`fd9956b`)하고 **"맨 앞 50자 이내에 핵심 가격 배치 + 1턴 즉시 수락"** 알고리즘으로 구조를 전환했습니다.
   * 그 결과, 파싱 에러와 Rate Limit이 완벽히 사라지며 **단 15분 만에 38건의 거래가 쏟아졌고, 평점은 4.8★로 수직 반등**했습니다.

---

## 4. 서명(Ed25519) 추가 과정의 실패 사례 및 디버깅 (Signature Failures & Debugging)

2라운드 규칙인 Ed25519 전자서명(`sig_seller`) 도입 및 운영 과정에서 3가지 중대한 기술적 실패를 마주하고 해결했습니다:

### (1) 정규화 JSON(Canonical JSON) 직렬화 불일치
* **실패 증상**: 로컬 검증에서는 통과했으나 `market_kit` 검증에서 `sig_seller does not verify with shop_key.pem` 오류 발생.
* **원인**: Python의 `json.dumps()` 기본값은 쉼표 뒤에 공백(`", "`)이 들어가지만, 과제 규격의 Canonical JSON은 공백이 전혀 없는 압축형(`separators=(",", ":")`)과 키 오름차순 정렬(`sort_keys=True`)을 요구했습니다.
* **해결**: `canon = lambda d: json.dumps({k: d[k] for k in sorted(d)}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")` 함수를 구현하여 엄격히 일치시켰습니다.

### (2) A2A 데이터 파트의 실수형(Double) 변환 오류
* **실패 증상**: `price: 120`을 서명했으나 상대방 에이전트가 검증할 때 서명이 깨짐.
* **원인**: A2A 프로토콜 스펙상 JSON-RPC의 data Part를 거치면서 정수 `120`이 JSON 실수형인 `120.0`으로 파싱되어 역직렬화 시 바이트열이 변조되었습니다.
* **해결**: 서명 생성 및 검증 직전에 `int(round(float(price)))`로 정수형 변환을 명시적으로 보장하여 불일치를 해소했습니다.

### (3) Cloudflare Workers 엣지 라우팅 간 영수증 가격 불일치 버그 (`Agreed offer 186; receipt says 180`)
* **실패 증상**: 오늘 새벽 선착순 특가 판매 도중 바이어들로부터 `1★` 리뷰 3개가 연달아 찍힘:
  ```text
  {'stars': 1, 'review': 'Agreed offer 186; receipt says 180.'}
  ```
* **원인**: 
  * 셀러가 선착순 1.03배 특가로 `186원`을 제시하여 바이어가 `186`을 오퍼로 보냈습니다.
  * 그러나 두 번째 턴 요청이 Cloudflare의 다른 엣지 격리 인스턴스(Isolate)로 인입되면서, 인메모리 상태가 없는 새 인스턴스가 기본 원가인 `180원`으로 영수증을 발행해 버렸습니다.
  * 바이어 측의 엄격한 사기 방지 검증 로직(`if receipt.price != last_offer`)에 걸려 가격 변조로 간주된 것이었습니다.
* **해결**: 바이어가 원가 이상을 제안하여 체결될 경우, 셀러 측의 내부 계산값 대신 **바이어가 직전에 보낸 합의 제안가(`finalPrice = offer`)를 영수증에 100% 동일하게 날인**하도록 수정하여(`commit 854a638`), 가격 불일치 `1★` 발생을 완전히 차단했습니다.

---

## 5. 챌린지 1 & 2 수행 결과 (Challenges 1 & 2)

### 5.1 Challenge 1 (Shop: 동적 가격 책정 및 마켓 상황 적응)
* **목표**: 상점 자신의 거래 이력과 마켓 리뷰를 반영하여 최저가(Reserve)를 침해하지 않으면서 동적으로 가격을 적응.
* **적용 전략 (4단계 동적 상태 전이)**:
  1. *기준가*: 정가 1.50배(`lamp 150 / chair 270 / bookshelf 390`)
  2. *초고속 평점 복구 모드*: 평점 하락 시 첫 호가를 즉시 1.02배(`102 / 184 / 265`)로 인하하고 원가 즉시 수락.
  3. *마켓 Wave 감지 동적 할인*: 최근 3분 내 거래 3건 이상 또는 평점 4.1★ 이상 시 **영수증에 5~10% 랜덤 재방문 쿠폰 날인** 및 **재구매 시 5% 즉시 할인**.
  4. *1시간 유휴 선착순 4명 매출 극대화 특가*: 1시간 동안 손님이 없을 경우 4명 한정(1인당 최대 2개)으로 1.03배(`103 / 186 / 268`) 즉시 결제 유도.
* **전후 데이터 변화 (dashboard & logs)**:
  * 초기 고정가 대비: 거래량 **21건 → 186건** (+785% 폭증)
  * 누적 매출: **4,952원 → 35,576원** (+618% 증가)
  * 점유율(`share`): **0.1% → 0.9%** (상위권 진입)
  * 상세 로그 증빙: [`logs/challenge-1/challenge-1.log`](file:///c:/Users/clair/Desktop/Projects/ai-agent-engineering-101/submissions/25512085/week-07/logs/challenge-1/challenge-1.log)

### 5.2 Challenge 2 (Buyer: 부정/함정 상점 회피 및 품질 검증)
* **목표**: 마켓 내의 부정직한 상점이나 더미 함정 상점을 식별하고 로컬 메모리를 통해 회피.
* **적용 전략**:
  1. *더미 하우스 샵(`grand-furniture`) 회피*:
     * 700회 이상의 방문에도 거래 체결이 1건에 불과하고 터무니없는 가격(277원)을 요구하는 주최 측 더미 상점(`grand-furniture`)을 분석하여 후보군에서 완전 제외. 수백 번의 무의미한 예산 낭비를 차단.
  2. *무보증 덤핑 상점 감점 및 보증 상점 우대*:
     * 원가(100/180/260)에 판매하더라도 서비스, 품질보증, 환불 정책 키워드가 전혀 없는 상점은 품질 불량 의심으로 `3★`를 부여하고, 명확한 보증을 제공하는 상점에게는 `5★` 우대 리뷰를 부여.
  3. *불가능한 주문 100% 보류 (Hopeless Order Hold)*:
     * 원가 미만의 불가능 예산(책장 230원, 의자 160원, 램프 90원)을 철저히 식별하여 **292건의 불가능 주문을 단 1건도 시장에 날리지 않고 100% 보류(`held`)**.
  * 상세 로그 증빙: [`logs/challenge-2/challenge-2.log`](file:///c:/Users/clair/Desktop/Projects/ai-agent-engineering-101/submissions/25512085/week-07/logs/challenge-2/challenge-2.log)

---

## 6. 에이전트 배포 아키텍처 (Deployment Architecture)

마감 시점까지 중단 없는 연속 운영을 달성하기 위해 셀러와 바이어를 이원화 분산 배포했습니다:

```
[ Market Server (https://market.ourolabs.ai) ]
       │                               ▲
       │ A2A Purchases                 │ Long-polling Orders
       ▼                               │
[ Seller: Cloudflare Workers ]         [ Buyer: Local Machine Daemon ]
  - https://mjforge-shop.workers.dev     - market_kit.py join
  - 24/7 Global Edge Runtime             - while ($true) Auto-Reconnect
  - Web Crypto Ed25519 Signing           - Codex gpt-5.6-sol Harness
```

1. **상점 (Seller) - Cloudflare Workers 24/7 엣지 서버리스 배포**:
   * 로컬 PC가 절전 모드에 들어가거나 꺼지더라도 상점이 중단되지 않도록, 자바스크립트 V8 엣지 런타임인 **Cloudflare Workers**에 셀러 로직 전체를 포팅하여 배포했습니다 (`https://mjforge-shop.mjforge-worker.workers.dev`).
   * Web Crypto API(`crypto.subtle`)의 native Ed25519 알고리즘을 활용하여 외부 의존성 없이 1ms 미만의 지연 시간으로 영수증을 즉시 서명하도록 구현했습니다.
2. **구매자 (Buyer) - 자동 복구(Auto-Reconnect) 무한 루프 데몬 가동**:
   * 구매자는 마켓 서버와 롱폴링을 유지해야 하므로 로컬 환경에서 실행하되, 일시적인 DNS 순단(`getaddrinfo failed`)에 대비하여 **PowerShell 5초 자동 재접속 래퍼(`while ($true)`)** 데몬으로 감싸 무중단 가동을 보장했습니다.

---

## 7. 결론 (Conclusion)
Week 07 에이전트 마켓 과제를 통해 단순한 LLM 프롬프트 엔지니어링을 넘어, **상호 운용성 높은 통신 규격(A2A v1.0), 암호학적 신뢰 검증(Ed25519), 분산 시장의 메커니즘 디자인, 그리고 엣지 클라우드 기반의 무중단 인프라 구축**까지 완결된 자율 에이전트 경제 시스템을 성공적으로 구현하고 검증할 수 있었습니다.
