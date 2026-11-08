# growth-roadmap / baseline — 429 복구

상태: succeeded. facts: 16/16.
모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.

## growth-roadmap.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline/artifacts/growth-roadmap.json)

S안(소규모 팀)과 E안(기업용) 확장안을 경영진이 결정할 수 있도록 손익·예산·기술 설계·사업 권고·운영 준비를 통합했다. 모든 수치는 제공 자료 기반 계산이며 실제 구현·테스트·부하측정·시장조사·고객 인터뷰는 수행하지 않았다(설계 제안 단계).

[1. 손익·예산 기준선]
S안: 공헌이익 48,000원, 기준 100곳 월이익 2,800,000원, 손익분기 42곳, 저(70곳) 1,360,000원, 고(130곳) 4,240,000원. E안: 공헌이익 240,000원, 기준 18곳 월이익 2,320,000원, 손익분기 9곳, 저(12곳) 880,000원, 고(23곳) 3,520,000원. 두 안 모두 저 시나리오에서 흑자. 공통 개발 160h, S 총 224h(예산 240h 이내, 적합), E 총 256h(16h 초과, 부적합).

[2. 비교표]
항목 | S안 | E안
월 요금 | 60,000원 | 300,000원
고객당 변동비 | 12,000원 | 60,000원
공헌이익/고객 | 48,000원 | 240,000원
기준 고객수 | 100곳 | 18곳
기준 월이익 | 2,800,000원 | 2,320,000원
손익분기 고객수 | 42곳 | 9곳
저 시나리오(70%) | 70곳, 1,360,000원 | 12곳, 880,000원
고 시나리오(130%) | 130곳, 4,240,000원 | 23곳, 3,520,000원
이익 변동폭 | 2,880,000원 | 2,640,000원
개발 총시간 | 224h | 256h
예산 240h 적합 | 적합 | 16h 초과

[3. 선택 근거와 반증 가설]
근거: ① 기준 월이익 S 2,800,000원 > E 2,320,000원(차 480,000원). ② 예산 적합성 S 224h ≤ 240h, E 256h로 16h 초과. ③ 저 시나리오 방어력 S 1,360,000원 > E 880,000원. ④ 손익분기 여유 S 58곳, E 9곳. 종합적으로 현 자료 기준 S안 우위, E안은 16h 초과분을 분기 밖으로 이연해야 실행 가능.
H1 'S안이 E안보다 우월': 반증 조건 E 실제 고객 30곳 이상이면 E 월이익 5,200,000원으로 S 기준을 상회, 또는 S 고객 42곳 미만이면 S 적자. 관찰 지표: 분기 내 계약/활성 고객수.
H2 'E안은 예산상 실행 불가': 반증 조건 SSO/감사 기록 범위를 16h 이상 축소해 총 240h 이내 진입. 관찰 지표: 실제 개발 소요시간.
H3 '두 안 모두 저 시나리오 흑자': 반증 조건 S 42곳 미만 또는 E 9곳 미만. 관찰 지표: 월별 실측 고객수.

[4. MVP 포함/제외 범위]
포함(공통 4모듈, 160h): 요금/청구 64h, 조직/좌석 40h, 지표 수집 32h, 운영 도구 24h. S 추가: 셀프 온보딩 64h. E 추가: SSO/감사 기록 96h. E 이연(분기 밖): (a) 감사 로그 보존/내보내기 고도화, (b) SSO 예외 처리 고급화(IdP 메타데이터 자동 갱신·다중 IdP·SCIM), (c) 감사 로그 검색 인덱스/대시보드. 분기 내 유지: SSO SP-initiated 로그인·세션 발급/만료, 감사 로그 append+해시 체인 기록, 관리자 조회 API 최소 버전.

[5. 코드·데이터 변경 개요 (설계 제안, 미구현)]
요금/청구: Plan/Subscription/Invoice, POST /plans, POST /subscriptions, PATCH /subscriptions/{id}, GET /invoices, POST /invoices/{id}/retry; plans·subscriptions·invoices·invoice_line_items 신설, invoice 상태 전이(pending→issued→paid/failed) 단일화로 이중 청구 방지.
조직/좌석: Organization/Seat/Invitation, POST /orgs, POST /orgs/{id}/seats, POST /invitations/{token}/accept; seats (org_id,user_id) 유니크, 좌석 상한은 plan.tier 파생.
지표 수집: MetricEvent/MetricDaily, POST /events(배치), GET /metrics/summary; events append-only(파티셔닝=occurred_at), metrics_daily 일 롤업.
운영 도구: AdminAction/FeatureFlag, GET /admin/orgs, POST /admin/orgs/{id}/suspend, POST /admin/flags/{key}/toggle, GET /admin/audit.
S 추가(셀프 온보딩): OnboardingStep/OnboardingProgress, POST /onboarding/self-serve-signup(조직+플랜+좌석 한 흐름); 측정 지표=온보딩 완료율·단계별 이탈률·가입→첫 좌석 활성화 소요시간·셀프 가입 비중.
E 추가(SSO/감사): SSOConnection/SSOSession/AuditLog, POST /orgs/{id}/sso/connections, GET /sso/login(SP-initiated), POST /sso/acs, GET /audit/logs, POST /audit/export; audit_logs append-only+해시 체인; 측정 지표=SSO 로그인 성공률·SSO p95 지연·감사 로그 커버리지·내보내기 성공률·세션 만료 후 재인증 비율.

[6. 선후 관계 단계별 일정 (담당·전달)]
Stage 0(선행 없음, 4모듈 병렬): 담당 A(제품·기술). 전달=도메인/API/스키마, 마이그레이션.
Stage 1(Stage 0 의존): S 셀프 온보딩 또는 E SSO/감사 기록 — 담당 A.
Stage 2(Stage 1 의존): 지표·운영 도구 연동 — 담당 A(구현), B(지표 정의·손익 대조).
Stage 3(Stage 2 의존): 영업/지원팀 준비 및 파일럿 — 담당 C(운영·커뮤니케이션), B(사업 검증).

[7. 영업/지원팀 준비 및 고객 안내]
공통 범위 지원팀 인수인계 문서·상담 절차 확정. S 선택 시 셀프 온보딩 가이드·FAQ·에스컬레이션 경로. E 선택 시 SSO/감사 대응 스크립트·SSO 실패 대응·감사 로그 조회 처리 절차(단, 16h 부족분 해소 전 출시 불가). 고객 수(100/18)와 매출·계약은 추정치로 보장 없음, 저/고 시나리오는 계획 가정임을 안내. 손익분기(42/9)는 내부 지표로 고객 약속으로 표현 금지. E 이연 범위 사전 고지.

[8. 출시 중단·롤백 기준 (5건)]
SSO 로그인 성공률 임계 미달, 감사 로그 기록 누락, 청구 이중 생성, 온보딩 완료율 급락, 지표 수집 지연 목표 초과. 트리거 발생 시 해당 모듈 배포 중단·이전 버전 복원, 이연 범위는 분기 밖 유지. 저 시나리오 미달(S 70곳 미만/E 12곳 미만) 시 재검토, E 16h 미해소 시 E 출시 중단, 요금·환불·SLA 미확정 시 출시 중단, 두 안 동시 선택 금지 위반 시 중단.

[9. 모순·미확인 사항]
E안 240h 예산 vs 256h 필수 범위 구조적 모순 → 이연 없이 분기 내 완료 불가. 고객 수는 추정치로 매출·계약 보장 없음. 성능 목표(지표 수집 지연, SSO p95)는 원자료에 수치 없어 설계 가정. 환불 규정·SLA·실제 계약 조건·고객 확보 일정은 제공 자료에 없어 미확인. 실제 구현/테스트/부하 측정 미수행 → 모든 성능·정확도 수치 미검증. S안은 예산 내 정합, E안은 16h 초과로 이연 필요.

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
  "s_profit_advantage": 480000,
  "s_break_even_buffer": 58,
  "e_break_even_buffer": 9,
  "s_low_customers": 70,
  "s_high_customers": 130,
  "e_low_customers": 12,
  "e_high_customers": 23,
  "s_profit_swing": 2880000,
  "e_profit_swing": 2640000,
  "both_profitable_in_low": true,
  "common_modules_count": 4,
  "s_extra_hours": 64,
  "e_extra_hours": 96,
  "e_deferred_scope_count": 3,
  "stages_count": 4,
  "rollback_triggers_count": 5,
  "simultaneous_selection_allowed": false,
  "implementation_done": false,
  "tests_executed": false,
  "design_only": true,
  "market_research_performed": false,
  "customer_interviews_performed": false,
  "refund_policy_confirmed": false,
  "sla_confirmed": false,
  "contract_terms_confirmed": false,
  "customer_acquisition_schedule_confirmed": false
}
```

### Evidence

1. S 공헌이익 = 60,000 - 12,000 = 48,000원; E 공헌이익 = 300,000 - 60,000 = 240,000원
2. S 기준 월이익 = 48,000×100 - 2,000,000 = 2,800,000원; E 기준 월이익 = 240,000×18 - 2,000,000 = 2,320,000원; 차 = 480,000원
3. S 손익분기 = ceil(2,000,000/48,000) = ceil(41.6667) = 42곳; E 손익분기 = ceil(2,000,000/240,000) = ceil(8.3333) = 9곳
4. 저/고 시나리오 floor 적용: S 저=floor(100×0.7)=70, S 고=floor(100×1.3)=130, E 저=floor(18×0.7)=12, E 고=floor(18×1.3)=23
5. S 저 월이익 = 48,000×70-2,000,000 = 1,360,000원; S 고 = 48,000×130-2,000,000 = 4,240,000원; 변동폭 = 2,880,000원
6. E 저 월이익 = 240,000×12-2,000,000 = 880,000원; E 고 = 240,000×23-2,000,000 = 3,520,000원; 변동폭 = 2,640,000원
7. 공통 개발시간 = 요금/청구 64 + 조직/좌석 40 + 지표 수집 32 + 운영 도구 24 = 160시간
8. S 총 개발시간 = 160 + 셀프 온보딩 64 = 224시간 ≤ 240시간 → s_fits_budget=true
9. E 총 개발시간 = 160 + SSO/감사 기록 96 = 256시간 > 240시간 → 부족분 = 256-240 = 16시간, e_fits_budget=false
10. 손익분기 여유: S = 100-42 = 58곳; E = 18-9 = 9곳
11. 두 안 모두 저 시나리오 월이익 양수(1,360,000원, 880,000원)로 흑자 유지
12. E 이연 후보 3건: 감사 로그 보존/내보내기 고도화, SSO 예외 처리 고급화, 감사 로그 검색 인덱스/대시보드; 분기 내 유지: SSO SP-initiated 로그인·세션 발급/만료, 감사 로그 append+해시 체인, 관리자 조회 API 최소 버전
13. 일정 4단계: Stage 0(공통 4모듈 병렬) → Stage 1(S 셀프 온보딩 또는 E SSO/감사) → Stage 2(지표·운영 도구 연동) → Stage 3(영업/지원 준비·파일럿)
14. 출시 중단·롤백 트리거 5건: SSO 로그인 성공률 임계 미달, 감사 로그 기록 누락, 청구 이중 생성, 온보딩 완료율 급락, 지표 수집 지연 목표 초과
15. 고객 수는 추정치이며 매출·계약 보장 없음(source 명시); 시장 조사·고객 인터뷰는 수행하지 않았고 제공 자료 계산만 인용
16. 환불 규정·SLA·실제 계약 조건·고객 확보 일정은 제공 자료에 없어 미확인으로 표시
17. 실제 구현·테스트·부하 측정은 수행하지 않았으므로 모든 성능·정확도 수치는 미검증(design_only=true, implementation_done=false, tests_executed=false)
## growth-roadmap/business_recommendation.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline/artifacts/growth-roadmap/business_recommendation.json)

선행 profit_analysis의 facts/evidence만 인용해 S안(소규모 팀)과 E안(기업용)을 사업 관점에서 비교했다. 단위 경제성: S 공헌이익 48,000원, E 공헌이익 240,000원. 기준 월이익은 S 2,800,000원 > E 2,320,000원으로 S가 480,000원 우위. 손익분기 고객수는 S 42곳, E 9곳. 저/고 시나리오(기준 70%/130%, 소수점 버림): S 저 70곳 1,360,000원, 고 130곳 4,240,000원(변동폭 2,880,000원); E 저 12곳 880,000원, 고 23곳 3,520,000원(변동폭 2,640,000원). 두 안 모두 저 시나리오에서 월이익 양수로 흑자 유지. 개발 예산 240시간 대비 공통 160시간 + S 셀프 온보딩 64시간 = 224시간으로 S는 적합(s_fits_budget=true), E는 공통 160 + SSO/감사 기록 96 = 256시간으로 16시간 초과(e_fits_budget=false, e_budget_shortfall_hours=16).

[비교표]
항목 | S안 | E안
월 요금 | 60,000원 | 300,000원
고객당 변동비 | 12,000원 | 60,000원
공헌이익/고객 | 48,000원 | 240,000원
기준 고객수 | 100곳 | 18곳
기준 월이익 | 2,800,000원 | 2,320,000원
손익분기 고객수 | 42곳 | 9곳
저 시나리오(70%) | 70곳, 1,360,000원 | 12곳, 880,000원
고 시나리오(130%) | 130곳, 4,240,000원 | 23곳, 3,520,000원
이익 변동폭(저~고) | 2,880,000원 | 2,640,000원
개발 총시간 | 224시간 | 256시간
예산 240시간 적합 | 적합 | 16시간 초과

[선택 근거] ① 기준 월이익: S 2,800,000원 > E 2,320,000원(차 480,000원). ② 예산 적합성: S 224시간으로 240시간 이내, E 256시간으로 16시간 초과. ③ 손익분기 여유: S는 42곳, E는 9곳으로 E가 낮은 진입장벽을 갖지만 기준 18곳 대비 여유가 9곳에 불과. ④ 저 시나리오 방어력: S 1,360,000원 > E 880,000원. 종합적으로 현 자료 기준에서는 S안이 기준 이익·예산 적합성·저 시나리오 방어력에서 우위이며, E안은 16시간 예산 초과분을 분기 밖으로 미루거나 SSO/감사 기록 일부를 후속 릴리스로 이연해야 실행 가능하다.

[반증 가능한 가설]
H1: 'S안이 E안보다 우월하다.' 반증 조건: E안 실제 고객수가 30곳 이상으로 확보되면 E 월이익 = 240,000×30-2,000,000 = 5,200,000원으로 S 기준 2,800,000원을 상회. 또는 S안 고객수가 42곳 미만으로 하락하면 S는 적자 전환. 관찰 지표: 분기 내 계약/활성 고객수.
H2: 'E안은 예산상 실행 불가하다.' 반증 조건: SSO/감사 기록 범위를 16시간 이상 축소하거나 공통 범위를 재사용해 총 240시간 이내로 들어오면 반증. 관찰 지표: 실제 개발 소요시간.
H3: '두 안 모두 저 시나리오에서 흑자다.' 반증 조건: S 고객수 42곳 미만 또는 E 고객수 9곳 미만이면 적자. 관찰 지표: 월별 실측 고객수.

[불확실성·집중 리스크] E안은 기준 18곳으로 소수 고객 의존도가 높아 1곳 이탈 시 월이익이 240,000원 감소하며, 저~고 변동폭 2,640,000원으로 이익 변동성이 크다. S안은 100곳 기반으로 분산되어 개별 이탈 충격이 48,000원 수준으로 작고 저 시나리오에서도 흑자를 유지한다. 다만 S안도 변동폭 2,880,000원으로 절대 변동 금액은 E보다 크다. 고객 수는 추정치이며 매출·계약은 보장되지 않는다. 본 분석은 제공된 profit_analysis facts/evidence만 인용했으며 시장 조사·고객 인터뷰는 수행하지 않았다.

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
  "s_profit_advantage": 480000,
  "s_low_customers": 70,
  "e_low_customers": 12,
  "s_high_customers": 130,
  "e_high_customers": 23,
  "s_profit_swing": 2880000,
  "e_profit_swing": 2640000,
  "s_break_even_buffer": 58,
  "e_break_even_buffer": 9,
  "both_profitable_in_low": true,
  "market_research_performed": false,
  "customer_interviews_performed": false
}
```

### Evidence

1. S 공헌이익 = 60,000 - 12,000 = 48,000원 (profit_analysis)
2. E 공헌이익 = 300,000 - 60,000 = 240,000원 (profit_analysis)
3. S 기준 월이익 = 48,000×100 - 2,000,000 = 2,800,000원 (profit_analysis)
4. E 기준 월이익 = 240,000×18 - 2,000,000 = 2,320,000원 (profit_analysis)
5. 기준 월이익 차 = 2,800,000 - 2,320,000 = 480,000원 (S 우위)
6. S 손익분기 = ceil(2,000,000/48,000) = 42곳 (profit_analysis)
7. E 손익분기 = ceil(2,000,000/240,000) = 9곳 (profit_analysis)
8. 저/고 시나리오: 기준 70%/130% 후 floor → S 저 70, S 고 130, E 저 12, E 고 23 (profit_analysis)
9. S 저 월이익 = 48,000×70 - 2,000,000 = 1,360,000원; S 고 = 48,000×130 - 2,000,000 = 4,240,000원 (profit_analysis)
10. E 저 월이익 = 240,000×12 - 2,000,000 = 880,000원; E 고 = 240,000×23 - 2,000,000 = 3,520,000원 (profit_analysis)
11. S 이익 변동폭 = 4,240,000 - 1,360,000 = 2,880,000원; E 이익 변동폭 = 3,520,000 - 880,000 = 2,640,000원
12. 공통 개발시간 = 64+40+32+24 = 160시간 (source)
13. S 총 개발시간 = 160+64 = 224시간 ≤ 240시간 → s_fits_budget=true (source)
14. E 총 개발시간 = 160+96 = 256시간 > 240시간 → e_fits_budget=false, 부족분 = 256-240 = 16시간 (source)
15. S 손익분기 여유 = 100-42 = 58곳; E 손익분기 여유 = 18-9 = 9곳
16. 두 안 모두 저 시나리오 월이익 양수(1,360,000원, 880,000원)로 흑자 유지
17. 고객 수는 추정치이며 매출·계약 보장 없음(source 명시)
18. 시장 조사·고객 인터뷰는 수행하지 않았고 profit_analysis facts/evidence만 인용
## growth-roadmap/ops_readiness.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline/artifacts/growth-roadmap/ops_readiness.json)

선행 profit_analysis의 확정 수치를 인용해 출시 전 영업/지원 준비 체크리스트, 고객 안내 문구 방향, 팀 간 일정·담당·전달 계획, 출시 중단 기준을 운영·커뮤니케이션 관점에서 정리했다. 확정 사실: S안 공헌이익 48,000원, 기준(100곳) 월이익 2,800,000원, 손익분기 42곳, 저(70곳) 1,360,000원, 고(130곳) 4,240,000원, 총 개발 224시간으로 240시간 예산 적합. E안 공헌이익 240,000원, 기준(18곳) 월이익 2,320,000원, 손익분기 9곳, 저(12곳) 880,000원, 고(23곳) 3,520,000원, 총 개발 256시간으로 16시간 초과 부적합. 공통 개발 160시간(요금/청구 64 + 조직/좌석 40 + 지표 수집 32 + 운영 도구 24). 두 안은 대안이므로 동시 선택하지 않는다.

[1] 출시 전 영업/지원 준비 체크리스트
- 공통 범위(요금/청구, 조직/좌석, 지표 수집, 운영 도구)의 지원팀 인수인계 문서와 상담 절차 확정.
- S안 선택 시: 셀프 온보딩 64시간 범위의 고객 자가설정 가이드, FAQ, 온보딩 실패 시 지원 에스컬레이션 경로 준비.
- E안 선택 시: SSO/감사 기록 96시간 범위의 기업 고객 보안·감사 요구 대응 스크립트, SSO 연동 실패 대응 절차, 감사 로그 조회 요청 처리 절차 준비. 단, E안은 16시간 부족분이 해소되기 전 출시 불가.
- 요금·환불·SLA 정책 확정본을 상담 스크립트에 반영(미확인 항목은 확정 전 안내 금지).
- 저/고 시나리오는 계획 가정임을 영업 자료에 명시.

[2] 고객 안내 문구 방향
- 고객 수(100곳/18곳)와 매출·계약은 추정치이며 보장되지 않음을 명시.
- 저/고 시나리오(70%/130%, 소수점 버림)는 계획 가정임을 안내.
- E안 선택 시 16시간 부족분과 분기 밖 이연 범위를 사전 고지.
- 손익분기(42곳/9곳)는 내부 판단 지표이며 고객에게 약속으로 표현하지 않음.

[3] 팀 간 일정·담당·전달 계획(단계별)
1단계 개발 완료(담당: 개발) → 산출물: 기능 릴리스 노트, 지원팀 인수인계 문서. 선행: 예산 적합 확인(S안 224시간 적합 / E안 256시간, 16시간 초과 해소 필요).
2단계 영업 교육(담당: 영업, 입력: 1단계 릴리스 노트) → 산출물: 요금·시나리오 안내 자료, 반증 가설 목록.
3단계 지원팀 상담 절차 확정(담당: 지원, 입력: 1단계 인수인계 문서, 2단계 안내 자료) → 산출물: 상담 스크립트, 에스컬레이션 경로.
4단계 출시(담당: 운영 총괄, 입력: 2·3단계 완료 및 정책 확정) → 산출물: 출시 공지, 중단 기준 모니터링.
의존 관계: 1→2, 1→3, 2·3→4. 2단계와 3단계는 1단계 완료 후 병렬 가능.

[4] 미확인 정책(미확인 표시)
- 환불 규정: 미확인(확인 주체: 정책/법무, 시점: 출시 전).
- SLA 수준: 미확인(확인 주체: 운영, 시점: 지원 절차 확정 전).
- 실제 계약 조건: 미확인(확인 주체: 영업, 시점: 영업 교육 전).
- 고객 확보 일정: 미확인(확인 주체: 영업, 시점: 출시 전).

[5] 출시 중단 기준
- 저 시나리오 미달(S 70곳 미만 / E 12곳 미만) 시 재검토.
- E안 예산 초과 16시간 미해소 시 E안 출시 중단.
- 요금·환불·SLA 정책 미확정 시 출시 중단.
- 두 안 동시 선택 금지 위반 시 중단.

[6] 모순·미확인 사항
- E안은 예산 240시간 대비 256시간으로 16시간 초과이며, 부족분과 분기 밖 이연 범위가 확정되지 않아 일정·기능·사업 권고 간 조정 필요.
- 고객 수는 추정치로 매출·계약 보장 없음.
- 실제 구현/테스트는 수행하지 않았으며, 본 결과는 제공 자료 기반 운영 계획 제안이다.

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
  "ops_readiness_defined": true,
  "refund_policy_confirmed": false,
  "sla_confirmed": false,
  "contract_terms_confirmed": false,
  "customer_acquisition_schedule_confirmed": false,
  "e_launch_blocked_until_shortfall_resolved": true,
  "simultaneous_selection_allowed": false,
  "implementation_or_test_performed": false
}
```

### Evidence

1. S 공헌이익 = 60,000 - 12,000 = 48,000원, E 공헌이익 = 300,000 - 60,000 = 240,000원
2. S 기준 월이익 = 48,000 × 100 - 2,000,000 = 2,800,000원, E 기준 월이익 = 240,000 × 18 - 2,000,000 = 2,320,000원
3. S 손익분기 = ceil(2,000,000 / 48,000) = 42곳, E 손익분기 = ceil(2,000,000 / 240,000) = 9곳
4. 저/고 시나리오: S 저=floor(100×0.7)=70, S 고=floor(100×1.3)=130, E 저=floor(18×0.7)=12, E 고=floor(18×1.3)=23
5. S 저 월이익 = 48,000×70-2,000,000 = 1,360,000원, S 고 = 48,000×130-2,000,000 = 4,240,000원
6. E 저 월이익 = 240,000×12-2,000,000 = 880,000원, E 고 = 240,000×23-2,000,000 = 3,520,000원
7. 공통 개발시간 = 64+40+32+24 = 160시간, S 총 = 160+64 = 224시간(240시간 이내 적합), E 총 = 160+96 = 256시간(240시간 초과, 부족분 16시간)
8. 출시 전 준비 체크리스트와 일정·담당·전달 계획은 선행 profit_analysis의 확정 수치와 source의 개발 범위·예산 조건을 근거로 작성했으며, 실제 구현·테스트는 수행하지 않음
9. 환불 규정, SLA, 실제 계약 조건, 고객 확보 일정은 제공 자료에 없어 미확인으로 표시함
## growth-roadmap/profit_analysis.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline/artifacts/growth-roadmap/profit_analysis.json)

S안과 E안의 단위 경제성, 기준·저·고 시나리오 월이익, 손익분기 고객수, 개발 총시간 및 240시간 예산 적합성을 계산했다. S안: 공헌이익 48,000원, 기준 100곳 월이익 2,800,000원, 손익분기 42곳, 저(70곳) 1,360,000원, 고(130곳) 4,240,000원. E안: 공헌이익 240,000원, 기준 18곳 월이익 2,320,000원, 손익분기 9곳, 저(12곳) 880,000원, 고(23곳) 3,520,000원. 공통 개발 160시간에 S는 셀프 온보딩 64시간을 더해 224시간으로 예산 240시간 이내(적합), E는 SSO/감사 기록 96시간을 더해 256시간으로 16시간 초과(부적합). 수요 불확실성 측면에서 E안은 기준 18곳으로 소수 고객 의존도가 높아 이익 변동 폭이 크고(저 880,000원~고 3,520,000원, 변동폭 2,640,000원), S안은 100곳 기반으로 분산되어 있으나 저 시나리오에서도 흑자를 유지한다(저 1,360,000원~고 4,240,000원, 변동폭 2,880,000원). 두 안 모두 저 시나리오에서도 월이익이 양수로 흑자를 유지한다. 고객 수는 추정치이며 매출·계약은 보장되지 않는다.

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

1. S 공헌이익 = 요금 60,000 - 변동비 12,000 = 48,000원
2. E 공헌이익 = 요금 300,000 - 변동비 60,000 = 240,000원
3. S 기준 월이익 = 48,000 × 100 - 2,000,000 = 4,800,000 - 2,000,000 = 2,800,000원
4. E 기준 월이익 = 240,000 × 18 - 2,000,000 = 4,320,000 - 2,000,000 = 2,320,000원
5. S 손익분기 = ceil(2,000,000 / 48,000) = ceil(41.6667) = 42곳
6. E 손익분기 = ceil(2,000,000 / 240,000) = ceil(8.3333) = 9곳
7. 저/고 시나리오는 기준의 70%/130% 적용 후 소수점 내림(floor): S 저=floor(100×0.7)=floor(70.0)=70, S 고=floor(100×1.3)=floor(130.0)=130, E 저=floor(18×0.7)=floor(12.6)=12, E 고=floor(18×1.3)=floor(23.4)=23
8. S 저 월이익 = 48,000 × 70 - 2,000,000 = 3,360,000 - 2,000,000 = 1,360,000원
9. S 고 월이익 = 48,000 × 130 - 2,000,000 = 6,240,000 - 2,000,000 = 4,240,000원
10. E 저 월이익 = 240,000 × 12 - 2,000,000 = 2,880,000 - 2,000,000 = 880,000원
11. E 고 월이익 = 240,000 × 23 - 2,000,000 = 5,520,000 - 2,000,000 = 3,520,000원
12. 공통 개발시간 = 요금/청구 64 + 조직/좌석 40 + 지표 수집 32 + 운영 도구 24 = 160시간
13. S 총 개발시간 = 160 + 셀프 온보딩 64 = 224시간, 예산 240시간 이내이므로 s_fits_budget=true
14. E 총 개발시간 = 160 + SSO/감사 기록 96 = 256시간, 예산 240시간 초과이므로 e_fits_budget=false, 부족분 = 256 - 240 = 16시간
15. 고객 집중·수요 불확실성: E안은 기준 18곳으로 소수 고객 의존도가 높아 저~고 이익 변동폭이 2,640,000원(880,000~3,520,000)으로 크고, S안은 100곳 기반 분산으로 저~고 변동폭 2,880,000원(1,360,000~4,240,000)이나 저 시나리오에서도 흑자 유지. 두 안 모두 저 시나리오에서 월이익 양수로 흑자. 고객 수는 추정치이며 매출·계약은 보장되지 않음
## growth-roadmap/tech_design/ops_rollout_plan.json

Worker: B; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline/artifacts/growth-roadmap/tech_design/ops_rollout_plan.json)

tech_change_design(설계 제안, 미구현·미검증)과 profit_analysis facts를 입력으로 MVP 포함/제외 범위, 선행 관계가 있는 단계별 일정(담당·전달 계획), 영업/지원팀 준비, 출시 중단·롤백 기준을 운영·커뮤니케이션 관점에서 정리했다. 모든 수치는 선행 산출물 인용이며 실제 구현·테스트·부하측정은 수행하지 않았다(설계 제안 단계).

[1. MVP 포함/제외 범위]
공통 필수 4개 모듈(포함, 160h): 요금/청구(64h), 조직/좌석(40h), 지표 수집(32h), 운영 도구(24h).
S안 추가(포함, 64h): 셀프 온보딩. S 총 224h ≤ 240h → s_fits_budget=true.
E안 추가(포함, 96h): SSO/감사 기록. E 총 256h > 240h → 16h 초과, e_fits_budget=false.
E안 이연(제외, 분기 밖): (a) 감사 로그 보존/내보내기 고도화(장기 보존 정책, 대용량 비동기 export, 포맷 옵션), (b) SSO 예외 처리 고급화(IdP 메타데이터 자동 갱신, 다중 IdP, SCIM), (c) 감사 로그 검색 인덱스/대시보드. 이연 원칙: 인증 핵심 경로(SSO 로그인/세션)와 감사 로그 '기록'(append+해시 체인)은 분기 내 유지, 조회·내보내기·보존 고도화만 이연.

[2. 선행 관계가 있는 단계별 일정 (담당·전달 계획)]
Stage 0(선행 없음, 병렬): 공통 기반 모듈 4종 설계·구현 — 담당 A(제품·기술). 전달: 도메인/API/데이터 스키마, 마이그레이션 스크립트. 선행: 없음.
Stage 1(Stage 0 의존): S안 셀프 온보딩 또는 E안 SSO/감사 기록 — 담당 A. 전달: 온보딩 흐름 또는 SSO/감사 로그 모듈. 선행: Stage 0.
Stage 2(Stage 1 의존): 지표·운영 도구 연동 및 측정 지표 계측 — 담당 A(구현), B(지표 정의·손익 대조). 전달: 대시보드, 지표 정의서. 선행: Stage 1.
Stage 3(Stage 2 의존): 영업/지원팀 준비 및 파일럿 — 담당 C(운영·커뮤니케이션), B(사업 검증). 전달: 안내 스크립트, 지원 절차, 파일럿 결과. 선행: Stage 2.
병렬 가능: Stage 0의 4개 모듈은 상호 독립적으로 병렬 실행 가능. Stage 3의 영업 준비와 파일럿은 Stage 2 완료 후 진행.

[3. 영업/지원팀 준비]
- E안 이연 범위 사전 고지: 감사 요구가 강한 기업 고객의 컴플라이언스 검토 지연 리스크를 영업/지원팀에 사전 통지.
- 요금/청구·좌석 변경 안내 스크립트: 플랜 변경, 좌석 상한(plan.tier 파생), 청구 상태 전이(pending→issued→paid/failed) 안내.
- 온보딩/SSO 지원 절차: 셀프 가입 흐름, SSO SP-initiated 로그인, 세션 만료 재인증 안내.
- 상담 에스컬레이션 경로: 청구 이중 생성, SSO 로그인 실패, 감사 로그 누락 시 기술팀(A) 에스컬레이션.

[4. 출시 중단·롤백 기준 (정량 트리거, 성능 목표는 설계 가정·미실측)]
- SSO 로그인 성공률 임계 미달(목표치는 원자료에 수치 없어 설계 가정으로만 표기, 실측 아님).
- 감사 로그 기록 누락(감사 대상 액션 중 로그 미기록 발생).
- 청구 이중 생성(invoice 상태 전이 위반).
- 온보딩 완료율 급락(완료 org/시작 org 비율 하락).
- 지표 수집 지연 목표 초과(설계 가정: 수집→롤업 24h 이내, 미실측).
롤백: 위 트리거 발생 시 해당 모듈 배포 중단 및 이전 버전 복원, 이연 범위는 분기 밖으로 유지.

[5. 모순·미확인 사항]
- E안 240h 예산 vs 256h 필수 범위 구조적 모순 → 이연 없이는 분기 내 완료 불가.
- 고객 수(100/18)는 추정치이며 매출·계약 보장 없음.
- 성능 목표(지표 수집 지연, SSO p95)는 원자료에 수치 없어 설계 가정으로만 표기, 실측 아님.
- 실제 구현/테스트/부하 측정 미수행 → 모든 성능·정확도 수치 미검증.
- 기능 설계(tech_change_design)와 개발시간·사업 권고(profit_analysis) 간 대조: S안은 예산 내 정합, E안은 16h 초과로 이연 필요.

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
  "mvp_common_modules": 4,
  "s_extra_hours": 64,
  "e_extra_hours": 96,
  "e_deferred_scope_count": 3,
  "implementation_done": false,
  "tests_executed": false,
  "design_only": true,
  "rollback_triggers_count": 5,
  "stages_count": 4
}
```

### Evidence

1. 선행 profit_analysis facts 인용: s_margin=48000, e_margin=240000, s_profit=2800000, e_profit=2320000, s_break_even=42, e_break_even=9, s_low_profit=1360000, e_low_profit=880000, s_high_profit=4240000, e_high_profit=3520000, common_dev_hours=160, s_total_dev_hours=224, e_total_dev_hours=256, e_budget_shortfall_hours=16, s_fits_budget=true, e_fits_budget=false
2. 공통 개발시간 = 요금/청구 64 + 조직/좌석 40 + 지표 수집 32 + 운영 도구 24 = 160시간
3. S 총 개발시간 = 160 + 셀프 온보딩 64 = 224시간 ≤ 예산 240시간 → s_fits_budget=true
4. E 총 개발시간 = 160 + SSO/감사 기록 96 = 256시간 > 예산 240시간 → 부족분 = 256 - 240 = 16시간, e_fits_budget=false
5. S 공헌이익 = 60,000 - 12,000 = 48,000원, E 공헌이익 = 300,000 - 60,000 = 240,000원
6. S 기준 월이익 = 48,000×100 - 2,000,000 = 2,800,000원, E 기준 월이익 = 240,000×18 - 2,000,000 = 2,320,000원
7. S 손익분기 = ceil(2,000,000/48,000)=42곳, E 손익분기 = ceil(2,000,000/240,000)=9곳
8. 저/고 시나리오: S 저=floor(100×0.7)=70 → 1,360,000원, S 고=floor(100×1.3)=130 → 4,240,000원, E 저=floor(18×0.7)=12 → 880,000원, E 고=floor(18×1.3)=23 → 3,520,000원
9. tech_change_design 인용: E안 이연 후보 3건(감사 로그 보존/내보내기 고도화, SSO 예외 처리 고급화, 감사 로그 검색 인덱스/대시보드), 분기 내 유지: SSO SP-initiated 로그인·세션 발급/만료·감사 로그 append 및 해시 체인 기록·관리자 조회 API 최소 버전
10. 일정 단계: Stage 0(공통 4모듈, 선행 없음, 병렬) → Stage 1(S안 셀프 온보딩 또는 E안 SSO/감사 기록) → Stage 2(지표·운영 도구 연동) → Stage 3(영업/지원팀 준비 및 파일럿). 담당: A(제품·기술), B(사업·분석), C(운영·커뮤니케이션)
11. 출시 중단·롤백 트리거 5건: SSO 로그인 성공률 임계 미달, 감사 로그 기록 누락, 청구 이중 생성, 온보딩 완료율 급락, 지표 수집 지연 목표 초과. 성능 목표치는 원자료에 수치 없어 설계 가정으로만 표기, 실측 아님
12. 실제 구현·테스트·부하 측정은 수행하지 않았으므로 모든 성능·정확도 수치는 미검증(design_only=true, implementation_done=false, tests_executed=false)
13. 고객 수(100/18)는 추정치이며 매출·계약 보장 없음(선행 분석과 동일 전제)
## growth-roadmap/tech_design/tech_change_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline/artifacts/growth-roadmap/tech_design/tech_change_design.json)

S안/E안의 도메인·API·데이터 변경 개요와 측정 지표를 '설계 제안'으로 정리했다. 실제 구현·테스트는 수행하지 않았다(미검증). 선행 profit_analysis facts를 그대로 인용해 예산을 대조했다: 공통 160h, S 총 224h(예산 240h 이내, s_fits_budget=true), E 총 256h(16h 초과, e_fits_budget=false).

[공통 필수 4개 모듈 — 도메인/API/데이터 변경 개요(설계 제안)]
1) 요금/청구(64h): 도메인 엔터티 Plan(plan_id, tier, monthly_fee, currency), Subscription(sub_id, org_id, plan_id, status, started_at, canceled_at), Invoice(invoice_id, org_id, period_start, period_end, amount, status). API: POST /plans, GET /plans, POST /subscriptions, PATCH /subscriptions/{id}(업/다운그레이드), GET /invoices?org_id&period, POST /invoices/{id}/retry. 데이터: plans, subscriptions, invoices, invoice_line_items 테이블 신설; 금액은 정수(원) 저장, 기간은 반개구간 [start, end). 변경 리스크: 기존 결제 흐름과의 이중 청구 방지 위해 invoice 상태 전이(pending→issued→paid/failed) 단일화.
2) 조직/좌석(40h): 엔터티 Organization(org_id, name, owner_user_id), Seat(seat_id, org_id, user_id, role, state), Invitation(invite_id, org_id, email, role, expires_at). API: POST /orgs, GET /orgs/{id}, POST /orgs/{id}/seats, DELETE /orgs/{id}/seats/{seat_id}, POST /orgs/{id}/invitations, POST /invitations/{token}/accept. 데이터: orgs, seats, invitations; seats에 (org_id, user_id) 유니크 제약, 좌석 수 상한은 plan.tier에서 파생.
3) 지표 수집(32h): 엔터티 MetricEvent(event_id, org_id, user_id, name, value, occurred_at, source), MetricDaily(org_id, date, name, agg_value). API: POST /events(배치 허용), GET /metrics/summary?org_id&from&to&name. 데이터: events(append-only, 파티셔닝 키=occurred_at), metrics_daily(일 단위 롤업). 지연 목표는 설계 가정으로만 표기(예: 수집→롤업 24h 이내) — 실측 아님.
4) 운영 도구(24h): 엔터티 AdminAction(actor_id, action, target, reason, created_at), FeatureFlag(key, scope, enabled). API: GET /admin/orgs, POST /admin/orgs/{id}/suspend, POST /admin/flags/{key}/toggle, GET /admin/audit(운영 조작 로그). 데이터: admin_actions, feature_flags.

[S안 추가 — 셀프 온보딩(64h), 총 224h ≤ 240h]
도메인: OnboardingStep(step_id, org_id, key, status, completed_at), OnboardingProgress(org_id, current_step, completed_count). API: GET /onboarding/steps, POST /onboarding/steps/{key}/complete, POST /onboarding/self-serve-signup(조직 생성+플랜 선택+좌석 초대를 한 흐름으로). 데이터: onboarding_steps, onboarding_progress; signup 시 orgs/plans/subscriptions에 트랜잭션 기록. 측정 지표: 온보딩 완료율(완료 org/시작 org), 단계별 이탈률, 가입→첫 좌석 활성화 소요시간, 셀프 가입 비중(영업 개입 없이 생성된 org 비율). 데이터 소스: onboarding_progress, seats, events.

[E안 추가 — SSO/감사 기록(96h), 총 256h → 16h 부족]
도메인: SSOConnection(conn_id, org_id, idp_metadata_url, protocol, status), SSOSession(session_id, user_id, org_id, issued_at, expires_at), AuditLog(log_id, org_id, actor_id, action, target, before, after, occurred_at, hash_chain_prev). API: POST /orgs/{id}/sso/connections, GET /sso/login?org_id(SP-initiated), POST /sso/acs(assertion 소비), GET /audit/logs?org_id&from&to&action, POST /audit/export. 데이터: sso_connections, sso_sessions, audit_logs(append-only, 해시 체인으로 변조 탐지), audit_exports. 측정 지표: SSO 로그인 성공률, SSO 로그인 지연(p95), 감사 로그 커버리지(감사 대상 액션 중 로그 기록 비율), 감사 로그 내보내기 성공률, 세션 만료 후 재인증 비율. 데이터 소스: sso_sessions, audit_logs, admin_actions.

[E안 16시간 부족분 — 분기 밖 이연 범위(우선순위 기준)]
이연 원칙: (a) 인증 핵심 경로(SSO 로그인/세션 발급)와 감사 로그 '기록'은 분기 내 유지, (b) 조회·내보내기·보존 정책 고도화는 이연. 이연 후보(합계 16h 이상): 1) 감사 로그 보존/내보내기 고도화(장기 보존 정책, 대용량 비동기 export, CSV/JSON 포맷 옵션) — 이연, 2) SSO 예외 처리 고급화(IdP 메타데이터 자동 갱신, 다중 IdP 동시 지원, SCIM 프로비저닝) — 이연, 3) 감사 로그 검색 인덱스/대시보드 — 이연. 분기 내 유지: SSO SP-initiated 로그인, 세션 발급/만료, 감사 로그 append 및 해시 체인 기록, 관리자 조회 API 최소 버전. 이연 시 리스크: 감사 요구가 강한 기업 고객의 컴플라이언스 검토 지연 가능 — 영업/지원팀에 이연 범위를 사전 고지해야 함.

[모순·미확인 사항]
- E안은 240h 예산과 256h 필수 범위가 구조적으로 모순 → 이연 없이는 분기 내 완료 불가(설계 제안 단계에서 명시).
- 지표 수집 지연 목표, SSO p95 지연 목표는 자료에 수치가 없어 '설계 가정'으로만 표기했고 실측하지 않았다.
- 고객 수(100/18)는 추정치이며 매출·계약 보장 없음(선행 분석과 동일 전제).
- 실제 구현/테스트/부하 측정은 수행하지 않았으므로 모든 성능·정확도 수치는 미검증이다.

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
  "common_modules_count": 4,
  "s_extra_hours": 64,
  "e_extra_hours": 96,
  "e_deferred_scope_count": 3,
  "implementation_done": false,
  "tests_executed": false,
  "design_only": true
}
```

### Evidence

1. 선행 profit_analysis facts 인용: common_dev_hours=160, s_total_dev_hours=224, s_fits_budget=true, e_total_dev_hours=256, e_budget_shortfall_hours=16, e_fits_budget=false
2. 공통 개발시간 = 요금/청구 64 + 조직/좌석 40 + 지표 수집 32 + 운영 도구 24 = 160시간
3. S 총 개발시간 = 160 + 셀프 온보딩 64 = 224시간 ≤ 예산 240시간 → s_fits_budget=true
4. E 총 개발시간 = 160 + SSO/감사 기록 96 = 256시간 > 예산 240시간 → 부족분 = 256 - 240 = 16시간, e_fits_budget=false
5. S 공헌이익 = 60,000 - 12,000 = 48,000원, E 공헌이익 = 300,000 - 60,000 = 240,000원 (선행 분석 인용)
6. S 기준 월이익 = 48,000×100 - 2,000,000 = 2,800,000원, E 기준 월이익 = 240,000×18 - 2,000,000 = 2,320,000원 (선행 분석 인용)
7. S 손익분기 = ceil(2,000,000/48,000)=42곳, E 손익분기 = ceil(2,000,000/240,000)=9곳 (선행 분석 인용)
8. 저/고 시나리오: S 저=floor(100×0.7)=70 → 1,360,000원, S 고=floor(100×1.3)=130 → 4,240,000원, E 저=floor(18×0.7)=12 → 880,000원, E 고=floor(18×1.3)=23 → 3,520,000원 (선행 분석 인용)
9. 도메인·API·데이터 개요는 제공된 개발 범위 항목(요금/청구, 조직/좌석, 지표 수집, 운영 도구, 셀프 온보딩, SSO/감사 기록)과 시간 배분만을 근거로 한 설계 제안이며, 실제 코드 작성·배포·테스트는 수행하지 않음
10. E안 이연 후보 3건(감사 로그 보존/내보내기 고도화, SSO 예외 처리 고급화, 감사 로그 검색 인덱스/대시보드)은 16시간 부족분을 상쇄하기 위한 우선순위 기준 제안이며, 이연 시간의 정밀 재추정은 미검증
11. 지표 수집 지연·SSO p95 지연 등 성능 목표치는 원자료에 수치가 없어 설계 가정으로만 표기했고 실측하지 않음
## growth-roadmap/tech_design.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline/artifacts/growth-roadmap/tech_design.json)

선행 profit_analysis(확정 계산)와 두 하위 산출물(tech_change_design, ops_rollout_plan)을 통합했다. 모든 수치는 선행 산출물 인용이며, 실제 구현·테스트·부하측정은 수행하지 않았다(설계 제안 단계, design_only=true).

[1. 손익·예산 기준선 (profit_analysis 인용)]
S안: 공헌이익 48,000원, 기준 100곳 월이익 2,800,000원, 손익분기 42곳, 저(70곳) 1,360,000원, 고(130곳) 4,240,000원. E안: 공헌이익 240,000원, 기준 18곳 월이익 2,320,000원, 손익분기 9곳, 저(12곳) 880,000원, 고(23곳) 3,520,000원. 두 안 모두 저 시나리오에서 흑자. 공통 개발 160h, S 총 224h(예산 240h 이내, 적합), E 총 256h(16h 초과, 부적합).

[2. MVP 포함/제외 범위]
포함(공통 4모듈, 160h): 요금/청구 64h, 조직/좌석 40h, 지표 수집 32h, 운영 도구 24h. S 추가 포함: 셀프 온보딩 64h. E 추가 포함: SSO/감사 기록 96h. E 이연(제외, 분기 밖): (a) 감사 로그 보존/내보내기 고도화, (b) SSO 예외 처리 고급화(IdP 메타데이터 자동 갱신·다중 IdP·SCIM), (c) 감사 로그 검색 인덱스/대시보드. 분기 내 유지: SSO SP-initiated 로그인·세션 발급/만료, 감사 로그 append+해시 체인 기록, 관리자 조회 API 최소 버전.

[3. 도메인·API·데이터 변경 개요 (설계 제안, 미구현)]
요금/청구: Plan/Subscription/Invoice 엔터티, POST /plans, POST /subscriptions, PATCH /subscriptions/{id}, GET /invoices, POST /invoices/{id}/retry; plans·subscriptions·invoices·invoice_line_items 신설, invoice 상태 전이(pending→issued→paid/failed) 단일화로 이중 청구 방지.
조직/좌석: Organization/Seat/Invitation, POST /orgs, POST /orgs/{id}/seats, POST /invitations/{token}/accept; seats (org_id,user_id) 유니크, 좌석 상한은 plan.tier 파생.
지표 수집: MetricEvent/MetricDaily, POST /events(배치), GET /metrics/summary; events append-only(파티셔닝=occurred_at), metrics_daily 일 롤업.
운영 도구: AdminAction/FeatureFlag, GET /admin/orgs, POST /admin/orgs/{id}/suspend, POST /admin/flags/{key}/toggle, GET /admin/audit.
S 추가(셀프 온보딩): OnboardingStep/OnboardingProgress, POST /onboarding/self-serve-signup(조직+플랜+좌석 한 흐름), 측정 지표=온보딩 완료율·단계별 이탈률·가입→첫 좌석 활성화 소요시간·셀프 가입 비중.
E 추가(SSO/감사): SSOConnection/SSOSession/AuditLog, POST /orgs/{id}/sso/connections, GET /sso/login(SP-initiated), POST /sso/acs, GET /audit/logs, POST /audit/export; audit_logs append-only+해시 체인, 측정 지표=SSO 로그인 성공률·SSO p95 지연·감사 로그 커버리지·내보내기 성공률·세션 만료 후 재인증 비율.

[4. 선후 관계 일정 (담당·전달)]
Stage 0(선행 없음, 4모듈 병렬): 담당 A(제품·기술). 전달=도메인/API/스키마, 마이그레이션.
Stage 1(Stage 0 의존): S 셀프 온보딩 또는 E SSO/감사 기록 — 담당 A.
Stage 2(Stage 1 의존): 지표·운영 도구 연동 — 담당 A(구현), B(지표 정의·손익 대조).
Stage 3(Stage 2 의존): 영업/지원팀 준비 및 파일럿 — 담당 C(운영·커뮤니케이션), B(사업 검증).

[5. 영업/지원팀 준비]
E 이연 범위 사전 고지(컴플라이언스 검토 지연 리스크), 요금/청구·좌석 변경 안내 스크립트(플랜 변경, 좌석 상한, 청구 상태 전이), 온보딩/SSO 지원 절차, 상담 에스컬레이션 경로(청구 이중 생성·SSO 실패·감사 로그 누락 → A).

[6. 출시 중단·롤백 기준 (5건)]
SSO 로그인 성공률 임계 미달, 감사 로그 기록 누락, 청구 이중 생성, 온보딩 완료율 급락, 지표 수집 지연 목표 초과. 트리거 발생 시 해당 모듈 배포 중단·이전 버전 복원, 이연 범위는 분기 밖 유지. 성능 목표치는 원자료에 수치 없어 설계 가정으로만 표기(실측 아님).

[7. 모순·미확인 사항]
E안 240h 예산 vs 256h 필수 범위 구조적 모순 → 이연 없이 분기 내 완료 불가. 고객 수(100/18)는 추정치로 매출·계약 보장 없음. 성능 목표(지표 수집 지연, SSO p95)는 수치 없어 설계 가정. 실제 구현/테스트/부하 측정 미수행 → 모든 성능·정확도 수치 미검증. S안은 예산 내 정합, E안은 16h 초과로 이연 필요.

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
  "common_modules_count": 4,
  "s_extra_hours": 64,
  "e_extra_hours": 96,
  "e_deferred_scope_count": 3,
  "stages_count": 4,
  "rollback_triggers_count": 5,
  "design_only": true,
  "implementation_done": false,
  "tests_executed": false
}
```

### Evidence

1. S 공헌이익 = 60,000 - 12,000 = 48,000원, E 공헌이익 = 300,000 - 60,000 = 240,000원 (profit_analysis 인용)
2. S 기준 월이익 = 48,000×100 - 2,000,000 = 2,800,000원, E 기준 월이익 = 240,000×18 - 2,000,000 = 2,320,000원
3. S 손익분기 = ceil(2,000,000/48,000)=42곳, E 손익분기 = ceil(2,000,000/240,000)=9곳
4. 저/고 시나리오 floor 적용: S 저=floor(100×0.7)=70 → 1,360,000원, S 고=floor(100×1.3)=130 → 4,240,000원, E 저=floor(18×0.7)=12 → 880,000원, E 고=floor(18×1.3)=23 → 3,520,000원
5. 공통 개발시간 = 요금/청구 64 + 조직/좌석 40 + 지표 수집 32 + 운영 도구 24 = 160시간
6. S 총 개발시간 = 160 + 셀프 온보딩 64 = 224시간 ≤ 예산 240시간 → s_fits_budget=true
7. E 총 개발시간 = 160 + SSO/감사 기록 96 = 256시간 > 예산 240시간 → 부족분 = 256 - 240 = 16시간, e_fits_budget=false
8. tech_change_design 인용: E안 이연 후보 3건(감사 로그 보존/내보내기 고도화, SSO 예외 처리 고급화, 감사 로그 검색 인덱스/대시보드), 분기 내 유지=SSO SP-initiated 로그인·세션 발급/만료·감사 로그 append 및 해시 체인 기록·관리자 조회 API 최소 버전
9. ops_rollout_plan 인용: 일정 단계 Stage 0(공통 4모듈, 선행 없음, 병렬) → Stage 1(S 셀프 온보딩 또는 E SSO/감사 기록) → Stage 2(지표·운영 도구 연동) → Stage 3(영업/지원팀 준비 및 파일럿), 담당 A/B/C
10. ops_rollout_plan 인용: 출시 중단·롤백 트리거 5건(SSO 로그인 성공률 임계 미달, 감사 로그 기록 누락, 청구 이중 생성, 온보딩 완료율 급락, 지표 수집 지연 목표 초과)
11. 성능 목표치(지표 수집 지연, SSO p95)는 원자료에 수치 없어 설계 가정으로만 표기, 실측 아님
12. 실제 구현·테스트·부하 측정은 수행하지 않았으므로 모든 성능·정확도 수치는 미검증(design_only=true, implementation_done=false, tests_executed=false)
13. 고객 수(100/18)는 추정치이며 매출·계약 보장 없음(선행 분석과 동일 전제)