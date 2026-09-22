# growth-roadmap / baseline — 429 복구

상태: succeeded. facts: 14/16.
모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.

## growth-roadmap.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap.json)

세 선행 결과(biz_analysis, ops_readiness, tech_design)를 통합해 경영진용 최종 보고서를 작성했다. 모든 수치는 제공된 공식과 추정 고객 수에 근거하며, 실제 구현·배포·회귀 테스트·시장 조사·고객 인터뷰는 수행하지 않았고 본 산출물은 설계 제안이다.

[1. 비교표]
구분 | S안(소규모 팀) | E안(기업용)
월 요금 | 60,000원 | 300,000원
고객당 변동비 | 12,000원 | 60,000원
공헌이익/고객 | 48,000원 | 240,000원
공헌이익률 | 0.8 | 0.8
기준 고객수 | 100곳 | 18곳
기준 월이익 | 2,800,000원 | 2,320,000원
저 시나리오(70%) | 70곳 / 1,360,000원 | 12곳 / 880,000원
고 시나리오(130%) | 130곳 / 4,240,000원 | 23곳 / 3,520,000원
손익분기 고객수 | 42곳 | 9곳
기준 대비 손익분기 여유 | 58곳(2.38배) | 9곳(2.00배)
고객 1곳 이탈 이익 영향 | -48,000원(-1.7%) | -240,000원(-10.3%)
공통 개발시간 | 160h | 160h
안별 추가 개발시간 | 64h(셀프 온보딩) | 96h(SSO/감사)
총 개발시간 | 224h | 256h
예산(240h) 적합성 | 적합(여유 16h) | 부족(16h 초과)

[2. 선택 근거와 반증 가능한 가설]
기준 시나리오에서 S안 월이익 2,800,000원이 E안 2,320,000원보다 480,000원 높다. 저 시나리오(S 1,360,000 vs E 880,000), 고 시나리오(S 4,240,000 vs E 3,520,000)에서도 S안이 우위다. 손익분기 절대값은 E안(9곳)이 낮지만 기준 대비 여유 배수는 S안(2.38배)이 E안(2.00배)보다 크다. 개발 예산도 S안 224h≤240h로 적합, E안 256h>240h로 16h 초과다. 따라서 제공된 수치만으로는 S안이 세 시나리오 모두에서 우위다.
반증 가설 1(E안 우위): E안 고객 21곳 이상이면 E안이 S안 기준을 추월. 검증식 240,000×N-2,000,000 > 2,800,000 → N>20.
반증 가설 2(S안 적자): S안 고객 41곳 이하이면 적자. 검증식 48,000×N-2,000,000 < 0 → N<41.67.
반증 가설 3(고객 집중): E안은 1곳 이탈 시 이익 -10.3%, S안은 -1.7%로 E안이 수요 불확실성에 더 취약.

[3. MVP 포함/제외]
포함: 공통 4개 모듈(요금/청구 64h, 조직/좌석 40h, 지표 수집 32h, 운영 도구 24h), S안 셀프 온보딩(64h), E안 SSO/감사 기본(96h).
제외(분기 밖 이월): 감사 로그 고급 필터/리포트, SSO 다중 IdP 확장. 이는 E안 16h 부족분을 처리하기 위함이다.

[4. 코드·데이터 변경 개요]
도메인: 요금제/구독(Plan 1-N Subscription, Subscription 1-N BillingCycle), 조직/좌석(Organization 1-N Member, Organization 1-N SeatAssignment), 온보딩 S(OnboardingFlow 1-N InviteToken), 인증/감사 E(Organization 1-N SSOConnection, Organization 1-N AuditEvent).
API: 공통 - GET /plans, POST /subscriptions, POST /billing/charge, GET/POST /organizations, POST /organizations/{id}/seats, POST /metrics/events. S 전용 - POST /onboarding/start, POST /onboarding/complete. E 전용 - POST /sso/connections, GET /sso/login, GET /audit/events. 권한: 공통 org_admin, S self-service, E org_admin+security_admin.
데이터: 신규 테이블 plans, subscriptions, billing_cycles, organizations, members, seat_assignments, metric_events, onboarding_flows, invite_tokens, sso_connections, audit_events. 마이그레이션 순서 plans→organizations→members→seat_assignments→subscriptions→billing_cycles→metric_events→(S)onboarding_flows→invite_tokens / (E)sso_connections→audit_events. audit_events는 append-only, 보존 기간 정책 미확정.
측정 지표: 활성 조직 수, 좌석 사용률, 온보딩 완료율(S), SSO 로그인 성공률(E), 감사 이벤트 커버리지(E), 청구 성공률, 이탈률.

[5. 단계별 일정(선후 관계)]
1) 공통 도메인·데이터 스키마 → 2) 공통 API·청구 → 3) 지표 수집 → 4) 운영 도구 → 5) S 온보딩 또는 E SSO/감사(안별 분기). 1→2→3→4는 순차, 5는 4 이후. E안은 5단계 일부(고급 필터/리포트, 다중 IdP)가 다음 분기로 이월.

[6. 영업/지원 준비]
가격/플랜: S안 월 60,000원 단일 플랜(확정), E안 월 300,000원(확정). 대상 고객군: S 소규모 팀(추정), E 기업(추정). 계약·청구 흐름, 경쟁 포지셔닝, 문의 유형 분류, 에스컬레이션 경로, SLA 수치, 환불 정책, 교육 일정, 담당자 이름은 모두 미확인. 고객 안내 문구 초안은 확정 사실(요금·변동비·고정비)과 추정치(고객 수)를 구분해 작성했다.

[7. 출시 중단 기준(롤백)]
롤백 트리거(정량, 임계값 미확인): 청구 오류율, 온보딩 실패율, SSO 인증 실패율, 지원 티켓 급증률, 손익분기 미달 지속 기간. 롤백 절차(제안): 1) 기능 플래그 off 2) 신규 계약 중단 3) 기존 고객 유지 정책 적용. 각 단계 승인자·소요시간·임계 수치는 미확인.

[8. 정합성 대조 및 모순]
E안은 256h>240h로 예산 초과이므로 'E안 전체를 이번 분기에 출시'라는 권고는 성립하지 않는다. S안은 224h≤240h로 이번 분기 출시 가능하다. 두 안은 대안이므로 동시 선택하지 않는다. 기능 설계(공통 160h+안별 추가), 시간표(224h/256h), 사업 권고(S안 우위)는 서로 정합한다. 미확인 사항: 실제 개발 속도, SSO IdP별 난이도, 지표 수집 인프라 비용, 감사 로그 보존 기간, SLA·환불·계약 조건, 롤백 임계값, 담당자 이름.

### Facts

```json
{
  "s_margin": 16,
  "e_margin": -16,
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
  "e_fits_budget": false,
  "s_base_customers": 100,
  "e_base_customers": 18,
  "s_low_customers": 70,
  "e_low_customers": 12,
  "s_high_customers": 130,
  "e_high_customers": 23,
  "s_margin_per_customer": 48000,
  "e_margin_per_customer": 240000,
  "s_contribution_ratio": 0.8,
  "e_contribution_ratio": 0.8,
  "fixed_cost_monthly": 2000000,
  "s_break_even_margin_multiple": 2.38,
  "e_break_even_margin_multiple": 2.0,
  "s_customer_churn_1_profit_impact": -48000,
  "e_customer_churn_1_profit_impact": -240000,
  "s_customer_churn_1_profit_pct": -0.017,
  "e_customer_churn_1_profit_pct": -0.103,
  "e_profit_advantage_threshold_customers": 21,
  "s_loss_threshold_customers": 41,
  "s_better_in_all_scenarios": true,
  "s_launch_this_quarter_valid": true,
  "e_full_launch_this_quarter_valid": false,
  "s_e_simultaneous_launch": false,
  "customer_counts_are_estimates": true,
  "market_research_conducted": false,
  "customer_interviews_conducted": false,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "mvp_common_modules_included": true,
  "mvp_s_onboarding_included": true,
  "mvp_e_sso_audit_included": true,
  "mvp_audit_advanced_filter_deferred": true,
  "mvp_sso_multi_idp_deferred": true,
  "schedule_sequential_common_to_ops": true,
  "schedule_branch_after_ops": true,
  "rollback_procedure_drafted": true,
  "rollback_trigger_thresholds_confirmed": false,
  "sales_support_scripts_drafted": true,
  "handoff_plan_drafted": true,
  "sla_value_confirmed": false,
  "refund_policy_confirmed": false,
  "contract_terms_confirmed": false,
  "training_schedule_confirmed": false,
  "owner_names_confirmed": false,
  "audit_log_retention_policy_confirmed": false
}
```

### Evidence

1. 공헌이익: S=60,000-12,000=48,000원, E=300,000-60,000=240,000원 (source 확정 사실)
2. 기준 월이익: S=48,000×100-2,000,000=2,800,000원, E=240,000×18-2,000,000=2,320,000원
3. 저 시나리오(70% 버림): S=floor(100×0.7)=70곳 → 48,000×70-2,000,000=1,360,000원, E=floor(18×0.7)=12곳 → 240,000×12-2,000,000=880,000원
4. 고 시나리오(130% 버림): S=floor(100×1.3)=130곳 → 48,000×130-2,000,000=4,240,000원, E=floor(18×1.3)=23곳 → 240,000×23-2,000,000=3,520,000원
5. 손익분기: S=ceil(2,000,000/48,000)=ceil(41.67)=42곳, E=ceil(2,000,000/240,000)=ceil(8.33)=9곳
6. 손익분기 여유 배수: S=100/42=2.38, E=18/9=2.00
7. 고객 1곳 이탈 이익 영향: S=-48,000원(2,800,000의 -1.7%), E=-240,000원(2,320,000의 -10.3%)
8. E안 역전 임계: 240,000×N-2,000,000>2,800,000 → N>20 → N=21곳
9. S안 적자 임계: 48,000×N-2,000,000<0 → N<41.67 → N≤41곳
10. 공통 개발시간: 64(요금/청구)+40(조직/좌석)+32(지표 수집)+24(운영 도구)=160h
11. S 총 개발시간: 160+64(셀프 온보딩)=224h, 예산 240h 대비 여유 240-224=16h → s_fits_budget=true, s_margin=16
12. E 총 개발시간: 160+96(SSO/감사)=256h, 예산 240h 대비 부족 256-240=16h → e_fits_budget=false, e_margin=-16, e_budget_shortfall_hours=16
13. 동시 개발로 총시간이 줄어든다는 가정은 자료 지시에 따라 배제
14. 도메인·API·데이터 변경 개요는 tech_design 산출물의 엔터티·엔드포인트·마이그레이션 순서를 통합 요약
15. 일정 선후: 공통 도메인→API·청구→지표→운영도구→안별 분기(5단계는 4단계 이후)
16. 모순 점검: E안 256h>240h이므로 'E안 전체 이번 분기 출시' 권고는 성립하지 않음. S안 224h≤240h로 출시 가능
17. 영업/지원 준비: 가격·변동비·고정비는 확정, 고객 수는 추정치, SLA·환불·계약·담당자·교육 일정은 미확인
18. 롤백 트리거 임계값·승인자·소요시간은 미확인이며 절차(기능 플래그 off→신규 계약 중단→기존 고객 유지)만 제안
19. 수행하지 않은 것: 시장 조사, 고객 인터뷰, 실제 코드 구현·배포·회귀 테스트. 본 산출물은 제공 자료 기반 설계 제안임
## growth-roadmap/biz_analysis.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap/biz_analysis.json)

S안과 E안의 기준·저·고 시나리오 손익, 손익분기 고객수, 고객 집중·수요 불확실성, 선택 근거와 반증 가능한 가설을 분석했다. 모든 수치는 제공된 공식과 추정 고객 수에 기반하며, 고객 수는 추정치이고 매출·계약은 보장되지 않는다.

[비교표]
구분 | S안(소규모 팀) | E안(기업용)
월 요금 | 60,000원 | 300,000원
고객당 변동비 | 12,000원 | 60,000원
공헌이익/고객 | 48,000원 | 240,000원
기준 고객수 | 100곳 | 18곳
기준 월이익 | 2,800,000원 | 2,320,000원
저 시나리오(70%) | 70곳 / 1,360,000원 | 12곳 / 880,000원
고 시나리오(130%) | 130곳 / 4,240,000원 | 23곳 / 3,520,000원
손익분기 고객수 | 42곳 | 9곳
기준 대비 손익분기 여유 | 58곳(2.38배) | 9곳(2.00배)

[선택 근거]
기준 시나리오에서는 S안 월이익 2,800,000원이 E안 2,320,000원보다 480,000원 높다. 저 시나리오에서도 S안 1,360,000원이 E안 880,000원보다 높고, 고 시나리오에서도 S안 4,240,000원이 E안 3,520,000원보다 높다. 손익분기 고객수는 S안 42곳, E안 9곳으로 E안이 절대적으로 낮지만, 기준 고객수 대비 여유 배수는 S안 2.38배, E안 2.00배로 S안이 더 크다. 따라서 제공된 수치만으로는 S안이 세 시나리오 모두에서 우위다. 다만 E안은 고객당 공헌이익이 5배(240,000/48,000)로, 소수 대형 고객 확보 시 이익 레버리지가 크다는 점이 반증 가능한 가설의 핵심이다.

[반증 가능한 가설]
1) E안 우위 가설: E안 기준 고객수가 18곳이 아니라 20곳 이상이면 E안 월이익이 S안 기준을 추월한다. 검증식: 240,000×N-2,000,000 > 2,800,000 → N > 20. 따라서 E안 고객 21곳 이상 확보 시 E안이 S안 기준을 앞선다.
2) S안 우위 가설: S안 고객수가 42곳 미만으로 떨어지면 S안도 적자다. 검증식: 48,000×N-2,000,000 < 0 → N < 41.67.
3) 고객 집중 가설: E안은 기준 18곳 중 상위 1~2곳 이탈만으로도 월이익이 급감한다. 예: E안 고객 1곳 이탈 시 월이익 2,320,000→2,080,000원(-240,000원, -10.3%). S안은 1곳 이탈 시 2,800,000→2,752,000원(-48,000원, -1.7%).

[고객 집중·수요 불확실성]
E안은 고객당 공헌이익이 커서 소수 고객 의존도가 높다. 기준 18곳에서 1곳 이탈은 이익의 약 10.3%를 차감하지만, S안은 100곳 기준 1곳 이탈이 약 1.7%에 불과하다. 저 시나리오에서 E안은 12곳으로 손익분기 9곳을 겨우 3곳 상회해 완충이 얇다. S안은 저 시나리오 70곳이 손익분기 42곳을 28곳 상회해 완충이 두껍다. 고객 수는 추정치이며 매출·계약은 보장되지 않으므로, E안은 수요 불확실성에 더 취약하다. 자료에 없는 시장 조사나 고객 인터뷰는 수행하지 않았고, 본 분석은 제공된 수치와 공식에만 근거한다.

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
  "s_low_customers": 70,
  "e_low_customers": 12,
  "s_high_customers": 130,
  "e_high_customers": 23,
  "s_base_customers": 100,
  "e_base_customers": 18,
  "fixed_cost": 2000000,
  "s_contribution_ratio": 0.8,
  "e_contribution_ratio": 0.8,
  "s_break_even_margin_multiple": 2.38,
  "e_break_even_margin_multiple": 2.0,
  "e_customer_churn_1_profit_impact": -240000,
  "s_customer_churn_1_profit_impact": -48000,
  "e_customer_churn_1_profit_pct": -0.103,
  "s_customer_churn_1_profit_pct": -0.017,
  "e_profit_advantage_threshold_customers": 21,
  "s_loss_threshold_customers": 41,
  "s_better_in_all_scenarios": true,
  "e_more_leveraged": true,
  "customer_counts_are_estimates": true,
  "market_research_conducted": false,
  "customer_interviews_conducted": false
}
```

### Evidence

1. 공헌이익: S=60000-12000=48000, E=300000-60000=240000
2. 기준 월이익: S=48000×100-2000000=2800000, E=240000×18-2000000=2320000
3. 저 시나리오 고객수: S=floor(100×0.7)=70, E=floor(18×0.7)=floor(12.6)=12
4. 고 시나리오 고객수: S=floor(100×1.3)=130, E=floor(18×1.3)=floor(23.4)=23
5. 저 시나리오 월이익: S=48000×70-2000000=1360000, E=240000×12-2000000=880000
6. 고 시나리오 월이익: S=48000×130-2000000=4240000, E=240000×23-2000000=3520000
7. 손익분기: S=ceil(2000000/48000)=ceil(41.67)=42, E=ceil(2000000/240000)=ceil(8.33)=9
8. 기준 대비 손익분기 여유 배수: S=100/42=2.38, E=18/9=2.00
9. E안이 S안 기준 월이익을 추월하는 고객수: 240000×N-2000000>2800000 → N>20 → N=21
10. S안 적자 전환 고객수: 48000×N-2000000<0 → N<41.67 → N≤41
11. 고객 1곳 이탈 이익 영향: E=-240000원(기준 2320000의 -10.3%), S=-48000원(기준 2800000의 -1.7%)
12. 공헌이익률: S=48000/60000=0.8, E=240000/300000=0.8
13. 고객 수는 제공된 추정치이며 매출·계약 보장 없음. 시장 조사·고객 인터뷰는 수행하지 않았고 제공 자료만 사용함
## growth-roadmap/integrate.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap/integrate.json)

세 선행 결과(biz_analysis, ops_readiness, tech_design)를 통합해 경영진용 최종 보고서를 작성했다. 모든 수치는 제공된 공식과 추정 고객 수에 근거하며, 실제 구현·배포·회귀 테스트·시장 조사·고객 인터뷰는 수행하지 않았고 본 산출물은 설계 제안이다.

[1. 비교표]
구분 | S안(소규모 팀) | E안(기업용)
월 요금 | 60,000원 | 300,000원
고객당 변동비 | 12,000원 | 60,000원
공헌이익/고객 | 48,000원 | 240,000원
공헌이익률 | 0.8 | 0.8
기준 고객수 | 100곳 | 18곳
기준 월이익 | 2,800,000원 | 2,320,000원
저 시나리오(70%) | 70곳 / 1,360,000원 | 12곳 / 880,000원
고 시나리오(130%) | 130곳 / 4,240,000원 | 23곳 / 3,520,000원
손익분기 고객수 | 42곳 | 9곳
기준 대비 손익분기 여유 | 58곳(2.38배) | 9곳(2.00배)
고객 1곳 이탈 이익 영향 | -48,000원(-1.7%) | -240,000원(-10.3%)
공통 개발시간 | 160h | 160h
안별 추가 개발시간 | 64h(셀프 온보딩) | 96h(SSO/감사)
총 개발시간 | 224h | 256h
예산(240h) 적합성 | 적합(여유 16h) | 부족(16h 초과)

[2. 선택 근거와 반증 가능한 가설]
기준 시나리오에서 S안 월이익 2,800,000원이 E안 2,320,000원보다 480,000원 높다. 저 시나리오(S 1,360,000 vs E 880,000), 고 시나리오(S 4,240,000 vs E 3,520,000)에서도 S안이 우위다. 손익분기 절대값은 E안(9곳)이 낮지만 기준 대비 여유 배수는 S안(2.38배)이 E안(2.00배)보다 크다. 개발 예산도 S안 224h≤240h로 적합, E안 256h>240h로 16h 초과다. 따라서 제공된 수치만으로는 S안이 세 시나리오 모두에서 우위다.
반증 가설 1(E안 우위): E안 고객 21곳 이상이면 E안이 S안 기준을 추월. 검증식 240,000×N-2,000,000 > 2,800,000 → N>20.
반증 가설 2(S안 적자): S안 고객 41곳 이하이면 적자. 검증식 48,000×N-2,000,000 < 0 → N<41.67.
반증 가설 3(고객 집중): E안은 1곳 이탈 시 이익 -10.3%, S안은 -1.7%로 E안이 수요 불확실성에 더 취약.

[3. MVP 포함/제외]
포함: 공통 4개 모듈(요금/청구 64h, 조직/좌석 40h, 지표 수집 32h, 운영 도구 24h), S안 셀프 온보딩(64h), E안 SSO/감사 기본(96h).
제외(분기 밖 이월): 감사 로그 고급 필터/리포트, SSO 다중 IdP 확장. 이는 E안 16h 부족분을 처리하기 위함이다.

[4. 코드·데이터 변경 개요]
도메인: 요금제/구독(Plan 1-N Subscription, Subscription 1-N BillingCycle), 조직/좌석(Organization 1-N Member, Organization 1-N SeatAssignment), 온보딩 S(OnboardingFlow 1-N InviteToken), 인증/감사 E(Organization 1-N SSOConnection, Organization 1-N AuditEvent).
API: 공통 - GET /plans, POST /subscriptions, POST /billing/charge, GET/POST /organizations, POST /organizations/{id}/seats, POST /metrics/events. S 전용 - POST /onboarding/start, POST /onboarding/complete. E 전용 - POST /sso/connections, GET /sso/login, GET /audit/events. 권한: 공통 org_admin, S self-service, E org_admin+security_admin.
데이터: 신규 테이블 plans, subscriptions, billing_cycles, organizations, members, seat_assignments, metric_events, onboarding_flows, invite_tokens, sso_connections, audit_events. 마이그레이션 순서 plans→organizations→members→seat_assignments→subscriptions→billing_cycles→metric_events→(S)onboarding_flows→invite_tokens / (E)sso_connections→audit_events. audit_events는 append-only, 보존 기간 정책 미확정.
측정 지표: 활성 조직 수, 좌석 사용률, 온보딩 완료율(S), SSO 로그인 성공률(E), 감사 이벤트 커버리지(E), 청구 성공률, 이탈률.

[5. 단계별 일정(선후 관계)]
1) 공통 도메인·데이터 스키마 → 2) 공통 API·청구 → 3) 지표 수집 → 4) 운영 도구 → 5) S 온보딩 또는 E SSO/감사(안별 분기). 1→2→3→4는 순차, 5는 4 이후. E안은 5단계 일부(고급 필터/리포트, 다중 IdP)가 다음 분기로 이월.

[6. 영업/지원 준비]
가격/플랜: S안 월 60,000원 단일 플랜(확정), E안 월 300,000원(확정). 대상 고객군: S 소규모 팀(추정), E 기업(추정). 계약·청구 흐름, 경쟁 포지셔닝, 문의 유형 분류, 에스컬레이션 경로, SLA 수치, 환불 정책, 교육 일정, 담당자 이름은 모두 미확인. 고객 안내 문구 초안은 확정 사실(요금·변동비·고정비)과 추정치(고객 수)를 구분해 작성했다.

[7. 출시 중단 기준(롤백)]
롤백 트리거(정량, 임계값 미확인): 청구 오류율, 온보딩 실패율, SSO 인증 실패율, 지원 티켓 급증률, 손익분기 미달 지속 기간. 롤백 절차(제안): 1) 기능 플래그 off 2) 신규 계약 중단 3) 기존 고객 유지 정책 적용. 각 단계 승인자·소요시간·임계 수치는 미확인.

[8. 정합성 대조 및 모순]
E안은 256h>240h로 예산 초과이므로 'E안 전체를 이번 분기에 출시'라는 권고는 성립하지 않는다. S안은 224h≤240h로 이번 분기 출시 가능하다. 두 안은 대안이므로 동시 선택하지 않는다. 기능 설계(공통 160h+안별 추가), 시간표(224h/256h), 사업 권고(S안 우위)는 서로 정합한다. 미확인 사항: 실제 개발 속도, SSO IdP별 난이도, 지표 수집 인프라 비용, 감사 로그 보존 기간, SLA·환불·계약 조건, 롤백 임계값, 담당자 이름.

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
  "e_fits_budget": false,
  "s_low_customers": 70,
  "e_low_customers": 12,
  "s_high_customers": 130,
  "e_high_customers": 23,
  "s_base_customers": 100,
  "e_base_customers": 18,
  "s_contribution_ratio": 0.8,
  "e_contribution_ratio": 0.8,
  "s_break_even_margin_multiple": 2.38,
  "e_break_even_margin_multiple": 2.0,
  "s_customer_churn_1_profit_impact": -48000,
  "e_customer_churn_1_profit_impact": -240000,
  "s_customer_churn_1_profit_pct": -0.017,
  "e_customer_churn_1_profit_pct": -0.103,
  "e_profit_advantage_threshold_customers": 21,
  "s_loss_threshold_customers": 41,
  "s_better_in_all_scenarios": true,
  "s_e_simultaneous_launch": false,
  "fixed_cost_monthly": 2000000,
  "s_dev_additional_hours": 64,
  "e_dev_additional_hours": 96,
  "dev_budget_hours": 240,
  "mvp_common_modules_included": true,
  "mvp_s_onboarding_included": true,
  "mvp_e_sso_audit_included": true,
  "mvp_audit_advanced_filter_deferred": true,
  "mvp_sso_multi_idp_deferred": true,
  "schedule_sequential_common_to_ops": true,
  "schedule_branch_after_ops": true,
  "e_full_launch_this_quarter_valid": false,
  "s_launch_this_quarter_valid": true,
  "design_schedule_business_aligned": true,
  "customer_counts_are_estimates": true,
  "market_research_conducted": false,
  "customer_interviews_conducted": false,
  "actual_implementation_performed": false,
  "actual_testing_performed": false,
  "sla_value_confirmed": false,
  "refund_policy_confirmed": false,
  "contract_terms_confirmed": false,
  "rollback_trigger_thresholds_confirmed": false,
  "owner_names_confirmed": false,
  "training_schedule_confirmed": false,
  "audit_log_retention_policy_confirmed": false,
  "sales_support_scripts_drafted": true,
  "rollback_procedure_drafted": true,
  "handoff_plan_drafted": true
}
```

### Evidence

1. 공헌이익: S=60,000-12,000=48,000원, E=300,000-60,000=240,000원 (source 확정 사실)
2. 기준 월이익: S=48,000×100-2,000,000=2,800,000원, E=240,000×18-2,000,000=2,320,000원
3. 저 시나리오(70% 버림): S=floor(100×0.7)=70곳 → 48,000×70-2,000,000=1,360,000원, E=floor(18×0.7)=12곳 → 240,000×12-2,000,000=880,000원
4. 고 시나리오(130% 버림): S=floor(100×1.3)=130곳 → 48,000×130-2,000,000=4,240,000원, E=floor(18×1.3)=23곳 → 240,000×23-2,000,000=3,520,000원
5. 손익분기: S=ceil(2,000,000/48,000)=ceil(41.67)=42곳, E=ceil(2,000,000/240,000)=ceil(8.33)=9곳
6. 손익분기 여유 배수: S=100/42=2.38, E=18/9=2.00
7. 고객 1곳 이탈 이익 영향: S=-48,000원(2,800,000의 -1.7%), E=-240,000원(2,320,000의 -10.3%)
8. E안 역전 임계: 240,000×N-2,000,000>2,800,000 → N>20 → N=21곳
9. S안 적자 임계: 48,000×N-2,000,000<0 → N<41.67 → N≤41곳
10. 공통 개발시간: 64(요금/청구)+40(조직/좌석)+32(지표 수집)+24(운영 도구)=160h
11. S 총 개발시간: 160+64(셀프 온보딩)=224h, 예산 240h 대비 여유 16h → s_fits_budget=true
12. E 총 개발시간: 160+96(SSO/감사)=256h, 예산 240h 대비 부족 16h → e_fits_budget=false, e_budget_shortfall_hours=16
13. 동시 개발로 총시간이 줄어든다는 가정은 자료 지시에 따라 배제
14. 도메인·API·데이터 변경 개요는 tech_design 산출물의 엔터티·엔드포인트·마이그레이션 순서를 통합 요약
15. 일정 선후: 공통 도메인→API·청구→지표→운영도구→안별 분기(5단계는 4단계 이후)
16. 모순 점검: E안 256h>240h이므로 'E안 전체 이번 분기 출시' 권고는 성립하지 않음. S안 224h≤240h로 출시 가능
17. 영업/지원 준비: 가격·변동비·고정비는 확정, 고객 수는 추정치, SLA·환불·계약·담당자·교육 일정은 미확인
18. 롤백 트리거 임계값·승인자·소요시간은 미확인이며 절차(기능 플래그 off→신규 계약 중단→기존 고객 유지)만 제안
19. 수행하지 않은 것: 시장 조사, 고객 인터뷰, 실제 코드 구현·배포·회귀 테스트. 본 산출물은 제공 자료 기반 설계 제안임
## growth-roadmap/ops_readiness.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap/ops_readiness.json)

S안/E안 대안 전제의 운영 준비 산출물을 작성했다. 확정 사실(요금·변동비·고정비·개발시간)과 추정치(고객 수)를 구분하고, 미확정 정책은 '미확인'으로 표시했다. 핵심 수치: S안 공헌이익 48,000원/곳, 기준 100곳 월이익 2,800,000원, 손익분기 42곳(2,000,000/48,000=41.67 올림). E안 공헌이익 240,000원/곳, 기준 18곳 월이익 2,320,000원, 손익분기 9곳(2,000,000/240,000=8.33 올림). 저/고 시나리오(70%/130% 버림): S안 70곳 이익 1,360,000원 / 130곳 이익 4,240,000원, E안 12곳 이익 880,000원 / 23곳 이익 3,520,000원. 개발시간: 공통 160시간(64+40+32+24), S안 합계 224시간(예산 내), E안 합계 256시간(16시간 초과). 두 안은 동시 선택 불가이므로 운영 준비도 단일 안 기준으로만 배포한다. 실제 시장 조사·고객 인터뷰·실제 테스트는 수행하지 않았으며, 모든 절차는 설계 제안이다.

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
  "e_fits_budget": false,
  "s_low_customers": 70,
  "s_high_customers": 130,
  "e_low_customers": 12,
  "e_high_customers": 23,
  "fixed_cost_monthly": 2000000,
  "dev_budget_hours": 240,
  "sla_value_confirmed": false,
  "refund_policy_confirmed": false,
  "training_schedule_confirmed": false,
  "owner_names_confirmed": false,
  "contract_terms_confirmed": false,
  "market_research_performed": false,
  "customer_interviews_performed": false,
  "rollback_trigger_thresholds_confirmed": false,
  "sales_support_scripts_drafted": true,
  "rollback_procedure_drafted": true,
  "handoff_plan_drafted": true,
  "s_e_simultaneous_launch": false
}
```

### Evidence

1. 공헌이익: S안 60,000-12,000=48,000원, E안 300,000-60,000=240,000원 (source 확정 사실)
2. 월이익: S안 48,000×100-2,000,000=2,800,000원, E안 240,000×18-2,000,000=2,320,000원
3. 손익분기: S안 ceil(2,000,000/48,000)=ceil(41.67)=42곳, E안 ceil(2,000,000/240,000)=ceil(8.33)=9곳
4. 저/고 시나리오: 기준의 70%/130% 버림 → S안 70/130곳, E안 12/23곳. 이익: S안 48,000×70-2,000,000=1,360,000 / 48,000×130-2,000,000=4,240,000, E안 240,000×12-2,000,000=880,000 / 240,000×23-2,000,000=3,520,000
5. 개발시간: 공통 64+40+32+24=160시간, S안 160+64=224시간(240 이내), E안 160+96=256시간(240 초과 16시간)
6. 영업 준비 표(S안): 가격/플랜=월 60,000원 단일 플랜(확정), 대상 고객군=소규모 팀(추정), 계약·청구 흐름=미확인, 경쟁 대비 포지셔닝=미확인
7. 영업 준비 표(E안): 가격/플랜=월 300,000원(확정), 대상 고객군=기업(추정), 계약·청구 흐름=미확인, 포지셔닝 한계=SSO/감사 기록 필수로 개발 예산 16시간 초과 가능성
8. 지원 준비 표: 온보딩 경로(S안 셀프 온보딩 64시간 개발 전제 / E안 SSO·감사 기록 96시간 전제), 문의 유형 분류=미확인, 에스컬레이션 경로=미확인, SLA=미확정 표시
9. 고객 안내 문구 초안(S안): '월 이용 요금 60,000원, 고객당 변동비 12,000원, 고정 운영비 월 2,000,000원은 확정 사항입니다. 기준 100곳은 추정치이며 실제 매출·계약은 보장되지 않습니다.'
10. 고객 안내 문구 초안(E안): '월 이용 요금 300,000원, 고객당 변동비 60,000원, 고정 운영비 월 2,000,000원은 확정 사항입니다. SSO/감사 기록이 필수 범위이며 개발 예산 240시간 대비 16시간 초과 가능성이 있습니다. 기준 18곳은 추정치로 매출·계약은 보장되지 않습니다.'
11. 롤백 트리거(정량, 임계값 미확인): 청구 오류율, 온보딩 실패율, SSO 인증 실패율, 지원 티켓 급증률, 손익분기 미달 지속 기간. 판단 주체·관찰 창·임계 수치는 미확인
12. 롤백 절차(제안): 1) 기능 플래그 off 2) 신규 계약 중단 3) 기존 고객 유지 정책 적용. 각 단계 승인자·소요시간은 미확인
13. 전달 계획(RACI 제안): A(제품·기술)로부터 확정 인터페이스·일정 수신, B(사업·분석)로부터 손익·시나리오 수치 수신, C 내부 영업/지원 분담. 담당자 이름·전달 시점은 미확인
14. 미확인 목록: SLA 수치, 환불 정책, 교육 일정, 담당자 이름, 계약 조건, 롤백 임계값, 문의 유형 분류, 에스컬레이션 경로
15. 수행하지 않은 것: 시장 조사, 고객 인터뷰, 실제 코드 구현·배포·회귀 테스트. 본 산출물은 정책·확정 사실 기반 설계 제안임
## growth-roadmap/tech_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap/tech_design.json)

S/E 확장안의 기술 설계(도메인·API·데이터·지표·MVP·일정)를 정리했다. 공통 필수 160h(요금/청구 64, 조직/좌석 40, 지표 수집 32, 운영 도구 24)를 기준선으로 확정. S=160+64=224h로 예산 240h 대비 16h 여유, E=160+96=256h로 16h 부족. 동시 개발로 총시간이 줄어든다는 가정은 배제했다. 도메인 변경: 요금제/구독(Plan, Subscription, BillingCycle), 조직/좌석(Organization, Member, SeatAssignment), 온보딩(S 셀프서비스: OnboardingFlow, InviteToken), 인증/감사(E: SSOConnection, AuditEvent). API 변경: 공통(요금/청구, 조직/좌석, 지표 이벤트)과 안별 전용(S 온보딩 API, E SSO/감사 API)을 구분. 데이터 변경: 신규 테이블/컬럼, 마이그레이션 순서, 지표 이벤트 스키마, 감사 로그 보존. 측정 지표: 활성 조직 수, 좌석 사용률, 온보딩 완료율(S), SSO 로그인 성공률·감사 이벤트 커버리지(E), 청구 성공률, 이탈률. MVP: 공통 4개 모듈 포함, S는 셀프 온보딩 포함, E는 SSO/감사 포함하되 16h 부족분 처리를 위해 감사 로그 고급 필터/리포트, SSO 다중 IdP 확장을 분기 밖으로 이월. 일정: 1) 공통 도메인·데이터 스키마 → 2) 공통 API·청구 → 3) 지표 수집 → 4) 운영 도구 → 5) S 온보딩 또는 E SSO/감사(안별 분기). E는 5단계 일부가 다음 분기로 이월. 모순 점검: E안은 256h>240h이므로 'E안 전체를 이번 분기에 출시' 권고는 성립하지 않음. 미확인 사항: 실제 개발 속도, SSO IdP별 난이도, 지표 수집 인프라 비용. 실제 구현/테스트는 수행하지 않았으며 설계 제안임을 명시한다.

### Facts

```json
{
  "common_dev_hours": 160,
  "s_total_dev_hours": 224,
  "e_total_dev_hours": 256,
  "e_budget_shortfall_hours": 16,
  "s_fits_budget": true,
  "e_fits_budget": false,
  "s_margin": 16,
  "e_margin": -16,
  "s_profit": 0,
  "e_profit": 0,
  "s_break_even": 0,
  "e_break_even": 0,
  "s_low_profit": 0,
  "e_low_profit": 0,
  "s_high_profit": 0,
  "e_high_profit": 0
}
```

### Evidence

1. 공통 필수: 64(요금/청구)+40(조직/좌석)+32(지표 수집)+24(운영 도구)=160h.
2. S 총 개발시간: 160+64(셀프 온보딩)=224h. 예산 240h 대비 여유 240-224=16h → s_fits_budget=true, s_margin=16.
3. E 총 개발시간: 160+96(SSO/감사)=256h. 예산 240h 대비 부족 256-240=16h → e_fits_budget=false, e_margin=-16, e_budget_shortfall_hours=16.
4. 동시 개발로 총시간이 줄어든다는 가정은 배제(자료 지시).
5. 도메인 변경 개요(엔터티·관계): 요금제/구독(Plan 1-N Subscription, Subscription 1-N BillingCycle), 조직/좌석(Organization 1-N Member, Organization 1-N SeatAssignment, Member 1-1 SeatAssignment), 온보딩 S(OnboardingFlow 1-N InviteToken, Organization 1-1 OnboardingFlow), 인증/감사 E(Organization 1-N SSOConnection, Organization 1-N AuditEvent).
6. API 변경 개요: 공통 - 요금/청구 API(GET /plans, POST /subscriptions, POST /billing/charge), 조직/좌석 API(GET/POST /organizations, POST /organizations/{id}/seats), 지표 이벤트 API(POST /metrics/events). S 전용 - 온보딩 API(POST /onboarding/start, POST /onboarding/complete). E 전용 - SSO API(POST /sso/connections, GET /sso/login), 감사 API(GET /audit/events). 권한: 공통은 org_admin, S는 self-service, E는 org_admin+security_admin.
7. 데이터 변경 개요: 신규 테이블 plans, subscriptions, billing_cycles, organizations, members, seat_assignments, metric_events, onboarding_flows, invite_tokens, sso_connections, audit_events. 마이그레이션 순서: plans→organizations→members→seat_assignments→subscriptions→billing_cycles→metric_events→(S)onboarding_flows→invite_tokens / (E)sso_connections→audit_events. 지표 이벤트 스키마: event_id, org_id, event_type, occurred_at, payload(json). 감사 로그 보존: audit_events는 append-only, 보존 기간 정책 필요(미확정).
8. 측정 지표: 활성 조직 수(일 1회), 좌석 사용률=할당 좌석/구매 좌석(일 1회), 온보딩 완료율(S, 온보딩 종료 시), SSO 로그인 성공률(E, 로그인 시), 감사 이벤트 커버리지(E, 일 1회), 청구 성공률(청구 시), 이탈률(월 1회).
9. MVP 포함/제외: 포함 - 공통 4개 모듈, S 셀프 온보딩, E SSO/감사 기본. 제외(분기 밖 이월) - 감사 로그 고급 필터/리포트, SSO 다중 IdP 확장. 이는 E 16h 부족분을 처리하기 위함.
10. 단계별 일정(선후 관계): 1) 공통 도메인·데이터 스키마 → 2) 공통 API·청구 → 3) 지표 수집 → 4) 운영 도구 → 5) S 온보딩 또는 E SSO/감사(안별 분기). 1→2→3→4는 순차, 5는 4 이후. E는 5단계 일부(고급 필터/리포트, 다중 IdP)가 다음 분기로 이월.
11. 모순 점검: E안은 256h>240h로 예산 초과이므로 'E안 전체를 이번 분기에 출시'라는 권고는 성립하지 않음. S안은 224h≤240h로 이번 분기 출시 가능.
12. 미확인 사항: 실제 개발 속도, SSO IdP별 난이도, 지표 수집 인프라 비용, 감사 로그 보존 기간 정책.
13. 실제 구현/테스트는 수행하지 않았으며, 본 산출물은 설계 제안임. 코드 조각은 summary/evidence 문자열로만 제시.