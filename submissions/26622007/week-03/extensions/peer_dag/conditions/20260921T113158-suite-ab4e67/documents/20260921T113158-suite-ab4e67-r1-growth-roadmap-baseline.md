# growth-roadmap / baseline / 1회

상태: failed. 필수 facts: 0/16.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## growth-roadmap.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-baseline/artifacts/growth-roadmap.json)

ValueError: child failed or blocked; partial results retained

## growth-roadmap/financial_analysis.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-baseline/artifacts/growth-roadmap/financial_analysis.json)

S안과 E안의 단위 경제성, 기준/저/고 시나리오 손익, 손익분기 고객수, 개발시간 예산 적합성을 계산했다. S안: 공헌이익 48,000원, 기준 100곳 이익 2,800,000원, 손익분기 42곳, 저(70곳) 1,360,000원, 고(130곳) 4,240,000원. E안: 공헌이익 240,000원, 기준 18곳 이익 2,320,000원, 손익분기 9곳, 저(12곳) 880,000원, 고(23곳) 3,520,000원. 개발시간은 공통 160시간, S안 총 224시간으로 240시간 예산 내 적합, E안 총 256시간으로 16시간 초과다. 리스크 측면에서 S안은 100곳 분산으로 고객 집중 리스크가 낮고, E안은 18곳 소수 고객 의존으로 이탈 1곳당 이익 충격이 크다(공헌이익 240,000원/곳). 수요 불확실성은 저/고 시나리오 폭으로 보면 S안 이익 변동폭 2,880,000원(1,360,000~4,240,000), E안 2,640,000원(880,000~3,520,000)으로 절대 변동은 유사하나 E안은 기준 대비 상대 변동이 크다. 고객 수는 추정치이며 매출·계약은 보장되지 않는다.

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

**1.** S 공헌이익 = 요금 60,000 - 변동비 12,000 = 48,000원

**2.** S 기준 이익 = 48,000×100 - 2,000,000 = 4,800,000 - 2,000,000 = 2,800,000원

**3.** S 손익분기 = ceil(2,000,000/48,000) = ceil(41.67) = 42곳

**4.** S 저 = floor(100×0.7)=70곳, 이익 = 48,000×70 - 2,000,000 = 3,360,000 - 2,000,000 = 1,360,000원

**5.** S 고 = floor(100×1.3)=130곳, 이익 = 48,000×130 - 2,000,000 = 6,240,000 - 2,000,000 = 4,240,000원

**6.** E 공헌이익 = 요금 300,000 - 변동비 60,000 = 240,000원

**7.** E 기준 이익 = 240,000×18 - 2,000,000 = 4,320,000 - 2,000,000 = 2,320,000원

**8.** E 손익분기 = ceil(2,000,000/240,000) = ceil(8.33) = 9곳

**9.** E 저 = floor(18×0.7)=12곳, 이익 = 240,000×12 - 2,000,000 = 2,880,000 - 2,000,000 = 880,000원

**10.** E 고 = floor(18×1.3)=23곳, 이익 = 240,000×23 - 2,000,000 = 5,520,000 - 2,000,000 = 3,520,000원

**11.** 공통 개발시간 = 64(요금/청구)+40(조직/좌석)+32(지표 수집)+24(운영 도구) = 160시간

**12.** S 총 개발시간 = 160 + 64(셀프 온보딩) = 224시간 ≤ 240시간 예산 → 적합

**13.** E 총 개발시간 = 160 + 96(SSO/감사 기록) = 256시간 > 240시간 예산 → 16시간 부족

**14.** 고객 집중 리스크: E안은 기준 18곳으로 1곳 이탈 시 공헌이익 240,000원 감소, S안은 100곳으로 1곳 이탈 시 48,000원 감소

**15.** 수요 불확실성: 저/고 시나리오는 제공된 기준×70%/130% 내림 가정이며, 고객 수는 추정치로 매출·계약이 보장되지 않음

**16.** 개발시간 추정은 제공된 값을 그대로 사용했으며 임의로 낮추거나 동시 개발로 총시간이 줄어든다고 가정하지 않음

## growth-roadmap/integration.json

Worker: ; 상태: blocked
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-baseline/artifacts/growth-roadmap/integration.json)

predecessor did not succeed

## growth-roadmap/ops_readiness.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-baseline/artifacts/growth-roadmap/ops_readiness.json)

JSONDecodeError: Unterminated string starting at: line 1 column 3354 (char 3353)

## growth-roadmap/tech_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-baseline/artifacts/growth-roadmap/tech_design.json)

S/E안 MVP의 기술 설계 제안(구현·테스트 미수행)을 정리한다. 공통 필수 범위는 요금/청구 64h, 조직/좌석 40h, 지표 수집 32h, 운영 도구 24h로 합계 160h이며, S안은 셀프 온보딩 64h를 더해 224h, E안은 SSO/감사 기록 96h를 더해 256h가 된다. 240h 예산 기준 S안은 16h 여유, E안은 16h 부족이다. E안 부족분 16h는 감사 기록 고도화(세분 이벤트·보존 정책·내보내기)와 운영 도구 일부(운영 대시보드 고도화)를 분기 밖으로 미루어 흡수하는 안을 제안한다. 동시 개발로 총시간이 줄어든다고 가정하지 않았고 추정 시간을 임의로 낮추지 않았다. 아래는 설계 제안이며 실제 코드 작성·배포·테스트는 수행하지 않았다.

[도메인·API·데이터 변경 개요]
1) 요금/청구(공통 64h): 도메인 Plan, Subscription, Invoice, PaymentMethod. API POST /plans, POST /subscriptions, GET /invoices/{id}, POST /billing/run(월 마감). 데이터: plans(plan_id, tier, monthly_fee, seat_limit), subscriptions(sub_id, org_id, plan_id, status, started_at), invoices(invoice_id, sub_id, period, amount, status), invoice_lines. 지표: 청구 정확도(수동 보정 건수/총 청구), 월 마감 소요시간, 결제 실패율.
2) 조직/좌석(공통 40h): 도메인 Organization, Seat, Member, Role. API POST /orgs, POST /orgs/{id}/seats, PATCH /seats/{id}, GET /orgs/{id}/usage. 데이터: orgs(org_id, name, tier), seats(seat_id, org_id, member_id, state, assigned_at), members. 지표: 좌석 활성화율(활성 좌석/할당 좌석), 좌석 회수율, 조직당 평균 좌석수.
3) 지표 수집(공통 32h): 도메인 Event, MetricSnapshot. API POST /events(배치), GET /metrics/usage. 데이터: events(event_id, org_id, type, ts, props), metric_daily(org_id, date, active_seats, sessions, minutes). 지표: 이벤트 수집 지연, 일별 활성 조직 수, 데이터 완전성(누락 일수).
4) 운영 도구(공통 24h): 도메인 AdminAction, AuditTrail(경량). API GET /admin/orgs, POST /admin/orgs/{id}/suspend, GET /admin/health. 데이터: admin_actions(actor, action, target, ts), 운영 대시보드 뷰. 지표: 운영 개입 건수, 평균 처리 시간, 장애 감지 시간.
5) S안 셀프 온보딩(추가 64h): 도메인 OnboardingFlow, Invite, Checklist. API POST /onboarding/start, POST /invites, GET /onboarding/status. 데이터: onboarding(org_id, step, completed_at), invites(code, org_id, expires_at). 지표: 온보딩 완료율, 첫 결제 전환율, 단계별 이탈률.
6) E안 SSO/감사 기록(추가 96h): 도메인 IdPConfig, SSOAssertion, AuditLog. API POST /sso/config(SAML/OIDC 메타데이터), GET /sso/login(리다이렉트), POST /sso/acs, GET /audit/logs(필터·페이지). 데이터: idp_configs(org_id, protocol, metadata, cert), audit_logs(log_id, org_id, actor, action, resource, ts, ip), 보존 정책. 지표: SSO 로그인 성공률, 프로비저닝 성공률, 감사 로그 커버리지(감사 대상 액션 중 기록 비율).

[단계별 일정(선후 관계)]
1단계 공통 기반(160h): 요금/청구 → 조직/좌석 → 지표 수집 → 운영 도구 순으로 의존. 청구가 조직/좌석 스키마에 의존하고, 지표 수집이 조직/좌석 이벤트에 의존하므로 이 순서를 유지한다.
2단계 분기: S안은 셀프 온보딩(64h)을 조직/좌석 완료 후 진행(총 224h, 예산 내 16h 여유). E안은 SSO/감사 기록(96h)을 조직/좌석 완료 후 진행(총 256h, 예산 16h 초과).
3단계 E안 조정: 부족분 16h를 감사 기록 고도화(세분 이벤트·보존 정책·내보내기)와 운영 도구 고도화(대시보드 심화)를 분기 밖으로 이월하여 흡수. SSO 기본 로그인·프로비저닝과 감사 로그 기본 기록은 분기 내 포함.

[모순·미확인 사항]
- E안은 240h 예산에서 16h 초과이므로 범위 축소 또는 일정 연장 없이는 분기 내 완료 불가. 본 설계는 범위 이월을 전제로 한다.
- 고객 수·매출은 추정치이며 계약 보장이 없다. 시장 조사·고객 인터뷰는 수행하지 않았다.
- 실제 구현·테스트·배포는 수행하지 않았으므로 성능·보안 검증은 미검증이다.

### Facts

```json
{
  "s_margin": 16,
  "e_margin": -16,
  "s_profit": 0,
  "e_profit": 0,
  "s_break_even": 0,
  "e_break_even": 0,
  "s_low_profit": 0,
  "e_low_profit": 0,
  "s_high_profit": 0,
  "e_high_profit": 0,
  "common_dev_hours": 160,
  "s_total_dev_hours": 224,
  "e_total_dev_hours": 256,
  "e_budget_shortfall_hours": 16,
  "s_fits_budget": true,
  "e_fits_budget": false
}
```

### Evidence

**1.** 공통 개발시간: 64(요금/청구)+40(조직/좌석)+32(지표 수집)+24(운영 도구)=160시간.

**2.** S안 총 개발시간: 160+64(셀프 온보딩)=224시간. 240-224=16시간 여유(s_margin=16, s_fits_budget=true).

**3.** E안 총 개발시간: 160+96(SSO/감사 기록)=256시간. 240-256=-16시간 부족(e_margin=-16, e_budget_shortfall_hours=16, e_fits_budget=false).

**4.** E안 부족분 16시간은 감사 기록 고도화(세분 이벤트·보존 정책·내보내기)와 운영 도구 고도화를 분기 밖으로 이월하여 흡수하는 안을 제안.

**5.** 추정 시간을 임의로 낮추지 않았고, 동시 개발로 총시간이 줄어든다고 가정하지 않았다.

**6.** 손익 관련 facts(s_profit, e_profit, s_break_even, e_break_even, s_low_profit, e_low_profit, s_high_profit, e_high_profit)는 본 기술 설계 작업 범위 밖이므로 0으로 두었다. 사업 분석 Worker의 산출물로 대체되어야 한다.

**7.** 설계 제안과 실제 구현/테스트 수행을 구분: 본 결과는 도메인·API·데이터 스키마 개요와 일정 제안이며, 코드 작성·배포·테스트는 수행하지 않았다(미검증).

**8.** 자료에 없는 시장 조사나 고객 인터뷰는 수행하지 않았다.
