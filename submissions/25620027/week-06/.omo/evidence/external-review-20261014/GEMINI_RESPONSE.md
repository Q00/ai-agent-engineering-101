1. 실제 검토 범위 및 실행 여부

⚬ 검토 수행 주체 및 환경: Gemini 앱 환경에서 정적 코드·설정·실행 증빙 스냅샷(REVIEW_PACKET.md)을 기반으로 독립 검증을 수행했습니다.
⚬ 실제 실행 여부: 소스 코드 수정·삭제·커밋·푸시·외부 제출·유료 API 재실행 및 로컬 명령 실행은 일절 수행하지 않았습니다. 본 보고서의 모든 판정은 직접 재현 실행이 아니라 첨부된 REVIEW_PACKET.md 내 코드와 기존 로그·아티팩트 대조를 통해 확인한 정적·기록 검증 결과입니다.
⚬ 검토 포함 범위:
  ⚬ 공식 기준 문서 및 스크립트 3종: weeks/week-06/README.md, scripts/trade_test_week06.py, scripts/check_week06.py.
  ⚬ 제출 핵심 소스 및 테스트 22개 .py 파일: market_a2a/ 하위 13개 모듈, harness_app.py, generate_catalog.py, challenge_run.py, tests/ 하위 6개 테스트 모듈.
  ⚬ 설정·데이터·보고서: agent.json, run_harness.sh, pyproject.toml, challenge_cases.json, shopping_list.json, test-report.json, REPORT.md, catalog.json 메타데이터 및 샘플 10건.
  ⚬ 실행 로그 및 카드 증빙: logs/trade-test-attempt-04-console.log, logs/challenge-1/attempt-3/(summary.json, invocation.json, wire.log, server.log), logs/challenge-2/attempt-4/(summary.json, invocation.json, shopping-input.json, directory.json, buyer.log, server.log, cards/00~19.json 및 -http.json), 정적 검증 로그 4종(current-tests-final.log, current-structure-final.log, current-ruff-final.log, current-types-final.log).
⚬ 패킷 미포함으로 인한 미확인 범위:
  ⚬ 5주차 원본 코드(week-05/market/host.py): 패킷에 포함되지 않아 5주차 코드와의 직접 diff 대조는 미확인입니다.
  ⚬ http_client.py 및 레거시 파이썬 파일 16개: current-structure-final.log(L00001)에는 38 source file(s)로 기록되어 있으나 패킷 매니페스트에는 22개 .py 파일만 수록되어 있습니다. 특히 buyer.py·harness_app.py·challenge_runtime.py·challenge_run.py가 import하는 http_client.py와 pyproject.toml에 언급된 a2a_client.py, archive.py, batches.py, episode.py, run.py, verify_protocol.py 및 레거시 테스트 파일들의 내부 구현은 미확인입니다.
  ⚬ 공식 검사기 생성 원본 파일명 일부 및 과거 실패 로그: check_week06.py가 검사하는 정식 경로인 logs/trade-test.log(패킷에는 logs/trade-test-attempt-04-console.log만 수록), logs/seller-under-test.log, logs/buyer-under-test-*.log, 그리고 REPORT.md 3절이 인용한 과거 실패 로그(logs/attempt-01/, logs/attempt-02/, logs/challenge-2/attempt-1/, logs/challenge-2/attempt-2/)와 legacy-card-experiment-report.md 원문은 패킷에 없으므로 미확인입니다.
  ⚬ 락파일 및 전체 카탈로그 원문: catalog.json 전체(3,008건 중 2,998건)와 harness_app.py.lock·challenge_run.py.lock 원문은 패킷에서 생략되었습니다(단, generate_catalog.py 전체 코드와 test_current_catalog.py의 결정론적 일치 테스트는 확인 가능).

2. 심각도순 결함 및 한계 목록 (확정 결함 vs 의심/구조적 한계 구분)

[High / 확정] H-1. 챌린지 1·2 설명 기반 검색이 5개 수제(curated) 상품 전용 키워드 필터에 과적합되어 모델의 후보 변별을 우회함

⚬ 구분: 확정 결함 (설계·평가 과적합)
⚬ 위치: generate_catalog.py 85165행, market_a2a/catalog_constraints.py 1131행, market_a2a/seller_turn.py 95100행, challenge_cases.json 18행, shopping_list.json 1~9행
⚬ 발생 조건: search_catalog 도구 호출 시 seller_turn.py가 모델의 검색어(query.query)로 상위 6개를 찾은 뒤, 구매자의 원본 요청 문자열(text)에 대해 compatible(product, text) 후처리 필터를 적용할 때 발생합니다.
⚬ 영향: 3,008개 카탈로그 중 3,000개 합성 상품은 FEATURES에 정의된 9개 키워드(lockable, flexible, dimmable, rounded, mesh, lumbar, armrests, cable tray, dual monitor)를 단 하나도 포함하지 않으며, 오직 수제 상품 5개(lamp-nocturne, bookshelf-meadow, chair-stratus, desk-harbor, storage-cove)에만 해당 키워드가 존재합니다. 평가용 challenge_cases.json 6건과 shopping_list.json 5건 모두 이 9개 키워드를 포함하고 있어, 코드 필터가 3,000개 합성 상품을 100% 배제하고 항상 단 1개의 정답 후보(candidates=1)만 모델에 전달합니다.
⚬ 근거: logs/challenge-1/attempt-3/server.log(L00008, L00039, L00070, L00101, L00143, L00174) 6건 전수와 logs/challenge-2/attempt-4/server.log(L00018, L00026, L00030, L00034, L00040) 5건 전수에서 candidates=1이 기록되어, 모델이 복수 후보 중에서 설명을 비교·선택한 사례가 실제로는 0건입니다.
⚬ 권장 조치: 3,000개 합성 상품 생성 규칙(generate_catalog.py의 FEATURES)에도 lockable, dimmable, rounded corners, mesh 등의 속성을 교차 분포시켜 검색 시 2~6개의 경쟁 후보가 모델에 노출되도록 하고, challenge_cases.json에 합성 SKU({kind}-{material}-{color}-{variant})를 정답으로 하는 테스트 케이스를 포함해야 합니다.

[Medium / 확정] M-1. 챌린지 2 재계획(planning_round > 0) 시 실패한 가게·예산 한도 정보가 모델 상태에서 누락됨

⚬ 구분: 확정 결함 (재계획 컨텍스트 설계 결함)
⚬ 위치: market_a2a/shopping.py 8089행(PlanningRound), 101111행(choose_plan), 149~164행(shop 루프)
⚬ 발생 조건: 1차 쇼핑 계획(planning_round == 0)에서 일부 품목 협상이 결렬되어 Purchase(completed=False, ...)가 반환된 후, planning_round == 1에서 choose_plan을 호출할 때 발생합니다.
⚬ 영향: PLAN_PROMPT(9697행)는 모델에게 "On later planning_rounds previous caps failed; choose a cheaper shop or reserve more of the remaining total_budget"이라고 지시하지만, 정작 PlanningRound 상태에는 already_bought(성공한 구매만 필터링: if outcome.completed)와 미구매 items(request_id, description)만 들어갈 뿐 직전 라운드에서 어떤 shop_url에 얼마의 cap을 배정했다가 실패했는지 전혀 전달되지 않습니다. 또한 harness.run은 매 호출마다 새로운 메시지 리스트로 시작하므로(harness.py 8790행), 모델은 직전 실패 원인을 모른 채 맹목적으로 재계획을 수행하게 됩니다.
⚬ 근거: market_a2a/shopping.py 155~162행에서 already_bought=tuple(outcome for outcome in outcomes.values() if outcome.completed)로 실패 내역을 버리고 이전 plan도 상태에 넣지 않음을 코드에서 직접 확인했습니다.
⚬ 권장 조치: PlanningRound 모델에 failed_attempts: tuple[Reservation, ...](또는 직전 실패한 shop_url·cap·관찰 요약) 필드를 추가하여 모델이 실제로 더 저렴한 가게를 고르거나 cap을 상향할 근거를 제공해야 합니다.

[Medium / 확정] M-2. 병렬 구매(anyio.create_task_group) 중 단일 가게의 전송·하네스 예외가 전체 쇼핑을 즉시 중단시킴

⚬ 구분: 확정 결함 (예외 격리 미비)
⚬ 위치: market_a2a/shopping.py 123147행(checkout), 165167행(anyio.create_task_group)
⚬ 발생 조건: 동시 협상 중인 5개 가게 중 한 곳에서 네트워크 오류(httpx2.HTTPError), JSON-RPC 에러 응답(HarnessError), 영수증 불일치(HarnessError), 또는 타임아웃(TimeoutError)이 발생할 때 일어납니다.
⚬ 영향: 정상적인 협상 거절(TASK_STATE_REJECTED)이나 포기(walk_away)는 Purchase(completed=False)를 반환하여 재계획으로 이어지지만, 예외가 발생하는 경우에는 checkout() 내부에 try ... except 격리 및 예약금 반환(reservations.pop) 처리가 없어 TaskGroup 전체가 취소되고 이미 진행 중이거나 완료된 다른 가게의 협상까지 모두 날아갑니다.
⚬ 근거: shopping.py 125131행에서 await buy(...)가 예외 처리 없이 호출되며, lock 블록(132147행) 진입 전에 예외가 발생하면 reservations 해제가 누락된 채 TaskGroup이 종료됩니다.
⚬ 권장 조치: checkout()에서 복구 가능한 협상 실패 예외(HarnessError, HTTPError, TimeoutError)를 포착해 Purchase(completed=False, ...)로 전환하고 lock 블록에서 예약금을 정상 해제(released = reservations.pop(entry.request_id))하도록 방어해야 합니다.

[Medium / 확정] M-3. 자연어 카테고리 파서(Constraints.parse)의 첫 매칭 우선순위로 인한 다중 명사 오분류

⚬ 구분: 확정 결함 (검색 일반화 오류)
⚬ 위치: market_a2a/catalog.py 70행(KINDS), 111행(Constraints.parse), 129~137행(Constraints.matches)
⚬ 발생 조건: 구매자 설명에 두 개 이상의 가구 관련 명사가 등장할 때(예: "a desk that fits my office chair" 또는 "a storage cabinet next to my lamp") 발생합니다.
⚬ 영향: KINDS = ("lamp", "chair", "bookshelf", "storage", "desk") 순서대로 단순 집합(frozenset) 포함 여부를 검사해 첫 번째로 매칭된 카테고리를 하드 필터(product.kind == self.kind)로 고정합니다. 따라서 "desk"나 "storage"를 찾는 문장에 비교 대상으로 "lamp"나 "chair"가 언급되면 카테고리가 "lamp" 또는 "chair"로 강제 고정되어 정작 원하는 책상·수납장 후보가 0건이 됩니다. (challenge_cases.json의 low-bookcase가 통과한 이유도 "books"→"bookshelf"가 index 2로서 index 4인 "desk"보다 우연히 앞에 있었기 때문입니다.)
⚬ 근거: catalog.py 111행 kind = next((value for value in KINDS if value in words), None) 및 130행 (self.kind is None or product.kind == self.kind).
⚬ 권장 조치: 쿼리 내 다수 카테고리가 감지되면 kind 하드 필터를 해제(kind = None)하고 TF-IDF 가중치 점수에 맡기거나, 주절의 목적어 명사를 우선 추출하도록 수정해야 합니다.

[Medium / 확정] M-4. 구매자 에이전트가 예산 부족 시 스스로 포기(walk_away)하지 못하고 동일 제안을 5회 반복함

⚬ 구분: 확정 결함 (모델 프롬프트 지시 불이행 및 코드 가드 부재)
⚬ 위치: market_a2a/buyer.py 5255행(PROMPT), 7195행(Negotiation.dispatch), logs/trade-test-attempt-04-console.log L00127~L00140
⚬ 발생 조건: 판매자의 최소 가격(reserve 260)이 구매자 예산(230)보다 높은 상황(buyer_holds_budget)에서 발생합니다.
⚬ 영향: 구매자 프롬프트는 "Before walking away from price alone, try your full budget once. Never repeat a rejected amount"라고 지시하지만, 실제 실행에서는 구매자가 walk_away 도구를 호출하지 않고 거절당한 최대 예산 230을 5번 연속 반복 제안했습니다. 공식 키트 판매자가 5회 실패 시 먼저 TASK_STATE_REJECTED를 선언해주었기 때문에 거래가 종료되었을 뿐, 만약 상대 판매자가 거절하지 않는 판매자(예: 본 제출물의 판매자)였다면 최대 라운드(MAX_ROUNDS = 12)까지 불필요한 동일 제안을 반복하게 됩니다.
⚬ 근거: logs/trade-test-attempt-04-console.log L00128, L00133, L00138에서 3회 모두 kit seller saw: offers [230, 230, 230, 230, 230], deals [], canceled 0, continued 5가 기록되어 있습니다.
⚬ 권장 조치: Negotiation.dispatch에 if offer.price == self.last_offer:(또는 이미 거절된 제안 목록 rejected_offers 포함 시) 도구 에러 피드백("Already offered this amount; offer a different amount within budget or call walk_away")을 반환하는 코드 가드를 추가해야 합니다.

[Low / 확정] L-1. 외부 판매자와의 상호운용성을 저해하는 엄격한 영수증·메타데이터 결합(purchase())

⚬ 구분: 확정 결함 (A2A 외부 상호운용성 제약)
⚬ 위치: market_a2a/buyer.py 123129행, market_a2a/remote.py 203224행
⚬ 발생 조건: 구매자가 첫 응답 status.message.metadata에 sku나 item을 담아주지 않는 외부 A2A 판매자(예: 다른 학생의 판매자)와 거래할 때, 해당 판매자가 최종 receipt 아티팩트에 자체 sku를 포함하거나 구매자의 긴 설명 문자열 대신 정규화된 item명을 넣어 반환할 때, 혹은 구매자 제안가보다 더 낮은(유리한) 가격으로 거래를 성사시켜 줄 때 발생합니다.
⚬ 영향: receipt.price != authorization.price(할인 수락 불허), receipt.sku != authorization.sku(초기 메타데이터 미제공 판매자의 영수증 SKU 불허), receipt.item != authorization.item 조건에 걸려 정상 거래 완료 후에도 구매자가 HarnessError("Receipt differs from authorized price or product")를 발생시키며 실패 처리합니다.
⚬ 근거: remote.py 215~221행의 동등 비교(!=) 코드.
⚬ 권장 조치: receipt.price <= authorization.price 및 receipt.price <= authorization.budget으로 완화하고, authorization.sku가 빈 문자열("")인 경우(외부 판매자가 사전 SKU 메타데이터를 주지 않은 경우)에는 영수증의 sku 검사를 생략하도록 유연화해야 합니다.

[Low / 의심·환경] L-2. run_harness.sh의 파이썬 패치 버전(3.12.11) 하드코딩 및 패킷 내 logs/trade-test.log 파일명 불일치

⚬ 구분: 의심 결함 / 재현성 리스크
⚬ 위치: run_harness.sh 5행(--python 3.12.11), scripts/check_week06.py 95행(logs/trade-test.log)
⚬ 발생 조건: 채점자 환경에 Python 3.12.11이 아닌 다른 3.12 패치 버전(예: 3.12.7, 3.12.9)만 설치되어 있고 오프라인이거나 uv 파이썬 자동 다운로드가 제한된 경우, 또는 Git 커밋 시 logs/trade-test.log가 누락된 경우 발생합니다.
⚬ 영향: uv run --frozen --python 3.12.11 실행이 실패하거나, check_week06.py가 logs/trade-test.log missing으로 실패할 수 있습니다. (단, current-structure-final.log L00004에 ok logs/trade-test.log present가 찍혀 있으므로 로컬 디스크에는 존재하나 REVIEW_PACKET.md 생성 시 trade-test-attempt-04-console.log만 스냅샷에 포함된 것으로 보입니다.)
⚬ 근거: run_harness.sh 5행 및 REVIEW_PACKET.md 매니페스트.
⚬ 권장 조치: run_harness.sh의 --python 3.12.11을 --python 3.12(또는 >=3.12)로 완화하고, PR 생성 전 git status로 logs/trade-test.log가 커밋 대상에 포함되었는지 반드시 확인해야 합니다.

3. 공식 요구사항별 판정표 및 핵심 질문(Q1~Q6) 상세 검증

Q1. 공식 요구사항별 충족 여부 판정표

구분	공식 요구사항 (weeks/week-06/README.md 및 검사기 기준)	판정	정확한 파일·줄·로그 근거
판매자	A2A v1.0 JSON-RPC 서버 및 /.well-known/agent-card.json 제공 (JSONRPC, 1.0, url)	충족	market_a2a/seller.py:72-104 (create_app) / test-report.json:10-49 (seller_card pass) / logs/trade-test-attempt-04-console.log:L00005
판매자	기본 3종(lamp 100, chair 180, bookshelf 260) 판매 및 reserve 설정	충족	generate_catalog.py:53-84 / catalog.json 샘플 1~3번 / tests/test_current_catalog.py:111-123
판매자	첫 메시지에 TASK_STATE_INPUT_REQUIRED 및 마지막 숫자로 호가 반환	충족	market_a2a/seller_turn.py:167-177 ("{product.name}. Asking price: {decision.price}") / logs/trade-test-attempt-04-console.log:L00009, L00017
판매자	동일 taskId 후속 메시지 처리: 역제안(INPUT_REQUIRED), 성사(COMPLETED + numeric price receipt artifact), 거절(REJECTED)	충족	market_a2a/seller.py:39-54 / market_a2a/seller_turn.py:142-212 / logs/trade-test-attempt-04-console.log:L00010-L00018, L00028-L00045
판매자	어떠한 경우에도 reserve 미만 호가·판매 금지 (seller_deal×3, seller_holds_reserve×3)	충족	market_a2a/seller_turn.py:156-165 (코드 가드) / logs/trade-test-attempt-04-console.log:L00018, L00035, L00045, L00066, L00087, L00108
판매자	종료된 task에 추가 메시지 전송 시 에러 반환 (seller_terminal)	충족	market_a2a/seller.py:52-53 및 a2a-sdk -32004 에러 / logs/trade-test-attempt-04-console.log:L00020-L00023
판매자	하네스(모델)가 호가, 역제안, 수락/거절을 직접 결정	충족	market_a2a/seller_turn.py:88-131 (harness.run 도구 디스패치) / logs/challenge-1/attempt-3/server.log:L00007, L00013, L00029 등
구매자	agent.json의 {url}, {item}, {budget} 치환 명령으로 실행 및 300초 내 종료	충족	agent.json:5-7 / harness_app.py:118-134 / market_a2a/buyer.py:117 (anyio.fail_after(280))
구매자	Agent Card 조회, 모든 요청에 A2A-Version: 1.0 헤더 포함, taskId로 후속 협상 유지 (buyer_protocol_*×6)	충족	market_a2a/remote.py:142-182 / logs/trade-test-attempt-04-console.log:L00115, L00120, L00125, L00130, L00135, L00140
구매자	예산 내 구매 성사(buyer_deal×3) 및 예산 초과 시 미구매(buyer_holds_budget×3)	충족	market_a2a/buyer.py:88-89 (예산 가드) / logs/trade-test-attempt-04-console.log:L00114, L00119, L00124, L00129, L00134, L00139
구매자	하네스(모델)가 모든 제안가와 포기(walk_away)를 결정	조건부 충족	market_a2a/buyer.py:40-50, 134-142 (offer/walk_away 도구). 단, buyer_holds_budget 실제 로그(L00128 등)에서는 모델이 walk_away를 호출하지 않고 230을 5회 반복 후 상대 판매자의 거절로 종료됨 (결함 M-4 참고)
하네스	비대화형 실행 및 프로젝트 지침을 읽지 않도록 빈 디렉터리에서 실행	충족	harness_app.py:59-67 (TemporaryDirectory(prefix="ax06-harness-") 생성 후 os.chdir(directory))
챌린지 1	수천 개 카탈로그 직접 생성·커밋, 이름 없이 설명만으로 SKU 선택, reserve 준수, 컨텍스트 관리	조건부 충족	catalog.json 3,008개(generate_catalog.py:53-190), catalog.py:175-190(최대 6개 반환), logs/challenge-1/attempt-3/summary.json(6/6 통과). 단, catalog_constraints.py의 수제 상품 전용 키워드 필터로 인해 6건 모두 candidates=1로 축소됨 (결함 H-1 참고)
챌린지 2	약 20개 Agent Card 디렉터리, 쇼핑 목록, 단일 총예산 내 전 품목 구매, 중복 구매 0, 동시 협상 및 상태 요약 관리	충족	harness_app.py:78-84(20개 샵), shopping.py:119-167(동시성 5, 원장 관리), buyer.py:97-108(상태 요약), logs/challenge-2/attempt-4/summary.json(20개 카드 중 5곳에서 5품목 1,692/2,200 구매, 중복 0)
제출물	agent.json, 현행 trade_test_week06.py가 생성한 test-report.json(21/21 pass), REPORT.md(600자 이상, 5개 필수 섹션 포함)	충족	test-report.json:1-147 (kit_sha256이 trade_test_week06.py SHA-256 8e00baa4...와 정확히 일치, 21/21 pass: true), REPORT.md (19,179 bytes, 1~5절 완비), logs/current-structure-final.log:L00001-L00007
제출물	logs/trade-test.log 존재 및 TASK_STATE_INPUT_REQUIRED 포함 (check_week06.py 검사 항목)	근거 부족 (패킷 미포함)	logs/current-structure-final.log:L00004에서 로컬 존재는 확인되나, REVIEW_PACKET.md 매니페스트에는 logs/trade-test-attempt-04-console.log만 수록되고 logs/trade-test.log 원문은 미포함

Q2. 모델 판단→도구 호출→관찰→재판단 루프 및 5주차 하네스 기반 입증 범위

1. 실제 모델→도구→관찰→재판단 수행 여부 (확정 입증):
  ⚬ market_a2a/harness.py의 Harness.run(80~142행)은 AsyncOpenAI를 통해 gpt-4.1-nano-2025-04-14(temperature=0, tool_choice="required", parallel_tool_calls=False)를 호출하고, 모델이 선택한 function tool을 dispatch로 실행한 뒤 그 결과를 role="tool" 메시지로 추가하여 result.terminal이 True가 될 때까지 최대 6라운드 반복합니다.
  ⚬ 단일 턴 내 다단계 재판단 실증: logs/challenge-1/attempt-3/server.log L00109~L00127(cable-workspace 사례)에서 구매자가 reserve(360) 미만인 359를 제안했을 때, ① 모델이 1라운드에 decide(action="reject", price=359)를 호출(L00114) → ② 코드 가드(rejection_feedback)가 {"error": "Too early to reject; counter at or above reserve."}를 terminal=False로 반환(L00115) → ③ 모델이 관찰 후 2라운드에 search_catalog 호출(L00119) → ④ 관찰 후 3라운드에 decide(action="quote", price=368)로 역제안을 수행(L00125)하여 실제 재판단 루프가 작동함을 증명합니다.
  ⚬ 턴 간(Inter-turn) 관찰→재판단 구조: 구매자(market_a2a/buyer.py 95행, 134~142행)에서 유효한 offer 도구 호출은 A2A SendMessage 실행 직후 terminal=True를 반환하여 해당 harness.run을 종료하고, 다음 while 루프 반복에서 negotiation.summary()의 recent_observations(최신 2개 관찰)에 상대방 응답을 담아 새로운 harness.run을 호출하는 방식으로 턴 간 재판단을 수행합니다.
2. 5주차 하네스 기반 설명의 입증 범위:
  ⚬ market_a2a/harness.py(15행, 3560행)의 assistant_message 변환과 Harness.run 루프 구조는 MCP 전송 계층을 로컬 함수 디스패치(ToolDispatch)로 치환한 형태임이 코드상 명확하며, 과제 명세(weeks/week-06/README.md 44~47행: "your own week-01 or week-02 loop, ... Any harness that you can call from code works")가 허용하는 자체 하네스 범주에 부합합니다.
  ⚬ 다만 이 패킷에는 5주차 원본(week-05/market/host.py)이 포함되어 있지 않으므로, 5주차 코드와의 직접 비교는 수행하지 못했음(미확인)을 명시합니다.

Q3. A2A 프로토콜 계약 일치성 및 기본 21/21 검사의 보장 범위와 한계

1. 코드와 실제 로그 간 프로토콜 계약 대조:
  ⚬ Agent Card & A2A-Version 1.0: 판매자(seller.py 75~97행)와