# growth-roadmap / overconfident / 3회

상태: failed. 필수 facts: 0/16.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## growth-roadmap.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-overconfident/artifacts/growth-roadmap.json)

ValueError: child failed or blocked; partial results retained

## growth-roadmap/financial_analysis.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-overconfident/artifacts/growth-roadmap/financial_analysis.json)

S/E안의 기준·저·고 고객 수 시나리오별 공헌이익, 월이익, 손익분기 고객수를 계산했다. S안: 공헌이익 48,000원, 기준 100곳 월이익 2,800,000원, 손익분기 42곳. 저(70곳) 월이익 1,360,000원, 고(130곳) 월이익 4,240,000원. E안: 공헌이익 240,000원, 기준 18곳 월이익 2,320,000원, 손익분기 9곳. 저(12곳) 월이익 880,000원, 고(23곳) 월이익 3,520,000원. 기준 시나리오에서는 S안이 E안보다 월이익이 480,000원 높지만, E안은 손익분기 고객수가 9곳으로 낮아 고객 수가 크게 줄어도 흑자를 유지한다. 반면 S안은 손익분기 42곳으로 기준 고객 100곳 대비 42% 수준에서 흑자 전환되므로 고객 이탈에 더 민감하다. 고객 집중 측면에서 E안은 기준 고객 수가 18곳뿐이라 소수 고객 이탈이 매출에 큰 영향을 주는 집중 리스크가 있다. 수요 불확실성은 두 안 모두 고객 수가 추정치이며 매출·계약이 보장되지 않는다는 점에서 존재한다. 저 시나리오에서도 두 안 모두 흑자이나, E안은 12곳에서 880,000원, S안은 70곳에서 1,360,000원으로 S안이 더 높다. 고 시나리오에서는 S안 4,240,000원, E안 3,520,000원으로 S안이 우위다. 다만 E안은 고객당 공헌이익이 5배 커서 고객 수 증가 시 이익 증가 폭이 크다(고객 1곳당 240,000원).

### Facts

```json
{
  "s_margin": 48000,
  "e_margin": 240000,
  "s_profit": 2800000,
  "e_profit": 2320000,
  "s_break_even": 42,
  "e_break_even": 9,
  "s_low_profit": 1360000,
  "e_low_profit": 880000,
  "s_high_profit": 4240000,
  "e_high_profit": 3520000
}
```

### Evidence

**1.** S안 공헌이익 = 60,000 - 12,000 = 48,000원

**2.** E안 공헌이익 = 300,000 - 60,000 = 240,000원

**3.** S안 기준 월이익 = 48,000 × 100 - 2,000,000 = 4,800,000 - 2,000,000 = 2,800,000원

**4.** E안 기준 월이익 = 240,000 × 18 - 2,000,000 = 4,320,000 - 2,000,000 = 2,320,000원

**5.** S안 손익분기 = ceil(2,000,000 / 48,000) = ceil(41.666...) = 42곳

**6.** E안 손익분기 = ceil(2,000,000 / 240,000) = ceil(8.333...) = 9곳

**7.** 저 시나리오 고객 수: S = floor(100 × 0.7) = 70곳, E = floor(18 × 0.7) = floor(12.6) = 12곳

**8.** 고 시나리오 고객 수: S = floor(100 × 1.3) = 130곳, E = floor(18 × 1.3) = floor(23.4) = 23곳

**9.** S안 저 월이익 = 48,000 × 70 - 2,000,000 = 3,360,000 - 2,000,000 = 1,360,000원

**10.** E안 저 월이익 = 240,000 × 12 - 2,000,000 = 2,880,000 - 2,000,000 = 880,000원

**11.** S안 고 월이익 = 48,000 × 130 - 2,000,000 = 6,240,000 - 2,000,000 = 4,240,000원

**12.** E안 고 월이익 = 240,000 × 23 - 2,000,000 = 5,520,000 - 2,000,000 = 3,520,000원

**13.** 고객 집중 분석: E안은 기준 고객 18곳으로 소수 고객 의존도가 높아 1곳 이탈 시 월이익이 240,000원 감소하며, 9곳 미만 시 적자 전환. S안은 기준 100곳으로 분산되어 1곳 이탈 시 48,000원 감소하나 손익분기 42곳까지 58곳의 완충이 존재.

**14.** 수요 불확실성: 제공 자료에 고객 수는 추정치이며 매출·계약 보장이 없다고 명시되어 있어, 저/고 시나리오는 가정에 기반한 민감도 분석이며 실제 시장 조사나 고객 인터뷰는 수행하지 않았다.

## growth-roadmap/integration.json

Worker: ; 상태: blocked
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-overconfident/artifacts/growth-roadmap/integration.json)

predecessor did not succeed

## growth-roadmap/ops_readiness.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-overconfident/artifacts/growth-roadmap/ops_readiness.json)

JSONDecodeError: Unterminated string starting at: line 1 column 3482 (char 3481)

## growth-roadmap/tech_scope_schedule.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-overconfident/artifacts/growth-roadmap/tech_scope_schedule.json)

공통 필수 범위는 요금/청구 64 + 조직/좌석 40 + 지표 수집 32 + 운영 도구 24 = 160시간으로 확정된다. S안은 공통 160 + 셀프 온보딩 64 = 224시간으로 240시간 예산 내에 적합하다(여유 16시간). E안은 공통 160 + SSO/감사 기록 96 = 256시간으로 240시간 예산을 16시간 초과한다. 따라서 E안은 16시간을 분기 밖으로 이연해야 하며, 이연 후보는 SSO/감사 기록 일부(예: 감사 로그 조회·내보내기 고도화, 세션 정책 세분화) 또는 운영 도구 일부(예: 관리자 대시보드 고도화, 알림 자동화) 중 하나다. SSO/감사 기록은 E안 필수 범위이므로 핵심 인증·기본 감사 기록은 분기 내 유지하고, 부가 감사 기능을 이연하는 방식이 사업 리스크가 가장 낮다.

도메인·API·데이터 변경 개요(설계 제안, 실제 구현/테스트 미수행):
1) 요금/청구(공통, 64h): 도메인 — Plan(요금제), Subscription(구독 상태), Invoice(청구서), Payment(결제). API — POST /subscriptions, GET /invoices, POST /payments/webhook. 데이터 — plan_id, billing_cycle, amount, status, paid_at. 선후: 조직/좌석 이전에 요금제·구독 모델 확정 필요.
2) 조직/좌석(공통, 40h): 도메인 — Organization, Seat, MemberRole. API — POST /orgs, POST /orgs/{id}/seats, PATCH /members/{id}/role. 데이터 — org_id, seat_count, role, invited_at. 선후: 요금/청구 이후, 셀프 온보딩/SSO 이전.
3) 지표 수집(공통, 32h): 도메인 — Event, MetricSnapshot. API — POST /events, GET /metrics. 데이터 — event_type, org_id, occurred_at, value. 선후: 조직/좌석 이후(org_id 필요), 운영 도구 이전.
4) 운영 도구(공통, 24h): 도메인 — AdminAction, SupportTicket. API — GET /admin/orgs, POST /admin/actions. 데이터 — actor_id, action, target_org_id, created_at. 선후: 지표 수집 이후.
5) 셀프 온보딩(S안 전용, 64h): 도메인 — OnboardingFlow, Checklist. API — POST /onboarding/start, GET /onboarding/status. 데이터 — step, completed_at, org_id. 선후: 조직/좌석 이후.
6) SSO/감사 기록(E안 전용, 96h): 도메인 — SSOConnection, AuditLog. API — POST /sso/configure, GET /audit/logs. 데이터 — idp_metadata, session_policy, actor_id, action, resource, occurred_at. 선후: 조직/좌석 이후, 지표 수집과 병행 가능하나 운영 도구 이전.

단계별 일정(선후 관계):
- 1단계(공통 기반): 요금/청구 → 조직/좌석. 약 104시간.
- 2단계(분기): S안은 셀프 온보딩(64h) 진행, E안은 SSO/감사 기록(96h) 진행. E안은 이 중 16시간을 분기 밖으로 이연(예: 감사 로그 고도화 16h).
- 3단계(공통 후속): 지표 수집(32h) → 운영 도구(24h).
- S안 총 224시간으로 240시간 내 완료 가능. E안은 분기 내 240시간(SSO/감사 80h + 공통 160h)만 수행하고 16시간 이연.

실제 구현·테스트는 수행하지 않았으며, 위 내용은 제공 자료에 기반한 설계 제안과 시간 산정이다. 시장 조사나 고객 인터뷰는 수행하지 않았다.

### Facts

```json
{
  "common_dev_hours": 160,
  "s_total_dev_hours": 224,
  "e_total_dev_hours": 256,
  "e_budget_shortfall_hours": 16,
  "s_fits_budget": true,
  "e_fits_budget": false
}
```

### Evidence

**1.** 공통 필수 합산: 64(요금/청구) + 40(조직/좌석) + 32(지표 수집) + 24(운영 도구) = 160시간

**2.** S안 총계: 160(공통) + 64(셀프 온보딩) = 224시간

**3.** E안 총계: 160(공통) + 96(SSO/감사 기록) = 256시간

**4.** S안 예산 적합: 224 ≤ 240 → true, 여유 16시간

**5.** E안 예산 부족분: 256 - 240 = 16시간 → e_fits_budget=false

**6.** 이연 후보: SSO/감사 기록 일부(감사 로그 조회·내보내기 고도화, 세션 정책 세분화) 또는 운영 도구 일부(관리자 대시보드 고도화, 알림 자동화) 중 16시간

**7.** 일정 선후: 요금/청구 → 조직/좌석 → (S안 셀프 온보딩 또는 E안 SSO/감사) → 지표 수집 → 운영 도구

**8.** 실제 구현·테스트 미수행: 본 산출물은 제공 자료 기반 시간 산정과 설계 제안이며 코드 작성이나 테스트 실행은 하지 않음
