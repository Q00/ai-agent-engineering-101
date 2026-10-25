# launch-operations / baseline / 1회

상태: failed. 필수 facts: 0/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## launch-operations.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-launch-operations-baseline/artifacts/launch-operations.json)

ValueError: child failed or blocked; partial results retained

## launch-operations/capacity_calc.json

Worker: B; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-launch-operations-baseline/artifacts/launch-operations/capacity_calc.json)

용량 계산: 초대 800명 × 20% = 160건 문의, 상담원 4명 × 5건/시간 × 4시간 = 80건 처리, 미처리 80건. 전량 처리를 위해서는 총 8명(160/(5×4)) 필요, 추가 4명 충원 필요. 충원 미승인 전제에서는 4시간 내 80건만 처리 가능하므로 나머지 80건은 대기열로 이관된다. 대기열 운영안: (1) 접수 순서 원칙이되 긴급도 우선순위 적용 — P1 결제 오류·중복 청구·이중 결제(즉시 상담원 연결), P2 서비스 이용 장애, P3 일반 문의·정책 안내. (2) 예상 대기 안내: 접수 시 순번과 예상 시작 시각을 고지하고, 4시간 창 종료 시점에 미처리분에 대해 '영업일 2일 내 처리 시작' 정책을 재고지. (3) 자동 환불은 보장되지 않음을 명시(immediate_refund_promised=false). (4) 콜백/비동기 접수 채널로 대기열 폭주 완화. 모든 수치는 가정 기반 추정이며 실제 문의율은 출시 후 실측으로 보정 필요.

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
  "agents_on_hand": 4,
  "per_agent_hourly_rate": 5,
  "invite_target": 800,
  "contact_rate": 0.2,
  "backlog_ratio": 0.5,
  "hiring_approved": false,
  "priority_p1": "결제 오류·중복 청구",
  "priority_p2": "서비스 이용 장애",
  "priority_p3": "일반 문의·정책 안내",
  "sla_policy": "상담 접수 후 영업일 2일 내 처리 시작",
  "completion_time_guaranteed": false,
  "pg_reflection_time_guaranteed": false
}
```

### Evidence

**1.** expected_contacts = 800 × 0.20 = 160건

**2.** four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건

**3.** backlog_without_hiring = 160 - 80 = 80건

**4.** required_total_agents = 160 / (5 × 4) = 8명

**5.** additional_agents = 8 - 4 = 4명

**6.** support_window_hours = 4 (출시 후 첫 4시간 가정)

**7.** launch_hour = 10 (금요일 10시 출시)

**8.** 확정 정책: '상담 접수 후 영업일 2일 안에 처리 시작', 처리 완료·PG 반영 시간 미보장 → immediate_refund_promised=false

**9.** 인원 충원 미승인 상태이므로 추가 4명 확보를 전제로 한 처리량 보장 불가, 대기열·우선순위 운영으로 대응

**10.** 미검증: 실제 문의율 20%는 가정치로 실측 데이터 없음, 상담원 시간당 5건 처리율도 가정치

## launch-operations/customer_comms.json

Worker: ; 상태: blocked
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-launch-operations-baseline/artifacts/launch-operations/customer_comms.json)

predecessor did not succeed

## launch-operations/policy_consistency.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-launch-operations-baseline/artifacts/launch-operations/policy_consistency.json)

JSONDecodeError: Unterminated string starting at: line 1 column 3776 (char 3775)

## launch-operations/tech_risk_checklist.json

Worker: ; 상태: blocked
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-launch-operations-baseline/artifacts/launch-operations/tech_risk_checklist.json)

predecessor did not succeed
