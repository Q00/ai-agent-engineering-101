# launch-operations / homogeneous / 2회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## launch-operations.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-launch-operations-homogeneous/artifacts/launch-operations.json)

금요일 10시 출시 통합 운영 패키지(설계 제안). 실제 구현·외부 연락·시스템 변경·테스트는 수행하지 않았고, 제공 자료 기반 계산과 문서/표/코드 구조 제안만 제시한다.

[1] 용량 계산(제공 수치)
- expected_contacts = 800 × 0.20 = 160건
- four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
- backlog_without_hiring = 160 − 80 = 80건
- required_total_agents = 160 ÷ (5×4) = 8명
- additional_agents = 8 − 4 = 4명
- support_window_hours = 4, launch_hour = 10
- immediate_refund_promised = false (광고 초안 문구는 확정 정책과 모순)

[2] 모순 명시
- 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작'. 완료 시점·PG 반영 시점 미보장.
- 광고 초안: '즉시 자동 환불' → 처리 시작 SLA와 완료/반영 미보장을 위반하는 약속. 즉시 환불은 승인된 정책이 아니므로 광고 문구에서 삭제/수정 필요.
- 미확인: 기술 배포 승인, 결제(PG) 검증 결과, 인원 충원 승인 모두 미제공.

[3] 단일 기준 문서(SSoT) 설계
- 문서명: launch_policy_sot.md (버전·승인자·유효기간 필드)
- 필수 필드: 정책ID, 고객 노출 문구, 내부 처리 SLA, 보장/미보장 항목, 승인자, 변경 이력.
- 변경 전달·승인 흐름(제안): 변경 요청 → 정책 오너(제품팀) 검토 → 승인 → SSoT 갱신 → 광고/상담/랜딩 담당자에게 동시 통보 → 각 채널 반영 확인. 승인 없는 문구는 게시 금지.
- 코드 구조(의사): policy = {id, customer_text, sla_start_business_days:2, completion_guaranteed:false, pg_reflection_guaranteed:false, approved_by, version}; assert '즉시' not in customer_text.

[4] 운영 일정(목요일, 담당·기한·선행조건)
- 14:00 제품팀: 확정 사양·제한 전달 (선행: 없음) → 모든 후속 작업의 입력.
- 14:00~15:00 정책 오너: SSoT 확정 (선행: 제품팀 사양).
- 15:00 광고업체: 문구 마감 (선행: SSoT 승인). '즉시 자동 환불' 제거 확인.
- 17:00 상담팀: 교육 (선행: SSoT + FAQ/분류 기준).
- 18:00 랜딩: 수정 마감 (선행: SSoT + 광고 문구 일치 확인).
- 출시 전: 기술 배포 승인·결제 검증 결과 확인(미제공, 게이트).

[5] 확정/미확인 사항표(텍스트)
- 확정: 정책 SLA(영업일 2일 내 처리 시작), 완료·PG 반영 미보장, 초대 800명, 문의율 20%, 상담원 4명·시간당 5건.
- 미확인: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인, 광고 최종 문구.

[6] 고객 안내문·FAQ·상담 분류 기준(제안)
- 안내문: '문의 접수 후 영업일 2일 안에 처리 시작합니다. 처리 완료와 PG 반영 시점은 보장되지 않습니다.'
- FAQ: Q 즉시 환불? A 아니오, 접수 후 영업일 2일 내 처리 시작. Q 완료 시점? A 미보장.
- 분류: (a) 환불/결제, (b) 기술 오류, (c) 일반 문의, (d) 불만/에스컬레이션. 우선순위: 결제·기술 오류 > 환불 > 일반.

[7] 충원 불가 대안
- 대기열: 접수 순 + 우선순위 태그. 4시간 내 80건 미처리 예상 → 영업일 2일 SLA 내 순차 처리.
- 우선순위: 결제/기술 > 환불 > 일반. 자동 응답으로 접수 확인·SLA 안내.
- 안내: 대기 사유와 예상 처리 시작일 고지.

[8] 비상 계획
- 광고 지연: SSoT 미승인 시 광고 게시 보류, 랜딩은 정책 문구만 게시.
- 기술 승인 지연: 출시 연기 또는 제한 오픈, 고객 공지.
- 문의 급증: 우선순위·대기열 강화, 추가 인원은 승인 전까지 미배치.

[9] 출시 직전 확인표
- [ ] SSoT 승인·버전 확인
- [ ] 광고/랜딩/상담 문구 일치
- [ ] 기술 배포 승인(미확인)
- [ ] 결제 검증(미확인)
- [ ] 인원 충원 승인(미확인)
- [ ] 상담 교육 완료

검증 상태: 실제 구현·외부 연락·시스템 변경·테스트는 미수행(미검증). 위 내용은 설계 제안이다.

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
  "invitees": 800,
  "contact_rate": 0.2,
  "agents_current": 4,
  "contacts_per_agent_hour": 5,
  "hiring_approved": false,
  "tech_deploy_approved": false,
  "payment_verification_received": false,
  "policy_sla_business_days": 2,
  "completion_guaranteed": false,
  "pg_reflection_guaranteed": false,
  "ad_copy_deadline_hour": 15,
  "training_deadline_hour": 17,
  "landing_deadline_hour": 18,
  "product_spec_hour": 14,
  "actual_implementation_done": false,
  "external_contact_done": false,
  "system_change_done": false,
  "tests_executed": false
}
```

### Evidence

**1.** expected_contacts = 800 × 0.20 = 160 (제공: 초대 800명, 첫 4시간 문의율 20%)

**2.** four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80 (제공: 상담원 4명, 시간당 5건, 4시간)

**3.** backlog_without_hiring = 160 − 80 = 80

**4.** required_total_agents = 160 ÷ (5×4) = 8

**5.** additional_agents = 8 − 4 = 4

**6.** support_window_hours = 4, launch_hour = 10 (제공: 첫 4시간, 금요일 10시 출시)

**7.** immediate_refund_promised = false: 확정 정책은 '영업일 2일 내 처리 시작'이며 완료·PG 반영 미보장, 광고 초안 '즉시 자동 환불'과 모순

**8.** 미확인 항목: 기술 배포 승인, 결제 검증, 인원 충원 승인은 제공 자료에 없음

**9.** 실제 구현·외부 연락·시스템 변경·테스트는 수행하지 않음(미검증), 문서/표/코드 구조는 설계 제안
