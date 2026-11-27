# Buyer outcome link v1 — 로컬 구현, 미배포 (2026-11-09)

## 범위 / 근거

가격·순위·재시도·보증·매입·판매 로직은 변경하지 않는다. 새 네트워크 호출,
폴링, 과거 backfill, 주문/거래 실행은 없다. 이 기록만으로 Saved나 순위 개선을
보장하지 않는다. 실제 수집 시작 시각은 **미배포이므로 아직 없음**이다.

공유 소스는 읽기만 했다. 원본 runtime/log/key는 검증 입력으로 사용하지 않았다.
`weeks/week-08/README.md`는 이 checkout에 없으며 `week-08.html`이 있다.

확인한 소스 경로 (수정 전 HEAD d91e1bb 기준):

- `scripts/market_kit.py:753-767`: 선택한 receipt와 별개로 AP2 성공 후에만
  payments `paid:true`, `order`, `ledger`가 생성되고 review가 실행된다.
  receipt 선택/result 자체는 실제 결제가 아니다.
- `scripts/market_kit.py:920-940`: 원장 broken의 `buyer`를 joined buyer와 정확히
  비교한 뒤 로컬 broken.jsonl에는 **name와 broke_at만** 남긴다.
  이 파일로 새 연결을 만들지 않는다.
- `store.py:382-390,530,568-575`: 기존 수신 원장 settle의 `data.order`,
  amount, item/sku, grade, warranty_hours, seq, ts 사용 근거.
- `store.py:450-459`, `seller_cash_policy.py:148-185`: broken의 정확한 order 참조.
- `seller_cash_policy.py:228-241`: broken.refunded 및 settle amount 스키마 근거.
- `store.py:60-61,534`: 원장의 `buyer`는 시장 buyer 식별 이름이며 OWN_NAME과
  정확 비교한다. 상품명/시각/판매자명 유사성으로 조인하지 않는다.
- `store.py:601-615`: 기존 이름 기반 shop_trust 귀속은 **기존 정책 경로로 남아
  있으며 이번에 수정하지 않음**. 새 journal/summary는 이를 읽거나 신뢰하지 않는다.
- `review.py:98-114`: 구매 이력 150건 한계. 새 journal은 hub/my_purchases,
  seller break_observer 및 기존 hub compaction과 독립이다.

추가 수집 지점은 `store.apply_ledger_rows` 시작 한 곳이다. 기존에 이미 수신한
원장 목록을 `buyer_outcomes.prepare`로 최소 allowlist snapshot만 복사한다.
`mutate_hub`의 최외곽 hub lock 해제 후 `append_events`로 동기 기록한다.
wrapper/AP2/receipt 후킹은
불필요하여 변경하지 않았다. review만 관측되고 원장이 아직 안 들어온 거래는
새 cohort에 아직 없다. 임의로 review price를 settle amount로 대체하지 않는다.

### P2 후 호출·저장 흐름

루트 Python 호출자 전체 검색 결과 런타임 `apply_ledger_rows` 호출은
`restock.py:155` 한 곳이다 (나머지는 테스트). 기존 코드 기준 흐름:

1. `restock.build_restock_plan:102-139`: catalog/inventory와 hub snapshot 읽기,
   **기존** ledger/identity/public fetch 후 신선도 계산. 조건부 기존 과거-window
   supply fetch는 원래 정책 경로이며 이번 변경으로 추가/확대하지 않았다.
2. `restock.py:140-147`: `mutate_hub` 진입 → own URLs/예약 기초 갱신 →
   `return plan_with_hub(...)` 평가. `plan_with_hub:150-155`가 원장을 적용하고
   나머지 정책/예약을 계산한다. 이 단계에서 journal 파일 열기/쓰기/fsync는 없다.
3. `store.mutate_hub`: context-local batch에 복사한 증거를 보관하며,
   기존 `_save_hub_unlocked`와 기존 `file_lock` 해제가 끝난 **뒤에** 최외곽
   context가 동기 append/flush/fsync한다. 중첩된 다른 hub context도 바깥 context
   종료 전에는 flush하지 않는다. daemon/background queue는 사용하지 않는다.
4. producer의 정책 결과 반환은 위 동기 append 완료 뒤다. 따라서 느린 I/O가
   **다른 hub writer의 잠금을 붙잡지는 않지만 producer 반환/다음 거래 처리는
   지연될 수 있다**. 전략에 완전 무지연을 보장하지 않는다. 기존 hub의 8초 뒤
   잠금 우회/저장 실패 처리 자체는 수정하지 않았다.

`save_hub`, load 시 migration/compaction, buyer/review/seller의 다른 `mutate_hub`
호출은 원장 적용을 하지 않아 새 batch/journal I/O가 없다. 독립적인
`apply_ledger_rows(hub,...)` 호출은 이제 in-memory-only다. 직접 호출자가 영속
관측을 원하면 동일한 `mutate_hub` 계약을 사용해야 한다. 수동 outer `file_lock`
안에 mutate_hub를 감싸는 호출은 확인된 런타임에 없으며 이 계약 밖이다.
이미 수신한 정산 증거는 hub 정책 코드가 예외로 종료돼도 lock 해제 후 기록한다.
hub 저장과 journal 사이 원자적 트랜잭션은 아니므로 그 사이 crash시 관측 손실은
여전히 가능하다. pending 증거는 hub JSON에 저장하지 않는다.

## JSONL schema v1

향후 실행 시 학생 디렉터리 `runtime/buyer-outcomes-v1.jsonl`에 append한다.
이 경로는 새 기록이며 기존 `.market`, state_hub, logs를 변경하지 않는다.

- 공통: `version:1`, `kind:settle|broken`, `buyer`(정확 OWN_NAME 일치),
  `order`(정확 원장 order), `seq`(원장 event ID), `ts`(원장 시각),
  `observed_at`(수신 로컬 시각), `unit`(원장이 명시한 unit만, 없으면 null).
- settle: `sku`(원장 sku/item), `paid_amount`(**settle.amount**),
  `list_price`(그 수신 시점 catalog.list_price snapshot),
  `list_price_source:catalog_at_observation|null`, `grade:A|B|C|null`,
  `warranty_hours`, `goods`(정확 goods SKU 형식 여부).
- broken: `refunded_amount`(명시적 원장 refunded만; 누락은 null, 0 추정 금지).
  해당 event ts/seq가 고장 및 환불 보고의 시각/근거이며 별도 refund 발생 시각은
  이 스키마로 확인할 수 없다. 알 수 없는 별도 refund event는 파싱하지 않는다.
- coverage: `version:1`, `kind:coverage`, `observed_at`, `complete:false`,
  `unlinked`(no_identity/no_order/no_ledger_or_timestamp/unknown_price/
  invalid_data/batch_cap_unknown 관측 횟수). 중복 batch 재수신 시 이 카운터는
  늘 수 있으므로 고유 거래 수가 아니다. 자기 buyer인지를 모르는 no_identity는
  전체 관련 settle/broken 수신 중 식별 불가 수이며 자기 구매 수로 해석하지 않는다.

문자열은 ID 형식 `[A-Za-z0-9_.:-]{1,128}`만, 숫자는 유한 비음수 <=10^12만.
원문 receipt, JWT/AP2/서명, env, 예산, 주소/URL, 상품 이름, 오류 원문은 저장하지 않는다.
서버가 field를 주지 않으면 null/미연결이며 역추정하지 않는다.

### P2 replay 검증 경계 / 하위 호환

`read_events`와 직접 `summarize(iterable,...)` 모두 동일 `validate_event`를
통과한 **allowlist 사본만** 사용한다. 알려진 필드의 타입/범위 위반, 필수
version/kind/observed_at 또는 buyer/order/seq/ts 누락은 corrupt_unknown이다.
version은 bool 아닌 정수 1, kind는 정확한 허용 문자열, seq는 bool 아닌 정수
0..10^12, 시각은 유한 비음수 <=10^12다. bool/NaN/Inf/음수/컨테이너를
가격·시각으로 받아들이지 않는다. ID는 ASCII 최대 128자로 제한한다.
unit/sku와 가격/grade/warranty/refund는 null 허용이며, goods는 정확 bool과
SKU goods 여부가 일치해야 한다. 양수 list_price는 snapshot 출처를 요구한다.
coverage는 complete가 정확 false이고 unlinked가 dict여야 하며 알려진 6개
counter는 bool 아닌 정수 0..10^12다. 미지의 필드와 counter key는 무시하며
원문/민감 필드를 결과에 보존하거나 오류에 전사하지 않는다.

기존 구현이 생성한 **version 1 JSONL은 그대로 읽는다** (이전 nullable 필드도
지원). version 누락/0/2는 추정 migration하지 않고 corrupt_unknown이다. 유효
JSON이어도 스키마가 틀리면 한 행만 corrupt_unknown으로 세고 다음 정상 행을
계속 처리한다. 직접 iterable의 non-dict도 동일하다. iterable 자체가 잘못됐으면
corrupt_unknown, iterator 중간 예외는 iterator_error_unknown/truncated로 남긴다.
문법 오류/깊은 JSON/너무 긴 행도 원문을 고치지 않고 corrupt_unknown이다.

## Replay 의미 / 한계

`read_events(path)` -> `summarize(events, OWN_NAME, now)`는 오프라인 함수다.
동일 kind+seq는 첫 기록만 사용한다. 따라서 최초 snapshot 가격과 출처가 유지된다.
broken이 settle보다 먼저 수신돼도 같은 order로 연결한다. buyer 타인(자기 가게의
환불 포함)은 배제하고 unit 양쪽 명시시 불일치를 unknown으로 처리한다.
복수 settle/복수 broken(서로 다른 seq)은 의미를 추정하거나 환불액을 합산하지
않고 conflicting_events로 둔다. 원장 broken 시각이 settle보다 앞서도 unknown이다.

- `surface_discount=(list-paid)/list`는 표면 할인율일 뿐 realized Saved가 아니다.
- goods의 알려진 고장: refunded==paid>0은 기여 0, refunded==0은 -paid/list.
  부분환불, 환불 누락/초과, 가격 누락은 기여 null. 무보증도 손실 0으로 보지 않는다.
- food는 served/value가 확인되지 않아 goods 수식을 적용하지 않는다.
- `mature_6h`는 settle 시각+6h 경과만 뜻한다. A72도 실제 고장 추적 창 6h 기준이다.
  뒤늦은 broken 수신은 저장/반영하며 단순히 성숙했다는 이유로 폐기하지 않는다.
- 기존 fetch는 실패와 빈 응답을 모두 []로 반환하고 연속 coverage 증명이 없다.
  그러므로 **모든 observation_complete/complete는 false**이고, 고장 미관측을
  무고장 완료로 단정하지 않는다. `official_saved`는 항상 null이다.
  공식 분모 deals+100 및 unserved=-1을 복원할 전체 주문/served 기록도 없다.
- unknown_reason 및 coverage의 broken_without_settle로 미연결을 구분한다.
  과거 이미 잘린 이력/손실은 복원 불가. 이번에는 보수적 최소 계측만 제공하며
  완전한 6h 순Saved/coverage 증명은 미해결이다.

## 용량 / 동시성 / 손상

삭제/rotation/TTL 없이 256 MiB까지 append-only. 기간이 아닌 용량 한도이므로
24~72h 또는 11/17까지 보관 가능성을 보장하지는 않는다. 한도 전까지 원문은
라운드 종료 후에도 유지한다. 포화시 기존 증거를 지우지 않고 stderr에 고정된
`journal_capacity_unknown`을 출력한다. 운영 배포 전 예상량/디스크 여유 검토 필요.

batch는 기존 rows 중 최대 2,000행을 검사하고 초과 수는 batch_cap_unknown이다.
replay는 최대 200,000 이벤트, 각 최대 4,096 bytes 읽기, settle/broken 각각 최대
20,000 order와 최대 200,000 dedup key로 메모리를 제한한다. TTL이 아닌 상한이다.
한도 초과는 event_cap_unknown 또는 pending_eviction_unknown을 명시한다.
event cap으로 prefix만 읽었거나 index eviction/iterator 오류가 발생하면
`truncated:true`, `complete:false`, 각 cohort의
`unknown_reason:replay_truncated_unknown`, `known_broken_contribution:null`로
명시한다. 200,000행 뒤의 broken 미반영을 완성된 무고장 관측으로 반환하지 않는다.
정확히 200,000행이고 EOF이면 truncated=false지만 coverage unproven이므로 여전히
complete=false다. eviction 후 연결은 완전한 결과가 아니다. 거대한 실제 입력
객체 자체는 기존 수신자의 책임이다. replay 결과를 공식 집계로 쓰지 않는다.

동일 프로세스 thread lock + 별도 파일 nonblocking OS lock을 쓴다. 다른 process가
잠금을 보유하면 **잠금 우회 없이** 안전 진단 후 포기한다. fsync 후 반환하나
디스크/프로세스 강제 종료시 손실은 가능하다. I/O/lock 실패는 고정 stderr 진단
`journal_write_failed_unknown`만 출력하며 거래/정산을 중단시키지 않는다.
진단만 남고 영구 journal에 손실 표식을 못 쓸 수 있으므로 완전성 주장 금지.
tail 손상은 기존 bytes를 고치지 않고 개행만 추가해 이후 행과 분리한다.
replay는 corrupt_unknown을 반환한다. 중복 append는 허용하되 replay가 dedup한다.
원본 journal을 정리/수정하지 않는다.
thread lock은 RLock으로 동일 thread 재진입 때 deadlock하지 않으며 중첩된 파일
잠금은 OS lock이 fail-closed로 거절한다. 병렬 thread는 직렬화하고 다중 process
충돌은 진단 후 포기한다. capture 실패는 hub lock 해제 뒤 고정
`journal_prepare_failed_unknown` 진단으로 남긴다.

## 격리 검증

`check.py`는 tracked Python + 이번 신규 소스만 고유 evidence tree로 복사한다.
untracked 사용자 테스트/운영 파일은 복사하지 않는다. 환경은 최소 whitelist만,
키/API 실행 없음. child audit은 외부 socket, 원본 repo/runtime 읽기, 격리 경로
외 쓰기를 금지한다. 임시 fixture도 이 학생 하위 고유 경로만 사용한다.
실행 명령/결과는 이 디렉터리의 고유 `.log`에 원문 보존한다.

```powershell
.\.venv\Scripts\python.exe -B submissions/25512085/week-08/history/20261109-buyer-outcome-link/check.py focused test_buyer_outcomes_offline.py
.\.venv\Scripts\python.exe -B submissions/25512085/week-08/history/20261109-buyer-outcome-link/check.py all
```

첫 검증: focused-739ce7d878a7.log **26 tests OK**;
all-9f55e66186e9.log **600 tests OK** (최종 추가 테스트 이전).
최종 검증: final-focused-2f86a0a82e1d.log **32 tests OK** (0.471s),
final-all-358deaa0f77c.log **606 tests OK** (27.697s, 신규 32개 포함).
`git diff --check -- submissions/25512085/week-08/store.py`도 통과했다
(Windows LF→CRLF 안내 경고만 있음). 원본의 기존 dirty 로그/test-report와
다른 untracked 파일은 수정하지 않았고 commit/push는 실행하지 않았다.
실제 VM/외부망/AP2/장기 운영/다중 process crash 또는 파일시스템 전원 장애는
실행하지 않았다. 별도 주간 check_week08.py는 이번 검증에 실행하지 않았다.

### P2 회귀 검증 (원문 로그 추가 보존)

수정 전 `check.py p2-before test_buyer_outcomes_p2_offline.py`:
`p2-before-a4e32caf6cc6.log` — 3 tests, failure 1/errors 4로 재현.
느린 fsync가 동시 hub update를 막는 실패, missing ts KeyError,
seq list TypeError, coverage 잘못된 타입 및 truncated 표식 누락을 확인했다.
실패 로그와 당시 고유 source tree는 수정하지 않았다.

수정 후 `check.py p2-after test_buyer_outcomes*offline.py`:
`p2-after-6a1189213536.log` — **47 tests OK**;
`check.py p2-all`: `p2-all-8639a87ef82c.log` — **621 tests OK**.
느린 fsync 중 별도 writer의 실제 hub 저장, context 중첩/예외 종료,
snapshot 불변성, 동일 thread journal 재진입, 별도 Python process의 동일 journal
OS lock 충돌, valid-JSON 스키마 위반과 후속 정상 행, v1 호환, 민감필드 제거,
prefix/eviction/iterator 오류의 explicit unknown을 격리 fixture로 검증했다.
이는 실제 운영 디스크의 최대 지연이나 process crash 복구를 검증한 것은 아니다.

최종 코드 재검증 명령:

```powershell
.\.venv\Scripts\python.exe -B submissions/25512085/week-08/history/20261109-buyer-outcome-link/check.py p2-verified-focused test_buyer_outcomes*offline.py
.\.venv\Scripts\python.exe -B submissions/25512085/week-08/history/20261109-buyer-outcome-link/check.py p2-verified-all
```

- `p2-verified-focused-295b43d8ee71.log`: **48 tests OK** (0.394s).
- `p2-verified-all-dbf024cb86ae.log`: **622 tests OK** (23.506s, 신규 P2 16개 포함).
- 실제 `restock.build_restock_plan` 전체 store update 흐름에서 hub 영속 저장
  이후 append되는지, 기존 plan 결과와 fetch 횟수가 유지되는지도 추가 검증했다.
- `git diff --check -- submissions/25512085/week-08/store.py` 통과
  (기존 Windows 줄바꿈 안내만 있음). 로컬 격리 검증만 수행했으며 기존 원본
  데이터/dirty 로그/운영 runtime/키는 테스트에 사용하거나 수정하지 않았다.
  VM 접속·실거래·설치·commit·push는 하지 않았다.
# 메인 검증

- 독립 리뷰의 hub 잠금 중 journal I/O 및 schema 손상행 재생 문제를 보완했다. 실패 재현 기록도 보존한다.
- 메인이 격리 runner를 별도로 실행했다: `main-verification-196514c8d10c.log`, 전체 622개 통과.
- 변경 소스·테스트·실행 도우미·문서·원문 로그만 로컬 커밋한다. evidence의 복제 테스트 트리와 기존 사용자 변경은 포함하지 않는다.
- VM 미배포이므로 운영 수집은 아직 시작하지 않았다. 연속 원장 관측 증명이 없으면 공식 Saved와 완료 여부는 계속 unknown이며, 기록 추가 자체가 순위 개선을 보장하지 않는다.
