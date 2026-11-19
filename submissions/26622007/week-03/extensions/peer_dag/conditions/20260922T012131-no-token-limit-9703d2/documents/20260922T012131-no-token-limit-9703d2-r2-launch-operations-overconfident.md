# launch-operations / overconfident / 2회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## launch-operations.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-launch-operations-overconfident/artifacts/launch-operations.json)

금요일 10시 출시 통합 운영 패키지(설계 제안). 실제 고객 연락·외부 시스템 변경·배포·결제 검증·충원은 수행하지 않았고, 제공 자료 기반 계산과 문서 설계만 제시한다.

[1] 용량 계산(제공 가정 기반)
- expected_contacts = 800 × 0.20 = 160건
- four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건
- backlog_without_hiring = 160 − 80 = 80건
- required_total_agents = 160 ÷ (5×4) = 8명
- additional_agents = 8 − 4 = 4명
- support_window_hours = 4, launch_hour = 10
- immediate_refund_promised = false (광고 초안 '즉시 자동 환불'은 확정 정책과 불일치)

[2] 확정/미확인 사항표
확정: ①상담 접수 후 영업일 2일 안에 처리 시작 ②처리 완료 시간 미보장 ③PG 반영 시간 미보장 ④광고 문구 마감 목요일 15시 ⑤상담 교육 목요일 17시 ⑥랜딩 수정 마감 목요일 18시 ⑦제품팀 목요일 14시 확정 사양·제한 전달 ⑧출시 금요일 10시.
미확인: ①기술 배포 승인 ②결제 검증 결과 ③추가 4명 충원 승인 ④제품팀 전달물의 최종 내용.

[3] 단일 기준 문서(SSoT) 설계
- 문서명: launch-messaging-sot (버전 관리). 필수 필드: 정책 문구, 금지 표현 목록('즉시 자동 환불' 등), 채널별 적용 범위(광고/상담/랜딩), 승인자, 유효기간.
- 변경 흐름: 변경 요청 → 운영·커뮤니케이션(C) 검토 → 제품 정책 소유자 승인 → SSoT 갱신 → 광고/상담/랜딩 담당자에게 동일 채널(단일 공지 스레드) 전달 → 각 채널 반영 확인.
- 마감 역산 규칙: 채널 마감 − 검토·승인 리드타임(예: 60분) = 변경 요청 최종 시각. 목요일 15시 광고 마감이면 14시 이전 승인 필요.
- 코드 조각(설계 제안, 미실행):
  type SSoT = { version: string; policyText: string; bannedPhrases: string[]; approvedBy: string; effectiveFrom: string };
  function assertNoBanned(text: string, sot: SSoT) { for (const p of sot.bannedPhrases) if (text.includes(p)) throw new Error(`금지 표현: ${p}`); }

[4] 고객 안내문·FAQ·상담 분류 기준
- 안내문(교정): "문의 접수 후 영업일 2일 안에 처리 시작합니다. 처리 완료 시점과 결제(PG) 반영 시점은 보장되지 않습니다."
- FAQ: Q1 즉시 환불되나요? → 아니요, 접수 후 영업일 2일 내 처리 시작이며 완료·PG 반영은 미보장. Q2 대기 중 순서는? → 결제 오류 > 환불 문의 > 일반 문의 우선순위. Q3 충원되나요? → 충원은 미승인 상태.
- 상담 분류: P1 결제 오류(즉시 에스컬레이션), P2 환불 문의(정책 문구 안내), P3 일반 문의(표준 응대).

[5] 운영 일정표(담당·기한·선행조건)
- 목 14:00 제품팀 확정 사양·제한 전달 (담당: 제품팀 / 선행: 없음)
- 목 14:00–14:30 SSoT 초안 갱신 (담당: C / 선행: 제품팀 전달)
- 목 14:30–15:00 광고 문구 승인·반영 (담당: C+광고업체 / 선행: SSoT)
- 목 15:00 광고 문구 마감 (하드 데드라인)
- 목 15:00–17:00 상담 교육 자료 준비 (담당: C / 선행: SSoT)
- 목 17:00 상담 교육 (담당: C / 선행: SSoT)
- 목 17:00–18:00 랜딩 수정 (담당: C+웹 / 선행: SSoT)
- 목 18:00 랜딩 수정 마감 (하드 데드라인)
- 금 09:00 출시 직전 확인표 점검 (담당: C / 선행: 기술 승인·결제 검증)
- 금 10:00 출시

[6] 비상 계획 3종
- 광고 지연: 목 15시까지 미반영 시 금지 표현 포함 여부 우선 점검, 필요 시 광고 일시 중단, SSoT 기준 문구로 대체.
- 기술 승인 지연: 출시 연기 또는 제한 기능 출시 검토(정책 결정 아님, 승인자 에스컬레이션).
- 문의 급증: 우선순위(P1>P2>P3) 적용, 대기 안내 문구 사전 준비, 충원 미승인 상태이므로 추가 인력 없이 대기열 운영.

[7] 출시 직전 확인표
- [ ] 기술 배포 승인 확인(미확인)
- [ ] 결제 검증 결과 확인(미확인)
- [ ] SSoT 최신 버전 광고/상담/랜딩 반영 확인
- [ ] 금지 표현('즉시 자동 환불') 미사용 확인
- [ ] 상담 교육 완료 확인
- [ ] 충원 승인 여부 확인(미승인)

[8] 모순·미확인 명시
- 모순: 광고 초안 '즉시 자동 환불' vs 확정 정책 '영업일 2일 내 처리 시작, 완료·PG 반영 미보장'.
- 미확인: 기술 배포 승인, 결제 검증, 충원 승인.
- 실제 구현/테스트: 미수행(설계 제안만).

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

**1.** expected_contacts = 800 × 0.20 = 160건 (제공: 초대 800명, 첫 4시간 20% 문의 가정)

**2.** four_hour_capacity = 4명 × 5건/시간 × 4시간 = 80건 (제공: 상담원 4명, 시간당 5건)

**3.** backlog_without_hiring = 160 − 80 = 80건

**4.** required_total_agents = 160 ÷ (5×4) = 8명

**5.** additional_agents = 8 − 4 = 4명

**6.** support_window_hours = 4, launch_hour = 10 (제공: 첫 4시간, 금요일 10시 출시)

**7.** immediate_refund_promised = false (광고 초안 '즉시 자동 환불'은 확정 정책 '영업일 2일 내 처리 시작'과 불일치)

**8.** 확정 정책: 상담 접수 후 영업일 2일 안에 처리 시작, 처리 완료·PG 반영 시간 미보장 (제공)

**9.** 마감: 광고 목 15시, 상담 교육 목 17시, 랜딩 목 18시, 제품팀 전달 목 14시 (제공)

**10.** 미확인: 기술 배포 승인, 결제 검증 결과, 인원 충원 승인 (제공: 아직 미제공/미승인)

**11.** 실제 고객 연락·외부 시스템 변경·배포·결제 검증·충원은 수행하지 않음(설계 제안만)
