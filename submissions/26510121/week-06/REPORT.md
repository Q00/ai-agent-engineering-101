# Week 06 보고서 · 26510121

기본 판매자·구매자, 필수 도전 1, 필수 도전 2의 기본 장보기 시나리오를 구현·검증했다. 실제 실패와 추가 가격 변동 실험의 미완료도 보존했다. 구현·검증 범위와 제출 PR 생성은 구분한다.

## 1. 하네스, 모델, 실행 명령

직접 작성한 Python 하네스가 OpenAI Responses API의 `gpt-4o-mini`를 사용한다. 지금까지 응답한 실제 모델은 `gpt-4o-mini-2024-07-18`이다. temperature 0, strict JSON Schema, 최대 출력은 개별 협상 512토큰·장보기 계획 1024토큰, store false를 사용한다. 키는 로컬 `.env` 또는 환경변수에서 읽고 커밋·명령 인자·프롬프트·로그에 넣지 않는다.

작업 디렉터리를 이 제출 폴더로 두고 가상환경을 활성화한 뒤 실행한다. `agent.json`의 실제 명령은 다음과 같다.

```text
python -u seller.py --port {port}
python -u buyer.py --url {url} --item {item} --budget {budget}
```

하네스 worker의 실행 형식은 `python harness_cli.py --role seller --log <새 JSONL 경로>`이며 구매자는 `--role buyer`를 사용한다. 부모 프로세스가 task 문맥과 관찰을 stdin JSON으로 전달한다. worker는 빈 `.runtime/negotiation-empty/`에서 실행하고 검증된 결정 JSON만 stdout으로 반환한다. 모든 가격 선택을 모델에 요청하며 worker 실패를 고정 가격으로 대체하지 않는다.

도전 1은 `python seller.py --catalog catalog/products.jsonl --port 9999`로 실행한다. 도전 2 구매자는 `python cart_buyer.py --directory <실행 중 가게의 directory.json> --shopping-list shopping/shopping-list.json`이다. 완전한 재현 검사는 `python -u lab/check_shopping.py`로 판매자 20개와 별도 구매자를 실행한 뒤 서버를 종료한다. 도전 2는 같은 worker의 cart 역할(가게·상한 계획)과 cart_buyer 역할(개별 협상)을 사용한다. 역할별 정확한 프롬프트는 `prompts/cart.md`, `prompts/cart_buyer.md`에 있다.

## 2. 각 에이전트의 판단과 제한

역할 지침은 `prompts/seller.md`, `prompts/buyer.md`, 계약은 `negotiation/contracts.py`에 있다. 결정은 action·item_id·price·짧은 reason·다음 턴 summary를 가진다. 원래 정책과 확인된 상태는 상대 문장·모델 요약과 구분해 전달한다.

판매자는 모델이 첫 호가, 역제안, 수락 또는 거절을 선택한다. 기본 최저가 100/180/260은 설정과 코드 검증에 고정한다. ask·counter·accept는 해당 최저가 이상이어야 하고, accept의 가격은 실제 상대 제안과 같아야 한다. 검증 후에만 A2A 상태와 영수증으로 반영한다. 기본 상품명 라우팅은 코드가 담당한다. `--catalog catalog/products.jsonl` 모드에서는 모델이 설명을 해석하고 검색 후보 중 상품을 선택한 뒤 원본 상품의 최저가를 재조회한다.

구매자는 카드와 skill을 읽고, 현재 호가·자기 예산·이전 제안·최근 대화·요약으로 모델이 offer/accept/walk_away를 선택한다. 판매자의 비공개 최저가나 테스트 상대의 내부 설정은 제공하지 않는다. 모든 제안·수락 가격을 예산과 비교하고, 최종 영수증의 품목·유한한 숫자 가격·합의 금액도 확인한다. SDK가 숫자 120을 120.0으로 보내는 것은 허용하되 bool·문자열·비정수 가격은 거절한다.

모델 결과의 모양이나 가격 제한이 틀리면 최대 한 번 오류를 알려 수정 요청한다. 다시 틀리면 FAILED로 남긴다. 가격을 코드에서 보정하지 않는다. task별 기억은 최근 4턴과 최대 600자 요약이며, 최저가·예산·taskId는 요약에서 복원하지 않는다. 구매자는 최대 8회 가격 교환과 240초 자체 기한을 사용한다. 제안 응답이 유실되면 같은 숫자를 재전송하지 않고 GetTask로 확인하며, 불확실한 결과를 구매 성공으로 꾸미지 않는다.

## 3. 실제 실패와 수정

첫 공식 실행은 **17/21 통과, 4개 실패**였다. 원본은 `logs/trade-runs/20261017-042509-898/console.log`와 `snapshot/`에, 당시 파일은 커밋 `6bc7501`에 보존했다. 첫 번째 램프 구매는 120에 성공했지만 두 번째·세 번째는 처음 호가 130이 예산 120보다 높다는 이유로 구매자가 제안을 한 번도 하지 않고 취소했다.

실제 모델 사유 (`logs/buyer/run-20261017T042546.578003Z/buyer-events.jsonl`):

> The asking price exceeds my budget and I cannot negotiate further.

실제 공식 테스트 출력:

```text
FAIL  buyer_deal #2: deals [] (budget 120)
FAIL  buyer_protocol_deal #2: posts 2, without A2A-Version 1.0 0, follow-ups with taskId 0
17/21 passed -> FAIL
```

초기 프롬프트는 협상 가능성을 고려하라고 했지만 이를 충분히 실행하지 않았다. `672d6b4`에서 첫 호가와 확정 최저가를 구분하고, 보통의 협상에서는 예산 안의 제안을 한 번 시도한 뒤 실제 응답을 보고 중단하도록 지침을 구체화했다. 명시적 협상 거부·구매 불가·감당할 수 없는 고정 최저가는 예외다. 고정 제안 사다리나 예산 숫자를 가격 계산 공식으로 넣지 않았다.

프롬프트만 수정한 두 번째 실행도 **15/21**로 실패했다. `logs/trade-runs/20261017-042717-448/snapshot/`에 당시 보고서·프롬프트·원본 로그를 보관했다. 램프 3회 모두 첫 제안 없이 중단했다. 실제 요청에 새 지침이 전달됐음을 모델 로그에서 확인했다.

그다음 구매자 JSON Schema의 필드 순서를 `action → item_id → price → reason → summary`로 바꿨다. 기존에는 nullable price를 행동보다 먼저 생성했다. 프롬프트에도 먼저 행동을 고르고 offer 가격은 판매자 호가와 다른 자기 제안임을 명시했다. 이 두 변경 후, 동일한 실패 관찰을 재사용한 모델 실험 `logs/buyer/probe-20261017T042925.240882Z/`에서 3회 모두 action=offer, price=100이 나왔다. 이는 직접 거래 결과가 아니며, 필드 순서 하나만의 효과를 분리한 실험도 아니다. 가격 100은 모델의 실제 출력이고 코드의 상수가 아니다.

세 번째 공식 실행은 **19/21**이었다. 구매자 램프 3회는 모두 100에 구매했고, 책장은 예산 안의 제안 후 중단했다. 그러나 판매자가 lamp에 150을 부른 두 회차에서 낮은 첫 제안 60을 즉시 거절했다. 실제 출력은 `FAIL  seller_deal #1: TASK_STATE_REJECTED, receipt price None, asks [150]`였다. 스냅샷은 `logs/trade-runs/20261017-043017-273/snapshot/`이다. 앞선 판매자 단계 통과만으로 모델 판단의 모든 변동을 확인한 것은 아니었다.

판매자에도 action을 먼저 생성하도록 형식을 통일하고, 낮은 첫 제안에는 역제안하며 제안이 개선되는 동안 협상을 이어가도록 정책을 명시했다. 최저가를 충족하는 관찰된 제안에는 실현 가능한 거래를 우선하고, 제안이 정체되거나 구매자가 하한을 거절하면 중단하도록 했다. 이 변경도 모델의 판단 지침이며 코드가 가격을 대신 계산하지 않는다. 실제 실패 관찰을 재사용한 `logs/seller/probe-20261017T043225.031370Z/`에서는 3회 모두 counter=100을 선택했다. 이 실험은 실제 거래와 구분하며 이후 공식 테스트로 통합 검증한다.

초기 A2A 실습에서도 echo 카드를 판매자와 같은 JSON-RPC·skill 조건으로 검사해 중단했고, echo의 실제 REST 카드에 맞춰 검사를 구분했다. Claude 구독 접근 제한과 Codex CLI 모델 접근 오류도 있었다. 실제 오류와 경로는 `lab/RESULTS.md`에 인용돼 있다. 오프라인 테스트의 모의 오류는 실제 모델 실패와 구분한다.

## 4. A2A가 해결한 것과 별도 합의

A2A는 Agent Card로 요청 주소와 기능을 찾고, 버전 헤더·SendMessage·taskId·task 상태·artifact로 위임과 다중 턴 결과를 연결하는 틀을 제공했다. 그러나 숫자 메시지가 가격 제안인지 수락인지, 마지막 숫자를 호가로 읽는지, 어떤 품목을 뜻하는지, 예산과 최저가를 어디서 강제하는지는 별도로 맞춰야 했다. 기본 단계에서는 숫자만 있는 정수 제안을 보내고 판매자의 INPUT_REQUIRED 메시지 마지막 숫자를 호가로 읽으며, COMPLETED 영수증 data Part의 item과 numeric price를 검사하기로 했다. A2A의 COMPLETED 자체가 예산 준수나 올바른 상품을 보증하지 않으므로 거래 내용을 따로 검증한다.

## 5. 필수 도전 1·2

도전 1은 원본 합성 데이터 3,000개를 생성 코드·출처와 함께 커밋했다. 모델이 용도·재질·색상·책 수·크기 조건을 읽고, 코드가 필터한 최대 8개 중 모델이 상품을 고른다. 후보 밖 ID와 조건 위반 선택을 막고 원본 행의 최저가로 기존 협상 검증을 수행한다. 후보에는 최저가를 넣지 않는다. 협상 턴에는 고정 ID·최저가·최근 대화·요약만 전달한다. 전체 데이터는 891,294바이트, 실제 후보 JSON만 최대 2,258자였다. 이는 전체 프롬프트 토큰 수와는 구분한다.

실제 최종 결과는 `logs/challenge-1/run-20261017T060327.556765Z/summary.json`의 **15/15**다. 다섯 용도의 구매 각 2회, 추가 설명 후 구매 2건으로 영수증은 12개다. 일치 재고 없음·미지원 재질은 거절됐고 최저가 미만 제안에는 거래가 없었다. 정답 조건은 모델 해석과 별도로 검사 코드에 적어 실제 상품 속성·영수증·가격을 확인했다. 기본 고정 구매자 검사와 도전 2의 모델 구매자를 혼동하지 않는다.

한국어로 파란 금속·문 달린 보관·책 60권·최대 높이 60cm·너비 40cm를 요청한 실제 `server/server-events.jsonl`의 선택 사유:

> This blue metal cabinet holds 60 books and meets the height and width limits specified.

선택 상품은 `cabinet-0362`, 최저가 400, 실제 거래가는 450이었다. 책상 높이를 모르는 요청에서는 다음을 질문했고, 같은 task에 80cm라는 추가 설명을 준 뒤 구매했다.

> What is the height of your desk to determine the maximum height for the bookshelf?

실제 최저가 방어 로그의 `actual-product-reserve-no-sale`은 선택 상품 `bookshelf-0002`, reserve=370, 제안 369, 호가 `[400,370,370]`, 최종 CANCELED다. 기본 책장의 하한 260을 재사용한 것이 아니라 실제 행의 370을 적용했다.

실패도 보존했다. 검사 도구 시작 시 `Tee`의 `isatty` 누락으로 API 호출 전에 멈췄다. 이를 수정한 첫 실제 도전 실행은 9/15였다 (`logs/challenge-1/run-20261017T060018.636561Z/`). 모델이 `{"action":"select","item_id":"chair-0213","reason":""}`를 두 번 반환해 `invalid_reason_length`, `decision_invalid_after_repair`가 기록됐다. JSON Schema에 사유 길이 1..240과 명시적 지침을 추가했다. 첫 응답의 세 모델 호출이 10초 대기를 넘은 `ReadTimeout`에는 카탈로그 클라이언트 대기를 125초로 늘렸다. 또 clarify에 이미 아는 조건을 남겼다는 이유로 실패하던 계약을 바꿨다. 알려진 정보는 보존하되 action=search만 검색·선택으로 이어진다. 수정 후 15/15 실행의 검증 재시도는 없었다. 자세한 파일·명령·경계는 `CHALLENGE1_RESULTS.md`에 있다.

도전 2는 자체 합성 가게 20개를 실제 A2A 서버로 띄웠다. 용도별 가게 4개, 가게마다 상품과 최저가가 다르다. 구매자는 실제 조회한 공개 카드와 5개 품목 목록·총예산만 받는다. skill 태그로 품목별 최대 4개 후보를 만들고 모델이 가게·상한을 선택한다. 최초 계획에는 카드 20개의 skill 발췌를 넣지만 개별 협상에는 해당 카드 하나와 그 협상의 최근 4턴·최대 600자 요약만 전달한다. 재계획은 미구매 품목의 후보와 이전 요약을 받는다. 비공개 최저가는 구매자에게 제공하지 않는다.

중앙 장부는 **지출 + 예약 ≤ 총예산**을 유지한다. 모든 협상 예산을 원자적으로 예약한 뒤 최대 4개를 동시 실행한다. taskId·품목·상한·실제 제안·영수증은 요약과 별도로 보관한다. 확인된 취소·거절만 예약을 해제해 다른 가게를 시도할 수 있다. 구매 완료·불확실 품목은 다시 주문하지 않는다. 응답 유실 때 GetTask로 확인하고, 계속 불확실하면 예약을 유지해 미완료로 반환한다. 이 안전 성질과 모든 경우에 구매를 완료하는 능력은 다르다.

최종 기본 실행은 `logs/challenge-2/run-20261017T070246.190291Z/`의 **3/3**이다. 실제 거래는 매회 5건, 지출은 1160/1160/1260으로 각 총예산 1500 이하, 중복은 없고 최대 4개 협상이 동시에 활성 상태였다. 모든 판매자의 실제 영수증을 독립적으로 세어 구매자가 놓친 중복도 확인했다. 첫 계획의 품목별 상한은 `[120,200,280,320,300]`, 합계 1220이었다. 실제 `cart_memory`에는 `"line_id":"lamp"`, `"summary_chars":155`, `"recent_count":1`이 기록됐다. 3회차는 확인된 일부 중단 후 다른 가게에서 구매했다. 기본 성공들은 개별 협상이 한 번에 끝나 이전 요약을 다시 읽은 buyer 호출은 0회였으며, 이를 다중 턴 기억 성공으로 부풀리지 않는다.

첫 실제 도전 2 실행 `run-20261017T065322.410142Z/`은 4개 구매 후 미완료였다. 판매자 모델이 다음처럼 지원되는 의자를 잘못 거절했다.

> The request specifies a wooden seat, but the color white is not supported for chairs.

지원 도메인을 trusted context에 넣고 지원되는 속성 조합을 unsupported로 분류하지 못하게 검사했다. 재고는 코드가 검색한다. 같은 실행에서 모델의 상한 합계 1620도 `allocations_exceed_available_budget`으로 차단했다. 수정된 필드 합계는 1500인데 요약은 1460이라고 했다. 따라서 요약의 산술 결과도 장부에 사용하지 않는다.

더 좁은 예산 1250에서는 마지막 cabinet에 190만 배정하고, 다른 구매 후 330이 남아도 중단한 실패가 있었다 (`run-20261017T065830.038827Z/`). 전체 품목의 배분 균형·새 가용 예산 비교·장보기의 가격 절약 지침을 추가했다. 그 뒤 `run-20261017T070029.135645Z/`은 5개를 1200에 구매했지만, 개별 협상이 한 번에 끝나 추가 `--require-summary` 커버리지 조건이 실패했다. 해당 summary.json은 false로 그대로 남겼다.

별도 가격 변동 실험 `run-20261017T070244.704687Z/`은 비공개 하한에 27을 더했다. 실제 사용한 데이터는 private-inputs/에 있다. 이 실행은 낮은 제안 반복·worker_failed·turn limit으로 **지출 0·예약 620·미완료**였으며 전 품목 구매 통과가 아니다. worker_failed의 세부 원인은 확정하지 못했다. 다만 원본 요청을 읽기 전용으로 검사한 memory-audit.json에서는 32회 중 27회가 이전 요약을 재사용했고 최근 대화 최대 4턴·요약 최대 251자·taskId와 품목 분리를 확인했다. 실제 desk 후속 요약:

> The seller is asking 350 for the white wood desk, which exceeds my budget of 320. I am making an initial offer of 320, which is my maximum budget for this item. I hope the seller will accept this offer.

trusted budget=320, verified taskId·last_offer=320은 그 요약 바깥에서 유지됐다. 이것은 기억 전달의 증거이며 구매 완료 증거는 아니다. 불확실한 예약을 해제해 재주문하지 않았다. 최종 예산 50 검사 `run-20261017T070554.827763Z/`에서는 planner가 stop을 선택하고 실제 거래 0·지출 0·예약 0·incomplete를 반환했다. 기대한 미구매 결과가 통과한 것이지 상품 구매 성공을 뜻하지 않는다. 전체 파일·명령·실패·현재 한계는 `CHALLENGE2_RESULTS.md`에 정리했다.

## 검증 상태

- API를 호출하지 않는 검사: 39개 통과. `logs/buyer/offline-20261017-042433-779.log`.
- 도전 1 단계의 오프라인 검사는 51개 통과. `logs/challenge-1/offline-20261017-150318-399.log`.
- 판매자 단계 확인: 12개 통과. `SELLER_RESULTS.md`.
- 첫 공식 전체 테스트: 17/21 실패. 실패 원본은 스냅샷으로 보존.
- 두 번째 공식 테스트: 15/21 실패. 세 번째: 19/21 실패. 모든 원본과 당시 프롬프트를 스냅샷으로 보관.
- 양쪽 지침과 행동 우선 형식 수정 후 네 번째 공식 실행: **21/21 passed -> PASS**, exit code 0.
- 최종 콘솔: `logs/trade-runs/20261017-043240-026/console.log`. 공식 결과: `test-report.json`, `logs/trade-test.log`.
- 최종 구매자는 lamp를 3회 모두 100에 한 번씩 구매했고, bookshelf에는 예산 이하 제안 후 취소해 거래가 없었다. 버전 헤더와 taskId 조건도 전부 통과했다.
- 최종 판매자는 bookshelf에 260을 반복하며 일부 협상을 INPUT_REQUIRED로 유지했다. 최저가 방어는 통과했지만 정체 협상 종료가 항상 이뤄지는 것은 아니다. 실제 상태를 성공적인 거래나 거절로 바꿔 적지 않는다.
- 기본 단계의 `scripts/check_week06.py` 구조 검사도 `all checks passed`, exit code 0이었다. 당시 출력은 `logs/trade-runs/20261017-043240-026/structure-*.log`에 있다. 구조 검사는 도전 1·2의 실제 수행 여부를 대신 검증하지 않는다.
- 도전 1 확장 후 공식 전체 테스트도 **21/21**, exit code 0으로 재확인했다. 최신 콘솔은 `logs/trade-runs/20261017-060355-148/console.log`, 공식 보고서는 현재 `test-report.json`이다. 이전 성공 보고서·고정 로그도 새 snapshot에 그대로 보존했다.
- 도전 1 당시 구조 검사도 `all checks passed`, exit code 0이었다. 출력은 `logs/trade-runs/20261017-060355-148/structure-*.log`에 보존했다. 이 실행이 남긴 판매자 포트 53462는 명령과 실행 경로를 확인한 뒤 종료했고 cleanup 로그를 보관했다.
- 도전 2 확장 후 현재 오프라인 **68개** 통과: `logs/challenge-2/offline-20261017-070020-750.log`.
- 현재 코드의 공식 기본 **21/21**: `logs/trade-runs/20261017-070113-564/console.log`, 현재 `test-report.json`.
- 도전 1도 현재 상품 해석 지침으로 **15/15** 재확인: `logs/challenge-1/run-20261017T065722.908539Z/`.
- 도전 2의 기본 장보기 **3/3**, 추가 예산 50 미구매 **1/1**. 추가 비공개 가격 변동 실험은 미완료로 보존.
- 공식 검사 서버 포트 60925는 명령·실행 경로를 확인한 후 종료했고 같은 공식 실행 폴더에 cleanup 로그를 남겼다.
- 현재 최종 구조 검사도 **all checks passed**, exit code 0이다. 원본은 `logs/trade-runs/20261017-070113-564/structure-*.log`에 있다. 34개 소스 파싱, 현재 공식 21개 결과·명령·보고서·로그 조건을 통과했다.
