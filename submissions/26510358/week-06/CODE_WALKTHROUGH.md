# 코드 읽기 안내

이 구현의 중심은 “하네스가 판단하고, 코드가 검증한 뒤 실행한다”입니다. 먼저 [ARCHITECTURE.md](ARCHITECTURE.md)를 보고 다음 모듈을 순서대로 읽으면 모델 판단과 프로토콜·검증 책임을 구분할 수 있습니다. 개발 보조로 작성한 코드도 제출자가 직접 이해하고 수정할 수 있어야 합니다.

## 모듈과 책임

| 파일·시작점 | 읽을 내용 |
|---|---|
| [harness.py](harness.py) `system_prompt()`, `Harness.decide()` | 역할별 지시, 실제 Codex 호출, JSON 파싱, 검증 실패 재질문. 프롬프트와 원문 실행 출력이 로그에 남는 위치 |
| [구매자 스키마](buyer-decision.schema.json), [판매자 스키마](seller-decision.schema.json) | 역할별 허용 행동과 필드 형식. 구매 금액은 `price`, 판매자가 읽은 제안은 `offer` |
| [quote_normalization.py](quote_normalization.py) `collect_quote_evidence()`, `normalize_quote()`, `contract_issues()` | metadata/DataPart/텍스트의 현재 관측 정규화, 출처·충돌 검사, credits·수량 1 계약 대조 |
| [호가 관측 스키마](quote-observation.schema.json) | 실제 `quote_observer`가 반환하는 호가·상품 ID·견적 ID. 거래 행동·구매 금액을 선택하는 스키마와 구분 |
| [inference_queue.py](inference_queue.py) `InferenceQueue.slot()` | 스레드·프로세스 간 FIFO 입장, 제한 시간, 종료된 소유자 복구, 중앙 설정의 기본 5초 호출 간격 |
| [seller.py](seller.py) `build()`, `Seller._turn()` | Agent Card·A2A 서버, 상품 검색, 거래별 상태·잠금, 호가·영수증 응답 |
| [buyer.py](buyer.py) `negotiate()`, `_has_terminal_receipt_evidence()` | 카드 읽기, 같은 task ID로 흥정, 예산 예약·영수증 확인, 종료 상태와 구매 증거 충돌의 분쟁 처리 |
| [protocol.py](protocol.py) `A2AClient.rpc()` | 버전·출처·응답 ID 검증, 새 요청 deadline 확인, 이미 수신한 완료 응답 보존 |
| [evidence.py](evidence.py) `validate_*_decision()`, `verify_receipt()`, `Ledger` | 가격 제약·영수증 대조와 한 프로세스에서의 원자적 구매 예약 |
| [catalog.py](catalog.py) `search_catalog()` | 한국어·영어 설명과 수치 조건으로 수천 개 상품을 짧은 후보 목록으로 좁히기 |
| [generate_catalog.py](generate_catalog.py) `build_catalog()` | 커밋된 3,003개 합성 상품의 재현 가능한 생성 규칙 |
| [shopping.py](shopping.py) `allocate_budget()`, `select_shop()`, `summarize_coordination()`, `shop_all()` | 총예산의 한도 배분, 실제 카드 선택, 기본 2개 협상 스레드, 원장 사실 요약과 안전한 재선택 |
| [policy.py](policy.py) `context_key()`, `StrategyPolicy.choose()` | 공개 관측·자기 제약에서 전략 프롬프트 선택, 시작 시 고정한 정책 버전 |
| [train_policy.py](train_policy.py) `train()`, `simulate()`, `_outcome()` | v2 합성 협상에서 전략별 보상을 모아 bandit 추정값 갱신, 비공개 한도를 쓰는 평가 계산과 관측 분리 |
| [evaluate_policy.py](evaluate_policy.py) `evaluate()` | 합성 학습과 분리한 실제 하네스의 단일 턴 평가 |
| [promote_policy.py](promote_policy.py) `assemble()`, `verify_trace()`, `main()` | 실제 보고서·원문 CLI 출력과 후보 정책을 대조하고 검증 근거 저장·승격 실행 |
| [run_challenges.py](run_challenges.py), [run_adversarial.py](run_adversarial.py) | 실제 도전 실행과 적대적 검사를 시작·정리하고 근거 저장 |
| [benchmark_prepare.py](benchmark_prepare.py) `prepare()`, [benchmark_a2a.py](benchmark_a2a.py) `run_match()` | PR 커밋 고정·소스 복사 후 원래 제공자 또는 동일 모델로 고정 상대·교차 거래 실행 |
| [benchmark_transport.py](benchmark_transport.py) `invoke()`, [benchmark_summary.py](benchmark_summary.py) `summarize()` | 비교에만 쓰는 실제 Codex 추론 치환과 저장된 거래 근거의 읽기 전용 집계 |
| [tracing.py](tracing.py) `Trace.write()` | 실행 ID별 추가 전용 JSONL 기록과 간략한 콘솔 출력 |

## 한 거래를 따라 읽기

판매자가 첫 요청을 받으면 `Seller._turn()`이 카탈로그 검색 결과만 하네스에 전달합니다. 검색은 소재·높이·수납 조건을 적용하지만 최종 상품을 결정하지 않습니다. 하네스가 후보 중 상품 ID와 호가를 내면 `validate_seller_decision()`이 해당 상품의 하한가를 확인합니다. 통과하면 숫자 호가와 `INPUT_REQUIRED` 상태를 A2A로 반환합니다.

구매자는 `negotiate()`에서 Agent Card를 읽고 거래를 시작합니다. 후속 요청에는 처음 받은 `taskId`를 유지합니다. 구매 판단보다 먼저 `normalize_quote()`가 현재 task·메시지 metadata와 DataPart에서 지원하는 호가·상품·견적 필드를 읽고 출처를 보존합니다. 유일한 정상 숫자 호가가 있으면 관측용 모델 호출을 추가하지 않습니다. 텍스트만 있거나 구조화된 값을 바로 읽을 수 없을 때는 실제 `Harness("quote_observer", ...)`가 호가·상품 ID·견적 ID를 추출합니다. 여러 숫자의 의미를 해석하는 것은 모델이고, 정규식은 반환된 숫자가 원문에 있는지 확인하는 데만 씁니다.

구조화된 가격·상품·견적 필드가 충돌하면 모델이 하나를 임의로 골라 확정하지 않고 중단합니다. 관측 모델이 원문에 없는 숫자·ID를 반환하면 기존 `Harness.decide()`의 검증 실패 재질문으로 돌아갑니다. 호가를 알 수 없으면 null로 유지하며 수락을 허용하지 않습니다. 예산 안에서 새 제안을 하거나 종료하는 선택은 구매 하네스가 합니다. 관측값을 정규화한 뒤 `StrategyPolicy.choose()`를 호출하므로 텍스트형 상대의 실제 호가도 정책 상태의 `ask`에 들어갑니다. 판매자·구매자의 원문은 계속 비신뢰 데이터이며 자기 하한가·예산과 구분합니다.

`currency`·`quantity`는 구조화된 필드에서만 읽고 출처를 남깁니다. 거래의 기본 계약은 credits·수량 1이며, 누락은 기존 과제 상대의 기본 규약으로 허용합니다. 명시된 값이 다르거나 잘못된 타입·수치이면 구매 제안을 보내기 전에 차단합니다. 환율이나 수량을 모델로 추정하지 않습니다. 원문에 있다고 해서 예산 변경 지시를 따르는 것도 아닙니다.

텍스트에만 있는 통화·수량은 `quote_observer` 스키마가 별도 추출하지 않으므로 이 결정적 검사의 대상이 아닙니다. 예를 들어 `110 USD for two lamps`는 구매 하네스의 의미 판단에 의존합니다. `collect_quote_evidence()`는 task metadata도 검사하므로 이전 task 호가와 현재 메시지 호가가 다르면 충돌로 중단합니다. task metadata를 최신 값으로 간주할 수 없는 외부 구현에는 호환 제한이 있습니다.

현재 역할 프롬프트는 구매자에게 낮은 구매 가격과 거래 성사, 판매자에게 높은 판매 가격과 거래 성사를 함께 추구하도록 지시합니다. 구매 예산은 반드시 다 써야 할 목표가 아니며 판매 하한가도 목표 판매가나 생산원가가 아닙니다. 이전 제안과 상대의 실제 반응으로 양보하되 비공개 한도를 안다고 가정하지 않습니다. 밴딧이 고르는 세 전략은 이 목표 아래의 진행 속도·검토 방식을 조정합니다.

하네스가 낸 구매 가격이 예산을 넘으면 실행하지 않고 검증 원인을 담아 재질문합니다. 통과한 **모든 제안**은 송신 전에 원장에 예약합니다. 역제안도 판매자가 바로 수락할 수 있기 때문입니다. 코드가 예산에 맞게 가격을 깎아 보내는 경로는 없습니다.

판매자가 수락하면 숫자 `price`를 가진 영수증과 `COMPLETED` 상태를 반환합니다. 구매자는 자신이 제안한 가격, 견적의 상품 ID, 예산, task ID와 대조하고, 영수증에 명시된 통화·수량도 기본 계약과 비교한 뒤 원장을 확정합니다. 설명형 요청에서는 처음 견적의 상품 ID를 거래 중 고정하며, 도전 실행기는 최종 상품의 실제 카탈로그 속성도 설명 조건과 대조합니다. 현재 구매자는 외부 가게의 전체 상품 속성을 독립적으로 검증하는 기능까지 제공하지 않습니다.

`A2AClient.rpc()`는 새 HTTP 요청 전에 deadline을 검사하되 성공적으로 수신한 응답은 검증·파싱합니다. 수신·처리가 한도를 넘어도 이미 승인한 구매의 완료 증거를 버리지 않기 위해서입니다. `negotiate()`는 마지막 8번째 제안의 응답도 먼저 읽어 완료·명시적 종료를 처리하고, 다음 가격 판단 전에 턴·시간 한도를 검사합니다. 따라서 늦게 받은 견적에서 새 제안을 하지 않으며, 잘못된 JSON-RPC ID·형식이나 응답 유실은 계속 오류·분쟁 경로로 갑니다. 이 실제 buyer/protocol 연결 회귀는 [test_protocol.py](tests/test_protocol.py)에 있습니다.

실패·거절·취소 상태에 구매 증거가 함께 오면 단순 미체결로 해석하지 않습니다. `_has_terminal_receipt_evidence()`는 artifact의 DataPart에 `price` 키가 있거나 artifact 이름이 `receipt`인지 봅니다. 잘못된 가격과 부분 영수증도 포함합니다. `terminal_receipt_conflict`를 기록해 기존 예약을 분쟁으로 유지하며, 예약 없는 응답도 구매를 새로 승인하지 않고 분쟁으로 반환합니다. 영수증 이름·가격 증거가 없는 빈 artifact나 진단은 일반 종료로 처리할 수 있습니다. [혼합 종료 회귀](tests/test_terminal_receipt_conflict.py)는 실제 `shopping → buyer → Ledger` 경로에서 상점 재선택이 일어나지 않는지 확인합니다.

명시한 기본 ID와 로컬 분류·번호 ID는 최초 견적부터 다른 상품으로 바뀌면 거절합니다. 외부 가게의 임의 SKU는 `negotiate(..., expected_item_id="SKU-LAMP_2026-A")` 또는 CLI의 `--expected-item-id`로 지정합니다. 이 값은 구매 목록 키와 별개인 정확한 상품 기대값입니다. 하네스의 `item_id`도 거래에서 고정한 상품 ID와 같아야 합니다.

종료된 task에 새 메시지를 보내는 오류 처리는 A2A SDK의 요청 처리 계층에 맡깁니다. SDK가 메시지와 상태를 운반해도, “어떤 숫자가 가격인가”, “어떤 제안을 수락했는가”, “영수증의 상품이 같은가”는 이 구현이 정한 거래 규약입니다.

## 호가 관측의 실패와 로그 읽기

호가 관측의 실제 실패는 [최초 실행 원문](logs/improvements/20261015/stage-01-native-screen/r01-lamp-ours-buyer/buyer-trace/buyer-bdcd6501383d.jsonl)에서 확인할 수 있습니다. 상대가 `lamp: asking 130. Send your offer as a number.`라고 답했고 관측 모델도 올바른 값을 냈지만, 정규식이 마침표 뒤의 숫자 경계를 잘못 검사해 `Observed asking price must occur in the quoted remote evidence`로 반복 거부했습니다. `_NUMBERS`의 끝 경계를 바꾸어 문장 마침표를 허용하고, `130.50`에서 `130`이나 `50`만 잘라낸 관측은 계속 거부하도록 했습니다. [회귀 테스트](tests/test_quote_normalization.py)와 [수정 검사 로그](logs/improvements/20261015/stage-01b-grounding-unit-XvGNwnPO/unittest.log)를 함께 읽으면 모델의 의미 판단과 코드의 근거 검사가 각각 어디에서 실패할 수 있는지 볼 수 있습니다.

새 실행에서는 `quote_normalized`의 `source`, `evidence_sources`, `ask`를 먼저 보고 다음 `harness_request.context`의 정책 입력을 확인합니다. `quote_observation_started/finished`에는 추가 관측 호출의 남은 시간과 큐 대기 포함 경과 시간이 있습니다. 관측용 스키마와 프롬프트가 실제 호출 명령에 들어갔는지도 확인합니다. 구조화된 정상 호가만 처리했다면 관측 시작·종료 이벤트 없이 정규화 기록이 남습니다.

장보기 구매 모델의 `stance`는 공개할 협상 태도만 표현합니다. `buyer.py`는 모델이 선택한 `negotiable/firm`을 선택적 메타데이터로 보내고 `reason`이나 예산은 전송하지 않습니다. `protocol.py`의 숫자 본문과 기존 단일 구매 경로는 유지합니다. 판매자는 허용된 태도만 `remote_stance`로 읽되 하한가·수락 검증은 그대로 적용합니다. 상대의 최종 제안은 수락 의무나 비공개 예산의 증거가 아닙니다.

판매자의 게시 잠금은 추론 동안 잡지 않습니다. 취소가 먼저 확정되면 나중 모델 결과를 버리고, 영수증 게시가 먼저면 완료 상태를 유지합니다. SDK V2가 executor의 취소 예외를 실패 상태로 바꾸므로 이미 종료된 executor는 추가 이벤트 없이 반환하고, `CancellationAwareRequestHandler`가 바깥 경계에서 취소 불가 오류를 만듭니다. 실제 HTTP 경합 검사는 `tests/test_seller_cancel_http.py`에 있습니다.

## 원장의 상태 전이

`Ledger.reserve()`는 같은 구매 목록 키의 활성 예약·구매가 있는지와 남은 총예산을 잠금 안에서 함께 검사합니다. `item_key`는 구매 목록의 한 칸을 식별하고, `expected_item`은 영수증에 나와야 하는 상품을 식별합니다. 설명형 요청에서도 두 값을 혼동하지 않아야 합니다.

정상 흐름은 `reserved → committed`입니다. 구매 증거와 충돌하지 않는 명확한 거절이나 협상 중단은 `reserved → released`로 금액을 해제합니다. 응답 유실·영수증 불일치·종료 상태와 영수증 증거의 충돌은 `reserved → disputed`로 기록하고 금액을 유지합니다. 분쟁은 실제 판매가 취소되었다는 뜻이 아니며 자동 환불도 없습니다.

같은 영수증으로 확정을 재시도하면 결과를 재사용하고, 이미 사용한 구매 ID나 중복 상품 예약은 거절합니다. 이 잠금은 한 프로세스의 스레드 사이에서만 효력이 있습니다. 파일에 원장을 저장한다고 서로 다른 프로세스의 경쟁까지 해결되는 것은 아닙니다.

## 많은 상품과 여러 가게

`search_catalog()`은 이름뿐 아니라 한국어·영어 분류·소재 동의어, 명시된 높이 조건, 필요한 책 수를 사용합니다. 불가능한 조건은 빈 후보 목록으로 반환하고, 책상 높이처럼 알려지지 않은 값은 추정하지 않습니다. 후보는 깊은 복사로 반환하므로 호출자가 공유 카탈로그의 하한가를 바꾸지 못합니다.

`read_directory()`는 실제 가게에서 카드와 능력을 읽습니다. `select_shop()`은 모든 카드를 실제 하네스에 전달하고, 코드는 선택한 ID가 디렉터리에 있는지만 검증합니다. 가게 선택을 상품별로 하드코딩하지 않습니다.

`shop_all()`은 구매 목록 키가 중복되지 않는지 확인합니다. 개별 예산이 없으면 `allocate_budget()`이 전체 목록·총예산·공개 Agent Card만 실제 하네스에 전달해 한 번에 한도 벡터를 받습니다. 비공개 하한가를 넘기거나 모델을 부르기 전 별도 거래를 열지 않습니다. 코드는 모든 키가 한 번씩 포함되었는지, 배분 합계가 총예산 이내인지 검증합니다. 사용자가 명시한 개별 한도가 있으면 보존합니다. 공개 카드만으로 배분한 한도가 실제 판매 가격에 충분하다는 보장은 없습니다.

협상마다 짧은 이력 요약·독립 task·개별 로그를 유지하고 공유 원장으로 총액을 지킵니다. 기본 스레드는 2개이며, 전체 장보기 600초 안에서 개별 협상은 기본 280초와 전체 잔여 시간 중 작은 값으로 제한합니다. `validate_execution_limits()`가 유한한 양수·300초 미만의 개별 상한과 양의 정수 worker를 모델 호출 전에 확인합니다. 두 CLI의 `--workers`, `--negotiation-timeout`으로 조정할 수 있고, 단일 구매자와 도전 1의 기본 240초는 바뀌지 않습니다. `invocation_slot()`은 공유 추론 한 개를 FIFO 대기열과 완료 후 기본 5초 간격으로 조절합니다. 중앙 `inference_slot(interval=None)`이 `A2A_INFERENCE_GAP_SECONDS`의 유한한 0 이상 값을 검증합니다. 명시적 `interval`은 그 값을 우선하므로 기존 짧은 간격 테스트는 바뀌지 않습니다. 일반 하네스와 비교 transport는 간격을 하드코딩하지 않고 이 정책을 공유합니다.

`no_deal`과 실제 `TASK_STATE_FAILED`·`TASK_STATE_REJECTED`·`TASK_STATE_CANCELED` 중 하나가 함께 있어야 재선택을 검토합니다. 해당 목록 키의 모든 기록이 released인지도 확인하며, 기록이 전혀 없는 경우는 지출이 없는 것으로 처리합니다. 전체 시간이 남아 있을 때 최대 한 번만 실제 하네스가 상점을 다시 고르고 `attempt-01/attempt-02` 로그를 나눕니다. 응답 유실·분쟁·완료·시간 초과·진행 중 응답·모델의 단순 중단·회차 제한 종료에는 재구매하지 않습니다. 명확히 거절된 거래와 응답을 받지 못한 거래를 구분하는 조건입니다.

`summarize_coordination()`은 상점 선택 전과 실제 구매 시도 전에 원장을 읽습니다. 현재 예산 한도, 실제 총액·확정·예약·가용액, 이 목록 키의 상태별 기록 개수, 다른 확정·미확정 구매, 남은 시간을 작은 사실 객체로 만듭니다. 원문 대화나 전체 영수증, 판매자의 비공개 하한가는 포함하지 않습니다. 실패 이유가 길면 일부만 보존하고 잘랐다는 표시를 남깁니다. 요약용 추가 모델 호출은 없습니다.

`select_shop()`은 이 객체와 정확한 이전 종료 상태를 받고, `negotiate(..., coordination_context=...)`도 시도 직전 스냅샷을 받습니다. 구매 하네스는 `purpose=shopping_list`일 때 공유 호출 용량과 전체 목록의 성사를 고려해 반복적인 작은 양보보다 의미 있는 진전을 검토합니다. 이는 가격을 계산하는 규칙이 아니며, 결정 가격은 실제 모델이 냅니다. 스냅샷은 매 턴의 실시간 원장이 아니고, 최상위 `budget`이나 코드의 예약·영수증 검증을 바꾸지 않습니다. 한 번 배분한 개별 예산도 재배분하지 않습니다.

## 다른 구현과 비교한 근거 읽기

재현 명령 전체는 [README의 비교 안내](README.md#다른-구현과-a2a-비교-재현)에 있습니다. 비교용 의존성은 `python3 -m pip install -r requirements-benchmark.txt`로 준비하며, 앱의 기본 의존성과 구분합니다. `benchmark_prepare.py`는 고정 SHA의 원본 소스를 외부 새 디렉터리에 복사하고 해시·명령·준비 실패를 남깁니다. 이 단계에서는 모델이나 에이전트를 실행하지 않습니다.

`benchmark_a2a.py`에서 `--mode`는 `native` 또는 `controlled`, `--kind`는 `fixed` 또는 `cross`로 독립적으로 선택합니다. native는 원래 제공자 환경, controlled는 상대의 CLI/HTTP 추론만 실제 `gpt-6-sol/low`로 치환한 환경입니다. `benchmark_transport.py`는 가격을 보정하거나 대신 판단하지 않고 원래 요청과 실제 CLI 출력을 남깁니다. 실제 gap은 queue 이벤트의 `interval_seconds`와 transport 요청의 `queue_interval_seconds`에 남습니다. 예전 10초 실행과 새 기본 5초 실행을 같은 조건으로 합치지 않습니다. 원래 모델의 도구·생성 파라미터·지연을 재현하지 않으며 지원하지 않는 요청은 실패로 남습니다. 원본 소스가 유지됐다고 원래 모델까지 재현됐다는 뜻은 아닙니다.

fixed는 각 구현을 과제의 고정 상대와 거래시키고, cross는 우리 구매자↔상대 판매자와 상대 구매자↔우리 판매자를 구분합니다. `--roles`는 선택한 참가자의 역할이며 최종 `buyer`·`seller` 필드로 방향을 확인합니다. `manifest.json`의 커밋·소스 해시·실행기 해시, 개별 wire 로그와 원문 CLI, `summary.json`의 실제 성사·안전 문제·오류를 차례로 대조합니다.

마지막 `benchmark_summary.py`는 저장된 결과만 읽고 모드·역할·상품·예산·실행기별로 나누어 집계합니다. 미완료 checkpoint나 합성 stub을 실제 완료 성과와 섞지 않고, 모르는 호출 수·지연을 0으로 채우지 않습니다. 공유 HTTP 로그는 전체 호출 근거이며 개별 매치에 임의 귀속하지 않습니다. 공식 과제 통과, 고정 상대의 가격, 직접 교차 거래의 상호운용은 서로 다른 결과이므로 한 줄 순위로 바꾸지 않습니다.

## 학습이 바꾸는 부분

`context_key()`가 협상 단계, 관측 가격과 자기 한도의 비율, 의심 신호를 간단한 상태로 묶습니다. 구매자 정책 입력에는 판매자의 비공개 하한가를 넣지 않습니다. 판매자 정책도 구매자의 비공개 예산을 입력으로 받지 않습니다.

`train()`은 전략을 탐색하고 합성 협상 한 번의 보상을 받아 해당 상태·전략의 평균 추정값을 갱신합니다. 이는 단일 전략 선택의 contextual bandit이며 LLM 가중치 학습이나 전체 대화의 PPO 학습은 아닙니다. 합성 환경의 가격 공식은 학습·시뮬레이션에만 있고, 실제 판매자·구매자의 가격과 행동은 계속 하네스가 결정합니다.

단계 4의 `negotiation-surrogate-v2`는 같은 관측에서도 전략·진행 단계에 따라 서로 다른 합성 행동을 허용합니다. 예산 안의 호가가 곧바로 수락 의무가 되지 않으며 `balanced/patient`는 여유가 있을 때 할인 제안을 탐색하고 `direct`와 마지막 턴은 가능한 성사를 우선 검토합니다. 상대의 양보와 중단은 명시된 모의 규칙입니다. 실제 `STRATEGIES` 문구도 역할별 탐색·진전·양보 속도를 구분하지만, 합성 가격 공식과 LLM의 응답은 동일한 실행 모델이 아닙니다.

`_outcome()`은 성사 여부와 `budget - reserve`에서 자기 이익이 차지하는 비율, 호출 비용으로 보상을 계산합니다. 양쪽 한도를 함께 쓰는 이 비율은 합성 평가 전용이며, 모의 상대는 자기 한도로 응답합니다. `Scenario.observation()`과 `initial_action()`을 함께 읽어 구매자 행동에는 상대 reserve가, 판매자 행동에는 상대 budget이 전달되지 않는지 확인합니다. 사기 시나리오는 관측 가능한 영수증 위반을 모의하고 공통 guard가 차단한다고 가정하므로 실제 사기 검출 성능의 근거가 아닙니다.

단계 1–3b의 관측·조정 개선 뒤, 단계 4에서 seed 42·5,000회 갱신한 [env2 후보](policies/candidate-env2-20261015.json)를 생성했습니다. [학습 보고서](logs/improvements/20261015/stage-04-training-report.json)는 학습 당시의 합성 결과로 보존하고, 이후 실제 거래 screen·PR296 probe를 별도 기록했습니다. probe는 미체결이었고 [후보를 승격하지 않기로 결정](logs/improvements/20261015/stage-04-opponent-probe-decision.json)했습니다. 후보 전용 독립 holdout·공격·공식 검사 gate는 완료하지 않았습니다. 기본 활성 파일과 기존 Q값은 유지하되 공통 전략 안내 문구는 변경됐으므로, 활성 파일 해시가 같다고 전체 실행 행동이 같지는 않습니다. 후보는 전용 사본에서 평가했고 기본 런타임에 배포하지 않았습니다. 전역 예산 재배분이나 실제 실패 로그에서 자동 학습하는 기능도 없습니다. 실제 가격·성사 결과는 보고서의 완료된 근거로 확인합니다.

후보 JSON을 만들었다고 실행 정책이 자동 개선되는 것은 아닙니다. 별도 실제 평가, 적대적 검사, 후보를 사용한 공식 거래 검사 근거를 확인해야 합니다. `promote_policy()`가 후보 해시와 결과의 출처를 검사하며, 실행 중인 `StrategyPolicy` 객체는 정책 교체 후에도 이미 읽은 버전을 유지합니다. 실제 실행 로그를 이용한 온라인 자동 학습은 현재 구현하지 않았습니다.

`promote_policy.py`는 후보·holdout·적대적 보고서·공식 보고서·공식 추론 로그를 인자로 받아 원문까지 대조합니다. `--verify-only`는 검증 근거 JSON만 저장합니다. 옵션을 제거하면 검증 후 `policies/active.json`을 원자적으로 교체합니다. [README의 승격 명령](README.md)에 모든 인자를 명시했습니다. 현재 모델과 다른 모델의 추론 로그를 섞거나 실제 모델 검사를 건너뛴 보고서는 근거가 될 수 없습니다.

공식 실행은 `A2A_TRACE_DIR`로 새 추론 디렉터리를 지정하고 `policy_loaded`와 `harness_request`를 확인합니다. 명시한 개별 로그 경로가 있으면 환경변수보다 우선합니다. 현재 승격 점검은 공식 실행의 판매자·구매자 추론 로그가 분리된 한 디렉터리에 있는지 확인하므로, 도전이나 임의 실행을 그 디렉터리에 추가하지 않습니다.

## 최종 검증 원문 따라 읽기

[최종 status](logs/improvements/20261015/final-active-final-status.json)의 공식·도전·구조 단계 `passed`와 소스·선택 정책·활성 정책·스크립트·검사기 불변을 먼저 봅니다. [독립 감사](logs/improvements/20261015/final-active-independent-validation-audit-20261015T084150Z.json)는 같은 결과를 원문과 다시 대조했고 오류가 없습니다. 기본 5초 간격과 기존 활성 Q값으로 공식 21/21, 실제 3,003개 C1, 실제 20개 상점 C2를 통과했습니다. CLI 시작 근거 117회는 실제 호출 수이며 토큰 사용량은 아닙니다.

[C1 요약](logs/challenge-1/20261015T083337Z-0d22d2ea/summary.json)의 선택 상품·속성·최저가·지불 220을 대조하고, [C2 요약](logs/challenge-2/20261015T083337Z-0d22d2ea/summary.json)의 영수증 100/220/280과 원장 확정 600·예약 0·분쟁 없음·각 첫 시도를 확인합니다. 이전 C2의 540보다 지출이 높으므로 이 검증은 목록 성사와 예산·중복 제약을 확인한 것이며 더 싼 장보기를 입증하지 않습니다. 통제 교차·native·hosted·후보 사본은 별도 실험이고, 활성 정책의 공식 통과를 env2 후보의 승격 근거로 옮기지 않습니다.

## 직접 확인할 질문

- `harness_decision`의 가격이 어디에서 검증되며, 실패할 때 어떤 피드백이 재질문에 들어가는가?
- 구조화된 호가와 텍스트 호가에서 관측 호출 수가 왜 달라지며, 정책은 어느 시점에 `ask`를 받는가?
- 영수증 가격·상품을 바꾼 테스트와 응답 유실 테스트에서 예약 금액이 왜 유지되는가?
- 같은 상품을 두 스레드가 예약하려 할 때 어느 잠금과 상태 검사가 중복 구매를 막는가?
- 합성 학습 보고서와 실제 모델 평가 보고서의 `mode`·정책 해시가 어떻게 다른가?
- A2A SDK가 정한 메시지 구조와 우리가 정한 가격·상품 의미는 어디에서 만나는가?

질문은 [tests](tests/)와 실행별 JSONL 로그를 나란히 읽으며 확인할 수 있습니다. `inspect_untrusted()`의 신호는 추가 검토의 근거이며 사기 사실의 증명은 아닙니다. 거래 증거 검증과 실물 배송·판매자 신원 인증의 범위를 구분해서 설명해야 합니다.
