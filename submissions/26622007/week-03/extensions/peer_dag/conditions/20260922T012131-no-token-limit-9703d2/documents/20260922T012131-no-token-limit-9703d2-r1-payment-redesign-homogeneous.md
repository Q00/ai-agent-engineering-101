# payment-redesign / homogeneous / 1회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## payment-redesign.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-payment-redesign-homogeneous/artifacts/payment-redesign.json)

결제 중복 사고를 재현 가능한 설계 문제로 분석했다. 원인 가설: (H1) 두 요청 모두 idempotency_key가 비어 있어 서버가 재시도를 신규 결제로 처리 → PG 승인 2건. (H2) 첫 요청 504는 네트워크 타임아웃일 뿐 승인 여부 미확정인데 클라이언트가 2초 뒤 재시도하면서 멱등 키 없이 중복 승인 유발. (H3) 웹훅 중복/역전으로 주문 DB가 완료 1건만 반영(승인 2건 중 1건 누락 또는 순서 역전으로 덮어씀). 확인 증거: PG 승인 로그의 동일 order_id 2건과 각 승인 시각/금액, 두 요청의 헤더·바디에 idempotency_key 부재, 웹훅 수신 로그의 event_id 중복 및 도착 순서, 주문 DB 결제 레코드 1건과 상태 이력. 설계(제안, 미구현): PaymentCommand는 order_id+idempotency_key를 받아 IdempotencyStore에 (key) 유니크 제약으로 선점 기록 후 GatewayPort.authorize 호출, 결과를 상태머신으로 반영. IdempotencyStore는 key PK, request_hash, status(IN_PROGRESS/SUCCEEDED/FAILED), gateway_ref, expires_at 저장, 동일 key 재요청 시 저장된 결과 반환(재시도 안전). GatewayPort는 authorize/cancel/query 인터페이스, 타임아웃은 UNKNOWN으로 반환하고 절대 성공/실패로 단정하지 않음. WebhookHandler는 event_id 유니크 제약으로 중복 무시, 상태 전이는 단조 증가 버전/시각 비교로 역전 방지. ReconciliationJob은 PG 승인 목록과 주문 DB를 주기적으로 대사하여 불일치(승인 2 vs 완료 1)를 탐지·보정. 상태 전이표(제안): 결제 PENDING→AUTHORIZED→CAPTURED→(CANCELLED/REFUNDED), 취소 요청 시 CAPTURED→CANCEL_PENDING→CANCELLED, UNKNOWN은 PENDING 유지 후 대사로 확정. 코드 디렉터리(제안): src/payment/{command,idempotency,gateway,webhook,reconciliation}, 각 모듈에 도메인·어댑터 분리. 핵심 의사코드: handle(cmd){ if(!cmd.idempotency_key) reject; rec=store.claim(key,hash); if(rec.exists) return rec.result; r=gateway.authorize(...); store.finish(key,r); return r; }. 마이그레이션 산술: backfill_batches=1200000/5000=240, serial=240*15=3600초, two_worker=3600/2=1800초, downtime_budget=20*60=1200초, coexistence_hours=72, offline_backfill_fits=(1800<=1200)=false, can_release=false(미검증 3건 존재). 데이터 전환은 온라인 준비(신규 스키마 병행 기록, 백필은 온라인에서 2워커로 1800초 소요)와 최종 전환(짧은 쓰기 중단, 검증 후 스위치)으로 구분하며, 1800초>1200초이므로 오프라인 일괄은 허용 시간 내 불가. 테스트 행렬(제안): 중복 요청(동일 key 2회→1승인), 순서 역전(웹훅 늦은 도착→상태 유지), 부분 실패(승인 후 DB 실패→대사 보정). 배포·롤백·중단 조건: canary 배포, 롤백 트리거는 중복 승인 재발·대사 불일치 임계 초과, 중단 조건은 미검증 항목 미해소. 지원팀 전달 사실: PG 승인 2건/DB 완료 1건, idempotency_key 부재, 504는 미확정. 미확인: 롤백·재시도 결제·취소 결제 검증 미완료(unverified_release_checks=3). 모순: 72시간 공존 요구와 멱등 키 없는 구버전 앱의 중복 위험, 1800초 백필과 1200초 중단 예산 불일치. 실제 PG·DB 조회/환불/배포는 수행하지 않았으며 모든 구현·테스트는 설계 제안으로만 제시한다.

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

**1.** backfill_batches = 1,200,000 / 5,000 = 240 (제공: 마이그레이션 대상 1,200,000행, 배치 5,000행)

**2.** serial_backfill_seconds = 240 * 15 = 3,600초 (제공: 배치당 15초)

**3.** two_worker_backfill_seconds = 3,600 / 2 = 1,800초 (제공: 작업자 최대 2개, 병목/오버헤드 없음 가정)

**4.** downtime_budget_seconds = 20 * 60 = 1,200초 (제공: 서비스 중단 허용 20분)

**5.** offline_backfill_fits = (1,800 <= 1,200) = false

**6.** coexistence_hours = 72 (제공: 구버전·신버전 앱 72시간 공존 요구)

**7.** unverified_release_checks = 3 (제공: 롤백·재시도 결제·취소 결제 검증 모두 미완료)

**8.** can_release = false (미검증 3건 존재 및 오프라인 백필이 중단 예산 초과)

**9.** 원인 가설 근거: 제공 합성 기록에서 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 비어 있음, PG 승인 2건 vs 주문 DB 완료 1건

**10.** 설계·코드·상태 전이표·테스트 행렬·배포/롤백 조건은 실제 구현/테스트가 아닌 설계 제안이며, 실제 PG·DB 조회나 환불·배포는 수행하지 않음
