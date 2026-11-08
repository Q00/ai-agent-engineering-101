# launch-operations / baseline / 2회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## launch-operations.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-launch-operations-baseline/artifacts/launch-operations.json)

금요일 10시 출시용 통합 운영 패키지(launch_operations_report)를 두 선행 산출물(capacity_and_policy, customer_comms) 통합으로 완성했다. 두 산출물의 7개 숫자 키와 immediate_refund_promised 값이 상호 일치함을 대조 확인했다.

[1] 용량 계산(제공 가정만 사용)
expected_contacts = 800 × 0.20 = 160건, four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건, backlog_without_hiring = 160 − 80 = 80건, required_total_agents = 160 ÷ (5 × 4) = 8명, additional_agents = 8 − 4 = 4명. support_window_hours = 4, launch_hour = 10. 충원은 미승인(hiring_approved=false)이므로 추가 4명은 '필요 인원'일 뿐 확보 인력이 아니다.

[2] 확정/미확인 사항표
- 확정: 접수 후 영업일 2일 내 처리 시작(policy_processing_start_business_days=2). 처리 완료 시점·PG 반영 시점 미보장(processing_completion_guaranteed=false, pg_reflection_guaranteed=false). 마감: 제품팀 사양 목14시, 광고 목15시, 상담 교육 목17시, 랜딩 목18시. 초대 800명, 첫 4시간 문의율 20%, 상담원 4명, 시간당 5건, 지원 창 4시간, 출시 10시.
- 미확인: 기술 배포 승인(tech_deploy_approval_provided=false), 결제 검증(payment_verification_provided=false), 인원 충원 승인(hiring_approved=false). → 완료로 표기 금지, '대기/미승인'으로 표기.
- 모순: 광고 초안 '즉시 자동 환불' ≠ 확정 정책 '영업일 2일 내 처리 시작' → immediate_refund_promised=false, 광고 문구 수정 필수.

[3] 단일 기준 문서(SSOT) 원칙과 변경 전달·승인 흐름
'고객 커뮤니케이션 기준서' 1개만 원본으로 두고 광고·상담·랜딩은 인용만 하며 독자 문구 신설 금지. 흐름: 제품팀 목14시 사양·제한 전달 → 기준서 v1 반영(운영·커뮤니케이션) → 광고 목15시 → 상담 교육 목17시 → 랜딩 목18시. 각 단계 선행조건은 직전 단계 승인.

[4] 운영 일정표(담당·기한·선행조건)
- 목14:00 제품팀 확정 사양·알려진 제한 전달 / 선행 없음 / 담당 제품팀
- 목14:00~15:00 기준서 v1 반영 / 선행 제품팀 전달 / 담당 운영·커뮤니케이션
- 목15:00 광고 문구 최종('즉시 자동 환불' 삭제) / 선행 기준서 v1 / 담당 광고+운영
- 목17:00 상담 교육(분류·FAQ·금지 표현) / 선행 기준서 v1 / 담당 운영·커뮤니케이션
- 목18:00 랜딩 수정 / 선행 기준서 v1 / 담당 랜딩+운영
- 금10:00 출시 / 선행 기술 배포 승인(미확인), 결제 검증(미확인), 위 3개 마감 완료

[5] 고객 안내문(확정 문구)
"문의를 접수해 주셔서 감사합니다. 접수하신 내용은 영업일 기준 2일 안에 처리 시작됩니다. 처리 완료 시점과 결제(PG) 반영 시점은 보장되지 않으며 순차 처리됩니다. 결제·환불 오류는 최우선으로 확인합니다."

[6] FAQ
- 즉시 환불되나요? 아니요. 접수 후 영업일 2일 내 처리 시작이며 완료·PG 반영은 미보장입니다.
- 4시간 내 모두 답변되나요? 문의량에 따라 지연될 수 있고 우선순위에 따라 순차 처리됩니다.
- 결제 오류는? 최우선 순위로 분류됩니다.

[7] 상담 분류 기준(우선순위)
P1 결제/환불 오류 > P2 서비스 이용 불가 > P3 일반 문의. P1은 즉시 태깅·에스컬레이션.

[8] 인력 대안(충원 미승인 전제)
대기열·우선순위 운영, 자동 응답·FAQ 셀프서비스 유도, 콜백 예약, 4시간 내 미처리 약 80건 지연 가능성 사전 고지.

[9] 비상 계획 3종
- 광고 지연: 트리거 목15시 미완 → 기준서 문구로 임시 대체, 담당 운영, 고객 안내 동일.
- 기술 배포 승인 지연: 트리거 금10시 미승인 → 출시 보류 또는 제한 오픈, 담당 제품·기술, '일정 변동' 공지.
- 문의 급증: 트리거 4시간 문의 >160 → P1 우선, 콜백 확대, 추가 인력 승인 요청(미승인 상태 명시).

[10] 출시 직전 확인표
정책 문구 일치('즉시 자동 환불' 없음) □ / 승인 상태(배포·결제·충원) □ / 인력 4명 □ / 랜딩·광고·교육 완료 □ / 롤백·에스컬레이션 경로 □

[11] 실제 수행/설계 제안 구분
본 산출물은 계산·분석·설계 문서이며 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않았다(actual_implementation_performed=false, actual_testing_performed=false, customer_contact_performed=false, external_system_change_performed=false). 승인되지 않은 충원·환불·배포는 완료로 기술하지 않았다.

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
  "invited_users": 800,
  "contact_rate_first_4h": 0.2,
  "agents_current": 4,
  "contacts_per_agent_per_hour": 5,
  "policy_processing_start_business_days": 2,
  "processing_completion_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "hiring_approved": false,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "product_spec_delivery_thu_hour": 14,
  "ad_copy_deadline_thu_hour": 15,
  "agent_training_thu_hour": 17,
  "landing_fix_deadline_thu_hour": 18,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "customer_contact_performed": false,
  "external_system_change_performed": false
}
```

### Evidence

**1.** expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)

**2.** four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건

**3.** backlog_without_hiring = 160 − 80 = 80건

**4.** required_total_agents = 160 ÷ (5 × 4) = 8명

**5.** additional_agents = 8 − 4 = 4명

**6.** support_window_hours = 4, launch_hour = 10 (제공 가정)

**7.** 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 미보장 (제공 자료)

**8.** 광고 초안 '즉시 자동 환불'은 확정 정책과 상충 → immediate_refund_promised=false

**9.** 마감: 제품팀 사양 목14시, 광고 목15시, 상담 교육 목17시, 랜딩 목18시 (제공 자료)

**10.** 미확인: 기술 배포 승인, 결제 검증, 인원 충원 승인 (제공 자료에 결과 미제공)

**11.** 두 선행 산출물(capacity_and_policy, customer_comms)의 7개 숫자 키와 immediate_refund_promised 값이 상호 일치함을 대조 확인

**12.** 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음(미검증)

## launch-operations/capacity_and_policy.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-launch-operations-baseline/artifacts/launch-operations/capacity_and_policy.json)

용량·인력 계산과 정책 모순·미확인 사항 분석을 수행했다. 초대 800명 중 첫 4시간 문의율 20% 가정으로 예상 문의 160건, 상담원 4명의 4시간 처리 용량 80건, 미처리 80건이 발생한다. 전량 처리를 위해서는 총 8명(시간당 5건×4시간 기준)이 필요해 추가 4명 충원이 요구되나 충원은 미승인 상태다. 정책 측면에서 광고 초안의 '즉시 자동 환불'은 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작'과 모순되며, 처리 완료 시점과 PG 반영 시점은 보장되지 않는다. 기술 배포 승인과 결제 검증 결과는 미제공으로 출시 전 확인이 필요하다. 충원 불가 시 정책 변경 없이 대기열 우선순위(결제 오류·중복 결제 등 금전 피해 우선), 안내 문구 표준화(처리 시작 기준·완료/PG 미보장 명시), 셀프서비스/FAQ 유도, 처리 시작 시점 기준 안내로 대응한다. 본 작업은 계산·분석이며 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않았다.

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
  "processing_completion_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "policy_processing_start_business_days": 2,
  "ad_copy_deadline_thu_hour": 15,
  "agent_training_thu_hour": 17,
  "landing_fix_deadline_thu_hour": 18,
  "product_spec_delivery_thu_hour": 14,
  "invited_users": 800,
  "contact_rate_first_4h": 0.2,
  "agents_current": 4,
  "contacts_per_agent_per_hour": 5,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "customer_contact_performed": false,
  "external_system_change_performed": false
}
```

### Evidence

**1.** expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)

**2.** four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건

**3.** backlog_without_hiring = 160 - 80 = 80건

**4.** required_total_agents = 160 / (5 × 4) = 8명

**5.** additional_agents = 8 - 4 = 4명

**6.** support_window_hours = 4, launch_hour = 10 (금요일 10시 출시, 첫 4시간 창)

**7.** 정책 모순: 광고 초안 '즉시 자동 환불' vs 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작' → immediate_refund_promised=false

**8.** 미보장 사항: 처리 완료 시간과 PG 반영 시간은 확정 정책상 보장되지 않음(processing_completion_guaranteed=false, pg_reflection_guaranteed=false)

**9.** 미확인 사항: 기술 배포 승인 결과 미제공(tech_deploy_approval_provided=false), 결제 검증 결과 미제공(payment_verification_provided=false)

**10.** 일정 입력: 광고 문구 마감 목요일 15시, 상담 교육 목요일 17시, 랜딩 수정 마감 목요일 18시, 제품팀 확정 사양·제한 전달 목요일 14시

**11.** 충원 미승인(hiring_approved=false) 시 대안: (1) 대기열 우선순위 — 결제 오류·중복 결제 등 금전 피해 및 출시 직후 다발 이슈 우선, (2) 안내 문구 표준화 — '영업일 2일 내 처리 시작' 기준으로 통일하고 완료·PG 반영 미보장 명시, (3) 셀프서비스/FAQ 유도로 단순 문의 흡수, (4) 처리 시작 시점 기준 안내로 '즉시 환불' 오해 차단

**12.** 본 작업은 계산·분석 산출물이며 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음(actual_implementation_performed=false, actual_testing_performed=false, customer_contact_performed=false, external_system_change_performed=false)

## launch-operations/customer_comms.json

Worker: B; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-launch-operations-baseline/artifacts/launch-operations/customer_comms.json)

고객 커뮤니케이션·운영 일정 패키지(Worker B, 사업·분석 관점). 확정 사실과 미확인 사항을 분리하고, 단일 기준 문서 원칙 아래 안내문·FAQ·상담 분류 기준, 담당·기한·선행조건이 연결된 일정표, 변경 전달·승인 흐름, 비상 계획 3종, 출시 직전 확인표를 텍스트로 작성했다. 용량 계산은 제공 가정만 사용했다: expected_contacts=800×0.20=160, four_hour_capacity=4×5×4=80, backlog_without_hiring=160-80=80, required_total_agents=160/(5×4)=8, additional_agents=8-4=4. 인원 충원은 미승인이므로 '충원 불가 시 대안'으로만 제시했고, '즉시 자동 환불'은 확정 정책과 다르므로 모든 산출물에서 배제했다. 실제 고객 연락·외부 시스템 변경·배포·결제 검증은 수행하지 않았다(미검증).

[1] 확정/미확인 사항표
- 확정: 접수 후 영업일 2일 내 처리 시작. 처리 완료 시점·PG 반영 시점 미보장. 마감: 광고 목15시, 상담 교육 목17시, 랜딩 목18시. 제품팀 목14시 확정 사양·알려진 제한 전달. 초대 800명, 첫 4시간 문의율 20%, 상담원 4명, 시간당 5건, 지원 창 4시간, 출시 10시.
- 미확인: 기술 배포 승인 여부, 결제 검증 결과, 인원 충원(추가 4명) 승인 여부. → 완료로 표기 금지, '대기/미승인'으로 표기.
- 모순: 광고 초안 '즉시 자동 환불' ≠ 확정 정책 '영업일 2일 내 처리 시작'. → 광고 문구 수정 필수.

[2] 단일 기준 문서 원칙
- '고객 커뮤니케이션 기준서(SSOT)' 1개만 원본. 광고·상담·랜딩은 인용만 하고 독자 문구 신설 금지.
- 변경 흐름: 제품팀 목14시 사양 전달 → 기준서 반영(담당: 운영·커뮤니케이션) → 광고 목15시 → 상담 교육 목17시 → 랜딩 목18시 순 승인. 각 단계 선행조건은 직전 단계 승인.

[3] 운영 일정표(담당·기한·선행조건)
- 목14:00 제품팀 사양·제한 전달 / 선행: 없음 / 담당: 제품팀
- 목14:00~15:00 기준서 v1 반영 / 선행: 제품팀 전달 / 담당: 운영·커뮤니케이션
- 목15:00 광고 문구 최종(즉시 자동 환불 삭제) / 선행: 기준서 v1 / 담당: 광고+운영
- 목17:00 상담 교육(분류·FAQ·금지 표현) / 선행: 기준서 v1 / 담당: 운영·커뮤니케이션
- 목18:00 랜딩 수정 / 선행: 기준서 v1 / 담당: 랜딩+운영
- 금10:00 출시 / 선행: 기술 배포 승인(미확인), 결제 검증(미확인), 위 3개 마감 완료

[4] 고객 안내문(확정 문구)
"문의를 접수해 주셔서 감사합니다. 접수하신 내용은 영업일 기준 2일 안에 처리 시작됩니다. 처리 완료 시점과 결제(PG) 반영 시점은 보장되지 않으며, 순차 처리됩니다. 결제·환불 오류는 최우선으로 확인합니다."

[5] FAQ
- Q 즉시 환불되나요? A 아니요. 접수 후 영업일 2일 내 처리 시작이며 완료·PG 반영은 미보장입니다.
- Q 4시간 내 모두 답변되나요? A 문의량에 따라 지연될 수 있습니다. 우선순위에 따라 순차 처리됩니다.
- Q 결제 오류는? A 최우선 순위로 분류됩니다.

[6] 상담 분류 기준(우선순위)
- P1 결제/환불 오류 > P2 서비스 이용 불가 > P3 일반 문의. P1 즉시 태깅·에스컬레이션.

[7] 인력 대안(충원 미승인 전제)
- 대기열·우선순위, 자동 응답·FAQ 셀프서비스, 콜백 예약, 4시간 내 미처리 약 80건 지연 가능성 사전 고지.

[8] 비상 계획
- 광고 지연: 트리거 목15시 미완 → 기준서 문구로 임시 대체, 담당 운영, 고객 안내 동일.
- 기술 배포 승인 지연: 트리거 금10시 미승인 → 출시 보류 또는 제한 오픈, 담당 제품·기술, 고객 안내 '일정 변동' 공지.
- 문의 급증: 트리거 4시간 문의 >160 → P1 우선, 콜백 확대, 추가 인력 승인 요청(미승인 상태 명시).

[9] 출시 직전 확인표
- 정책 문구 일치(즉시 자동 환불 없음) □ / 승인 상태(배포·결제·충원) □ / 인력 4명 □ / 랜딩·광고·교육 완료 □ / 롤백·에스컬레이션 경로 □

[10] 자체 점검
- 확정/제안 구분 표기 완료. 미승인 충원·환불·배포를 완료로 쓰지 않음. 실제 외부 연락·시스템 변경 없음(미검증).

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
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "ad_copy_deadline_hour": 15,
  "training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "invited_users": 800,
  "inquiry_rate_first_4h": 0.2,
  "agents_current": 4,
  "cases_per_agent_per_hour": 5,
  "hiring_approved": false,
  "tech_deploy_approved": false,
  "payment_verification_done": false,
  "single_source_document": true,
  "external_contact_performed": false,
  "system_change_performed": false
}
```

### Evidence

**1.** expected_contacts = 800 × 0.20 = 160

**2.** four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80

**3.** backlog_without_hiring = 160 − 80 = 80

**4.** required_total_agents = 160 ÷ (5 × 4) = 8

**5.** additional_agents = 8 − 4 = 4

**6.** support_window_hours = 4, launch_hour = 10 (제공 가정)

**7.** 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 미보장 (제공 자료)

**8.** 광고 초안 '즉시 자동 환불'은 확정 정책과 상충 → immediate_refund_promised=false로 표기

**9.** 마감: 광고 목15시, 상담 교육 목17시, 랜딩 목18시, 제품팀 사양 목14시 (제공 자료)

**10.** 미확인: 기술 배포 승인, 결제 검증, 인원 충원 승인 (제공 자료에 결과 미제공)

**11.** 실제 고객 연락·외부 시스템 변경·배포·결제 검증은 수행하지 않음(미검증)

## launch-operations/integrate.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-launch-operations-baseline/artifacts/launch-operations/integrate.json)

capacity_and_policy와 customer_comms 두 선행 산출물을 통합해 금요일 10시 출시용 launch_operations_report를 작성했다. 두 산출물의 7개 숫자 키와 immediate_refund_promised 값이 완전히 일치함을 대조 확인했다.

[1] 용량 계산(제공 가정만 사용)
expected_contacts = 800 × 0.20 = 160건, four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건, backlog_without_hiring = 160 − 80 = 80건, required_total_agents = 160 ÷ (5 × 4) = 8명, additional_agents = 8 − 4 = 4명. support_window_hours = 4, launch_hour = 10. 충원은 미승인(hiring_approved=false)이므로 추가 4명은 '필요 인원'일 뿐 확보 인력이 아니다.

[2] 확정/미확인 사항표
- 확정: 접수 후 영업일 2일 내 처리 시작(policy_processing_start_business_days=2). 처리 완료 시점·PG 반영 시점 미보장(processing_completion_guaranteed=false, pg_reflection_guaranteed=false). 마감: 제품팀 사양 목14시, 광고 목15시, 상담 교육 목17시, 랜딩 목18시. 초대 800명, 첫 4시간 문의율 20%, 상담원 4명, 시간당 5건, 지원 창 4시간, 출시 10시.
- 미확인: 기술 배포 승인(tech_deploy_approval_provided=false), 결제 검증(payment_verification_provided=false), 인원 충원 승인(hiring_approved=false). → 완료로 표기 금지, '대기/미승인'으로 표기.
- 모순: 광고 초안 '즉시 자동 환불' ≠ 확정 정책 '영업일 2일 내 처리 시작' → immediate_refund_promised=false, 광고 문구 수정 필수.

[3] 단일 기준 문서(SSOT) 원칙과 변경 전달·승인 흐름
'고객 커뮤니케이션 기준서' 1개만 원본으로 두고 광고·상담·랜딩은 인용만 하며 독자 문구 신설 금지. 흐름: 제품팀 목14시 사양·제한 전달 → 기준서 v1 반영(운영·커뮤니케이션) → 광고 목15시 → 상담 교육 목17시 → 랜딩 목18시. 각 단계 선행조건은 직전 단계 승인.

[4] 운영 일정표(담당·기한·선행조건)
- 목14:00 제품팀 확정 사양·알려진 제한 전달 / 선행 없음 / 담당 제품팀
- 목14:00~15:00 기준서 v1 반영 / 선행 제품팀 전달 / 담당 운영·커뮤니케이션
- 목15:00 광고 문구 최종('즉시 자동 환불' 삭제) / 선행 기준서 v1 / 담당 광고+운영
- 목17:00 상담 교육(분류·FAQ·금지 표현) / 선행 기준서 v1 / 담당 운영·커뮤니케이션
- 목18:00 랜딩 수정 / 선행 기준서 v1 / 담당 랜딩+운영
- 금10:00 출시 / 선행 기술 배포 승인(미확인), 결제 검증(미확인), 위 3개 마감 완료

[5] 고객 안내문(확정 문구)
"문의를 접수해 주셔서 감사합니다. 접수하신 내용은 영업일 기준 2일 안에 처리 시작됩니다. 처리 완료 시점과 결제(PG) 반영 시점은 보장되지 않으며 순차 처리됩니다. 결제·환불 오류는 최우선으로 확인합니다."

[6] FAQ
- 즉시 환불되나요? 아니요. 접수 후 영업일 2일 내 처리 시작이며 완료·PG 반영은 미보장입니다.
- 4시간 내 모두 답변되나요? 문의량에 따라 지연될 수 있고 우선순위에 따라 순차 처리됩니다.
- 결제 오류는? 최우선 순위로 분류됩니다.

[7] 상담 분류 기준(우선순위)
P1 결제/환불 오류 > P2 서비스 이용 불가 > P3 일반 문의. P1은 즉시 태깅·에스컬레이션.

[8] 인력 대안(충원 미승인 전제)
대기열·우선순위 운영, 자동 응답·FAQ 셀프서비스 유도, 콜백 예약, 4시간 내 미처리 약 80건 지연 가능성 사전 고지.

[9] 비상 계획 3종
- 광고 지연: 트리거 목15시 미완 → 기준서 문구로 임시 대체, 담당 운영, 고객 안내 동일.
- 기술 배포 승인 지연: 트리거 금10시 미승인 → 출시 보류 또는 제한 오픈, 담당 제품·기술, '일정 변동' 공지.
- 문의 급증: 트리거 4시간 문의 >160 → P1 우선, 콜백 확대, 추가 인력 승인 요청(미승인 상태 명시).

[10] 출시 직전 확인표
정책 문구 일치('즉시 자동 환불' 없음) □ / 승인 상태(배포·결제·충원) □ / 인력 4명 □ / 랜딩·광고·교육 완료 □ / 롤백·에스컬레이션 경로 □

[11] 실제 수행/설계 제안 구분
본 산출물은 계산·분석·설계 문서이며 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않았다(actual_implementation_performed=false, actual_testing_performed=false, customer_contact_performed=false, external_system_change_performed=false). 승인되지 않은 충원·환불·배포는 완료로 기술하지 않았다.

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
  "invited_users": 800,
  "contact_rate_first_4h": 0.2,
  "agents_current": 4,
  "contacts_per_agent_per_hour": 5,
  "hiring_approved": false,
  "policy_processing_start_business_days": 2,
  "processing_completion_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "product_spec_delivery_thu_hour": 14,
  "ad_copy_deadline_thu_hour": 15,
  "agent_training_thu_hour": 17,
  "landing_fix_deadline_thu_hour": 18,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "customer_contact_performed": false,
  "external_system_change_performed": false
}
```

### Evidence

**1.** expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)

**2.** four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건

**3.** backlog_without_hiring = 160 − 80 = 80건

**4.** required_total_agents = 160 ÷ (5 × 4) = 8명

**5.** additional_agents = 8 − 4 = 4명

**6.** support_window_hours = 4, launch_hour = 10 (제공 가정)

**7.** 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 미보장 (제공 자료)

**8.** 광고 초안 '즉시 자동 환불'은 확정 정책과 상충 → immediate_refund_promised=false

**9.** 마감: 제품팀 사양 목14시, 광고 목15시, 상담 교육 목17시, 랜딩 목18시 (제공 자료)

**10.** 미확인: 기술 배포 승인, 결제 검증, 인원 충원 승인 (제공 자료에 결과 미제공)

**11.** 두 선행 산출물(capacity_and_policy, customer_comms)의 7개 숫자 키와 immediate_refund_promised 값이 상호 일치함을 대조 확인

**12.** 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음(미검증)
