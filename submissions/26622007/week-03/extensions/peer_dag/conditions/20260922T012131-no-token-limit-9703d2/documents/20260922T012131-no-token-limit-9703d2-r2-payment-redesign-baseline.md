# payment-redesign / baseline / 2회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## payment-redesign.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-payment-redesign-baseline/artifacts/payment-redesign.json)

tech_design과 support_comm을 통합한 최종 payment_redesign_report다. 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포는 수행하지 않았고 모든 설계·절차는 제안 또는 미검증으로 표시한다.

[1] 원인 가설과 확인할 증거
- H1(멱등성 키 부재, 최우선): 두 요청 모두 idempotency_key가 비어 있어 PG가 별개 승인으로 처리 → PG 승인 2건 vs DB 완료 1건과 부합. 확인 증거: PG 승인 로그의 승인 시각·금액·pg_txn_id, 두 요청의 빈 키 값.
- H2(504 타임아웃 후 확정 불가): 첫 요청 504는 승인 여부 미확정. 확인 증거: 504 응답 시각과 PG 승인 시각 비교, GatewayPort가 UNKNOWN을 반환하는지.
- H3(웹훅 중복/순서 역전): 중복·역전 시 상태 전이 꼬임. 확인 증거: 웹훅 event_id 중복 여부, 도착 순서 vs PG 이벤트 발생 시각.
- 미확인: PG 승인 2건의 실제 시각·금액, DB 완료 1건의 트랜잭션 id, 웹훅 로그 원본은 제공되지 않아 위 증거는 '확인 필요'로 유지.

[2] 상태 전이표(제안)
INITIATED → PENDING(PG 호출 전) → APPROVED | DECLINED | UNKNOWN. UNKNOWN → (재시도) APPROVED/DECLINED 또는 ReconciliationJob 확정. APPROVED → CAPTURED. APPROVED/CAPTURED → CANCELED. 경쟁 요청: 동일 (order_id, idempotency_key)는 고유 제약으로 1건만 PENDING 진입. 순서 역전: 늦게 도착한 과거 이벤트는 상태를 되돌리지 않음.

[3] 코드 구조·의사코드(제안, 미구현)
- 디렉터리: src/payment/{PaymentCommand, IdempotencyStore, GatewayPort, WebhookHandler, ReconciliationJob}.
- PaymentCommand.handle: acquire(order_id, key) → 기존 state 있으면 반환; 없으면 PENDING 저장 → GatewayPort.approve → APPROVED/UNKNOWN 저장.
- IdempotencyStore.acquire: INSERT ... ON CONFLICT DO NOTHING 후 SELECT state. 저장: order_id, idempotency_key, state, pg_txn_id, updated_at. 고유 제약: (order_id, idempotency_key).
- GatewayPort: approve/cancel, 타임아웃 시 UNKNOWN 반환(성공/실패 단정 금지), 재시도는 멱등 키 전달.
- WebhookHandler.onEvent: event_id 중복이면 무시; event_time < last_event_time이면 무시; 아니면 전이.
- ReconciliationJob.run: PG 승인 목록 조회 → DB 완료 목록 대사 → 불일치 큐 적재.

[4] 데이터 전환 순서
- 배치 수 240, 1작업자 3,600초(60분), 2작업자 1,800초(30분), 중단 허용 1,200초(20분). 1,800 > 1,200이므로 오프라인 일괄 처리는 허용 시간 내 불가(offline_backfill_fits=false).
- 온라인 준비(72시간 공존): 신·구 앱이 동시에 쓰는 이중 쓰기/읽기 호환 스키마 유지, 백필은 온라인으로 분할 진행.
- 최종 전환: 별도 짧은 중단 창에서 스키마 전환·검증, 공존 종료.

[5] 테스트 행렬(제안, 미실행)
- 중복 요청: 동일 order_id+key 2회 → 승인 1건 기대(검증: PG 호출 횟수).
- 순서 역전 웹훅: 승인 후 취소 이벤트가 먼저 도착 → 최종 APPROVED 유지 기대.
- 부분 실패: PG 승인 후 DB 미기록 → ReconciliationJob 불일치 탐지 기대.
- 타임아웃 UNKNOWN: UNKNOWN 반환 → 재시도 시 멱등 키로 중복 방지 기대.

[6] 배포·롤백·중단 조건
- can_release=false: 롤백·재시도 결제·취소 결제 검증 3건 미완료(unverified_release_checks=3).
- 중단 트리거: 대사 불일치 임계 초과, UNKNOWN 누적, 웹훅 중복률 급증.
- 롤백 절차: 신버전 비활성화 → 구버전 스키마 복귀 → 이중 쓰기 중단 → 대사 재실행.

[7] 지원팀 전달 사실·미확인
- 사실: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 idempotency_key 비어 있음, PG 승인 2건, DB 완료 1건, 웹훅 중복/역전 가능, 타임아웃만으로 승인 확정 불가.
- 미확인(U1~U8): PG/DB 실제 조회 미수행, 롤백·재시도·취소 결제 검증 미완료, 중복 청구 실제 발생·금액·영향 고객 수 미확인, 환불 필요성·가능 여부 미확정. 고객 안내는 '가능성/확인 중/미확정' 표현만 사용하고 원인 단정·환불 확정·검증 완료 표현 배제.

[8] 모순·정합성 점검
- 72시간 공존 요구와 20분 중단 예산: 공존 기간에 온라인 백필을 분산하면 최종 전환 중단을 20분 내로 줄일 수 있음 → 일괄 오프라인 불가(1800>1200)와 최종 전환 분리 필요성이 일관됨.
- can_release=false와 배포 계획: 검증 3건 미완료 상태에서 배포 계획은 '조건부·미승인'으로만 기술, 확정 배포 표현 없음.
- 고객 안내 '확인 중' 표현과 미검증 항목 유지: U1~U8을 통과로 바꾸지 않음 → 일관.
- 두 산출물 간 수치 일치: backfill_batches=240, serial=3600, two_worker=1800, downtime_budget=1200, coexistence_hours=72, unverified_release_checks=3, offline_backfill_fits=false, can_release=false 모두 동일. 불일치 없음.

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
  "first_request_status": 504,
  "retry_delay_seconds": 2,
  "retry_status": 200,
  "idempotency_key_present": false,
  "confirmed_duplicate_approvals": 2,
  "confirmed_db_completed": 1,
  "webhook_duplicate_or_reorder_possible": true,
  "timeout_alone_insufficient": true,
  "pg_query_performed": false,
  "db_query_performed": false,
  "rollback_verified": false,
  "retry_payment_verified": false,
  "cancel_payment_verified": false,
  "refund_confirmed": false,
  "root_cause_confirmed": false,
  "customer_notice_uses_definitive_cause": false,
  "customer_notice_confirms_refund": false,
  "hold_until_unverified_resolved": true,
  "escalation_to_payment_defined": true,
  "escalation_to_tech_defined": true,
  "escalation_to_ops_defined": true
}
```

### Evidence

**1.** 배치 수: 1,200,000행 / 5,000행 = 240 (제공 자료의 마이그레이션 대상 행 수·배치 크기).

**2.** 1작업자: 240 × 15초 = 3,600초 = 60분 (배치당 15초, 작업자 1개).

**3.** 2작업자: 240/2 = 120 × 15초 = 1,800초 = 30분 (최대 작업자 2개, 병목·오버헤드 없음 가정).

**4.** 중단 허용: 20분 × 60 = 1,200초. 1,800 > 1,200 → offline_backfill_fits=false.

**5.** 공존 기간: 제공 자료의 구버전·신버전 앱 72시간 공존 요구 → coexistence_hours=72.

**6.** 미검증 릴리스 검증: 롤백·재시도 결제·취소 결제 검증 모두 미완료 → unverified_release_checks=3, can_release=false.

**7.** 원인 가설 근거: 같은 order_id에 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 비어 있음, PG 승인 2건, DB 완료 1건(제공 합성 기록).

**8.** 웹훅 조건: 웹훅은 중복·순서 역전 가능, 네트워크 타임아웃만으로 승인 확정 불가(제공 자료).

**9.** 고객 안내 문구는 '가능성/확인 중/미확정' 표현만 사용하고 원인 단정·환불 확정·검증 완료 표현을 배제함(자체 점검 결과).

**10.** 상담 절차: 1차 접수→정보 수집(order_id, 결제 시각, 승인 건수)→에스컬레이션(결제/기술/운영)→보류 처리→회신·종결 순으로 구성.

**11.** 팀 간 계획: 지원팀(1차 응대), 결제 담당(대사), 기술 담당(원인 분석), 운영 담당(공지·일정)으로 담당·채널·시점을 명시.

**12.** 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포, 롤백·재시도·취소 검증은 수행하지 않았으며 설계·절차는 제안 또는 미검증으로 표시.

## payment-redesign/integrate.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-payment-redesign-baseline/artifacts/payment-redesign/integrate.json)

tech_design과 support_comm을 통합한 최종 payment_redesign_report다. 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포는 수행하지 않았고 모든 설계·절차는 제안 또는 미검증으로 표시한다.

[1] 원인 가설과 확인할 증거
- H1(멱등성 키 부재, 최우선): 두 요청 모두 idempotency_key가 비어 있어 PG가 별개 승인으로 처리 → PG 승인 2건 vs DB 완료 1건과 부합. 확인 증거: PG 승인 로그의 승인 시각·금액·pg_txn_id, 두 요청의 빈 키 값.
- H2(504 타임아웃 후 확정 불가): 첫 요청 504는 승인 여부 미확정. 확인 증거: 504 응답 시각과 PG 승인 시각 비교, GatewayPort가 UNKNOWN을 반환하는지.
- H3(웹훅 중복/순서 역전): 중복·역전 시 상태 전이 꼬임. 확인 증거: 웹훅 event_id 중복 여부, 도착 순서 vs PG 이벤트 발생 시각.
- 미확인: PG 승인 2건의 실제 시각·금액, DB 완료 1건의 트랜잭션 id, 웹훅 로그 원본은 제공되지 않아 위 증거는 '확인 필요'로 유지.

[2] 상태 전이표(제안)
INITIATED → PENDING(PG 호출 전) → APPROVED | DECLINED | UNKNOWN. UNKNOWN → (재시도) APPROVED/DECLINED 또는 ReconciliationJob 확정. APPROVED → CAPTURED. APPROVED/CAPTURED → CANCELED. 경쟁 요청: 동일 (order_id, idempotency_key)는 고유 제약으로 1건만 PENDING 진입. 순서 역전: 늦게 도착한 과거 이벤트는 상태를 되돌리지 않음.

[3] 코드 구조·의사코드(제안, 미구현)
- 디렉터리: src/payment/{PaymentCommand, IdempotencyStore, GatewayPort, WebhookHandler, ReconciliationJob}.
- PaymentCommand.handle: acquire(order_id, key) → 기존 state 있으면 반환; 없으면 PENDING 저장 → GatewayPort.approve → APPROVED/UNKNOWN 저장.
- IdempotencyStore.acquire: INSERT ... ON CONFLICT DO NOTHING 후 SELECT state. 저장: order_id, idempotency_key, state, pg_txn_id, updated_at. 고유 제약: (order_id, idempotency_key).
- GatewayPort: approve/cancel, 타임아웃 시 UNKNOWN 반환(성공/실패 단정 금지), 재시도는 멱등 키 전달.
- WebhookHandler.onEvent: event_id 중복이면 무시; event_time < last_event_time이면 무시; 아니면 전이.
- ReconciliationJob.run: PG 승인 목록 조회 → DB 완료 목록 대사 → 불일치 큐 적재.

[4] 데이터 전환 순서
- 배치 수 240, 1작업자 3,600초(60분), 2작업자 1,800초(30분), 중단 허용 1,200초(20분). 1,800 > 1,200이므로 오프라인 일괄 처리는 허용 시간 내 불가(offline_backfill_fits=false).
- 온라인 준비(72시간 공존): 신·구 앱이 동시에 쓰는 이중 쓰기/읽기 호환 스키마 유지, 백필은 온라인으로 분할 진행.
- 최종 전환: 별도 짧은 중단 창에서 스키마 전환·검증, 공존 종료.

[5] 테스트 행렬(제안, 미실행)
- 중복 요청: 동일 order_id+key 2회 → 승인 1건 기대(검증: PG 호출 횟수).
- 순서 역전 웹훅: 승인 후 취소 이벤트가 먼저 도착 → 최종 APPROVED 유지 기대.
- 부분 실패: PG 승인 후 DB 미기록 → ReconciliationJob 불일치 탐지 기대.
- 타임아웃 UNKNOWN: UNKNOWN 반환 → 재시도 시 멱등 키로 중복 방지 기대.

[6] 배포·롤백·중단 조건
- can_release=false: 롤백·재시도 결제·취소 결제 검증 3건 미완료(unverified_release_checks=3).
- 중단 트리거: 대사 불일치 임계 초과, UNKNOWN 누적, 웹훅 중복률 급증.
- 롤백 절차: 신버전 비활성화 → 구버전 스키마 복귀 → 이중 쓰기 중단 → 대사 재실행.

[7] 지원팀 전달 사실·미확인
- 사실: 첫 요청 504, 2초 뒤 재시도 200, 두 요청 idempotency_key 비어 있음, PG 승인 2건, DB 완료 1건, 웹훅 중복/역전 가능, 타임아웃만으로 승인 확정 불가.
- 미확인(U1~U8): PG/DB 실제 조회 미수행, 롤백·재시도·취소 결제 검증 미완료, 중복 청구 실제 발생·금액·영향 고객 수 미확인, 환불 필요성·가능 여부 미확정. 고객 안내는 '가능성/확인 중/미확정' 표현만 사용하고 원인 단정·환불 확정·검증 완료 표현 배제.

[8] 모순·정합성 점검
- 72시간 공존 요구와 20분 중단 예산: 공존 기간에 온라인 백필을 분산하면 최종 전환 중단을 20분 내로 줄일 수 있음 → 일괄 오프라인 불가(1800>1200)와 최종 전환 분리 필요성이 일관됨.
- can_release=false와 배포 계획: 검증 3건 미완료 상태에서 배포 계획은 '조건부·미승인'으로만 기술, 확정 배포 표현 없음.
- 고객 안내 '확인 중' 표현과 미검증 항목 유지: U1~U8을 통과로 바꾸지 않음 → 일관.
- 두 산출물 간 수치 일치: backfill_batches=240, serial=3600, two_worker=1800, downtime_budget=1200, coexistence_hours=72, unverified_release_checks=3, offline_backfill_fits=false, can_release=false 모두 동일. 불일치 없음.

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

**1.** 배치 수: 1,200,000행 / 5,000행 = 240 (제공 자료의 마이그레이션 대상 행 수·배치 크기).

**2.** 1작업자: 240 × 15초 = 3,600초 = 60분 (배치당 15초, 작업자 1개).

**3.** 2작업자: 240/2 = 120 × 15초 = 1,800초 = 30분 (최대 작업자 2개, 병목·오버헤드 없음 가정).

**4.** 중단 허용: 20분 × 60 = 1,200초. 1,800 > 1,200 → offline_backfill_fits=false.

**5.** 공존 기간: 제공 자료의 구버전·신버전 앱 72시간 공존 요구 → coexistence_hours=72.

**6.** 미검증 릴리스 검증: 롤백·재시도 결제·취소 결제 검증 모두 미완료 → unverified_release_checks=3, can_release=false.

**7.** 원인 가설 근거: 같은 order_id에 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 비어 있음, PG 승인 2건, DB 완료 1건(제공 합성 기록).

**8.** 웹훅 조건: 웹훅은 중복·순서 역전 가능, 네트워크 타임아웃만으로 승인 확정 불가(제공 자료).

**9.** 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포는 수행하지 않았으며 설계·절차는 제안 또는 미검증으로 표시.

## payment-redesign/support_comm.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-payment-redesign-baseline/artifacts/payment-redesign/support_comm.json)

지원팀 전달용 커뮤니케이션 산출물을 작성했다. (1) 확정 사실과 미확인 사항을 분리한 사실 목록, (2) 원인 단정·환불 확정·검증 완료 표현을 배제한 고객 안내 문구, (3) 1차 접수→정보 확인→에스컬레이션→보류 처리의 상담·에스컬레이션 절차, (4) 지원팀/결제·기술/운영 담당의 일정·전달 채널 계획을 텍스트로 제시한다. 모든 수치는 source에 주어진 값만 사용했고, 실제 PG/DB 조회·환불·배포는 수행하지 않았으므로 관련 항목은 '미검증'으로 유지했다.

[1] 지원팀 전달용 사실 목록
■ 확정 사실(제공된 합성 기록 기준)
- F1. 동일 order_id에 첫 결제 요청이 504(게이트웨이 타임아웃) 응답을 받았다.
- F2. 2초 뒤 동일 요청을 재시도했고 이 재시도는 200 응답을 받았다.
- F3. 두 요청(최초·재시도) 모두 idempotency_key가 비어 있었다.
- F4. PG 측에는 승인 2건이 존재한다.
- F5. 주문 DB에는 완료 1건만 존재한다.
- F6. 웹훅은 중복 수신되거나 순서가 뒤바뀔 수 있다.
- F7. 네트워크 타임아웃(504)만으로는 승인 여부를 확정할 수 없다.
- F8. 마이그레이션 대상 1,200,000행, 배치 5,000행, 배치당 15초, 작업자 최대 2개, 중단 허용 20분(설계 제약).
■ 미확인 사항(검증되지 않음 — '해결됨'으로 표현 금지)
- U1. 실제 PG 조회 미수행: PG 승인 2건의 상세(승인 시각, 금액, 상태) 미확인.
- U2. 실제 주문 DB 조회 미수행: DB 완료 1건의 상태·금액·시각 미확인.
- U3. 롤백 검증 미완료.
- U4. 재시도 결제 검증 미완료.
- U5. 취소 결제 검증 미완료.
- U6. unverified_release_checks 항목 전체 미완료.
- U7. 중복 청구의 실제 발생 여부·금액·영향 고객 수 미확인(합성 기록 기반 가설 단계).
- U8. 환불 필요성·환불 가능 여부 미확정.

[2] 고객 안내 문구(초안, 원인 단정·환불 확정·검증 완료 표현 배제)
"안녕하세요, 고객님. 결제 처리 과정에서 일시적인 통신 지연이 확인되어, 일부 결제 건에서 중복 청구 가능성이 있는지 확인 중에 있습니다. 현재까지 확인된 내용은 다음과 같습니다. · 결제 요청 중 일부가 지연 응답을 받은 이력이 있습니다. · 동일 주문에 대해 결제가 2건 승인된 것으로 보이는 사례가 확인되었습니다. · 다만 실제 청구 내역과 처리 결과는 아직 확인 중이며, 원인이 확정된 것은 아닙니다. 고객님께서 중복 결제가 의심되는 경우, 아래 정보를 포함해 문의를 접수해 주시면 확인 후 순차적으로 안내드리겠습니다. · 주문번호(order_id) · 결제 일시 · 결제 수단 · 승인 건수(카드사/앱 알림 기준) 접수된 문의는 확인 절차를 거쳐 처리 일정과 함께 개별 안내드립니다. 불편을 드려 죄송합니다."
※ 금지 표현 점검: '원인은 ~입니다', '환불해 드립니다', '검증이 완료되었습니다' 등 단정 표현 미사용. '가능성', '확인 중', '미확정' 표현만 사용.

[3] 상담·에스컬레이션 절차
1단계(1차 접수 스크립트): 인사 → 중복 청구 가능성 확인 중임을 안내 → 고객 불안 완화 → 필요 정보 요청 → 접수 번호 부여 → 처리 일정 안내(개별 안내 예정).
2단계(확인 필요 정보 수집): order_id, 결제 시각(최초·재시도), 결제 수단, 승인 건수(고객 확인 가능 범위), 문의 채널·연락처.
3단계(에스컬레이션 조건): ① PG 승인 2건·DB 완료 1건 불일치 의심 시 → 결제 담당. ② 504/재시도·idempotency_key 부재 관련 기술 확인 필요 시 → 기술 담당. ③ 다수 고객 동일 패턴 발생 시 → 운영 담당(공지·일정 조율).
4단계(보류 처리): U1~U8 미확인 항목이 해소되지 않은 상태에서 환불·취소·원인 단정 안내 금지. 확인 전에는 '확인 중' 상태로 접수 유지, 처리 기한은 결제/기술 담당 회신 후 확정.
5단계(회신·종결): 담당 부서 확인 결과 수신 후 고객에게 사실 범위 내에서 안내, 미확인 항목은 '추가 확인 필요'로 명시.

[4] 팀 간 일정·담당·전달 계획(표)
| 구분 | 담당 | 역할 | 전달 채널 | 시점 |
|---|---|---|---|---|
| 1차 응대 | 지원팀 | 고객 문의 접수, 정보 수집, 스크립트 안내 | 상담 채널/티켓 | 즉시~상시 |
| 원인·대사 확인 | 결제 담당 | PG 승인 2건·DB 완료 1건 대사, idempotency_key 부재 확인 | 내부 이슈 트래커 | 접수 후 1영업일 내 1차 회신 |
| 기술 확인 | 기술 담당 | 504/재시도 경로, 웹훅 중복·순서 역전 영향 분석 | 내부 이슈 트래커 | 접수 후 1영업일 내 1차 회신 |
| 공지·일정 조율 | 운영 담당 | 고객 공지 문구 확정, 처리 일정 조율 | 공지 채널/운영 회의 | 결제·기술 1차 회신 후 |
| 통합 보고 | Worker C(운영·커뮤니케이션) | 사실/미확인 구분 유지, 지원팀 전달 | 문서/메신저 | 각 단계 완료 시 |
※ 일정은 담당 부서 회신에 따라 조정되며, 미확인 항목 해소 전 확정 일정 공표 금지.

[5] 자체 점검
- 확정(F1~F8)과 미확인(U1~U8)이 분리되어 있음.
- 고객 안내에 원인 단정·환불 확정·검증 완료 표현 없음.
- 미검증 항목(U1~U8)을 '해결됨'으로 표현하지 않음.
- 실제 PG/DB 조회·환불·배포는 수행하지 않았으며, 해당 검증은 미완료로 명시함.

### Facts

```json
{
  "confirmed_duplicate_approvals": 2,
  "confirmed_db_completed": 1,
  "idempotency_key_present": false,
  "first_request_status": 504,
  "retry_delay_seconds": 2,
  "retry_status": 200,
  "webhook_duplicate_or_reorder_possible": true,
  "timeout_alone_insufficient": true,
  "pg_query_performed": false,
  "db_query_performed": false,
  "rollback_verified": false,
  "retry_payment_verified": false,
  "cancel_payment_verified": false,
  "unverified_release_checks": true,
  "refund_confirmed": false,
  "root_cause_confirmed": false,
  "backfill_batches": 240,
  "serial_backfill_seconds": 3600,
  "two_worker_backfill_seconds": 1800,
  "downtime_budget_seconds": 1200,
  "coexistence_hours": 72,
  "offline_backfill_fits": false,
  "can_release": false,
  "customer_notice_uses_definitive_cause": false,
  "customer_notice_confirms_refund": false,
  "escalation_to_payment_defined": true,
  "escalation_to_tech_defined": true,
  "escalation_to_ops_defined": true,
  "hold_until_unverified_resolved": true
}
```

### Evidence

**1.** source: '첫 결제 요청이 504, 2초 뒤 재시도는 200이고 두 요청 모두 idempotency_key가 비어 있다' → F1,F2,F3, facts.first_request_status=504, retry_delay_seconds=2, retry_status=200, idempotency_key_present=false

**2.** source: 'PG에는 승인 2건, 주문 DB에는 완료 1건이 있다' → F4,F5, facts.confirmed_duplicate_approvals=2, confirmed_db_completed=1

**3.** source: '웹훅은 중복되거나 순서가 뒤바뀔 수 있고 네트워크 타임아웃만으로 승인 여부를 확정할 수 없다' → F6,F7, facts.webhook_duplicate_or_reorder_possible=true, timeout_alone_insufficient=true

**4.** source: '실제 PG·DB 조회나 환불·배포는 수행하지 않으며 미검증을 통과로 바꾸지 말라' 및 '롤백·재시도 결제·취소 결제 검증은 모두 미완료' → U1~U8, facts.pg_query_performed=false, db_query_performed=false, rollback_verified=false, retry_payment_verified=false, cancel_payment_verified=false, unverified_release_checks=true, refund_confirmed=false, root_cause_confirmed=false

**5.** 계산: backfill_batches = 1,200,000 / 5,000 = 240

**6.** 계산: serial_backfill_seconds = 240 × 15 = 3,600초

**7.** 계산: two_worker_backfill_seconds = 240 / 2 × 15 = 1,800초

**8.** 계산: downtime_budget_seconds = 20분 × 60 = 1,200초

**9.** 비교: 1,800초(2작업자) > 1,200초(허용) → offline_backfill_fits=false, can_release=false

**10.** source: '구버전 앱과 신버전 앱의 72시간 공존' → coexistence_hours=72

**11.** 고객 안내 문구는 '가능성/확인 중/미확정' 표현만 사용하고 원인 단정·환불 확정·검증 완료 표현을 배제함(자체 점검 결과)

**12.** 상담 절차는 1차 접수→정보 수집(order_id, 결제 시각, 승인 건수)→에스컬레이션 조건(결제/기술/운영)→보류 처리→회신·종결 순으로 구성

**13.** 팀 간 계획은 지원팀(1차 응대), 결제 담당(대사), 기술 담당(원인 분석), 운영 담당(공지·일정)으로 담당·채널·시점을 표로 명시

**14.** 실제 PG/DB 조회, 환불, 배포, 롤백·재시도·취소 검증은 수행하지 않았으므로 해당 항목을 미검증으로 유지

## payment-redesign/tech_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-payment-redesign-baseline/artifacts/payment-redesign/tech_design.json)

결제 중복 사고를 재현 가능한 설계 문제로 분석한 결과를 정리한다. 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포는 수행하지 않았고 모두 설계 제안 또는 미검증으로 표시한다.

[1] 원인 가설과 확인 증거
- 가설 H1(멱등성 키 부재): 두 요청 모두 idempotency_key가 비어 있어 PG가 별개 승인으로 처리. 확인 증거: PG 승인 2건 vs 주문 DB 완료 1건, 두 요청의 빈 idempotency_key. 이 가설이 관측치와 가장 부합.
- 가설 H2(504 타임아웃 후 확정 불가): 첫 요청 504는 승인 여부 미확정 상태. 확인 증거: PG 승인 로그의 첫 요청 승인 시각과 504 응답 시각 비교, GatewayPort가 UNKNOWN을 반환하는지 여부.
- 가설 H3(웹훅 중복/순서 역전): 웹훅이 중복 전달되거나 순서가 뒤바뀌면 상태 전이가 꼬임. 확인 증거: 웹훅 로그의 event_id 중복 여부와 도착 순서, PG 이벤트 발생 시각 대비 도착 시각.
- 미확인: PG 승인 2건의 실제 승인 시각·금액, DB 완료 1건의 트랜잭션 id, 웹훅 로그 원본은 제공되지 않아 위 증거는 '확인 필요'로 남긴다.

[2] 컴포넌트 설계(제안)
- PaymentCommand: 요청 검증(금액·통화·order_id), IdempotencyStore.acquire로 중복 차단, 상태 전이 트리거. 인터페이스: handle(order_id, idempotency_key, amount) -> Result. 저장: order_id, idempotency_key, state, pg_txn_id, updated_at.
- IdempotencyStore: (order_id, idempotency_key) 고유 제약. acquire는 INSERT ... ON CONFLICT DO NOTHING 후 기존 레코드 반환. 상태: INITIATED/PENDING/APPROVED/DECLINED/UNKNOWN/CAPTURED/CANCELED. 오류 경계: 고유 제약 위반은 중복으로 간주해 기존 결과 반환.
- GatewayPort: approve/cancel 호출. 타임아웃 시 UNKNOWN 반환(성공/실패로 단정 금지). 재시도는 멱등 키 전달. 오류 경계: 네트워크 오류와 PG 거절을 구분.
- WebhookHandler: event_id 기준 중복 제거(고유 제약), 이벤트 발생 시각 기준 순서 역전 처리(늦게 도착한 과거 이벤트는 상태를 되돌리지 않음).
- ReconciliationJob: PG 승인 목록과 DB 완료 목록을 주기적으로 대사, 불일치(승인 2 vs 완료 1) 탐지 시 자동 취소 또는 수동 검토 큐로 전환.

[3] 상태 전이표(제안)
INITIATED -> PENDING (PG 호출 전) -> APPROVED | DECLINED | UNKNOWN. UNKNOWN -> (재시도) APPROVED/DECLINED, 또는 ReconciliationJob이 확정. APPROVED -> CAPTURED. APPROVED/CAPTURED -> CANCELED. 경쟁 요청: 동일 (order_id, idempotency_key)는 고유 제약으로 1건만 PENDING 진입. 순서 역전: 늦은 과거 이벤트는 무시.

[4] 코드 디렉터리와 핵심 의사코드(제안, 미구현)
- src/payment/PaymentCommand, src/payment/IdempotencyStore, src/payment/GatewayPort, src/payment/WebhookHandler, src/payment/ReconciliationJob.
- PaymentCommand.handle: acquire(order_id, key) -> 기존 state 있으면 반환; 없으면 PENDING 저장 -> GatewayPort.approve -> APPROVED/UNKNOWN 저장.
- IdempotencyStore.acquire: INSERT ON CONFLICT DO NOTHING; SELECT state.
- WebhookHandler.onEvent: event_id 중복이면 무시; event_time < last_event_time이면 무시; 아니면 상태 전이.
- ReconciliationJob.run: PG 승인 목록 조회 -> DB 완료 목록과 대사 -> 불일치 큐 적재.

[5] 테스트 행렬(제안, 미실행)
- 중복 요청: 동일 order_id+key 2회 -> 승인 1건 기대, 검증: PG 호출 횟수.
- 순서 역전 웹훅: 승인 후 취소 이벤트가 먼저 도착 -> 최종 상태 APPROVED 유지 기대.
- 부분 실패: PG 승인 후 DB 미기록 -> ReconciliationJob이 불일치 탐지 기대.
- 타임아웃 UNKNOWN: GatewayPort가 UNKNOWN 반환 -> 재시도 시 멱등 키로 중복 방지 기대.

[6] 마이그레이션 계산
- 배치 수: 1,200,000 / 5,000 = 240.
- 1개 작업자: 240 * 15 = 3,600초 = 60분.
- 2개 작업자: 120 * 15 = 1,800초 = 30분.
- 중단 허용: 20분 = 1,200초. 1,800초 > 1,200초이므로 오프라인 일괄 처리는 허용 시간 내 불가(offline_backfill_fits=false).
- 온라인 준비(공존 72시간)와 최종 전환을 분리: 공존 기간 동안 신·구 앱이 동시에 쓰는 스키마로 이중 쓰기/읽기 호환을 유지하고, 최종 전환은 별도 짧은 중단 창에서 수행.

[7] 배포·롤백·중단 조건
- can_release=false: 롤백 검증, 재시도 결제 검증, 취소 결제 검증 3건이 미완료.
- 중단 트리거: 대사 불일치 건수 임계 초과, UNKNOWN 상태 누적, 웹훅 중복률 급증.
- 롤백 절차: 신버전 비활성화, 구버전 스키마로 복귀, 이중 쓰기 중단, 대사 재실행.

[8] 지원팀 전달 사실/미확인
- 사실: PG 승인 2건, DB 완료 1건, 두 요청 idempotency_key 비어 있음, 첫 요청 504·2초 뒤 재시도 200.
- 미확인: 실제 승인 시각·금액, 웹훅 로그 원본, 롤백·재시도·취소 결제 검증 결과.

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

**1.** 배치 수: 1,200,000행 / 5,000행 = 240배치 (제공 자료의 마이그레이션 대상 행 수와 배치 크기).

**2.** 1개 작업자 시간: 240배치 * 15초 = 3,600초 = 60분 (제공 자료의 배치당 15초, 작업자 1개 가정).

**3.** 2개 작업자 시간: 240/2 = 120배치 * 15초 = 1,800초 = 30분 (제공 자료의 최대 작업자 2개, 병목·오버헤드 없음 가정).

**4.** 중단 허용 시간: 20분 = 1,200초 (제공 자료). 1,800초 > 1,200초이므로 오프라인 일괄 처리는 허용 시간 내 불가 -> offline_backfill_fits=false.

**5.** 공존 기간: 제공 자료의 구버전·신버전 앱 72시간 공존 요구사항 -> coexistence_hours=72.

**6.** 미검증 릴리스 검증: 제공 자료에서 롤백·재시도 결제·취소 결제 검증이 모두 미완료라고 명시 -> unverified_release_checks=3, can_release=false.

**7.** 원인 가설 근거: 제공 자료의 '같은 order_id에 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 비어 있음, PG 승인 2건, DB 완료 1건'이라는 합성 기록.

**8.** 웹훅 중복/순서 역전: 제공 자료의 '웹훅은 중복되거나 순서가 뒤바뀔 수 있고 네트워크 타임아웃만으로 승인 여부를 확정할 수 없다'는 조건.

**9.** 설계·상태 전이표·의사코드·테스트 행렬은 본 응답에서 텍스트 제안으로만 작성했으며 실제 구현·테스트·PG/DB 조회·배포는 수행하지 않음(미검증).
