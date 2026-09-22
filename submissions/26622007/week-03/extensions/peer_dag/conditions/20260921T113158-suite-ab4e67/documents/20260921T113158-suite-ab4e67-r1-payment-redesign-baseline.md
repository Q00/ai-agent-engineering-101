# payment-redesign / baseline / 1회

상태: succeeded. 필수 facts: 8/8.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## payment-redesign.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-payment-redesign-baseline/artifacts/payment-redesign.json)

결제 중복 사고를 재현 가능한 설계 문제로 정리했다. [원인 가설] 첫 요청이 504로 끝났을 때 클라이언트/서버가 승인 여부를 확정하지 못한 채 2초 뒤 재시도했고, 두 요청 모두 idempotency_key가 비어 있어 PG가 별개 승인 2건을 만들었다. 주문 DB는 완료 1건만 기록해 PG-DB 불일치가 발생했다. 504는 게이트웨이 타임아웃일 뿐 승인 실패를 의미하지 않으므로 타임아웃만으로 승인 여부를 확정하면 안 된다. [확인할 증거] PG 승인 2건의 거래 ID·승인 시각·상태, 주문 DB 결제 레코드와 상태 이력, 두 요청의 헤더/본문(idempotency_key 부재 확인), 웹훅 수신 로그의 이벤트 ID·타임스탬프·순서, 게이트웨이 타임아웃 로그. [컴포넌트 설계] PaymentCommand: 결제 의도(order_id, amount, currency, idempotency_key 필수)를 검증·정규화하고 IdempotencyStore와 GatewayPort를 조율하는 유스케이스. IdempotencyStore: (merchant_id, idempotency_key) UNIQUE 제약으로 키→결제시도 매핑을 저장하고, 동일 키 재요청 시 기존 결과를 반환(진행 중이면 IN_PROGRESS 반환). GatewayPort: authorize/cancel/query 인터페이스, 타임아웃 시 UNKNOWN 반환(성공/실패로 단정 금지), 재시도는 멱등 키 전달. WebhookHandler: 이벤트 ID 기반 중복 제거(processed_events UNIQUE), 순서 역전 대비 상태 머신 적용(늦게 온 이벤트가 최신 상태를 덮어쓰지 않도록 버전/타임스탬프 비교). ReconciliationJob: PG 조회 결과와 주문 DB를 대사해 불일치(승인 2건 vs 완료 1건)를 탐지·보고하고, 자동 취소/환불은 정책 승인 후에만 수행. [상태 전이표] INITIATED→AUTHORIZING→(AUTHORIZED|FAILED|UNKNOWN); UNKNOWN→(조회로 AUTHORIZED|FAILED); AUTHORIZED→(CAPTURED|CANCELED); CAPTURED→(REFUNDED|PARTIALLY_REFUNDED); CANCELED/REFUNDED는 종결. 중복 승인 탐지 시 DUPLICATE_SUSPECTED→RECONCILING→(CANCELED|REFUNDED|RESOLVED). [코드 디렉터리] src/payment/{command,idempotency,gateway,webhook,reconciliation}/, migrations/, tests/{duplicate,out_of_order,partial_failure}/. 핵심 의사코드: PaymentCommand.execute(cmd){ key=cmd.idempotency_key; if(!key) reject; rec=store.getOrCreate(key); if(rec.status==DONE) return rec.result; if(rec.status==IN_PROGRESS) return CONFLICT; rec.status=IN_PROGRESS; r=gateway.authorize(cmd,key); if(r==TIMEOUT) rec.status=UNKNOWN else rec.status=r.status; store.save(rec); return rec.result; }. [데이터 전환] 온라인 준비(스키마 추가, 신규 컬럼 nullable, 이중 쓰기/읽기 검증)와 최종 전환(제약 활성화, 구경로 차단)을 분리한다. 배치 수=1,200,000/5,000=240. 1작업자=240*15=3,600초=60분. 2작업자=1,800초=30분. 중단 허용=20분=1,200초. 30분>20분이므로 오프라인 일괄 전환은 허용 시간 내 불가. 따라서 온라인 백필(중단 없이) 후 짧은 최종 전환(제약 활성화·트래픽 스위치)만 중단 창에 배치해야 한다. [테스트 행렬] 중복 요청(동일 키 동시 2건→1건만 승인), 순서 역전(웹훅 늦은 도착→상태 미덮어쓰기), 부분 실패(승인 후 DB 저장 실패→대사로 복구), 타임아웃 후 재시도(UNKNOWN→조회 확정). [배포·롤백·중단 조건] 카나리 배포, 멱등 키 강제 후 구버전 72시간 공존(coexistence_hours=72). 롤백은 신버전 데이터 스키마 되돌리기와 트래픽 구경로 복귀. 중단 조건: 중복 승인률 임계 초과, 대사 불일치 급증, 롤백 검증 미완료 상태에서 전체 전환. [미검증] 신버전 데이터 롤백·재시도 결제·취소 결제 검증 3건 모두 미완료이므로 can_release=false. 실제 PG·DB 조회, 환불, 배포는 수행하지 않았고 미검증을 통과로 바꾸지 않았다. [지원팀 브리프 통합] 확정 사실: 동일 order_id PG 승인 2건, 주문 DB 완료 1건, 두 요청 모두 idempotency_key 비어 있음, 첫 요청 504 후 2초 뒤 재시도 200. 미확인: 실제 PG 승인 시각·취소 가능 여부, 웹훅 도착 순서, 롤백·재시도·취소 검증. 고객 안내는 중복 청구·환불을 단정하지 않고 '확인 중' 표현과 안내 채널·시점을 명시한다. 상담 질문: 주문번호, 결제 시각, PG 승인 내역 확인 여부, 중복 청구 인지, 환불 요청 여부, 증빙 제공 가능 여부.

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
  "db_completed_records": 1,
  "idempotency_key_present": false,
  "first_request_status": 504,
  "retry_delay_seconds": 2,
  "retry_request_status": 200,
  "migration_rows": 1200000,
  "batch_size": 5000,
  "batch_seconds": 15,
  "max_workers": 2
}
```

### Evidence

**1.** 배치 수 = 1,200,000 / 5,000 = 240

**2.** 1작업자 시간 = 240 * 15초 = 3,600초 = 60분

**3.** 2작업자 시간 = 3,600 / 2 = 1,800초 = 30분

**4.** 중단 허용 = 20분 = 1,200초; 2작업자 1,800초 > 1,200초이므로 오프라인 일괄 전환 불가(offline_backfill_fits=false)

**5.** 제공 합성 기록: 동일 order_id에 PG 승인 2건, 주문 DB 완료 1건, 두 요청 모두 idempotency_key 비어 있음, 첫 요청 504 후 2초 뒤 재시도 200

**6.** 제공 자료: 웹훅은 중복·순서 역전 가능, 네트워크 타임아웃만으로 승인 여부 확정 불가

**7.** 제공 자료: 구버전·신버전 앱 72시간 공존 요구(coexistence_hours=72)

**8.** 제공 자료: 신버전 데이터 롤백·재시도 결제·취소 결제 검증 모두 미완료 → unverified_release_checks=3, can_release=false

**9.** 지원팀 브리프(하위 결과): 확정 사실 4건과 미확인 6건을 구분하고 고객 안내 문구·상담 질문 목록을 포함하며, 실제 PG·DB 조회·환불·배포는 미수행으로 명시

## payment-redesign/support_brief.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-payment-redesign-baseline/artifacts/payment-redesign/support_brief.json)

지원팀 전달용 운영·커뮤니케이션 브리프. [확정 사실] ① 동일 order_id에 PG 승인 2건이 존재한다. ② 동일 order_id에 주문 DB 완료 레코드는 1건이다. ③ 첫 결제 요청과 2초 뒤 재시도 요청 모두 idempotency_key가 비어 있다. ④ 첫 요청은 504(게이트웨이 타임아웃)로 응답했고, 2초 뒤 재시도 요청은 200으로 응답했다. 위 4건은 제공된 합성 기록에 근거한 확정 사실이며, 그 외 해석(중복 청구 확정, 환불 대상 확정 등)은 하지 않는다. [미확인 사항] ① 실제 PG 승인 시각(두 승인의 발생 시각·순서) — 제공 기록에 시각 값이 없어 미확인, 확인 주체: 결제 운영 담당 + PG사, 방법: PG 승인 내역 조회(거래 ID·승인 시각). ② 취소 가능 여부 — PG 승인 상태·정산 상태에 따라 달라지며 조회 전 미확인, 확인 주체: 결제 운영 담당, 방법: PG 취소 가능 상태 및 정산 여부 조회. ③ 웹훅 도착 순서 — 웹훅은 중복·순서 역전이 가능하다는 전제만 있고 실제 도착 순서 로그는 미확인, 확인 주체: 서버/결제 개발팀, 방법: 웹훅 수신 로그 및 이벤트 ID·타임스탬프 대조. ④ 신버전 데이터 롤백 검증 — 미완료. ⑤ 재시도 결제 검증 — 미완료. ⑥ 취소 결제 검증 — 미완료. ④~⑥은 확인 주체: 결제 개발팀, 방법: 검증 테스트 수행 후 결과 회신. [고객 안내 문구 초안] "안녕하세요, 고객님. 문의하신 주문(주문번호: ___)의 결제 내역을 확인하고 있습니다. 현재 시스템상 결제 승인 기록과 주문 완료 기록 간 차이가 확인되어, 중복 결제 여부 및 처리 방향을 확인 중에 있습니다. 확인이 완료되는 대로 결제 내역과 처리 결과(정상 처리 또는 환불 등)를 다시 안내드리겠습니다. 추가 결제나 환불 절차를 먼저 진행하지 않으셔도 되며, 확인 완료 시점에 별도로 연락드리겠습니다. 문의: 고객센터 ___." — 중복 청구 가능성과 환불 절차를 단정하지 않고 '확인 중' 표현을 사용했으며, 안내 채널은 고객센터(전화/이메일/앱 알림 중 가용 채널), 시점은 '확인 완료 시점'으로 명시. [상담 시 확인 질문 목록] ① 주문번호(order_id)가 무엇인가요? ② 결제를 시도한 시각(대략적 시각 포함)은 언제인가요? ③ 결제 수단과 PG 승인 내역(문자/앱 알림 등)을 확인하셨나요? ④ 동일 금액이 두 번 청구된 것으로 보이시나요? ⑤ 환불을 요청하시는지, 아니면 확인 후 안내를 원하시는지? ⑥ 결제 관련 추가 문의나 증빙(캡처 등)을 제공해 주실 수 있나요? [운영 절차] 상담 접수 시 위 질문으로 사실 확인 → 결제 운영 담당에게 order_id 전달 → PG 승인 내역·취소 가능 여부 확인 → 확인 결과에 따라 고객 재안내. 확정 전에는 환불 진행·중복 청구 단정 금지.

### Facts

```json
{}
```

### Evidence

**1.** 제공된 합성 기록: 동일 order_id에 PG 승인 2건, 주문 DB 완료 1건, 두 요청 모두 idempotency_key 비어 있음, 첫 요청 504 후 2초 뒤 재시도 200 — 이 4건을 확정 사실로 인용.

**2.** 미확인 사항 근거: 실제 PG 승인 시각·취소 가능 여부·웹훅 도착 순서는 제공 자료에 값이 없고, 신버전 데이터 롤백·재시도 결제·취소 결제 검증은 '모두 미완료'로 명시되어 있어 미확인으로 분류.

**3.** 고객 안내 문구는 중복 청구 가능성과 환불 절차를 단정하지 않고 '확인 중' 표현과 안내 채널·시점을 명시하는 요구사항에 따라 작성.

**4.** 상담 질문 목록은 주문번호, 결제 시각, PG 승인 내역, 중복 청구 인지 여부, 환불 요청 여부를 포함하도록 task.acceptance에 따라 구성.

**5.** 본 작업은 문서 정리이며 실제 PG·DB 조회, 환불, 배포는 수행하지 않았고 미검증 항목을 통과로 바꾸지 않음.
