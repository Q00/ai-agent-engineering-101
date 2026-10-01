# launch-operations / homogeneous — 429 복구

상태: succeeded. facts: 8/8.
모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.

## launch-operations.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous/artifacts/launch-operations.json)

세 선행 결과(capacity_calc, policy_comms, schedule_contingency)를 통합해 금요일 10시 출시용 운영 패키지를 확정했다. 핵심 수치: 초대 800명 × 첫 4시간 문의율 20% = 160건, 상담원 4명 × 시간당 5건 × 4시간 = 80건 처리, 미처리 80건, 전량 처리 총원 8명, 추가 4명(미승인). 확정 정책은 '상담 접수 후 영업일 2일 안에 처리 시작'이며 처리 완료·PG 반영 시간은 보장되지 않는다. 광고 초안 '즉시 자동 환불'은 정책과 모순되므로 immediate_refund_promised=false로 확정하고 목 15시 광고 마감 전 단일 기준 문서(PMST)로 수정해야 한다.

[운영 일정표(담당·기한·선행조건)] 1) 목 14:00 제품팀 — 확정 사양·알려진 제한 전달(선행 없음, 확정). 2) 목 14:00~15:00 기준 문서 관리자 — PMST 확정(선행 1, 미확인: 사양 내용 미제공). 3) 목 15:00 광고 담당 — 광고 문구 마감, '즉시 자동 환불' 제거(선행 2, 마감 확정). 4) 목 17:00 상담 리드 — 상담 교육(선행 1·2, 확정). 5) 목 18:00 랜딩 담당 — 랜딩 수정 마감(선행 2, 확정). 6) 목요일 중 제품/기술 — 기술 배포 승인(미확인). 7) 목요일 중 결제 담당 — 결제 검증(미확인). 8) 금 10:00 전 팀 — 출시(선행 3·4·5·6·7, 시각 확정이나 6·7 미확인).

[확정/미확인 사항표] 확정: 정책 '영업일 2일 내 처리 시작', 완료·PG 반영 미보장, 마감 목 14/15/17/18시, 출시 금 10시, 초대 800명·문의율 20%·상담원 4명·시간당 5건. 미확인: 기술 배포 승인, 결제 검증, 인원 충원(미승인). 모순: 광고 '즉시 자동 환불' vs 정책 → 목 15시 전 PMST로 수정 필요.

[PMST 구조] policy_id, status(confirmed|unconfirmed), 적용 범위, 승인된 고객 약속 문구, 변경 이력, 승인자, 유효 시점, 미확인/제한 목록. 모든 채널은 PMST 승인 문구만 인용.

[고객 안내문] "환불 요청은 상담 접수 후 영업일 2일 이내에 처리 절차가 시작됩니다. 처리 완료 시점과 PG 반영 시점은 보장되지 않습니다." [FAQ] 즉시 환불 아님, 광고 문구는 수정 중, PG 반영 시점 보장 불가, 기술 배포·결제 검증은 미확인. [상담 분류] (A)환불 정책 (B)처리 지연 (C)PG 반영 (D)광고 불일치 (E)기술/결제 이슈, 우선순위 결제 오류 > 환불 지연 > 일반.

[용량·인력 대안] 800×0.20=160건, 4×5×4=80건, 160−80=80건, 160÷(5×4)=8명, 8−4=4명. 충원 미승인 시 FIFO 접수+우선순위(금전 피해 > 서비스 불가 > 일반)+'영업일 2일 내 처리 시작' 안내+FAQ 셀프서비스.

[3종 비상계획] 1) 광고 지연: 발동=목 15시 문구 미확정/불일치 잔존, 절차=게재 보류→확정 문구 대체→이력 기록. 2) 기술 승인 지연: 발동=배포 승인·결제 검증 미수신, 절차=보류/제한 출시→미확인 고지. 3) 문의 급증: 발동=접수 160건 초과 또는 미처리 80건 초과, 절차=FIFO+우선순위+이월 고지.

[출시 직전 확인표 8항목] 1)사양 전달(목14) 2)PMST 확정 3)광고 문구 확정·'즉시 자동 환불' 제거(목15) 4)상담 교육(목17) 5)랜딩 수정(목18) 6)기술 배포 승인(미수신) 7)결제 검증(미수신) 8)충원 승인(미승인, 추가 4명).

[한계] 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않았고, 기술 배포 승인·결제 검증·충원 승인은 미제공이므로 완료로 간주하지 않는다. 본 산출물은 설계 제안이며 새 정책 결정이 아니다.

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
  "completion_time_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "policy_processing_start_business_days": 2,
  "hiring_approved": false,
  "tech_deploy_approval_status": "unconfirmed",
  "payment_verification_status": "unconfirmed",
  "product_spec_delivery_hour": 14,
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_fix_deadline_hour": 18,
  "contingency_plans_count": 3,
  "checklist_items": 8,
  "single_source_document_defined": true,
  "implementation_performed": false,
  "test_performed": false,
  "external_contact_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (상담원 4명, 시간당 5건, 지원 창 4시간)
3. backlog_without_hiring = 160 − 80 = 80건 (예상 문의 − 4시간 처리 용량)
4. required_total_agents = 160 ÷ (5 × 4) = 8명 (전량 처리를 위한 총 상담원 수)
5. additional_agents = 8 − 4 = 4명 (필요 총원 − 현재 4명)
6. support_window_hours = 4, launch_hour = 10 (출시 후 첫 4시간 지원 창, 금요일 10시 출시)
7. 확정 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작', '처리 완료 시간과 PG 반영 시간은 보장하지 않는다' → completion_time_guaranteed=false, pg_reflection_guaranteed=false
8. 광고 초안 '즉시 자동 환불'은 정책의 '영업일 2일 내 처리 시작' 및 '완료/PG 미보장'과 모순 → immediate_refund_promised=false
9. 일정 근거: 제품팀 목 14시 사양 전달, 광고 마감 목 15시, 상담 교육 목 17시, 랜딩 수정 마감 목 18시, 금 10시 출시
10. 미확인 근거: 기술 배포 승인과 결제 검증 결과는 미제공, 인원 충원은 미승인 → tech_deploy_approval_status=unconfirmed, payment_verification_status=unconfirmed, hiring_approved=false
11. PMST 구조: policy_id/status/적용 범위/승인 문구/변경 이력/승인자/유효 시점/미확인 목록 필드로 설계
12. 비상계획 3종: 광고 지연(목 15시 트리거), 기술 승인 지연(승인·검증 미수신), 문의 급증(접수 160건 또는 미처리 80건 초과)
13. 출시 직전 확인표 8항목: 사양 전달/PMST 확정/광고 문구/상담 교육/랜딩 수정/기술 배포 승인/결제 검증/충원 승인
14. 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음 → implementation_performed=false, test_performed=false, external_contact_performed=false
## launch-operations/capacity_calc.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous/artifacts/launch-operations/capacity_calc.json)

첫 4시간 용량·인력 계산과 충원 불가 시 운영 대안을 정리했다. 초대 800명 중 20%가 문의하면 160건이 발생하고, 상담원 4명이 시간당 5건씩 4시간 처리하면 80건을 처리해 80건이 미처리된다. 전량 처리를 위해서는 총 8명이 필요하므로 추가 4명이 필요하다. 지원 창은 출시 후 첫 4시간(금요일 10:00~14:00)으로 두고, 출시 시각은 금요일 10시다. 인원 충원은 아직 승인되지 않았으므로 충원을 전제로 한 완료 표현은 쓰지 않았고, 충원 불가 시 대안으로 대기열 운영(접수 순), 우선순위(결제/환불 오류 > 계정 접근 > 일반 문의), 고객 안내(영업일 2일 내 처리 시작 고지, 즉시 자동 환불 아님 명시)를 제시한다. 광고 초안의 '즉시 자동 환불'은 확정 정책과 모순되므로 단일 기준 문서에서 금지 문구로 관리해야 한다. 기술 배포 승인과 결제 검증 결과는 미제공 상태로 미확인 사항에 남긴다.

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

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (상담원 4명, 시간당 5건, 지원 창 4시간)
3. backlog_without_hiring = 160 − 80 = 80건 (예상 문의 − 4시간 처리 용량)
4. required_total_agents = 160 ÷ (5 × 4) = 8명 (전량 처리를 위한 총 상담원 수)
5. additional_agents = 8 − 4 = 4명 (필요 총원 − 현재 4명)
6. support_window_hours = 4 (출시 후 첫 4시간을 지원 창으로 명시)
7. launch_hour = 10 (금요일 10시 출시)
8. 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료 시간과 PG 반영 시간은 보장하지 않음. 따라서 immediate_refund_promised = false이며 광고 초안의 '즉시 자동 환불'은 정책과 모순됨
9. 충원 미승인 전제 대안: 대기열 운영(접수 순), 우선순위(결제/환불 오류 > 계정 접근 > 일반 문의), 고객 안내(영업일 2일 내 처리 시작, 즉시 자동 환불 아님 명시)
10. 미확인: 기술 배포 승인과 결제 검증 결과는 제공되지 않아 검증 불가로 표시
## launch-operations/integrate.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous/artifacts/launch-operations/integrate.json)

세 선행 결과(capacity_calc, policy_comms, schedule_contingency)를 통합해 최종 운영 패키지를 확정했다. 핵심 수치는 초대 800명 × 첫 4시간 문의율 20% = 160건, 상담원 4명 × 시간당 5건 × 4시간 = 80건 처리, 미처리 80건, 전량 처리 총원 8명, 추가 4명(미승인)이다. 확정 정책은 '상담 접수 후 영업일 2일 안에 처리 시작'이며 처리 완료·PG 반영 시간은 보장되지 않는다. 광고 초안의 '즉시 자동 환불'은 정책과 모순되므로 immediate_refund_promised=false로 확정하고 목 15시 광고 마감 전 단일 기준 문서(PMST)로 수정해야 한다.

[운영 일정표(담당·기한·선행조건)] 1) 목 14:00 제품팀 — 확정 사양·알려진 제한 전달(선행 없음, 확정). 2) 목 14:00~15:00 기준 문서 관리자 — PMST 확정(선행 1, 미확인: 사양 내용 미제공). 3) 목 15:00 광고 담당 — 광고 문구 마감, '즉시 자동 환불' 제거(선행 2, 마감 확정). 4) 목 17:00 상담 리드 — 상담 교육(선행 1·2, 확정). 5) 목 18:00 랜딩 담당 — 랜딩 수정 마감(선행 2, 확정). 6) 목요일 중 제품/기술 — 기술 배포 승인(미확인). 7) 목요일 중 결제 담당 — 결제 검증(미확인). 8) 금 10:00 전 팀 — 출시(선행 3·4·5·6·7, 시각 확정이나 6·7 미확인).

[확정/미확인 사항표] 확정: 정책 '영업일 2일 내 처리 시작', 완료·PG 반영 미보장, 마감 목 14/15/17/18시, 출시 금 10시, 초대 800명·문의율 20%·상담원 4명·시간당 5건. 미확인: 기술 배포 승인, 결제 검증, 인원 충원(미승인). 모순: 광고 '즉시 자동 환불' vs 정책 → 목 15시 전 PMST로 수정 필요.

[PMST 구조] policy_id, status(confirmed|unconfirmed), 적용 범위, 승인된 고객 약속 문구, 변경 이력, 승인자, 유효 시점, 미확인/제한 목록. 모든 채널은 PMST 승인 문구만 인용.

[고객 안내문] "환불 요청은 상담 접수 후 영업일 2일 이내에 처리 절차가 시작됩니다. 처리 완료 시점과 PG 반영 시점은 보장되지 않습니다." [FAQ] 즉시 환불 아님, 광고 문구는 수정 중, PG 반영 시점 보장 불가, 기술 배포·결제 검증은 미확인. [상담 분류] (A)환불 정책 (B)처리 지연 (C)PG 반영 (D)광고 불일치 (E)기술/결제 이슈, 우선순위 결제 오류 > 환불 지연 > 일반.

[용량·인력 대안] 800×0.20=160건, 4×5×4=80건, 160−80=80건, 160÷(5×4)=8명, 8−4=4명. 충원 미승인 시 FIFO 접수+우선순위(금전 피해 > 서비스 불가 > 일반)+'영업일 2일 내 처리 시작' 안내+FAQ 셀프서비스.

[3종 비상계획] 1) 광고 지연: 발동=목 15시 문구 미확정/불일치 잔존, 절차=게재 보류→확정 문구 대체→이력 기록. 2) 기술 승인 지연: 발동=배포 승인·결제 검증 미수신, 절차=보류/제한 출시→미확인 고지. 3) 문의 급증: 발동=접수 160건 초과 또는 미처리 80건 초과, 절차=FIFO+우선순위+이월 고지.

[출시 직전 확인표 8항목] 1)사양 전달(목14) 2)PMST 확정 3)광고 문구 확정·'즉시 자동 환불' 제거(목15) 4)상담 교육(목17) 5)랜딩 수정(목18) 6)기술 배포 승인(미수신) 7)결제 검증(미수신) 8)충원 승인(미승인, 추가 4명).

[한계] 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않았고, 기술 배포 승인·결제 검증·충원 승인은 미제공이므로 완료로 간주하지 않는다. 본 산출물은 설계 제안이며 새 정책 결정이 아니다.

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
  "product_spec_delivery_hour": 14,
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_fix_deadline_hour": 18,
  "tech_deploy_approval_status": "unconfirmed",
  "payment_verification_status": "unconfirmed",
  "hiring_approved": false,
  "single_source_document_defined": true,
  "contingency_plans_count": 3,
  "checklist_items": 8,
  "external_contact_performed": false,
  "implementation_performed": false,
  "test_performed": false
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (상담원 4명, 시간당 5건, 지원 창 4시간)
3. backlog_without_hiring = 160 − 80 = 80건 (예상 문의 − 4시간 처리 용량)
4. required_total_agents = 160 ÷ (5 × 4) = 8명 (전량 처리를 위한 총 상담원 수)
5. additional_agents = 8 − 4 = 4명 (필요 총원 − 현재 4명)
6. support_window_hours = 4, launch_hour = 10 (출시 후 첫 4시간 지원 창, 금요일 10시 출시)
7. 확정 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작', '처리 완료 시간과 PG 반영 시간은 보장하지 않는다' → completion_time_guaranteed=false, pg_reflection_guaranteed=false
8. 광고 초안 '즉시 자동 환불'은 정책의 '영업일 2일 내 처리 시작' 및 '완료/PG 미보장'과 모순 → immediate_refund_promised=false
9. 일정 근거: 제품팀 목 14시 사양 전달, 광고 마감 목 15시, 상담 교육 목 17시, 랜딩 수정 마감 목 18시, 금 10시 출시
10. 미확인 근거: 기술 배포 승인과 결제 검증 결과는 미제공, 인원 충원은 미승인 → tech_deploy_approval_status=unconfirmed, payment_verification_status=unconfirmed, hiring_approved=false
11. PMST 구조: policy_id/status/적용 범위/승인 문구/변경 이력/승인자/유효 시점/미확인 목록 필드로 설계
12. 비상계획 3종: 광고 지연(목 15시 트리거), 기술 승인 지연(승인·검증 미수신), 문의 급증(접수 160건 또는 미처리 80건 초과)
13. 출시 직전 확인표 8항목: 사양 전달/PMST 확정/광고 문구/상담 교육/랜딩 수정/기술 배포 승인/결제 검증/충원 승인
14. 실제 구현·테스트·고객 연락·외부 시스템 변경은 수행하지 않음 → implementation_performed=false, test_performed=false, external_contact_performed=false
## launch-operations/policy_comms.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous/artifacts/launch-operations/policy_comms.json)

확정 정책과 광고 초안의 모순을 식별하고, 단일 기준 문서 구조·변경 전달/승인 흐름·고객 안내문/FAQ/상담 분류 기준 초안을 설계했다. 핵심 모순: 확정 정책은 '상담 접수 후 영업일 2일 안에 처리 시작'이며 처리 완료 시점과 PG 반영 시점은 보장하지 않는데, 광고 초안은 '즉시 자동 환불'을 약속한다. '즉시'와 '자동'은 정책의 '영업일 2일 내 처리 시작' 및 '완료/PG 반영 미보장'과 정면으로 충돌하므로 광고 문구는 수정 전까지 게시 불가다. immediate_refund_promised=false로 확정한다.

[단일 기준 문서 구조] 문서명: Policy & Messaging Source of Truth (PMST). 필드: (1) policy_id (예: REFUND-2024-001), (2) status: confirmed | unconfirmed, (3) 적용 범위(대상 서비스/채널/기간), (4) 고객 약속 문구(승인된 정확 문구, 채널별 변형 금지), (5) 변경 이력(버전, 일시, 변경자, 사유), (6) 승인자(제품 정책 오너, 법무/CS 리드), (7) 유효 시점(effective_from, effective_to), (8) 미확인/제한 사항 목록(기술 배포 승인, 결제 검증 결과 등). 모든 채널(광고/상담/랜딩)은 이 문서의 승인 문구만 인용하며, 문서에 없는 약속은 금지한다.

[변경 전달·승인 흐름] 목요일 14:00 제품팀이 확정 사양·알려진 제한 전달 → 14:00~14:30 정책 오너가 PMST에 반영하고 status=confirmed로 승인 → 14:30 광고/상담/랜딩 담당자에게 배포 → 15:00 광고 문구 마감(PMST 문구만 사용, '즉시 자동 환불' 삭제) → 17:00 상담 교육(PMST 기반 스크립트·FAQ) → 18:00 랜딩 수정(PMST 문구 반영). 각 단계는 승인 게이트: 정책 오너 승인 없이는 다음 단계 진행 불가. 미확인 항목(기술 배포 승인, 결제 검증)은 PMST에 unconfirmed로 표기하고 고객 약속에 포함하지 않는다.

[고객 안내문 초안] "환불 요청은 상담 접수 후 영업일 2일 이내에 처리 절차가 시작됩니다. 처리 완료 시점과 결제사(PG) 반영 시점은 개별 결제 상황에 따라 달라질 수 있으며, 정확한 시점은 보장되지 않습니다. 진행 상황은 접수 채널을 통해 안내드립니다."

[FAQ 초안] Q1. 즉시 환불되나요? A: 아닙니다. 접수 후 영업일 2일 내 처리 시작이며 완료/PG 반영은 보장되지 않습니다. Q2. 왜 광고와 다른가요? A: 광고 초안 문구는 확정 정책과 달라 수정 중입니다. Q3. 언제 돈이 돌아오나요? A: PG 반영 시점은 보장 불가하며, 처리 시작 후 PG 정책에 따릅니다. Q4. 기술 배포/결제 검증은 완료됐나요? A: 아직 미확인이며 확인 전까지 약속하지 않습니다.

[상담 분류 기준] 카테고리: (A) 환불 정책 문의 → PMST 문구 안내, (B) 처리 지연/미완료 → 접수번호 확인 후 영업일 2일 기준 안내, (C) PG 반영 문의 → 보장 불가 안내, (D) 광고 문구 불일치 제보 → 정정 안내 및 기록, (E) 기술/결제 이슈 → 미확인 상태 안내 후 에스컬레이션. 우선순위: 결제 오류 > 환불 지연 > 일반 문의.

[미확인/모순 요약] 모순: 광고 '즉시 자동 환불' vs 정책 '영업일 2일 내 처리 시작, 완료/PG 미보장'. 미확인: 기술 배포 승인, 결제 검증 결과. 새 정책 결정이나 승인 완료 표현은 하지 않았으며, 실제 고객 연락·외부 시스템 변경도 수행하지 않았다.

### Facts

```json
{
  "immediate_refund_promised": false,
  "policy_processing_start": "영업일 2일 이내",
  "completion_time_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_update_hour": 18,
  "product_spec_delivery_hour": 14,
  "tech_deploy_approval_status": "unconfirmed",
  "payment_verification_status": "unconfirmed",
  "single_source_document_defined": true,
  "approval_gate_defined": true,
  "customer_notice_drafted": true,
  "faq_drafted": true,
  "support_classification_drafted": true,
  "new_policy_decided": false,
  "external_contact_performed": false
}
```

### Evidence

1. 확정 정책 원문: '상담 접수 후 영업일 2일 안에 처리 시작', '처리 완료 시간과 PG 반영 시간은 보장하지 않는다'.
2. 광고 초안 원문: '즉시 자동 환불' → '즉시'(즉시성)와 '자동'이 정책의 '영업일 2일 내 처리 시작' 및 '완료/PG 미보장'과 모순되므로 immediate_refund_promised=false.
3. 일정 근거: 제품팀 목요일 14시 사양 전달, 광고 마감 목요일 15시, 상담 교육 목요일 17시, 랜딩 수정 마감 목요일 18시.
4. 미확인 근거: '기술 배포 승인과 결제 검증 결과는 아직 미제공'이라고 명시됨.
5. 단일 기준 문서 구조는 정책 ID/확정·미확인/적용 범위/고객 약속 문구/변경 이력/승인자/유효 시점 필드로 설계함.
6. 승인 흐름은 14:00 전달 → 14:00~14:30 PMST 반영·승인 → 15:00 광고 → 17:00 상담 → 18:00 랜딩 순차 반영 및 승인 게이트로 설계함.
7. 고객 안내문·FAQ·상담 분류 기준 초안은 완료/PG 반영 시점을 보장하지 않고 미확인 기술 배포·결제 검증을 명시하도록 작성함.
8. 실제 고객 연락, 외부 시스템 변경, 충원·환불·배포 완료 표현은 수행하지 않음.
## launch-operations/schedule_contingency/capacity_calc.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous/artifacts/launch-operations/schedule_contingency/capacity_calc.json)

첫 4시간 용량·인력 계산과 충원 불가 시 운영 대안을 정리했다. 초대 800명의 20%인 160건이 첫 4시간 예상 문의이며, 상담원 4명이 시간당 5건씩 4시간 처리하면 80건 처리 가능해 80건이 미처리된다. 전량 처리를 위해서는 총 8명이 필요하므로 추가 4명 충원이 필요하지만 충원은 아직 미승인 상태이므로, 충원 불가를 전제로 대기열·우선순위·고객 안내 대안을 제시한다. 광고 초안의 '즉시 자동 환불'은 확정 정책('영업일 2일 내 처리 시작')과 다르므로 immediate_refund_promised=false로 표기한다. 지원 창은 출시 후 첫 4시간, 출시 시각은 금요일 10시다. 실제 고객 연락·외부 시스템 변경·충원 승인·배포 승인은 수행하지 않았고, 기술 배포 승인과 결제 검증 결과는 미제공 상태로 미검증이다.

[대기열·우선순위·고객 안내 대안(충원 미승인 전제)]
1) 대기열 운영: 접수 순서(FIFO) 기반 티켓 번호 부여, 자동 접수 확인 메시지 즉시 발송, 예상 응답 시간(대기 인원/시간당 처리량 기반) 안내. 4시간 내 80건 미처리분은 다음 영업시간으로 이월하고 이월 사실을 고지.
2) 우선순위 기준: (a) 결제 오류·중복 결제 등 금전 피해 가능 건 최우선, (b) 서비스 이용 불가(로그인·핵심 기능 장애) 차순위, (c) 일반 문의·정책 문의 후순위. 우선순위는 접수 시 자동 분류 + 상담원 수동 재분류 허용.
3) 고객 안내 대안: 자동 접수 확인, 예상 응답 시간 안내, FAQ 셀프서비스 링크, 정책 문구는 '영업일 2일 내 처리 시작'으로 통일(처리 완료·PG 반영 시간 미보장 명시).
4) 단일 기준 문서: 광고·상담·랜딩이 동일한 확정 정책 문구를 사용하도록 기준 문서 1개를 두고, 변경은 제품팀(목 14시 사양 전달) → 기준 문서 반영 → 광고/상담/랜딩 담당 승인 흐름으로 전달한다. 광고 문구 마감 목 15시, 상담 교육 목 17시, 랜딩 수정 마감 목 18시에 맞춰 기준 문서 확정이 선행되어야 한다.

[미확인 사항] 기술 배포 승인, 결제 검증 결과, 인원 충원 승인은 미제공이며 완료로 간주하지 않는다. 실제 구현·테스트는 수행하지 않았으므로 미검증이다.

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
  "deployment_approval_received": false,
  "payment_verification_received": false,
  "policy_processing_start_business_days": 2,
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_fix_deadline_hour": 18,
  "product_spec_delivery_hour": 14
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (상담원 4명, 시간당 5건, 지원 창 4시간)
3. backlog_without_hiring = 160 - 80 = 80건 (예상 문의 - 4시간 처리 용량)
4. required_total_agents = 160 / (5 × 4) = 8명 (전량 처리를 위한 총 상담원 수)
5. additional_agents = 8 - 4 = 4명 (필요 총원 - 현재 4명)
6. support_window_hours = 4 (출시 후 첫 4시간 기준)
7. launch_hour = 10 (금요일 10시 출시)
8. immediate_refund_promised = false: 광고 초안의 '즉시 자동 환불'은 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작'과 불일치하며, 처리 완료·PG 반영 시간은 보장되지 않음
9. 충원 미승인: 인원 충원은 아직 승인되지 않았으므로 추가 4명 충원을 완료로 간주하지 않고 대기열·우선순위·고객 안내 대안을 제시함
10. 미확인: 기술 배포 승인과 결제 검증 결과는 제공되지 않았고, 실제 고객 연락·외부 시스템 변경·구현/테스트는 수행하지 않아 미검증임
## launch-operations/schedule_contingency/contingency_plan.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous/artifacts/launch-operations/schedule_contingency/contingency_plan.json)

선행 capacity_calc의 수치를 인용해 3종 비상계획(광고 지연·기술 승인 지연·문의 급증)과 출시 직전 확인표를 작성했다. 모든 비상계획에 발동 조건·담당·대응 절차·고객 안내 문구를 명시했고, 미승인 충원·환불·배포는 완료로 표기하지 않았다.

[공통 전제] 출시: 금요일 10시. 지원 창: 출시 후 첫 4시간. 예상 문의 160건, 4시간 처리 용량 80건, 미처리 80건, 전량 처리 총원 8명, 추가 4명(미승인). 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장. 광고 초안 '즉시 자동 환불'은 정책 불일치(immediate_refund_promised=false).

[비상계획 1: 광고 지연]
- 발동 조건: 목 15시(ad_copy_deadline_hour=15)까지 광고 문구가 확정 기준 문서 문구로 확정·승인되지 않거나, '즉시 자동 환불' 등 정책 불일치 표현이 남아 있을 때.
- 담당: 광고 담당(집행) + 기준 문서 관리자(문구 확정·승인).
- 대응 절차: (1) 승인 전 게재 보류. (2) 기준 문서의 확정 문구로 대체하고 '즉시 자동 환불' 표현 제거. (3) 정책 문구를 '상담 접수 후 영업일 2일 안에 처리 시작(처리 완료·PG 반영 시간 미보장)'으로 통일. (4) 15시까지 미확정이면 해당 슬롯 광고를 정책 중립 문구(서비스 소개·문의 채널 안내)로 축소 게재. (5) 변경 이력과 승인자·시각을 기준 문서에 기록.
- 고객 안내 문구: "환불은 상담 접수 후 영업일 2일 안에 처리 시작되며, 처리 완료 및 결제수단(PG) 반영 시점은 보장되지 않습니다."

[비상계획 2: 기술 승인 지연]
- 발동 조건: 출시 전까지 기술 배포 승인 미수신(deployment_approval_received=false) 또는 결제 검증 결과 미수신(payment_verification_received=false).
- 담당: 제품/기술 담당(승인·검증 상태 판단), 랜딩 담당(상태 고지), 상담 리드(스크립트 반영).
- 대응 절차: (1) 승인·검증 미수신 시 전면 출시 보류 또는 제한 출시(결제 기능 제외/읽기 전용) 중 제품 담당이 결정. (2) 랜딩에 현재 상태(점검/제한 운영) 고지. (3) 상담 스크립트에 미확인 사항(배포 승인·결제 검증 미완) 명시, 확정된 사실만 안내. (4) 승인·검증 수신 시점을 기준 문서에 기록하고 그 전까지 '완료' 표현 금지.
- 고객 안내 문구: "현재 일부 기능의 배포 승인 및 결제 검증이 진행 중입니다. 확인된 범위만 안내드리며, 결제 관련 문의는 접수 후 순차 처리됩니다."

[비상계획 3: 문의 급증]
- 발동 조건: 첫 4시간 접수 160건 초과(expected_contacts=160) 또는 미처리 80건 초과(backlog_without_hiring=80).
- 담당: 상담 리드.
- 대응 절차: (1) FIFO 접수 순서 유지 + 우선순위 재적용(금전 피해 가능 건 > 서비스 이용 불가 > 일반·정책 문의). (2) 4시간 내 미처리분은 다음 영업시간으로 이월하고 이월 사실 고지. (3) FAQ 셀프서비스 링크·자동 접수 확인·예상 응답 시간 안내로 유입 분산. (4) 추가 4명 충원은 미승인(hiring_approved=false)이므로 충원을 완료로 표기하지 않고, 필요 총원 8명·추가 4명 소요를 승인 요청 사항으로만 기록.
- 고객 안내 문구: "문의가 많아 접수 순서대로 처리 중입니다. 예상 응답 시간을 안내드리며, 금전 피해 가능 건을 우선 처리합니다. 미처리분은 다음 영업시간으로 이월될 수 있습니다."

[출시 직전 확인표(담당·기한·선행조건)]
1) 제품 사양·알려진 제한 전달 — 제품팀, 목 14시(product_spec_delivery_hour=14), 선행조건: 없음(기준 문서 확정의 선행).
2) 기준 문서 확정(단일 문구) — 기준 문서 관리자, 목 14시 이후~15시 이전, 선행조건: 1).
3) 광고 문구 확정·승인 — 광고 담당, 목 15시(ad_copy_deadline_hour=15), 선행조건: 2). '즉시 자동 환불' 제거 확인.
4) 상담 교육 — 상담 리드, 목 17시(support_training_hour=17), 선행조건: 2).
5) 랜딩 수정 마감 — 랜딩 담당, 목 18시(landing_fix_deadline_hour=18), 선행조건: 2).
6) 기술 배포 승인 — 제품/기술 담당, 기한 미정, 상태: 미수신(deployment_approval_received=false).
7) 결제 검증 — 결제 담당, 기한 미정, 상태: 미수신(payment_verification_received=false).
8) 인원 충원 승인 — 미승인(hiring_approved=false), 필요 추가 4명, 완료 표기 금지.
검증: 미승인 충원·환불·배포를 완료로 쓰지 않았는지 확인 — 충원/배포/결제검증은 '미수신·미승인'으로만 표기, 환불은 '영업일 2일 내 처리 시작'으로만 표기.

[미검증] 실제 고객 연락, 외부 시스템 변경, 구현·테스트는 수행하지 않았다. 기술 배포 승인·결제 검증·충원 승인은 미제공이며 완료로 간주하지 않는다.

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
  "landing_fix_deadline_hour": 18,
  "product_spec_delivery_hour": 14,
  "hiring_approved": false,
  "deployment_approval_received": false,
  "payment_verification_received": false,
  "policy_processing_start_business_days": 2,
  "contingency_plans_count": 3,
  "ad_delay_trigger_hour": 15,
  "surge_trigger_contacts": 160,
  "surge_trigger_backlog": 80,
  "checklist_items": 8
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20%)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
3. backlog_without_hiring = 160 - 80 = 80건
4. required_total_agents = 160 / (5 × 4) = 8명, additional_agents = 8 - 4 = 4명
5. 광고 지연 발동 조건: 목 15시(ad_copy_deadline_hour=15) 문구 미확정/미승인 또는 '즉시 자동 환불' 잔존 — 광고 초안 표현이 확정 정책('영업일 2일 내 처리 시작')과 불일치
6. 기술 승인 지연 발동 조건: deployment_approval_received=false 또는 payment_verification_received=false (제공 자료상 미수신)
7. 문의 급증 발동 조건: 접수 160건 초과 또는 미처리 80건 초과 (capacity_calc 수치 인용)
8. 확인표 기한: 제품 사양 목14시, 광고 목15시, 상담교육 목17시, 랜딩 목18시 — 기준 문서 확정이 광고·상담·랜딩의 선행조건
9. 미승인 항목: hiring_approved=false(추가 4명), deployment_approval_received=false, payment_verification_received=false — 완료로 표기하지 않음
10. 실제 고객 연락·외부 시스템 변경·구현/테스트는 수행하지 않아 미검증
## launch-operations/schedule_contingency/schedule_table.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous/artifacts/launch-operations/schedule_contingency/schedule_table.json)

운영 일정표(담당·기한·선행조건)와 확정/미확인 사항표를 작성했다.

[운영 일정표]
1) 목 14:00 — 제품팀: 확정 사양·알려진 제한 전달. 선행조건: 없음. 상태: 확정(전달 예정).
2) 목 14:00~15:00 — 광고/상담/랜딩 담당: 단일 기준 문서(정책·문구·FAQ) 확정본 반영. 선행조건: 1) 제품팀 사양 전달. 상태: 미확인(사양 내용 미제공).
3) 목 15:00 — 광고업체: 광고 문구 마감. 선행조건: 2) 단일 기준 문서. 상태: 확정(마감 시각).
4) 목 17:00 — 상담팀: 상담 교육 완료. 선행조건: 2) 단일 기준 문서, 1) 사양 전달. 상태: 확정(교육 시각).
5) 목 18:00 — 랜딩 담당: 랜딩 수정 마감. 선행조건: 2) 단일 기준 문서. 상태: 확정(마감 시각).
6) 목요일 중 — 기술팀: 기술 배포 승인. 선행조건: 없음(외부 승인). 상태: 미확인(미제공).
7) 목요일 중 — 결제팀: 결제 검증 결과. 선행조건: 없음(외부 검증). 상태: 미확인(미제공).
8) 금 10:00 — 전 팀: 신규 서비스 출시. 선행조건: 3) 광고 문구, 4) 상담 교육, 5) 랜딩 수정, 6) 기술 배포 승인, 7) 결제 검증. 상태: 확정(출시 시각)이나 선행조건 중 6)·7) 미확인.

[확정/미확인 사항표]
확정: 상담 접수 후 영업일 2일 내 처리 시작. 처리 완료 시간·PG 반영 시간 미보장. 마감 시각(목 14/15/17/18시), 출시(금 10시). 초대 800명, 첫 4시간 문의율 20%, 상담원 4명, 시간당 5건.
미확인: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인(미승인).

[모순 명시] 광고 초안의 '즉시 자동 환불'은 확정 정책('영업일 2일 내 처리 시작', 완료·PG 반영 미보장)과 다르다. 광고 문구 마감(목 15시) 전에 단일 기준 문서로 수정 필요.

[용량 계산] 예상 문의 = 800 × 0.20 = 160건. 4시간 처리 용량 = 4명 × 5건/시 × 4시간 = 80건. 미처리 = 160 − 80 = 80건. 필요 총 상담원 = 160 ÷ (5 × 4) = 8명. 추가 인원 = 8 − 4 = 4명. 지원 창(4시간) = 4시간. 출시 시각 = 10시.

[미승인 충원 시 대안] 대기열·우선순위(결제/환불 문의 우선), 고객 안내문(영업일 2일 내 처리 시작, 완료·PG 반영 미보장), FAQ, 상담 분류 기준을 단일 기준 문서에 포함. 비상 계획: 광고 지연 시 문구 마감 연장·출시 후 순차 게시, 기술 승인 지연 시 출시 보류 또는 제한 오픈, 문의 급증 시 우선순위·안내 강화.

[출시 직전 확인표] 6)·7) 승인/검증 확인, 단일 기준 문서 반영 확인, 광고·상담·랜딩 문구 일치 확인, 상담 인력·교육 확인. 실제 고객 연락·외부 시스템 변경·테스트는 수행하지 않음(미검증).

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

1. 예상 문의: 800명 × 20% = 160건
2. 4시간 처리 용량: 상담원 4명 × 시간당 5건 × 4시간 = 80건
3. 미처리량: 160 − 80 = 80건
4. 필요 총 상담원: 160 ÷ (5 × 4) = 8명
5. 추가 인원: 8 − 4 = 4명
6. 지원 창: 출시 후 첫 4시간 = 4시간
7. 출시 시각: 금요일 10시 = 10
8. 광고 초안 '즉시 자동 환불' vs 확정 정책 '영업일 2일 내 처리 시작, 완료·PG 반영 미보장' → immediate_refund_promised=false
9. 미확인: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인(미승인) — 완료로 기재하지 않음
10. 마감: 목 14시 제품 사양, 목 15시 광고 문구, 목 17시 상담 교육, 목 18시 랜딩 수정, 금 10시 출시
11. 실제 고객 연락·외부 시스템 변경·테스트 미수행(미검증)
## launch-operations/schedule_contingency.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous/artifacts/launch-operations/schedule_contingency.json)

세 하위 결과(capacity_calc, schedule_table, contingency_plan)를 통합해 담당·기한·선행조건이 연결된 운영 일정, 확정/미확인 사항표, 3종 비상계획, 출시 직전 확인표를 하나의 패키지로 정리했다. 핵심 수치는 초대 800명 × 첫 4시간 문의율 20% = 160건, 상담원 4명 × 시간당 5건 × 4시간 = 80건 처리, 미처리 80건, 전량 처리 총원 8명, 추가 4명(미승인)이다. 확정 정책은 '상담 접수 후 영업일 2일 안에 처리 시작'이며 처리 완료·PG 반영 시간은 보장되지 않는다. 광고 초안의 '즉시 자동 환불'은 이 정책과 불일치하므로 immediate_refund_promised=false로 표기하고 광고 문구 마감(목 15시) 전에 단일 기준 문서로 수정해야 한다.

[운영 일정표(담당·기한·선행조건)]
1) 목 14:00 제품팀 — 확정 사양·알려진 제한 전달. 선행: 없음. 상태: 확정(전달 예정).
2) 목 14:00~15:00 기준 문서 관리자 — 단일 기준 문서(정책·문구·FAQ·상담 분류) 확정. 선행: 1). 상태: 미확인(사양 내용 미제공).
3) 목 15:00 광고 담당 — 광고 문구 마감. 선행: 2). '즉시 자동 환불' 제거 확인. 상태: 확정(마감 시각).
4) 목 17:00 상담 리드 — 상담 교육 완료. 선행: 1), 2). 상태: 확정(교육 시각).
5) 목 18:00 랜딩 담당 — 랜딩 수정 마감. 선행: 2). 상태: 확정(마감 시각).
6) 목요일 중 제품/기술 담당 — 기술 배포 승인. 선행: 없음(외부 승인). 상태: 미확인(미제공).
7) 목요일 중 결제 담당 — 결제 검증 결과. 선행: 없음(외부 검증). 상태: 미확인(미제공).
8) 금 10:00 전 팀 — 신규 서비스 출시. 선행: 3),4),5),6),7). 상태: 출시 시각 확정이나 6)·7) 미확인.

[확정/미확인 사항표]
확정: 정책 '상담 접수 후 영업일 2일 내 처리 시작', 처리 완료·PG 반영 시간 미보장. 마감 목 14/15/17/18시, 출시 금 10시. 초대 800명, 문의율 20%, 상담원 4명, 시간당 5건.
미확인: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인(미승인).
모순: 광고 초안 '즉시 자동 환불' vs 확정 정책 → 목 15시 전 단일 기준 문서로 수정 필요.

[용량 계산] 예상 문의 800×0.20=160건. 4시간 용량 4×5×4=80건. 미처리 160−80=80건. 필요 총원 160÷(5×4)=8명. 추가 8−4=4명. 지원 창 4시간, 출시 10시.

[충원 미승인 시 대안] FIFO 접수+자동 접수 확인+예상 응답 시간 안내, 미처리분 다음 영업시간 이월 고지. 우선순위: 금전 피해 가능 건 > 서비스 이용 불가 > 일반·정책 문의. 안내 문구는 '영업일 2일 내 처리 시작(완료·PG 반영 미보장)'으로 통일, FAQ 셀프서비스 병행.

[3종 비상계획]
1) 광고 지연: 발동=목 15시까지 문구 미확정/미승인 또는 '즉시 자동 환불' 잔존. 담당=광고 담당+기준 문서 관리자. 절차=게재 보류→확정 문구 대체→중립 문구 축소 게재→이력 기록. 안내="환불은 상담 접수 후 영업일 2일 안에 처리 시작되며 완료·PG 반영 시점은 보장되지 않습니다."
2) 기술 승인 지연: 발동=배포 승인 또는 결제 검증 미수신. 담당=제품/기술+랜딩+상담 리드. 절차=전면 보류 또는 제한 출시 결정→랜딩 상태 고지→상담 스크립트에 미확인 명시→수신 시점 기록. 안내="일부 기능 배포 승인·결제 검증이 진행 중이며 확인된 범위만 안내합니다."
3) 문의 급증: 발동=접수 160건 초과 또는 미처리 80건 초과. 담당=상담 리드. 절차=FIFO+우선순위 재적용→미처리분 이월 고지→FAQ·자동 안내로 분산→추가 4명은 승인 요청으로만 기록. 안내="접수 순서대로 처리 중이며 금전 피해 가능 건을 우선, 미처리분은 다음 영업시간으로 이월될 수 있습니다."

[출시 직전 확인표] 1) 제품 사양 전달(목14) 2) 기준 문서 확정 3) 광고 문구 확정·'즉시 자동 환불' 제거(목15) 4) 상담 교육(목17) 5) 랜딩 수정(목18) 6) 기술 배포 승인(미수신) 7) 결제 검증(미수신) 8) 인원 충원 승인(미승인, 추가 4명). 검증: 미승인 충원·배포·결제검증은 '미수신·미승인'으로만, 환불은 '영업일 2일 내 처리 시작'으로만 표기.

[한계] 실제 고객 연락, 외부 시스템 변경, 구현·테스트는 수행하지 않았고 기술 배포 승인·결제 검증·충원 승인은 미제공이므로 완료로 간주하지 않는다.

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
  "product_spec_delivery_hour": 14,
  "ad_copy_deadline_hour": 15,
  "support_training_hour": 17,
  "landing_fix_deadline_hour": 18,
  "hiring_approved": false,
  "deployment_approval_received": false,
  "payment_verification_received": false,
  "contingency_plans_count": 3,
  "checklist_items": 8,
  "surge_trigger_contacts": 160,
  "surge_trigger_backlog": 80,
  "ad_delay_trigger_hour": 15
}
```

### Evidence

1. expected_contacts = 800 × 0.20 = 160건 (초대 800명, 첫 4시간 문의율 20% 가정)
2. four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
3. backlog_without_hiring = 160 − 80 = 80건
4. required_total_agents = 160 ÷ (5 × 4) = 8명, additional_agents = 8 − 4 = 4명
5. support_window_hours = 4 (출시 후 첫 4시간), launch_hour = 10 (금요일 10시)
6. 확정 정책 '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장 → 광고 초안 '즉시 자동 환불'과 불일치, immediate_refund_promised=false
7. 마감: 목 14시 제품 사양, 목 15시 광고 문구, 목 17시 상담 교육, 목 18시 랜딩 수정, 금 10시 출시
8. 미확인: 기술 배포 승인(deployment_approval_received=false), 결제 검증(payment_verification_received=false), 인원 충원(hiring_approved=false) — 완료로 표기하지 않음
9. 비상계획 발동 조건: 광고 지연=목 15시 문구 미확정/불일치 표현 잔존, 기술 지연=승인·검증 미수신, 문의 급증=접수 160건 초과 또는 미처리 80건 초과
10. 실제 고객 연락·외부 시스템 변경·구현/테스트는 수행하지 않아 미검증