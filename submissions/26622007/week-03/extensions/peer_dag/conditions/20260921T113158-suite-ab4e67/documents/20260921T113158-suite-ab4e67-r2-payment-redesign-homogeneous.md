# payment-redesign / homogeneous / 2회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## payment-redesign.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r2-payment-redesign-homogeneous/artifacts/payment-redesign.json)

제공된 합성 기록과 수치만으로 결제 중복 사고를 설계 문제로 분석했다. 실제 PG·DB 조회, 환불, 배포, 테스트 실행은 하지 않았고 모두 설계 제안 또는 미검증으로 표시한다.

[1] 원인 가설과 확인할 증거
- H1(주원인): 두 요청 모두 idempotency_key가 비어 있어 서버가 재시도를 새 결제로 취급 → PG 승인 2건. 확인 증거: PG 승인 로그의 동일 order_id 2건, 각 요청의 idempotency_key 필드 null/빈값, 게이트웨이 요청 본문 해시 동일 여부.
- H2: 첫 요청 504는 네트워크 타임아웃이며 승인 여부 불확정. 클라이언트가 2초 뒤 재시도하면서 중복 발생. 확인 증거: PG측 승인 시각과 클라이언트 요청 시각, 게이트웨이 커넥션 타임아웃 설정, 504 응답 본문.
- H3: 웹훅 중복/순서 역전으로 DB 상태가 승인 1건으로만 반영. 확인 증거: 웹훅 수신 로그의 event_id 중복, 수신 순서, 처리 시각, DB 결제 상태 이력.
- H4: 주문 DB 완료 1건은 웹훅 1건만 반영된 결과이며 PG 2건과 불일치. 확인 증거: 대사 리포트의 order_id별 PG 승인 수 vs DB 완료 수.

[2] 컴포넌트 책임·인터페이스·저장 데이터·고유 제약·오류/재시도 경계
- PaymentCommand: 결제/취소 유스케이스 오케스트레이션. 입력 {order_id, amount, currency, idempotency_key(필수), request_hash}. 멱등키가 없으면 즉시 400 거부(신버전). 저장: payment_attempt(order_id, idempotency_key UNIQUE, state, pg_txn_id, amount, updated_at). 오류 경계: PG 타임아웃 시 상태 UNKNOWN으로 두고 재시도는 같은 키로만 허용.
- IdempotencyStore: 키→결과 매핑. 저장: idempotency_record(key UNIQUE, request_hash, response_snapshot, state, expires_at). 경쟁 요청은 key UNIQUE 제약 + INSERT ... ON CONFLICT로 한쪽만 승리. 같은 키·다른 request_hash는 409.
- GatewayPort: PG 호출 추상화. authorize(order_id, amount, idempotency_key) → {status: APPROVED|DECLINED|UNKNOWN, pg_txn_id}. cancel(pg_txn_id, idempotency_key). 타임아웃은 UNKNOWN으로 반환하며 절대 성공/실패로 단정하지 않음.
- WebhookHandler: PG 이벤트 수신. event_id UNIQUE 제약으로 중복 무시, order_id+pg_txn_id 기준으로 상태 upsert. 순서 역전은 이벤트 발생 시각(occurred_at) 비교로 오래된 이벤트가 최신 상태를 덮어쓰지 않게 함. 서명 검증 실패 시 401.
- ReconciliationJob: 주기적으로 PG 승인 목록과 DB 결제 상태를 order_id 기준 대사. 불일치 유형(DB에 없음/금액 불일치/중복 승인) 리포트 생성. 자동 환불은 하지 않고 수동 검토 큐에 넣음.

[3] 상태 전이표(결제)
INIT → AUTHORIZING (요청 수락, 키 잠금)
AUTHORIZING → AUTHORIZED (PG APPROVED)
AUTHORIZING → FAILED (PG DECLINED)
AUTHORIZING → UNKNOWN (타임아웃) → 같은 키 재시도 시 AUTHORIZING 재진입, 다른 키는 거부
AUTHORIZED → CAPTURED → COMPLETED
AUTHORIZED/CAPTURED/COMPLETED → CANCEL_REQUESTED → CANCELLED
UNKNOWN → (대사로) AUTHORIZED 또는 FAILED 확정
취소: CANCELLED는 종결, 재취소는 멱등 응답.

[4] 코드 디렉터리(제안)
/payments/domain/{PaymentCommand, PaymentState, IdempotencyKey}
/payments/app/{AuthorizePaymentUseCase, CancelPaymentUseCase}
/payments/ports/{GatewayPort, IdempotencyStore, PaymentRepository}
/payments/adapters/{PgHttpAdapter, RedisIdempotencyStore, SqlPaymentRepository}
/payments/webhook/{WebhookHandler, EventDeduplicator}
/payments/jobs/{ReconciliationJob}
/payments/migrations/{V1__idempotency.sql, V2__backfill.sql}
핵심 의사코드: authorize(order_id, key, amount): if key empty → reject; rec = store.getOrCreate(key, hash(request)); if rec.done → return rec.response; if rec.inflight → 409/retry-after; resp = gateway.authorize(order_id, amount, key); if resp==UNKNOWN → state=UNKNOWN, return 202; store.complete(key, resp); return resp.

[5] 데이터 전환 순서
온라인 준비: (a) idempotency_record 테이블 생성 및 UNIQUE 인덱스, (b) 신버전 코드가 키 없으면 거부하도록 배포하되 구버전 앱은 서버가 키를 생성해 대체, (c) 이중쓰기로 신규 결제를 양쪽 스키마에 기록, (d) 백필을 온라인으로 진행(서비스 중단 없이), (e) 검증 쿼리로 불일치 0 확인.
최종 전환: 짧은 쓰기 중단 또는 무중단 전환(읽기 전용 모드 후 스위치). 20분 예산 내에는 오프라인 일괄이 불가하므로 온라인 백필이 필수.

[6] 테스트 행렬(제안, 미실행)
- 중복: 같은 키 동시 2요청 → 1건만 승인, 나머지 멱등 응답.
- 순서 역전: 웹훅 B(최신) 후 A(과거) 도착 → 최신 상태 유지.
- 부분 실패: PG 승인 후 DB 커밋 실패 → 대사 잡이 불일치 검출, 재처리 시 멱등.
- 타임아웃: PG UNKNOWN → 같은 키 재시도 시 중복 승인 없음.
- 취소: 취소 후 재시도 결제 → 거부 또는 새 키 필요.

[7] 배포·롤백·중단 조건
배포: 카나리 → 10% → 100%. 롤백 조건: 중복 승인률 증가, 대사 불일치 임계 초과, 5xx 급증. 중단 조건: PG 승인 수와 DB 완료 수 차이가 임계 초과 시 신규 결제 일시 중단.

[8] 지원팀 전달 사실과 미확인 사항
사실: 동일 order_id에 PG 승인 2건, DB 완료 1건, 두 요청 idempotency_key 비어 있음, 첫 요청 504 후 2초 뒤 재시도 200.
미확인: 실제 PG 조회 결과, 환불 가능 여부, 롤백 절차 검증, 재시도 결제 검증, 취소 결제 검증 — 모두 미완료.

[9] 모순/미확인
- 72시간 공존 요구와 '키 없으면 거부' 규칙이 충돌: 구버전 앱은 서버가 키를 생성해 대체해야 함.
- 오프라인 일괄은 20분 예산 초과로 불가.
- can_release=false: 롤백·재시도·취소 검증 미완료.

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

**1.** 배치 수 = 1,200,000 / 5,000 = 240

**2.** 1개 작업자 시간 = 240 * 15초 = 3,600초 = 60분

**3.** 2개 작업자 시간 = 3,600 / 2 = 1,800초 = 30분

**4.** 중단 허용 = 20분 = 1,200초

**5.** 오프라인 일괄 최소 1,800초 > 1,200초이므로 허용 시간 내 불가(offline_backfill_fits=false)

**6.** 합성 기록: 같은 order_id 첫 요청 504, 2초 뒤 재시도 200, 두 요청 idempotency_key 비어 있음

**7.** PG 승인 2건 vs 주문 DB 완료 1건 불일치

**8.** 미완료 검증 3건: 롤백, 재시도 결제, 취소 결제 → can_release=false

**9.** 72시간 공존 요구는 source의 명시 요구사항
