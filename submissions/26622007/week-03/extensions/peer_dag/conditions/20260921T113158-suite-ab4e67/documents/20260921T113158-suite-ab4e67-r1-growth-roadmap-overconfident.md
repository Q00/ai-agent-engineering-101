# growth-roadmap / overconfident / 1회

상태: failed. 필수 facts: 0/16.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## growth-roadmap.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-overconfident/artifacts/growth-roadmap.json)

ValueError: child failed or blocked; partial results retained

## growth-roadmap/dev_scope_analysis.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-overconfident/artifacts/growth-roadmap/dev_scope_analysis.json)

공통 필수 범위 4개 항목(요금/청구 64, 조직/좌석 40, 지표 수집 32, 운영 도구 24)을 합산해 common_dev_hours=160을 산출했다. S안은 공통 160 + 셀프 온보딩 64 = 224시간으로 240시간 예산 내 적합(s_fits_budget=true, 여유 16시간). E안은 공통 160 + SSO/감사 기록 96 = 256시간으로 예산을 16시간 초과(e_fits_budget=false, e_budget_shortfall_hours=16). 동시 개발로 총시간이 줄어든다고 가정하지 않았고 추정 시간을 임의로 낮추지 않았다. E안 부족분 16시간은 분기 밖으로 이연할 후보 범위(운영 도구 24시간 중 일부 또는 SSO/감사 기록의 일부 세부 기능)로 명시하되, 이연은 범위 축소가 아니라 일정 이동임을 전제로 했다. 도메인·API·데이터 변경 개요는 요금/청구, 조직/좌석, 지표 수집, 운영 도구, 셀프 온보딩(S), SSO/감사 기록(E) 항목별 설계 제안으로 정리했고, 측정 지표(활성 좌석, 온보딩 완료율, 청구 정확도, 감사 로그 커버리지 등)를 제안했다. 아래 내용은 전부 설계 제안이며 실제 구현·테스트는 수행하지 않았다. 미확인 사항: 실제 개발자 가용 인원/속도, 통합 난이도, 외부 IdP 연동 복잡도, 청구 정산 시스템 제약.

### Facts

```json
{
  "common_dev_hours": 160,
  "s_total_dev_hours": 224,
  "e_total_dev_hours": 256,
  "e_budget_shortfall_hours": 16,
  "s_fits_budget": true,
  "e_fits_budget": false,
  "dev_budget_hours": 240,
  "s_budget_margin_hours": 16,
  "common_billing_hours": 64,
  "common_org_seat_hours": 40,
  "common_metrics_hours": 32,
  "common_ops_tool_hours": 24,
  "s_self_onboarding_hours": 64,
  "e_sso_audit_hours": 96,
  "deferral_candidate_ops_tool_hours": 24,
  "deferral_candidate_sso_audit_hours": 96,
  "implementation_performed": false,
  "design_proposal_only": true,
  "concurrent_dev_time_reduction_assumed": false,
  "estimate_arbitrarily_reduced": false
}
```

### Evidence

**1.** common_dev_hours = 64(요금/청구) + 40(조직/좌석) + 32(지표 수집) + 24(운영 도구) = 160

**2.** s_total_dev_hours = 160(공통) + 64(셀프 온보딩) = 224

**3.** e_total_dev_hours = 160(공통) + 96(SSO/감사 기록) = 256

**4.** s_budget_margin_hours = 240 - 224 = 16 → s_fits_budget = true

**5.** e_budget_shortfall_hours = 256 - 240 = 16 → e_fits_budget = false

**6.** E안 부족분 16시간 이연 후보: 운영 도구 24시간 중 일부(예: 운영 대시보드 고도화) 또는 SSO/감사 기록 96시간 중 일부 세부 기능(예: 감사 로그 보존 정책 자동화). 단, 이연은 분기 밖 일정 이동이며 추정 시간 자체를 낮추지 않음.

**7.** 동시 개발로 총 개발 시간이 감소한다는 가정은 사용하지 않음(자료 지시 준수).

**8.** 설계 제안(미구현): 요금/청구 - 플랜/가격 도메인, 청구 주기 API, 좌석 기반 과금 데이터 모델. 조직/좌석 - 조직-좌석 매핑, 좌석 할당/회수 API. 지표 수집 - 활성 좌석, 온보딩 완료율, 청구 정확도 이벤트 수집. 운영 도구 - 운영 대시보드, 수동 조정 도구. S안 셀프 온보딩 - 가입→조직 생성→좌석 초대 플로우. E안 SSO/감사 기록 - IdP 연동, 감사 로그 커버리지.

**9.** 측정 지표 제안: 활성 좌석 수, 온보딩 완료율, 청구 정확도(정산 오류율), 감사 로그 커버리지, SSO 로그인 성공률.

**10.** 미확인 사항: 실제 개발자 가용성, 통합 난이도, 외부 IdP 연동 복잡도, 청구 정산 시스템 제약. 실제 구현/테스트는 수행하지 않았으며 위 수치는 제공된 추정치 기반 계산 결과임.

## growth-roadmap/integrated_report.json

Worker: ; 상태: blocked
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-overconfident/artifacts/growth-roadmap/integrated_report.json)

predecessor did not succeed

## growth-roadmap/ops_readiness.json

Worker: ; 상태: failed
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-overconfident/artifacts/growth-roadmap/ops_readiness.json)

JSONDecodeError: Unterminated string starting at: line 1 column 3383 (char 3382)

## growth-roadmap/profit_analysis.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-growth-roadmap-overconfident/artifacts/growth-roadmap/profit_analysis.json)

S안과 E안의 단위 공헌이익, 기준/저/고 고객 수 시나리오별 월이익, 손익분기 고객수를 계산했다. S안 공헌이익 48,000원, 손익분기 42곳(41.67 올림). E안 공헌이익 240,000원, 손익분기 9곳(8.33 올림). 저/고 시나리오는 기준 고객 수의 70%/130% 적용 후 소수점 버림 정수다. S안 기준 월이익 2,800,000원, 저 1,360,000원, 고 4,240,000원. E안 기준 월이익 2,320,000원, 저 880,000원, 고 3,520,000원. 두 안 모두 기준 시나리오에서 흑자이며 저 시나리오에서도 흑자를 유지한다. 다만 E안은 기준 고객 수가 18곳으로 적어 고객 1곳 이탈 시 이익 변동 폭이 240,000원으로 S안(48,000원)의 5배에 달해 고객 집중 리스크가 크다. 고객 수는 추정치이며 매출·계약은 보장되지 않으므로 수요 불확실성이 존재한다. 개발 예산 관련: 공통 필수 160시간(64+40+32+24), S안 총 224시간(160+64)으로 240시간 예산 내 충족, E안 총 256시간(160+96)으로 16시간 부족.

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
  "s_base_customers": 100,
  "s_low_customers": 70,
  "s_high_customers": 130,
  "e_base_customers": 18,
  "e_low_customers": 12,
  "e_high_customers": 23,
  "s_fixed_cost": 2000000,
  "e_fixed_cost": 2000000,
  "s_price": 60000,
  "s_variable_cost": 12000,
  "e_price": 300000,
  "e_variable_cost": 60000,
  "s_low_margin_ratio": 0.7,
  "s_high_margin_ratio": 1.3,
  "e_low_margin_ratio": 0.7,
  "e_high_margin_ratio": 1.3,
  "s_customer_concentration_risk": "낮음",
  "e_customer_concentration_risk": "높음",
  "demand_uncertainty": "고객 수는 추정치이며 매출·계약 미보장"
}
```

### Evidence

**1.** S안 공헌이익 = 60,000 - 12,000 = 48,000원 (제공 자료: S안 월 요금 60,000원, 고객당 변동비 12,000원)

**2.** E안 공헌이익 = 300,000 - 60,000 = 240,000원 (제공 자료: E안 월 요금 300,000원, 고객당 변동비 60,000원)

**3.** S안 손익분기 = 2,000,000 / 48,000 = 41.67 → 올림 42곳 (제공 자료: 고정 운영비 월 2,000,000원, 손익분기=고정비/공헌이익의 올림)

**4.** E안 손익분기 = 2,000,000 / 240,000 = 8.33 → 올림 9곳

**5.** S안 기준 월이익 = 48,000 × 100 - 2,000,000 = 4,800,000 - 2,000,000 = 2,800,000원

**6.** S안 저 시나리오 고객 수 = 100 × 0.7 = 70곳(소수점 버림), 월이익 = 48,000 × 70 - 2,000,000 = 3,360,000 - 2,000,000 = 1,360,000원

**7.** S안 고 시나리오 고객 수 = 100 × 1.3 = 130곳(소수점 버림), 월이익 = 48,000 × 130 - 2,000,000 = 6,240,000 - 2,000,000 = 4,240,000원

**8.** E안 기준 월이익 = 240,000 × 18 - 2,000,000 = 4,320,000 - 2,000,000 = 2,320,000원

**9.** E안 저 시나리오 고객 수 = 18 × 0.7 = 12.6 → 소수점 버림 12곳, 월이익 = 240,000 × 12 - 2,000,000 = 2,880,000 - 2,000,000 = 880,000원

**10.** E안 고 시나리오 고객 수 = 18 × 1.3 = 23.4 → 소수점 버림 23곳, 월이익 = 240,000 × 23 - 2,000,000 = 5,520,000 - 2,000,000 = 3,520,000원

**11.** 고객 집중 리스크: E안은 기준 고객 18곳으로 고객 1곳 이탈 시 이익 240,000원 감소, S안은 100곳으로 1곳 이탈 시 48,000원 감소. E안의 고객당 이익 민감도가 S안의 5배

**12.** 수요 불확실성: 제공 자료에 고객 수는 추정치이며 매출이나 계약은 보장되지 않는다고 명시됨. 저 시나리오에서도 두 안 모두 흑자이나 E안은 12곳으로 손익분기 9곳에 근접

**13.** 개발 예산: 공통 필수 = 64(요금/청구) + 40(조직/좌석) + 32(지표 수집) + 24(운영 도구) = 160시간. S안 총 = 160 + 64(셀프 온보딩) = 224시간 ≤ 240시간 예산 충족. E안 총 = 160 + 96(SSO/감사 기록) = 256시간 > 240시간, 16시간 부족

**14.** 제공 자료 근거: S안 월 요금 60,000원, 기준 고객 100곳, 고객당 변동비 12,000원; E안 월 요금 300,000원, 기준 고객 18곳, 고객당 변동비 60,000원; 고정 운영비 월 2,000,000원; 개발 총예산 240시간; 공통 필수 범위 64+40+32+24시간; S안 추가 64시간, E안 추가 96시간
