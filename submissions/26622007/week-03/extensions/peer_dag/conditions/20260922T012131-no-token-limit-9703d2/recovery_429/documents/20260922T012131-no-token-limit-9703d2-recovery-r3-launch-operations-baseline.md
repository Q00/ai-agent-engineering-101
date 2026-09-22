# launch-operations / baseline — 429 복구

상태: succeeded. facts: 8/8.
모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.

## launch-operations.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-baseline/artifacts/launch-operations.json)

금요일 10시 출시 통합 운영 패키지(설계 제안). capacity_and_risk와 customer_comms 두 선행 산출물을 대조한 결과 수치 불일치 0건이며, 이를 통합해 확정/미확인 사항표, 모순·인터페이스 점검표, 비상 계획 3종, 출시 직전 확인표, 담당·기한·선행조건이 연결된 운영 일정, 고객 안내문·FAQ·상담 분류 기준, 용량 계산·인력 대안을 완성했다.

[1] 확정/미확인 사항표
- 확정: 정책 '상담 접수 후 영업일 2일 내 처리 시작'(처리 완료·PG 반영 시점 미보장), 용량 계산(expected_contacts=160, four_hour_capacity=80, backlog_without_hiring=80, required_total_agents=8, additional_agents=4, support_window_hours=4, launch_hour=10), 일정 마감(제품팀 사양 14시, 광고 15시, 상담 교육 17시, 랜딩 18시), immediate_refund_promised=false.
- 미확인: 기술 배포 승인(출시 가능성 영향), 결제 검증 결과(환불·결제 오류 대응 정확성 영향), 인원 충원 승인(대기열 80건 규모 영향). 각 미확인 항목은 '확인 후 게시/안내' 보류로 표기.

[2] 모순·인터페이스 점검표
- 모순1: 광고 초안 '즉시 자동 환불' vs 확정 정책 '영업일 2일 내 처리 시작' → 금지 표현 목록('즉시 자동 환불','즉시 환불','당일 환불 보장','100% 자동 처리')으로 봉합, SSOT 인용 규칙 적용.
- 모순2: 처리 완료·PG 반영 시간 미보장인데 고객이 완료 시점을 기대할 위험 → 안내문·FAQ에 '완료 시점 보장 안 함' 명시.
- 인터페이스: 광고/상담/랜딩 3채널은 SSOT v1.0 '고객 노출 문구' 섹션만 인용, 임의 변형 금지. 정책 문구 변경은 C 검수 + 제품팀 확인 필요.

[3] 비상 계획 3종(트리거·대응·담당·에스컬레이션)
- 광고 지연: 트리거 15시 미제출 → 금지 표현 포함 여부 우선 검수, 미승인 문구 게시 보류, SSOT 문구로 대체. 담당 광고 담당(검수 C). 에스컬레이션 C→제품팀.
- 기술 승인 지연: 트리거 출시 전 기술 배포 승인 미확인 → 기술 관련 약속 문구 전면 보류, '확인 후 안내' 통일, 상담은 알려진 제한만 안내. 담당 제품팀(미확인). 에스컬레이션 C→제품팀.
- 문의 급증: 트리거 대기열 80건 초과 → 우선순위(결제·기술>환불>일반) 적용, 대기 안내문 발송, 미승인 충원은 '충원 미승인' 상태로 에스컬레이션. 담당 상담 리드. 에스컬레이션 상담 리드→C→제품팀.

[4] 출시 직전 확인표
- SSOT v1.0 동결 여부 / 광고·랜딩 금지 표현 0건 / 상담 교육 완료 / 기술 배포 승인 확인(미확인) / 결제 검증 확인(미확인) / 비상 계획 숙지 / 충원 승인 여부(미승인).

[5] 운영 일정(담당·기한·선행조건)
- 14:00 제품팀→C: 확정 사양·제한 전달 | 선행 없음 | 제품팀.
- 14:00~14:30 C: SSOT v1.0 작성 | 선행 14:00 전달 | C.
- 14:30 C→광고/상담/랜딩: 문구 배포 | 선행 SSOT v1.0 | C.
- 15:00 광고 문구 마감·제출 | 선행 14:30 배포 | 광고 담당(검수 C).
- 17:00 상담 교육 완료 | 선행 14:30 배포 | 상담 리드.
- 18:00 랜딩 수정 마감 | 선행 14:30 배포 | 랜딩 담당(검수 C).
- 출시 전: 기술 배포 승인·결제 검증 확인 | 선행 미제공 | 제품팀(미확인).

[6] 고객 안내문·FAQ·상담 분류 기준
- 안내문: '접수하신 상담은 영업일 기준 2일 안에 처리 시작됩니다. 처리 완료 시점과 PG 반영 시점은 보장되지 않습니다.' 금지 표현 배제.
- FAQ: 처리 시점(영업일 2일 내 시작, 완료 미보장), 환불(자동 즉시 아님), 결제(PG 반영 미보장), 문의 급증(접수 순서·긴급도 우선), 연결 지연(접수 채널 순차 처리).
- 분류: 환불/결제/기술/일반 4분류, 우선순위 결제·기술>환불>일반.

[7] 용량 계산·인력 대안
- expected_contacts=800×0.2=160, four_hour_capacity=4×5×4=80, backlog_without_hiring=160-80=80, required_total_agents=160/(5×4)=8, additional_agents=8-4=4.
- 충원 불가 시 대안: 대기열 운영, 결제 오류·환불 우선순위, '영업일 2일 내 처리 시작' 부합 안내 문구, 즉시 자동 환불 표현 금지.

[8] 수행 구분
- 실제 고객 연락·외부 시스템 변경·구현·테스트는 미수행(미검증). 모든 일정·문구·비상 계획은 설계 제안이며, 미승인 충원·환불·배포를 완료로 표기하지 않음.

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
  "pg_reflection_time_guaranteed": false,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "staffing_increase_approved": false,
  "product_spec_delivery_hour": 14,
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_deadline_hour": 18,
  "ssot_document_defined": true,
  "ops_schedule_defined": true,
  "customer_notice_drafted": true,
  "faq_drafted": true,
  "classification_criteria_defined": true,
  "contingency_plan_defined": true,
  "prelaunch_checklist_defined": true,
  "capacity_facts_consistent": true,
  "comms_facts_consistent": true,
  "actual_customer_contact_performed": false,
  "external_system_change_performed": false,
  "implementation_or_test_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.2 = 160 (source: 초대 800명, 첫 4시간 20% 문의 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80 (source: 상담원 4명, 시간당 5건)
3. backlog_without_hiring = 160 - 80 = 80
4. required_total_agents = 160 / (5 × 4) = 8
5. additional_agents = 8 - 4 = 4
6. support_window_hours = 4, launch_hour = 10 (source: 금요일 10시 출시, 첫 4시간)
7. immediate_refund_promised = false (source: 확정 정책 '영업일 2일 내 처리 시작' vs 광고 초안 '즉시 자동 환불' 모순)
8. 일정 근거: 제품팀 사양 14시, 광고 마감 15시, 상담 교육 17시, 랜딩 마감 18시 (source 명시)
9. 미확인 근거: 기술 배포 승인·결제 검증 결과·인원 충원 승인 모두 미제공(source 명시)
10. 선행 산출물 대조: capacity_and_risk facts(expected_contacts=160, four_hour_capacity=80, backlog_without_hiring=80, required_total_agents=8, additional_agents=4, support_window_hours=4, launch_hour=10, immediate_refund_promised=false)와 customer_comms facts가 동일 값으로 일치, 불일치 0건
11. 실제 고객 연락·외부 시스템 변경·구현/테스트는 수행하지 않음(미검증), 본 산출물은 통합 설계 제안 텍스트임
## launch-operations/capacity_and_risk.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-baseline/artifacts/launch-operations/capacity_and_risk.json)

제공된 수치만으로 출시 첫 4시간 용량·인력 대안과 정책 모순·미확인 리스크를 정리했다. 계산: 초대 800명 중 20% 문의 → 160건. 상담원 4명 × 시간당 5건 × 4시간 = 80건 처리 가능. 미처리 160-80=80건. 전량 처리를 위해서는 160/(5×4)=8명 필요, 추가 4명. 지원 창구는 4시간, 출시 시각은 10시. 정책 모순: 확정 정책은 '영업일 2일 내 처리 시작'이며 처리 완료·PG 반영 시간은 미보장인데, 광고 초안은 '즉시 자동 환불'을 약속하므로 immediate_refund_promised=false로 표기한다. 미확인: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인 모두 미제공이며 각각 출시 가능성·환불 처리 정확성·대기열 규모에 영향. 충원 불가 시 대안은 대기열 운영, 결제 오류·환불 문의 우선순위, 정책에 부합하는 안내 문구(즉시 환불 아님, 영업일 2일 내 처리 시작)로 한정한다. 본 작업은 설계·분석 제안이며 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않았다(미검증).

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
  "tech_deploy_approved": false,
  "payment_verification_received": false,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "customer_contact_performed": false,
  "external_system_change_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.2 = 160건
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
3. backlog_without_hiring = 160 - 80 = 80건
4. required_total_agents = 160 / (5 × 4) = 8명
5. additional_agents = 8 - 4 = 4명
6. support_window_hours = 4, launch_hour = 10 (출시 후 첫 4시간 가정)
7. 모순: 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장 vs 광고 초안 '즉시 자동 환불' → immediate_refund_promised=false
8. 미확인: 기술 배포 승인 미제공(출시 가능성 영향), 결제 검증 결과 미제공(환불·결제 오류 대응 정확성 영향), 인원 충원 승인 미제공(대기열 80건 규모 영향)
9. 충원 불가 시 대안(설계 제안): 대기열 운영, 결제 오류·환불 문의 우선순위, '영업일 2일 내 처리 시작'에 부합하는 안내 문구 사용, 즉시 자동 환불 표현 금지
10. 수행 구분: 본 산출물은 계산·리스크·대안 설계 제안이며 실제 구현·테스트·고객 연락·외부 시스템 변경은 미수행(미검증)
## launch-operations/customer_comms.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-baseline/artifacts/launch-operations/customer_comms.json)

확정 정책('상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장)에 맞춘 고객 안내문·FAQ·상담 분류 기준과 광고·상담·랜딩 단일 기준 문서, 변경 전달·승인 흐름, 담당·기한·선행조건이 연결된 운영 일정을 텍스트로 작성했다. '즉시 자동 환불' 표현은 전면 배제했고, 미승인 충원·환불·배포는 완료로 쓰지 않았다. 실제 고객 연락·외부 시스템 변경·구현·테스트는 수행하지 않았으며(미검증), 아래 일정·문구는 설계 제안이다.

[1] 단일 기준 문서(SSOT) 개요
- 문서명: 신규 서비스 출시 커뮤니케이션 단일 기준서 v1(목요일 14시 제품팀 확정 사양 반영 후 동결).
- 적용 범위: 광고 문구, 상담 스크립트/교육자료, 랜딩 페이지 문구. 세 채널은 이 문서의 '고객 노출 문구' 섹션만 인용하며 임의 변형 금지.
- 필수 포함: 확정 정책 1건(영업일 2일 내 처리 시작), 미보장 항목(처리 완료 시점, PG 반영 시점), 알려진 제한(제품팀 14시 전달분), 금지 표현 목록('즉시 자동 환불', '즉시 환불', '당일 환불 보장', '100% 자동 처리').
- 버전 규칙: v1.0(14시 사양 반영) → 변경 시 v1.1, 변경 이력에 시각·변경자·승인자 기록.

[2] 변경 전달·승인 흐름(목요일)
- 14:00 제품팀 → 운영·커뮤니케이션(C)에 확정 사양·알려진 제한 전달. C가 SSOT v1.0 작성(14:00~14:30).
- 14:30 C → 광고 담당에 광고 인용 문구 전달, 상담 리드에 스크립트·FAQ 전달, 랜딩 담당에 랜딩 문구 전달(동시 발송).
- 15:00 광고 문구 마감: 광고업체 최종본 제출. C가 SSOT 대조 검수(금지 표현 0건 확인) 후 승인.
- 17:00 상담 교육: 상담 리드가 상담원 4명 대상 SSOT 기반 교육 완료, 분류 기준·FAQ 숙지 확인.
- 18:00 랜딩 수정 마감: 랜딩 담당이 SSOT 문구 반영 완료, C 최종 대조.
- 승인권: 정책 문구 변경은 C 검수 + 제품팀 확인 필요. 미승인 상태로 광고/랜딩 게시 금지.
- 미확인: 기술 배포 승인, 결제 검증 결과는 미제공 → 해당 문구는 '확인 후 게시' 보류 항목으로 표기.

[3] 고객 안내문(확정 정책 기반)
"안녕하세요. 문의를 접수해 주셔서 감사합니다. 접수하신 상담은 영업일 기준 2일 안에 처리 시작됩니다. 다만 처리 완료 시점과 결제(PG) 반영 시점은 보장되지 않으며, 순차 처리로 지연될 수 있습니다. 접수 순서와 긴급도에 따라 우선 처리되며, 진행 상황은 접수 채널로 안내드립니다. 불편을 드려 죄송합니다."
- 금지: '즉시 자동 환불', '바로 환불', '당일 처리 보장'.

[4] FAQ(확정 사실만)
Q1. 언제 처리되나요? → 영업일 2일 안에 처리 시작됩니다. 완료 시점은 보장하지 않습니다.
Q2. 환불은 자동인가요? → 자동 즉시 환불이 아닙니다. 접수 후 정해진 절차와 영업일 2일 내 처리 시작 정책에 따릅니다.
Q3. 결제는 언제 반영되나요? → PG 반영 시점은 보장하지 않습니다.
Q4. 문의가 많으면? → 접수 순서·긴급도 기준 우선순위로 처리하며, 대기 시 안내드립니다.
Q5. 상담원 연결이 안 되면? → 접수 채널(채팅/폼)로 접수되며 순차 처리됩니다.
- 미확인: 기술 배포 일정, 결제 검증 결과 관련 답변은 '확인 후 안내'로 보류.

[5] 상담 분류 기준(4분류)
- 환불: 환불 요청/정책 문의 → 정책 문구 안내, '즉시 자동 환불' 아님 명시, 접수 후 영업일 2일 내 처리 시작.
- 결제: 결제 오류/중복/미반영 → PG 반영 시점 미보장 안내, 결제 검증 결과 미제공 상태 명시.
- 기술: 오류/접속/기능 문의 → 제품팀 14시 전달된 알려진 제한 범위 내 안내, 배포 승인 미확인 시 보류.
- 일반: 기타/사용법 → FAQ 기반 안내.
- 우선순위: 결제·기술(서비스 중단) > 환불 > 일반. 급증 시 접수 순서+긴급도 병행.

[6] 운영 일정표(담당·기한·선행조건)
- 14:00 제품팀→C: 확정 사양·제한 전달 | 선행: 없음 | 담당: 제품팀.
- 14:00~14:30 C: SSOT v1.0 작성 | 선행: 14:00 전달 | 담당: C.
- 14:30 C→광고/상담/랜딩: 문구 배포 | 선행: SSOT v1.0 | 담당: C.
- 15:00 광고: 문구 마감·제출 | 선행: 14:30 배포 | 담당: 광고 담당(검수 C).
- 17:00 상담: 교육 완료 | 선행: 14:30 배포 | 담당: 상담 리드.
- 18:00 랜딩: 수정 마감 | 선행: 14:30 배포 | 담당: 랜딩 담당(검수 C).
- 출시 전: 기술 배포 승인·결제 검증 확인 | 선행: 미제공 | 담당: 제품팀(미확인).

[7] 비상 계획
- 광고 지연: 15시 미제출 시 금지 표현 포함 여부 우선 검수, 미승인 문구는 게시 보류, SSOT 문구로 대체.
- 기술 승인 지연: 기술 관련 약속 문구 전면 보류, '확인 후 안내'로 통일, 상담은 알려진 제한만 안내.
- 문의 급증: 우선순위(결제·기술>환불>일반) 적용, 대기 안내문 자동 발송, 미승인 충원은 완료로 쓰지 않고 '충원 미승인' 상태로 에스컬레이션.

[8] 출시 직전 확인표
- SSOT v1.0 동결 여부 / 광고·랜딩 금지 표현 0건 / 상담 교육 완료 / 기술 배포 승인 확인(미확인) / 결제 검증 확인(미확인) / 비상 계획 숙지.

[용량 계산(참고, task 범위 외 수치 검산)]
expected_contacts=800*0.2=160, four_hour_capacity=4*5*4=80, backlog_without_hiring=160-80=80, required_total_agents=160/(5*4)=8, additional_agents=8-4=4, support_window_hours=4, launch_hour=10, immediate_refund_promised=false.

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
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_time_guaranteed": false,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "staffing_increase_approved": false,
  "ssot_document_created": true,
  "customer_notice_drafted": true,
  "faq_drafted": true,
  "classification_criteria_defined": true,
  "ops_schedule_defined": true,
  "contingency_plan_defined": true,
  "prelaunch_checklist_defined": true,
  "actual_customer_contact_performed": false,
  "external_system_change_performed": false,
  "implementation_or_test_performed": false
}
```

### Evidence

1. expected_contacts = 800 * 0.2 = 160 (source: 초대 800명, 첫 4시간 20% 문의 가정)
2. four_hour_capacity = 4명 * 5건/시간 * 4시간 = 80 (source: 상담원 4명, 시간당 5건)
3. backlog_without_hiring = 160 - 80 = 80
4. required_total_agents = 160 / (5*4) = 8
5. additional_agents = 8 - 4 = 4
6. support_window_hours = 4, launch_hour = 10 (source: 금요일 10시 출시, 첫 4시간)
7. immediate_refund_promised = false (source: 확정 정책은 '영업일 2일 안에 처리 시작', 광고 초안의 '즉시 자동 환불'은 정책과 다름)
8. 일정 근거: 광고 문구 마감 목요일 15시, 상담 교육 목요일 17시, 랜딩 수정 마감 목요일 18시, 제품팀 사양 전달 목요일 14시 (source 명시)
9. 미확인 근거: 기술 배포 승인과 결제 검증 결과는 아직 미제공(source 명시), 인원 충원은 미승인(source 명시)
10. 실제 고객 연락·외부 시스템 변경·구현/테스트는 수행하지 않음(미검증), 본 산출물은 설계 제안 텍스트임
## launch-operations/integration.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-baseline/artifacts/launch-operations/integration.json)

capacity_and_risk와 customer_comms 산출물을 통합한 최종 운영 패키지다. 모든 수치는 두 선행 산출물과 일치하며 불일치 없음.

[1] 확정/미확인 사항표
- 확정: 정책 '상담 접수 후 영업일 2일 내 처리 시작'(처리 완료·PG 반영 시점 미보장), 용량 계산(expected_contacts=160, four_hour_capacity=80, backlog_without_hiring=80, required_total_agents=8, additional_agents=4, support_window_hours=4, launch_hour=10), 일정 마감(제품팀 사양 14시, 광고 15시, 상담 교육 17시, 랜딩 18시), immediate_refund_promised=false.
- 미확인: 기술 배포 승인(출시 가능성 영향), 결제 검증 결과(환불·결제 오류 대응 정확성 영향), 인원 충원 승인(대기열 80건 규모 영향). 각 미확인 항목은 '확인 후 게시/안내' 보류로 표기.

[2] 모순·인터페이스 점검표
- 모순1: 광고 초안 '즉시 자동 환불' vs 확정 정책 '영업일 2일 내 처리 시작' → 금지 표현 목록('즉시 자동 환불','즉시 환불','당일 환불 보장','100% 자동 처리')으로 봉합, SSOT 인용 규칙 적용.
- 모순2: 처리 완료·PG 반영 시간 미보장인데 고객이 완료 시점을 기대할 위험 → 안내문·FAQ에 '완료 시점 보장 안 함' 명시.
- 인터페이스: 광고/상담/랜딩 3채널은 SSOT v1.0 '고객 노출 문구' 섹션만 인용, 임의 변형 금지. 정책 문구 변경은 C 검수 + 제품팀 확인 필요.

[3] 비상 계획 3종(트리거·대응·담당·에스컬레이션)
- 광고 지연: 트리거 15시 미제출 → 금지 표현 포함 여부 우선 검수, 미승인 문구 게시 보류, SSOT 문구로 대체. 담당 광고 담당(검수 C). 에스컬레이션 C→제품팀.
- 기술 승인 지연: 트리거 출시 전 기술 배포 승인 미확인 → 기술 관련 약속 문구 전면 보류, '확인 후 안내' 통일, 상담은 알려진 제한만 안내. 담당 제품팀(미확인). 에스컬레이션 C→제품팀.
- 문의 급증: 트리거 대기열 80건 초과 → 우선순위(결제·기술>환불>일반) 적용, 대기 안내문 발송, 미승인 충원은 '충원 미승인' 상태로 에스컬레이션. 담당 상담 리드. 에스컬레이션 상담 리드→C→제품팀.

[4] 출시 직전 확인표
- SSOT v1.0 동결 여부 / 광고·랜딩 금지 표현 0건 / 상담 교육 완료 / 기술 배포 승인 확인(미확인) / 결제 검증 확인(미확인) / 비상 계획 숙지 / 충원 승인 여부(미승인).

[5] 운영 일정(담당·기한·선행조건)
- 14:00 제품팀→C: 확정 사양·제한 전달 | 선행 없음 | 제품팀.
- 14:00~14:30 C: SSOT v1.0 작성 | 선행 14:00 전달 | C.
- 14:30 C→광고/상담/랜딩: 문구 배포 | 선행 SSOT v1.0 | C.
- 15:00 광고 문구 마감·제출 | 선행 14:30 배포 | 광고 담당(검수 C).
- 17:00 상담 교육 완료 | 선행 14:30 배포 | 상담 리드.
- 18:00 랜딩 수정 마감 | 선행 14:30 배포 | 랜딩 담당(검수 C).
- 출시 전: 기술 배포 승인·결제 검증 확인 | 선행 미제공 | 제품팀(미확인).

[6] 고객 안내문·FAQ·상담 분류 기준
- 안내문: '접수하신 상담은 영업일 기준 2일 안에 처리 시작됩니다. 처리 완료 시점과 PG 반영 시점은 보장되지 않습니다.' 금지 표현 배제.
- FAQ: 처리 시점(영업일 2일 내 시작, 완료 미보장), 환불(자동 즉시 아님), 결제(PG 반영 미보장), 문의 급증(접수 순서·긴급도 우선), 연결 지연(접수 채널 순차 처리).
- 분류: 환불/결제/기술/일반 4분류, 우선순위 결제·기술>환불>일반.

[7] 용량 계산·인력 대안
- expected_contacts=800×0.2=160, four_hour_capacity=4×5×4=80, backlog_without_hiring=160-80=80, required_total_agents=160/(5×4)=8, additional_agents=8-4=4.
- 충원 불가 시 대안: 대기열 운영, 결제 오류·환불 우선순위, '영업일 2일 내 처리 시작' 부합 안내 문구, 즉시 자동 환불 표현 금지.

[8] 수행 구분
- 실제 고객 연락·외부 시스템 변경·구현·테스트는 미수행(미검증). 모든 일정·문구·비상 계획은 설계 제안이며, 미승인 충원·환불·배포를 완료로 표기하지 않음.

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
  "pg_reflection_time_guaranteed": false,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "staffing_increase_approved": false,
  "product_spec_delivery_hour": 14,
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_deadline_hour": 18,
  "ssot_document_defined": true,
  "contingency_plan_defined": true,
  "prelaunch_checklist_defined": true,
  "classification_criteria_defined": true,
  "customer_notice_drafted": true,
  "faq_drafted": true,
  "ops_schedule_defined": true,
  "actual_customer_contact_performed": false,
  "external_system_change_performed": false,
  "implementation_or_test_performed": false,
  "capacity_facts_consistent": true,
  "comms_facts_consistent": true
}
```

### Evidence

1. expected_contacts = 800 × 0.2 = 160 (source: 초대 800명, 첫 4시간 20% 문의 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80 (source: 상담원 4명, 시간당 5건)
3. backlog_without_hiring = 160 - 80 = 80
4. required_total_agents = 160 / (5 × 4) = 8
5. additional_agents = 8 - 4 = 4
6. support_window_hours = 4, launch_hour = 10 (source: 금요일 10시 출시, 첫 4시간)
7. immediate_refund_promised = false (source: 확정 정책 '영업일 2일 내 처리 시작' vs 광고 초안 '즉시 자동 환불' 모순)
8. 일정 근거: 제품팀 사양 14시, 광고 마감 15시, 상담 교육 17시, 랜딩 마감 18시 (source 명시)
9. 미확인 근거: 기술 배포 승인·결제 검증 결과·인원 충원 승인 모두 미제공(source 명시)
10. 선행 산출물 대조: capacity_and_risk facts(expected_contacts=160, four_hour_capacity=80, backlog_without_hiring=80, required_total_agents=8, additional_agents=4, support_window_hours=4, launch_hour=10, immediate_refund_promised=false)와 customer_comms facts가 동일 값으로 일치, 불일치 0건
11. 실제 고객 연락·외부 시스템 변경·구현/테스트는 수행하지 않음(미검증), 본 산출물은 통합 설계 제안 텍스트임