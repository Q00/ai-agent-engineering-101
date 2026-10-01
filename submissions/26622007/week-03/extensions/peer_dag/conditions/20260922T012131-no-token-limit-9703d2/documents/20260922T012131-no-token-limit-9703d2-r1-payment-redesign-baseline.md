# payment-redesign / baseline / 1회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## payment-redesign.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-payment-redesign-baseline/artifacts/payment-redesign.json)

제공 자료만으로 결제 중복 사고를 재현 가능한 설계 문제로 분석했다. 실제 PG·DB 조회, 환불, 배포, 코드 구현, 테스트 실행은 하지 않았고 모두 설계 제안 또는 미검증으로 표시한다.

[1] 원인 가설과 확인 필요 증거
- H1(가장 유력): 두 요청 모두 idempotency_key가 비어 있어 서버가 재시도를 신규 결제로 처리. 첫 요청은 PG 승인 후 응답 전 504(타임아웃)로 클라이언트가 실패로 오인, 2초 뒤 재시도가 두 번째 승인을 생성. PG 승인 2건 vs DB 완료 1건과 정합.
- H2: 웹훅 중복/순서 역전으로 늦게 도착한 첫 승인 웹훅이 두 번째 승인 상태를 덮어쓰거나, 취소 이벤트가 승인 이벤트보다 먼저 도착해 상태가 역전.
- H3: DB 트랜잭션은 1건만 커밋(두 번째 요청이 유니크 제약/경쟁에서 실패)했으나 PG에는 2건이 승인되어 대사 불일치 발생.
- 확인 필요 증거: PG 승인 2건의 approval_id/승인시각/금액, 각 요청의 idempotency_key 헤더 실제 값, 게이트웨이 요청 로그의 correlation id, DB 결제 레코드의 상태·버전·생성시각, 웹훅 수신 로그의 event_id·서명·도착순서, 클라이언트 재시도 정책(백오프/타임아웃 값).
- 대사 관점: PG 승인 2건 - DB 완료 1건 = 미대사 1건. 이 1건은 (a) PG 승인 후 DB 미기록(고아 승인) 또는 (b) DB 완료 후 PG 미승인(고아 완료) 중 어느 쪽인지 확정 불가. 네트워크 타임아웃만으로 승인 여부를 확정할 수 없으므로 PG 조회(단건 조회 API)로 확정해야 하며, 현재는 미확인.

[2] 컴포넌트 설계(책임·인터페이스·저장·제약·오류경계)
- PaymentCommand: 결제/취소 명령의 진입점. 책임은 명령 검증, idempotency_key 필수화(비어 있으면 거부), 상태 전이 오케스트레이션. 인터페이스: execute(orderId, amount, idemKey, op) -> Result. 저장: 없음(오케스트레이터). 오류경계: idemKey 누락 시 400 즉시 거부, 게이트웨이 타임아웃 시 '미확정' 상태로 기록하고 재시도는 동일 idemKey로만 허용.
- IdempotencyStore: idemKey -> 결과 매핑 저장. 저장 데이터: idemKey(PK), orderId, op, requestHash, status(IN_PROGRESS/SUCCEEDED/FAILED), gatewayRef, 응답 스냅샷, createdAt, expiresAt. 고유 제약: UNIQUE(idemKey). 경쟁 요청은 INSERT ... ON CONFLICT로 한쪽만 IN_PROGRESS 획득, 나머지는 기존 결과 폴링/반환. 오류경계: TTL 내 재사용만 허용, requestHash 불일치 시 409.
- GatewayPort: PG 추상화. 인터페이스: authorize(idemKey, orderId, amount), capture, cancel, getStatus(gatewayRef). 저장: 없음(외부). 오류경계: 타임아웃/5xx는 '미확정'으로 분류(성공/실패로 단정 금지), 재시도는 동일 idemKey 전달, 멱등 조회로 확정.
- WebhookHandler: PG 이벤트 수신. 저장 데이터: event_id(PK), gatewayRef, type, payload, receivedAt, processedAt. 고유 제약: UNIQUE(event_id)로 중복 무시. 순서 역전 대비: 이벤트에 발생시각/버전을 저장하고, 상태 전이는 단조(monotonic) 규칙으로 늦은 이벤트가 최신 상태를 덮지 않게 함. 오류경계: 서명 검증 실패 거부, 처리 실패는 재처리 큐.
- ReconciliationJob: 주기적으로 PG 승인 목록과 DB 결제 레코드를 대사. 저장: 대사 실행 로그, 불일치 항목(orphan_pg, orphan_db, amount_mismatch). 오류경계: 자동 환불/보정은 하지 않고 불일치 리포트만 생성(수동 승인 필요).
- 경쟁 요청 검증: 동일 orderId 두 요청이 동시에 들어오면 IdempotencyStore의 UNIQUE(idemKey)와 orderId 단위 직렬화(예: orderId 기준 락 또는 UNIQUE(orderId, active) 부분 인덱스)로 한 건만 승인. 순서 역전 검증: 취소 이벤트가 승인 이벤트보다 먼저 도착해도 상태 버전 비교로 최종 상태가 승인→취소 순서를 보존.

[3] 결제/취소 상태 전이표(설계 제안)
- INITIATED -> AUTHORIZED (authorize 성공)
- INITIATED -> FAILED (authorize 명시 실패)
- INITIATED -> PENDING_UNKNOWN (타임아웃/5xx, 재시도는 동일 idemKey)
- PENDING_UNKNOWN -> AUTHORIZED (getStatus 확정)
- PENDING_UNKNOWN -> FAILED (getStatus 확정 실패)
- AUTHORIZED -> CAPTURED (capture 성공)
- AUTHORIZED -> CANCELED (cancel 성공)
- CAPTURED -> CANCELED (환불/취소 성공)
- CANCELED -> (종결, 재취소 멱등 무시)
- 금지 전이: FAILED->CAPTURED, CANCELED->AUTHORIZED(역전 이벤트는 무시)

[4] 코드 디렉터리 구조(제안)
- src/payment/command/PaymentCommand.ts
- src/payment/idempotency/IdempotencyStore.ts
- src/payment/gateway/GatewayPort.ts, GatewayAdapter.ts
- src/payment/webhook/WebhookHandler.ts
- src/payment/reconcile/ReconciliationJob.ts
- src/payment/domain/stateMachine.ts, types.ts
- migrations/0001_idempotency.sql, 0002_webhook_events.sql
- tests/duplicate.spec.ts, ordering.spec.ts, partial_failure.spec.ts

[5] 핵심 처리 의사코드(제안)
PaymentCommand.execute(orderId, amount, idemKey, op):
  if idemKey is empty: return 400
  rec = IdempotencyStore.tryAcquire(idemKey, orderId, op, hash)
  if rec.status == SUCCEEDED: return rec.response
  if rec.status == IN_PROGRESS: return poll(rec)
  try: res = GatewayPort.authorize(idemKey, orderId, amount)
  catch Timeout/5xx: IdempotencyStore.mark(PENDING_UNKNOWN); return 202
  IdempotencyStore.complete(idemKey, res); return res
WebhookHandler.onEvent(evt):
  if not verify(evt): return 401
  if exists(evt.event_id): return 200 (중복 무시)
  save(evt); applyStateTransition(evt.gatewayRef, evt.type, evt.occurredAt)
ReconciliationJob.run():
  pg = GatewayPort.listApprovals(window)
  db = db.listPayments(window)
  diff = compare(pg, db); report(diff)  // 자동 보정 없음

[6] 데이터 전환 순서(온라인 준비 vs 최종 전환)
- 온라인 준비(무중단): 신규 스키마 추가(idempotency, webhook_events), 이중 쓰기 또는 읽기 호환 뷰, 백필을 온라인으로 진행.
- 최종 전환(중단 창): 쓰기 차단 -> 잔여 백필/검증 -> 읽기 전환 -> 구버전 앱 72시간 공존 유지 -> 모니터링 후 구경로 제거.
- 72시간 공존: 구버전 앱은 idemKey 미전송 가능성이 있으므로 서버가 orderId 기반 보조 멱등키를 생성해 중복 차단.

[7] 테스트 행렬(설계, 미실행)
- 중복: 동일 idemKey 2회 -> 1건 승인. 동일 orderId 다른 idemKey 동시 -> 1건만 승인.
- 순서 역전: cancel 이벤트가 authorize보다 먼저 도착 -> 최종 CANCELED 유지.
- 부분 실패: authorize 성공 후 DB 커밋 실패 -> PENDING_UNKNOWN, 대사에서 orphan_pg로 검출.
- 웹훅 중복: 동일 event_id 2회 -> 1회만 반영.
- 모두 미실행(미검증).

[8] 배포·롤백·중단 조건
- 배포: 카나리 -> 점진 확대. 롤백 트리거: 중복 승인률 증가, 대사 불일치 건수 임계 초과, PENDING_UNKNOWN 급증.
- 중단 조건: 대사 불일치 > 0이 지속, 롤백 검증 미완료 상태에서 전체 배포 금지.
- 롤백: 이중 쓰기 유지 구간에서는 구경로로 즉시 복귀 가능. 스키마는 하위 호환이라 데이터 손실 없음.

[9] 지원팀 전달용
- 확정 사실: 두 요청 모두 idempotency_key 비어 있음, 첫 요청 504 후 2초 뒤 재시도 200, PG 승인 2건 vs DB 완료 1건, 웹훅 중복/역전 가능, 타임아웃만으로 승인 확정 불가.
- 미확인: 실제 PG 승인 상세, DB 레코드 상태, 웹훅 로그, 고아 승인의 실제 방향, 롤백·재시도 결제·취소 결제 검증(3건 모두 미완료).

[10] 모순/미확인
- 모순: '재시도 안전성' 요구 vs 구버전 앱이 idemKey를 보내지 않을 수 있음 -> 서버 측 보조 멱등키 필요. '72시간 공존' vs '중복 청구 방지' -> 공존 기간 동안 서버 강제 멱등성 필수. '중단 20분' vs 백필 1800초(30분) -> 오프라인 일괄 백필은 허용 시간 초과, 온라인 백필 필요.
- 미검증: 모든 테스트, 롤백/재시도/취소 결제 검증 3건, 실제 대사 결과.

마이그레이션 계산: backfill_batches=1,200,000/5,000=240. serial=240*15=3,600초. two_worker=3,600/2=1,800초. downtime_budget=20*60=1,200초. coexistence_hours=72. offline_backfill_fits=false(1,800>1,200). unverified_release_checks=3. can_release=false(검증 3건 미완료 + 오프라인 백필 불가).

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
  "pg_approvals": 2,
  "db_completed": 1,
  "unreconciled_count": 1,
  "first_request_status": 504,
  "retry_delay_seconds": 2,
  "retry_status": 200,
  "idempotency_key_empty": true,
  "batch_size": 5000,
  "migration_rows": 1200000,
  "batch_seconds": 15,
  "max_workers": 2,
  "downtime_budget_minutes": 20,
  "tests_executed": false,
  "pg_db_queried": false,
  "refund_performed": false,
  "deploy_performed": false
}
```

### Evidence

**1.** backfill_batches = 1,200,000 / 5,000 = 240 (제공: 마이그레이션 대상 1,200,000행, 배치 5,000행)

**2.** serial_backfill_seconds = 240 * 15 = 3,600초 (제공: 배치당 15초)

**3.** two_worker_backfill_seconds = 3,600 / 2 = 1,800초 (제공: 작업자 최대 2개, 병목/오버헤드 없음 가정)

**4.** downtime_budget_seconds = 20분 * 60 = 1,200초 (제공: 서비스 중단 허용 20분)

**5.** offline_backfill_fits = false: 1,800초 > 1,200초이므로 중단 창 내 일괄 백필 불가

**6.** coexistence_hours = 72 (제공: 구버전/신버전 앱 72시간 공존 요구)

**7.** unverified_release_checks = 3 (제공: 롤백·재시도 결제·취소 결제 검증 모두 미완료)

**8.** can_release = false: 검증 3건 미완료이고 오프라인 백필이 중단 예산을 초과

**9.** unreconciled_count = PG 승인 2건 - DB 완료 1건 = 1건 (제공: PG 승인 2건, 주문 DB 완료 1건)

**10.** 원인 가설 근거: 두 요청 모두 idempotency_key가 비어 있어(제공) 서버가 재시도를 신규 결제로 처리했을 가능성

**11.** 타임아웃 확정 불가 근거: 제공 자료에 '네트워크 타임아웃만으로 승인 여부를 확정할 수 없다'고 명시

**12.** 실제 PG·DB 조회, 환불, 배포, 코드 구현, 테스트 실행은 수행하지 않았으며 모두 설계 제안 또는 미검증으로 표시
