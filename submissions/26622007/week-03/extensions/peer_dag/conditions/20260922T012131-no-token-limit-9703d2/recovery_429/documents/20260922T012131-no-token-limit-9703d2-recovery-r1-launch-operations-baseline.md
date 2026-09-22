# launch-operations / baseline — 429 복구

상태: succeeded. facts: 8/8.
모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.

## launch-operations.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline/artifacts/launch-operations.json)

capacity_and_policy와 customer_comms 산출물을 통합해 launch_operations_report를 완성했다. 두 산출물의 8개 숫자 키와 immediate_refund_promised 불리언을 대조한 결과 전부 일치했다: expected_contacts=160, four_hour_capacity=80, backlog_without_hiring=80, required_total_agents=8, additional_agents=4, support_window_hours=4, launch_hour=10, immediate_refund_promised=false. 모순·미확인 사항은 단일 목록으로 통합했다: (1) 정책-광고 불일치 — 확정 정책 '영업일 2일 내 처리 시작(완료·PG 반영 미보장)' vs 광고 초안 '즉시 자동 환불' → 광고 초안을 금지 표현으로 분류, SSOT 허용 문구만 사용. (2) 일정 선행조건 충돌 — 제품팀 14:00 사양 전달이 지연되면 광고 15:00 문구 마감이 불가하므로 대체 문구('영업일 2일 내 처리 시작')만 사용하는 폴백 필요. (3) 미확인 사항 — 기술 배포 승인, 결제 검증(PG) 결과, 인력 충원 승인 모두 미제공 → '보류/확인 후 안내'로만 표기, 완료 표현 금지. (4) 용량-인력 충돌 — 160건 문의 대비 80건만 처리 가능, 추가 4명 미승인 상태이므로 대기열·우선순위(P1 결제 오류/이용 불가, P2 환불/계정, P3 일반) 대안 적용.

[launch_operations_report 통합 구조]
1) 운영 일정표(담당·기한·선행조건): 제품팀 14:00 사양·제한 전달(선행 없음) → 광고 15:00 문구 마감(선행: 제품팀 14:00) → 상담 17:00 교육(선행: 광고 15:00) → 랜딩 18:00 수정 마감(선행: 상담 17:00) → 금요일 10:00 출시(선행: 1~4 완료 및 확인표 통과).
2) 확정/미확인 사항표: 확정 — 영업일 2일 내 처리 시작, 완료·PG 반영 미보장, P1/P2/P3 분류, 미확정 사안 '확인 후 안내'. 미확인 — 충원 미승인, 배포 승인 미제공, 결제 검증 미제공, 환불 완료 미수행, '즉시 자동 환불' 미확정(금지 표현).
3) 고객 안내문·FAQ·상담 분류 기준: 안내문 핵심 문구 '접수 후 영업일 2일 안에 처리를 시작합니다. 완료 및 PG 반영 시점은 보장하지 않습니다.' FAQ 6문항(Q1 환불 시점, Q2 '즉시 자동 환불' 문구 정정, Q3 지연 사유, Q4 PG 반영, Q5 상담원 부족, Q6 급한 건). 분류 P1/P2/P3 + 미확정 사안 '확인 후 안내'.
4) SSOT 구조 6섹션(확정 정책 원문, 허용 문구, 금지 문구, 채널별 적용, 미확인 사항, 버전·이력)과 변경 전달·승인 흐름(변경 요청 → SSOT 개정 → 제품 리드 승인 → 채널 반영 → 회신, 구두·단독 변경 금지).
5) 용량 계산: 800×20%=160건, 4명×5건/시×4시간=80건, 미처리 80건, 총 8명 필요, 추가 4명.
6) 인력 대안: 대기열(접수 순+긴급도), 우선순위 P1/P2/P3, 고객 안내는 '영업일 2일 내 처리 시작'만 약속.
7) 비상 계획: (a) 광고 지연 → SSOT 허용 문구만 사용, 제품 리드→운영 리드 에스컬레이션. (b) 기술 승인 지연 → '출시 준비 중, 확정 시 안내' 보류 문구만 사용, '배포 완료' 금지. (c) 문의 급증 → P1 우선 배정, P2/P3 순차, 지연 사유 안내, 충원은 '확인 후 안내'.
8) 출시 직전 확인표 5항목: SSOT 버전 확인, 채널별 반영 회신, 보류 문구 사용 여부, 미확정 사안 잔여 여부, 구두·단독 변경 금지 준수.

[설계 vs 실제 수행 구분]
본 산출물은 설계 문서다. 실제 구현·테스트·고객 연락·외부 시스템 변경·광고/랜딩/상담 시스템 반영은 수행하지 않았다(미검증). 상담 처리율, PG 반영 시간, 배포 승인 여부는 제공 자료가 없어 검증되지 않았다. 제품 정책을 새로 결정하지 않았고, 승인되지 않은 충원·환불·배포를 완료로 표기하지 않았다.

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
  "policy_conflict_present": true,
  "hiring_approval_provided": false,
  "deployment_approval_provided": false,
  "payment_verification_provided": false,
  "product_team_deadline_hour": 14,
  "ad_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "priority_levels_defined": 3,
  "ssot_sections": 6,
  "checklist_items_count": 5,
  "schedule_steps_count": 5,
  "numeric_keys_matched": true,
  "boolean_key_matched": true,
  "contradictions_listed": true,
  "unconfirmed_items_listed": true,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "actual_customer_contact_performed": false,
  "external_system_change_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20%)
2. four_hour_capacity = 4명 × 5건/시 × 4시간 = 80건
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 − 4 = 4명
6. support_window_hours = 4, launch_hour = 10 (금요일 10시 출시, 첫 4시간)
7. capacity_and_policy.facts와 customer_comms.facts의 8개 숫자 키 대조 결과 전부 일치(160/80/80/8/4/4/10)
8. immediate_refund_promised: 두 산출물 모두 false — 확정 정책 '영업일 2일 내 처리 시작'과 광고 초안 '즉시 자동 환불' 불일치
9. 모순 통합: 정책-광고 불일치, 일정 선행조건 충돌(제품팀 14:00 지연 시 광고 15:00 마감 불가), 용량-인력 충돌(160건 vs 80건 처리)
10. 미확인 통합: 기술 배포 승인·결제 검증 결과·인력 충원 승인 모두 제공 자료에 없음 → '보류/확인 후 안내'로만 표기
11. 일정 체인: 제품팀 14:00 → 광고 15:00 → 상담 17:00 → 랜딩 18:00 → 금요일 10:00 출시
12. SSOT 6섹션(확정 정책 원문, 허용 문구, 금지 문구, 채널별 적용, 미확인 사항, 버전·이력) 및 변경 전달·승인 흐름 설계
13. 출시 직전 확인표 5항목(SSOT 버전, 채널별 반영, 보류 문구 사용, 미확정 사안 잔여, 구두 변경 금지)
14. 비상 계획 3종(광고 지연/기술 승인 지연/문의 급증) 통합
15. 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음(제공 자료만 사용, 미검증)
## launch-operations/capacity_and_policy.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline/artifacts/launch-operations/capacity_and_policy.json)

제공된 수치로 첫 4시간 용량·인력 계산을 완료하고, 확정 정책과 광고 초안의 모순 및 미확인 사항을 정리했다. 초대 800명 × 20% = 160건 문의 예상, 상담원 4명 × 5건/시 × 4시간 = 80건 처리 가능, 미처리 80건 발생. 전량 처리를 위해서는 총 8명(160÷20) 필요, 추가 4명 충원 필요. 충원 미승인 상태이므로 대기열·우선순위·고객 안내 대안과 비상 계획 초안을 제시한다. 확정 정책은 '영업일 2일 내 처리 시작'이며 처리 완료·PG 반영 시간은 미보장이므로 광고 초안의 '즉시 자동 환불'은 정책 위반 소지가 있어 immediate_refund_promised=false로 표기한다. 기술 배포 승인, 결제 검증 결과, 인원 충원 승인은 모두 미제공 상태다. 실제 구현·테스트·외부 연락은 수행하지 않았다.

[용량 계산]
- expected_contacts = 800 × 0.20 = 160건
- four_hour_capacity = 4명 × 5건/시 × 4시간 = 80건
- backlog_without_hiring = 160 - 80 = 80건
- required_total_agents = 160 ÷ (5 × 4) = 8명
- additional_agents = 8 - 4 = 4명
- support_window_hours = 4, launch_hour = 10

[정책-광고 모순]
- 확정 정책: 접수 후 영업일 2일 내 '처리 시작'. 완료 시점·PG 반영 시점 미보장.
- 광고 초안: '즉시 자동 환불' → 정책과 불일치. 광고 문구 수정 또는 정책 재승인 필요. 담당자 임의 결정 금지.
- 단일 기준 문서(SSOT) 필요: 광고/상담/랜딩이 동일 문구 사용. 변경 시 제품팀→운영 리드→광고/상담/랜딩 순 전달·승인 흐름.

[미확인 사항]
- 기술 배포 승인: 미제공 (목요일 14시 제품팀 사양 전달 예정)
- 결제 검증(PG) 결과: 미제공
- 인원 충원 승인: 미승인

[충원 불가 시 대안]
- 대기열: 접수 순 + 우선순위(결제 오류 > 환불 문의 > 일반 문의)
- 우선순위: 결제 실패/중복결제 최우선, 환불 정책 문의 차순위, 일반 FAQ 자동응답
- 고객 안내: '영업일 2일 내 처리 시작' 명시, 완료·PG 반영 시점 미보장 고지, 자동응답·FAQ로 1차 필터
- 비상 계획: (a) 광고 지연 → 랜딩·상담 교육 일정 순연, (b) 기술 승인 지연 → 출시 연기 또는 제한 기능 출시, (c) 문의 급증 → 임시 인력·FAQ 강화·SLA 재공지

[미검증]
- 실제 상담 처리율, PG 반영 시간, 배포 승인 여부는 검증되지 않음(제공 자료 없음).

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
  "policy_conflict_present": true,
  "deployment_approval_provided": false,
  "payment_verification_provided": false,
  "hiring_approval_provided": false,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "external_contact_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20%)
2. four_hour_capacity = 4명 × 5건/시 × 4시간 = 80건 (상담원 4명, 시간당 5건)
3. backlog_without_hiring = 160 - 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 - 4 = 4명
6. support_window_hours = 4, launch_hour = 10 (금요일 10시 출시, 첫 4시간)
7. 확정 정책 '영업일 2일 내 처리 시작' vs 광고 초안 '즉시 자동 환불' → immediate_refund_promised=false, 정책-광고 모순 존재
8. 기술 배포 승인·결제 검증·충원 승인 모두 제공 자료에 없음 → 미확인으로 표기
9. 실제 구현·테스트·외부 연락은 수행하지 않음(제공 자료만 사용)
## launch-operations/customer_comms/capacity_analysis.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline/artifacts/launch-operations/customer_comms/capacity_analysis.json)

첫 4시간 용량·인력 계산과 충원 불가 시 운영 대안을 정리했다. 계산: 초대 800명 × 20% = 160건 문의. 상담원 4명 × 시간당 5건 × 4시간 = 80건 처리 가능. 미처리 160 − 80 = 80건. 전량 처리를 위해 160 ÷ (5 × 4) = 8명 필요, 추가 4명. 기준 시점은 금요일 10시 출시, 지원 창은 첫 4시간(10:00–14:00). 확정 정책은 '접수 후 영업일 2일 내 처리 시작'이며 완료·PG 반영 시점은 보장되지 않으므로 '즉시 자동 환불' 약속은 하지 않는다(immediate_refund_promised=false). 충원 미승인 상태이므로 대안으로 (1) 대기열 운영: 접수 순번·예상 응답 구간 안내, (2) 우선순위: 결제 오류·중복 청구·접근 불가 등 긴급도 기준 분류, (3) 고객 안내문: '영업일 2일 내 처리 시작'만 약속하고 완료·환불 시점은 비보장 명시, (4) 상담 분류 기준 초안: 결제오류/중복청구/환불문의/기능문의/기타. 미확인 사항: 추가 인력 충원 승인 여부, 기술 배포 승인, 결제 검증 결과는 제공되지 않아 미검증으로 표기한다. 실제 고객 연락·외부 시스템 변경은 수행하지 않았다.

### Facts

```json
{
  "expected_contacts": 160,
  "four_hour_capacity": 80,
  "backlog_without_hiring": 80,
  "required_total_agents": 8,
  "additional_agents": 4,
  "support_window_hours": 4,
  "launch_hour": "금요일 10시",
  "immediate_refund_promised": false,
  "assumption_contacts_per_agent": 1,
  "assumption_agents_available": 4,
  "assumption_cases_per_agent_hour": 5,
  "policy_processing_start": "접수 후 영업일 2일 내 처리 시작",
  "policy_completion_guarantee": false,
  "policy_pg_reflection_guarantee": false,
  "staffing_approved": false,
  "deploy_approval_provided": false,
  "payment_verification_provided": false,
  "queue_plan_defined": true,
  "priority_criteria_defined": true,
  "customer_notice_draft_defined": true,
  "ticket_classification_draft_defined": true
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (상담원 4명, 시간당 5건 가정)
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 − 4 = 4명
6. support_window_hours = 4, launch_hour = 금요일 10시 (출시 후 첫 4시간 기준)
7. immediate_refund_promised = false: 확정 정책은 '접수 후 영업일 2일 내 처리 시작'이며 완료·PG 반영 시간은 보장하지 않음. 광고 초안의 '즉시 자동 환불'은 정책과 불일치
8. 미확인: 추가 인력 충원 미승인, 기술 배포 승인·결제 검증 결과 미제공 → 해당 항목은 미검증으로 표기
9. 대안: 대기열(접수 순번·예상 안내), 우선순위(결제 오류·중복 청구 등 긴급도), 고객 안내문(처리 시작 기한만 약속), 상담 분류 기준 초안 제시
## launch-operations/customer_comms/comms_pack.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline/artifacts/launch-operations/customer_comms/comms_pack.json)

확정 정책(접수 후 영업일 2일 내 처리 시작, 처리 완료·PG 반영 시간 미보장)만 반영한 고객 안내문, FAQ, 상담 분류 기준을 작성했다. '즉시 자동 환불'은 미확정으로 배제했고, 인력 충원·기술 배포·환불 완료를 완료된 사실로 표현하지 않았다. 실제 고객 연락이나 외부 시스템 변경은 수행하지 않았다.

[고객 안내문]
제목: 문의 접수 및 처리 안내
- 접수 경로: 앱/웹 내 1:1 문의, 고객센터 이메일, 전화 상담(운영 시간 내).
- 처리 시작 기준: 문의 접수 후 영업일 2일 안에 담당자가 처리를 시작합니다. 접수 시각이 영업시간 외이거나 주말·공휴일이면 다음 영업일부터 기산됩니다.
- 보장하지 않는 항목: 처리 완료 시점과 결제대행사(PG) 반영 시점은 보장하지 않습니다. PG 반영은 PG사 처리 일정에 따라 추가 시간이 걸릴 수 있습니다.
- 문의 시 확인 가능 정보: 접수 번호, 접수 시각, 현재 처리 단계(접수/처리 시작/처리 중), 담당 부서.
- 안내: 현재 상담 인력 충원은 확정되지 않았습니다. 문의가 몰릴 경우 처리 시작이 지연될 수 있으며, 이 경우 접수 순서와 긴급도에 따라 우선 처리합니다.

[FAQ]
Q1. 환불은 언제 되나요?
A. 문의 접수 후 영업일 2일 안에 처리를 시작합니다. 처리 완료 시점과 PG 반영 시점은 보장하지 않습니다.
Q2. '즉시 자동 환불'이라고 광고에서 봤는데요?
A. 해당 문구는 확정된 정책이 아니며, 현재 확정 정책은 '영업일 2일 내 처리 시작'입니다. 광고 문구는 확정 정책 기준으로 조정 중입니다.
Q3. 처리가 지연되고 있는데 왜인가요?
A. 문의량이 많거나 담당 부서 확인이 필요한 경우 지연될 수 있습니다. 접수 번호로 현재 단계를 확인할 수 있습니다.
Q4. PG 반영이 늦는 이유는?
A. PG사 처리 일정에 따르며, 당사가 반영 시점을 보장하지 않습니다.
Q5. 상담원이 부족한가요?
A. 인력 충원은 아직 확정되지 않았습니다. 충원이 어려운 경우 접수 순서와 긴급도 기준으로 우선 처리하며, 지연 시 별도 안내합니다.
Q6. 급한 건은 어떻게 하나요?
A. 결제 오류·서비스 이용 불가 등 긴급 등급으로 분류되면 우선 처리 대상이 됩니다. 접수 시 해당 항목을 선택해 주세요.

[상담 분류 기준(우선순위)]
- P1(긴급): 결제 오류(중복 결제, 결제 후 미반영), 서비스 이용 불가. 처리: 접수 즉시 분류, 영업일 2일 내 처리 시작 원칙보다 우선 배정, 담당 리드 에스컬레이션.
- P2(높음): 환불 요청, 계정/접근 문제. 처리: 영업일 2일 내 처리 시작, 지연 시 사유 안내.
- P3(일반): 정책·절차 문의, 사용 방법 문의. 처리: 영업일 2일 내 처리 시작, 순차 처리.
- 미확정 사안(즉시 자동 환불, 충원 완료, 배포 완료 등): '확인 후 안내'로 분류하고 확정 전 약속하지 않음.

[미확정·미완료 명시]
- '즉시 자동 환불'은 확정 정책과 다르므로 안내문·FAQ·분류 기준에서 사용하지 않음.
- 인력 충원, 기술 배포, 환불 처리는 완료된 것으로 표현하지 않음.
- 실제 고객 연락 및 외부 시스템 변경은 수행하지 않음.

### Facts

```json
{
  "immediate_refund_promised": false,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_time_guaranteed": false,
  "hiring_approved": false,
  "deployment_approved": false,
  "refund_completed": false,
  "actual_customer_contact_performed": false,
  "external_system_change_performed": false,
  "priority_levels_defined": 3,
  "unconfirmed_items_labeled_check_and_inform": true
}
```

### Evidence

1. 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작' — 안내문·FAQ·분류 기준의 처리 시작 기준으로 반영.
2. 처리 완료 시간과 PG 반영 시간은 보장하지 않음 — 안내문 '보장하지 않는 항목' 및 FAQ Q1·Q4에 명시.
3. 광고 초안의 '즉시 자동 환불'은 확정 정책과 다름 — FAQ Q2에서 미확정으로 안내하고 안내문·분류 기준에서 배제.
4. 인원 충원 미승인, 기술 배포 승인·결제 검증 미제공 — 안내문 및 FAQ Q5에서 충원 미확정으로 안내, 완료 표현 없음.
5. 상담 분류 기준: P1 결제 오류/서비스 이용 불가, P2 환불/계정, P3 일반 문의, 미확정 사안은 '확인 후 안내'로 분류.
6. 실제 고객 연락·외부 시스템 변경 미수행 — 산출물에 명시.
## launch-operations/customer_comms/ops_schedule.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline/artifacts/launch-operations/customer_comms/ops_schedule.json)

predecessors의 comms_pack(고객 안내문·FAQ·상담 분류 기준)과 single_source(SSOT 섹션, 변경 전달·승인 흐름, 마감-선행조건 체인, 보류 문구)를 근거로 담당·기한·선행조건이 연결된 운영 일정을 작성했다. 목요일 마감 체인은 제품팀 14:00 사양·제한 전달(선행 없음) → 광고 15:00 문구 마감(선행: 제품팀) → 상담 17:00 교육(선행: 광고 문구 확정) → 랜딩 18:00 수정 마감(선행: 상담 교육)으로 정리했고, 각 단계에 담당·기한·선행조건·차단 조건·지연 시 에스컬레이션 경로를 명시했다. 미승인·미확정 항목(인력 충원 미승인, 기술 배포 승인 미제공, 결제 검증 미제공, 환불 완료 미수행)은 '미완료/보류'로 별도 표기하고 완료 표현을 배제했다. 출시 직전 확인표에는 SSOT 버전, 채널별 반영 확인, 보류 문구 사용 여부, 미확정 사안 잔여 여부를 점검 항목으로 넣었다. 실제 고객 연락·외부 시스템 변경은 수행하지 않았다.

[운영 일정표 — 담당·기한·선행조건]
1) 제품팀 사양·제한 전달 — 담당: 제품팀 / 기한: 목요일 14:00 / 선행조건: 없음 / 차단 조건: 사양 미확정 시 광고 문구 확정 불가 / 지연 시: 제품 리드 → 운영 리드 에스컬레이션.
2) 광고 문구 마감 — 담당: 광고 / 기한: 목요일 15:00 / 선행조건: 제품팀 14:00 전달 / 차단 조건: SSOT 허용 문구 미확정 시 마감 불가 / 지연 시: 제품 리드 → 운영 리드 에스컬레이션, 대체 문구 '영업일 2일 내 처리 시작'만 사용.
3) 상담 교육 — 담당: 상담 / 기한: 목요일 17:00 / 선행조건: 광고 15:00 문구 확정 / 차단 조건: 교육 자료 미승인 시 교육 보류 / 지연 시: 운영 리드 에스컬레이션.
4) 랜딩 수정 마감 — 담당: 랜딩 / 기한: 목요일 18:00 / 선행조건: 상담 17:00 교육 완료 / 차단 조건: 미확인 사항 확정 전 보류 문구만 사용 / 지연 시: 운영 리드 에스컬레이션.
5) 출시 — 금요일 10:00 / 선행조건: 1~4 완료 및 출시 직전 확인표 통과.

[확정/미확인 사항표]
- 확정: 접수 후 영업일 2일 내 처리 시작, 처리 완료·PG 반영 시간 미보장, 상담 분류 P1/P2/P3 3단계, 미확정 사안 '확인 후 안내' 분류.
- 미확인/미완료: 인력 충원 미승인, 기술 배포 승인 미제공, 결제 검증 결과 미제공, 환불 완료 미수행, '즉시 자동 환불' 미확정(금지 표현).

[용량 계산]
- 예상 문의: 800명 × 20% = 160건(출시 후 첫 4시간).
- 처리 용량: 상담원 4명 × 시간당 5건 × 4시간 = 80건.
- 미처리량: 160 - 80 = 80건.
- 전량 처리 필요 총 상담원: 160 ÷ (5 × 4) = 8명.
- 추가 인원: 8 - 4 = 4명(충원 미승인이므로 확보 불가 상태).

[충원 불가 시 대기열·우선순위·고객 안내 대안]
- 대기열: 접수 순서 + 긴급도(P1 우선) 기준. P1(결제 오류/서비스 이용 불가) 즉시 분류·우선 배정, P2(환불/계정) 영업일 2일 내 처리 시작, P3(일반) 순차 처리.
- 고객 안내: '접수 후 영업일 2일 안에 처리를 시작합니다. 완료 및 PG 반영 시점은 보장하지 않습니다.' 충원 미확정 사실을 안내하고 완료 표현 금지.

[비상 계획]
- 광고 지연: SSOT 허용 문구만 사용, 마감 미준수 시 제품 리드 → 운영 리드 에스컬레이션.
- 기술 승인 지연: '출시 준비 중이며, 확정 시 안내드립니다.' 보류 문구만 사용, '배포 완료' 표현 금지.
- 문의 급증: P1 우선 배정, P2/P3 순차 처리, 지연 시 사유 안내, 충원은 미승인이므로 완료 표현 없이 '확인 후 안내'.

[출시 직전 확인표]
- SSOT 버전 확인(개정 시 버전 증가·승인 기록).
- 채널별 반영 확인: 광고/상담/랜딩 각 책임자 회신.
- 보류 문구 사용 여부 확인(배포·결제 검증·즉시 환불 표현 미사용).
- 미확정 사안 잔여 여부 확인(충원·배포·결제 검증·환불 완료).
- 구두·개별 채널 단독 변경 금지 준수 확인.

[미수행 명시]
- 실제 고객 연락, 외부 시스템 변경, 광고/랜딩/상담 시스템 반영은 수행하지 않았다(미검증). 본 산출물은 일정·표·문구 설계 문서다.

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
  "product_team_deadline_hour": 14,
  "ad_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "hiring_approved": false,
  "deployment_approved": false,
  "payment_verification_provided": false,
  "refund_completed": false,
  "actual_customer_contact_performed": false,
  "external_system_change_performed": false,
  "policy_processing_start_business_days": 2,
  "completion_time_guaranteed": false,
  "pg_reflection_time_guaranteed": false,
  "priority_levels_defined": 3,
  "unconfirmed_items_labeled_check_and_inform": true,
  "schedule_steps_count": 5,
  "escalation_path_defined": true,
  "checklist_items_count": 5,
  "verbal_change_prohibited": true,
  "version_control_required": true
}
```

### Evidence

1. expected_contacts = 800명 × 20% = 160건 (출시 후 첫 4시간)
2. four_hour_capacity = 상담원 4명 × 시간당 5건 × 4시간 = 80건
3. backlog_without_hiring = 160 - 80 = 80건
4. required_total_agents = 160건 ÷ (5건/시간 × 4시간) = 8명
5. additional_agents = 8 - 4 = 4명
6. support_window_hours = 4 (출시 후 첫 4시간 가정), launch_hour = 10 (금요일 10시 출시)
7. 마감 체인: 제품팀 14:00 → 광고 15:00 → 상담 17:00 → 랜딩 18:00 (각 단계 선행조건 명시)
8. 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작' — 안내문·FAQ·분류 기준의 처리 시작 기준으로 반영
9. 처리 완료 시간과 PG 반영 시간은 보장하지 않음 — 안내문 '보장하지 않는 항목' 및 FAQ Q1·Q4에 명시
10. 광고 초안의 '즉시 자동 환불'은 확정 정책과 다름 — FAQ Q2에서 미확정으로 안내하고 안내문·분류 기준에서 배제
11. 인원 충원 미승인, 기술 배포 승인·결제 검증 미제공 — 안내문 및 FAQ Q5에서 충원 미확정으로 안내, 완료 표현 없음
12. 상담 분류 기준: P1 결제 오류/서비스 이용 불가, P2 환불/계정, P3 일반 문의, 미확정 사안은 '확인 후 안내'로 분류
13. 미확인 사항: 기술 배포 승인, 결제 검증 결과 (2건) — 확정 전 보류 문구만 사용
14. 본 작업은 일정·표·문구 설계 문서 작성이며 실제 광고/랜딩/상담 시스템 변경·고객 연락은 미수행(미검증)
## launch-operations/customer_comms/single_source.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline/artifacts/launch-operations/customer_comms/single_source.json)

단일 기준 문서(SSOT) 구조와 변경 전달·승인 흐름을 설계했다. 핵심 불일치는 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작(완료·PG 반영 미보장)'과 광고 초안 '즉시 자동 환불'이다. 광고 초안은 금지 표현으로 분류하고, 모든 채널은 SSOT의 허용 문구만 사용하도록 규정했다. 목요일 마감은 선행조건 체인으로 연결했다: 제품팀 14:00 확정 사양·제한 전달 → 광고 15:00 문구 마감 → 상담 17:00 교육 → 랜딩 18:00 수정 마감. 미확인 사항(기술 배포 승인, 결제 검증 결과)은 확정 전까지 '보류 문구'만 사용하도록 명시했다. 본 작업은 설계 문서 작성이며 실제 광고/랜딩/상담 시스템 변경이나 고객 연락은 수행하지 않았다(미검증).

[SSOT 섹션 구조]
1) 확정 정책 원문 — 책임: 제품팀 / 승인: 제품 리드. 원문: '상담 접수 후 영업일 2일 안에 처리 시작. 처리 완료 시간과 PG 반영 시간은 보장하지 않음.'
2) 고객 대면 허용 문구 — 책임: 광고·상담·랜딩 공통, 승인: 제품 리드. 예: '접수 후 영업일 2일 안에 처리를 시작합니다. 완료 및 환불 반영 시점은 별도 안내됩니다.'
3) 금지 문구 — 책임: 제품팀 관리, 전 채널 준수. 예: '즉시 자동 환불', '즉시 환불 완료', '당일 환불 보장', '처리 완료 보장'.
4) 채널별 적용 — 광고(문구 반영), 상담(교육·FAQ), 랜딩(페이지 수정). 각 채널 책임자 지정.
5) 미확인 사항 — 기술 배포 승인, 결제 검증 결과. 확정 전 '보류 문구'만 사용.
6) 버전·이력 — 개정 시 버전 증가, 승인권자 확인 기록.

[변경 전달·승인 흐름]
변경 요청 → SSOT 개정(버전 증가) → 승인권자(제품 리드) 확인 → 채널별 반영(광고/상담/랜딩) → 반영 확인 회신. 구두·개별 채널 단독 변경 금지.

[마감-선행조건 체인]
- 제품팀 14:00: 확정 사양·제한 전달 (선행: 없음). 차단 조건: 사양 미확정 시 광고 문구 확정 불가.
- 광고 15:00: 문구 마감 (선행: 제품팀 14:00). 차단 조건: SSOT 허용 문구 미확정 시 마감 불가.
- 상담 17:00: 교육 (선행: 광고 15:00 문구 확정). 차단 조건: 교육 자료 미승인 시 교육 보류.
- 랜딩 18:00: 수정 마감 (선행: 상담 17:00 교육 완료). 차단 조건: 미확인 사항 확정 전 보류 문구만 사용.

[미확인 사항 보류 문구]
기술 배포 승인·결제 검증 결과 확정 전까지 광고·상담·랜딩에서 '배포 완료', '결제 검증 완료', '즉시 환불' 표현 사용 금지. 대체: '출시 준비 중이며, 확정 시 안내드립니다.'

[에스컬레이션]
마감 지연 시 제품 리드 → 운영 리드 순 에스컬레이션. 대체 문구: '영업일 2일 내 처리 시작'만 사용.

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
  "ssot_sections": 6,
  "product_team_deadline_hour": 14,
  "ad_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "unconfirmed_items_count": 2,
  "version_control_required": true,
  "verbal_change_prohibited": true
}
```

### Evidence

1. expected_contacts = 800명 × 20% = 160건 (출시 후 첫 4시간)
2. four_hour_capacity = 상담원 4명 × 시간당 5건 × 4시간 = 80건
3. backlog_without_hiring = 160 - 80 = 80건
4. required_total_agents = 160건 ÷ (5건/시간 × 4시간) = 8명
5. additional_agents = 8 - 4 = 4명
6. support_window_hours = 4 (출시 후 첫 4시간 가정)
7. launch_hour = 10 (금요일 10시 출시)
8. immediate_refund_promised = false: 확정 정책은 '영업일 2일 내 처리 시작'이며 완료·PG 반영 미보장, 광고 초안 '즉시 자동 환불'은 금지 표현으로 분류
9. 마감 체인: 제품팀 14:00 → 광고 15:00 → 상담 17:00 → 랜딩 18:00 (각 단계 선행조건 명시)
10. 미확인 사항: 기술 배포 승인, 결제 검증 결과 (2건) — 확정 전 보류 문구만 사용
11. 본 작업은 설계 문서 작성이며 실제 광고/랜딩/상담 시스템 변경·고객 연락은 미수행(미검증)
## launch-operations/customer_comms.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline/artifacts/launch-operations/customer_comms.json)

고객 안내문·FAQ·상담 분류 기준, 단일 기준 문서(SSOT) 구조와 변경 전달·승인 흐름, 담당·기한·선행조건이 연결된 운영 일정, 용량 계산과 인력 대안, 비상 계획, 출시 직전 확인표를 통합했다. 핵심 불일치는 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작(완료·PG 반영 미보장)'과 광고 초안 '즉시 자동 환불'이며, 광고 초안은 금지 표현으로 분류하고 전 채널이 SSOT 허용 문구만 사용하도록 규정했다. 목요일 마감은 선행조건 체인으로 연결했다: 제품팀 14:00 사양·제한 전달 → 광고 15:00 문구 마감 → 상담 17:00 교육 → 랜딩 18:00 수정 마감 → 금요일 10:00 출시. 용량 계산은 초대 800명 × 20% = 160건 문의, 상담원 4명 × 시간당 5건 × 4시간 = 80건 처리, 미처리 80건, 전량 처리에 총 8명 필요(추가 4명)이다. 인력 충원은 미승인이므로 대기열(접수 순번·긴급도), 우선순위(P1 결제 오류/이용 불가, P2 환불/계정, P3 일반), 고객 안내('영업일 2일 내 처리 시작'만 약속) 대안을 제시했다. 미확인 사항(기술 배포 승인, 결제 검증 결과, 인력 충원 승인)은 '보류/확인 후 안내'로 표기하고 완료 표현을 배제했다. 실제 고객 연락·외부 시스템 변경·광고/랜딩/상담 시스템 반영은 수행하지 않았으며, 본 산출물은 설계 문서다.

[고객 안내문]
제목: 문의 접수 및 처리 안내
- 접수 경로: 앱/웹 1:1 문의, 고객센터 이메일, 전화 상담(운영 시간 내).
- 처리 시작 기준: 문의 접수 후 영업일 2일 안에 담당자가 처리를 시작합니다. 접수 시각이 영업시간 외이거나 주말·공휴일이면 다음 영업일부터 기산됩니다.
- 보장하지 않는 항목: 처리 완료 시점과 결제대행사(PG) 반영 시점은 보장하지 않습니다.
- 확인 가능 정보: 접수 번호, 접수 시각, 현재 처리 단계(접수/처리 시작/처리 중), 담당 부서.
- 안내: 상담 인력 충원은 확정되지 않았습니다. 문의가 몰릴 경우 처리 시작이 지연될 수 있으며, 접수 순서와 긴급도에 따라 우선 처리합니다.

[FAQ]
Q1. 환불은 언제 되나요? A. 접수 후 영업일 2일 안에 처리를 시작합니다. 완료 및 PG 반영 시점은 보장하지 않습니다.
Q2. '즉시 자동 환불'이라고 봤는데요? A. 해당 문구는 확정 정책이 아니며, 확정 정책은 '영업일 2일 내 처리 시작'입니다.
Q3. 처리가 지연되는 이유는? A. 문의량 증가 또는 담당 부서 확인 필요 시 지연될 수 있습니다. 접수 번호로 단계 확인 가능합니다.
Q4. PG 반영이 늦는 이유는? A. PG사 처리 일정에 따르며 당사가 반영 시점을 보장하지 않습니다.
Q5. 상담원이 부족한가요? A. 인력 충원은 아직 확정되지 않았습니다. 충원이 어려우면 접수 순서와 긴급도 기준으로 우선 처리합니다.
Q6. 급한 건은? A. 결제 오류·서비스 이용 불가 등 긴급 등급은 우선 처리 대상입니다. 접수 시 해당 항목을 선택해 주세요.

[상담 분류 기준(우선순위)]
- P1(긴급): 결제 오류(중복 결제, 결제 후 미반영), 서비스 이용 불가. 즉시 분류, 우선 배정, 담당 리드 에스컬레이션.
- P2(높음): 환불 요청, 계정/접근 문제. 영업일 2일 내 처리 시작, 지연 시 사유 안내.
- P3(일반): 정책·절차·사용 방법 문의. 영업일 2일 내 처리 시작, 순차 처리.
- 미확정 사안(즉시 자동 환불, 충원 완료, 배포 완료 등): '확인 후 안내'로 분류, 확정 전 약속하지 않음.

[SSOT 섹션 구조]
1) 확정 정책 원문(책임: 제품팀, 승인: 제품 리드): '상담 접수 후 영업일 2일 안에 처리 시작. 처리 완료 시간과 PG 반영 시간은 보장하지 않음.'
2) 고객 대면 허용 문구(광고·상담·랜딩 공통): '접수 후 영업일 2일 안에 처리를 시작합니다. 완료 및 환불 반영 시점은 별도 안내됩니다.'
3) 금지 문구: '즉시 자동 환불', '즉시 환불 완료', '당일 환불 보장', '처리 완료 보장'.
4) 채널별 적용: 광고(문구), 상담(교육·FAQ), 랜딩(페이지 수정), 각 채널 책임자 지정.
5) 미확인 사항: 기술 배포 승인, 결제 검증 결과 — 확정 전 보류 문구만 사용.
6) 버전·이력: 개정 시 버전 증가, 승인권자 확인 기록.

[변경 전달·승인 흐름]
변경 요청 → SSOT 개정(버전 증가) → 승인권자(제품 리드) 확인 → 채널별 반영(광고/상담/랜딩) → 반영 확인 회신. 구두·개별 채널 단독 변경 금지.

[운영 일정표 — 담당·기한·선행조건]
1) 제품팀 사양·제한 전달 — 제품팀 / 목요일 14:00 / 선행 없음 / 사양 미확정 시 광고 문구 확정 불가 / 지연 시 제품 리드 → 운영 리드.
2) 광고 문구 마감 — 광고 / 목요일 15:00 / 선행: 제품팀 14:00 / SSOT 허용 문구 미확정 시 마감 불가 / 지연 시 대체 문구 '영업일 2일 내 처리 시작'만 사용.
3) 상담 교육 — 상담 / 목요일 17:00 / 선행: 광고 15:00 문구 확정 / 교육 자료 미승인 시 보류 / 지연 시 운영 리드 에스컬레이션.
4) 랜딩 수정 마감 — 랜딩 / 목요일 18:00 / 선행: 상담 17:00 교육 완료 / 미확인 사항 확정 전 보류 문구만 사용 / 지연 시 운영 리드 에스컬레이션.
5) 출시 — 금요일 10:00 / 선행: 1~4 완료 및 출시 직전 확인표 통과.

[확정/미확인 사항표]
- 확정: 접수 후 영업일 2일 내 처리 시작, 완료·PG 반영 미보장, 상담 분류 P1/P2/P3, 미확정 사안 '확인 후 안내'.
- 미확인/미완료: 인력 충원 미승인, 기술 배포 승인 미제공, 결제 검증 결과 미제공, 환불 완료 미수행, '즉시 자동 환불' 미확정(금지 표현).

[용량 계산]
- 예상 문의: 800 × 20% = 160건(출시 후 첫 4시간).
- 처리 용량: 4명 × 5건/시간 × 4시간 = 80건.
- 미처리량: 160 − 80 = 80건.
- 전량 처리 필요 총 상담원: 160 ÷ (5 × 4) = 8명.
- 추가 인원: 8 − 4 = 4명(충원 미승인으로 확보 불가 상태).

[충원 불가 시 대안]
- 대기열: 접수 순서 + 긴급도(P1 우선). P1 즉시 분류·우선 배정, P2 영업일 2일 내 처리 시작, P3 순차 처리.
- 고객 안내: '접수 후 영업일 2일 안에 처리를 시작합니다. 완료 및 PG 반영 시점은 보장하지 않습니다.' 충원 미확정 사실 안내, 완료 표현 금지.

[비상 계획]
- 광고 지연: SSOT 허용 문구만 사용, 마감 미준수 시 제품 리드 → 운영 리드 에스컬레이션.
- 기술 승인 지연: '출시 준비 중이며, 확정 시 안내드립니다.' 보류 문구만 사용, '배포 완료' 표현 금지.
- 문의 급증: P1 우선 배정, P2/P3 순차 처리, 지연 시 사유 안내, 충원은 미승인이므로 '확인 후 안내'.

[출시 직전 확인표]
- SSOT 버전 확인(개정 시 버전 증가·승인 기록).
- 채널별 반영 확인: 광고/상담/랜딩 책임자 회신.
- 보류 문구 사용 여부 확인(배포·결제 검증·즉시 환불 표현 미사용).
- 미확정 사안 잔여 여부 확인(충원·배포·결제 검증·환불 완료).
- 구두·개별 채널 단독 변경 금지 준수 확인.

[미수행 명시]
실제 고객 연락, 외부 시스템 변경, 광고/랜딩/상담 시스템 반영은 수행하지 않았다(미검증). 본 산출물은 일정·표·문구 설계 문서다.

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
  "hiring_approved": false,
  "deployment_approved": false,
  "payment_verification_provided": false,
  "refund_completed": false,
  "actual_customer_contact_performed": false,
  "external_system_change_performed": false,
  "priority_levels_defined": 3,
  "unconfirmed_items_labeled_check_and_inform": true,
  "product_team_deadline_hour": 14,
  "ad_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "schedule_steps_count": 5,
  "checklist_items_count": 5,
  "ssot_sections": 6,
  "verbal_change_prohibited": true,
  "version_control_required": true,
  "escalation_path_defined": true
}
```

### Evidence

1. expected_contacts = 800명 × 0.20 = 160건 (초대 800명, 출시 후 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (상담원 4명, 시간당 5건 가정)
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 − 4 = 4명
6. support_window_hours = 4, launch_hour = 10 (금요일 10시 출시, 첫 4시간 기준)
7. 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작' — 안내문·FAQ·분류 기준의 처리 시작 기준으로 반영
8. 처리 완료 시간과 PG 반영 시간은 보장하지 않음 — 안내문 '보장하지 않는 항목' 및 FAQ Q1·Q4에 명시
9. 광고 초안의 '즉시 자동 환불'은 확정 정책과 다름 — FAQ Q2에서 미확정으로 안내하고 안내문·분류 기준에서 배제(immediate_refund_promised=false)
10. 인원 충원 미승인, 기술 배포 승인·결제 검증 미제공 — 안내문 및 FAQ Q5에서 충원 미확정으로 안내, 완료 표현 없음
11. 마감 체인: 제품팀 14:00 → 광고 15:00 → 상담 17:00 → 랜딩 18:00 (각 단계 선행조건 명시)
12. 상담 분류 기준: P1 결제 오류/서비스 이용 불가, P2 환불/계정, P3 일반 문의, 미확정 사안은 '확인 후 안내'로 분류
13. SSOT 구조 6개 섹션(확정 정책 원문, 허용 문구, 금지 문구, 채널별 적용, 미확인 사항, 버전·이력)과 변경 전달·승인 흐름 설계
14. 출시 직전 확인표 5개 항목(SSOT 버전, 채널별 반영, 보류 문구 사용, 미확정 사안 잔여, 구두 변경 금지)
15. 실제 고객 연락·외부 시스템 변경·광고/랜딩/상담 시스템 반영은 미수행(미검증) — 본 산출물은 설계 문서
## launch-operations/integrate_report.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline/artifacts/launch-operations/integrate_report.json)

capacity_and_policy와 customer_comms 산출물을 통합해 launch_operations_report를 작성했다. 두 산출물의 8개 숫자 키와 immediate_refund_promised 불리언을 대조한 결과 전부 일치했다: expected_contacts=160, four_hour_capacity=80, backlog_without_hiring=80, required_total_agents=8, additional_agents=4, support_window_hours=4, launch_hour=10, immediate_refund_promised=false. 모순·미확인 사항은 단일 목록으로 통합했다: (1) 정책-광고 불일치 — 확정 정책 '영업일 2일 내 처리 시작(완료·PG 반영 미보장)' vs 광고 초안 '즉시 자동 환불' → 광고 초안을 금지 표현으로 분류, SSOT 허용 문구만 사용. (2) 일정 선행조건 충돌 — 제품팀 14:00 사양 전달이 지연되면 광고 15:00 문구 마감이 불가하므로 대체 문구('영업일 2일 내 처리 시작')만 사용하는 폴백 필요. (3) 미확인 사항 — 기술 배포 승인, 결제 검증(PG) 결과, 인력 충원 승인 모두 미제공 → '보류/확인 후 안내'로만 표기, 완료 표현 금지. (4) 용량-인력 충돌 — 160건 문의 대비 80건만 처리 가능, 추가 4명 미승인 상태이므로 대기열·우선순위(P1 결제 오류/이용 불가, P2 환불/계정, P3 일반) 대안 적용.

[launch_operations_report 통합 구조]
1) 운영 일정표(담당·기한·선행조건): 제품팀 14:00 사양·제한 전달(선행 없음) → 광고 15:00 문구 마감(선행: 제품팀 14:00) → 상담 17:00 교육(선행: 광고 15:00) → 랜딩 18:00 수정 마감(선행: 상담 17:00) → 금요일 10:00 출시(선행: 1~4 완료 및 확인표 통과).
2) 확정/미확인 사항표: 확정 — 영업일 2일 내 처리 시작, 완료·PG 반영 미보장, P1/P2/P3 분류, 미확정 사안 '확인 후 안내'. 미확인 — 충원 미승인, 배포 승인 미제공, 결제 검증 미제공, 환불 완료 미수행, '즉시 자동 환불' 미확정(금지 표현).
3) 고객 안내문·FAQ·상담 분류 기준: 안내문은 '접수 후 영업일 2일 안에 처리를 시작합니다. 완료 및 PG 반영 시점은 보장하지 않습니다.'를 핵심 문구로 사용. FAQ 6문항(Q1 환불 시점, Q2 '즉시 자동 환불' 문구 정정, Q3 지연 사유, Q4 PG 반영, Q5 상담원 부족, Q6 급한 건). 분류 P1/P2/P3 + 미확정 사안 '확인 후 안내'.
4) SSOT 구조 6섹션(확정 정책 원문, 허용 문구, 금지 문구, 채널별 적용, 미확인 사항, 버전·이력)과 변경 전달·승인 흐름(변경 요청 → SSOT 개정 → 제품 리드 승인 → 채널 반영 → 회신, 구두·단독 변경 금지).
5) 용량 계산: 800×20%=160건, 4명×5건/시×4시간=80건, 미처리 80건, 총 8명 필요, 추가 4명.
6) 인력 대안: 대기열(접수 순+긴급도), 우선순위 P1/P2/P3, 고객 안내는 '영업일 2일 내 처리 시작'만 약속.
7) 비상 계획: (a) 광고 지연 → SSOT 허용 문구만 사용, 제품 리드→운영 리드 에스컬레이션. (b) 기술 승인 지연 → '출시 준비 중, 확정 시 안내' 보류 문구만 사용, '배포 완료' 금지. (c) 문의 급증 → P1 우선 배정, P2/P3 순차, 지연 사유 안내, 충원은 '확인 후 안내'.
8) 출시 직전 확인표 5항목: SSOT 버전 확인, 채널별 반영 회신, 보류 문구 사용 여부, 미확정 사안 잔여 여부, 구두·단독 변경 금지 준수.

[설계 vs 실제 수행 구분]
본 산출물은 설계 문서다. 실제 구현·테스트·고객 연락·외부 시스템 변경·광고/랜딩/상담 시스템 반영은 수행하지 않았다(미검증). 상담 처리율, PG 반영 시간, 배포 승인 여부는 제공 자료가 없어 검증되지 않았다. 제품 정책을 새로 결정하지 않았고, 승인되지 않은 충원·환불·배포를 완료로 표기하지 않았다.

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
  "policy_conflict_present": true,
  "deployment_approval_provided": false,
  "payment_verification_provided": false,
  "hiring_approval_provided": false,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "actual_customer_contact_performed": false,
  "external_system_change_performed": false,
  "completion_time_guaranteed": false,
  "pg_reflection_time_guaranteed": false,
  "policy_processing_start_business_days": 2,
  "priority_levels_defined": 3,
  "ssot_sections": 6,
  "checklist_items_count": 5,
  "schedule_steps_count": 5,
  "product_team_deadline_hour": 14,
  "ad_deadline_hour": 15,
  "support_training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "numeric_keys_matched": true,
  "boolean_key_matched": true,
  "contradictions_listed": true,
  "unconfirmed_items_listed": true
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20%)
2. four_hour_capacity = 4명 × 5건/시 × 4시간 = 80건
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명
5. additional_agents = 8 − 4 = 4명
6. support_window_hours = 4, launch_hour = 10 (금요일 10시 출시, 첫 4시간)
7. capacity_and_policy.facts와 customer_comms.facts의 8개 숫자 키 대조 결과 전부 일치(160/80/80/8/4/4/10)
8. immediate_refund_promised: 두 산출물 모두 false — 확정 정책 '영업일 2일 내 처리 시작'과 광고 초안 '즉시 자동 환불' 불일치
9. 모순 통합: 정책-광고 불일치, 일정 선행조건 충돌(제품팀 14:00 지연 시 광고 15:00 마감 불가), 용량-인력 충돌(160건 vs 80건 처리)
10. 미확인 통합: 기술 배포 승인·결제 검증 결과·인력 충원 승인 모두 제공 자료에 없음 → '보류/확인 후 안내'로만 표기
11. 일정 체인: 제품팀 14:00 → 광고 15:00 → 상담 17:00 → 랜딩 18:00 → 금요일 10:00 출시
12. SSOT 6섹션(확정 정책 원문, 허용 문구, 금지 문구, 채널별 적용, 미확인 사항, 버전·이력) 및 변경 전달·승인 흐름 설계
13. 출시 직전 확인표 5항목(SSOT 버전, 채널별 반영, 보류 문구 사용, 미확정 사안 잔여, 구두 변경 금지)
14. 비상 계획 3종(광고 지연/기술 승인 지연/문의 급증) 통합
15. 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음(제공 자료만 사용, 미검증)