# launch-operations / baseline / 3회

상태: failed. 필수 facts: 0/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## launch-operations.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-launch-operations-baseline/artifacts/launch-operations.json)

ValueError: child failed or blocked; partial results retained

## launch-operations/capacity_calc.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-launch-operations-baseline/artifacts/launch-operations/capacity_calc.json)

첫 4시간 용량·인력 계산과 충원 미승인 전제의 대기열·우선순위·고객 안내 대안을 제시한다. 초대 800명 × 20% = 160건이 첫 4시간 예상 문의다. 상담원 4명 × 시간당 5건 × 4시간 = 80건이 4시간 처리 용량이며, 160 - 80 = 80건이 충원 없을 때 미처리(backlog)다. 전량 처리를 위해서는 160 ÷ (5건/시간 × 4시간) = 8명이 필요하고, 8 - 4 = 4명 추가 충원이 필요하다. 지원 창은 4시간, 출시 시각은 10시다. 충원이 승인되지 않은 경우 대기열·우선순위 대안: (1) 우선순위 1순위는 결제/환불 오해 및 출시 차단 이슈(서비스 이용 불가, 결제 오류, 중복 결제), 2순위는 계정·접근 문제, 3순위는 일반 문의·기능 질문으로 분류한다. (2) 4시간 내 80건만 처리되므로 나머지 80건은 대기열로 적체되며, 접수 순서가 아니라 우선순위 기반 큐로 운영한다. (3) 고객 안내 대안: 확정 정책인 '상담 접수 후 영업일 2일 안에 처리 시작'을 그대로 고지하고, 처리 완료 시간과 PG 반영 시간은 보장하지 않음을 명시한다. '즉시 자동 환불'은 확정 정책과 다르므로 광고·상담·랜딩 어디에도 사용하지 않는다(immediate_refund_promised=false). (4) 자동환불이 아니라는 점, 환불은 접수 후 영업일 2일 내 처리 시작되며 PG 반영은 별도 시간이 걸린다는 점을 FAQ에 명시한다. 미확인 사항: 인원 충원 미승인, 기술 배포 승인 미제공, 결제 검증 결과 미제공. 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않았다.

### Facts

```json
{
  "expected_contacts": 160,
  "four_hour_capacity": 80,
  "backlog_without_hiring": 80,
  "required_total_agents": 8,
  "additional_agents": 4,
  "support_window_hours": 4,
  "launch_hour": 10,
  "immediate_refund_promised": false,
  "hiring_approved": false,
  "deployment_approved": false,
  "payment_verification_received": false,
  "priority_1": "결제/환불 오해·출시 차단 이슈",
  "priority_2": "계정·접근 문제",
  "priority_3": "일반 문의·기능 질문",
  "customer_notice_policy": "상담 접수 후 영업일 2일 안에 처리 시작, 완료·PG 반영 시간 미보장",
  "auto_refund_used_in_ads": false
}
```

### Evidence

**1.** expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)

**2.** four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건

**3.** backlog_without_hiring = 160 - 80 = 80건

**4.** required_total_agents = 160 ÷ (5건/시간 × 4시간) = 160 ÷ 20 = 8명

**5.** additional_agents = 8 - 4 = 4명

**6.** support_window_hours = 4, launch_hour = 10 (금요일 10시 출시, 첫 4시간 창)

**7.** 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장 → immediate_refund_promised=false

**8.** 광고 초안의 '즉시 자동 환불'은 확정 정책과 상충하므로 사용 금지

**9.** 미확인: 인원 충원 미승인, 기술 배포 승인 미제공, 결제 검증 결과 미제공 → hiring_approved=false, deployment_approved=false, payment_verification_received=false

**10.** 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음(미검증)

## launch-operations/contingency_checklist.json

Worker: ; 상태: blocked
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-launch-operations-baseline/artifacts/launch-operations/contingency_checklist.json)

predecessor did not succeed

## launch-operations/customer_comms.json

Worker: ; 상태: blocked
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-launch-operations-baseline/artifacts/launch-operations/customer_comms.json)

predecessor did not succeed

## launch-operations/policy_consistency/customer_comms.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-launch-operations-baseline/artifacts/launch-operations/policy_consistency/customer_comms.json)

확정 정책('상담 접수 후 영업일 2일 안에 처리 시작', 완료/PG 반영 미보장)에 부합하는 고객 안내문·FAQ·상담 분류 기준 초안을 작성했다. '즉시 자동 환불' 표현은 전면 배제하고, 선행 policy_consistency_doc의 5개 모순 유형(처리 시작 vs 즉시 완료, 완료 보장 vs 미보장, PG 반영 보장 vs 미보장, 자동 vs 수동, 영업일 vs 실시간)에 각각 대응 문구를 배치했다. 실제 고객 연락·외부 시스템 변경·환불 실행은 수행하지 않았다.

[1] 고객 안내문 초안 (광고·랜딩·상담 공통, SSOT v1 기준)
"환불 요청은 상담 접수 후 영업일 기준 2일 안에 처리를 시작합니다. 처리는 상담 접수 내용을 확인한 뒤 진행되며, 자동으로 즉시 처리되지 않습니다. 처리 완료 시점과 카드사(PG) 반영 시점은 개별 결제 상황에 따라 달라질 수 있어 정확한 시간을 보장해 드리지 못합니다. 진행 상황은 접수하신 상담 채널을 통해 확인하실 수 있습니다."
- 금지 표현: '즉시', '자동', '바로 환불', '완료 보장', '실시간 반영'.
- 필수 포함: 처리 시작 기준(영업일 2일), 수동 처리, 완료/PG 반영 미보장.

[2] FAQ 초안 (모순 유형별 대응)
Q1. 즉시 환불되나요? → 아니요. 접수 후 영업일 2일 안에 처리를 시작합니다. '즉시'는 당사 정책이 아닙니다. (모순 유형: 처리 시작 vs 즉시 완료)
Q2. 언제 완료되나요? → 완료 시점은 보장하지 않습니다. 결제 수단·카드사 상황에 따라 달라집니다. (완료 보장 vs 미보장)
Q3. 자동으로 처리되나요? → 아니요. 상담 접수 내용을 확인한 뒤 담당자가 처리합니다. (자동 vs 수동)
Q4. 카드사(PG)에는 언제 반영되나요? → PG 반영 시점은 보장하지 않습니다. 처리 완료 후 카드사 정책에 따라 반영됩니다. (PG 반영 보장 vs 미보장)
Q5. 영업일이 무슨 뜻인가요? → 주말·공휴일을 제외한 평일 기준입니다. 접수 시각이 아닌 영업일 기준으로 2일입니다. (영업일 vs 실시간)
Q6. 진행 상황은 어떻게 확인하나요? → 접수한 상담 채널에서 확인 가능하며, 추가 문의는 동일 채널로 접수해 주세요.

[3] 상담 분류 기준 초안
- 유형 A: 환불 진행 문의(접수 확인) — 우선순위 중, 표준 안내문 회신, 영업일 2일 기준 재안내.
- 유형 B: 완료 지연 문의(영업일 2일 경과) — 우선순위 상, 담당자 확인 후 처리 상태 회신, 완료 시점 보장 표현 금지.
- 유형 C: PG 반영 지연 문의 — 우선순위 중, PG 반영 미보장 안내, 카드사 확인 절차 안내.
- 유형 D: 불만·에스컬레이션(보장 오인, 광고 문구 불일치 제기) — 우선순위 최상, 운영 리드 에스컬레이션, SSOT 문구 기준으로 정정 안내.
- 유형 E: 정책 외 요청(즉시/자동 환불 요구) — 우선순위 상, 정책 원문 인용으로 안내, 예외 약속 금지.
- 에스컬레이션 경로: 상담원 → 운영 리드 → (정책 사실 변경 필요 시) 제품·법무 승인 후 SSOT 개정.

[4] 자체 점검
- '즉시 자동 환불' 표현이 안내문·FAQ·분류 기준 어디에도 남아 있지 않음(금지 표현 목록으로 배제).
- 확정 정책 문구를 그대로 인용했으며 새 정책을 결정하지 않음.
- 실제 고객 연락·외부 시스템 변경·환불 실행·배포를 수행하지 않음(미검증).
- 본 산출물은 문서 초안이며 코드 구현/테스트는 수행하지 않음(미검증).

### Facts

```json
{
  "immediate_refund_promised": false,
  "actual_customer_contact_performed": false,
  "actual_external_system_change_performed": false,
  "actual_refund_performed": false,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_time_guaranteed": false,
  "automatic_processing_claimed": false,
  "contradiction_types_addressed": 5,
  "faq_item_count": 6,
  "inquiry_category_count": 5,
  "ssot_version_referenced": "v1",
  "code_implemented": false,
  "test_performed": false
}
```

### Evidence

**1.** 정책 원문 정합성: 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작'을 안내문·FAQ Q1·Q5에 그대로 반영, '처리 시작'과 '완료'를 구분해 즉시 완료 함의를 제거.

**2.** 모순 유형 대응 근거: 선행 policy_consistency_doc의 5개 모순 유형(처리 시작 vs 즉시 완료, 완료 보장 vs 미보장, PG 반영 보장 vs 미보장, 자동 vs 수동, 영업일 vs 실시간)을 FAQ Q1~Q5에 1:1 대응 배치.

**3.** 금지 표현 배제 근거: 안내문에 금지 표현 목록('즉시','자동','바로 환불','완료 보장','실시간 반영')을 명시하고 전 채널 공통 적용을 선언.

**4.** 미보장 명시 근거: '처리 완료 시점과 카드사(PG) 반영 시점은 ... 보장해 드리지 못합니다' 문장으로 완료/PG 반영 미보장을 명문화.

**5.** 상담 분류 근거: 유형 A~E를 문의 성격(진행/지연/반영/불만/정책 외)으로 구분하고, 정책 외 요청(유형 E)에 예외 약속 금지를 명시해 광고-정책 불일치 재발을 차단.

**6.** 에스컬레이션 근거: 정책 사실 변경이 필요한 경우 제품·법무 승인 후 SSOT 개정 경로를 명시(선행 문서의 승인자 규칙과 일치).

**7.** 자체 점검 근거: 실제 고객 연락·외부 시스템 변경·환불·배포·코드 구현·테스트를 수행하지 않았음을 summary와 facts(actual_* = false, code_implemented=false, test_performed=false)에 명시.

## launch-operations/policy_consistency/policy_consistency_doc.json

Worker: B; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-launch-operations-baseline/artifacts/launch-operations/policy_consistency/policy_consistency_doc.json)

단일 기준 문서(Single Source of Truth, SSOT) 구조와 변경 전달·승인 흐름을 설계하고, 확정 정책과 광고 초안의 모순을 대조했다. 실제 승인·배포·환불은 수행하지 않았으며 모두 미확인/미완료로 표기한다.

[1] 모순 유형 대조표 (확정 정책 vs 광고 초안 '즉시 자동 환불')
| 구분 | 확정 정책 | 광고 초안 | 모순 유형 | 판정 |
|---|---|---|---|---|
| 시점 | 접수 후 영업일 2일 내 '처리 시작' | '즉시' | 처리 시작 vs 즉시 완료 | 모순 |
| 완료 보장 | 완료 시간 미보장 | 즉시 완료 함의 | 보장 vs 미보장 | 모순 |
| PG 반영 | PG 반영 시간 미보장 | 즉시 반영 함의 | 보장 vs 미보장 | 모순 |
| 실행 주체 | 상담 접수 후 처리(수동 개입) | '자동' | 자동 vs 수동 | 모순 |
| 범위 | 영업일 기준 | 시간 기준(즉시) | 영업일 vs 실시간 | 모순 |

[2] 단일 기준 문서 구조 (섹션·승인자·버전)
- 섹션: (S1) 정책 요약 — '영업일 2일 내 처리 시작, 완료/PG 반영 미보장', (S2) 고객 안내 문구(광고·랜딩·상담 공통), (S3) FAQ, (S4) 상담 분류 기준(문의 유형·우선순위·에스컬레이션), (S5) 변경 이력(버전·일자·변경자·승인자·사유).
- 승인자(역할 기준): 제품(정책 사실), 법무(보장 표현·약관), 운영(상담 절차·분류), 마케팅(광고·랜딩 문구).
- 버전 규칙: vMAJOR.MINOR, MAJOR=정책 사실 변경(제품+법무 필수), MINOR=문구·절차 변경(해당 승인자). 모든 채널은 동일 버전 번호를 인용해야 하며, 버전 불일치 시 배포 동결.

[3] 변경 전달·승인 흐름
초안 작성(담당) → 검토(제품·법무·운영·마케팅) → 승인(필수 승인자 전원) → 배포(채널별) → 이력 기록. 미승인 상태에서는 배포 동결(freeze). 긴급 변경도 동일 흐름을 따르며 구두 승인 불가.

[4] 승인 타임라인 (목요일, 마감 역산)
| 시각 | 담당 | 작업 | 선행조건 |
|---|---|---|---|
| 목 14:00 | 제품팀 | 확정 사양·알려진 제한 전달 | 없음(입력) |
| 목 14:00~15:00 | 제품·법무·운영·마케팅 | SSOT v1 검토·승인 | 제품팀 전달 |
| 목 15:00 | 마케팅 | 광고 문구 마감(승인된 SSOT v1 기준) | SSOT 승인 |
| 목 17:00 | 운영 | 상담 교육 마감 | SSOT 승인 |
| 목 18:00 | 마케팅/웹 | 랜딩 수정 마감 | SSOT 승인 |
| 금 10:00 | 전 채널 | 출시 | 위 전부 완료 |

[5] 확정/미확인 사항표
| 항목 | 상태 | 비고 |
|---|---|---|
| 정책: 영업일 2일 내 처리 시작 | 확정 | 완료/PG 반영 미보장 |
| 광고 초안 '즉시 자동 환불' | 모순(수정 필요) | SSOT v1 문구로 대체 필요 |
| 기술 배포 승인 | 미확인 | 미제공 |
| 결제(PG) 검증 결과 | 미확인 | 미제공 |
| 인원 충원 승인 | 미확인 | 미승인 상태 |
| 광고/교육/랜딩 마감 시각 | 확정 | 목 15/17/18시 |

[6] 자체 점검
- 실제 승인·배포·환불 완료를 주장하지 않음: 위 표에서 모두 미확인/미완료로 표기.
- 제품 정책을 새로 결정하지 않음: 확정 정책 문구를 그대로 인용.
- 실제 고객 연락·외부 시스템 변경 없음: 설계 문서만 제시.
- 본 작업은 문서·표·흐름 설계이며 코드 구현/테스트는 수행하지 않음(미검증).

### Facts

```json
{
  "immediate_refund_promised": false,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_time_guaranteed": false,
  "ad_copy_deadline_hour": 15,
  "training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "launch_hour": 10,
  "tech_deploy_approval_status": "미확인",
  "payment_verification_status": "미확인",
  "hiring_approval_status": "미확인",
  "ssot_version_rule": "vMAJOR.MINOR",
  "ssot_required_approvers": "제품,법무,운영,마케팅",
  "unapproved_deploy_frozen": true,
  "contradiction_count": 5,
  "actual_approval_performed": false,
  "actual_deploy_performed": false,
  "actual_refund_performed": false,
  "code_implemented": false,
  "test_performed": false
}
```

### Evidence

**1.** 모순 대조 근거: 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작' vs 광고 초안 '즉시 자동 환불' → (a) 처리 시작 vs 즉시 완료, (b) 완료 미보장 vs 즉시 완료 함의, (c) PG 반영 미보장 vs 즉시 반영 함의, (d) 수동 처리 vs 자동, (e) 영업일 기준 vs 실시간. 총 5개 모순 유형.

**2.** 타임라인 근거: 제품팀 목14시 전달 → 광고 목15시 → 교육 목17시 → 랜딩 목18시 → 금10시 출시. 각 마감은 SSOT 승인을 선행조건으로 함.

**3.** 버전 규칙 근거: MAJOR=정책 사실 변경(제품+법무 필수), MINOR=문구·절차 변경(해당 승인자). 채널 간 버전 불일치 시 배포 동결.

**4.** 미확인 사항 근거: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인은 source에서 '미제공/미승인'으로 명시됨.

**5.** 자체 점검: 실제 승인·배포·환불·고객 연락·외부 시스템 변경은 수행하지 않았으며, 코드 구현/테스트도 수행하지 않음(미검증).

## launch-operations/policy_consistency.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-launch-operations-baseline/artifacts/launch-operations/policy_consistency.json)

JSONDecodeError: Unterminated string starting at: line 1 column 3743 (char 3742)
