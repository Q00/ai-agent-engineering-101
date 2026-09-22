# 출력 토큰 상한 제거 후 45회 재실험 결과

5개 작업 × 3개 조건 × 3회, 총 **45회**를 실행했다. 필수 facts 전부 통과는 **29/45**, 개별 facts는 **306/486**다.
이 수치는 숫자·명시 제약 검사이며 설계 문서 완성도나 실제 구현·테스트 성공률이 아니다.

## 이전 결과 정정과 비교

이전 max_tokens=2200은 사용자가 요청한 조건이 아니라 구현 에이전트가 임의로 넣은 상한이었다. 이전 17회 JSON 잘림을 시스템 자체의 능력 한계로 해석해서는 안 된다.
[기존 원본 결과](../20260921T113158-suite-ab4e67/FINAL_REPORT.md)는 보존했다. 이번에는 실패 17회만 고르지 않고 동일 45개 슬롯을 모두 재실행했다.
[사전 대조](comparison_preflight.json)에서 설정의 유일한 변경은 max_tokens 삭제이며 작업/정답, 모델 프롬프트/역할, 스키마, 스케줄러, 실행 순서가 동일함을 확인했다.
두 실험의 시간 차이와 모델 비결정성도 있으므로 차이 전부를 상한 제거의 인과 효과라고 단정하지 않는다.

이번에는 Fireworks upstream_provider_shared_pool 429가 길게 이어진 구간이 있다. 전체 통과율과 소요 시간은 공급자 가용성에도 영향을 받으므로, 출력 잘림·응답 스키마 실패·내용 오답과 분리해서 해석한다.

|조건|기존 2200 상한|이번 요청 상한 생략|
|---|---:|---:|
|baseline|8/15|10/15|
|homogeneous|11/15|10/15|
|overconfident|9/15|9/15|
|전체|28/45|29/45|

동일 작업/조건/회차 대조: 실패→통과 7, 실패→실패 10, 통과→통과 22, 통과→실패 6.
실제 요청 토큰 상한 필드 없음: True. 2200토큰 초과 응답 23개, 최장 3934 completion tokens.
생략 시 provider 기본값이 적용된다. [OpenRouter 문서](https://openrouter.ai/docs/api/reference/parameters)를 기준으로 API 요청에 애플리케이션 상한을 넣지 않은 실험이며, 제공업체 자체 한도가 무한이라는 주장은 아니다.

## 고정 설정과 실행

실행 소스 `d3014d2b3fbda00c262d5fd5f56402e03a4019e0`. [사전 규약](../SUITE_NO_TOKEN_LIMIT_PROTOCOL.md), [manifest](manifest.json), [CSV](results.csv), [전체 지표](summary.json).
모델 deepseek/deepseek-v4.1-flash / OpenRouter / Fireworks, temperature=0, max_tokens와 max_completion_tokens 생략, reasoning disabled.
모든 단계 strict JSON Schema response_format. 공통 역할표 A/B/C 공유. 요청자는 상황별 역할이며 고정 관리자 없음. 장기 메모리 없음.
max_depth=5, max_tasks=12, max_steps=4, max_calls=64, max_parallel=3. 외부 실험은 직렬, 사이 15초 대기, 실행당 600초 상한.
소켓 30초, 응답 수신 기한 90초/2MB 및 기존 JSON Schema의 필드 크기/업무 검증은 유지했다.
429는 동일 payload로 최대 6회, 기타 오류 최대 2회. 최초 45회 결과는 교체하지 않았다. 429 소진 실패만 별도 recovery batch에서 한 번씩 재실행했으며 아래 본 실험 통계에 합산하지 않는다.
release-review는 위임을 요구하며 새 4개 작업은 직접 실행/위임을 모델이 선택한다. 제공된 합성 자료만 분석하며 웹 검색·실제 코드 실행은 없다.

## 조건별 결과

|조건|자동 통과|facts|LLM 호출|실행 중첩 관측|동일 Worker 중첩|루트 C|보고 비용|
|---|---:|---:|---:|---:|---:|---:|---:|
|baseline|10/15|106/162|156|4|0|0/15|$0.155239|
|homogeneous|10/15|106/162|130|3|1|0/15|$0.102295|
|overconfident|9/15|94/162|208|9|6|10/15|$0.211290|

|작업|baseline|homogeneous|overconfident|
|---|---:|---:|---:|
|release-review|3/3|3/3|2/3|
|game-design-architecture|3/3|3/3|3/3|
|payment-redesign|2/3|2/3|2/3|
|growth-roadmap|1/3|1/3|1/3|
|launch-operations|1/3|1/3|1/3|

호출 494회, HTTP 810회. 반환된 비용 합계 $0.468825. 실행 간 대기를 포함한 첫 시작~마지막 종료 115.0분.
응답 없는 요청의 실제 청구액은 알 수 없다. 기간 전체 계정 비용이나 사람 작업 시간과 동일하지 않다.

## 실패·재시도·응답 검증

HTTP 오류: `{'429': 361}`. 429 영향을 받은 논리 호출 100개 중 55개에서 이후 HTTP 응답을 받았다.
응답 종료 사유: `{'stop': 449}`. 이번 요청에는 2200토큰 상한이 없다. length 종료나 잘못된 JSON이 있으면 제공업체 기본 제한 및 응답 원문과 함께 별도로 기록한다.
request_checks는 실제 요청의 메시지/스키마/provider 일치 여부이고 response_checks는 실제 응답의 JSON/Schema 준수 여부다. 두 검사를 혼동하지 않는다.
HTTP 오류만 있고 모델 응답이 0개인 실행도 response_checks=false다. 이는 생성된 JSON이 스키마를 어긴 것과 다르므로 응답 수와 오류 원문을 함께 확인한다.
실제 provider `{'Fireworks': 449}`, model `{'deepseek/deepseek-v4.1-flash': 449}`.

|검사|결과|
|---|---|
|exact_schedule|True|
|source_unchanged|True|
|case_condition_match|True|
|same_controls_per_case|True|
|request_checks|True|
|response_checks|False|
|trace_checks|True|

아래 실패는 산출물 없는 실행도 0개 정답으로 포함한다. 부분 하위 산출물은 원본 runs 폴더와 열람용 문서에 남는다.

|작업/조건/회차|상태|검사|원인/오답|
|---|---|---:|---|
|growth-roadmap/baseline/1|failed|0/16|growth-roadmap: CallError: HTTP 429|
|growth-roadmap/homogeneous/1|failed|0/16|growth-roadmap: ValueError: no valid bids|
|growth-roadmap/overconfident/1|failed|0/16|growth-roadmap: ValueError: no valid bids|
|launch-operations/baseline/1|failed|0/8|launch-operations: ValueError: no valid bids|
|launch-operations/homogeneous/1|failed|0/8|launch-operations: ValueError: no valid bids|
|launch-operations/overconfident/1|failed|0/8|launch-operations: ValueError: no valid bids|
|payment-redesign/overconfident/3|failed|0/8|payment-redesign/integrate_report: CallError: HTTP 429; payment-redesign: ValueError: child failed or blocked; partial results retained|
|payment-redesign/baseline/3|failed|0/8|payment-redesign: ValueError: no valid bids|
|payment-redesign/homogeneous/3|failed|0/8|payment-redesign: ValueError: no valid bids|
|growth-roadmap/overconfident/3|failed|0/16|growth-roadmap: ValueError: no valid bids|
|growth-roadmap/baseline/3|failed|0/16|growth-roadmap: ValueError: no valid bids|
|growth-roadmap/homogeneous/3|failed|0/16|growth-roadmap: ValueError: no valid bids|
|launch-operations/overconfident/3|failed|0/8|launch-operations: ValueError: no valid bids|
|launch-operations/baseline/3|failed|0/8|launch-operations: ValueError: no valid bids|
|launch-operations/homogeneous/3|failed|0/8|launch-operations: ValueError: no valid bids|
|release-review/overconfident/3|failed|0/12|release-review: ValueError: no valid bids|

## 재귀와 실제 병렬 실행

실제 최대 깊이 분포: `{'1': 19, '0': 26}`. 깊이 5는 허용 상한이며 실측 깊이와 다르다.
병렬성은 execute/synthesize의 call_start~call_end 중첩으로 센다. 동시에 제안만 한 것은 병렬 작업으로 세지 않는다. HTTP 이벤트는 버퍼 기록이므로 그 시각으로 중첩을 계산하지 않는다.
중첩이 있어도 작업이 성공했다는 뜻은 아니다. 표의 both succeeded는 해당 두 하위 작업만의 상태이며 최종 루트 성공과 별개다.

|실행|동시 작업|Worker|초|두 작업 성공|
|---|---|---|---:|---|
|release-review/baseline/1|release-review/profitability + release-review/tech_risk|B + A|3.773|True|
|release-review/homogeneous/1|release-review/profitability_review + release-review/tech_risk_review|B + B|6.573|True|
|release-review/overconfident/1|release-review/tech_risk + release-review/profitability|C + C|5.625|True|
|game-design-architecture/overconfident/1|game-design-architecture/budget_schedule + game-design-architecture/tech_design|C + C|8.813|True|
|payment-redesign/overconfident/1|payment-redesign/migration_math + payment-redesign/tech_design|C + A|1.283|True|
|game-design-architecture/overconfident/2|game-design-architecture/budget_schedule + game-design-architecture/design_draft|C + C|5.568|True|
|payment-redesign/overconfident/2|payment-redesign/ops_plan + payment-redesign/tech_design|C + A|4.326|True|
|payment-redesign/baseline/2|payment-redesign/tech_design + payment-redesign/support_comm|A + C|16.217|True|
|growth-roadmap/overconfident/2|growth-roadmap/business_analysis + growth-roadmap/technical_analysis|C + C|6.071|True|
|release-review/homogeneous/2|release-review/profitability_review + release-review/tech_risk_review|B + C|3.641|True|
|release-review/overconfident/2|release-review/profitability + release-review/tech_risk|C + A|2.567|True|
|release-review/baseline/2|release-review/profitability + release-review/tech_risk|C + A|2.864|True|
|payment-redesign/overconfident/3|payment-redesign/migration_ops + payment-redesign/design_analysis|C + C|4.510|True|
|release-review/baseline/3|release-review/tech_risk + release-review/profitability|A + B|3.439|True|
|game-design-architecture/overconfident/3|game-design-architecture/budget_scope + game-design-architecture/game_design|C + A|5.354|True|
|game-design-architecture/overconfident/3|game-design-architecture/budget_scope + game-design-architecture/architecture|C + A|5.928|True|
|game-design-architecture/overconfident/3|game-design-architecture/game_design + game-design-architecture/architecture|A + A|11.661|True|
|game-design-architecture/homogeneous/3|game-design-architecture/budget_facts + game-design-architecture/game_design|B + C|2.184|True|
|game-design-architecture/homogeneous/3|game-design-architecture/game_design + game-design-architecture/code_architecture|C + A|4.525|True|

## 결정성 및 배정 쏠림

|작업/조건|facts 있는 실행|서로 다른 facts 벡터|서로 다른 전체 산출물|서로 다른 루트 계획|
|---|---:|---:|---:|---:|
|release-review/baseline|3/3|1|3|3|
|release-review/homogeneous|3/3|1|3|3|
|release-review/overconfident|2/3|1|2|2|
|game-design-architecture/baseline|3/3|1|3|3|
|game-design-architecture/homogeneous|3/3|1|3|3|
|game-design-architecture/overconfident|3/3|1|3|3|
|payment-redesign/baseline|2/3|1|2|2|
|payment-redesign/homogeneous|2/3|1|2|2|
|payment-redesign/overconfident|2/3|1|2|3|
|growth-roadmap/baseline|1/3|1|1|1|
|growth-roadmap/homogeneous|1/3|1|1|1|
|growth-roadmap/overconfident|1/3|1|1|1|
|launch-operations/baseline|1/3|1|1|1|
|launch-operations/homogeneous|1/3|1|1|1|
|launch-operations/overconfident|1/3|1|1|1|

facts 일치에 실패 실행은 포함되지 않으므로 항상 전체 통과율과 함께 본다. 전체 산출물/계획 hash는 표현과 작업 ID 차이도 반영하며 의미상 차이 크기를 재는 지표는 아니다.
최초 루트 제안의 실제 요청 payload가 같은 작업·조건·Worker의 3회 반복에서 동일했는가: True. 총 45개 묶음의 입력/응답 hash와 응답 받은 반복 수를 supplementary_metrics.json에 보존했다. 재시도 요청을 별도 반복으로 세지 않는다.
루트 배정 분포(배정 전 실패 포함): `{'baseline': {'A': 9, 'unawarded': 5, 'B': 1}, 'homogeneous': {'A': 10, 'unawarded': 5}, 'overconfident': {'C': 10, 'unawarded': 5}}`. 표의 C/전체 시도 비율과 실제 배정이 있었던 루트에서의 비율을 혼동하지 않는다.
점수는 coverage/feasibility/verification 각 0~2로 평가하고 동점일 때 자기 확신도를 사용한다. 확신도를 가린 점수 평가만으로 과신의 영향을 제거하지 못할 수 있다.

|조건|전체 C 배정|C의 95 이상 유효 입찰|최고 평가 점수가 동점인 배정|
|---|---:|---:|---:|
|baseline|3/28|2/29|25|
|homogeneous|2/23|4/23|23|
|overconfident|31/38|38/38|30|

실행 전 오프라인 검사 96개(기본 30, peer 49, 중단된 research 호환성 17)를 통과했다. 직렬화 요청과 모든 단계/조건에서 토큰 상한 부재 및 strict response_format 유지를 검사했다.
이전 batch의 [고정 응답 재생 10회](../20260921T113158-suite-ab4e67/replay_verification.json)는 당시 구현 검사 기록이며 이번 45회 결과에 합산하지 않는다. 이번 스케줄러 코드는 변경하지 않았다.

## 산출물 품질 점검

사전에 정한 cases/README.md 기준을 코딩 보조 에이전트가 성공한 루트의 summary/facts/evidence와 필요한 원본 로그에 대조했다. 별도의 맹검 심사나 객관적인 성능 척도는 아니다. 실패한 루트는 완성 산출물 없음으로 미충족이다.
판정 수: `{'충족': 8, '부분 충족': 21, '미충족': 16}`. 자동 facts 검사와 별개다. [45회 정성 점검과 산출물 모음](QUALITY_REVIEW.md), [기계 판독 기록](qualitative_notes.jsonl).

|작업|충족|부분 충족|미충족|
|---|---:|---:|---:|
|release-review|8|0|1|
|game-design-architecture|0|9|0|
|payment-redesign|0|6|3|
|growth-roadmap|0|3|6|
|launch-operations|0|3|6|

## HTTP 429 실패의 별도 복구 실행

본 실험 종료 뒤 원래 429 실패 16개를 각각 한 번 더 실행했다. 복구 자동 통과는 15/16다. 원래 45회 성공률이나 실패 기록은 바꾸지 않았다.
별도 복구까지 포함해 통과 산출물을 확보한 원래 슬롯은 44/45다. 이는 최초 시도 성공률이 아니라 복구를 포함한 산출물 확보 수다.
복구 실행의 실제 깊이 분포는 {'1': 7, '0': 4, '2': 5}, 작업 중첩은 11회, 동일 Worker 중첩은 5회다. 최초 45회와 분리한 수치다.
[복구 규약](HTTP429_RECOVERY_PROTOCOL.md), [복구 실행 결과](recovery_429/REPORT.md), [복구 산출물 점검](recovery_429/QUALITY_REVIEW.md).
복구에서는 공헌이익 facts 키가 기술 계획의 개발 여유 시간으로 바뀌는 의미 충돌도 관측했다. [원본 추적](recovery_429/FACT_KEY_COLLISION.md)에 48000/240000원과 16/-16시간의 합성 과정을 기록했다. 해당 결과는 14/16이며 JSON Schema만으로 키의 단위를 보장하지 못했다.
복구 r1-growth-roadmap-overconfident에서는 운영 하위 결과의 고객 수 내림 누락을 통합 과정이 감지해 880000/3520000원으로 수정했다. [해당 산출물](recovery_429/documents/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-overconfident.md)에 성공한 교정 사례도 보존했다.
다른 제공업체의 구조화 출력 호환성 진단도 별도 진행했다. [진단 원문](../../../../diagnostics/NO_TOKEN_LIMIT_PROVIDER_DIAGNOSTICS.md)은 본 45회 및 복구 통계에 포함하지 않았다. 모든 본 실험과 복구는 Fireworks를 유지했다.

## 해석 범위

- strict response_format와 로컬 검증은 형식 오류를 감지하며 설계 내용의 정확성을 보장하지 않는다.
- 출력 토큰 상한을 API 요청에서 생략했으며 제공업체 자체 기본 한도와 기존 JSON Schema/전송/작업 자원 제약은 남아 있다.
- 수치 검사는 코드 계산, 설계는 규칙-타입 대응·RNG 복원·상태 전이·원자성처럼 검증 가능한 계약으로 나누는 것이 다음 개선 후보이다. 이 batch에는 사후 수정을 적용하지 않았다.
- 사례별 3회이고 작업별 분해 구조가 달라 조건의 일반적인 우월성이나 완전한 결정성을 주장할 수 없다. 과신 C의 배정 증가와 품질의 인과관계도 이 표만으로 단정하지 않는다.
- 이 45회는 peer DAG 확장 실험이다. 고정 manager의 기본 강의 과제 results.csv와 합산하지 않는다. 기본 제출 체크 및 Smith 비교/해석은 별도 범위다.
