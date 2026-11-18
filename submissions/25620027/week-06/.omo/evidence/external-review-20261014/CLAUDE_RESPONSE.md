# AX Week06 독립 검토 — Claude Code 회신 (2026-10-14)

## 0. 검토자·범위·실행 여부

- 앱/모델: Claude Code 데스크톱(Code 탭), 모델 `claude-opus-5-5`.
- 대상: `submissions/25620027/week-06`, HEAD `23351c55775ae520ee012507285f61d10409de90`. 기준 문서는 저장소 안의 `weeks/week-06/README.md`, `scripts/trade_test_week06.py`, `scripts/check_week06.py`입니다. `3defe07`이 HEAD의 조상이고 그 뒤로 `weeks/`·`scripts/`는 바뀌지 않았음을 git으로 확인했습니다.
- 읽은 자료: REQUEST.md, REVIEW_PACKET.md(목차와 매니페스트만 보고 본문은 저장소 원본으로 읽음), 제출 폴더의 원본 코드·로그·REPORT.md, Week05 `market/host.py`. Week05 파일은 패킷에 없어서 fork 브랜치 `new-origin/week-05`(42f05e3)에서 `git show`로 읽었습니다. 다른 AI의 검토 보고서는 읽지 않았습니다.
- **직접 실행한 것(오프라인, 네트워크·모델 호출 없음)** — 출력은 `CLAUDE_CHECKS.log`에 있습니다.
  1. `pytest -p no:cacheprovider`: **84 passed**
  2. `scripts/check_week06.py`: **all checks passed**
  3. 주요 소스 `ast.parse` 통과
  4. `generate_catalog.build_products()`를 메모리에서 다시 생성한 결과가 커밋된 `catalog.json`과 바이트 단위로 같음
  5. `upstream/main...HEAD` 기준으로 본인 폴더 밖 변경 0건
  6. 검사기 SHA-256이 test-report의 `kit_sha256`과 일치
  7. 반례 탐색 스크립트: 가짜 harness·가짜 buy를 쓰고 제출 모듈을 import만 함
- **실행하지 않은 것**: 공식 거래 검사 `trade_test_week06.py`, `challenge_run.py challenge-1/2`. 둘 다 유료 OpenAI 호출이 필요합니다. 따라서 **21/21 통과, 챌린지 1·2 통과는 모두 기존 로그를 읽은 정적 판단이며, 직접 재현해서 통과를 확인한 것이 아닙니다.**
- 저장소 변경: 이 폴더에 `CLAUDE_RESPONSE.md`와 `CLAUDE_CHECKS.log` 두 파일만 썼습니다. 검사 전후 `git status`가 같음을 확인했습니다.

---

## 1. 심각도순 문제 목록

### [H1·확정] 챌린지 1 후보 필터가 정답을 사실상 미리 정해 줌. "설명 기반으로 모델이 선택했다"는 주장이 과장됨

- 위치: `market_a2a/catalog_constraints.py:11-29`(`FEATURES`, `compatible`), `market_a2a/seller_turn.py:102-106`(opening 단계에서 원 요청에 hard filter 적용)
- 근거(실행 결과, CLAUDE_CHECKS.log [7] P2·P3):
  - 필터 단어 9개(lockable, flexible, dimmable, rounded, mesh, lumbar, armrests, cable tray, dual monitor)가 3,008개 설명 중 **각각 정확히 1개 상품, 그것도 시험용 정답 5개에만** 나옵니다.
  - 6개 사례 모두 `compatible(request)`만 적용해도 남는 상품이 **정답 1개**입니다.
  - 실제 로그도 같습니다. `logs/challenge-1/attempt-3/server.log`의 6개 검색이 모두 `candidates=1`입니다(8행, 39행 등).
- 영향: 모델은 사실상 후보 1개 중에서 "선택"했습니다. 과제의 핵심인 "설명만 듣고 수천 개 중 고르기"가 모델 판단으로 입증되지 않았습니다.
  - expected_sku를 직접 넘기지는 않았지만, 필터 단어 목록이 정답 집합과 1:1로 대응하는 **간접 오라클**입니다.
  - REPORT 143행("특징 조합으로 구별되는 시험용 5개")과 164행("판매자 검색·선택·가격·수락은 실제 모델이 결정")은 이 사실을 밝히지 않습니다.
- 부작용 반례(실행 결과, P4): hard filter가 부정 표현을 무시하고 **반대 상품을 강제**합니다.
  - `"a chair with mesh back but no armrests"` → armrests가 있는 `chair-stratus`만 남음
  - `"a lamp that is not steel"` → steel 램프만 남음
  - `"any chair except a black one"` → black 의자만 남음
  - `"under 1.2 m"`, `"30 inches"`는 높이 조건이 파싱되지 않아 180cm 책장이 후보에 들어옴
  - `"a stool"`, `"something to read by at night"`는 후보 0개
- 권장: 제출 전에 REPORT에 "필터 단어가 시험용 상품에만 있어 6개 사례 모두 후보가 1개였다"는 점을 밝히고 주장을 낮추세요. 개선하려면 다음 방법이 있습니다.
  - 같은 특징을 공유하는 decoy 상품을 추가
  - 사례와 분리된 held-out 요청으로 평가
  - 부정 표현 처리를 추가하거나, 특징 판단을 hard filter 대신 모델에게 맡기고 결과만 검증

### [M1·확정] 챌린지 2에서 재계획·협상이 실제로 일어나지 않음. 가게 선택에 순서 교란이 있음

- 근거:
  - 통과한 attempt-3/4의 `buyer.log`에는 `[SHOPPING PLAN]`이 1회뿐입니다. `SHOPPING_USAGE calls=6`은 계획 1회와 품목별 제안 1회씩입니다.
  - 다섯 협상 모두 판매자의 **첫 호가를 그대로 수락**했습니다(예: `offer {"price":156,...}`, 이는 reserve 120×1.3).
  - 재계획 경로의 근거는 `tests/test_challenge_replanning.py` 회귀 테스트뿐입니다. REPORT 131행이 이 점을 스스로 밝힌 것은 정직합니다.
- 교란: 가게 index가 곧 가격 offset(index×5)이고, 디렉터리 순서도 index 순서입니다. 그래서 종류별 첫 카드(00–04)가 항상 가장 쌉니다. 카드에는 가격 정보가 없으므로 00–04를 고른 결과는 순서 효과와 구분되지 않습니다. `experiment_config.json`에 `card_order_seed`가 있지만 이번 경로에서는 쓰이지 않습니다.
- 상태 전달: 재계획 상태(`PlanningRound`, `shopping.py:99-107`)에는 *어느 가게가 어떤 호가로 실패했는지*가 빠져 있습니다. 모델은 `planning_round` 번호만 보고 "더 싼 가게"를 추측해야 합니다. 과제가 "핵심"이라고 한 협상 요약·상태 전달이 실패 이후 단계에서는 약합니다.
- 권장: REPORT에 "첫 호가 수락, 계획 1회, 순서 교란"을 명시하세요. 재계획 프롬프트에는 실패한 협상 요약(가게, 마지막 호가, 결과)을 넣으세요.

### [M2·확정] 한 checkout의 예외가 동시에 진행 중인 모든 협상을 중단시킴. 영수증 가격이 정확히 같아야만 받아들임

- 위치: `shopping.py:152-209`(task group 안에서 `buy()` 예외를 처리하지 않음), `remote.py:250-257`(receipt.price가 마지막 제안과 정확히 같아야 함), `buyer.py:136`(280초 초과 시 `TimeoutError`)
- 근거(실행 결과, P6·P7):
  - 품목 B의 `buy()`가 `HarnessError`를 던지면 진행 중이던 A도 취소되고 `ExceptionGroup`으로 전체가 종료됩니다. 재계획으로 넘어가지 않습니다.
  - 판매자가 120 제안을 받고 110으로 체결하면 구매자는 `Receipt differs...` 예외로 종료됩니다.
- 영향: 이번 자체 가게와 공식 검사에서는 발생하지 않았습니다. 하지만 다른 하네스의 가게, 네트워크 오류, 시간 초과가 있으면 다음 문제가 생깁니다.
  - 이미 판매자 쪽에서 체결된 거래가 원장에서 빠질 수 있음
  - 하나의 실패가 쇼핑 전체 실패로 번짐
- 권장: checkout 하나의 예외는 `completed=False`와 사유로 바꿔 기록하고 재계획 대상으로 넘기세요. 영수증은 `price ≤ 허가 제안 ≤ cap` 조건으로 받아들이세요.

### [M3·확정] 공식 예산 유지 검사는 모델이 아니라 코드 가드 덕분에 통과함

- 근거(`logs/buyer-under-test-buyer_holds_budget-1.log:27-28, 44-45, 61-62`):
  - 모델이 예산 230을 넘는 284와 260을 제안했고, `Offer exceeds immutable budget`로 차단됐습니다.
  - 세 번 실행 모두 차단이 4회씩, 총 12회입니다. `walk_away` 호출은 0회입니다.
  - 프롬프트의 "거절된 금액 반복 금지"에도 230을 5번 반복했습니다(`trade-test.log` 128·133·138행). 협상은 공식 판매자가 다섯 번 거절해서 끝났습니다.
- 평가: README가 허용하는 설계("enforced in code")이고, REPORT 85행에도 코드 가드가 적혀 있습니다. 다만 3절 "실패와 수정"에 이 개입 횟수를 인용하지 않았습니다. 그래서 독자는 모델이 스스로 예산을 지킨 것으로 읽게 됩니다.
- 권장: 이 로그 줄을 REPORT (2)·(3)에 인용하세요. 이 의견은 "보고서가 근거보다 강한 인상을 준다"는 지적입니다.

### [M4·의심/근거 부족] 챌린지 2 최종 로그가 최종 커밋 코드보다 먼저 만들어짐

- 근거:
  - `logs/challenge-2/attempt-4/summary.json`의 mtime은 07:54:45입니다.
  - 그 뒤 커밋 `ed90c4a`(08:00:34)가 `shopping.py`의 `PLAN_PROMPT`를 공백 join 방식에서 줄바꿈이 들어간 triple-quote 문자열로 바꿨습니다. 단어는 같지만 모델 입력 바이트가 다릅니다. 같은 커밋에서 `challenge_run.py`도 리팩터링됐습니다.
  - 당시 작업 트리에 이 변경이 이미 있었는지는 git만으로 판별할 수 없습니다.
  - 공식 거래 경로(`buyer.py`, `seller*.py`, `harness.py`, `remote.py`)는 test-report 생성 뒤로 바뀌지 않았습니다.
- 권장: 이 사실을 REPORT에 적거나, 비용을 감수할 수 있다면 HEAD 기준으로 새 attempt(5)를 실행해 보존하세요. 기존 로그는 덮어쓰지 마세요. 추가 유료 실행 여부는 작성자가 판단할 일입니다.

### [L1·확정] 판매자는 숫자만 있는 제안만 이해함

`seller_turn.py:68`의 `re.fullmatch(r"[0-9]{1,9}")` 때문에 `"I offer 110"`, `"110.0"`, `"1,100"`은 `offer=None`이 됩니다(P5). 공식 검사에는 영향이 없습니다. 하지만 다른 학생 하네스와 거래하면 판매자가 수락할 수 없습니다. 챌린지 3이 범위 밖이어도 README (4)의 "가격 표기 합의" 논점으로 REPORT에 적을 만합니다.

### [L2·확정] 수락이 사실상 코드 신호에 따라 결정됨

`offer_meets_reserve`(`seller_turn.py:73-77`)와 프롬프트의 "If it is true, ACCEPT"(`seller_policy.py:23-24`) 때문에, reserve 이상 제안의 수락은 사실상 코드 신호가 정합니다. REPORT 73행이 부분적으로 밝히고 있으니 결함이라기보다 설계 설명을 보강하면 됩니다.

### [L3·확정] 예시 문구에 맞춘 규칙

`catalog.py:24`의 `"books"→"bookshelf"` 별칭과 `catalog.py:153-156`의 "not taller than my desk = 75cm"는 README 예시 문장에 맞춘 규칙입니다. 75cm 가정은 REPORT 193행에 밝혀져 있습니다.

### [L4·참고] 챌린지 1 평가용 구매자가 reserve를 앎

평가용 구매자는 reserve를 알고 reserve−1, reserve 순서로 제안합니다(`challenge_run.py:71-75`). 그래서 이 평가는 하한 준수를 검사하지만 협상 능력은 검사하지 않습니다. REPORT 164행에 적혀 있습니다.

---

## 2. 요구사항별 판정

판정 표기: **통과**(코드와 로그가 일치), **통과(약함)**(형식은 충족하지만 입증력이 제한됨), **근거 부족**. 모든 "통과"는 기존 로그를 읽은 정적 판단이며, 공식 거래 검사를 다시 실행하지 않았습니다.

| 요구사항 | 판정 | 근거 |
|---|---|---|
| Agent Card `/.well-known/agent-card.json`, JSONRPC·1.0·url | 통과 | `seller.py:77-106`, test-report `seller_card` |
| lamp/chair/bookshelf, reserve 100/180/260 | 통과 | `catalog.json` 기본 SKU 3개(`generate_catalog.py`, 재생성 일치) |
| 첫 메시지 → INPUT_REQUIRED와 호가 | 통과 | `seller_turn.py:190-202`, `trade-test.log` seller_opens(ask 130) |
| 역제안/COMPLETED+numeric price artifact/REJECTED | 통과 | `seller_turn.py:156-243`, seller_deal ×3 receipt 100 |
| reserve 미만 호가·판매 금지 | 통과 | 코드 가드 `seller_turn.py:177-188`, 로그 asks 모두 ≥ reserve, `seller.guard` 0회 |
| 종료된 task에 보낸 메시지 → 오류 | 통과 | `seller.py:56-57`, seller_terminal |
| 하네스가 호가·수락·거절 결정 | 통과(약함) | `decide` 도구로 결정하지만 수락은 `offer_meets_reserve` 신호가 사실상 지시(L2) |
| agent.json 명령·placeholder | 통과 | `agent.json`, check_week06 직접 실행 통과 |
| 카드 읽기, 모든 요청에 `A2A-Version: 1.0` | 통과 | `remote.py:166-195`, buyer_protocol ×6 |
| taskId로 협상 계속 | 통과 | `remote.py:184-185`(contextId도 함께 보냄), 응답의 id 변경 시 거부 `remote.py:207-211` |
| 예산 이내 구매, 초과 지불 금지 | 통과 | `buyer.py:105-106`, `remote.py:250-257`. 단, 모델 판단이 아니라 코드 가드로 지킴(M3) |
| 포기 또는 중단 | 통과(약함) | 구매자가 스스로 멈추지 않음. 공식 판매자가 다섯 번 거절해 종료. `walk_away` 실사용 0회 |
| 300초 안에 종료 | 통과 | `buyer.py:136`(280초), 실행 로그 |
| 하네스가 제안 결정 | 통과 | `offer`/`walk_away` 도구 |
| test-report가 현행 검사기와 명령에서 나오고 전부 통과 | 통과 | SHA 일치, 21/21, check_week06 직접 실행 통과 |
| logs/, logs/challenge-1·2 | 통과 | 파일 존재. 실패한 attempt도 보존 |
| REPORT (1)–(5) | 통과(보완 필요) | 구성은 충족. H1·M1·M3 공개 누락 |
| C1: 수천 개 카탈로그를 직접 만들어 커밋 | 통과 | 3,008개, 생성기로 재생성 일치. 다만 합성 데이터이고 다양성이 낮음 |
| C1: 이름 없이 설명으로 선택 | **근거 부족** | 6/6이지만 필터가 정답 1개만 남김(H1) |
| C1: 잘못된 상품 판매나 reserve 미만 호가 없음 | 통과(fixture 한정) | `challenge-1/attempt-3/summary.json`. 부정 표현 반례에서는 잘못된 상품을 강제(H1) |
| C1: 문맥 관리(카탈로그 전체 미투입) | 통과 | 검색 결과 최대 6개(`catalog.py:216-231`), 상태 JSON 약 300자 |
| C2: 카드 약 20개, skills를 읽고 가게 선택 | 통과(약함) | 실제 카드 20개를 HTTP로 조회. skill 문구가 품목 종류뿐이고 순서 교란 있음(M1) |
| C2: 여러 가게와 동시 협상 | 통과 | `shopping.py:207-209` task group. ledger 순서가 계획 순서와 달라 동시 진행이 실제로 있었음을 뒷받침 |
| C2: 전체 구매, 총예산 이내, 중복 없음 | 통과 | 1,692/2,200, task·SKU 5개 모두 고유, 원장 재생 감사 `challenges.py:160-214` |
| C2: 실패 후 재계획 | **근거 부족(실측)** | 단위 테스트만 있고 실제 실행에서는 발생하지 않음(REPORT 131행이 인정) |
| C2: 디렉터리 구성(한 프로세스에 앱 20개 mount) | 통과(약함) | URL·카드·TaskStore·catalog·가격이 가게마다 별도라 A2A 수준에서는 독립. 다만 model client와 동시성 limiter를 공유하고, catalog는 같은 파일의 종류별 부분집합에 offset만 더한 것. 다른 학생 가게는 없음. README의 "several copies"를 형식상 충족 |
| PR 경로 제한 | 통과 | `upstream/main...HEAD` 기준 본인 폴더 밖 변경 0건 |

### 공식 21개 검사가 보장하는 것과 보장하지 않는 것

- **보장함**:
  - 카드 형식
  - lamp·bookshelf 각 3회에서 고정 구매자를 상대로 reserve 위반 없음
  - 종료된 task에 오류 반환
  - 공식 판매자를 상대로 예산 초과 거래 없음
  - 모든 POST에 버전 헤더, 후속 요청의 taskId
- **보장하지 않음**:
  - contextId 검사(구매자는 보내지만 검사기는 확인하지 않음)
  - 모델 단독으로 예산을 지키는지(M3)
  - 구매자가 스스로 포기하는지, CancelTask 경로
  - 숫자가 아닌 제안이나 다른 영수증 형식과의 상호운용(L1, M2)
  - 설명 기반 카탈로그와 동시성(챌린지 영역)
  - 실행 3회를 넘어서는 모델 변동성
  - 강사 환경에서의 재현. `OPENAI_API_KEY`와 uv가 필요하며, README "Budget"의 "no API key is needed"는 참조 구현 기준입니다.

### 상태 전이와 receipt 계약

- 판매자: SUBMITTED → INPUT_REQUIRED → COMPLETED / REJECTED / FAILED(모델 루프 소진, `seller_turn.py:140-150`) / CANCELED(`seller.py:60-71`).
- 영수증 `{seller, item=kind, sku, price}`는 구매자가 검증하는 필드(item, sku, price)와 같습니다. 공식 판매자는 sku를 보내지 않는데, 이때 빈 문자열끼리 비교되어 통과합니다. 코드와 로그가 일치합니다.

---

## 3. 모델 루프와 Week05 기반이라는 설명

- 판매자: 같은 실행 안에서 `search_catalog` → 관찰(후보 목록) → `decide` 순서로 진행합니다. 가드 오류를 받으면 모델이 수정합니다(`seller-under-test.log:177` "Too early to reject" 이후 counter). **판단→도구→관찰→재판단이 실제로 일어났습니다.**
- 구매자: 한 실행에 offer 하나만 확정합니다(terminal). 판매자 응답을 요약(`recent_observations` 2개)에 넣어 다음 실행에서 다시 판단합니다. 가드 오류 뒤에는 같은 실행 안에서 다시 제안합니다(holds_budget-1:27→다음 round). 루프는 실제로 동작하지만, 관찰 결과를 반영한 판단의 질은 낮습니다(반복 제안, 첫 호가 수락).
- Week05 비교: `new-origin/week-05`의 `market/host.py`를 직접 대조했습니다. assistant tool_call 변환, `tool_choice="required"`, 429/5xx 재시도, 라운드 상한 구조가 대응합니다. 달라진 점은 MCP 대신 로컬 dispatch, 라운드 4→6, `parallel_tool_calls=False`, terminal 결과 개념입니다. REPORT 15–16행의 "이식했고 import한 것은 아님"이라는 설명은 **정확합니다**. 같은 모듈을 재사용한 것이 아니라 패턴을 이식한 것으로 보는 게 맞습니다.

---

## 4. 총평

**조건부 제출 가능.**

- 하한선인 공식 거래 검사는 로그상 21/21입니다. 형식 검사는 직접 실행해서 통과를 확인했습니다. 코드 구조, 원장 감사, 실패 이력 보존, 비밀정보 미포함은 학생 과제로서 좋은 수준입니다.
- 다만 챌린지 1의 "설명 기반 선택"은 사실상 필터가 정답을 정해 준 결과라 과제 취지의 입증력이 약합니다. REPORT가 이 점을 밝히지 않은 것은 "과정의 정직성" 채점에서 위험합니다.

**제출 전에 꼭 할 것** (REPORT 수정과 커밋은 작성자가 판단)
1. H1 공개: 필터 단어가 정답에만 있다는 점, 6/6 모두 후보 1개였다는 점(server.log 인용), 부정 표현 반례. "모델이 선택"이라는 표현을 낮추기.
2. M3 공개: 공식 holds_budget에서 코드 가드가 12회 개입했고 `walk_away`는 0회였다는 점.
3. M1 공개: 챌린지 2에서 첫 호가 수락, 계획 1회, 디렉터리 순서와 가격의 교란.
4. M4 처리: 최종 로그 이후 `PLAN_PROMPT`가 바뀐 사실을 적거나, HEAD 기준 새 attempt를 실행.
5. 작업 트리 정리: 현재 `record-week06-completion.md`가 수정 상태이고 이 검토 폴더는 untracked입니다. 커밋할지 제외할지 정하세요.

**선택적 개선** (우선순위 순)
1. decoy 상품과 held-out 설명 세트(10–20개)로 챌린지 1 재평가
2. checkout 단위로 예외를 격리하고 영수증 가격 허용 범위를 `≤`로 바꾸기
3. 재계획 상태에 실패한 협상 요약 넣기
4. 카드 순서 무작위화
5. 판매자의 자연어 제안 파싱
6. 구매자가 실제로 깎는 전략 쓰기(현재는 예산이나 첫 호가를 그대로 제안)

**타당한 설계 선택**
- 금액 한도를 코드 가드와 프롬프트 양쪽에서 강제
- 모델 출력을 typed tool 인자로 받기
- 협상마다 요약 상태만 넘기고 전체 이력은 다시 넣지 않기
- 예약·해제 원장과 로그 재생 감사기
- 실패한 attempt 보존과 기존 로그 덮어쓰기 금지(`open("x")`)

---

## 5. 장애·정보 제약 에이전트 / 서포트 에이전트 공정 경쟁 연구로 확장할 때 필요한 통제

아래는 의견이며, 이 연구가 수행됐다고 보지 않습니다.

- **오라클 차단**: 평가 정답과 연결된 필터나 단어 목록을 없애고, held-out 요청과 decoy가 있는 카탈로그를 사용
- **개입 계측 분리**: 코드 가드 차단 횟수를 "모델 단독 준수율"과 별도 지표로 기록. 가드 유무를 실험 조건으로 다룸
- **정보 제약의 조작적 정의**: 문맥 마스크, 요약 길이, 도구 접근을 조건별로 명시하고 같은 토큰·호출·시간 예산을 부여
- **순서와 역할 교란 제거**: 카드·가게 순서와 가격 offset을 무작위화하고, 구매자·판매자 역할을 맞바꿔 배치
- **반복과 고정**: 모델 버전, temperature, seed를 고정하고 조건마다 충분히 반복해 신뢰구간 보고. 지표와 판정 규칙을 사전 등록
- **상대 고정**: 비교할 때 상대 에이전트를 규칙 기반이나 고정 모델로 묶어, 개선 효과가 상대 변화와 섞이지 않게 함
- **실패 처리 공정성**: 네트워크·타임아웃 실패를 조건 간에 같은 방식으로 처리하고 기록(M2 해결이 전제)

## 6. 추가로 있으면 좋은 자료

- 챌린지 2를 HEAD 커밋 기준으로 다시 실행한 attempt(M4 해소)
- 시험용 5개 외 설명 요청 10개 이상의 held-out 챌린지 1 실행 로그
- 다른 하네스 판매자와의 교차 거래 로그 1건(L1·M2의 실제 영향 확인)
