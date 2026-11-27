# [week-08] 26512071 — fair-furniture

> **스냅샷 기준**: 2026-11-17 17:55:41 KST, Round 3.
> 최종 결과는 2026-11-17 23:59 KST 마감까지 바뀔 수 있습니다. 아래 수치는 모두 이 시점의 값입니다.

## 0. 작성 주체와 제출 범위

- **초안 작성**: 이 보고서의 최초 원문과 [architecture.tex](architecture.tex)는 Claude Opus(`claude-opus-5-5`)가 작성했습니다. 이후 사실 교정과 아래 **2.1 전략·구현 변경 이력과 의사결정 근거**는 Hermes(`gpt-6.1-sol`)가 기존 개발 이력·운영 수용 기록을 읽고 보완했습니다. 이 보완을 Opus의 새 작성물이나 새 전략 실험으로 표시하지 않습니다.
  - 최초 Opus 초안 작성자는 셸, 네트워크, 파일 쓰기를 쓰지 않았습니다.
  - 근거는 전달받은 실제 소스 바이트와 증거 JSON(`ranks.json`, `paid-proof.json`, `log-excerpts.json`)뿐입니다.
  - 증거 수집, 빌드, 테스트는 Hermes가 맡았습니다.
- **제출 스냅샷**: upstream main 위의 별도 worktree에서 준비했습니다. 실행 중인 런타임은 건드리지 않았습니다.
- **kit 경계**:
  - 정산(checkout, payment, settlement)은 kit v4가 소유하며, 공유 스크립트는 수정하지 않았습니다.
  - 실험용 durable checkout 후보는 **설치하지도, 배포하지도 않았습니다**.
- **포함한 것**:
  - 현재 루트의 `.py`/`.ps1`, fixture, 선택 사항인 `tool_forge`
  - 실행 중이던 Windows 배포에서 스냅샷 시점에 그대로 복사한 로그 두 세그먼트
    - [logs/market-kit.log](logs/market-kit.log): 현재 세그먼트, 5.3MB
    - [logs/market-kit.log.1](logs/market-kit.log.1): 회전된 직전 세그먼트, 20MB
  - 원본 해시: [logs/submission-20261117/source-snapshot.json](logs/submission-20261117/source-snapshot.json)
  - 표준 API 키, GitHub 토큰, JWT 패턴 보안 스캔에서 걸린 것이 없어 치환은 하지 않았습니다. 합성 로그도 없습니다.
- **제외한 것**: private PEM, `.market`, auth, worklog
- **로그의 한계**: 그 이전에 회전되어 사라진 로그는 확보할 수 없습니다. 따라서 이 로그는 **라운드 전체 이력이 아닙니다**.
- **개발 이력**: 원 브랜치에 rewrite나 squash 없이 그대로 남아 있습니다. 설명용 export는 [development-history.txt](logs/submission-20261117/development-history.txt)입니다. 제출 브랜치는 다른 주차의 변경과 운영 상태를 섞지 않기 위한 별도 스냅샷이며, 원 개발 커밋을 squash/rebase한 것이 아닙니다.
- **테스트 상태: 실행 완료, 전체 통과 아님**
  - 실제 실행 결과와 실패 범위는 아래 Hermes 검증 절에 기록했습니다.
  - 현재 upstream에는 `check_week08.py`가 없습니다. week07의 점검 구조는 중간고사 합격 기준이 아닙니다.

## 1. 모델 · 컨텍스트/메모리 · 도구 (40%)

이 장의 내용은 **소스 설계**입니다. 런타임에서 실제로 무슨 일이 있었는지는 3장의 원장과 로그로 따로 보입니다.

### 1.1 모델 배치

| 역할 | 모델 | 근거 | 출력 형식 |
|---|---|---|---|
| 판매 협상 한 턴 | `gpt-6.1-terra` | [harness.py](harness.py) `SELLER_MODEL` | JSON counter/accept/reject |
| 구매 협상 한 턴 | `gpt-6.1-terra` | [buyer_terra.py](buyer_terra.py) `MODEL` | JSON counter/accept/leave |
| SKU별 가격 정책 (주기적) | `gpt-6.1-sol` | [sol_policy.py](sol_policy.py) `MODEL` | JSON `{skus:{…}}` |
| 재입고, 리뷰 별점 | 모델 없음 (결정론적 코드) | [restock_r3.py](restock_r3.py), [review_r3.py](review_r3.py) | — |
| 이 보고서와 그림 | `claude-opus-5-5` | — | 런타임 구성요소 아님 |

**확인되지 않았거나 낡은 부분**

- **buyer 모델 주석이 낡음**: `harness.py` docstring에는 buyer가 nano를 유지한다고 적혀 있습니다. 그러나 실제 [buyer_r3.py](buyer_r3.py)는 `DECIDE=buyer_terra.terra_decide`로 Terra를 부릅니다. 이 주석은 stale입니다.
- **Sonnet**: 레거시 파일이 남아 있을 뿐, 현재 런타임 모델이 아닙니다.
- **Sol 활성화 여부**: `sol-policy-config.json`의 `enabled=true`와 `restock_r3.approved()` 해시 게이트가 **둘 다** 성립할 때만 켜집니다. 스냅샷 시점에 게이트가 참이었는지는 이 보고서에서 검증하지 않았습니다. 받은 로그 발췌에도 `[sol]` 행은 없습니다.
- **reasoning effort `none`**: `buyer_terra.py` 주석에 `openai_harness`에서 설정한다고 되어 있습니다. 하지만 `openai_harness.py` 본문은 제가 받은 자료에 없어 확인하지 못했습니다.

### 1.2 컨텍스트 설계

**구매자 (Terra)**

- 모델이 받는 것은 `facts()`의 **숫자뿐**입니다: SKU, catalog list price, budget cap, ask 이력, 보낸 offer, 남은 메시지·초·호출 수.
- 판매자 문장은 프롬프트에 들어가지 않습니다. ask는 코드가 정규식으로 숫자만 뽑아냅니다.
- `parse_decision`의 거부 조건:
  - 키가 `action`, `offer` 외에 더 있음
  - offer가 int가 아님
  - cap 초과, 최신 ask 초과
  - 직전 offer보다 낮아짐(후퇴)
- 거부되거나 오류가 나면 재시도하지 않고 다른 모델도 쓰지 않습니다. 그 상점의 나머지 협상은 bounded-v2 결정론 규칙으로 넘어갑니다.

**판매자 (Terra)**

- 프롬프트에는 품목명, list price, 원가 하한, 이번 task의 가격 앵커와 라벨, 상한, 대화 기록이 들어갑니다.
- 대화 기록에는 구매자 원문이 포함되므로, 판매자 모델 입력은 **비신뢰 입력**입니다. 대신 출력이 엄격히 묶여 있습니다.
  - `model_price`가 int만 받고 `[cost, 직전 ask]`로 clamp합니다.
  - accept 전에는 재고를 다시 읽습니다. 원가 미만 accept는 counter로 바뀝니다.
- **결정론 fast path**: 단순 문의, 정수 offer, 단순 수락은 모델을 부르지 않습니다. 조건, 부정, 보증, 쿠폰이 섞인 문장은 harness로 갑니다.

**Sol**

- 사실값은 전부 코드가 계산합니다. 근거가 되는 비교 대상이 없으면 `missing=true`로 명시합니다.
- 응답 처리:
  - `validate`가 원가~list 범위로 clamp하고, clamp 내역을 기록합니다.
  - 반환 모델명이 정확히 일치하지 않으면 정책으로 받지 않습니다.
- 호출 제한:
  - 요청 **전**에 journal에 먼저 기록하고, 롤링 24h 호출 상한(기본 24회)에 셉니다.
  - TTL은 120분입니다.
- 실패하면 마지막 유효 정책을 쓰고, 그것도 없으면 baseline을 씁니다.

### 1.3 영속 메모리

[evidence_memory.py](evidence_memory.py)의 `sync()`가 하는 일:

- **입력 파일**: `payments.jsonl`, `deal.json`, `broken.jsonl`, 구매 주문. 완결된 줄만 다시 스캔합니다.
- **결제 구매**: ledger id를 키로 `setdefault`합니다. 같은 행이 다시 들어오거나 로그가 회전돼도 중복이 생기지 않습니다(**idempotent**).
- **거절 기록**: `(order, shop, item, reason)`의 지문을 키로 저장합니다.
- **deal.json 보강**: 핵심 사실(order, shop, sku, price, ledger)이 일치할 때만 grade, claim_honest, warranty_hours 같은 필드를 덧붙입니다.
- **동시성**: 디렉터리 락을 쓰고, 오래된 락을 훔치지 않습니다. 쓰기는 fsync를 동반한 atomic replace입니다.

메모리를 읽는 곳:

- **다음 구매자**: `candidates()`의 상점 점수(신뢰도, price/list 비율, 품질 증거)
- **재입고**: local-biased 표본이라는 라벨을 붙여 따로 씀
- **결제 후 리뷰**: `review_r3`가 리뷰 전에 `sync()` 호출
- **판매자 개시가 상한**: 판매자는 자기 원장 파일(`one-day-pilot-ledger.json`)에서 직접 12h 결제가를 읽습니다(`recovery-open-v1`).
- **공개 원장 캐시**: 실제 `kind=settle` 행의 `data.amount`를 파싱합니다.

메모리의 한계:

- 이 표본은 **우리 주문이 만든 편향 표본**입니다. 시장 수요가 아닙니다.
- `demand.json`의 카테고리 커버리지는 **인기도가 아닙니다**.
- `broken.jsonl` 행은 정확한 구매 시점과 연결할 수 없어 `unresolved`로 남깁니다.

### 1.4 도구, 권한, 예산

- **런타임 모델 호출에는 도구가 없습니다.** 메시지 전송, 서명, checkout, 재입고 주문은 모두 코드가 합니다.
- **`tool_forge`**:
  - 제한된 AST 코드의 테스트·해시 기반 등록을 구현한 opt-in 실험 라이브러리입니다. ACP descriptor로 checkout 도구를 만드는 기능은 아닙니다.
  - 소스에는 포함되어 있지만 checkout 경로가 아니고, 자동 배포된 에이전트도 아닙니다.
  - OS 샌드박스를 제공한다고 주장하지 않습니다.
- **판매자 신원 확인**:
  - 레지스트리 URL로 공개키를 고릅니다.
  - 그 키로 정규화된 `{shop,item,price,task}`의 Ed25519 서명을 검증합니다.
  - 서명 안의 표시 이름은 레지스트리 닉네임과 달라도 됩니다. 예: 3장의 `Sungjin Shop` ↔ `ax-week07.treasuresky.xyz`.
  - wire에서 정수값 float(120.0)는 검증 전에 int로 복원합니다. kit과 같은 처리입니다.
- **구매자 예산**:
  - 상점 최대 3곳, 서명 견적 최대 2개 비교
  - 주문당 모델 호출 6회, 상점당 3회
  - 호출 1회 최대 20초
  - 230초가 지나면 새 시도를 멈춥니다. kit의 주문당 약 300초 중 checkout 여유분을 남기기 위해서입니다.
- **선택되지 않은 서명 영수증**: 결제하지 않고 남겨둡니다.

## 2. 독창적 아키텍처 (30%)

[architecture.tex](architecture.tex)는 세 칸으로 그렸습니다.

- **왼쪽**: 구매자
- **가운데**: kit v4와 신뢰 경계
- **오른쪽**: 판매자와 재입고
- **아래**: 영속 메모리. 결제·거절·정산 행이 다시 위의 결정으로 돌아갑니다.

kit이 제공하는 것은 레지스트리, checkout, 결제, 정산뿐입니다. 아래는 모두 우리가 설계한 부분입니다.

1. **숫자만 보는 구매 모델 + 2견적 비교**
   - 모델은 판매자 문장을 보지 않습니다.
   - 서명 검증을 통과한 견적 중 price/list가 가장 낮은 것을 고릅니다.
   - price/list는 절감액의 대리지표입니다. 공식 Saved 지표가 아닙니다.
2. **판매가 3단 정책** (뒤로 갈수록 개시가를 낮추기만 함)
   - 1단: 공개 캐시 기반 baseline 앵커
   - 2단: 게이트가 열렸을 때만 Sol 정책을 덮어씀
   - 3단: `recovery-open-v1`. 최근 12h 자사 결제가 3건 이상의 `median_low`로 개시가에 상한을 둡니다.
     - grade와 과거 보증 조건이 섞인 **휴리스틱**입니다. 자격 있는 비교 대상이 아닙니다.
3. **보증 정책 `warranty-0h-new-v1`**
   - 신규 견적은 모두 0h입니다.
   - 보증을 **요구**하는 구매자 문장에는 서명 없이 거절합니다. 0h 영수증으로 대신하지 않습니다.
   - 과거에 72h 의도로 잡힌 거래는 그대로 발행합니다.
   - 이미 발행된 보증 권한 **64건은 보존**합니다. 면제하지 않았습니다.
4. **재입고 (모델 없음)**
   - `bounded-v2`와 one-day pilot을 씁니다.
   - `demand-cover-v1`: 증거가 있는 수요의 약 6시간분을 목표로 합니다.
   - `PER_CALL=20`은 공급 프로토콜의 호출당 수량 제한이지 SKU별 총 보유 재고 상한이 아닙니다.
   - 미확인 공급 행은 정확한 결과가 나올 때까지 전액 예약 상태로 둡니다. 시간이 지났다고 풀지 않습니다(TTL 없음).
   - 실제 one-day-pilot 설정의 상태:
     - seq8 이후 `financial_limits`가 꺼져 있습니다.
     - 시뮬레이션용 재무 한도는 제거했습니다.
     - 만료 시각 2026-11-17 23:59 KST는 그대로입니다.
     - 부채와 미확인 행은 보존합니다.
   - `agent.json`의 harness 설명 문장은 이 상태를 반영하지 못한 **stale** 문장입니다.
5. **리뷰와 포럼**
   - 별점은 정산된 grade와 `claim_honest`로 결정론적으로 매깁니다.
   - 포럼 게시는 `forum_terra`/`forum_r3`의 opt-in 부수효과이며, 리뷰 내용을 바꾸지 못합니다.
   - 최초 Opus 입력에는 포럼 소스가 없었습니다. 제출본에는 실제 소스를 포함했고, 결합 상한 관련 테스트 실행은 아래 검증 절에서 별도로 보고합니다.
   - 큐에 들어간 것은 게시된 것이 아닙니다.

### 2.1 전략·구현 변경 이력과 의사결정 근거

아래는 최종 구성만 설명하는 대신 **무엇이 막혔고, 왜 기존 방법을 버렸는지**를 복원한 기록입니다. 날짜는 KST 개발 커밋·수용 기록 기준이며, 같은 날의 변경도 별도 의사결정으로 구분합니다. 검증된 동작과 성능 가설을 섞지 않습니다.

초기 변경은 [원 개발 이력](logs/submission-20261117/development-history.txt)의 커밋으로, 이후 미커밋 운영 변경은 로컬 수용 기록에서 필요한 사실만 투영한 [decision-evidence.json](logs/submission-20261117/decision-evidence.json)으로 출처를 구분했습니다. 후자는 원시 영수증이나 이번 작업의 독립 재정산 결과가 아닙니다. 비공개 worklog 전체를 올린 것처럼 링크하지 않았습니다.

#### ① 11/02–03 — 구형 품목 전략을 바로 재사용하지 않고 v4 SKU 거래로 전환

- **문제·이유:** week07의 lamp/chair/bookshelf와 고정 reserve는 실물 SKU·매입 원가·유통기한이 있는 v4에 맞지 않았습니다. 연결만 되었다고 자동 매입을 허용하면 시험도 하기 전에 자본을 소모할 수 있었습니다.
- **변경:** 최초에는 명시적 no-buy/abstain으로 등록을 확인한 뒤, 재고를 읽는 [판매자](seller_r3.py)·[구매자](buyer_r3.py)·[재입고](restock_r3.py)의 bounded baseline으로 옮겼습니다.
- **검증·한계:** `be55a07`, `6d53d14`, `3a316be`에는 안전 가드와 배포 차단 상태가 남아 있습니다. 이 시점의 등록 성공을 실거래 완료나 전략 완성이라고 부르지 않습니다.

#### ② 11/03–04 — 일회성 로그에서 다음 결정을 바꾸는 영속 메모리로

- **문제·이유:** 과거 가격·품질·거절을 매번 잊으면 같은 부적합 상점 선택을 반복합니다. 반대로 단순 누적 로그는 다음 hook이 읽지 않으면 학습 경로가 아닙니다.
- **변경:** [evidence_memory.py](evidence_memory.py)에 paid/refused를 중복 없이 저장하고, buyer·review·restock에서 읽도록 연결했습니다. 로컬 주문 수와 실제 결제 수를 분리했습니다.
- **검증·한계:** `3cc4875`, `83ce8f6`은 저장과 hook 연결을 구분해 남겼습니다. 메모리 항목 증가가 품질 향상의 증거는 아니며, 자기 구매 표본을 전 시장 인기도로 해석하지 않습니다.

#### ③ 11/04 — 협상 성공처럼 보이던 서명·RPC 경계 오류 수정

- **문제·이유:** 숫자가 같아 보여도 wire의 `120.0`과 kit의 `120`은 서명할 JSON 바이트가 다를 수 있습니다. A2A 루트 주소에 보내는 것과 카드가 광고한 RPC 경로에 보내는 것도 같지 않았습니다.
- **변경:** kit과 같은 정수 정규화, 원 task 고정, 광고된 RPC 경로 사용을 적용했습니다. 가격을 잘 깎는 것보다 **정상 영수증이 실제 checkout까지 통과하는 것**을 먼저 복구했습니다.
- **검증·한계:** `00611a9` 재현 → `2be11e2`, `7146a75` 수정 → `baecaa4` 실제 kit canonicalization 검증이 남아 있습니다. 프로토콜 수정은 가격 정책의 우수성 증명이 아닙니다.

#### ④ 11/05 — 공개 가격이 있어도 쓰이지 않던 캐시·수집 경로 교정

- **문제·이유:** 이전 baseline 앵커의 재사용이 새 비교 가격을 가릴 수 있었고, cursor의 최댓값만으로는 중간 원장 누락을 알 수 없었습니다. Windows에서는 응답 본문 종료 뒤 닫힌 소켓에 `settimeout`을 호출하는 오류도 재현됐습니다.
- **변경:** [public_evidence.py](public_evidence.py)의 앵커 버전·증거 tier·freshness를 구분하고, 누락 구간을 유지하며, 제한된 fetch/backoff와 닫힌 소켓 처리를 넣었습니다. 비교 자료가 없으면 proxy/fallback임을 표시했습니다.
- **검증·한계:** `0ffcd51`, `2471b34`, `6f63fdd`, `6f1ba67`에 단계별 교정이 있습니다. 마지막 실제 fetch timeout도 남아 있으므로 “수집 문제가 모두 해결돼 항상 시장가를 쓴다”고 주장하지 않습니다.

#### ⑤ 11/06 이후 — 미확인 공급을 지우지 않고 제한된 위험 예외로 분리

- **문제·이유:** 과거 공급 11건(245,080)은 원 요청에 결속할 정보가 부족했습니다. 시간이 지났거나 현재 재고에 없다는 이유로 예약을 풀면 이중 지출 가능성을 숨기게 됩니다. 그렇다고 매번 같은 복구 검색만 반복하면 정상 매입도 멈춥니다.
- **변경:** 원 UNKNOWN을 동결하고, 별도 승인된 pilot에 누적 매입·동시 노출·손실·현금 바닥·만료를 두었습니다. 재시도나 한도 증액으로 이전 사용액을 초기화하지 않았습니다.
- **검증·한계:** `53711e5`, `c2fc756`, `0cd60e1`은 분리된 pilot과 실제 공급 확인을 기록합니다. 11/14 수용 기록에도 원 11건은 그대로입니다. 이는 위험을 명시한 운영 재개이지, 과거 미확인 거래의 복구 완료가 아닙니다.

#### ⑥ 11/12 — 매 턴 협상과 SKU 가격 정책을 서로 다른 시간축으로 분리

- **문제·이유:** 한 턴의 counter/accept/reject와 여러 SKU의 시작가·목표가·하한 검토는 필요한 컨텍스트와 호출 빈도가 다릅니다. 같은 모델 설명 문장만 고쳐서는 실제 실행 경로가 바뀌지 않습니다.
- **변경:** [sol_policy.py](sol_policy.py)는 제한된 주기·TTL의 SKU 정책을, [harness.py](harness.py)의 Terra는 개별 판매 협상을 맡았습니다. 이때 buyer는 아직 결정론적이었으며, 나중 buyer 전환과 혼동하지 않습니다.
- **검증·한계:** 11/12 실제 공개 진단 견적에서 Sol 시작가 11,850이 baseline 10,720 대신 사용됐고 Terra/Sol returned model과 usage가 기록됐습니다. 취소한 견적이지 결제 사례가 아니며, 더 높은 시작가가 이익을 늘렸다는 뜻도 아닙니다.

#### ⑦ 11/14 — 순위 정렬만 바꿔도 남던 무판매 스프레이 추가 매입 차단

- **문제·이유:** 스프레이는 이미 꼴찌였지만, 남은 room 13,105에는 브라우니 상한 13,388이 안 들어가고 스프레이 상한 4,343×3은 들어갔습니다. greedy sizing이 싼 잔여 후보를 계속 샀으므로 단순 재정렬은 해법이 아니었습니다. 당시 스프레이 180개·원가 415,770, 자체 확정판매 0건이었습니다.
- **변경:** [microeconomics.py](microeconomics.py)에서 **이미 보유 중이고 자체 판매 0이며 공개 증거 승격도 없는** SKU의 추가 매입만 제외했습니다. 최초 탐색과 근거 있는 승격은 유지했습니다.
- **검증·한계:** 17:29:23 정상 hook에 `held_unsold_no_realized_sale`, `buy_candidate=false`가 나타났습니다. 새 매입 차단은 확인했지만 기존 415,770의 자본을 회수하거나 수요를 만든 것은 아닙니다.

#### ⑧ 11/14 — “들어가는 만큼 구매”에서 수요 커버리지 기반 수량으로

- **문제·이유:** 한도가 남았다는 것은 수요가 있다는 뜻이 아닙니다. 저장된 재현 사례에서는 12h 판매 1건·재고 44개인 SKU에도 기존 계산이 20개를 더 배정했습니다.
- **변경:** `demand-cover-v1`은 자체 exact paid 속도와 자격 있는 공개 per-shop 속도로 약 6h분 목표를 잡고, 이미 보유한 수량과 실제 미확정 공급을 빼도록 했습니다.
- **검증·한계:** `45b17d1` 재현 → `06bd638` 수정 → `dc52e80` 설치 → `3b9a72c` 정상 hook의 소량 매입 기록을 남겼습니다. 6h와 공개 속도 prior는 실험 가정이며 미래 수요 예측의 정확도가 검증된 값은 아닙니다.

#### ⑨ 11/14 — 고정 계단 흥정에서 실제 Terra 판단 + 두 서명 견적 비교로

- **문제·이유:** 기존 buyer는 “흥정을 전혀 안 한 것”은 아니었습니다. 고정 paid 500건 중 333건이 첫 호가보다 낮고 167건은 동가였으며, 거래별 첫 호가 할인 평균은 14.2269%였습니다. 그러나 판매자가 낮춘 역호가를 빨리 수락하고 첫 서명 상점에서 끝나므로 현재 다른 상점 가격을 비교하지 못했습니다.
- **변경:** [buyer_terra.py](buyer_terra.py)에 숫자 컨텍스트만 받는 실제 모델 판단을 넣고, 최대 3상점에서 유효 서명 견적 2개를 모아 비교했습니다. 호출·시간·예산 제한과 오류 시 결정론 fallback을 유지했습니다. 보내지 않은 offer를 보냈다고 세던 journal과 표시 이름 동등성 가정도 교정했습니다.
- **검증·한계:** 원장 `6473174`에서 첫 후보 409,448 대신 375,424, `6473646`에서 17,516 대신 15,825를 선택·결제했습니다. 이는 **새 정책 안의 후보 비교**입니다. 구 buyer와 동일 주문 A/B가 아니고, offline에는 모델 75,000 대 baseline 70,000인 반례도 있어 장기 Saved·순위 개선을 단정하지 않습니다.

#### ⑩ 11/15 — 유효 Sol 하한을 넘은 제안에도 다음 counter를 기다리던 지연 제거

- **문제·이유:** 기존 5%-of-list 다음 역호가 기준은 원가와 유효 전략 하한을 이미 만족한 제안까지 다시 올려 부를 수 있었습니다. 회전이 중요한 상황에서는 그 추가 마진 시도가 합의를 늦출 수 있었습니다.
- **변경:** [seller_r3.py](seller_r3.py)에서 기존 ask가 있는 단순 정수 제안이 `max(개별 원가+1, 유효 Sol min)` 이상이면 빠르게 수락하도록 했습니다. 복잡한 조건 문장은 모델 경로로 남겼고, 하한을 낮추지는 않았습니다.
- **검증·한계:** 관련 132개 테스트 통과 기록이 있습니다. 후속 실제 결제 `6728316`은 구 규칙도 수락했을 사례여서 새 shortcut의 증분 매출 증거가 아닙니다. 더 기다리면 받을 수 있었을 높은 가격을 포기할 수 있다는 trade-off도 남습니다.

#### ⑪ 11/15 — 경쟁력 가설로 시작한 보증 확대에서 신규 0h·현금 회복 견적으로

- **문제·이유:** 72h 보증은 경쟁 상점 조건을 따라가려는 가설이었지만, 구 kit에는 발행 권한의 정확한 종료를 증명할 경로가 부족했습니다. 이미 발행한 64건·예약 973,081을 TTL이나 취소만으로 해제할 수 없어 재무 여유를 계속 압박했습니다.
- **변경:** 신규 보증을 무조건 0h로 하되 기존 약속은 보존했습니다. 신규 시작가는 최근 12h 자기 exact-SKU 결제 3건 이상의 `median_low`로 제한하고, 개별 원가와 유효 Sol min을 지켰습니다. 보증을 명시 요구하면 0h로 슬쩍 대체하지 않고 서명 없이 거절합니다.
- **검증·한계:** 공개 진단에서 오일 29,700→23,760, 베개 10,600→8,834와 0h·취소 readback을 확인했습니다. 당시 관찰창의 신규 자연 결제는 0건이었습니다. 과거 등급·보증이 섞인 중앙값은 휴리스틱이지 동일 조건 시장가가 아니며, 신규 0h가 구 부채를 없애거나 보관료를 멈추지도 않습니다.

#### ⑫ 11/16 — 실제로 막는 재무 항목을 확인하고 승인 범위별로 제한 해제

- **문제·이유:** 누적 매입 한도를 늘려도 동시 노출·현금 바닥·손실+보증 중 다른 항목이 묶이면 매입은 여전히 0입니다. 특히 구 보증 예약 때문에 위험 한도가 먼저 막혔습니다. 사용자 승인도 “위험 한도만”과 “시뮬레이션 재무 한도 전체”는 달랐습니다.
- **변경:** seq7은 combined loss ceiling만, 뒤의 명시적 승인인 seq8은 `financial_limits='disabled'`로 시뮬레이션 재무 제한 전체를 해제했습니다. 무한대나 거대한 숫자로 우회하지 않았고, 원 부채·누적 사용·이전 intent·11/17 23:59 만료와 영수증·주문예산 검증은 보존했습니다. 실제 API 과금 한도는 이 승인과 별개입니다.
- **검증·한계:** seq7 정상 hook은 제한 해제 후에도 공급창이 닫혀 abstain했습니다. seq8 관찰창에는 49개·1,059,231의 정상 공급과, 신규 원가 221,083 상품을 380,645에 판 원장 `7174863`이 확인됐습니다. 차액 159,562는 **보관료 전 매출총기여** 한 사례입니다. 그 관찰창 Players 46/64·Class 38/46은 그대로였고, 이후 본 보고서의 17:55 순위 변화도 이 정책 하나의 인과 효과로 돌리지 않습니다.

**실패한 가정에서 남긴 세 가지 교훈**

1. **자본 확대 ≠ 회전 개선:** 후보의 수요·현재 보유량·greedy 잔여 배정을 함께 봐야 합니다. 순위만 바꾸거나 한도 숫자만 올려도 무판매 재고는 줄지 않았습니다.
2. **새 정책 ≠ 과거 의무 소멸:** 보증 중단·타임아웃·재시작은 기존 서명 권한의 종료 증거가 아닙니다. 미확인 부채를 지워 성과를 좋아 보이게 하지 않았습니다.
3. **고급 모델 ≠ 성과 보장:** 실제 returned model, 정상 hook, exact paid는 각각 다른 검증 단계입니다. Fable의 설계 조언, Opus의 구현, Terra/Sol의 운영 판단, Hermes의 이번 문서 보완을 구분하며, 정상 결제가 확인돼도 대조 실험 없는 순위 인과는 주장하지 않습니다.

## 3. Round 3 결과 (30%)

### 3.1 기본 요건: 판매 20건, 구매 20건 실결제

**충족했습니다.** 공개 `/api/ledger`의 정확한 seq로 대조했습니다.

- 판매 30건, 구매 30건이 양수 금액의 `kind=settle` 행으로 확인됐습니다.
- 누락은 `[]`, `r3_20_each: true`입니다.

발췌:

| 역할 | seq | 금액 | 상대 | 비고 |
|---|---|---|---|---|
| 판매 | 7758980 | 231,894 | chaewoonee | 매트리스, claim A, honest |
| 판매 | 7784280 | 206,528 | chaewoonee | 로그 `settled … ledger #7784280`과 일치 |
| 판매 | 7785482 | 10,331 | nocha12 | 식품: claim 없음 (코드 설계대로) |
| 구매 | 7781518 | 112,000 | ax-week07.treasuresky.xyz | 판매자 측 warranty 1.0h |
| 구매 | 7786199 | 408,733 | ax-week07.treasuresky.xyz | 로그 `paid 408733 … #7786199`와 일치 |
| 구매 | 7786287 | 70,000 | docs-quotes-…trycloudflare.com | grade B, 유통기한 447.9h 남음 |

### 3.2 스냅샷 순위와 지표

**순위**

- 전체 플레이어: 44/64위 (동점 2)
- 학생 클래스: 35/46위
  - `student_total` 0.768, 공식 `student_pct` 0.244
- shop: 41/61위 (`all` 0.422)
- buyer: 45/68위 (`all` 0.543)

**판매 (shop)**

| 지표 | 값 |
|---|---|
| 거래 | 1,832 |
| 방문 | 5,217 |
| 별점 | 4.38 |
| 매출 | 87,664,248 |
| Cash (`profit`, 순현금: 잔고−초기자본−파산지원금) | **−4,427,316** |
| 재고 | 311개, 원가 13,574,717 |
| 장부가 | 9,147,401 |
| 환불 | 1 |
| 파손 | 45 |
| claim | 496건 모두 honest |
| 파산 | 0 |

**구매 (buyer)**

| 지표 | 값 |
|---|---|
| 주문 | 52,104 |
| 거래 | 30,462 |
| paid | 0.599 |
| saved | 0.6796 |
| served | 29,416 |
| forum | 0.905 (게시 96건) |

### 3.3 실패, 미확정 결과, 한계

- **순현금(Cash)이 음수**입니다. 이는 실현 매출총이익이 아니라 재고 구입도 지출로 포함한 값입니다. 재고 311개, 원가 1,357만에 자본이 묶여 있고 Book은 양수입니다.
  - 이 재고가 마감 전에 팔릴지, 그 가격이 얼마일지는 알 수 없습니다.
- **파손 45건, 환불 1건**이 있습니다.
- **`paid=0.599`는 성공 확률이 아닙니다.** 공식 board 설명의 거래별 가격/주문 예산 평균입니다. `saved=0.6796`은 공식 마진 몫 기반 Saved이며 첫 호가 할인율이나 price/list와 다릅니다.
- **합의와 결제는 분리**합니다. `checkout ships`나 서명 영수증만으로 미결제/성공을 판정하지 않고, 이 제출에서는 명시적인 `[acp] paid`/`settled`의 정확한 seq만 공개 원장과 대조했습니다. 선택한 60개 참조가 전체 라운드의 전수 재검증을 뜻하지는 않습니다.
- **보증 권한 64건과 미확인 공급 행**이 아직 열린 의무로 남아 있습니다.
- **인과 주장을 하지 않습니다.**
  - 정책 변경(Terra, Sol, recovery cap, demand-cover)이 순위를 올렸다는 비교 실험은 하지 않았습니다.
  - 따라서 개선을 주장하지 않고, 성능 우위도 보장하지 않습니다.
- **이 제출 작업에서 하지 않은 것**:
  - 유료 투표나 대출을 실행하지 않았습니다. 과거 전체 계정 활동을 전수 감사했다는 뜻은 아닙니다.
  - 실험 도구의 배포 주장도 하지 않습니다.

### 3.4 Week 10 당시 기록에서 복원한 기준값과 제출 시점 비교

> **2026-11-23 Hermes 보완:** Week 10 LAB의 “기준 숫자와 지금 숫자를 나란히 적는다”는 항목을 보완하기 위해, 11/10에 실제 저장한 운영 기록과 기존 제출 스냅샷을 대조했습니다. **당시 실습에서 사전 지정한 지표라는 원본 메모는 찾지 못했으므로, 아래는 사전 등록 실험이 아니라 당시 기록에서 복원한 비교입니다.**

- **대상 행동:** 주문을 기다리지 않고 실행되는 재입고 hook에 `microecon-v1`을 연결하여, 기존 매입 후보의 순서를 자체 실결제 기여·자본 회전 증거로 재배치했습니다. 11/10 19:59:33 KST 정상 hook 기록에 `policy_version=microecon-v1`, `decision_reason=one_day_pilot_chosen`이 있습니다. 공개 comparable이 없어 실제 공개가격 활용은 확인되지 않았습니다.
- **측정 시점:** 기준은 **2026-11-10 19:59:09 KST** 배포 근방 보드이며, 이후는 **2026-11-17 17:55:41 KST** 기존 제출 스냅샷입니다. 기준은 변경 직전 통제군이 아니며, 이후 값도 23:59 최종 마감값이 아닙니다.
- **누적 판매 정산 수 (`shop.deals`): 288건 → 1,832건.**
- **누적 매출 (`shop.revenue`): 4,096,611 → 87,664,248 크레딧.**
- **장부가 (`shop.book`): 865,534 → 9,147,401 크레딧.** Book은 Cash에 보유 재고 원가를 더한 값이며 실현 판매이익이 아닙니다.
- **근거:** [week10-comparison.json](logs/submission-20261117/week10-comparison.json)에 당시 `board-deployment.json`과 `runtime-microeconomics-status.json`의 자기 상점 필드·측정 시각·정상 hook 필드 및 원본 SHA-256을 투영했습니다. 이후 값은 기존 [board.json](logs/submission-20261117/board.json)의 동일 상점·동일 필드입니다. 비공개 worklog 전체나 원시 로그를 새로 제출한 것으로 표시하지 않습니다.
- **해석 한계:** 같은 Round 3의 누적값 비교이며, 운영 기간·재고 확대·여러 정책 변경이 함께 반영됩니다. 이 증가를 재입고 규칙 하나의 인과 효과로 주장하지 않습니다. 11/12에 정의가 바뀐 Saved는 이 전후 비교에서 제외했습니다. 원래 Week 10 사전 지정 기준값이 확인된 것으로 간주하지 않으며, 강의 실습의 사전 기록 요건을 완전히 충족했다고 단정하지 않습니다.

## 4. 재현과 검증 상태

- **그림 빌드**: `tectonic architecture.tex`. 실제 Tectonic 0.17.0 컴파일과 PDF 페이지/렌더 검증을 수행했습니다.
- **테스트**: 실행 완료. 전체 discovery 실패를 포함한 결과는 아래 검증 절에 기록합니다.

## 참고 파일

**소스**

- [seller_r3.py](seller_r3.py)
- [buyer_r3.py](buyer_r3.py)
- [buyer_terra.py](buyer_terra.py)
- [harness.py](harness.py)
- [sol_policy.py](sol_policy.py)
- [restock_r3.py](restock_r3.py)
- [evidence_memory.py](evidence_memory.py)
- [review_r3.py](review_r3.py)

**그림**

- [architecture.tex](architecture.tex)

**로그와 이력**

- [logs/market-kit.log](logs/market-kit.log)
- [logs/market-kit.log.1](logs/market-kit.log.1)
- [logs/submission-20261117/source-snapshot.json](logs/submission-20261117/source-snapshot.json)
- [development-history.txt](logs/submission-20261117/development-history.txt)

**증거 JSON**

- `ranks.json`, `paid-proof.json`, `log-excerpts.json`
- 이 세 파일의 저장 경로는 상위 프로세스의 검증 섹션 기준입니다.

## 5. Hermes 독립 검증 및 원문 교정 (2026-11-17 KST)

Opus 원문을 유지하되 이 제출본에서 사실 오류를 교정했습니다: `profit`은 실현이익이 아니라 Cash, `paid`는 성공률이 아니라 가격/예산 평균입니다. `tool_forge`는 제한된 AST 도구 등록 실험이며 ACP 동적 checkout 도구가 아닙니다. 그림의 실험 경계와 이력 링크도 교정했습니다. 최초 Opus 산출물 해시는 [opus-authorship.json](logs/submission-20261117/opus-authorship.json)에 있어 수정 후 파일 해시와 구별됩니다.

### 실제 실행 결과

- 현재 경로의 구매자·메모리·공급 journal·Sol·재무 한도·신규 0h 보증·가격 회복·microeconomics·포럼 결합 상한 등 **선별 suite: 182개, 1 skipped, exit 0**. [tests-focused.log](logs/submission-20261117/tests-focused.log).
- 선택 실험인 **Tool Forge: 14개, exit 0**. [tests-2.log](logs/submission-20261117/tests-2.log).
- 첫 넓은 선별 실행: **192개, errors 6, skipped 1, exit 1**. `test_seller_warranty0h_supersede_v1.SellerFlow0h`의 `self.sellers[-1]` fixture 오류를 보존했습니다. 실패한 실행을 삭제하지 않았습니다. [tests-1.log](logs/submission-20261117/tests-1.log).
- 전체 root discovery: **721개, failures 12 / errors 9 / skipped 1, exit 1**. 구형 72h 보증 기대값, 일부 smoke/fast-path assertion, 제외한 운영 fixture/마이그레이션 파일 등에 걸린 오류가 함께 있습니다. 모든 실패 원인을 해결하거나 재현 환경을 완전히 복원한 것은 아닙니다. **전체 테스트 통과를 주장하지 않습니다.** [tests-3.log](logs/submission-20261117/tests-3.log), [실행 명령과 결과](logs/submission-20261117/test-results.json).
- 현재 제출 Python **102개 AST 구문 검사 통과**. [security-scan.json](logs/submission-20261117/security-scan.json). API 키·GitHub 토큰·claim code·JWT·개인키·Bearer literal·AWS/Slack 토큰 패턴에서 검출 0건입니다. 패턴 검사는 완전한 보안 감사를 뜻하지 않습니다.
- 실제 운영 소스와 제출 루트 코드의 SHA-256을 비교해 **불일치 0개**, 제출 로그 두 파일의 snapshot SHA-256도 일치했습니다. 운영 소스를 고치거나 시장을 재시작하지 않았습니다.
- 공유 `scripts/market_kit.py`는 운영 checkout과 upstream 제출 checkout의 SHA-256이 동일했습니다. 별도 kit 변경은 제출하지 않습니다.
- 실제 **Tectonic 0.17.0**으로 `architecture.tex`를 컴파일했습니다. PyMuPDF로 A4 가로 **1페이지**를 확인하고 실제 PDF 렌더를 눈으로 검사했습니다. 그림·화살표·캡션의 겹침/잘림이 없음을 확인했습니다. [architecture.pdf](architecture.pdf), [빌드 로그](logs/submission-20261117/latex-build.log).

### 실제 운영과 로그의 확인

- [runtime-projection.json](logs/submission-20261117/runtime-projection.json): 별도 시점 18:00 KST 근방의 compact 운영 projection입니다. board와 동시 스냅샷이 아니므로 메모리 수치를 board 거래 수와 억지로 같게 맞추지 않았습니다. 구매 메모리 30,473개, 거절 13,768개, requested orders 52,125개를 보관하며, 이것은 메모리 항목 수이지 독립 재정산 수가 아닙니다. 보증 authority 64개는 남아 있습니다.
- 같은 projection에는 `gpt-6.1-sol` requested/returned 일치와 실제 token usage가 있는 최근 결과가 있습니다. 이는 호출 성공 증거이며, 개별 quote에 그 정책이 반영됐는지는 별도의 결정 기록으로 확인해야 합니다.
- `openai_harness.py`의 실제 `REASONING_EFFORT`는 Terra `none`, Sol `low`입니다. 이 파일은 제출본에도 있습니다. 최초 Opus 입력에서 빠졌던 소스의 확인을 여기서 보완합니다.
- 등록된 자기 URL의 `/.well-known/acp.json`을 읽어 200 응답을 확인했습니다. [own-acp.json](logs/submission-20261117/own-acp.json). 이것만으로 거래 성공을 주장하지 않고, 아래 실제 정산과 구별합니다.
- [paid-proof.json](logs/submission-20261117/paid-proof.json): 로그에서 뽑은 판매 30개·구매 30개 exact seq를 공개 원장에서 다시 읽었습니다. 각 행의 **top-level round=3**, `kind=settle`, 금액 양수, 자기 shop URL/자기 buyer 이름을 대조했습니다. 같은 주인의 가게와 구매자로 테스트 결제를 만든 것이 아닙니다.
- [ranks.json](logs/submission-20261117/ranks.json), [board.json](logs/submission-20261117/board.json): 요청한 Players 전체 순위는 non-house numeric `total`에 대해 `1 + 자기보다 큰 값의 수`로 계산한 **공동 44/64위**, 두 역할 ALL 합은 **0.965**입니다. 동점 2명이며, 이 방식의 하위 비율은 약 30.16%입니다. 별도 shop/buyer 순위 역시 non-house numeric ALL 기준입니다.
- **실제 채점 기준은 Players의 Class 보기**라는 최신 강의 문구를 함께 보존했습니다. Class는 **35/46위**, 공식 `student_pct=0.244`(24.4백분위), `student_total=0.768`입니다. 전체 Players 순위와 학생끼리의 최종 성적을 혼동하지 않습니다. 최종 성적은 23:59 board로 결정됩니다.

실제 거절 원문([log-excerpts.json](logs/submission-20261117/log-excerpts.json), 제출한 kit 로그):

```text
17:31:37 [acp] not paying: 76.37 h of shelf life left, the policy asks for 84.0
17:33:36 [acp] not paying: 352.61 h of shelf life left, the policy asks for 360.0
17:42:30 [supply] {'sku': 'G85a1a856', 'qty': 1} refused: 429 {"error":"supply is closed; the next restock window opens in 13 s","wait":14.0,"opens_at":1794904950,"closes_at":1794905130}
17:47:27 [acp] not paying: 332.34 h of shelf life left, the policy asks for 360.0
```

식품 남은 시간 미달은 checkout 완결 전에 막은 사례이고, 공급 429는 창이 닫혀 생긴 거절입니다. 이를 상점 사기나 모델 오류라고 바꾸어 쓰지 않았습니다. 원본 로그는 보안 검사 후 byte-identical로 제출했고, 이전 회전 로그가 없는 구간이나 별도 4GB buyer decision journal을 전체 제출 로그처럼 가장하지 않았습니다.

### 안전한 재실행 경계

오프라인 검증은 저장소 의존성(`a2a-sdk[http-server]`, `uvicorn`, `httpx`, `cryptography`)이 있는 Python으로 이 폴더에서 `python -m unittest <모듈 목록> -v`로 수행합니다. 실제 실행의 모듈 목록은 각 로그와 `test-results.json`을 참고하십시오. 하위 프로세스가 같은 환경을 쓰도록 해당 venv의 Scripts를 PATH 앞에 두었습니다. 그림은 `tectonic architecture.tex`로 빌드합니다.

실제 시장 운영에는 별도 등록 키, API 환경 변수, `.market` inventory와 승인 해시 상태가 필요합니다. 이 비공개 런타임 상태는 제출하지 않았고, 없으면 재입고 승인 게이트가 닫히는 것이 의도입니다. 제출 폴더를 받았다고 자동으로 동일 재고·거래 이력을 재현하거나, 즉시 join/구매를 실행하는 것은 아닙니다. API 키는 환경에서 제공하며 개인키·claim code를 코드나 PR 본문에 넣지 않습니다.
