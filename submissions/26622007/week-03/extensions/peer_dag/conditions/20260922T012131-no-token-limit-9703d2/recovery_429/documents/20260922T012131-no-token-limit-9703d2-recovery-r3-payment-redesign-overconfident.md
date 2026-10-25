# payment-redesign / overconfident — 429 복구

상태: succeeded. facts: 8/8.
모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.

## payment-redesign.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident/artifacts/payment-redesign.json)

세 선행 결과(tech_design, migration_calc, support_comms)를 통합해 payment_redesign_report를 확정했다. 모든 산출물은 제공된 합성 기록과 가정에 기반한 설계 제안이며, 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포·환불은 수행하지 않았다.

[1] 원인 가설과 확인할 증거
- H1(가장 유력): 두 요청 모두 idempotency_key가 비어 PG가 별개 승인으로 처리. 증거: PG 승인 2건, 주문 DB 완료 1건, idempotency_key 공백.
- H2: 첫 요청 504를 실패로 오판하고 2초 뒤 재시도. 증거: 첫 요청 504, 재시도 200, 2초 간격. 타임아웃만으로 승인 확정 불가.
- H3: 웹훅 중복·순서 역전으로 상태 전이 꼬임. 증거: 웹훅 중복/역전 가능성 명시. 단 PG 2건/DB 1건은 웹훅만으로 설명 안 됨.
- H4: PG-DB 불일치(대사 부재). 증거: PG 2건 vs DB 1건.
- 확인 필요(미확인): PG 승인 로그의 원 요청 식별자, 504 시점 PG 실제 처리 여부, 웹훅 도착 순서 로그, 클라이언트 재시도 정책.

[2] 상태 전이표(설계 제안)
결제: INITIATED→AUTHORIZING→(APPROVED→CAPTURED | DECLINED | UNKNOWN→(APPROVED|DECLINED)). UNKNOWN 재시도는 동일 idempotency_key/reqId로만 허용.
취소: CAPTURED→CANCEL_REQUESTED→(CANCELED | CANCEL_FAILED). CANCELED는 종결, 재취소 금지.
금지 전이: CANCELED→CAPTURED, CAPTURED→INITIATED, 늦은 웹훅으로 인한 역행.

[3] 코드 구조와 핵심 의사코드(설계 제안)
구조: /payment/domain/{PaymentCommand, OrderState, StateTransition}, /payment/app/{IdempotencyStore, GatewayPort, WebhookHandler, ReconciliationJob}, /payment/infra/{PgGatewayAdapter, IdempotencyRepo, WebhookRepo}, /payment/api/{PaymentController, WebhookController}, /payment/jobs/{ReconciliationJobRunner}.
책임·인터페이스: PaymentCommand.execute(orderId, amount, idempotencyKey, op); IdempotencyStore.tryAcquire(key, orderId)->{ACQUIRED|IN_PROGRESS|DONE}, complete(key,result); GatewayPort.authorize(reqId,orderId,amount)->{APPROVED|DECLINED|UNKNOWN}, queryStatus(reqId); WebhookHandler.handle(eventId,orderId,type,seq,occurredAt); ReconciliationJob.run(window)->{matched,pgOnly,dbOnly}.
제약: idempotency_key UNIQUE, webhook eventId UNIQUE, order 상태 전이 유효성.
의사코드: execute: key 비면 거절; r=store.tryAcquire; r!=ACQUIRED면 r.result 반환; res=gateway.authorize; res==UNKNOWN이면 queryStatus로 확정; store.complete; 반환. tryAcquire: INSERT ... ON CONFLICT(key) DO NOTHING. handle: eventId 존재 시 무시, seq<lastSeq면 무시(역전), 전이 적용 후 저장.
경쟁 요청 타당성: 동일 키 동시 진입 시 UNIQUE 제약으로 하나만 ACQUIRED → PG 승인 1건 보장. 현재 사고는 키가 비어 방어 미작동. 순서 역전: seq/occurredAt 비교로 늦은 이벤트 역행 차단, eventId UNIQUE로 중복 무시.

[4] 데이터 전환 순서(설계 제안)
온라인 준비(무중단): 신규 스키마 추가 → 이중 쓰기 → 백그라운드 백필. 최종 전환: 짧은 쓰기 차단 → 검증 → 트래픽 전환. 72시간 공존 기간에는 구·신버전 동시 쓰기가 발생하므로 order_id 기반 고유 제약과 멱등성 키 저장소로 중복 청구를 차단한다.

[5] 테스트 행렬(설계 제안, 미실행)
- 중복: 동일 키 동시 2요청 → 승인 1건 기대.
- 순서 역전: 늦은 웹훅 → 상태 역행 없음 기대.
- 부분 실패: PG 승인 후 DB 기록 실패 → 대사로 불일치 탐지 기대.
모두 '미실행/미검증'으로 표기.

[6] 배포·롤백·중단 조건(설계 제안)
배포: 온라인 백필 → 이중쓰기/이중읽기 → 컷오버. 롤백: 구버전 스키마 유지 기간 내 복귀. 중단 조건: 대사 불일치율 임계 초과, 승인 중복 재발, 컷오버 중 오류율 급증.

[7] 지원팀 전달 사실 vs 미확인
사실: PG 승인 2건, DB 완료 1건, 두 요청 idempotency_key 공백, 2초 간격, 첫 요청 504, 재시도 200, 웹훅 중복/역전 가능, 타임아웃만으로 승인 확정 불가.
미확인: 실제 중복 청구 확정 여부, 환불 필요성, 웹훅 순서 로그, 롤백·재시도·취소 검증 결과. 미검증을 '정상/통과/환불 완료'로 표현 금지.

[8] 모순·미확인 사항
- offline_backfill_fits 상충: migration_calc와 tech_design은 false, support_comms는 true로 기재. 계산 근거(240배치, 2작업자 1,800초 > 1,200초 예산)에 따라 false로 확정하며 support_comms의 true는 오기다.
- 72시간 공존 요구와 20분 중단 예산 간 정합성: 공존 기간 중 이중쓰기 필요, 컷오버는 별도 창 필요.
- offline_backfill_fits=false이므로 '일괄 오프라인 전환' 전제와 20분 예산이 모순.
- 롤백·재시도·취소 검증 미완료로 릴리스 불가(can_release=false).

[9] 실제 수행 구분
실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포, 환불은 수행하지 않았다. 모든 수치·표·의사코드는 제공 자료와 가정에 기반한 설계 제안이다.

### Facts

```json
{
  "migration_rows": 1200000,
  "batch_size": 5000,
  "batch_seconds": 15,
  "backfill_batches": 240,
  "serial_backfill_seconds": 3600,
  "two_worker_backfill_seconds": 1800,
  "max_workers": 2,
  "downtime_budget_seconds": 1200,
  "offline_backfill_fits": false,
  "coexistence_hours": 72,
  "unverified_release_checks": 3,
  "can_release": false,
  "pg_approvals_observed": 2,
  "db_completed_observed": 1,
  "idempotency_key_present": false,
  "first_request_status": 504,
  "second_request_status": 200,
  "retry_interval_seconds": 2,
  "timeout_alone_confirms_approval": false,
  "webhook_duplicate_possible": true,
  "webhook_order_reversal_possible": true,
  "rollback_verification_done": false,
  "retry_payment_verification_done": false,
  "cancel_payment_verification_done": false,
  "actual_pg_db_queried": false,
  "actual_deploy_performed": false,
  "actual_refund_performed": false,
  "tests_executed": false,
  "design_only": true,
  "support_comms_offline_backfill_fits_conflict": true
}
```

### Evidence

1. 배치 수 = 1,200,000행 / 5,000행 = 240배치 (제공 자료: 마이그레이션 대상 1,200,000행, 배치 5,000행)
2. 1개 작업자 전체 시간 = 240배치 × 15초 = 3,600초 = 60분 (제공 자료: 배치당 15초)
3. 2개 작업자 전체 시간 = 240 / 2 = 120배치/작업자 × 15초 = 1,800초 = 30분 (제공 자료: 작업자 최대 2개, 병목·오버헤드 없음 가정)
4. 중단 허용 예산 = 20분 × 60초 = 1,200초 (제공 자료: 서비스 중단 허용 시간 20분)
5. 비교: 1개 작업자 3,600초 > 1,200초, 2개 작업자 1,800초 > 1,200초 → 오프라인 일괄 백필은 허용 시간 내 불가, offline_backfill_fits=false
6. 공존 기간 = 72시간 (제공 자료: 구버전·신버전 앱 72시간 공존 요구사항)
7. 미완료 검증 = 롤백, 재시도 결제, 취소 결제 3건 (제공 자료: 새 버전 데이터의 롤백·재시도·취소 검증 모두 미완료) → unverified_release_checks=3, can_release=false
8. 원인 증거: PG 승인 2건, 주문 DB 완료 1건, 두 요청 idempotency_key 공백, 첫 요청 504·2초 뒤 재시도 200 (제공 합성 기록)
9. 웹훅 중복/순서 역전 가능, 네트워크 타임아웃만으로 승인 확정 불가 (제공 자료)
10. 모순: support_comms는 offline_backfill_fits=true로 기재했으나 계산 근거(1,800초 > 1,200초)에 따라 false로 확정, support_comms 값은 오기
11. 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포, 환불은 수행하지 않았으며 모든 산출물은 설계 제안임
## payment-redesign/integrate.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident/artifacts/payment-redesign/integrate.json)

세 선행 결과(tech_design, migration_calc, support_comms)를 통합해 payment_redesign_report를 작성했다. 모든 산출물은 제공된 합성 기록과 가정에 기반한 설계 제안이며, 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포·환불은 수행하지 않았다.

[1] 원인 가설과 확인할 증거
- H1(가장 유력): 두 요청 모두 idempotency_key가 비어 있어 PG가 별개 승인으로 처리. 증거: PG 승인 2건, 주문 DB 완료 1건, idempotency_key 공백.
- H2: 첫 요청 504를 실패로 오판하고 2초 뒤 재시도. 증거: 첫 요청 504, 재시도 200, 2초 간격. 타임아웃만으로 승인 확정 불가.
- H3: 웹훅 중복·순서 역전으로 상태 전이 꼬임. 증거: 웹훅 중복/역전 가능성 명시. 단 PG 2건/DB 1건은 웹훅만으로 설명 안 됨.
- H4: PG-DB 불일치(대사 부재). 증거: PG 2건 vs DB 1건.
- 확인 필요(미확인): PG 승인 로그의 원 요청 식별자, 504 시점 PG 실제 처리 여부, 웹훅 도착 순서 로그, 클라이언트 재시도 정책.

[2] 상태 전이표(설계 제안)
결제: INITIATED→AUTHORIZING→(APPROVED→CAPTURED | DECLINED | UNKNOWN→(APPROVED|DECLINED)). UNKNOWN 재시도는 동일 idempotency_key/reqId로만 허용.
취소: CAPTURED→CANCEL_REQUESTED→(CANCELED | CANCEL_FAILED). CANCELED는 종결, 재취소 금지.
금지 전이: CANCELED→CAPTURED, CAPTURED→INITIATED, 늦은 웹훅으로 인한 역행.

[3] 코드 구조와 핵심 의사코드(설계 제안)
구조: /payment/domain/{PaymentCommand, OrderState, StateTransition}, /payment/app/{IdempotencyStore, GatewayPort, WebhookHandler, ReconciliationJob}, /payment/infra/{PgGatewayAdapter, IdempotencyRepo, WebhookRepo}, /payment/api/{PaymentController, WebhookController}, /payment/jobs/{ReconciliationJobRunner}.
책임·인터페이스: PaymentCommand.execute(orderId, amount, idempotencyKey, op); IdempotencyStore.tryAcquire(key, orderId)->{ACQUIRED|IN_PROGRESS|DONE}, complete(key,result); GatewayPort.authorize(reqId,orderId,amount)->{APPROVED|DECLINED|UNKNOWN}, queryStatus(reqId); WebhookHandler.handle(eventId,orderId,type,seq,occurredAt); ReconciliationJob.run(window)->{matched,pgOnly,dbOnly}.
제약: idempotency_key UNIQUE, webhook eventId UNIQUE, order 상태 전이 유효성.
의사코드: execute: key 비면 거절; r=store.tryAcquire; r!=ACQUIRED면 r.result 반환; res=gateway.authorize; res==UNKNOWN이면 queryStatus로 확정; store.complete; 반환. tryAcquire: INSERT ... ON CONFLICT(key) DO NOTHING. handle: eventId 존재 시 무시, seq<lastSeq면 무시(역전), 전이 적용 후 저장.
경쟁 요청 타당성: 동일 키 동시 진입 시 UNIQUE 제약으로 하나만 ACQUIRED → PG 승인 1건 보장. 현재 사고는 키가 비어 방어 미작동. 순서 역전: seq/occurredAt 비교로 늦은 이벤트 역행 차단, eventId UNIQUE로 중복 무시.

[4] 데이터 전환 순서(설계 제안)
온라인 준비(무중단): 신규 스키마 추가 → 이중 쓰기 → 백그라운드 백필. 최종 전환: 짧은 쓰기 차단 → 검증 → 트래픽 전환. 72시간 공존 기간에는 구·신버전 동시 쓰기가 발생하므로 order_id 기반 고유 제약과 멱등성 키 저장소로 중복 청구를 차단한다.

[5] 테스트 행렬(설계 제안, 미실행)
- 중복: 동일 키 동시 2요청 → 승인 1건 기대.
- 순서 역전: 늦은 웹훅 → 상태 역행 없음 기대.
- 부분 실패: PG 승인 후 DB 기록 실패 → 대사로 불일치 탐지 기대.
모두 '미실행/미검증'으로 표기.

[6] 배포·롤백·중단 조건(설계 제안)
배포: 온라인 백필 → 이중쓰기/이중읽기 → 컷오버. 롤백: 구버전 스키마 유지 기간 내 복귀. 중단 조건: 대사 불일치율 임계 초과, 승인 중복 재발, 컷오버 중 오류율 급증.

[7] 지원팀 전달 사실 vs 미확인
사실: PG 승인 2건, DB 완료 1건, 두 요청 idempotency_key 공백, 2초 간격, 첫 요청 504, 재시도 200, 웹훅 중복/역전 가능, 타임아웃만으로 승인 확정 불가.
미확인: 실제 중복 청구 확정 여부, 환불 필요성, 웹훅 순서 로그, 롤백·재시도·취소 검증 결과. 미검증을 '정상/통과/환불 완료'로 표현 금지.

[8] 모순·미확인 사항
- offline_backfill_fits 상충: migration_calc와 tech_design은 false, support_comms는 true로 기재. 계산 근거(240배치, 2작업자 1,800초 > 1,200초 예산)에 따라 false로 확정하며 support_comms의 true는 오기다.
- 72시간 공존 요구와 20분 중단 예산 간 정합성: 공존 기간 중 이중쓰기 필요, 컷오버는 별도 창 필요.
- offline_backfill_fits=false이므로 '일괄 오프라인 전환' 전제와 20분 예산이 모순.
- 롤백·재시도·취소 검증 미완료로 릴리스 불가(can_release=false).

[9] 실제 수행 구분
실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포, 환불은 수행하지 않았다. 모든 수치·표·의사코드는 제공 자료와 가정에 기반한 설계 제안이다.

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
  "migration_rows": 1200000,
  "batch_size": 5000,
  "batch_seconds": 15,
  "max_workers": 2,
  "pg_approvals_observed": 2,
  "db_completed_observed": 1,
  "idempotency_key_present": false,
  "first_request_status": 504,
  "second_request_status": 200,
  "retry_interval_seconds": 2,
  "webhook_duplicate_possible": true,
  "webhook_order_reversal_possible": true,
  "timeout_alone_confirms_approval": false,
  "rollback_verification_done": false,
  "retry_payment_verification_done": false,
  "cancel_payment_verification_done": false,
  "actual_pg_db_queried": false,
  "actual_deploy_performed": false,
  "actual_refund_performed": false,
  "tests_executed": false,
  "design_only": true,
  "support_comms_offline_backfill_fits_conflict": true
}
```

### Evidence

1. 배치 수 = 1,200,000행 / 5,000행 = 240배치 (제공 자료: 마이그레이션 대상 1,200,000행, 배치 5,000행)
2. 1개 작업자 전체 시간 = 240배치 × 15초 = 3,600초 = 60분 (제공 자료: 배치당 15초)
3. 2개 작업자 전체 시간 = 240 / 2 = 120배치/작업자 × 15초 = 1,800초 = 30분 (제공 자료: 작업자 최대 2개, 병목·오버헤드 없음 가정)
4. 중단 허용 예산 = 20분 × 60초 = 1,200초 (제공 자료: 서비스 중단 허용 시간 20분)
5. 비교: 1개 작업자 3,600초 > 1,200초, 2개 작업자 1,800초 > 1,200초 → 오프라인 일괄 백필은 허용 시간 내 불가, offline_backfill_fits=false
6. 공존 기간 = 72시간 (제공 자료: 구버전·신버전 앱 72시간 공존 요구사항)
7. 미완료 검증 = 롤백, 재시도 결제, 취소 결제 3건 (제공 자료: 새 버전 데이터의 롤백·재시도·취소 검증 모두 미완료) → unverified_release_checks=3, can_release=false
8. 원인 증거: PG 승인 2건, 주문 DB 완료 1건, 두 요청 idempotency_key 공백, 첫 요청 504·2초 뒤 재시도 200 (제공 합성 기록)
9. 웹훅 중복/순서 역전 가능, 네트워크 타임아웃만으로 승인 확정 불가 (제공 자료)
10. 모순: support_comms는 offline_backfill_fits=true로 기재했으나 계산 근거(1,800초 > 1,200초)에 따라 false로 확정, support_comms 값은 오기
11. 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포, 환불은 수행하지 않았으며 모든 산출물은 설계 제안임
## payment-redesign/migration_calc.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident/artifacts/payment-redesign/migration_calc.json)

마이그레이션 계산과 릴리스 판단을 수행했다. 대상 1,200,000행을 5,000행 배치로 나누면 240배치다. 배치당 15초 기준 1개 작업자는 3,600초(60분), 2개 작업자는 1,800초(30분)가 걸린다. 중단 허용 예산 20분(1,200초)과 비교하면 1개 작업자(3,600초)와 2개 작업자(1,800초) 모두 예산을 초과하므로 오프라인 일괄 백필은 허용 시간 내 불가하다(offline_backfill_fits=false). 따라서 온라인 준비 단계(신규 스키마 추가, 이중 쓰기, 서비스 중단 없이 백그라운드 백필 진행)와 최종 전환 단계(짧은 쓰기 차단, 검증, 트래픽 전환)를 분리해야 한다. 구버전·신버전 앱 공존 기간은 72시간이며, 이 기간에는 양쪽 버전이 동시에 쓰기를 수행하므로 order_id 기반 고유 제약과 멱등성 키 저장소로 중복 청구를 차단해야 한다. 릴리스 판단: 새 버전 데이터의 롤백, 재시도 결제, 취소 결제 검증 3건이 모두 미완료이므로 can_release=false다. 미검증 항목을 통과로 간주하지 않았고, 실제 PG·DB 조회나 배포·환불은 수행하지 않았다.

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

1. 배치 수 = 1,200,000행 / 5,000행 = 240배치 (제공 자료: 마이그레이션 대상 1,200,000행, 배치 5,000행)
2. 1개 작업자 전체 시간 = 240배치 × 15초 = 3,600초 = 60분 (제공 자료: 배치당 15초)
3. 2개 작업자 전체 시간 = 240배치 / 2작업자 = 120배치/작업자, 120 × 15초 = 1,800초 = 30분 (제공 자료: 작업자 최대 2개, 병목·오버헤드 없음 가정)
4. 중단 허용 예산 = 20분 × 60초 = 1,200초 (제공 자료: 서비스 중단 허용 시간 20분)
5. 비교: 1개 작업자 3,600초 > 1,200초, 2개 작업자 1,800초 > 1,200초이므로 오프라인 일괄 백필은 허용 시간 내 불가 → offline_backfill_fits=false
6. 공존 기간 = 72시간 (제공 자료: 구버전 앱과 신버전 앱의 72시간 공존 요구사항)
7. 미완료 검증 = 롤백, 재시도 결제, 취소 결제 3건 (제공 자료: 새 버전 데이터의 롤백·재시도 결제·취소 결제 검증은 모두 미완료)
8. can_release=false 근거: 미검증 릴리스 검사 3건이 남아 있고, 미검증을 통과로 바꾸지 말라는 요구사항에 따라 릴리스 불가로 판단
9. 온라인 준비와 최종 전환 구분: 온라인 준비는 신규 스키마 추가·이중 쓰기·백그라운드 백필로 서비스 중단 없이 진행하고, 최종 전환은 짧은 쓰기 차단·검증·트래픽 전환으로 분리한다. 240배치를 2작업자로 처리해도 30분이 걸려 20분 예산을 초과하므로 일괄 오프라인 전환은 불가하다.
## payment-redesign/support_comms.json

Worker: C; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident/artifacts/payment-redesign/support_comms.json)

지원팀 전달용 안내문 초안과 고객 안내 문구·상담 절차를 확정 사실과 미확인 사항으로 분리해 작성했다. 확정 사실은 제공 자료에 명시된 것만 채택했고, 추정·미검증은 별도 섹션으로 격리했다. 고객 안내는 '확인 중' 프레임을 유지하고 미검증을 통과로 표현하는 금지 표현을 명시했다. 실제 PG·DB 조회, 환불, 배포, 롤백·재시도·취소 검증은 수행하지 않았으며 모두 미검증으로 표기했다.

[1] 확정 사실 목록(제공 자료 근거)
F1. 동일 order_id에 첫 결제 요청은 504, 2초 뒤 재시도 요청은 200 응답을 받았다.
F2. 두 요청 모두 idempotency_key가 비어 있었다.
F3. PG 측에는 승인 2건이 존재한다.
F4. 주문 DB 측에는 완료 1건만 존재한다.
F5. 웹훅은 중복 수신되거나 순서가 뒤바뀔 수 있다.
F6. 네트워크 타임아웃(504)만으로는 승인 여부를 확정할 수 없다.
F7. 마이그레이션 대상 1,200,000행, 배치 5,000행, 배치당 15초, 작업자 최대 2개, 병목·오버헤드 없음 가정, 서비스 중단 허용 20분.
F8. 구버전·신버전 앱 72시간 공존 요구사항이 존재한다.

[2] 미확인 사항 목록(통과로 표현 금지)
U1. 새 버전 데이터 롤백 검증 미완료.
U2. 재시도 결제 검증 미완료.
U3. 취소 결제 검증 미완료.
U4. 실제 PG·DB 상태 미조회(제공된 합성 기록만 존재).
U5. 실제 환불 미수행.
U6. 실제 배포 미수행.
U7. 중복 승인 2건 중 어느 것이 실제 청구로 이어졌는지 미확인.
U8. 웹훅 중복/역전이 실제로 발생했는지 여부 미확인(가능성만 확인).

[3] 지원팀 전달용 안내문 초안
제목: 결제 중복 가능성 관련 지원팀 안내(사실/미확인 구분)
- 확정 사실: F1~F8을 그대로 인용. 각 항목에 (제공 자료) 출처 표기.
- 미확인 사항: U1~U8. '검증 중', '확인 필요'로만 표기.
- 지원팀 요청사항: 문의 접수 시 주문번호·결제시각 수집, 확정 사실만 안내, 미확인은 '확인 중'으로 안내, 기술팀/결제팀 에스컬레이션 경로 사용.
- 금지: 미검증 항목을 '정상', '통과', '문제 없음', '환불 완료'로 표현하지 말 것.

[4] 고객 안내 문구 초안
사용 가능 표현:
- "중복 결제 가능성이 확인되어 현재 조사 중입니다."
- "확인된 사실은 PG 승인 2건, 주문 DB 완료 1건입니다."
- "재시도·취소·롤백 검증은 아직 진행 중입니다."
- "확인되는 대로 순차 안내드리겠습니다."
금지 표현:
- "환불 완료", "문제 없음", "검증 통과", "정상 처리됨", "이중 청구 없음", "안전합니다".

[5] 상담 절차
1) 문의 접수 → 2) 주문번호·결제시각·결제수단 수집 → 3) 확정 사실(F1~F8)만 안내 → 4) 미확인 사항(U1~U8)은 '확인 중'으로 안내 → 5) 에스컬레이션: 결제 상태 이슈는 결제팀, 데이터/배포 이슈는 기술팀 → 6) 응대 스크립트 예시: "주문번호 확인되었습니다. 현재 PG 승인 2건과 주문 DB 완료 1건이 확인되어 조사 중이며, 재시도·취소 검증은 진행 중입니다. 확인되는 대로 안내드리겠습니다."

[6] 자체 점검
- 확정 사실(F)과 미확인(U)이 섹션으로 분리되어 혼합 없음.
- 모든 확정 사실에 제공 자료 출처 표기.
- 미검증을 통과로 표현한 문구 없음.
- 실제 PG·DB 조회, 환불, 배포, 롤백·재시도·취소 검증은 수행하지 않았음을 명시.

### Facts

```json
{
  "confirmed_pg_approvals": 2,
  "confirmed_db_completed": 1,
  "idempotency_key_missing_requests": 2,
  "first_request_status": 504,
  "retry_delay_seconds": 2,
  "retry_request_status": 200,
  "webhook_duplicate_possible": true,
  "webhook_order_reversal_possible": true,
  "timeout_alone_confirms_approval": false,
  "migration_rows": 1200000,
  "batch_size": 5000,
  "batch_seconds": 15,
  "max_workers": 2,
  "downtime_budget_seconds": 1200,
  "coexistence_hours": 72,
  "rollback_verification_done": false,
  "retry_payment_verification_done": false,
  "cancel_payment_verification_done": false,
  "actual_pg_db_queried": false,
  "actual_refund_performed": false,
  "actual_deploy_performed": false,
  "unverified_release_checks": 3,
  "offline_backfill_fits": true,
  "can_release": false
}
```

### Evidence

1. 제공 자료: 동일 order_id 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 부재 → F1, F2
2. 제공 자료: PG 승인 2건, 주문 DB 완료 1건 → F3, F4
3. 제공 자료: 웹훅 중복/순서 역전 가능, 네트워크 타임아웃만으로 승인 확정 불가 → F5, F6
4. 제공 자료: 마이그레이션 1,200,000행, 배치 5,000행, 배치 15초, 작업자 최대 2, 중단 허용 20분 → F7
5. 제공 자료: 구버전·신버전 72시간 공존 요구 → F8
6. 제공 자료: 롤백·재시도·취소 검증 미완료, 실제 PG·DB 조회/환불/배포 미수행 → U1~U6
7. 배치 수 계산: 1,200,000 / 5,000 = 240 배치
8. 1개 작업자 전체 시간: 240 × 15초 = 3,600초 = 60분
9. 2개 작업자 전체 시간: 240 / 2 = 120 배치 × 15초 = 1,800초 = 30분
10. 중단 허용 20분(1,200초) 대비 2개 작업자 1,800초 > 1,200초 → 오프라인 일괄은 허용 시간 초과, 온라인 준비+최종 전환 분리 필요
11. 미검증 항목은 '확인 중'으로만 표기하고 통과/정상 표현 금지
## payment-redesign/tech_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident/artifacts/payment-redesign/tech_design.json)

[설계 제안 — 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포는 수행하지 않았음. 아래 모든 수치·표·의사코드는 제공된 합성 기록과 가정에 기반한 설계 제안이다.]

■ 1. 원인 가설과 확인 증거
- H1 (idempotency_key 부재로 인한 재시도 중복 승인): 두 요청 모두 idempotency_key가 비어 있어 PG가 별개 승인으로 처리. 증거: PG 승인 로그 2건, 주문 DB 완료 1건, idempotency_key 공백. 가장 유력.
- H2 (504 타임아웃을 실패로 오판): 첫 요청 504 후 클라이언트가 실패로 간주하고 2초 뒤 재시도. 증거: 요청 타임스탬프 2초 간격, 첫 응답 504. 타임아웃은 승인 여부를 확정하지 못함.
- H3 (웹훅 중복·순서 역전): 웹훅이 중복 도착하거나 순서가 뒤바뀌면 상태 전이가 꼬임. 증거: 웹훅 중복/역전 가능성 명시. 단, 현재 관측된 PG 2건/DB 1건은 웹훅만으로 설명 안 됨.
- H4 (PG-DB 불일치): PG 승인 2건 vs DB 완료 1건. 증거: 두 저장소 카운트 차이. H1의 결과이거나 대사 부재의 결과.
- 확인 필요(미확인): PG 승인 로그의 원 요청 식별자, 504 발생 시점의 PG 실제 처리 여부, 웹훅 도착 순서 로그, 클라이언트 재시도 정책.

■ 2. 컴포넌트 책임·인터페이스·저장 데이터·제약 (설계 제안)
- PaymentCommand: 결제/취소 유스케이스 오케스트레이션. 메서드: execute(orderId, amount, idempotencyKey, op) -> Result. 책임: 멱등키 선점 → 게이트웨이 호출 → 상태 기록. 오류 경계: 게이트웨이 타임아웃은 '미확정(UNKNOWN)'으로 분류, 실패로 단정하지 않음.
- IdempotencyStore: 멱등키-결과 매핑. 메서드: tryAcquire(key, orderId) -> {ACQUIRED|IN_PROGRESS|DONE(result)}, complete(key, result), get(key). 저장: key, orderId, status, result, createdAt. 제약: idempotency_key UNIQUE. 경계: IN_PROGRESS 재진입 시 폴링/거절.
- GatewayPort: PG 추상화. 메서드: authorize(reqId, orderId, amount) -> {APPROVED|DECLINED|UNKNOWN}, cancel(...), queryStatus(reqId). 경계: UNKNOWN은 재조회로 확정, 재시도 시 동일 reqId 사용.
- WebhookHandler: 웹훅 수신. 메서드: handle(eventId, orderId, type, seq, occurredAt). 책임: eventId 멱등 처리, seq/occurredAt 기준 순서 역전 방어(늦은 이벤트는 상태 역행 금지). 저장: eventId UNIQUE, 처리 이력.
- ReconciliationJob: PG-DB 대사. 메서드: run(window) -> {matched, pgOnly, dbOnly}. 책임: PG 승인 vs DB 완료 비교, 불일치 리포트·보정 제안(자동 환불은 하지 않음).
- 저장 제약 제안: idempotency_key UNIQUE, webhook eventId UNIQUE, order 상태 전이 제약(order_id+상태 전이 유효성).

■ 3. 상태 전이표 (설계 제안)
결제: INITIATED→AUTHORIZING→(APPROVED→CAPTURED | DECLINED | UNKNOWN→(APPROVED|DECLINED)). UNKNOWN에서 재시도는 동일 idempotency_key/reqId로만 허용.
취소: CAPTURED→CANCEL_REQUESTED→(CANCELED | CANCEL_FAILED). CANCELED는 종결, 재취소 금지.
금지 전이: CANCELED→CAPTURED, CAPTURED→INITIATED, 늦은 웹훅으로 인한 역행.

■ 4. 경쟁 요청·순서 역전 타당성 검증 (설계 제안)
- 경쟁 요청: 동일 idempotency_key로 두 요청 동시 진입 시 IdempotencyStore.tryAcquire가 UNIQUE 제약으로 하나만 ACQUIRED, 나머지는 IN_PROGRESS/DONE 반환 → PG 승인 1건 보장. 현재 사고는 키가 비어 이 방어가 작동하지 않은 사례.
- 순서 역전: 웹훅 seq/occurredAt 비교로 늦은 이벤트가 이미 종결된 상태를 역행시키지 못하게 함. eventId UNIQUE로 중복 무시.

■ 5. 코드 디렉터리 구조 (설계 제안)
/payment/domain/{PaymentCommand, OrderState, StateTransition}
/payment/app/{IdempotencyStore, GatewayPort, WebhookHandler, ReconciliationJob}
/payment/infra/{PgGatewayAdapter, IdempotencyRepo, WebhookRepo}
/payment/api/{PaymentController, WebhookController}
/payment/jobs/{ReconciliationJobRunner}

■ 6. 핵심 의사코드 (설계 제안)
PaymentCommand.execute: key=idempotencyKey; if empty -> reject; r=store.tryAcquire(key,orderId); if r!=ACQUIRED return r.result; res=gateway.authorize(key,orderId,amount); if res==UNKNOWN -> res=gateway.queryStatus(key); store.complete(key,res); return res.
IdempotencyStore.tryAcquire: INSERT ... ON CONFLICT(key) DO NOTHING; if inserted -> ACQUIRED else SELECT status.
WebhookHandler.handle: if eventId exists -> ignore; if seq < lastSeq[orderId] -> ignore(역전); apply transition; persist.
ReconciliationJob.run: for each order in window: compare pg.status vs db.status; emit mismatch report.

■ 7. 마이그레이션 계산 (제공 가정 기반)
배치 수 = 1,200,000 / 5,000 = 240. 1작업자 = 240 × 15s = 3,600s = 60분. 2작업자 = 1,800s = 30분. 허용 중단 = 20분 = 1,200s. 30분 > 20분이므로 오프라인 일괄 전환은 허용 시간 내 불가(offline_backfill_fits=false). 온라인 준비(무중단 백필)와 최종 전환(짧은 컷오버)을 분리해야 함. 롤백·재시도 결제·취소 결제 검증 미완료로 can_release=false.

■ 8. 테스트 행렬 (설계 제안, 미실행)
- 중복: 동일 키 동시 2요청 → 승인 1건 기대.
- 순서 역전: 늦은 웹훅 → 상태 역행 없음 기대.
- 부분 실패: PG 승인 후 DB 기록 실패 → 대사로 불일치 탐지 기대.
각 케이스는 '미실행/미검증'으로 표기.

■ 9. 배포·롤백·중단 조건 (설계 제안)
배포: 온라인 백필 → 이중쓰기/이중읽기 → 컷오버. 롤백: 구버전 스키마 유지 기간 내 복귀. 중단 조건: 대사 불일치율 임계 초과, 승인 중복 재발, 컷오버 중 오류율 급증.

■ 10. 지원팀 전달 사실 vs 미확인
사실: PG 승인 2건, DB 완료 1건, 두 요청 idempotency_key 공백, 2초 간격, 첫 요청 504.
미확인: 실제 중복 청구 확정 여부, 환불 필요성, 웹훅 순서 로그, 롤백/재시도/취소 검증 결과.

■ 모순·미확인 사항
- 72시간 공존 요구와 20분 중단 예산 간 정합성: 공존 기간 중 이중쓰기 필요, 컷오버는 별도 창 필요.
- offline_backfill_fits=false이므로 '일괄 오프라인 전환' 전제와 20분 예산이 모순.
- 롤백·재시도·취소 검증 미완료로 릴리스 불가.

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
  "pg_approvals_observed": 2,
  "db_completed_observed": 1,
  "retry_interval_seconds": 2,
  "idempotency_key_present": false,
  "first_request_status": 504,
  "second_request_status": 200,
  "design_only": true,
  "actual_pg_db_queried": false,
  "actual_deploy_performed": false,
  "tests_executed": false
}
```

### Evidence

1. 배치 수: 1,200,000 / 5,000 = 240 (제공 가정).
2. 1작업자 시간: 240 × 15s = 3,600s = 60분 (제공 가정: 배치 15초, 병목·오버헤드 없음).
3. 2작업자 시간: 3,600s / 2 = 1,800s = 30분.
4. 중단 예산: 20분 = 1,200s. 30분 > 20분이므로 offline_backfill_fits=false.
5. 공존 기간: 요구사항의 72시간.
6. 미검증 릴리스 항목: 롤백, 재시도 결제, 취소 결제 = 3건 → unverified_release_checks=3, can_release=false.
7. 원인 증거: PG 승인 로그 2건, 주문 DB 완료 1건, 요청 타임스탬프 2초 간격, 두 요청 idempotency_key 공백, 첫 요청 504·재시도 200 (제공 합성 기록).
8. 설계 타당성: idempotency_key UNIQUE 제약으로 동일 키 경쟁 요청 중 1건만 승인, 웹훅 eventId UNIQUE 및 seq 비교로 중복·순서 역전 방어 (설계 제안).
9. 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포는 수행하지 않았으며 모든 산출물은 설계 제안임.