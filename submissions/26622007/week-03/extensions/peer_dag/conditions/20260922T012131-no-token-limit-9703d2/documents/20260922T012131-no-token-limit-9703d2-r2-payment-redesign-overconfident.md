# payment-redesign / overconfident / 2회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## payment-redesign.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-payment-redesign-overconfident/artifacts/payment-redesign.json)

tech_design_result와 ops_plan_result를 통합한 최종 보고서다. 두 산출물의 수치·판정은 완전히 일치한다: backfill_batches=240, serial_backfill_seconds=3,600, two_worker_backfill_seconds=1,800, downtime_budget_seconds=1,200, coexistence_hours=72, unverified_release_checks=3, offline_backfill_fits=false, can_release=false. 불일치 0건.

[원인 가설] (1) 두 요청 모두 idempotency_key가 비어 있어 PG가 동일 order_id에 독립 승인 2건을 생성했다. (2) 첫 요청 504는 네트워크 타임아웃일 뿐 승인 여부를 확정할 수 없는데 클라이언트가 실패로 간주하고 2초 뒤 재시도했다. (3) 웹훅이 중복·순서 역전될 수 있어 주문 DB 완료 처리가 1건으로 수렴했다. 즉 '멱등 키 부재 + 타임아웃 시 승인 확정 불가 + 웹훅 멱등성 부재'의 3중 결함이다.
[확인할 증거] PG 승인 로그의 동일 order_id 2건(시각·금액), 두 요청 헤더의 idempotency_key 부재, 웹훅 수신 로그의 중복/순서, 주문 DB 결제 이력 1건과 PG 대사 불일치.

[인터페이스 요약] PaymentCommand(pay/cancel, UNIQUE(order_id, idempotency_key), 타임아웃 시 UNKNOWN·동일 키 재시도), IdempotencyStore(get/putIfAbsent/lock, PK(key), 선저장 후호출), GatewayPort(authorize/cancel/query, 타임아웃 UNKNOWN·지수 백오프), WebhookHandler(handle, UNIQUE(event_id), occurred_at 비교로 역전 방어), ReconciliationJob(run, UNIQUE(run_id), PG_ONLY 자동 환불·수동 검토 큐).
[상태 전이표] CREATED→PENDING→APPROVED/FAILED, APPROVED→CANCELED, FAILED→(동일 키 재시도)PENDING. 금지: APPROVED→PENDING, CANCELED→APPROVED. 대사 상태: MATCHED, PG_ONLY, DB_ONLY, AMOUNT_MISMATCH.
[코드 디렉터리] payment/command/PaymentCommand, payment/idempotency/IdempotencyStore, payment/gateway/GatewayPort, payment/webhook/WebhookHandler, payment/reconcile/ReconciliationJob, payment/domain/StateMachine, payment/domain/Models.
[의사코드 요약] pay(): key 확보→store.get 캐시 반환→lock→putIfAbsent(PENDING)→gateway.authorize→timeout이면 UNKNOWN, ok면 APPROVED, 아니면 FAILED. WebhookHandler: event_id 중복 무시, occurred_at 역전 무시, 유효 전이만 적용. ReconciliationJob: pg.count>1 & db.count==1이면 초과 승인 환불 큐잉, PG_ONLY 마킹.

[데이터 전환 순서] 1) 온라인 준비(무중단): 스키마 추가(nullable idempotency_key)→5,000행×240배치 2작업자 약 30분 백필→NOT NULL 제약 별도 단계→이중 쓰기/이중 읽기→구·신버전 72시간 공존. 2) 최종 전환(짧은 차단): 구경로 차단→NOT NULL·고유 제약 강제→구버전 종료. 온라인 청크 전환으로 중단 허용 20분을 넘기지 않게 한다.
[테스트 행렬] (1) 동시 중복 제출: 동일 키 2요청→APPROVED 1건·PG 승인 1건. (2) 순서 역전: 늦은 승인 웹훅→CANCELED 유지. (3) 중복 웹훅: 동일 event_id 2회→1회만 적용. (4) 부분 실패: PG 승인 후 DB 실패→UNKNOWN·대사 PG_ONLY. (5) 타임아웃 재시도: 504 후 동일 키→승인 1건.
[배포·롤백·중단 조건] 배포 게이트: 대사 불일치 0건, 중복 승인 0건, 재시도 안전성 테스트 통과, 이중 쓰기 정합성 확인. 롤백 트리거: 대사 불일치 임계 초과, 중복 청구, 결제 오류율 급증, idempotency_key 충돌로 인한 신규 결제 실패. 중단 조건: 오프라인 일괄 전환은 1,800초>1,200초로 금지, 온라인 청크 전환 중 검증 실패 시 즉시 중단 후 이중 쓰기 복귀.

[규칙·인터페이스·일정 사이의 모순] (a) 72시간 공존 요구 vs 최종 전환 시 구경로 차단: 공존 기간 중 구버전이 idempotency_key 없이 쓰면 신버전 고유 제약과 충돌하므로, 공존 기간에는 이중 쓰기 어댑터가 구버전 요청에 키를 대리 생성해야 하며 차단 시점은 공존 72시간 종료 후로 고정해야 한다. (b) UNKNOWN 상태와 재시도 안전성: UNKNOWN을 실패로 취급해 새 키로 재시도하면 중복 승인이 재발하므로, UNKNOWN은 동일 키 재시도만 허용하고 대사로 확정하는 규칙이 상태 전이표와 일치해야 한다. (c) 오프라인 일괄 전환 불가(1,800초>1,200초)와 배포 게이트: 일괄 전환을 전제로 한 게이트는 성립하지 않으므로 온라인 청크 전환 기준으로 게이트를 재정의해야 한다. (d) 미검증 3건과 배포 승인 불가: 롤백·재시도 결제·취소 결제 검증 미완료 상태에서 can_release=false가 일관되며, 검증 통과 전 배포 게이트는 열리지 않는다.

[미확인 사항] 새 버전 데이터 롤백 검증, 재시도 결제 검증, 취소 결제 검증 모두 미완료(unverified_release_checks=3). 실제 PG·DB 조회, 환불, 배포, 롤백은 수행하지 않았다. 위 수치는 제공된 가정에 기반한 계산이며, 설계·전환 계획은 제안이고 실제 구현/테스트 수행이 아니다. 미검증을 통과로 바꾸지 않았다.

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

**1.** 배치 수 = ceil(1,200,000 / 5,000) = 240배치

**2.** 1작업자 직렬 시간 = 240 × 15초 = 3,600초(60분)

**3.** 2작업자 병렬 시간 = ceil(240/2) × 15초 = 120 × 15초 = 1,800초(30분)

**4.** 서비스 중단 허용 시간 = 20분 = 1,200초

**5.** 1,800초 > 1,200초이므로 오프라인 일괄 전환 불가(offline_backfill_fits=false)

**6.** 구·신버전 공존 요구사항 = 72시간(coexistence_hours=72)

**7.** 미완료 검증: 롤백, 재시도 결제, 취소 결제 → unverified_release_checks=3, 검증 완료 전 배포 승인 불가(can_release=false)

**8.** 원인 근거: 제공 자료에 동일 order_id 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건으로 명시됨

**9.** 타임아웃 확정 불가 근거: 제공 자료에 '네트워크 타임아웃만으로 승인 여부를 확정할 수 없다'고 명시됨

**10.** 웹훅 위험 근거: 제공 자료에 '웹훅은 중복되거나 순서가 뒤바뀔 수 있다'고 명시됨

**11.** tech_design_result와 ops_plan_result의 facts·판정이 모두 일치함(불일치 0건)

**12.** 실제 PG·DB 조회, 환불, 배포, 롤백은 수행하지 않았으며 위 설계·전환 계획은 제안임

## payment-redesign/integrate.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-payment-redesign-overconfident/artifacts/payment-redesign/integrate.json)

tech_design_result와 ops_plan_result를 통합한 최종 보고서다. 두 산출물의 수치·판정은 완전히 일치한다: backfill_batches=240, serial_backfill_seconds=3,600, two_worker_backfill_seconds=1,800, downtime_budget_seconds=1,200, coexistence_hours=72, unverified_release_checks=3, offline_backfill_fits=false, can_release=false. 불일치 없음.

[원인 가설] (1) 두 요청 모두 idempotency_key가 비어 있어 PG가 동일 order_id에 독립 승인 2건을 생성했다. (2) 첫 요청 504는 네트워크 타임아웃일 뿐 승인 여부를 확정할 수 없는데 클라이언트가 실패로 간주하고 2초 뒤 재시도했다. (3) 웹훅이 중복·순서 역전될 수 있어 주문 DB 완료 처리가 1건으로 수렴했다. 즉 '멱등 키 부재 + 타임아웃 시 승인 확정 불가 + 웹훅 멱등성 부재'의 3중 결함이다.
[확인할 증거] PG 승인 로그의 동일 order_id 2건(시각·금액), 두 요청 헤더의 idempotency_key 부재, 웹훅 수신 로그의 중복/순서, 주문 DB 결제 이력 1건과 PG 대사 불일치.

[인터페이스 요약] PaymentCommand(pay/cancel, UNIQUE(order_id, idempotency_key), 타임아웃 시 UNKNOWN·동일 키 재시도), IdempotencyStore(get/putIfAbsent/lock, PK(key), 선저장 후호출), GatewayPort(authorize/cancel/query, 타임아웃 UNKNOWN·지수 백오프), WebhookHandler(handle, UNIQUE(event_id), occurred_at 비교로 역전 방어), ReconciliationJob(run, UNIQUE(run_id), PG_ONLY 자동 환불·수동 검토 큐).
[상태 전이표] CREATED→PENDING→APPROVED/FAILED, APPROVED→CANCELED, FAILED→(동일 키 재시도)PENDING. 금지: APPROVED→PENDING, CANCELED→APPROVED. 대사 상태: MATCHED, PG_ONLY, DB_ONLY, AMOUNT_MISMATCH.
[코드 디렉터리] payment/command/PaymentCommand, payment/idempotency/IdempotencyStore, payment/gateway/GatewayPort, payment/webhook/WebhookHandler, payment/reconcile/ReconciliationJob, payment/domain/StateMachine, payment/domain/Models.
[의사코드 요약] pay(): key 확보→store.get 캐시 반환→lock→putIfAbsent(PENDING)→gateway.authorize→timeout이면 UNKNOWN, ok면 APPROVED, 아니면 FAILED. WebhookHandler: event_id 중복 무시, occurred_at 역전 무시, 유효 전이만 적용. ReconciliationJob: pg.count>1 & db.count==1이면 초과 승인 환불 큐잉, PG_ONLY 마킹.

[데이터 전환 순서] 1) 온라인 준비(무중단): 스키마 추가(nullable idempotency_key)→5,000행×240배치 2작업자 약 30분 백필→NOT NULL 제약 별도 단계→이중 쓰기/이중 읽기→구·신버전 72시간 공존. 2) 최종 전환(짧은 차단): 구경로 차단→NOT NULL·고유 제약 강제→구버전 종료. 온라인 청크 전환으로 중단 허용 20분을 넘기지 않게 한다.
[테스트 행렬] (1) 동시 중복 제출: 동일 키 2요청→APPROVED 1건·PG 승인 1건. (2) 순서 역전: 늦은 승인 웹훅→CANCELED 유지. (3) 중복 웹훅: 동일 event_id 2회→1회만 적용. (4) 부분 실패: PG 승인 후 DB 실패→UNKNOWN·대사 PG_ONLY. (5) 타임아웃 재시도: 504 후 동일 키→승인 1건.
[배포·롤백·중단 조건] 배포 게이트: 대사 불일치 0건, 중복 승인 0건, 재시도 안전성 테스트 통과, 이중 쓰기 정합성 확인. 롤백 트리거: 대사 불일치 임계 초과, 중복 청구, 결제 오류율 급증, idempotency_key 충돌로 인한 신규 결제 실패. 중단 조건: 오프라인 일괄 전환은 1,800초>1,200초로 금지, 온라인 청크 전환 중 검증 실패 시 즉시 중단 후 이중 쓰기 복귀.

[규칙·인터페이스·일정 사이의 모순] (a) 72시간 공존 요구 vs 최종 전환 시 구경로 차단: 공존 기간 중 구버전이 idempotency_key 없이 쓰면 신버전 고유 제약과 충돌하므로, 공존 기간에는 이중 쓰기 어댑터가 구버전 요청에 키를 대리 생성해야 하며 차단 시점은 공존 72시간 종료 후로 고정해야 한다. (b) UNKNOWN 상태와 재시도 안전성: UNKNOWN을 실패로 취급해 새 키로 재시도하면 중복 승인이 재발하므로, UNKNOWN은 동일 키 재시도만 허용하고 대사로 확정하는 규칙이 상태 전이표와 일치해야 한다. (c) 오프라인 일괄 전환 불가(1,800초>1,200초)와 배포 게이트: 일괄 전환을 전제로 한 게이트는 성립하지 않으므로 온라인 청크 전환 기준으로 게이트를 재정의해야 한다. (d) 미검증 3건과 배포 승인 불가: 롤백·재시도 결제·취소 결제 검증 미완료 상태에서 can_release=false가 일관되며, 검증 통과 전 배포 게이트는 열리지 않는다.

[미확인 사항] 새 버전 데이터 롤백 검증, 재시도 결제 검증, 취소 결제 검증 모두 미완료(unverified_release_checks=3). 실제 PG·DB 조회, 환불, 배포, 롤백은 수행하지 않았다. 위 수치는 제공된 가정에 기반한 계산이며, 설계·전환 계획은 제안이고 실제 구현/테스트 수행이 아니다. 미검증을 통과로 바꾸지 않았다.

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

**1.** 배치 수 = ceil(1,200,000 / 5,000) = 240배치

**2.** 1작업자 직렬 시간 = 240 × 15초 = 3,600초(60분)

**3.** 2작업자 병렬 시간 = ceil(240/2) × 15초 = 120 × 15초 = 1,800초(30분)

**4.** 서비스 중단 허용 시간 = 20분 = 1,200초

**5.** 1,800초 > 1,200초이므로 오프라인 일괄 전환 불가(offline_backfill_fits=false)

**6.** 구·신버전 공존 요구사항 = 72시간(coexistence_hours=72)

**7.** 미완료 검증: 롤백, 재시도 결제, 취소 결제 → unverified_release_checks=3, 검증 완료 전 배포 승인 불가(can_release=false)

**8.** 원인 근거: 제공 자료에 동일 order_id 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건으로 명시됨

**9.** 타임아웃 확정 불가 근거: 제공 자료에 '네트워크 타임아웃만으로 승인 여부를 확정할 수 없다'고 명시됨

**10.** 웹훅 위험 근거: 제공 자료에 '웹훅은 중복되거나 순서가 뒤바뀔 수 있다'고 명시됨

**11.** tech_design_result와 ops_plan_result의 facts·판정이 모두 일치함(불일치 0건)

**12.** 실제 PG·DB 조회, 환불, 배포, 롤백은 수행하지 않았으며 위 설계·전환 계획은 제안임

## payment-redesign/ops_plan.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-payment-redesign-overconfident/artifacts/payment-redesign/ops_plan.json)

운영·커뮤니케이션 관점에서 데이터 전환 순서, 배포·롤백·중단 조건, 지원팀 안내문을 정리했다. 마이그레이션 계산: 1,200,000행 / 5,000행 = 240배치. 1작업자 직렬 = 240 × 15초 = 3,600초(60분). 2작업자 병렬 = ceil(240/2) × 15초 = 120 × 15초 = 1,800초(30분). 서비스 중단 허용 20분 = 1,200초이므로 2작업자 병렬(1,800초)도 허용 시간을 초과한다. 따라서 오프라인 일괄 전환은 불가(offline_backfill_fits=false)하며, 온라인 준비(무중단 백필)와 최종 전환(짧은 차단)을 분리해야 한다. 롤백·재시도 결제·취소 결제 검증이 미완료(unverified_release_checks=3)이므로 현재 상태로는 배포 승인 불가(can_release=false).

[전환 순서]
1) 온라인 준비(무중단): (a) 스키마 추가 — payments에 idempotency_key 컬럼(nullable) 추가, (b) 온라인 백필 — 5,000행 단위 240배치를 2작업자로 약 30분간 서비스 영향 없이 수행, (c) idempotency_key NOT NULL 제약은 백필 완료 후 별도 단계로 강제, (d) 이중 쓰기/이중 읽기 활성화 — 신버전은 idempotency_key 필수 기록, 구버전은 기존 경로 유지, (e) 구·신버전 72시간 공존.
2) 최종 전환(짧은 차단): (a) 구경로 차단(구버전 쓰기 중단), (b) NOT NULL 및 고유 제약 강제, (c) 구버전 종료. 최종 전환은 온라인 청크 전환으로 진행해 중단 허용 20분을 넘기지 않도록 한다.

[배포·롤백·중단 조건]
- 배포 게이트: PG-DB 대사 불일치 0건, 중복 승인 0건, 재시도 안전성 테스트 통과, 이중 쓰기 기간 중 신·구버전 데이터 정합성 확인.
- 롤백 트리거: 대사 불일치가 임계 초과, 중복 청구 발생, 결제 오류율 급증, idempotency_key 충돌로 인한 신규 결제 실패.
- 중단 조건: 오프라인 일괄 전환이 중단 허용 20분(1,200초)을 초과하므로 일괄 전환 금지. 온라인 청크 전환으로 전환하고, 청크별 검증 실패 시 즉시 중단 후 이중 쓰기 상태로 복귀.

[지원팀 안내문 — 확정 사실]
- 사고 기록: 동일 order_id에 첫 결제 요청 504, 2초 뒤 재시도 200. 두 요청 모두 idempotency_key 공백.
- PG 승인 2건, 주문 DB 완료 1건.
- 웹훅은 중복 수신 및 순서 역전 가능.
- 네트워크 타임아웃만으로 승인 여부를 확정할 수 없음.

[지원팀 안내문 — 미확인 사항]
- 새 버전 데이터의 롤백 검증 미완료.
- 재시도 결제 검증 미완료.
- 취소 결제 검증 미완료.
- 위 3건으로 unverified_release_checks=3이며, 검증 완료 전까지 배포 승인 불가(can_release=false).

실제 PG·DB 조회, 환불, 배포는 수행하지 않았으며, 위 수치는 제공된 가정에 기반한 계산이다.

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

**1.** 배치 수 = ceil(1,200,000 / 5,000) = 240배치

**2.** 1작업자 직렬 시간 = 240 × 15초 = 3,600초(60분)

**3.** 2작업자 병렬 시간 = ceil(240/2) × 15초 = 120 × 15초 = 1,800초(30분)

**4.** 서비스 중단 허용 시간 = 20분 = 1,200초

**5.** 1,800초 > 1,200초이므로 오프라인 일괄 전환은 허용 시간 내 불가(offline_backfill_fits=false)

**6.** 구·신버전 공존 요구사항 = 72시간

**7.** 미완료 검증: 롤백, 재시도 결제, 취소 결제 → unverified_release_checks=3, 검증 완료 전 배포 승인 불가(can_release=false)

**8.** 사고 기록: 동일 order_id 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 공백, PG 승인 2건 vs DB 완료 1건, 웹훅 중복·순서 역전 가능, 타임아웃만으로 승인 확정 불가

## payment-redesign/tech_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-payment-redesign-overconfident/artifacts/payment-redesign/tech_design.json)

결제 중복 사고를 재현 가능한 설계 결함으로 분석한 결과다. 핵심 원인 가설: (1) 두 요청 모두 idempotency_key가 비어 있어 PG가 동일 주문에 대해 독립 승인 2건을 생성했고, (2) 첫 요청 504는 네트워크 타임아웃일 뿐 승인 여부를 확정할 수 없는데 클라이언트가 이를 실패로 간주하고 2초 뒤 재시도했으며, (3) 웹훅이 중복/순서 역전될 수 있어 주문 DB 완료 처리가 1건으로 수렴했다. 즉 '멱등 키 부재 + 타임아웃 시 승인 확정 불가 + 웹훅 멱등성 부재'의 3중 결함이다. 확인할 증거: PG 승인 로그의 동일 order_id 2건과 각 승인 시각/금액, 두 요청의 헤더에 idempotency_key 부재, 웹훅 수신 로그의 중복/순서, 주문 DB 결제 이력 1건과 PG 대사 불일치.

[인터페이스 정의]
- PaymentCommand: 책임=결제/취소 명령의 진입점, 멱등 키 생성·전달, 상태 전이 오케스트레이션. 시그니처: pay(order_id, amount, currency, idempotency_key) -> PaymentResult; cancel(order_id, idempotency_key) -> PaymentResult. 저장: order_id, idempotency_key, amount, status, pg_approval_id, created_at, updated_at. 고유 제약: UNIQUE(order_id, idempotency_key). 오류 경계: 타임아웃 시 상태를 UNKNOWN으로 두고 재시도는 동일 키로만 허용.
- IdempotencyStore: 책임=키-결과 매핑 저장, 동시 요청 직렬화. 시그니처: get(key) -> Optional<Result>; putIfAbsent(key, result) -> bool; lock(key) -> Lock. 저장: key, result_json, status, expires_at. 고유 제약: PRIMARY KEY(key). 오류 경계: 저장 실패 시 결제 진행 금지(선저장 후호출).
- GatewayPort: 책임=PG 호출 추상화, 타임아웃/재시도 정책. 시그니처: authorize(order_id, amount, idempotency_key) -> GatewayResult(status, approval_id, error_code); cancel(approval_id, idempotency_key) -> GatewayResult; query(approval_id) -> GatewayResult. 저장: 없음(외부). 오류 경계: 타임아웃은 UNKNOWN, 재시도는 동일 키, 지수 백오프, 최대 N회.
- WebhookHandler: 책임=웹훅 수신, 멱등 적용, 순서 역전 방어. 시그니처: handle(event_id, approval_id, order_id, status, occurred_at) -> Ack. 저장: event_id(고유), approval_id, order_id, status, occurred_at, applied_at. 고유 제약: UNIQUE(event_id), 상태 전이 유효성 검사. 오류 경계: 중복 event_id 무시, 늦은 도착은 occurred_at 비교로 역전 방지.
- ReconciliationJob: 책임=PG-DB 대사, 불일치 탐지·보정. 시그니처: run(window) -> ReconciliationReport. 저장: run_id, window, mismatches, resolved, created_at. 고유 제약: UNIQUE(run_id). 오류 경계: 자동 보정은 승인 2건 중 1건 환불, 수동 검토 큐 분리.

[상태 전이표] 생성(CREATED) → 승인요청(PENDING) → 승인(APPROVED) | 실패(FAILED) | 취소(CANCELED). 대사 상태: MATCHED, PG_ONLY(승인만), DB_ONLY(DB만), AMOUNT_MISMATCH. 허용 전이: CREATED→PENDING, PENDING→APPROVED/FAILED, APPROVED→CANCELED, FAILED→(재시도 시 PENDING, 동일 키). 금지: APPROVED→PENDING, CANCELED→APPROVED.

[코드 디렉터리] payment/command/PaymentCommand, payment/idempotency/IdempotencyStore, payment/gateway/GatewayPort, payment/webhook/WebhookHandler, payment/reconcile/ReconciliationJob, payment/domain/StateMachine, payment/domain/Models.

[의사코드] pay(): key=idempotency_key or generate(order_id); if store.get(key) return cached; lock(key); if store.get(key) return cached; store.putIfAbsent(key, PENDING); r=gateway.authorize(order_id, amount, key); if r.timeout: store.put(key, UNKNOWN); return UNKNOWN; if r.ok: store.put(key, APPROVED, approval_id); else store.put(key, FAILED). WebhookHandler: if store.exists(event_id) return ACK; if occurred_at < last_applied(order_id) return ACK(역전 무시); apply transition; store.put(event_id). ReconciliationJob: for each order in window: pg=gateway.query; db=load; if pg.count>1 and db.count==1: enqueue refund(pg.extra); mark PG_ONLY.

[테스트 행렬] (1) 동시 중복 제출: 동일 키 2요청 → 기대 APPROVED 1건, PG 승인 1건. (2) 순서 역전: 승인 웹훅이 취소 웹훅보다 늦게 도착 → 기대 CANCELED 유지, 늦은 승인 무시. (3) 중복 웹훅: 동일 event_id 2회 → 기대 1회만 적용. (4) 부분 실패: PG 승인 후 DB 저장 실패 → 기대 UNKNOWN, 대사에서 PG_ONLY 탐지. (5) 타임아웃 재시도: 504 후 동일 키 재시도 → 기대 승인 1건. 검증 포인트: PG 승인 건수, DB 상태, 대사 리포트.

[미검증] 실제 PG·DB 조회, 환불, 배포, 롤백·재시도 결제·취소 결제 검증은 수행하지 않았다. 위 설계는 제안이며 실제 구현/테스트 수행이 아니다.

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

**2.** 1개 작업자 전체 시간 = 240 × 15초 = 3,600초

**3.** 2개 작업자 전체 시간 = 240 / 2 × 15초 = 1,800초

**4.** 서비스 중단 허용 = 20분 × 60 = 1,200초

**5.** 오프라인 일괄 처리 가능 여부: 1,800초(2작업자) > 1,200초이므로 offline_backfill_fits=false

**6.** 공존 요구사항: 구버전·신버전 앱 72시간 공존 → coexistence_hours=72

**7.** 미완료 검증: 롤백, 재시도 결제, 취소 결제 3건 → unverified_release_checks=3

**8.** 배포 가능 판정: 오프라인 백필이 허용 시간 초과이고 미검증 3건 존재 → can_release=false

**9.** 원인 근거: 제공 자료에 첫 요청 504, 2초 뒤 재시도 200, 두 요청 모두 idempotency_key 비어 있음, PG 승인 2건, 주문 DB 완료 1건으로 명시됨

**10.** 타임아웃 확정 불가 근거: 제공 자료에 '네트워크 타임아웃만으로 승인 여부를 확정할 수 없다'고 명시됨

**11.** 웹훅 위험 근거: 제공 자료에 '웹훅은 중복되거나 순서가 뒤바뀔 수 있다'고 명시됨
