# Week 06 보고서 · 26510121

작성 중인 보고서다. 기본 판매자·구매자와 필수 도전 1을 구현·검증했다. 필수 도전 2는 미완료이며, 그 결과를 보완하기 전에는 최종 제출이 완료된 것으로 보지 않는다.

## 1. 하네스, 모델, 실행 명령

직접 작성한 Python 하네스가 OpenAI Responses API의 `gpt-4o-mini`를 사용한다. 지금까지 응답한 실제 모델은 `gpt-4o-mini-2024-07-18`이다. temperature 0, strict JSON Schema, 최대 출력 512토큰, store false를 사용한다. 키는 로컬 `.env` 또는 환경변수에서 읽고 커밋·명령 인자·프롬프트·로그에 넣지 않는다.

작업 디렉터리를 이 제출 폴더로 두고 가상환경을 활성화한 뒤 실행한다. `agent.json`의 실제 명령은 다음과 같다.

```text
python -u seller.py --port {port}
python -u buyer.py --url {url} --item {item} --budget {budget}
```

하네스 worker의 실행 형식은 `python harness_cli.py --role seller --log <새 JSONL 경로>`이며 구매자는 `--role buyer`를 사용한다. 부모 프로세스가 task 문맥과 관찰을 stdin JSON으로 전달한다. worker는 빈 `.runtime/negotiation-empty/`에서 실행하고 검증된 결정 JSON만 stdout으로 반환한다. 모든 가격 선택을 모델에 요청하며 worker 실패를 고정 가격으로 대체하지 않는다.

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

도전 2의 약 20개 가게, 장보기 목록, 총예산, 동시 협상은 아직 구현·실행하지 않았다. 가게별 task 상태, 중앙 예산 예약, line_id 중복 구매 방지 설계만 있다. 실제 상태 요약·예약·구매 결과 로그는 이후 추가해야 한다.

## 검증 상태

- API를 호출하지 않는 검사: 39개 통과. `logs/buyer/offline-20261017-042433-779.log`.
- 도전 1 검사를 추가한 현재 오프라인 검사는 51개 통과. `logs/challenge-1/offline-20261017-150318-399.log`.
- 판매자 단계 확인: 12개 통과. `SELLER_RESULTS.md`.
- 첫 공식 전체 테스트: 17/21 실패. 실패 원본은 스냅샷으로 보존.
- 두 번째 공식 테스트: 15/21 실패. 세 번째: 19/21 실패. 모든 원본과 당시 프롬프트를 스냅샷으로 보관.
- 양쪽 지침과 행동 우선 형식 수정 후 네 번째 공식 실행: **21/21 passed -> PASS**, exit code 0.
- 최종 콘솔: `logs/trade-runs/20261017-043240-026/console.log`. 공식 결과: `test-report.json`, `logs/trade-test.log`.
- 최종 구매자는 lamp를 3회 모두 100에 한 번씩 구매했고, bookshelf에는 예산 이하 제안 후 취소해 거래가 없었다. 버전 헤더와 taskId 조건도 전부 통과했다.
- 최종 판매자는 bookshelf에 260을 반복하며 일부 협상을 INPUT_REQUIRED로 유지했다. 최저가 방어는 통과했지만 정체 협상 종료가 항상 이뤄지는 것은 아니다. 실제 상태를 성공적인 거래나 거절로 바꿔 적지 않는다.
- 기본 단계의 `scripts/check_week06.py` 구조 검사도 `all checks passed`, exit code 0이었다. 당시 출력은 `logs/trade-runs/20261017-043240-026/structure-*.log`에 있다. 구조 검사는 도전 1·2의 실제 수행 여부를 대신 검증하지 않는다.
- 도전 1 확장 후 공식 전체 테스트도 **21/21**, exit code 0으로 재확인했다. 최신 콘솔은 `logs/trade-runs/20261017-060355-148/console.log`, 공식 보고서는 현재 `test-report.json`이다. 이전 성공 보고서·고정 로그도 새 snapshot에 그대로 보존했다.
- 최신 구조 검사도 `all checks passed`, exit code 0이다. 출력은 `logs/trade-runs/20261017-060355-148/structure-*.log`에 보존했다. 이 실행이 남긴 판매자 포트 53462는 명령과 실행 경로를 확인한 뒤 종료했고 cleanup 로그를 보관했다.
