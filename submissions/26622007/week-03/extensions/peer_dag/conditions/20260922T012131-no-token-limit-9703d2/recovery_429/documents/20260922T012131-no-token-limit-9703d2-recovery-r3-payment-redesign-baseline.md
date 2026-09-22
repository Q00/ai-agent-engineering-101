# payment-redesign / baseline — 429 복구

상태: succeeded. facts: 8/8.
모델 원본의 열람용 사본이며 실제 구현/실행 검증이 아니다.

## payment-redesign.json

Worker: A; 상태: succeeded
[원본 JSON](../../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-baseline/artifacts/payment-redesign.json)

제공된 합성 기록만으로 결제 중복 사고를 재현 가능한 설계 문제로 분석했다. 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포·환불은 수행하지 않았으며 모든 설계·의사코드·테스트 행렬은 제안이다.

[1] 원인 가설과 확인/반증 증거
- H1(멱등 키 부재): 두 요청 모두 idempotency_key가 비어 있어 서버가 재시도를 신규 결제로 처리 → PG 승인 2건. 확인 증거: PG 승인 로그의 idempotency_key 필드 공백, 요청 본문 해시 동일 여부, 게이트웨이 요청 ID 2개. 반증: PG가 동일 키로 중복 차단했다면 승인 1건이어야 함.
- H2(타임아웃 후 승인 확정 불가): 첫 요청 504는 네트워크 타임아웃이며 PG 측 승인 여부가 미확정. 확인 증거: PG 승인 시각과 첫 요청 시각의 근접성, 게이트웨이 응답 지연 로그. 반증: PG가 첫 요청을 명시적 거절했다면 승인 1건.
- H3(웹훅 중복/순서 역전): 웹훅이 중복·역전되면 DB 완료 1건과 PG 2건의 불일치가 고착. 확인 증거: 웹훅 이벤트 ID 중복, 이벤트 발생 시각 vs 수신 시각 역전, 처리 로그의 순서. 반증: 웹훅이 단일·정순이면 대사 잡이 즉시 정합화.
- H4(대사 부재/지연): ReconciliationJob이 없거나 지연되어 PG 2건 vs DB 1건이 방치. 확인 증거: 대사 잡 실행 이력, 불일치 큐 적재 여부. 반증: 대사 잡이 주기 실행 중이고 불일치가 해소됨.
- H5(경쟁 요청): 동일 order_id에 대한 동시 재시도가 선점 없이 PG로 전달. 확인 증거: 동일 order_id 동시 요청 타임스탬프, DB 유니크 제약 위반 로그. 반증: 선점(unique) 제약이 있었다면 두 번째 요청이 차단됨.

[2] 컴포넌트 책임·인터페이스·저장 데이터·고유 제약·오류/재시도 경계
- PaymentCommand: 결제/취소 유스케이스 오케스트레이션. 입력(order_id, amount, currency, idempotency_key, client_version). 출력(상태, pg_txn_id). 저장: 없음(오케스트레이션). 고유 제약: 없음. 오류 경계: 입력 검증 실패는 즉시 반환, PG 타임아웃은 UNKNOWN으로 반환하고 재시도는 IdempotencyStore 선점 후에만 허용.
- IdempotencyStore: 멱등 키 선점·결과 캐시. 인터페이스: reserve(key, order_id, request_hash) -> {RESERVED|EXISTS|CONFLICT}, complete(key, result), get(key). 저장: key(PK), order_id, request_hash, status(RESERVED/COMPLETED/FAILED), pg_txn_id, created_at, expires_at. 고유 제약: PK(key), UNIQUE(order_id, key)로 동일 주문 중복 키 차단, request_hash 불일치 시 CONFLICT. 오류 경계: reserve는 원자적(INSERT ... ON CONFLICT), complete는 멱등. TTL로 무한 증가 방지.
- GatewayPort: PG 호출 추상화. 인터페이스: authorize(order_id, amount, idempotency_key) -> {APPROVED|DECLINED|UNKNOWN, pg_txn_id}, cancel(pg_txn_id, idempotency_key) -> {CANCELED|UNKNOWN}, query(pg_txn_id) -> 상태. 저장: 없음(외부). 고유 제약: PG 측 idempotency_key 전달. 오류 경계: 타임아웃=UNKNOWN, 재시도는 동일 키로만, 지수 백오프+최대 횟수.
- WebhookHandler: PG 이벤트 수신·멱등 처리. 인터페이스: handle(event_id, type, pg_txn_id, order_id, occurred_at). 저장: processed_events(event_id PK), last_event_at per order. 고유 제약: event_id PK로 중복 차단, order_id+type+pg_txn_id 유니크로 역전 방어. 오류 경계: 중복은 200 반환(no-op), 순서 역전은 occurred_at 비교로 무시 또는 재조회.
- ReconciliationJob: PG vs DB 대사. 인터페이스: run(window) -> {matched, mismatched, resolved}. 저장: reconciliation_runs, mismatches(order_id, pg_txn_id, db_status, pg_status, detected_at). 고유 제약: UNIQUE(order_id, pg_txn_id). 오류 경계: 불일치 시 자동 보정은 정책에 따라 제한, 사람 검토 큐로 이관.

[3] 결제/취소 상태 전이표
- 결제: INITIATED -> RESERVED -> (APPROVED -> COMPLETED) | (DECLINED -> FAILED) | (UNKNOWN -> PENDING_RECONCILE). PENDING_RECONCILE -> COMPLETED(대사로 승인 확인) | FAILED(대사로 거절 확인).
- 취소: COMPLETED -> CANCEL_REQUESTED -> (CANCELED) | (UNKNOWN -> PENDING_RECONCILE). PENDING_RECONCILE -> CANCELED | COMPLETED(취소 실패).
- 금지 전이: FAILED -> COMPLETED(재시도는 새 키), CANCELED -> COMPLETED.

[4] 코드 디렉터리 구조(제안)
- src/payment/command/PaymentCommand.ts
- src/payment/idempotency/IdempotencyStore.ts
- src/payment/gateway/GatewayPort.ts, PgAdapter.ts
- src/payment/webhook/WebhookHandler.ts
- src/payment/reconcile/ReconciliationJob.ts
- src/payment/domain/stateMachine.ts
- migrations/001_idempotency.sql, 002_processed_events.sql, 003_reconciliation.sql
- tests/duplicate.spec.ts, order_inversion.spec.ts, partial_failure.spec.ts

[5] 핵심 처리 의사코드(제안)
- 멱등 키 생성·선점: key = hash(order_id + client_version + attempt_scope); r = store.reserve(key, order_id, hash(body)); if r==EXISTS return store.get(key); if r==CONFLICT return 409; try { res = gateway.authorize(order_id, amount, key); if res==UNKNOWN { store.complete(key, PENDING_RECONCILE); return 202; } store.complete(key, res); } catch { store.complete(key, FAILED); throw; }
- PG 호출: 동일 key로만 재시도, 지수 백오프, 최대 N회, 타임아웃은 UNKNOWN.
- 웹훅 멱등: if processed_events.insert(event_id) == duplicate return 200; if occurred_at < last_event_at[order_id] { trigger reconcile; return 200; } apply state transition; update last_event_at.
- 대사 잡: for each order in window: pg = gateway.query(pg_txn_id); db = db.get(order_id); if pg != db { enqueue mismatch; if policy allows auto-fix apply else human review }.

[6] 데이터 전환 순서(온라인 준비 vs 최종 전환)
- 온라인 준비(무중단): 신규 스키마 추가(idempotency, processed_events, reconciliation), 구버전 앱은 기존 경로 유지, 신버전 앱은 새 경로 사용, 72시간 공존. 백필은 온라인으로 진행(2작업자 1800초=30분).
- 최종 전환(짧은 중단): 구버전 트래픽 차단, 잔여 백필 확인, 스키마 제약 활성화, 신버전만 허용. 중단 예산 1200초(20분) 내에 전환 스크립트 실행.
- 모순: 2작업자 백필 1800초(30분) > 중단 예산 1200초(20분)이므로 오프라인 일괄 백필은 불가. 온라인 백필 후 최종 전환만 중단 내 수행해야 함.

[7] 테스트 행렬(제안, 미실행)
- 중복: 동일 키 2회 → 1회 승인, 2번째는 캐시 반환. 동일 order_id 다른 키 → 정책에 따라 차단 또는 별도 결제.
- 순서 역전: 웹훅 B(취소) 후 A(승인) 도착 → occurred_at 비교로 A 무시, 대사 트리거.
- 부분 실패: PG 승인 후 DB 커밋 실패 → PENDING_RECONCILE, 대사로 복구. PG 타임아웃 → UNKNOWN, 재시도는 동일 키.

[8] 배포·롤백·중단 조건
- 배포: 카나리 5% → 25% → 100%, 각 단계에서 중복/불일치 지표 확인. 롤백: 신버전 비활성화, 구버전 경로 복원, 새 스키마는 유지(하위 호환). 중단 조건: 중복 승인률 > 0, 대사 불일치 > 임계, 에러율 > 임계.
- 롤백 시 새 버전 데이터 롤백, 재시도 결제, 취소 결제 검증이 모두 미완료이므로 can_release=false.

[9] 지원팀 전달: 확정 사실(합성 기록: 504 후 2초 뒤 200, idempotency_key 공백, PG 2건 vs DB 1건, 웹훅 중복/역전 가능, 타임아웃만으로 승인 확정 불가, 72시간 공존, 20분 중단 예산). 미확인 사항(실제 PG·DB 상태, 롤백·재시도·취소 검증, 대사 잡 운영 여부, 구버전 앱 실제 트래픽 비중).

[10] 미검증: 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포·환불 모두 미수행. unverified_release_checks=3(롤백, 재시도 결제, 취소 결제).

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

1. backfill_batches = 1,200,000 / 5,000 = 240 (제공: 마이그레이션 대상 1,200,000행, 배치 5,000행)
2. serial_backfill_seconds = 240 * 15 = 3,600초 (제공: 배치당 15초, 작업자 1개)
3. two_worker_backfill_seconds = 3,600 / 2 = 1,800초 (제공: 작업자 최대 2개, 병목·오버헤드 없음 가정)
4. downtime_budget_seconds = 20 * 60 = 1,200초 (제공: 서비스 중단 허용 20분)
5. coexistence_hours = 72 (제공: 구버전·신버전 앱 72시간 공존 요구)
6. offline_backfill_fits = (1,800 <= 1,200) = false → 오프라인 일괄 백필은 중단 예산 초과, 온라인 백필 후 최종 전환만 중단 내 수행
7. unverified_release_checks = 3 (제공: 새 버전 데이터 롤백·재시도 결제·취소 결제 검증 모두 미완료)
8. can_release = false (미검증 3건 존재, 실제 PG·DB 조회·테스트·배포 미수행)
9. 원인 가설 근거: 제공 합성 기록(동일 order_id, 첫 요청 504, 2초 뒤 재시도 200, 두 요청 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건)
10. 웹훅 중복·순서 역전 가능 및 타임아웃만으로 승인 확정 불가(제공 요구사항)
11. 모순: 2작업자 백필 1,800초 > 중단 예산 1,200초 → 오프라인 일괄 전환 불가, 온라인 준비와 최종 전환 분리 필요
12. 실제 PG·DB 조회, 코드 구현, 테스트 실행, 배포·환불은 수행하지 않음(미검증)