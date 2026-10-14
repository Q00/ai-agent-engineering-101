# launch-operations / homogeneous — 429 복구

상태: succeeded. facts: 8/8.
모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.

## launch-operations.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations.json)

금요일 10시 출시를 위한 통합 운영 패키지를 4개 하위 산출물(capacity_calc, policy_schedule, customer_comms, contingency_checklist)로 분해·실행·통합했다. 핵심 수치: 초대 800명 × 첫 4시간 문의율 20% = 160건 예상, 상담원 4명 × 5건/시간 × 4시간 = 80건 처리, 미처리 80건, 전량 처리에 총 8명 필요(추가 4명). 충원은 미승인(hiring_approved=false)이므로 대기열·우선순위(결제/환불 오류 > 계정 접근 > 일반 문의)·고객 안내 대안으로 대체한다. 확정 정책은 '상담 접수 후 영업일 2일 안에 처리 시작'이며 완료·PG 반영은 미보장이므로 광고 초안의 '즉시 자동 환불'은 모순(immediate_refund_promised=false)이다. 단일 기준 문서 v1.0(허용/금지 표현)과 승인 흐름(제품팀 목 14:00 → 기준문서 담당 14:00~14:30 → 광고 15:00 / 상담 교육 17:00 / 랜딩 18:00)을 설계했다. 기술 배포 승인·결제 검증은 미제공이므로 미확인으로 표기하고 완료로 쓰지 않는다. 비상 계획(광고 지연/기술 승인 지연/문의 급증)과 출시 직전 확인표를 포함한다. 모든 산출물은 설계 제안이며 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않았다.

[운영 일정] 목 14:00 제품팀 사양·제한 전달(선행 없음) → 목 14:00~14:30 기준문서 v1.0 확정(기준문서 담당) → 목 15:00 광고 문구 마감(광고 담당) / 목 17:00 상담 교육(상담 운영 담당) / 목 18:00 랜딩 수정 마감(랜딩 담당) → 금 10:00 출시(출시 총괄, 선행: 기술 배포 승인·결제 검증·3채널 반영 완료).

[확정/미확인] 확정: 정책 원문(2영업일 내 시작, 완료·PG 미보장), 마감 시각 15/17/18시, 제품팀 14시, 초대 800명, 문의율 20% 가정, 상담원 4명·시간당 5건. 미확인: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인, 실제 문의율.

[고객 안내/FAQ/분류] 안내문에 접수 확인, 처리 시작 기준(영업일 2일), 완료·PG 반영 미보장, 문의 채널을 명시하고 '즉시/자동 환불' 표현을 배제. FAQ 6문항(환불 시점, 즉시 여부, PG 반영, 문의 폭주, 완료 시각, 접수 확인). 상담 분류 P0 결제 오류·중복 청구(대기열 우회) > P1 일반 환불 접수 > P2 정책 문의 > P3 불만/에스컬레이션.

[비상 계획] 광고 지연: 허용 표현만으로 임시 문구, 금지 표현 잔존 시 게시 보류. 기술 승인 지연: 출시 연기 또는 기능 제한 출시 결정 에스컬레이션, 미확인 사항 고지. 문의 급증: 우선순위 큐·대기 안내·FAQ 셀프서비스, 충원은 승인 시에만 실행.

[출시 직전 확인표] 기준문서 v1.0 승인, 3채널 금지 표현 제거, 기술 배포 승인·결제 검증 확보 여부, 상담 인력 승인 상태, 대기열·우선순위·FAQ 준비, 고객 안내문 정책 명시, 미승인 항목 완료 표기 여부 최종 검토.

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
  "first_4h_inquiry_rate": 0.2,
  "agents_available": 4,
  "cases_per_agent_per_hour": 5,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "hiring_approved": false,
  "tech_deploy_approved": false,
  "payment_verification_confirmed": false,
  "product_spec_delivery_hour": 14,
  "ad_copy_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "priority_1": "결제/환불 오류",
  "priority_2": "계정 접근",
  "priority_3": "일반 문의",
  "single_source_doc_defined": true,
  "approval_flow_defined": true,
  "customer_notice_drafted": true,
  "faq_drafted": true,
  "support_classification_defined": true,
  "ad_delay_contingency_defined": true,
  "tech_delay_contingency_defined": true,
  "surge_contingency_defined": true,
  "prelaunch_checklist_defined": true,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "actual_customer_contact_performed": false,
  "actual_external_system_change_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 − 4 = 4명
6. support_window_hours = 4, launch_hour = 10 (제공 자료 가정)
7. 확정 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장 (제공 자료)
8. 광고 초안 '즉시 자동 환불'은 확정 정책과 모순 → immediate_refund_promised=false
9. 마감 시각: 광고 목 15시, 상담 교육 목 17시, 랜딩 목 18시, 제품 사양 전달 목 14시 (제공 자료)
10. 미제공: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인 (제공 자료에 없음) → 모두 미확인으로 표기, 완료로 쓰지 않음
11. 충원 미승인 대안: 접수 순서 기반 대기열·예상 대기 안내, 우선순위(결제/환불 오류 > 계정 접근 > 일반 문의), 영업일 2일 내 처리 시작 정책 고지 및 즉시 자동 환불 아님 명시
12. 단일 기준 문서 v1.0: 허용 표현('영업일 2일 내 처리 시작', '완료까지 추가 시간 소요 가능', 'PG 반영 지연 가능')과 금지 표현('즉시', '자동 환불', '당일 환불 보장', 'PG 즉시 반영') 정의, 광고·상담·랜딩·FAQ에 단일 출처 적용
13. 승인 흐름: 제품팀 목 14:00 전달 → 기준문서 담당 14:00~14:30 v1.0 확정 → 광고 15:00 / 상담 교육 17:00 / 랜딩 18:00 반영, 미승인 문구 게시 금지
14. 비상 계획 3종(광고 지연, 기술 승인 지연, 문의 급증)과 출시 직전 확인표는 위 수치·정책 사실을 전제로 한 설계 제안
15. 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음(미검증), 모든 산출물은 설계 제안
## launch-operations/capacity_calc.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations/capacity_calc.json)

제공된 가정으로 첫 4시간 용량을 계산했다. 초대 800명의 20%가 문의하면 160건이 예상되고, 상담원 4명이 시간당 5건씩 4시간 처리하면 80건을 처리할 수 있어 80건이 미처리된다. 모두 처리하려면 총 8명이 필요하므로 4명 추가 충원이 필요하다. 충원은 아직 승인되지 않았으므로 충원 불가를 전제로 대기열 운영(접수 순서, 예상 대기 안내), 우선순위(결제/환불 오류 > 계정 접근 > 일반 문의), 고객 안내 대안(영업일 2일 내 처리 시작 정책 고지, 즉시 자동 환불 아님 명시)을 제시한다. 광고 초안의 '즉시 자동 환불'은 확정 정책과 모순되므로 단일 기준 문서로 통일해야 하며, 기술 배포 승인과 결제 검증은 미확인 사항이다. 실제 구현·테스트·외부 연락은 수행하지 않았다.

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
  "payment_verification_confirmed": false,
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_fix_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "policy_processing_start_business_days": 2,
  "priority_1": "결제/환불 오류",
  "priority_2": "계정 접근",
  "priority_3": "일반 문의"
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명의 20%가 첫 4시간에 문의 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 − 4 = 4명
6. support_window_hours = 4 (첫 4시간 가정)
7. launch_hour = 10 (금요일 10시 출시)
8. immediate_refund_promised = false: 확정 정책은 '상담 접수 후 영업일 2일 안에 처리 시작'이며 처리 완료·PG 반영 시간은 보장하지 않음. 광고 초안의 '즉시 자동 환불'은 이 정책과 모순
9. hiring_approved = false: 인원 충원은 아직 승인되지 않음
10. tech_deploy_approved = false, payment_verification_confirmed = false: 기술 배포 승인과 결제 검증 결과 미제공
11. 일정 근거: 광고 문구 마감 목요일 15시, 상담 교육 목요일 17시, 랜딩 수정 마감 목요일 18시, 제품팀 확정 사양 전달 목요일 14시
12. 충원 불가 대안: 접수 순서 기반 대기열과 예상 대기 안내, 우선순위(결제/환불 오류 > 계정 접근 > 일반 문의), 고객 안내로 영업일 2일 내 처리 시작 정책 고지 및 즉시 자동 환불 아님 명시
13. 미검증: 실제 구현·테스트·외부 시스템 변경·고객 연락은 수행하지 않음
## launch-operations/contingency_checklist.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations/contingency_checklist.json)

비상 계획과 출시 직전 확인표를 작성하고, 선행 산출물(capacity_calc, policy_schedule)의 수치·정책 사실과 정합화했다. 모든 항목은 설계 제안이며 실제 구현·테스트·외부 연락은 수행하지 않았다.

[1] 광고 지연 시나리오
- 상황: 광고업체 문구 마감 목요일 15시를 초과하거나, '즉시 자동 환불' 등 금지 표현이 제거되지 않은 경우.
- 대응: (a) 기준문서 v1.0의 허용 표현만으로 임시 문구를 구성해 게시하고, '즉시', '자동 환불', '당일 환불 보장', 'PG 즉시 반영' 표현이 남아 있지 않은지 확인한다. (b) 15시 마감을 넘기면 신규 문구 게시를 보류하고 기준문서 담당 승인 요청을 우선한다. (c) 승인 전에는 광고를 게시하지 않으며, 게시 지연 사실을 출시 총괄에 보고한다. (d) 광고가 지연되어도 상담 스크립트·랜딩은 기준문서 v1.0을 단일 출처로 사용하므로 채널 간 약속 불일치는 발생하지 않는다.

[2] 기술 승인 지연 시나리오
- 상황: tech_deploy_approved=false, payment_verification_confirmed=false (미제공).
- 대응: (a) 두 항목을 '미확인'으로 명시하고 완료로 표기하지 않는다. (b) 출시 시각(금 10시) 연기 또는 기능 제한 출시(결제/환불 관련 기능 보류) 중 하나를 출시 총괄이 결정하도록 에스컬레이션한다. (c) 고객 안내문에 미확인 사항(기술 배포 승인·결제 검증 미완)을 고지하고, 결제/환불 오류 가능성에 대한 우선 처리 경로를 안내한다. (d) 승인·검증이 확보되기 전에는 '배포 완료', '결제 검증 완료' 문구를 어떤 채널에도 쓰지 않는다.

[3] 문의 급증 시나리오
- 상황: 첫 4시간 예상 문의 160건 대비 처리 용량 80건으로 미처리 80건 발생, 추가 4명 충원은 hiring_approved=false로 미승인.
- 대응: (a) 우선순위 큐 운영: 1순위 결제/환불 오류, 2순위 계정 접근, 3순위 일반 문의. (b) 접수 순서 기반 대기열과 예상 대기 안내 문구 제공. (c) FAQ·셀프서비스로 일반 문의 유도. (d) 추가 인원은 승인 전제로만 계획하고, 승인 없이 충원 완료로 표기하지 않는다. (e) 고객 안내에 '영업일 2일 내 처리 시작, 완료·PG 반영 미보장' 정책을 고지하고 즉시 자동 환불이 아님을 명시한다.

[4] 출시 직전 확인표 (금 10시 이전 점검)
- [ ] 기준문서 v1.0 승인 완료 (기준문서 담당)
- [ ] 광고·상담·랜딩 3채널 금지 표현('즉시', '자동 환불', '당일 환불 보장', 'PG 즉시 반영') 제거 확인
- [ ] 기술 배포 승인 확보 여부 (미확인 시 출시 연기/기능 제한 결정)
- [ ] 결제 검증 결과 확보 여부 (미확인 시 결제 기능 보류)
- [ ] 상담 인력 계획 및 충원 승인 상태 확인 (hiring_approved=false → 대기열·우선순위 대안 가동)
- [ ] 용량 대비 대기열·우선순위·FAQ 준비 완료
- [ ] 고객 안내문에 미확인 사항 및 정책(2영업일 내 시작, 완료·PG 미보장) 명시
- [ ] 미승인 충원·환불·배포를 완료로 표기한 문구가 없는지 최종 검토

[5] 정합성 검증
- 인용 수치: expected_contacts=160, four_hour_capacity=80, backlog_without_hiring=80, required_total_agents=8, additional_agents=4, support_window_hours=4, launch_hour=10 — capacity_calc facts와 일치.
- 정책: immediate_refund_promised=false, policy_processing_start_business_days=2, completion_time_guaranteed=false, pg_reflection_guaranteed=false — policy_schedule facts와 일치.
- 미확인: tech_deploy_approved=false, payment_verification_confirmed=false, hiring_approved=false — 완료로 표기하지 않음.
- 실제 구현·테스트·외부 연락 미수행.

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
  "payment_verification_confirmed": false,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "ad_copy_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "priority_1": "결제/환불 오류",
  "priority_2": "계정 접근",
  "priority_3": "일반 문의",
  "ad_delay_contingency_defined": true,
  "tech_delay_contingency_defined": true,
  "surge_contingency_defined": true,
  "prelaunch_checklist_defined": true,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "external_contact_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (capacity_calc facts 인용)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (capacity_calc facts 인용)
3. backlog_without_hiring = 160 − 80 = 80건 (capacity_calc facts 인용)
4. required_total_agents = 160 ÷ (5 × 4) = 8명 (capacity_calc facts 인용)
5. additional_agents = 8 − 4 = 4명 (capacity_calc facts 인용)
6. support_window_hours = 4, launch_hour = 10 (capacity_calc facts 인용)
7. immediate_refund_promised = false: 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작'과 광고 초안 '즉시 자동 환불'의 모순 (policy_schedule facts 인용)
8. hiring_approved = false, tech_deploy_approved = false, payment_verification_confirmed = false: 미제공·미승인 사항 (capacity_calc/policy_schedule facts 인용)
9. 마감 시각: 광고 목 15시, 상담 교육 목 17시, 랜딩 목 18시, 제품 사양 전달 목 14시 (policy_schedule facts 인용)
10. 우선순위: 결제/환불 오류 > 계정 접근 > 일반 문의 (capacity_calc facts 인용)
11. 비상 계획 3종(광고 지연, 기술 승인 지연, 문의 급증)과 출시 직전 확인표는 위 수치·정책 사실을 전제로 작성한 설계 제안이며, 실제 구현·테스트·외부 연락은 수행하지 않음
## launch-operations/customer_comms.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations/customer_comms.json)

확정 정책('상담 접수 후 영업일 2일 안에 처리 시작', 완료·PG 반영 미보장)을 기준선으로 고정하고, 고객 안내문·FAQ·상담 분류 기준·채널 약속 불일치 방지 문구를 설계 제안으로 작성했다. 실제 고객 연락·외부 시스템 변경은 수행하지 않았다.

[1] 기준선(선행 결과 고정)
- 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작'. 처리 완료 시각과 PG 반영 시각은 보장하지 않음.
- 금지 표현: '즉시', '자동 환불', '당일 환불 보장', '완료 시각 보장', 'PG 즉시 반영'.
- 허용 표현: '영업일 2일 내 처리 시작', '처리 시작 후 완료까지 추가 시간 소요 가능', 'PG 반영은 카드사/결제사에 따라 지연될 수 있음'.
- immediate_refund_promised=false: 광고 초안 '즉시 자동 환불'은 확정 정책과 모순이므로 사용 금지.

[2] 고객 안내문(설계 제안, 초안)
"안녕하세요. 환불 요청이 정상 접수되었습니다. 접수 확인: 요청 접수 시점에 접수 번호를 안내드립니다. 처리 시작 기준: 상담 접수 후 영업일 2일 안에 처리를 시작합니다. 완료 및 반영 안내: 처리 시작 이후 완료까지 추가 시간이 소요될 수 있으며, PG(결제대행) 반영 시점은 카드사/결제사 사정에 따라 지연될 수 있습니다. 완료 시각과 PG 반영 시각은 보장되지 않습니다. 문의 채널: 앱/웹 1:1 문의, 상담 채널을 통해 확인하실 수 있습니다. 문의가 많은 경우 안내가 지연될 수 있으며, 결제 오류·중복 청구 건은 우선 처리됩니다."
- 포함 요소: 접수 확인, 처리 시작 기준(영업일 2일), 완료·PG 반영 미보장, 문의 채널 안내.
- 배제 요소: '즉시', '자동 환불' 등 금지 표현 전부.

[3] FAQ(설계 제안)
Q1. 언제 환불되나요? → A. 상담 접수 후 영업일 2일 안에 처리를 시작합니다. 완료까지 추가 시간이 소요될 수 있습니다.
Q2. 즉시 환불되나요? → A. 아닙니다. '즉시 자동 환불'은 제공되지 않으며, 확정 정책은 영업일 2일 내 처리 시작입니다.
Q3. PG 반영은 언제인가요? → A. PG 반영 시점은 보장하지 않으며 카드사/결제사에 따라 지연될 수 있습니다.
Q4. 문의가 많으면 어떻게 되나요? → A. 대기열이 발생할 수 있으며, 결제 오류·중복 청구 건을 최우선으로 처리합니다. 일반 환불 접수는 접수 순서와 우선순위 규칙에 따라 처리됩니다.
Q5. 완료 시각을 알 수 있나요? → A. 완료 시각은 보장되지 않습니다. 처리 시작 후 진행 상황을 문의 채널에서 확인하실 수 있습니다.
Q6. 접수가 되었는지 어떻게 확인하나요? → A. 접수 시 안내된 접수 번호로 문의 채널에서 확인 가능합니다.

[4] 상담 분류 기준(설계 제안)
- P0(최우선): 결제 오류, 중복 청구, 이중 결제 → 즉시 상담원 배정, 결제 검증 결과 미제공 상태이므로 '확인 후 안내'로 응대, 완료 약속 금지.
- P1: 일반 환불 접수 → 접수 번호 부여, 영업일 2일 내 처리 시작 안내.
- P2: 정책 문의(환불 기간, PG 반영 등) → FAQ 기반 표준 답변, 금지 표현 사용 금지.
- P3: 불만/에스컬레이션 → 상담 운영 담당 에스컬레이션, 보상·환불 약속 금지.
- 라우팅 규칙: P0는 대기열 우회, P1~P3는 접수 순서+우선순위. 모든 응대는 기준문서 허용 표현만 인용.

[5] 광고/상담/랜딩 약속 불일치 방지 문구(설계 제안)
- 공통 원칙: 모든 채널 문구는 단일 기준 문서의 허용 표현만 인용, 금지 표현은 게시 전 제거.
- 광고 체크: '즉시', '자동 환불' 포함 여부 확인 → 포함 시 게시 금지, 기준문서 담당 승인 필요.
- 상담 체크: 스크립트에 '영업일 2일 내 처리 시작', '완료·PG 반영 미보장' 포함 여부 확인.
- 랜딩 체크: 환불 안내 섹션이 기준문서 허용 표현과 일치하는지 확인, '즉시/자동' 문구 제거 확인.
- 승인: 기준문서 담당 최종 승인, 제품팀 정책 원문 일치 확인. 미승인 문구 게시 금지.

[6] 미검증/미수행 명시
- 실제 고객 연락, 외부 시스템 변경, 광고 게시, 랜딩 수정은 수행하지 않음.
- 기술 배포 승인, 결제 검증 결과, 인원 충원 승인은 제공 자료에 없어 미확인.
- 위 안내문·FAQ·분류 기준은 모두 설계 제안이며, 실제 적용 전 기준문서 담당 승인 필요.

### Facts

```json
{
  "immediate_refund_promised": false,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "actual_customer_contact_performed": false,
  "actual_external_system_change_performed": false,
  "customer_notice_drafted": true,
  "faq_drafted": true,
  "support_classification_defined": true,
  "channel_consistency_guidance_defined": true,
  "forbidden_phrases_listed": true,
  "allowed_phrases_listed": true,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "hiring_approved": false,
  "ad_copy_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "launch_hour": 10,
  "expected_contacts": 160,
  "four_hour_capacity": 80,
  "backlog_without_hiring": 80,
  "required_total_agents": 8,
  "additional_agents": 4,
  "support_window_hours": 4,
  "agents_available": 4,
  "cases_per_agent_per_hour": 5,
  "invited_users": 800,
  "first_4h_inquiry_rate": 0.2
}
```

### Evidence

1. 확정 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장 (제공 자료)
2. 광고 초안 '즉시 자동 환불'은 확정 정책과 모순 → immediate_refund_promised=false
3. 고객 안내문에 접수 확인, 처리 시작 기준(영업일 2일), 완료·PG 반영 미보장, 문의 채널 안내 포함; '즉시/자동 환불' 표현 배제
4. FAQ 6문항: 언제 환불되나요 / 즉시 환불되나요 / PG 반영은 언제인가요 / 문의가 많으면 어떻게 되나요 / 완료 시각을 알 수 있나요 / 접수 확인 방법
5. 상담 분류: P0 결제 오류·중복 청구(최우선), P1 일반 환불 접수, P2 정책 문의, P3 불만/에스컬레이션; P0 대기열 우회, P1~P3 접수 순서+우선순위
6. 채널 불일치 방지: 광고/상담/랜딩 모두 단일 기준 문서 허용 표현만 인용, 금지 표현 게시 전 제거, 기준문서 담당 최종 승인
7. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
8. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
9. backlog_without_hiring = 160 − 80 = 80건
10. required_total_agents = 160 ÷ (5 × 4) = 8명
11. additional_agents = 8 − 4 = 4명
12. support_window_hours = 4, launch_hour = 10 (제공 자료 가정)
13. 마감 시각: 광고 목 15시, 상담 교육 목 17시, 랜딩 목 18시, 제품 사양 전달 목 14시 (제공 자료)
14. 미제공: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인 (제공 자료에 없음) → 모두 미확인으로 표시
15. 실제 고객 연락·외부 시스템 변경·광고 게시·랜딩 수정은 수행하지 않음(미검증), 모든 산출물은 설계 제안
## launch-operations/policy_schedule/capacity_plan.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations/policy_schedule/capacity_plan.json)

용량 계산 결과: 초대 800명의 20%인 160건이 첫 4시간에 문의된다. 상담원 4명이 시간당 5건씩 처리하면 4시간 용량은 80건이므로 80건이 미처리된다. 160건을 4시간 내 처리하려면 시간당 40건이 필요하고, 상담원 1인당 시간당 5건이므로 총 8명이 필요하다. 현재 4명이므로 추가 4명이 필요하다. 충원은 아직 승인되지 않았으므로 추가 인력을 확보했다고 가정하지 않는다. 충원 미승인 시 대안: (1) 대기열 운영 - 접수 순서와 SLA(영업일 2일 내 처리 시작)를 기준으로 큐를 운영하고, 4시간 내 처리 불가 건은 다음 영업일로 이월한다. (2) 우선순위 - 결제/환불 오류, 서비스 이용 불가, 보안·개인정보 이슈를 최우선(P1), 일반 문의를 P2, 단순 안내를 P3로 분류한다. (3) 고객 안내 - 접수 시 예상 응답 시간과 '영업일 2일 내 처리 시작' 정책을 안내하고, 자동 회신과 FAQ로 1차 응대를 분산한다. (4) 임시 완화 - 셀프서비스 FAQ, 챗봇/자동 응답, 기존 상담원 초과근무(승인 필요) 등을 검토하되 승인되지 않은 충원·환불·배포를 완료했다고 쓰지 않는다. immediate_refund_promised=false: 광고 초안의 '즉시 자동 환불'은 확정 정책('영업일 2일 내 처리 시작', 처리 완료·PG 반영 시간 미보장)과 다르므로 즉시 환불을 약속하지 않는다.

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
  "immediate_refund_promised": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
3. backlog_without_hiring = 160 - 80 = 80건
4. required_total_agents = 160 ÷ (5건/시간 × 4시간) = 160 ÷ 20 = 8명
5. additional_agents = 8 - 4 = 4명
6. support_window_hours = 4 (출시 후 첫 4시간 가정)
7. launch_hour = 10 (금요일 10시 출시)
8. 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료 시간과 PG 반영 시간은 보장하지 않음. 광고 초안의 '즉시 자동 환불'은 이 정책과 다르므로 immediate_refund_promised=false
9. 인원 충원은 아직 승인되지 않았으므로 추가 4명 확보를 완료로 간주하지 않고, 대기열·우선순위·고객 안내 대안만 제시함
## launch-operations/policy_schedule/contingency_checklist.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations/policy_schedule/contingency_checklist.json)

비상 계획과 출시 직전 확인표를 선행 결과(policy_schedule, capacity_plan)의 확정 사실에 근거해 작성했다. 실제 구현·테스트·외부 연락은 수행하지 않았고 모두 설계 제안이다.

[기준 확정 사실]
- 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작'. 처리 완료·PG 반영 시간 미보장. immediate_refund_promised=false.
- 마감: 광고 목 15:00, 상담 교육 목 17:00, 랜딩 목 18:00, 제품 사양 전달 목 14:00. 출시 금 10:00.
- 용량: expected_contacts=160, four_hour_capacity=80, backlog_without_hiring=80, required_total_agents=8, additional_agents=4, support_window_hours=4, launch_hour=10.
- 미확인: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인.

[시나리오 1] 광고 지연
- 트리거: 목 15:00까지 기준문서 v1.0 승인 문구가 광고에 반영되지 않음.
- 대응: (a) 기준문서 허용 표현만으로 임시 문구 게시: '영업일 2일 내 처리 시작', '처리 시작 후 완료까지 추가 시간 소요 가능', 'PG 반영은 카드사/결제사에 따라 지연될 수 있음'. (b) 금지 표현 '즉시', '자동 환불', '당일 환불 보장', 'PG 즉시 반영' 삭제 확인. (c) 광고 담당이 기준문서 담당에게 승인 요청, 미승인 문구는 게시 금지. (d) 15:00 초과 시 광고는 허용 표현 최소 세트로만 게시하고 나머지 문구는 보류.
- 담당/기한: 광고 담당, 목 15:00(초과 시 즉시 에스컬레이션). 선행: 기준문서 v1.0.

[시나리오 2] 기술 배포 승인 지연
- 트리거: 금 10:00까지 기술 배포 승인 또는 결제 검증 결과 미확보.
- 대응: (a) 출시 총괄이 출시 연기 또는 기능 제한 출시(환불 자동화 등 미검증 기능 비활성) 중 택1. (b) 고객 안내문에 미확인 사항 명시: '결제/환불 처리 일정은 검증 후 확정'. (c) 승인·검증 확보 전 '배포 완료' 표기 금지. (d) 연기 시 광고·랜딩·상담 스크립트에 일정 변경 반영.
- 담당/기한: 출시 총괄, 금 10:00 이전 결정. 선행: 기술 배포 승인, 결제 검증(둘 다 미제공).

[시나리오 3] 문의 급증
- 트리거: 첫 4시간 문의가 160건(가정) 또는 그 이상.
- 대응: (a) 우선순위 큐: P1 결제/환불 오류·중복 청구·보안/개인정보, P2 일반 문의, P3 단순 안내. (b) 예상 대기 안내: 접수 시 '영업일 2일 내 처리 시작'과 예상 응답 시간 고지. (c) FAQ·자동 회신으로 셀프서비스 유도, 1차 응대 분산. (d) 4시간 내 처리 불가 건은 다음 영업일로 이월(backlog_without_hiring=80). (e) 충원(additional_agents=4)은 승인 시에만 실행, 미승인 상태에서는 대기열·우선순위·안내로만 대응.
- 담당/기한: 상담 운영 담당, 출시일 실시간. 선행: 기준문서 v1.0, 상담 교육(목 17:00).

[출시 직전 확인표]
1. 기준문서 v1.0 승인 여부 — 미확인(승인 필요).
2. 광고·상담·랜딩 3채널 금지 표현 제거 여부 — 확인 필요.
3. 기술 배포 승인 확보 여부 — 미제공/미확인.
4. 결제 검증 결과 확보 여부 — 미제공/미확인.
5. 상담 인력 충원 승인 상태 — 미승인(추가 4명 미확보).
6. 고객 안내문·FAQ·상담 분류 기준 반영 여부 — 확인 필요.
7. 비상 계획(광고 지연/기술 지연/문의 급증) 담당자 지정 여부 — 확인 필요.

미제공 승인·검증(기술 배포 승인, 결제 검증, 인원 충원)은 모두 미확인으로 표시했고 완료로 쓰지 않았다. 실제 구현/테스트/외부 연락은 수행하지 않았다.

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
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "hiring_approved": false,
  "contingency_ad_delay_defined": true,
  "contingency_tech_delay_defined": true,
  "contingency_surge_defined": true,
  "prelaunch_checklist_defined": true,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "external_contact_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 − 4 = 4명
6. support_window_hours = 4, launch_hour = 10 (제공 자료 가정)
7. 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 완료·PG 반영 미보장 → 광고 초안 '즉시 자동 환불'과 모순, immediate_refund_promised=false
8. 마감 시각: 광고 목 15:00, 상담 교육 목 17:00, 랜딩 목 18:00, 제품 사양 전달 목 14:00 (제공 자료)
9. 미제공: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인 (제공 자료에 없음) → 모두 미확인으로 표시
10. 실제 구현·테스트·외부 연락은 수행하지 않음(미검증), 모든 산출물은 설계 제안
## launch-operations/policy_schedule/customer_comms.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations/policy_schedule/customer_comms.json)

확정 정책('상담 접수 후 영업일 2일 안에 처리 시작', 완료·PG 반영 미보장)을 기준선으로 고정하고, 고객 안내문·FAQ·상담 분류 기준을 작성했다. 모든 문구는 기준문서 v1.0의 허용 표현만 인용하며 '즉시', '자동 환불', '당일 보장' 등 금지 표현을 배제했다. 실제 구현·테스트·외부 연락은 수행하지 않았고 모두 설계 제안이다.

[1] 기준선 고정(기준문서 v1.0)
- 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작'. 처리 완료 시간과 PG 반영 시간은 보장하지 않음.
- 허용 표현: '영업일 2일 내 처리 시작', '처리 시작 후 완료까지 추가 시간 소요 가능', 'PG 반영은 카드사/결제사에 따라 지연될 수 있음'.
- 금지 표현: '즉시', '자동 환불', '당일 환불 보장', '완료 시각 보장', 'PG 즉시 반영'.
- 단일 출처 원칙: 광고·상담 스크립트·랜딩·FAQ·안내문의 모든 문구는 기준문서 v1.0 허용 표현만 인용. 개정은 기준문서 담당 승인 후에만 유효.

[2] 고객 안내문(설계 제안, 문자열)
"[환불 처리 안내]\n안녕하세요, 문의해 주셔서 감사합니다.\n- 접수 확인: 문의가 정상 접수되었습니다.\n- 처리 시작: 상담 접수 후 영업일 2일 안에 처리를 시작합니다.\n- 완료·PG 반영: 처리 시작 후 완료까지 추가 시간이 소요될 수 있으며, PG(결제대행사) 반영은 카드사/결제사에 따라 지연될 수 있습니다. 완료 시각과 PG 반영 시각은 보장되지 않습니다.\n- 문의 채널: 앱/웹 1:1 문의, 고객센터 전화, FAQ를 이용해 주세요.\n- 문의 폭주 시: 접수 순서와 우선순위에 따라 안내가 지연될 수 있습니다.\n※ 본 안내는 확정 정책(영업일 2일 내 처리 시작, 완료·PG 반영 미보장)에 따르며, '즉시', '자동 환불', '당일 보장'은 제공되지 않습니다."

[3] FAQ(설계 제안, 질문-답변 쌍)
Q1. 언제 환불되나요? → A. 상담 접수 후 영업일 2일 안에 처리를 시작합니다. 완료까지 추가 시간이 소요될 수 있습니다.
Q2. 즉시 환불되나요? → A. 아니요. '즉시 자동 환불'은 제공되지 않습니다. 영업일 2일 내 처리 시작 기준입니다.
Q3. PG 반영은 언제 되나요? → A. PG 반영 시점은 보장하지 않으며 카드사/결제사에 따라 지연될 수 있습니다.
Q4. 문의가 폭주하면 어떻게 되나요? → A. 접수 순서와 우선순위(결제 오류·중복 청구 우선)에 따라 안내가 지연될 수 있습니다.
Q5. 상담 인력이 충원되나요? → A. 충원은 아직 승인되지 않았습니다. 승인 전까지는 현재 인력 기준으로 운영되며 대기 안내가 제공될 수 있습니다.
Q6. 완료 시각을 알 수 있나요? → A. 완료 시각은 보장되지 않습니다. 처리 시작 후 진행 상태를 안내해 드립니다.

[4] 상담 분류 기준(설계 제안)
- C1 결제 오류·중복 청구(최우선): 증빙(결제ID·시각·금액) 수집, 즉시 에스컬레이션, 처리 시작 기한 안내. 스크립트 요지: "중복 청구 가능성을 확인 중이며, 접수 후 영업일 2일 내 처리를 시작합니다."
- C2 환불 진행 상태: 접수번호 조회, 처리 시작 여부·단계 안내, 완료·PG 미보장 고지. 스크립트 요지: "현재 단계는 OO이며, 완료·PG 반영 시점은 보장되지 않습니다."
- C3 일반 문의: FAQ 안내, 셀프서비스 유도.
- C4 정책 불일치 제보: 광고/랜딩 문구와 확정 정책 불일치 접수, 기준문서 담당에 전달, 금지 표현 발견 시 즉시 보고.
- 우선순위: C1 > C2 > C4 > C3. 모든 응대는 기준문서 v1.0 허용 표현만 사용.

[5] 단일 기준 문서 연결
안내문·FAQ·분류 기준 상단에 "본 문구는 환불 처리 기준 문서 v1.0의 허용 표현만 인용하며, 개정은 기준문서 담당 승인 후에만 유효함"을 표기. 광고(목 15시)·상담 교육(목 17시)·랜딩(목 18시) 반영 시 동일 기준문서를 참조.

[6] 자체 점검
- 금지 표현('즉시', '자동 환불', '당일 보장') 미사용 확인.
- 확정/미확인 사항: 기술 배포 승인·결제 검증·충원 승인은 미제공이므로 완료로 표기하지 않음.
- immediate_refund_promised=false 유지.

[7] 용량 계산(참고, 가정 기반)
expected_contacts = 800 × 0.20 = 160건. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건. backlog_without_hiring = 160 − 80 = 80건. required_total_agents = 160 ÷ (5 × 4) = 8명. additional_agents = 8 − 4 = 4명. support_window_hours = 4, launch_hour = 10. 충원 미승인 시 대안: 우선순위 큐(C1 우선), 예상 대기 안내, FAQ 셀프서비스 유도.

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
  "hiring_approved": false,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "invited_users": 800,
  "first_4h_inquiry_rate": 0.2,
  "agents_available": 4,
  "cases_per_agent_per_hour": 5,
  "ad_copy_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "forbidden_phrase_immediate_used": false,
  "forbidden_phrase_auto_refund_used": false,
  "single_source_doc_linked": true,
  "faq_item_count": 6,
  "category_count": 4
}
```

### Evidence

1. 확정 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작', 완료·PG 반영 미보장 (제공 자료)
2. 광고 초안 '즉시 자동 환불'은 확정 정책과 모순 → immediate_refund_promised=false
3. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
4. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
5. backlog_without_hiring = 160 − 80 = 80건
6. required_total_agents = 160 ÷ (5 × 4) = 8명
7. additional_agents = 8 − 4 = 4명
8. 마감 시각: 광고 목 15시, 상담 교육 목 17시, 랜딩 목 18시, 제품 사양 전달 목 14시 (제공 자료)
9. 미제공: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인 (제공 자료에 없음)
10. 안내문·FAQ·분류 기준은 기준문서 v1.0 허용 표현만 인용하도록 작성, 금지 표현('즉시','자동 환불','당일 보장') 미사용
11. 실제 구현·테스트·외부 연락은 수행하지 않음(미검증), 모든 산출물은 설계 제안
## launch-operations/policy_schedule/policy_schedule.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations/policy_schedule/policy_schedule.json)

확정 정책과 광고 초안의 모순을 식별하고, 단일 기준 문서·변경 전달/승인 흐름·운영 일정·확정/미확인 사항표를 설계했다. 실제 구현·테스트·외부 연락은 수행하지 않았고 모두 설계 제안이다.

[1] 모순 식별
- 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작'. 처리 완료 시간과 PG 반영 시간은 보장하지 않음.
- 광고 초안: '즉시 자동 환불'.
- 판정: immediate_refund_promised=false. '즉시'는 접수 즉시 처리를, '자동 환불'은 완료·PG 반영 보장을 함의하므로 확정 정책(2영업일 내 '시작', 완료·PG 미보장)과 직접 모순. 광고 문구는 수정/보류 대상이며, 확정 정책을 광고에 맞춰 바꾸는 것은 금지(제품 정책 신규 결정 금지).

[2] 단일 기준 문서(설계 제안)
- 문서명: 환불 처리 기준 문서 v1.0. 구성: (a) 정책 원문(영업일 2일 내 처리 시작, 완료·PG 반영 미보장), (b) 허용 표현: '영업일 2일 내 처리 시작', '처리 시작 후 완료까지 추가 시간 소요 가능', 'PG 반영은 카드사/결제사에 따라 지연될 수 있음', (c) 금지 표현: '즉시', '자동 환불', '당일 환불 보장', '완료 시각 보장', 'PG 즉시 반영', (d) 적용 채널: 광고·상담 스크립트·랜딩·FAQ.
- 단일 출처 원칙: 모든 채널 문구는 이 문서의 허용 표현만 인용. 문서 개정은 기준문서 담당 승인 후에만 유효.

[3] 변경 전달·승인 흐름(설계 제안)
- 흐름: 제품팀(목 14:00 확정 사양·알려진 제한 전달) → 기준문서 담당(14:00~14:30 정책 원문을 허용/금지 표현으로 반영, v1.0 확정) → 광고(15:00 마감 전 반영), 상담 교육(17:00 전 스크립트·분류 기준 반영), 랜딩(18:00 전 반영).
- 승인자: 기준문서 담당(문구 적합성 최종 승인), 제품팀(정책 원문 일치 확인). 미승인 문구는 게시 금지.
- 기한: 광고 15:00, 상담 교육 17:00, 랜딩 18:00. 각 채널 담당은 반영 후 기준문서 담당에게 승인 요청.

[4] 운영 일정(담당·기한·선행조건)
- 목 14:00 제품 사양·제한 전달 | 담당: 제품팀 | 선행: 없음.
- 목 14:00~14:30 기준문서 v1.0 확정 | 담당: 기준문서 담당 | 선행: 제품팀 전달.
- 목 15:00 광고 문구 마감·수정 반영 | 담당: 광고 담당 | 선행: 기준문서 v1.0 승인.
- 목 17:00 상담 교육(스크립트·분류 기준) | 담당: 상담 운영 담당 | 선행: 기준문서 v1.0.
- 목 18:00 랜딩 수정 마감 | 담당: 랜딩 담당 | 선행: 기준문서 v1.0.
- 금 10:00 출시 | 담당: 출시 총괄 | 선행: 기술 배포 승인(미제공), 결제 검증(미제공), 위 3채널 반영 완료.

[5] 확정/미확인 사항표(텍스트)
- 확정: 정책 원문(2영업일 내 시작, 완료·PG 미보장), 채널 마감 시각(15/17/18시), 제품팀 전달 시각(14시), 초대 800명, 첫 4시간 문의율 20%, 상담원 4명·시간당 5건.
- 미확인: 기술 배포 승인 여부, 결제 검증 결과, 인원 충원 승인 여부, 실제 문의율(가정치).
- 금지: 미승인 충원·환불·배포를 완료로 표기하지 않음. 제품 정책 신규 결정 없음.

[6] 용량 계산(참고, 가정 기반)
- expected_contacts = 800 × 0.20 = 160건.
- four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건.
- backlog_without_hiring = 160 − 80 = 80건.
- required_total_agents = 160 ÷ (5 × 4) = 8명.
- additional_agents = 8 − 4 = 4명.
- support_window_hours = 4, launch_hour = 10.
- 충원 미승인 시 대안(설계 제안): 대기열 우선순위(결제 오류·중복 청구 우선), 예상 대기 안내 문구, FAQ로 셀프서비스 유도, 추가 인원은 승인 전제로만 계획.

[7] 비상 계획(설계 제안)
- 광고 지연: 기준문서 허용 표현만으로 임시 문구 게시, '즉시 자동 환불' 삭제 확인.
- 기술 승인 지연: 출시 시각 연기 또는 기능 제한 출시, 고객 안내문에 미확인 사항 명시.
- 문의 급증: 우선순위 큐·안내문·FAQ 강화, 충원은 승인 시에만 실행.

[8] 출시 직전 확인표(설계 제안)
- 기준문서 v1.0 승인 여부, 3채널 금지 표현 제거 여부, 기술 배포 승인·결제 검증 확보 여부, 상담 인력 계획(승인 상태) 확인.

실제 구현/테스트/외부 연락은 수행하지 않았으며 위는 모두 설계 제안이다.

### Facts

```json
{
  "immediate_refund_promised": false,
  "expected_contacts": 160,
  "four_hour_capacity": 80,
  "backlog_without_hiring": 80,
  "required_total_agents": 8,
  "additional_agents": 4,
  "support_window_hours": 4,
  "launch_hour": 10,
  "ad_copy_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "invited_users": 800,
  "first_4h_inquiry_rate": 0.2,
  "agents_available": 4,
  "cases_per_agent_per_hour": 5,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "hiring_approved": false,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_guaranteed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (상담원 4명, 시간당 5건)
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 − 4 = 4명
6. 모순: 확정 정책 '영업일 2일 내 처리 시작, 완료·PG 반영 미보장' vs 광고 초안 '즉시 자동 환불' → immediate_refund_promised=false
7. 마감 시각: 광고 목 15시, 상담 교육 목 17시, 랜딩 목 18시, 제품 사양 전달 목 14시 (제공 자료)
8. 미제공: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인 (제공 자료에 없음)
9. 실제 구현·테스트·외부 연락은 수행하지 않음(미검증), 모든 산출물은 설계 제안
## launch-operations/policy_schedule.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous/artifacts/launch-operations/policy_schedule.json)

확정 정책('상담 접수 후 영업일 2일 안에 처리 시작', 완료·PG 반영 미보장)과 광고 초안 '즉시 자동 환불'의 모순을 식별하고, 단일 기준 문서·변경 전달/승인 흐름·담당·기한·선행조건이 연결된 운영 일정·확정/미확인 사항표를 설계했다. 실제 구현·테스트·외부 연락은 수행하지 않았고 모두 설계 제안이다.

[1] 모순 식별
- 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작'. 처리 완료 시간과 PG 반영 시간은 보장하지 않음.
- 광고 초안: '즉시 자동 환불'.
- 판정: immediate_refund_promised=false. '즉시'는 접수 즉시 처리를, '자동 환불'은 완료·PG 반영 보장을 함의하므로 확정 정책(2영업일 내 '시작', 완료·PG 미보장)과 직접 모순. 광고 문구는 수정/보류 대상이며, 확정 정책을 광고에 맞춰 바꾸는 것은 금지(제품 정책 신규 결정 금지).

[2] 단일 기준 문서(설계 제안)
- 문서명: 환불 처리 기준 문서 v1.0. 구성: (a) 정책 원문(영업일 2일 내 처리 시작, 완료·PG 반영 미보장), (b) 허용 표현: '영업일 2일 내 처리 시작', '처리 시작 후 완료까지 추가 시간 소요 가능', 'PG 반영은 카드사/결제사에 따라 지연될 수 있음', (c) 금지 표현: '즉시', '자동 환불', '당일 환불 보장', '완료 시각 보장', 'PG 즉시 반영', (d) 적용 채널: 광고·상담 스크립트·랜딩·FAQ.
- 단일 출처 원칙: 모든 채널 문구는 이 문서의 허용 표현만 인용. 문서 개정은 기준문서 담당 승인 후에만 유효.

[3] 변경 전달·승인 흐름(설계 제안)
- 흐름: 제품팀(목 14:00 확정 사양·알려진 제한 전달) → 기준문서 담당(14:00~14:30 정책 원문을 허용/금지 표현으로 반영, v1.0 확정) → 광고(15:00 마감 전 반영), 상담 교육(17:00 전 스크립트·분류 기준 반영), 랜딩(18:00 전 반영).
- 승인자: 기준문서 담당(문구 적합성 최종 승인), 제품팀(정책 원문 일치 확인). 미승인 문구는 게시 금지.
- 기한: 광고 15:00, 상담 교육 17:00, 랜딩 18:00. 각 채널 담당은 반영 후 기준문서 담당에게 승인 요청.

[4] 운영 일정(담당·기한·선행조건)
- 목 14:00 제품 사양·제한 전달 | 담당: 제품팀 | 선행: 없음.
- 목 14:00~14:30 기준문서 v1.0 확정 | 담당: 기준문서 담당 | 선행: 제품팀 전달.
- 목 15:00 광고 문구 마감·수정 반영 | 담당: 광고 담당 | 선행: 기준문서 v1.0 승인.
- 목 17:00 상담 교육(스크립트·분류 기준) | 담당: 상담 운영 담당 | 선행: 기준문서 v1.0.
- 목 18:00 랜딩 수정 마감 | 담당: 랜딩 담당 | 선행: 기준문서 v1.0.
- 금 10:00 출시 | 담당: 출시 총괄 | 선행: 기술 배포 승인(미제공), 결제 검증(미제공), 위 3채널 반영 완료.

[5] 확정/미확인 사항표(텍스트)
- 확정: 정책 원문(2영업일 내 시작, 완료·PG 미보장), 채널 마감 시각(15/17/18시), 제품팀 전달 시각(14시), 초대 800명, 첫 4시간 문의율 20%, 상담원 4명·시간당 5건.
- 미확인: 기술 배포 승인 여부, 결제 검증 결과, 인원 충원 승인 여부, 실제 문의율(가정치).
- 금지: 미승인 충원·환불·배포를 완료로 표기하지 않음. 제품 정책 신규 결정 없음.

[6] 용량 계산(참고, 가정 기반)
- expected_contacts = 800 × 0.20 = 160건.
- four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건.
- backlog_without_hiring = 160 − 80 = 80건.
- required_total_agents = 160 ÷ (5 × 4) = 8명.
- additional_agents = 8 − 4 = 4명.
- support_window_hours = 4, launch_hour = 10.
- 충원 미승인 시 대안(설계 제안): 대기열 우선순위(결제 오류·중복 청구 우선), 예상 대기 안내 문구, FAQ로 셀프서비스 유도, 추가 인원은 승인 전제로만 계획.

[7] 비상 계획(설계 제안)
- 광고 지연: 기준문서 허용 표현만으로 임시 문구 게시, '즉시 자동 환불' 삭제 확인.
- 기술 승인 지연: 출시 시각 연기 또는 기능 제한 출시, 고객 안내문에 미확인 사항 명시.
- 문의 급증: 우선순위 큐·안내문·FAQ 강화, 충원은 승인 시에만 실행.

[8] 출시 직전 확인표(설계 제안)
- 기준문서 v1.0 승인 여부, 3채널 금지 표현 제거 여부, 기술 배포 승인·결제 검증 확보 여부, 상담 인력 계획(승인 상태) 확인.

실제 구현/테스트/외부 연락은 수행하지 않았으며 위는 모두 설계 제안이다.

### Facts

```json
{
  "immediate_refund_promised": false,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "ad_copy_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "launch_hour": 10,
  "invited_users": 800,
  "first_4h_inquiry_rate": 0.2,
  "cases_per_agent_per_hour": 5,
  "agents_available": 4,
  "expected_contacts": 160,
  "four_hour_capacity": 80,
  "backlog_without_hiring": 80,
  "required_total_agents": 8,
  "additional_agents": 4,
  "support_window_hours": 4,
  "tech_deploy_approval_provided": false,
  "payment_verification_provided": false,
  "hiring_approved": false,
  "single_source_doc_defined": true,
  "approval_flow_defined": true,
  "schedule_defined": true,
  "confirmed_unconfirmed_table_defined": true,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "external_contact_performed": false
}
```

### Evidence

1. 확정 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장 (제공 자료)
2. 광고 초안 '즉시 자동 환불'은 확정 정책과 모순 → immediate_refund_promised=false
3. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
4. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
5. backlog_without_hiring = 160 − 80 = 80건
6. required_total_agents = 160 ÷ (5 × 4) = 8명
7. additional_agents = 8 − 4 = 4명
8. support_window_hours = 4, launch_hour = 10 (제공 자료 가정)
9. 마감 시각: 광고 목 15시, 상담 교육 목 17시, 랜딩 목 18시, 제품 사양 전달 목 14시 (제공 자료)
10. 미제공: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인 (제공 자료에 없음) → 모두 미확인으로 표시
11. 실제 구현·테스트·외부 연락은 수행하지 않음(미검증), 모든 산출물은 설계 제안