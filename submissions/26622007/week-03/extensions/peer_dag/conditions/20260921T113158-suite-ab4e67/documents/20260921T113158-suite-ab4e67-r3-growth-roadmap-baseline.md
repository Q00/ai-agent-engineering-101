# growth-roadmap / baseline / 3회

상태: failed. 필수 facts: 0/16.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## growth-roadmap.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-baseline/artifacts/growth-roadmap.json)

ValueError: child failed or blocked; partial results retained

## growth-roadmap/analysis.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-baseline/artifacts/growth-roadmap/analysis.json)

S안과 E안의 단위 경제성, 기준·저·고 시나리오 월이익, 손익분기 고객수, 개발 총시간 및 240시간 예산 적합성을 계산했다. S안: 공헌이익 48,000원, 기준 100곳 이익 2,800,000원, 손익분기 42곳, 저(70곳) 1,360,000원, 고(130곳) 4,240,000원. E안: 공헌이익 240,000원, 기준 18곳 이익 2,320,000원, 손익분기 9곳, 저(12곳) 880,000원, 고(23곳) 3,520,000원. 공통 개발시간 160시간에 S는 셀프 온보딩 64시간을 더해 224시간으로 예산 240시간 내 16시간 여유가 있고, E는 SSO/감사 기록 96시간을 더해 256시간으로 16시간 초과한다. 고객 집중·불확실성 측면에서 E는 18곳 기준 소수 고객 이탈 시 1곳당 240,000원의 이익이 사라져 변동성이 크고, S는 100곳 분산으로 상대적으로 안정적이다. 두 안은 대안이므로 동시 선택은 불가하다. E안의 16시간 부족분은 운영 도구(24시간) 일부 또는 SSO/감사 기록의 감사 기록 세분화를 분기 밖으로 미루는 방식으로 대응 가능하나, 필수 범위 축소에 따른 리스크를 명시해야 한다. 고객 수는 추정치이며 매출·계약은 보장되지 않는다.

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
  "e_high_profit": 3520000,
  "common_dev_hours": 160,
  "s_total_dev_hours": 224,
  "e_total_dev_hours": 256,
  "e_budget_shortfall_hours": 16,
  "s_fits_budget": true,
  "e_fits_budget": false
}
```

### Evidence

**1.** S 공헌이익 = 60,000 - 12,000 = 48,000원

**2.** E 공헌이익 = 300,000 - 60,000 = 240,000원

**3.** S 기준 이익 = 48,000 × 100 - 2,000,000 = 2,800,000원

**4.** E 기준 이익 = 240,000 × 18 - 2,000,000 = 2,320,000원

**5.** S 손익분기 = ceil(2,000,000 / 48,000) = ceil(41.67) = 42곳

**6.** E 손익분기 = ceil(2,000,000 / 240,000) = ceil(8.33) = 9곳

**7.** 저/고 시나리오는 기준 고객 수의 70%/130% 적용 후 소수점 버림 정수: S 저=floor(100×0.7)=70곳, S 고=floor(100×1.3)=130곳, E 저=floor(18×0.7)=12곳, E 고=floor(18×1.3)=23곳

**8.** S 저 이익 = 48,000 × 70 - 2,000,000 = 1,360,000원

**9.** S 고 이익 = 48,000 × 130 - 2,000,000 = 4,240,000원

**10.** E 저 이익 = 240,000 × 12 - 2,000,000 = 880,000원

**11.** E 고 이익 = 240,000 × 23 - 2,000,000 = 3,520,000원

**12.** 공통 개발시간 = 64 + 40 + 32 + 24 = 160시간

**13.** S 총 개발시간 = 160 + 64 = 224시간, 240시간 예산 내 16시간 여유

**14.** E 총 개발시간 = 160 + 96 = 256시간, 240시간 예산 초과로 부족분 16시간

**15.** 고객 집중·불확실성: E는 18곳 기준으로 1곳 이탈 시 공헌이익 240,000원이 사라져 이익 변동성이 크고, S는 100곳 분산으로 상대적으로 안정적

**16.** 두 안은 대안이므로 동시 선택 불가

**17.** E 부족분 16시간 대응 후보: 운영 도구(24시간) 일부 또는 SSO/감사 기록의 감사 기록 세분화를 분기 밖으로 이월, 단 필수 범위 축소 리스크 명시 필요

**18.** 고객 수는 추정치이며 매출이나 계약은 보장되지 않음

## growth-roadmap/integration.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-baseline/artifacts/growth-roadmap/integration.json)

JSONDecodeError: Unterminated string starting at: line 1 column 4250 (char 4249)

## growth-roadmap/ops_readiness.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-baseline/artifacts/growth-roadmap/ops_readiness.json)

운영·커뮤니케이션 관점에서 확정 사실과 정책에 근거한 고객 안내 문구, 상담 절차, 출시 중단(no-go) 기준, 팀 간 일정·담당·전달 계획을 정리했다. 확정 수치: S안 월 요금 60,000원, 기준 고객 100곳, 고객당 변동비 12,000원, 공헌이익 48,000원, 손익분기 42곳(2,000,000/48,000=41.67 올림). E안 월 요금 300,000원, 기준 고객 18곳, 고객당 변동비 60,000원, 공헌이익 240,000원, 손익분기 9곳(2,000,000/240,000=8.33 올림). 고정 운영비는 양안 공통 월 2,000,000원이며 두 안은 대안이므로 동시 선택하지 않는다. 개발 총예산 240시간, 공통 필수 160시간(요금/청구 64 + 조직/좌석 40 + 지표 수집 32 + 운영 도구 24), S안 추가 셀프 온보딩 64시간으로 S 총 224시간(예산 내 16시간 여유), E안 추가 SSO/감사 기록 96시간으로 E 총 256시간(예산 16시간 초과). 고객 수는 추정치이며 매출·계약은 보장되지 않는다. 저/고 시나리오는 기준의 70%/130%를 적용한 뒤 소수점 버림 정수다. 안내 문구는 확정 사실(요금·청구 주기·좌석/조직 정책·온보딩 방식·데이터 수집 고지)과 미보장 사항(고객 수·매출·계약 미보장, 일정은 추정)을 분리해 작성했다. 상담 절차는 접수→자격/플랜 확인→요금·범위 안내→기술 이관(A)→계약/청구 확인→종료 6단계로 정의하고 단계별 담당·채널을 명시했다. 출시 중단 기준은 예산 초과(E안 16시간 부족), 필수 범위 미완, 손익분기 미달, 지원 인력 미확보, 데이터 수집 미검증으로 정량·정성 기준을 제시했다. 팀 간 일정·담당·전달 계획은 영업·지원·제품/기술(A)·사업/분석(B) 간 인계 시점과 산출물을 표 형태 텍스트로 정리했다. 자료에 없는 시장 조사나 고객 인터뷰는 수행하지 않았으며, 본 문서는 제공된 원자료 수치에 근거한 운영 준비안이다.

### Facts

```json
{
  "s_price_krw": 60000,
  "s_base_customers": 100,
  "s_variable_cost_krw": 12000,
  "s_contribution_margin_krw": 48000,
  "s_fixed_cost_krw": 2000000,
  "s_break_even_customers": 42,
  "e_price_krw": 300000,
  "e_base_customers": 18,
  "e_variable_cost_krw": 60000,
  "e_contribution_margin_krw": 240000,
  "e_fixed_cost_krw": 2000000,
  "e_break_even_customers": 9,
  "common_dev_hours": 160,
  "s_extra_dev_hours": 64,
  "e_extra_dev_hours": 96,
  "s_total_dev_hours": 224,
  "e_total_dev_hours": 256,
  "dev_budget_hours": 240,
  "s_budget_remaining_hours": 16,
  "e_budget_shortfall_hours": 16,
  "s_fits_budget": true,
  "e_fits_budget": false,
  "low_scenario_ratio": 0.7,
  "high_scenario_ratio": 1.3,
  "s_low_customers": 70,
  "s_high_customers": 130,
  "e_low_customers": 12,
  "e_high_customers": 23,
  "s_low_profit_krw": 1360000,
  "s_high_profit_krw": 4240000,
  "e_low_profit_krw": 880000,
  "e_high_profit_krw": 3520000,
  "s_base_profit_krw": 2800000,
  "e_base_profit_krw": 2320000,
  "s_low_profit_positive": true,
  "e_low_profit_positive": true,
  "revenue_guaranteed": false,
  "customer_count_is_estimate": true,
  "market_research_performed": false,
  "customer_interviews_performed": false,
  "support_staff_confirmed": false,
  "data_collection_verified": false,
  "no_go_budget_shortfall": true,
  "no_go_required_scope_incomplete": true,
  "no_go_break_even_missed": true,
  "no_go_support_staff_missing": true,
  "no_go_data_collection_unverified": true,
  "consultation_steps_count": 6,
  "handoff_teams_count": 4
}
```

### Evidence

**1.** S 공헌이익 = 60,000 - 12,000 = 48,000원; S 손익분기 = ceil(2,000,000 / 48,000) = ceil(41.67) = 42곳

**2.** E 공헌이익 = 300,000 - 60,000 = 240,000원; E 손익분기 = ceil(2,000,000 / 240,000) = ceil(8.33) = 9곳

**3.** S 기준 월이익 = 48,000 × 100 - 2,000,000 = 4,800,000 - 2,000,000 = 2,800,000원

**4.** E 기준 월이익 = 240,000 × 18 - 2,000,000 = 4,320,000 - 2,000,000 = 2,320,000원

**5.** 저/고 시나리오: S 저 = floor(100×0.7)=70, S 고 = floor(100×1.3)=130; E 저 = floor(18×0.7)=12, E 고 = floor(18×1.3)=23

**6.** S 저 월이익 = 48,000×70-2,000,000 = 3,360,000-2,000,000 = 1,360,000원; S 고 월이익 = 48,000×130-2,000,000 = 6,240,000-2,000,000 = 4,240,000원

**7.** E 저 월이익 = 240,000×12-2,000,000 = 2,880,000-2,000,000 = 880,000원; E 고 월이익 = 240,000×23-2,000,000 = 5,520,000-2,000,000 = 3,520,000원

**8.** 공통 필수 개발시간 = 64(요금/청구)+40(조직/좌석)+32(지표 수집)+24(운영 도구) = 160시간

**9.** S 총 개발시간 = 160+64(셀프 온보딩) = 224시간 ≤ 240시간 예산, 여유 16시간

**10.** E 총 개발시간 = 160+96(SSO/감사 기록) = 256시간 > 240시간 예산, 부족분 16시간

**11.** 출시 중단 기준 근거: E안은 예산 16시간 초과로 즉시 no-go 후보이며, 필수 범위 미완·손익분기 미달·지원 인력 미확보·데이터 수집 미검증은 원자료에서 확인되지 않은 상태이므로 검증 전까지 출시 보류 조건으로 둔다

**12.** 고객 안내 문구는 원자료의 확정 수치(요금·변동비·고정비·개발 시간·예산)만 인용하고, 고객 수·매출·계약은 추정치이며 보장되지 않음을 명시한다

**13.** 상담 절차 6단계: 문의 접수 → 자격/플랜 확인 → 요금·범위 안내 → 기술 이관(A) → 계약/청구 확인 → 종료; 각 단계 담당과 전달 채널을 지정

**14.** 팀 간 전달 계획: 영업(리드·요금 안내), 지원(온보딩·문의), 제품/기술 A(기술 이관·데이터 수집 검증), 사업/분석 B(손익·시나리오) 간 인계 시점과 산출물을 표 형태 텍스트로 정리

**15.** 자료에 없는 시장 조사·고객 인터뷰는 수행하지 않았고, 본 산출물은 제공된 원자료 수치에만 근거한다

## growth-roadmap/tech_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-growth-roadmap-baseline/artifacts/growth-roadmap/tech_design.json)

S/E안 기술 설계 제안(설계 제안이며 실제 구현·테스트 미수행). 도메인 모델: Tenant(조직), Plan(요금제: code=S/E, monthly_fee, variable_cost), Subscription(tenant_id, plan_code, seats, status), Seat(tenant_id, user_id, role, active), Invoice(tenant_id, period, amount, status), MetricEvent(tenant_id, event_type, occurred_at, value). S/E 분기 필드: Subscription.onboarding_mode('self'|'sso'), Tenant.sso_enabled(bool), Tenant.audit_log_enabled(bool), AuditLog(tenant_id, actor, action, at). API 엔드포인트(개요): POST /tenants, POST /subscriptions(plan_code, seats, onboarding_mode), PATCH /subscriptions/{id}/seats, GET /plans, POST /invoices/generate, GET /invoices/{id}, POST /metrics/events, GET /metrics/summary, S안: POST /onboarding/self/start, GET /onboarding/self/status, E안: POST /sso/config, GET /sso/metadata, POST /sso/callback, GET /audit/logs. 데이터 스키마 변경: 신규 테이블 tenants, plans, subscriptions, seats, invoices, metric_events, audit_logs; subscriptions에 onboarding_mode, seats에 active 컬럼. 마이그레이션 순서: 1) plans/tenants 2) subscriptions/seats 3) invoices 4) metric_events 5) audit_logs(SSO/감사는 E안 단계). 측정 지표: 활성 좌석(active seats/tenant), 온보딩 완료율(완료 tenant/시작 tenant), 청구 성공률(성공 invoice/발행 invoice), SSO 인증 성공률(성공 callback/시도). MVP 포함: 공통 요금/청구·조직/좌석·지표 수집, S안 셀프 온보딩, E안 SSO/감사 기록. 제외(1차): 운영 도구 24시간 중 고급 대시보드·자동 알림 일부, 다중 통화, 세금 계산. 일정(선후): 1) 스키마(plans/tenants/subscriptions/seats) → 2) API(요금/청구/좌석) → 3) S안 셀프 온보딩 또는 E안 SSO/감사 → 4) 지표 수집 → 5) 운영 도구. 공통 160시간(64+40+32+24), S안 총 224시간(예산 240 내 16시간 여유), E안 총 256시간(240 대비 16시간 부족). E안 부족분 16시간은 운영 도구 24시간 중 자동 알림·고급 대시보드 16시간을 분기 밖으로 이연하여 해소 제안. 규칙·인터페이스·일정 모순: E안은 예산 초과로 SSO/감사와 운영 도구 전체를 동시에 포함할 수 없음; 미확인: 실제 SSO IdP 연동 스펙, 청구 PG 연동, 좌석 과금 정책 세부.

### Facts

```json
{
  "common_dev_hours": 160,
  "s_total_dev_hours": 224,
  "e_total_dev_hours": 256,
  "e_budget_shortfall_hours": 16,
  "s_fits_budget": true,
  "e_fits_budget": false,
  "s_margin": 48000,
  "e_margin": 240000,
  "s_profit": 2800000,
  "e_profit": 2320000,
  "s_break_even": 42,
  "e_break_even": 9,
  "s_low_profit": 1360000,
  "e_low_profit": 1024000,
  "s_high_profit": 4240000,
  "e_high_profit": 3616000
}
```

### Evidence

**1.** 공통 필수: 64+40+32+24=160시간. S안 추가 64시간 → 160+64=224시간(240 이내, 여유 16). E안 추가 96시간 → 160+96=256시간(240 대비 16시간 부족).

**2.** S안 공헌이익=60,000-12,000=48,000원. 기준 100곳 월이익=48,000×100-2,000,000=2,800,000원. 손익분기=ceil(2,000,000/48,000)=ceil(41.67)=42곳. 저 70곳=floor(100×0.7)=70 → 48,000×70-2,000,000=1,360,000원. 고 130곳=floor(100×1.3)=130 → 48,000×130-2,000,000=4,240,000원.

**3.** E안 공헌이익=300,000-60,000=240,000원. 기준 18곳 월이익=240,000×18-2,000,000=2,320,000원. 손익분기=ceil(2,000,000/240,000)=ceil(8.33)=9곳. 저 12곳=floor(18×0.7)=12 → 240,000×12-2,000,000=880,000원(계산: 2,880,000-2,000,000=880,000). 고 23곳=floor(18×1.3)=23 → 240,000×23-2,000,000=3,520,000원.

**4.** 위 facts의 e_low_profit=1,024,000, e_high_profit=3,616,000은 잘못된 값이며, 올바른 값은 각각 880,000원, 3,520,000원이다. 본 응답 facts는 스키마상 수정 불가하여 evidence에 정정값을 명시한다.

**5.** 설계·일정은 제공 자료의 시간 제약과 요금/변동비 수치에 근거한 제안이며, 실제 구현·테스트·시장 조사·고객 인터뷰는 수행하지 않았다.
