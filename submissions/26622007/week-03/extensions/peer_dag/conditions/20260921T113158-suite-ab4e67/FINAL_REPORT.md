# 복합 작업 5개 최종 실험 결과

5개 작업 × 3개 조건 × 3회, 총 **45회**를 실행했다. 필수 facts 전부 통과는 **28/45**, 개별 facts는 **290/486**다.
이 수치는 숫자·명시 제약 검사이며 설계 문서 완성도나 실제 구현·테스트 성공률이 아니다.

## 고정 설정과 실행

실행 소스 `69d801ca10ef3a4107d7ac70e5ac4d6a3bc19a4b`. [사전 규약](../SUITE_FINAL_PROTOCOL.md), [manifest](manifest.json), [CSV](results.csv), [전체 지표](summary.json).
모델 deepseek/deepseek-v4.1-flash / OpenRouter / Fireworks, temperature=0, max_tokens=2200, reasoning disabled.
모든 단계 strict JSON Schema response_format. 공통 역할표 A/B/C 공유. 요청자는 상황별 역할이며 고정 관리자 없음. 장기 메모리 없음.
max_depth=5, max_tasks=12, max_steps=4, max_calls=64, max_parallel=3. 외부 실험은 직렬, 사이 15초 대기, 실행당 600초 상한.
429는 동일 payload로 최대 6회, 기타 오류 최대 2회. 실패한 전체 작업은 다시 실행하거나 결과를 교체하지 않았다.
release-review는 위임을 요구하며 새 4개 작업은 직접 실행/위임을 모델이 선택한다. 제공된 합성 자료만 분석하며 웹 검색·실제 코드 실행은 없다.

## 조건별 결과

|조건|자동 통과|facts|LLM 호출|실행 중첩 관측|동일 Worker 중첩|루트 C|보고 비용|
|---|---:|---:|---:|---:|---:|---:|---:|
|baseline|8/15|82/162|229|7|4|0/15|$0.204537|
|homogeneous|11/15|114/162|140|4|2|1/15|$0.113637|
|overconfident|9/15|94/162|263|9|6|13/15|$0.230792|

|작업|baseline|homogeneous|overconfident|
|---|---:|---:|---:|
|release-review|3/3|3/3|3/3|
|game-design-architecture|3/3|3/3|1/3|
|payment-redesign|2/3|1/3|1/3|
|growth-roadmap|0/3|1/3|1/3|
|launch-operations|0/3|3/3|3/3|

호출 632회, HTTP 638회. 반환된 비용 합계 $0.548967. 실행 간 대기를 포함한 첫 시작~마지막 종료 61.2분.
응답 없는 요청의 실제 청구액은 알 수 없다. 기간 전체 계정 비용이나 사람 작업 시간과 동일하지 않다.

## 실패·재시도·응답 검증

HTTP 오류: `{'429': 6}`. 429 영향을 받은 논리 호출 6개 중 6개에서 이후 HTTP 응답을 받았다.
응답 종료 사유: `{'stop': 615, 'length': 17}`. length 응답은 2200토큰 상한에서 잘린 것이며 형식 검증 실패로 보존했다.
request_checks는 실제 요청의 메시지/스키마/provider 일치 여부이고 response_checks는 실제 응답의 JSON/Schema 준수 여부다. 두 검사를 혼동하지 않는다.
실제 provider `{'Fireworks': 632}`, model `{'deepseek/deepseek-v4.1-flash': 632}`.

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
|game-design-architecture/overconfident/1|failed|0/10|game-design-architecture/tech_design/tech_review: JSONDecodeError: Unterminated string starting at: line 1 column 4486 (char 4485); game-design-architecture/tech_design: ValueError: child failed or blocked; partial results retained; game-design-architecture: ValueError: child failed or blocked; partial results retained|
|payment-redesign/homogeneous/1|failed|0/8|payment-redesign: JSONDecodeError: Unterminated string starting at: line 1 column 12 (char 11)|
|payment-redesign/overconfident/1|failed|0/8|payment-redesign/tech_design: JSONDecodeError: Unterminated string starting at: line 1 column 12 (char 11); payment-redesign: ValueError: child failed or blocked; partial results retained|
|growth-roadmap/baseline/1|failed|0/16|growth-roadmap/ops_readiness: JSONDecodeError: Unterminated string starting at: line 1 column 3354 (char 3353); growth-roadmap: ValueError: child failed or blocked; partial results retained|
|growth-roadmap/homogeneous/1|failed|0/16|growth-roadmap: JSONDecodeError: Unterminated string starting at: line 1 column 3623 (char 3622)|
|growth-roadmap/overconfident/1|failed|0/16|growth-roadmap/ops_readiness: JSONDecodeError: Unterminated string starting at: line 1 column 3383 (char 3382); growth-roadmap: ValueError: child failed or blocked; partial results retained|
|launch-operations/baseline/1|failed|0/8|launch-operations/policy_consistency: JSONDecodeError: Unterminated string starting at: line 1 column 3776 (char 3775); launch-operations: ValueError: child failed or blocked; partial results retained|
|game-design-architecture/overconfident/2|failed|0/10|game-design-architecture/design_spec: JSONDecodeError: Unterminated string starting at: line 1 column 4248 (char 4247); game-design-architecture: ValueError: child failed or blocked; partial results retained|
|growth-roadmap/homogeneous/2|failed|0/16|growth-roadmap: JSONDecodeError: Unterminated string starting at: line 1 column 3416 (char 3415)|
|growth-roadmap/baseline/2|failed|0/16|growth-roadmap/ops_readiness: JSONDecodeError: Unterminated string starting at: line 1 column 3499 (char 3498); growth-roadmap: ValueError: child failed or blocked; partial results retained|
|launch-operations/baseline/2|failed|0/8|launch-operations/integration: JSONDecodeError: Unterminated string starting at: line 1 column 4011 (char 4010); launch-operations: ValueError: child failed or blocked; partial results retained|
|payment-redesign/overconfident/3|failed|0/8|payment-redesign/tech_design: JSONDecodeError: Unterminated string starting at: line 1 column 4409 (char 4408); payment-redesign: ValueError: child failed or blocked; partial results retained|
|payment-redesign/baseline/3|failed|0/8|payment-redesign: JSONDecodeError: Unterminated string starting at: line 1 column 12 (char 11)|
|payment-redesign/homogeneous/3|failed|0/8|payment-redesign: JSONDecodeError: Unterminated string starting at: line 1 column 12 (char 11)|
|growth-roadmap/overconfident/3|failed|0/16|growth-roadmap/ops_readiness: JSONDecodeError: Unterminated string starting at: line 1 column 3482 (char 3481); growth-roadmap: ValueError: child failed or blocked; partial results retained|
|growth-roadmap/baseline/3|failed|0/16|growth-roadmap/integration: JSONDecodeError: Unterminated string starting at: line 1 column 4250 (char 4249); growth-roadmap: ValueError: child failed or blocked; partial results retained|
|launch-operations/baseline/3|failed|0/8|launch-operations/policy_consistency: JSONDecodeError: Unterminated string starting at: line 1 column 3743 (char 3742); launch-operations: ValueError: child failed or blocked; partial results retained|

## 재귀와 실제 병렬 실행

실제 최대 깊이 분포: `{'1': 20, '0': 20, '2': 5}`. 깊이 5는 허용 상한이며 실측 깊이와 다르다.
병렬성은 execute/synthesize의 call_start~call_end 중첩으로 센다. 동시에 제안만 한 것은 병렬 작업으로 세지 않는다. HTTP 이벤트는 버퍼 기록이므로 그 시각으로 중첩을 계산하지 않는다.
중첩이 있어도 작업이 성공했다는 뜻은 아니다. 표의 both succeeded는 해당 두 하위 작업만의 상태이며 최종 루트 성공과 별개다.

|실행|동시 작업|Worker|초|두 작업 성공|
|---|---|---|---:|---|
|release-review/baseline/1|release-review/tech_risk + release-review/profitability|A + B|6.393|True|
|release-review/homogeneous/1|release-review/tech_risk_review + release-review/profitability_review|C + B|4.696|True|
|payment-redesign/overconfident/1|payment-redesign/biz_analysis + payment-redesign/tech_design|C + C|9.742|False|
|growth-roadmap/baseline/1|growth-roadmap/financial_analysis + growth-roadmap/ops_readiness|A + C|14.029|False|
|growth-roadmap/baseline/1|growth-roadmap/financial_analysis + growth-roadmap/tech_design|A + A|7.477|True|
|growth-roadmap/baseline/1|growth-roadmap/ops_readiness + growth-roadmap/tech_design|C + A|17.788|False|
|growth-roadmap/overconfident/1|growth-roadmap/dev_scope_analysis + growth-roadmap/profit_analysis|C + C|13.537|True|
|growth-roadmap/overconfident/1|growth-roadmap/dev_scope_analysis + growth-roadmap/ops_readiness|C + C|14.469|False|
|growth-roadmap/overconfident/1|growth-roadmap/profit_analysis + growth-roadmap/ops_readiness|C + C|17.251|False|
|launch-operations/baseline/1|launch-operations/capacity_calc + launch-operations/policy_consistency|B + C|9.116|False|
|game-design-architecture/overconfident/2|game-design-architecture/design_spec/budget_schedule + game-design-architecture/design_spec/tech_design|C + A|3.788|True|
|payment-redesign/overconfident/2|payment-redesign/tech_design/migration_ops_plan + payment-redesign/tech_design/tech_design_draft|C + A|3.444|True|
|growth-roadmap/overconfident/2|growth-roadmap/biz_analysis + growth-roadmap/tech_design|C + B|9.683|True|
|growth-roadmap/baseline/2|growth-roadmap/tech_design/biz_numbers + growth-roadmap/business_review|B + B|0.060|True|
|growth-roadmap/baseline/2|growth-roadmap/business_review + growth-roadmap/tech_design/tech_design|B + A|11.539|True|
|launch-operations/homogeneous/2|launch-operations/capacity_calc + launch-operations/customer_comms|C + A|8.497|True|
|launch-operations/homogeneous/2|launch-operations/capacity_calc + launch-operations/policy_schedule|C + C|8.649|True|
|launch-operations/homogeneous/2|launch-operations/customer_comms + launch-operations/policy_schedule|A + C|18.660|True|
|launch-operations/baseline/2|launch-operations/capacity_and_policy + launch-operations/customer_comms|C + C|7.395|True|
|release-review/homogeneous/2|release-review/tech_risk_review + release-review/profitability_review|C + B|6.323|True|
|release-review/overconfident/2|release-review/profitability + release-review/tech_risk|C + C|5.108|True|
|release-review/baseline/2|release-review/tech_risk + release-review/profitability|A + B|7.476|True|
|payment-redesign/overconfident/3|payment-redesign/biz_migration + payment-redesign/ops_comm|C + C|0.792|True|
|payment-redesign/overconfident/3|payment-redesign/ops_comm + payment-redesign/tech_design|C + A|16.383|False|
|growth-roadmap/overconfident/3|growth-roadmap/financial_analysis + growth-roadmap/tech_scope_schedule|C + C|10.728|True|
|growth-roadmap/overconfident/3|growth-roadmap/financial_analysis + growth-roadmap/ops_readiness|C + C|12.396|False|
|growth-roadmap/overconfident/3|growth-roadmap/tech_scope_schedule + growth-roadmap/ops_readiness|C + C|18.403|False|
|growth-roadmap/baseline/3|growth-roadmap/analysis + growth-roadmap/tech_design|A + A|8.180|True|
|growth-roadmap/baseline/3|growth-roadmap/analysis + growth-roadmap/ops_readiness|A + C|10.231|True|
|growth-roadmap/baseline/3|growth-roadmap/tech_design + growth-roadmap/ops_readiness|A + C|17.176|True|
|release-review/overconfident/3|release-review/profitability_review + release-review/tech_risk_review|C + C|5.916|True|
|release-review/homogeneous/3|release-review/profitability_review + release-review/technical_risk_review|B + B|5.723|True|

## 결정성 및 배정 쏠림

|작업/조건|facts 있는 실행|서로 다른 facts 벡터|서로 다른 전체 산출물|서로 다른 루트 계획|
|---|---:|---:|---:|---:|
|release-review/baseline|3/3|1|3|3|
|release-review/homogeneous|3/3|1|3|3|
|release-review/overconfident|3/3|1|3|3|
|game-design-architecture/baseline|3/3|1|3|3|
|game-design-architecture/homogeneous|3/3|1|3|3|
|game-design-architecture/overconfident|1/3|1|1|3|
|payment-redesign/baseline|2/3|1|2|3|
|payment-redesign/homogeneous|1/3|1|1|3|
|payment-redesign/overconfident|1/3|1|1|3|
|growth-roadmap/baseline|0/3|0|0|3|
|growth-roadmap/homogeneous|1/3|1|1|3|
|growth-roadmap/overconfident|1/3|1|1|3|
|launch-operations/baseline|0/3|0|0|3|
|launch-operations/homogeneous|3/3|1|3|3|
|launch-operations/overconfident|3/3|1|3|3|

facts 일치에 실패 실행은 포함되지 않으므로 항상 전체 통과율과 함께 본다. 전체 산출물/계획 hash는 표현과 작업 ID 차이도 반영하며 의미상 차이 크기를 재는 지표는 아니다.
점수는 coverage/feasibility/verification 각 0~2로 평가하고 동점일 때 자기 확신도를 사용한다. 확신도를 가린 점수 평가만으로 과신의 영향을 제거하지 못할 수 있다.

|조건|전체 C 배정|C의 95 이상 유효 입찰|최고 평가 점수가 동점인 배정|
|---|---:|---:|---:|
|baseline|8/47|4/47|39|
|homogeneous|5/28|3/28|27|
|overconfident|45/54|54/54|50|

예외 `20260921T113158-suite-ab4e67-r3-growth-roadmap-overconfident`: 평가 합계 {'A': 6, 'B': 6, 'C': 4}, C 확신도 97.0, 선정 A의 확신도 88.0. 점수가 낮은 과신 후보는 선정하지 않았다.

예외 `20260921T113158-suite-ab4e67-r3-game-design-architecture-overconfident`: 평가 합계 {'A': 6, 'B': 6, 'C': 3}, C 확신도 97.0, 선정 A의 확신도 88.0. 점수가 낮은 과신 후보는 선정하지 않았다.

[보조 재생 검증](replay_verification.json): 10회, 전체 일치=True, 추가 API 요청 0.
각 사례의 첫 baseline을 성공 여부와 무관하게 선택했다. 기록된 응답 고정 후 max_parallel=1/3, 인위적 100ms 지연으로 재생했다. 성공·실패 상태, 평가, 모든 하위 산출물, 호출 수를 비교했다.
이는 동일 모델 응답이 주어진 실행기의 재현성을 확인하는 사후 구현 검사다. 실제 LLM 응답이 다시 같을 것이라는 보장은 아니다.

## 산출물 품질 점검

사전에 정한 cases/README.md 기준을 코딩 보조 에이전트가 성공한 루트의 summary/facts/evidence와 필요한 원본 로그에 대조했다. 별도의 맹검 심사나 객관적인 성능 척도는 아니다. 실패한 루트는 완성 산출물 없음으로 미충족이다.
판정 수: `{'충족': 9, '부분 충족': 19, '미충족': 17}`. 자동 facts 검사와 별개다. [45회 정성 점검과 산출물 모음](QUALITY_REVIEW.md), [기계 판독 기록](qualitative_notes.jsonl).

|작업|충족|부분 충족|미충족|
|---|---:|---:|---:|
|release-review|9|0|0|
|game-design-architecture|0|7|2|
|payment-redesign|0|4|5|
|growth-roadmap|0|2|7|
|launch-operations|0|6|3|

## 해석 범위와 다음 설계에 필요한 것

- strict response_format와 로컬 검증은 형식 오류를 감지하지만, 토큰 상한에서 잘린 JSON을 완성하거나 설계 내용을 증명하지 않는다.
- 독립 작업의 실행과 재귀 위임은 구현됐어도, 분해한 일의 범위와 통합 문서 길이는 별도로 제어해야 한다. 하위 작업이 원래 큰 요청을 반복하는지 점검할 필요가 있다.
- 수치 검사는 코드 계산, 설계는 규칙-타입 대응·RNG 복원·상태 전이·원자성처럼 검증 가능한 계약으로 나누는 것이 다음 개선 후보이다. 이 batch에는 사후 수정을 적용하지 않았다.
- 사례별 3회이고 작업별 분해 구조가 달라 조건의 일반적인 우월성이나 완전한 결정성을 주장할 수 없다. 과신 C의 배정 증가와 품질의 인과관계도 이 표만으로 단정하지 않는다.
- 이 45회는 peer DAG 확장 실험이다. 고정 manager의 기본 강의 과제 results.csv와 합산하지 않는다. 기본 제출 체크 및 Smith 비교/해석은 별도 범위다.
