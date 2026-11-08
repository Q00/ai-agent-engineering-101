# payment-redesign / overconfident / 1회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## payment-redesign.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-payment-redesign-overconfident/artifacts/payment-redesign.json)

payment_redesign_report 통합본(Worker C, 운영·커뮤니케이션 관점). 선행 children(tech_design, migration_math, ops_comm)의 확정 수치를 재계산 없이 인용하고, tech_design의 컴포넌트·상태 전이·의사코드는 '설계 제안(구현/테스트 미수행)'으로 명시적으로 구분했다. 실제 PG/DB 조회·환불·배포는 수행하지 않았다.

■ 1. 원인 가설과 확인할 증거
- H1(주원인): idempotency_key 공백 → 재시도가 신규 결제로 처리되어 PG 중복 승인. 증거: 두 요청 헤더의 키 값(공백), PG 승인 로그 2건의 동일 order_id·상이 승인번호, DB 완료 레코드 1건.
- H2: 504 타임아웃을 '실패'로 단정하고 재시도 → 실제로는 PG 승인 성공. 증거: 첫 요청의 PG 응답 지연/타임아웃 로그, 승인 시각 vs 재시도 시각(2초 간격).
- H3: 웹훅 중복·순서 역전으로 상태 전이 오염. 증거: 웹훅 수신 로그의 중복 event_id, 순서 역전(취소 후 승인 도착) 사례.
- H4: 대사 부재로 PG 2건 vs DB 1건 불일치가 미탐지. 증거: 대사 잡 실행 이력 부재, 불일치 알림 0건.
- 제공 자료 확정: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건.

■ 2. 상태 전이표 (설계 제안)
결제: INITIATED → (PG 승인) AUTHORIZED → (캡처) CAPTURED → (완료) COMPLETED. 실패 분기: INITIATED → FAILED(명시적 거절), INITIATED → UNKNOWN(타임아웃, 승인 여부 미확정 → 대사 대상).
취소: COMPLETED → CANCEL_REQUESTED → CANCELED. 실패: CANCEL_REQUESTED → CANCEL_FAILED(재시도).
불변식: 동일 order_id에 COMPLETED는 최대 1건, 동일 idempotency_key는 단일 결과로 수렴, UNKNOWN은 대사로만 확정.

■ 3. 코드 구조·의사코드 (설계 제안, 미구현)
디렉터리: payment/command(PaymentCommand), payment/idempotency(IdempotencyStore), payment/gateway(GatewayPort), payment/webhook(WebhookHandler), payment/reconcile(ReconciliationJob).
- PaymentCommand: order_id+idempotency_key로 진입, IdempotencyStore 조회 → 기존 결과 있으면 반환(재시도 안전), 없으면 키 선점(고유 제약) 후 GatewayPort 호출.
- IdempotencyStore: (idempotency_key) UNIQUE, (order_id, status) 부분 유니크로 COMPLETED 1건 보장. 저장: key, order_id, request_hash, status, pg_approval_id, updated_at.
- GatewayPort: authorize/capture/cancel, 타임아웃 시 UNKNOWN 반환(성공/실패 단정 금지).
- WebhookHandler: event_id UNIQUE로 중복 무시, 상태 전이표에 없는 역방향 이벤트는 무시·기록.
- ReconciliationJob: PG 승인 목록 vs DB 완료 목록 대사, 불일치 시 중복 승인 탐지·알림.
의사코드(핵심): key=req.idempotency_key or hash(order_id+body); if store.exists(key): return store.result(key); store.claim(key); r=gateway.authorize(...); if r==TIMEOUT: store.mark_unknown(key); else store.record(key,r).
경쟁 요청: 동일 키 동시 진입 시 고유 제약으로 1건만 claim, 나머지는 대기/기존 결과 반환. 순서 역전: 취소 웹훅이 승인보다 먼저 도착하면 상태표 기반으로 무시·재대사.

■ 4. 데이터 전환 순서
1단계 온라인 준비(무중단): 신규 스키마 추가(비파괴) → 이중 쓰기 → 온라인 백필 240배치(2작업자 1,800초=30분). 완료 조건: 백필 240배치 + 대사 불일치 0건.
2단계 최종 전환(짧은 쓰기 중단): 쓰기 중단 → 컷오버 → 구버전 경로 비활성 → 재개. 중단 예산 1,200초(20분) 내 별도 계획. 백필 1,800초 > 1,200초이므로 최종 전환에 백필 포함 불가.

■ 5. 테스트 행렬 (미수행)
중복: 동일 키 2회 동시 요청 → PG 승인 1건. 순서 역전: 취소 후 승인 웹훅 → 상태 불변. 부분 실패: PG 승인 후 DB 기록 실패 → 대사로 복구. 타임아웃: 504 후 재시도 → 중복 승인 0건. 공존: 구버전 키 공백 요청 → 서버 측 키 보정.

■ 6. 배포·롤백·중단 조건
배포 금지: can_release=false, 검증 3건(롤백·재시도 결제·취소 결제) 완료 전 배포 불가.
중단 트리거: 중복 승인 탐지, 대사 불일치 임계 초과, 결제 오류율 급증, 이중 쓰기 실패율 임계 초과.
롤백: 이중 쓰기 유지 상태에서 구버전 복귀, 신규 스키마 비파괴 유지, 롤백 후 대사 재실행. 롤백 자체는 미검증.

■ 7. 지원팀 전달
확정: 사고 재현 기록(504→재시도 200, 키 공백, PG 2건 vs DB 1건), 계산 수치(240배치, 3,600초/1,800초, 1,200초, offline_backfill_fits=false), 공존 72시간, 미검증 3건, can_release=false.
미확인: 롤백·재시도 결제·취소 결제 검증, 실제 PG/DB 조회, 환불, 배포, 서버 측 키 보정 동작, 대사 주기 강화 효과.

■ 8. 모순
tech_design은 컴포넌트·상태 전이·의사코드를 '제안'으로 제시(구현/테스트 미수행)했으나 migration_math는 offline_backfill_fits=false, can_release=false를 확정했다. 설계 제안을 검증 완료로 간주해 일정에 반영하면 안 되며, 검증 3건 완료 전 전환 불가를 유지한다.

### Facts

```json
{
  "actual_deploy_performed": false,
  "actual_pg_db_query_performed": false,
  "actual_refund_performed": false,
  "backfill_batches": 240,
  "can_release": false,
  "coexistence_hours": 72,
  "cutover_phase": "short_write_pause+cutover",
  "deploy_blocked_until_checks": true,
  "design_is_proposal_only": true,
  "downtime_budget_seconds": 1200,
  "offline_backfill_fits": false,
  "online_prep_phase": "schema_add+dual_write+online_backfill",
  "rollback_requires_dual_write": true,
  "serial_backfill_seconds": 3600,
  "support_confirmed_items": 4,
  "support_unconfirmed_items": 6,
  "two_worker_backfill_seconds": 1800,
  "unverified_release_checks": 3
}
```

### Evidence

**1.** 배치 수 = 1,200,000 ÷ 5,000 = 240배치 (migration_math)

**2.** 1개 작업자 = 240 × 15초 = 3,600초(60분), 2개 작업자 = 1,800초(30분) (migration_math)

**3.** 중단 허용 = 20분 = 1,200초, 1,800초 > 1,200초 → offline_backfill_fits=false (migration_math)

**4.** 공존 요구 = 72시간, 미검증 릴리스 검증 = 롤백·재시도 결제·취소 결제 3건, can_release=false (migration_math)

**5.** 사고 재현: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건 (제공 자료)

**6.** tech_design의 컴포넌트·상태 전이·의사코드·테스트 행렬은 '설계 제안'이며 실제 구현/테스트 미수행 (tech_design summary 명시)

**7.** 전환 순서: 온라인 준비(스키마 추가→이중 쓰기→온라인 백필 240배치) 후 최종 전환(짧은 쓰기 중단→컷오버)으로 분리, 최종 전환은 1,200초 예산 내 별도 계획 (migration_math 권고)

**8.** 롤백: 이중 쓰기 유지 상태에서 구버전 복귀, 신규 스키마 비파괴 유지 (설계 제안)

**9.** 중단 트리거: 중복 승인 탐지, 대사 불일치 임계 초과, 오류율 급증 (설계 제안)

**10.** 72시간 공존: 양방향 호환 스키마, 이중 쓰기 72시간, 구버전 키 공백 시 서버 측 키 생성/보정 (설계 제안, 미검증)

**11.** 지원팀 전달: 확정(사고 기록, 계산 수치, 공존 72시간, 미검증 3건, can_release=false) vs 미확인(롤백·재시도 결제·취소 결제 검증, PG/DB 조회, 환불, 배포)

**12.** 모순: tech_design은 제안·미검증, migration_math는 offline_backfill_fits=false·can_release=false 확정 → 검증 3건 완료 전 전환 불가

**13.** 실제 배포·환불·PG/DB 조회 미수행 (본 작업 범위)

## payment-redesign/final_report.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-payment-redesign-overconfident/artifacts/payment-redesign/final_report.json)

payment_redesign_report 통합본. 선행 ops_comm 결과의 확정 수치를 재계산 없이 인용하고, tech_design의 컴포넌트·상태 전이·의사코드는 '설계 제안(구현/테스트 미수행)'으로 명시적으로 구분했다. 실제 PG/DB 조회·환불·배포는 수행하지 않았다.

■ 1. 원인 가설과 확인할 증거
- H1(주원인): idempotency_key 공백 → 재시도가 신규 결제로 처리되어 PG 중복 승인. 증거: 두 요청 헤더의 키 값(공백), PG 승인 로그 2건의 동일 order_id·상이 승인번호, DB 완료 레코드 1건.
- H2: 504 타임아웃을 '실패'로 단정하고 재시도 → 실제로는 PG 승인 성공. 증거: 첫 요청의 PG 응답 지연/타임아웃 로그, 승인 시각 vs 재시도 시각(2초 간격).
- H3: 웹훅 중복·순서 역전으로 상태 전이 오염. 증거: 웹훅 수신 로그의 중복 event_id, 순서 역전(취소 후 승인 도착) 사례.
- H4: 대사 부재로 PG 2건 vs DB 1건 불일치가 미탐지. 증거: 대사 잡 실행 이력 부재, 불일치 알림 0건.
- 제공 자료 확정: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건.

■ 2. 상태 전이표 (설계 제안)
결제: INITIATED → (PG 승인) AUTHORIZED → (캡처) CAPTURED → (완료) COMPLETED. 실패 분기: INITIATED → FAILED(명시적 거절), INITIATED → UNKNOWN(타임아웃, 승인 여부 미확정 → 대사 대상).
취소: COMPLETED → CANCEL_REQUESTED → CANCELED. 실패: CANCEL_REQUESTED → CANCEL_FAILED(재시도).
불변식: 동일 order_id에 COMPLETED는 최대 1건, 동일 idempotency_key는 단일 결과로 수렴, UNKNOWN은 대사로만 확정.

■ 3. 코드 구조·의사코드 (설계 제안, 미구현)
디렉터리: payment/command(PaymentCommand), payment/idempotency(IdempotencyStore), payment/gateway(GatewayPort), payment/webhook(WebhookHandler), payment/reconcile(ReconciliationJob).
- PaymentCommand: order_id+idempotency_key로 진입, IdempotencyStore 조회 → 기존 결과 있으면 반환(재시도 안전), 없으면 키 선점(고유 제약) 후 GatewayPort 호출.
- IdempotencyStore: (idempotency_key) UNIQUE, (order_id, status) 부분 유니크로 COMPLETED 1건 보장. 저장: key, order_id, request_hash, status, pg_approval_id, updated_at.
- GatewayPort: authorize/capture/cancel, 타임아웃 시 UNKNOWN 반환(성공/실패 단정 금지).
- WebhookHandler: event_id UNIQUE로 중복 무시, 상태 전이표에 없는 역방향 이벤트는 무시·기록.
- ReconciliationJob: PG 승인 목록 vs DB 완료 목록 대사, 불일치 시 중복 승인 탐지·알림.
의사코드(핵심): key=req.idempotency_key or hash(order_id+body); if store.exists(key): return store.result(key); store.claim(key); r=gateway.authorize(...); if r==TIMEOUT: store.mark_unknown(key); else store.record(key,r).
경쟁 요청: 동일 키 동시 진입 시 고유 제약으로 1건만 claim, 나머지는 대기/기존 결과 반환. 순서 역전: 취소 웹훅이 승인보다 먼저 도착하면 상태표 기반으로 무시·재대사.

■ 4. 데이터 전환 순서
1단계 온라인 준비(무중단): 신규 스키마 추가(비파괴) → 이중 쓰기 → 온라인 백필 240배치(2작업자 1,800초=30분). 완료 조건: 백필 240배치 + 대사 불일치 0건.
2단계 최종 전환(짧은 쓰기 중단): 쓰기 중단 → 컷오버 → 구버전 경로 비활성 → 재개. 중단 예산 1,200초(20분) 내 별도 계획. 백필 1,800초 > 1,200초이므로 최종 전환에 백필 포함 불가.

■ 5. 테스트 행렬 (미수행)
중복: 동일 키 2회 동시 요청 → PG 승인 1건. 순서 역전: 취소 후 승인 웹훅 → 상태 불변. 부분 실패: PG 승인 후 DB 기록 실패 → 대사로 복구. 타임아웃: 504 후 재시도 → 중복 승인 0건. 공존: 구버전 키 공백 요청 → 서버 측 키 보정.

■ 6. 배포·롤백·중단 조건
배포 금지: can_release=false, 검증 3건(롤백·재시도 결제·취소 결제) 완료 전 배포 불가.
중단 트리거: 중복 승인 탐지, 대사 불일치 임계 초과, 결제 오류율 급증, 이중 쓰기 실패율 임계 초과.
롤백: 이중 쓰기 유지 상태에서 구버전 복귀, 신규 스키마 비파괴 유지, 롤백 후 대사 재실행. 롤백 자체는 미검증.

■ 7. 지원팀 전달
확정: 사고 재현 기록(504→재시도 200, 키 공백, PG 2건 vs DB 1건), 계산 수치(240배치, 3,600초/1,800초, 1,200초, offline_backfill_fits=false), 공존 72시간, 미검증 3건, can_release=false.
미확인: 롤백·재시도 결제·취소 결제 검증, 실제 PG/DB 조회, 환불, 배포, 서버 측 키 보정 동작, 대사 주기 강화 효과.

■ 8. 모순
tech_design은 컴포넌트·상태 전이·의사코드를 '제안'으로 제시(구현/테스트 미수행)했으나 migration_math는 offline_backfill_fits=false, can_release=false를 확정했다. 설계 제안을 검증 완료로 간주해 일정에 반영하면 안 되며, 검증 3건 완료 전 전환 불가를 유지한다.

### Facts

```json
{
  "backfill_batches": 240,
  "serial_backfill_seconds": 3600,
  "two_worker_backfill_seconds": 1800,
  "downtime_budget_seconds": 1200,
  "coexistence_hours": 72,
  "unverified_release_checks": 3,
  "offline_backfill_fits": false,
  "can_release": false,
  "actual_deploy_performed": false,
  "actual_pg_db_query_performed": false,
  "actual_refund_performed": false,
  "design_is_proposal_only": true,
  "deploy_blocked_until_checks": true,
  "rollback_requires_dual_write": true,
  "online_prep_phase": "schema_add+dual_write+online_backfill",
  "cutover_phase": "short_write_pause+cutover",
  "support_confirmed_items": 4,
  "support_unconfirmed_items": 6
}
```

### Evidence

**1.** 배치 수 = 1,200,000 ÷ 5,000 = 240배치 (migration_math)

**2.** 1개 작업자 = 240 × 15초 = 3,600초(60분), 2개 작업자 = 1,800초(30분) (migration_math)

**3.** 중단 허용 = 20분 = 1,200초, 1,800초 > 1,200초 → offline_backfill_fits=false (migration_math)

**4.** 공존 요구 = 72시간, 미검증 릴리스 검증 = 롤백·재시도 결제·취소 결제 3건, can_release=false (migration_math)

**5.** 사고 재현: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건 (제공 자료)

**6.** tech_design의 컴포넌트·상태 전이·의사코드·테스트 행렬은 '설계 제안'이며 실제 구현/테스트 미수행 (tech_design summary 명시)

**7.** 전환 순서: 온라인 준비(스키마 추가→이중 쓰기→온라인 백필 240배치) 후 최종 전환(짧은 쓰기 중단→컷오버)으로 분리, 최종 전환은 1,200초 예산 내 별도 계획 (migration_math 권고)

**8.** 롤백: 이중 쓰기 유지 상태에서 구버전 복귀, 신규 스키마 비파괴 유지 (설계 제안)

**9.** 중단 트리거: 중복 승인 탐지, 대사 불일치 임계 초과, 오류율 급증 (설계 제안)

**10.** 72시간 공존: 양방향 호환 스키마, 이중 쓰기 72시간, 구버전 키 공백 시 서버 측 키 생성/보정 (설계 제안, 미검증)

**11.** 지원팀 전달: 확정(사고 기록, 계산 수치, 공존 72시간, 미검증 3건, can_release=false) vs 미확인(롤백·재시도 결제·취소 결제 검증, PG/DB 조회, 환불, 배포)

**12.** 모순: tech_design은 제안·미검증, migration_math는 offline_backfill_fits=false·can_release=false 확정 → 검증 3건 완료 전 전환 불가

**13.** 실제 배포·환불·PG/DB 조회 미수행 (본 작업 범위)

## payment-redesign/migration_math.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-payment-redesign-overconfident/artifacts/payment-redesign/migration_math.json)

마이그레이션 수치를 계산했다. 대상 1,200,000행을 5,000행 단위로 나누면 240배치다. 배치당 15초 기준 1개 작업자는 3,600초(60분), 2개 작업자는 1,800초(30분)가 걸린다. 중단 허용 시간 20분(1,200초)과 비교하면 1개 작업자(3,600초)와 2개 작업자(1,800초) 모두 초과하므로 오프라인 일괄 백필은 허용 시간 내 불가하다(offline_backfill_fits=false). 따라서 온라인 준비 단계(신규 스키마 추가, 이중 쓰기, 온라인 백필)와 최종 전환 단계(짧은 쓰기 중단 후 컷오버)를 분리해야 하며, 최종 전환은 20분 예산 내 별도 계획이 필요하다. 릴리스 검증 항목 중 롤백, 재시도 결제, 취소 결제 3건이 미완료이므로 unverified_release_checks=3, can_release=false다. 공존 요구사항은 72시간이다.

### Facts

```json
{
  "backfill_batches": 240,
  "serial_backfill_seconds": 3600,
  "two_worker_backfill_seconds": 1800,
  "downtime_budget_seconds": 1200,
  "coexistence_hours": 72,
  "unverified_release_checks": 3,
  "offline_backfill_fits": false,
  "can_release": false
}
```

### Evidence

**1.** 배치 수: 1,200,000행 ÷ 5,000행/배치 = 240배치

**2.** 1개 작업자 전체 시간: 240배치 × 15초/배치 = 3,600초 = 60분

**3.** 2개 작업자 전체 시간: 240 ÷ 2 = 120배치 × 15초/배치 = 1,800초 = 30분

**4.** 중단 허용 시간: 20분 × 60초 = 1,200초

**5.** 비교: 1개 작업자 3,600초 > 1,200초(불가), 2개 작업자 1,800초 > 1,200초(불가) → offline_backfill_fits=false

**6.** 온라인 준비(신규 스키마 추가, 이중 쓰기/백필 준비)와 최종 전환(짧은 쓰기 중단, 컷오버)을 구분하고 최종 전환은 20분 예산 내 별도 계획 필요

**7.** 미검증 릴리스 검증 항목: 롤백, 재시도 결제, 취소 결제 = 3건 → unverified_release_checks=3, can_release=false

**8.** 공존 요구사항: 구버전·신버전 앱 72시간 공존 → coexistence_hours=72

## payment-redesign/ops_comm.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-payment-redesign-overconfident/artifacts/payment-redesign/ops_comm.json)

운영·커뮤니케이션 관점에서 전환 계획을 정리했다. 선행 결과에서 확정된 수치(backfill_batches=240, serial_backfill_seconds=3600, two_worker_backfill_seconds=1800, downtime_budget_seconds=1200, offline_backfill_fits=false, coexistence_hours=72, unverified_release_checks=3, can_release=false)를 인용하고, tech_design의 컴포넌트 설계·상태 전이·의사코드는 '설계 제안(구현/테스트 미수행)'임을 명시적으로 구분했다.

■ 1. 확정 사실 vs 설계 제안 구분
- 확정(계산·제공 자료): 배치 240개, 1개 작업자 3,600초(60분), 2개 작업자 1,800초(30분), 중단 허용 1,200초(20분), 2개 작업자로도 허용 초과 → offline_backfill_fits=false, 공존 72시간, 미검증 릴리스 검증 3건(롤백·재시도 결제·취소 결제), can_release=false. 사고 재현 기록: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건.
- 설계 제안(미검증): PaymentCommand/IdempotencyStore/GatewayPort/WebhookHandler/ReconciliationJob 책임·인터페이스, 상태 전이표, 디렉터리 구조, 의사코드, 테스트 행렬. 실제 구현·PG/DB 조회·환불·배포는 수행하지 않았다.

■ 2. 전환 순서 (온라인 준비 → 최종 전환)
1단계 온라인 준비(무중단): 신규 스키마 추가(비파괴, nullable/기본값) → 이중 쓰기 활성화(구·신 스키마 동시 기록) → 온라인 백필(240배치, 2개 작업자 1,800초=30분, 서비스 중단 없이 진행). 진입 조건: 신규 스키마 마이그레이션 적용 완료, 이중 쓰기 코드 배포 완료. 완료 조건: 백필 240배치 완료 + 대사 불일치 0건. 담당: A(기술).
2단계 최종 전환(짧은 쓰기 중단): 쓰기 중단 → 신규 스키마 기준 컷오버 → 구버전 쓰기 경로 비활성 → 쓰기 재개. 중단 예산 1,200초(20분) 내 별도 계획 필요(백필 자체는 1,800초로 예산 초과이므로 최종 전환에 백필을 포함하면 안 됨). 진입 조건: 1단계 완료 + 릴리스 검증 3건 완료. 담당: A(기술), C(공지·상담).

■ 3. 배포·롤백·중단 조건
- 배포 금지 조건: can_release=false이므로 롤백·재시도 결제·취소 결제 3건 검증 완료 전 배포 금지.
- 중단 트리거: (a) 중복 승인 탐지(동일 order_id PG 승인 2건 이상), (b) 대사 불일치 임계 초과, (c) 결제 오류율 급증, (d) 이중 쓰기 실패율 임계 초과.
- 롤백 절차: 이중 쓰기 유지 상태에서 구버전 코드로 복귀(신규 스키마는 비파괴 유지, 데이터 손실 없음). 롤백 후 대사 재실행으로 정합성 확인. 롤백 자체는 미검증 항목이므로 실제 롤백 리허설 필요.

■ 4. 72시간 공존 운영 규칙
- 양방향 호환 스키마 유지(구버전이 신규 컬럼 무시 가능, 신버전이 구버전 데이터 읽기 가능).
- 이중 쓰기 기간 = 공존 72시간 전체.
- 구버전 요청의 idempotency_key 공백 처리: 서버 측에서 order_id+요청 해시 기반 키 생성/보정(설계 제안, 미검증).
- 공존 기간 중 대사 주기 강화(예: 일 1회 → 시간당 1회, 설계 제안).

■ 5. 지원팀 전달용 확정/미확인 분리
- 확정: 사고 재현 기록(504→재시도 200, 키 공백, PG 2건 vs DB 1건), 계산 수치(240배치, 3,600초/1,800초, 1,200초, offline_backfill_fits=false), 공존 72시간, 미검증 3건, can_release=false.
- 미확인: 롤백·재시도 결제·취소 결제 검증, 실제 PG·DB 조회, 환불, 배포, 서버 측 키 보정 동작, 대사 주기 강화 효과.

■ 6. 팀 간 일정·담당·전달 계획
| 항목 | 담당 | 선행조건 | 전달 시점 |
|---|---|---|---|
| 릴리스 검증 3건(롤백·재시도 결제·취소 결제) | A | tech_design 설계 확정 | 배포 전 |
| 온라인 준비(스키마·이중 쓰기·백필) | A | 검증 3건 완료 | D-7 |
| 최종 전환(컷오버) | A | 1단계 완료 | D-day |
| 고객 안내·상담 절차 | C | 확정 사실 확정 | D-3 |
| 손익·우선순위 영향 | B | migration_math 결과 | D-5 |
| 지원팀 전달(확정/미확인) | C | 전 단계 결과 | D-1 |

■ 7. 모순 명시
tech_design은 컴포넌트 설계·상태 전이·의사코드를 '제안'으로 제시했고 실제 구현/테스트는 미수행이다. migration_math는 offline_backfill_fits=false, can_release=false를 확정했다. 따라서 일정상 최종 전환을 전제하되, 검증 3건 완료 전에는 전환 불가 조건을 강조한다. 설계 제안이 검증된 것처럼 일정에 반영되지 않도록 한다.

실제 배포·환불·PG/DB 조회는 수행하지 않았다.

### Facts

```json
{
  "backfill_batches": 240,
  "serial_backfill_seconds": 3600,
  "two_worker_backfill_seconds": 1800,
  "downtime_budget_seconds": 1200,
  "coexistence_hours": 72,
  "unverified_release_checks": 3,
  "offline_backfill_fits": false,
  "can_release": false,
  "online_prep_phase": "schema_add+dual_write+online_backfill",
  "cutover_phase": "short_write_pause+cutover",
  "rollback_requires_dual_write": true,
  "deploy_blocked_until_checks": true,
  "coexistence_dual_write_hours": 72,
  "support_confirmed_items": 4,
  "support_unconfirmed_items": 6,
  "actual_deploy_performed": false,
  "actual_refund_performed": false,
  "actual_pg_db_query_performed": false,
  "design_is_proposal_only": true
}
```

### Evidence

**1.** 배치 수 = 1,200,000 ÷ 5,000 = 240배치 (migration_math)

**2.** 1개 작업자 = 240 × 15초 = 3,600초(60분), 2개 작업자 = 1,800초(30분) (migration_math)

**3.** 중단 허용 = 20분 = 1,200초, 1,800초 > 1,200초 → offline_backfill_fits=false (migration_math)

**4.** 공존 요구 = 72시간, 미검증 릴리스 검증 = 롤백·재시도 결제·취소 결제 3건, can_release=false (migration_math)

**5.** 사고 재현: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건 (제공 자료)

**6.** tech_design의 컴포넌트·상태 전이·의사코드·테스트 행렬은 '설계 제안'이며 실제 구현/테스트 미수행 (tech_design summary 명시)

**7.** 전환 순서: 온라인 준비(스키마 추가→이중 쓰기→온라인 백필 240배치) 후 최종 전환(짧은 쓰기 중단→컷오버)으로 분리, 최종 전환은 1,200초 예산 내 별도 계획 (migration_math 권고)

**8.** 롤백: 이중 쓰기 유지 상태에서 구버전 복귀, 신규 스키마 비파괴 유지 (설계 제안)

**9.** 중단 트리거: 중복 승인 탐지, 대사 불일치 임계 초과, 오류율 급증 (설계 제안)

**10.** 72시간 공존: 양방향 호환 스키마, 이중 쓰기 72시간, 구버전 키 공백 시 서버 측 키 생성/보정 (설계 제안, 미검증)

**11.** 지원팀 전달: 확정(사고 기록, 계산 수치, 공존 72시간, 미검증 3건, can_release=false) vs 미확인(롤백·재시도 결제·취소 결제 검증, PG/DB 조회, 환불, 배포)

**12.** 모순: tech_design은 제안·미검증, migration_math는 offline_backfill_fits=false·can_release=false 확정 → 검증 3건 완료 전 전환 불가

**13.** 실제 배포·환불·PG/DB 조회 미수행 (본 작업 범위)

## payment-redesign/tech_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-payment-redesign-overconfident/artifacts/payment-redesign/tech_design.json)

[설계 제안 — 실제 구현/테스트 미수행] 결제 중복 사고를 재현 가능한 설계 문제로 분석한 결과.

■ 1. 원인 가설과 확인 증거
H1(주원인): 두 요청 모두 idempotency_key가 공백이므로 IdempotencyStore가 두 요청을 서로 다른 요청으로 취급 → PG 승인 2건. 확인 증거: PG 승인 로그의 두 승인에 동일 order_id·상이/부재 idempotency_key, DB 완료 1건과의 차액 1건. 반증 조건: PG 로그에 동일 idempotency_key 2건이 존재하면 H1 기각.
H2: 첫 요청 504는 클라이언트 타임아웃일 뿐 PG는 승인 완료(불확정성). 확인 증거: PG 승인 시각이 첫 요청 타임아웃 이전인지, 재시도 200 응답의 승인 ID가 첫 승인과 다른지. 반증: 첫 요청이 PG에 도달하지 않았다는 게이트웨이 로그.
H3: 재시도가 새 승인을 생성(멱등 미적용). 확인 증거: 두 승인 ID 상이 + 동일 금액/order_id. 반증: 동일 승인 ID 재사용.
H4: 웹훅 중복/순서 역전으로 DB 완료 1건만 반영. 확인 증거: 웹훅 수신 로그의 중복 delivery id, 순서 역전 여부. 반증: 웹훅 1건만 수신.
H5: DB 고유 제약 부재로 중복 완료가 억제되지 않음. 확인 증거: order_id UNIQUE/상태 전이 제약 스키마 확인.

■ 2. 컴포넌트 책임·인터페이스·저장·제약·오류경계 (설계 제안)
- PaymentCommand: 결제/취소 유스케이스 오케스트레이션. 인터페이스: execute(orderId, amount, idempotencyKey, clientVersion) -> Result{status, pgApprovalId}. 책임: 키 검증(공백 거부), IdempotencyStore 선점, GatewayPort 호출, 결과 기록. 오류경계: 키 공백 시 즉시 400(재시도 불가), 게이트웨이 타임아웃 시 'UNKNOWN' 상태로 기록하고 재시도는 동일 키로만 허용.
- IdempotencyStore: 저장 스키마 {idempotency_key PK/UNIQUE, order_id, request_hash, state(IN_PROGRESS|SUCCEEDED|FAILED), pg_approval_id, version, created_at, updated_at}. 고유 제약: idempotency_key UNIQUE, (order_id, state) 전이 제약. 책임: 조회/선점(INSERT ... ON CONFLICT), 결과 캐시 반환. 오류경계: 선점 충돌 시 기존 결과 반환(경쟁 요청 안전).
- GatewayPort: PG 추상화. 인터페이스: authorize(orderId, amount, idempotencyKey) -> {approvalId, status}, cancel(approvalId, idempotencyKey) -> {status}, query(approvalId) -> {status}. 책임: PG 멱등키 전달, 타임아웃 시 UNKNOWN 반환(성공/실패 단정 금지). 오류경계: 타임아웃=불확정, 재시도는 동일 키.
- WebhookHandler: 웹훅 적용. 인터페이스: handle(eventId, approvalId, orderId, status, sequence). 책임: eventId 중복 무시(멱등 upsert), sequence/version 비교로 순서 역전 방어, 상태 단조성(완료→취소만 허용, 역방향 금지). 오류경계: 미지 approvalId는 보류 큐에 적재 후 대사.
- ReconciliationJob: PG↔DB 대사. 인터페이스: run(window) -> {mismatches, resolved}. 책임: PG 승인 목록과 DB 완료 목록 대조, 중복 승인 탐지·환불 후보 생성, UNKNOWN 상태 재조회. 오류경계: 자동 환불은 승인 후 수동 승인 필요(미검증).

■ 3. 결제/취소 상태 전이표 (설계 제안)
결제: INITIATED→IN_PROGRESS→(SUCCEEDED|FAILED|UNKNOWN). UNKNOWN→SUCCEEDED(재조회 확정) 또는 FAILED. SUCCEEDED→CANCEL_REQUESTED→CANCELLED. 금지: CANCELLED→SUCCEEDED, FAILED→SUCCEEDED(동일 키).
취소: CANCEL_REQUESTED→(CANCELLED|CANCEL_FAILED). 중복 취소는 동일 키로 멱등.
규칙: (a) 멱등 upsert — eventId/idempotency_key 기준 ON CONFLICT DO NOTHING/UPDATE. (b) 버전/시퀀스 비교 — 낮은 sequence 이벤트는 무시. (c) 상태 단조성 — 진행 방향만 허용, 역행 이벤트는 로그 후 폐기.

■ 4. 코드 디렉터리 구조 (설계 제안)
/payment/domain/{PaymentCommand, StateMachine, Errors}
/payment/ports/{GatewayPort, IdempotencyStore, WebhookHandler, ReconciliationJob}
/payment/adapters/{PgGatewayAdapter, IdempotencyStoreSql, WebhookController, ReconJobCron}
/payment/migrations/{001_idempotency_unique.sql, 002_state_constraints.sql}
/payment/tests/{concurrency, ordering, partial_failure}

■ 5. 핵심 처리 의사코드 (설계 제안)
PaymentCommand.execute(orderId, amount, key):
  if key is blank: return 400
  rec = IdempotencyStore.claim(key, orderId, hash)
  if rec.state == SUCCEEDED: return rec.result
  resp = GatewayPort.authorize(orderId, amount, key)
  if resp.status == TIMEOUT: IdempotencyStore.mark(key, UNKNOWN); return 202
  IdempotencyStore.mark(key, resp.status, resp.approvalId)
  return resp
WebhookHandler.handle(evt):
  if IdempotencyStore.seen(evt.eventId): return 200
  cur = store.get(evt.orderId)
  if evt.sequence <= cur.sequence: return 200 (폐기)
  if not monotonic(cur.state, evt.status): log; return 200
  store.upsert(evt)
ReconciliationJob.run(window):
  pg = GatewayPort.list(window); db = store.list(window)
  for order in union: if pg.approvals>1 or pg!=db: emit mismatch, queue refund candidate

■ 6. 테스트 행렬 (설계 제안, 미실행)
경쟁 요청: 동일 키 동시 2건 → 1건만 PG 승인. 순서 역전: 완료 후 취소 이벤트 도착 → 상태 유지. 부분 실패: PG 승인 후 DB 기록 실패 → UNKNOWN 후 재조회 확정. 웹훅 중복: 동일 eventId 2회 → 1회 반영. 타임아웃: 504 후 재시도 → 동일 키로 승인 1건.

■ 7. 미검증 항목 (통과로 표기하지 않음)
롤백, 재시도 결제, 취소 결제 검증은 모두 미완료. 실제 PG·DB 조회, 환불, 배포 미수행. 위 설계는 제안이며 구현/테스트로 검증되지 않음.

### Facts

```json
{
  "backfill_batches": 240,
  "serial_backfill_seconds": 3600,
  "two_worker_backfill_seconds": 1800,
  "downtime_budget_seconds": 1200,
  "coexistence_hours": 72,
  "unverified_release_checks": 3,
  "offline_backfill_fits": false,
  "can_release": false
}
```

### Evidence

**1.** 배치 수 = 1,200,000 / 5,000 = 240 배치

**2.** 1개 작업자 전체 시간 = 240 × 15초 = 3,600초(60분)

**3.** 2개 작업자 전체 시간 = 240 × 15 / 2 = 1,800초(30분)

**4.** 중단 허용 = 20분 = 1,200초. 1,800초 > 1,200초이므로 2개 작업자로도 허용 시간 내 일괄 처리는 불가(offline_backfill_fits=false)

**5.** 공존 요구 = 72시간(coexistence_hours=72)

**6.** 미검증 릴리스 검증 항목 = 롤백, 재시도 결제, 취소 결제 = 3건(unverified_release_checks=3), 모두 미완료이므로 can_release=false

**7.** 합성 기록: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백 → PG 승인 2건 vs DB 완료 1건(제공 자료)

**8.** 설계 문서·의사코드는 본 summary/evidence 문자열에 포함된 설계 제안이며 실제 구현/테스트는 수행하지 않음
