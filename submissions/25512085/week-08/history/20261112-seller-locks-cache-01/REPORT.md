# Seller 예약·hub 잠금·JSON 캐시 로컬 수정

> 최신 사용자 재결정(2026-11-13): **committed 300초(5분), 협상 absolute cap 900초 유지**. 아래 기존 900초/600초 committed 비교는 당시의 구현·조사 기록이며 현재 설정은 마지막 「사용자 재결정 반영」 절을 따른다.

## 범위와 보존

- 작업 경로: `submissions/25512085/week-08/`만 변경. `scripts/market_kit.py`는 읽기만 했다.
- VM 접속/배포/push, 외부 네트워크, 거래, 의존성 설치, Git 설정/기존 이력 변경은 하지 않았다.
- 운영 state, `.market`, env, PEM 내용을 읽지 않았다. 기존 로그, `test-report.json`, untracked 원본도 변경하지 않았다.
- `weeks/week-08/README.md`와 `scripts/check_week08.py`는 현재 저장소에 없어 실행할 수 없었다. 전달받은 승인 명세를 기준으로 검증했다.
- 테스트는 tracked Python/shell 코드와 정책 JSON만 고유 evidence 복사본으로 옮겼다. 키/재고/hub는 합성 또는 임시 fixture이며 evidence는 gitignored다. 기존 untracked `test_market_monitor_offline.py`는 전체 tracked suite에 포함하지 않았다.
- fake HOME/USERPROFILE, 임시 디렉터리 강제 격리, socket/DNS/HTTP 전송·자식 프로세스 audit 차단, 실 저장소 접근 및 evidence 밖 쓰기 차단을 사용했다. 테스트 코드가 의도적으로 시도한 자식 프로세스 호출도 차단되었다.

## 변경 파일과 핵심 위치

| 파일 | 변경 요지 |
|---|---|
| `seller.py:76–78,942–1004` | 15분 committed 예약 파일 복구, tmp+flush/fsync+os.replace, 정리 함수, 공통 발행 경계 |
| `seller.py:1016–1023,1149,1205,1228` | fresh inventory 유지, 요청당 catalog 1개, 마지막 활동 갱신, food 중복 catalog 읽기 제거 |
| `seller.py:1085,1350` | 즉시/협상 영수증 모두 같은 durable 예약 발행 함수 사용 |
| `store.py:114–175,274–284,331–343` | 잠금 timeout 예외, monotonic 시간, migration nonblocking, 기본 mutation 대기 0.1초 |
| `store.py:367–390,769,815` | catalog signature 캐시, 문의/주문 분석 timeout skip+stderr diagnostic |
| `seller_cash_policy.py:202–220` | 경로/mtime_ns/size config 캐시, nested 설정의 defensive deepcopy |
| `market_buyer.py:1058–1085,1535–1555` | pending cleanup, 실패 분석, pending checkout 기록 잠금 실패를 분석 손실로 제한 |
| `restock.py:140–151` | 잠금 실패 시 buy=[]로 안전 거절, 다음 주기 재시도 |
| `review.py:207–213` | 정산 후 분석 잠금 실패 시 평가 skip, stars=null 진단 응답 |
| `.gitignore` | 런타임 `seller_reservations.json` 제외 |
| `test_seller_locks_cache_offline.py` | 신규 집중 25개 |
| 기존 food/floor/selection/terms/warranty 테스트 5개 파일 | 독립 재판매 시뮬레이션만 별도 예약 파일 사용; 검증 assertion과 가격/선택 조건 유지 |
| 이 history의 runner/로그/manifest/report | 기준선→red→green 및 실패 과정 보존 |

## 예약 설계 결정

`scripts/market_kit.py:352–378,389,431–439`에서 ready_for_payment 세션은 시간 만료 없이 unit을 보류한다. `BUYER_TIMEOUT=300`(81행), HTTP timeout=240(284행)은 요청/프로세스 제한이지 정산 완료 상한이 아니다. 정산 관측 상한을 입증하는 근거를 찾지 못했으므로 승인 명세의 보수적 대안인 **900초(15분)**를 채택했다. 실제 ACP 보장 시간이라고 주장하지 않는다.

1. committed는 `{unit: 발행시각}`만 저장한다. 시작 시 복구하고, 복구 파일이 손상되거나 읽을 수 없으면 빈 예약으로 진행하지 않고 fail-closed한다.
2. 발행 전 파일 교체가 성공해야 서명/발행한다. 즉시 경로와 협상 경로 모두 artifact 성공 전 실패만 rollback한다. artifact 성공 후 complete 실패는 예약을 유지하고 open은 제거한다.
3. 정리 시 예약은 **읽은 재고의 유효한 units 목록에서 사라졌거나**, 고정 발행시각 이후 900초가 지나야 해제한다. 읽기 실패/누락된 inventory `{}`는 정산 증거로 쓰지 않는다. 파일 교체 성공 뒤 메모리를 바꾼다.
4. 협상은 마지막 활동 후 90초 idle TTL, 최초 생성 후 900초 absolute cap이다. 가격 없는 응답/food 기간 질문도 활동을 갱신한다. 정리 함수는 독립 호출 가능하고 기본 호출 시점은 execute 진입으로 유지했다(별도 타이머 없음).
5. 거절 경로는 event await 전에 open을 해제한다. 세션 조건, 파서, 가격 산식, C 우선 선택은 바꾸지 않았다.

### 한계

- 15분 이후에도 ACP ready_for_payment가 남을 수 있다. **상한 이후의 ACP 세션 만료/취소는 보장하지 않는다.** kit 변경/실거래 관측은 이번 범위 밖이다.
- 예약 파일은 현재 배치 구조의 **단일 seller 이벤트 루프/프로세스**가 소유한다. 중첩 비동기 요청은 보호하지만 여러 seller 프로세스가 같은 파일을 동시에 쓰는 다중 worker 조정은 구현하지 않았다.
- 디스크 오류는 영수증 발행을 중단하며, rollback 파일 쓰기 자체가 실패하면 보수적으로 예약을 유지할 수 있다. 전원 손실 시 디렉터리 metadata fsync까지의 보장은 테스트하지 않았다.

## 모든 운영 잠금 호출처와 처리

현재 최상위 운영 Python 파일을 전수 검색했다. 아래가 모든 실제 `with file_lock` 3곳과 `with mutate_hub` 7곳이다. 보존용 history 사본은 실행 대상에서 제외한다. 테스트 호출 위치까지 포함한 검색 결과는 `lock-callsites.txt`에 있다.

| 호출 위치 | 분류 | timeout 처리 / stale snapshot 방지 |
|---|---|---|
| `store.load_hub:277` → file_lock | 초기 migration/가격 읽기 | timeout=0; 바쁘면 migration 쓰기 없이 읽은 snapshot만 반환하고 진단. 다음 locked writer가 재독해 migration |
| `store.save_hub:326` → file_lock | 명시적 저장 API | 예외 전파, `_save_hub_unlocked` 미호출. 현재 운영 호출자는 없고 fixture 초기화 등에서만 사용 |
| `store.mutate_hub:341` → file_lock | 공통 mutation primitive | HubLockTimeout을 yield 전에 발생. default=0.1초(재시도 sleep 0.04초 단위), 명시 timeout 지원. lock 미획득 시 load/save 없음 |
| `store.record_inbound_inquiry:776` → mutate_hub | seller 분석 | skip+`[hub-lock-skipped]` stderr. seller는 기존 bounded background queue를 유지해 checkout 영향 없음 |
| `store.record_buyer_order:820` → mutate_hub | buyer 주문 분석 | skip+diagnostic, None 반환. 기존 정책 snapshot을 분석 실패 때문에 저장하지 않음 |
| `market_buyer.run_attempt:1059` → mutate_hub | 실패 attempt pending 정리 | skip+diagnostic. 취소/반환을 계속하며 현재 레코드 일치 조건 유지 |
| `market_buyer.run_attempt:1081` → mutate_hub | pair-local 실패 분석 | skip+diagnostic, 성공으로 바꾸지 않음 |
| `market_buyer._run_attempt:1536` → mutate_hub | 체크아웃 pending 귀속 분석 | 검증된 receipt/accept gate를 그대로 반환, 분석만 skip+diagnostic. pending은 결제 허가가 아니며 오래된 hub를 저장하지 않음 |
| `restock.build_restock_plan:141` → mutate_hub | 구매/가격/예약 결정 | plan 본문 미실행, buy=[]+diagnostic. 기존 주기의 다음 호출에서 재시도 |
| `review.main:208` → mutate_hub | 결제 이후 평가 분석 | evaluate 미실행, stars=null 중립 응답+diagnostic. 이미 끝난 결제에는 영향 없음 |

분석 기록 손실은 의도적인 가용성 절충이며 내구성 큐 재시도는 추가하지 않았다. mutation 기본 대기를 8초에서 0.1초로 줄여 이미 검증된 체크아웃의 요청 deadline을 잠금 대기 때문에 소진하지 않도록 했다. seller 가격 읽기는 원래의 읽기 snapshot을 사용하며 lock 획득 실패 후 저장하는 fallback은 없다.

## 캐시와 결과 불변성

- catalog 키는 `(resolve(path), st_mtime_ns, st_size)`이며 프로세스 내 최대 8개 signature를 보관한다. catalog는 read-only snapshot 계약이다. inventory는 캐시하지 않는다.
- config도 같은 signature 기준이며 결과의 nested dict를 복사해 캐시 오염을 막는다. 요청 중 파일 변경에 대해 transaction snapshot을 보장하는 방식은 아니지만, 변경이 없을 때 반복 JSON 파싱은 없어진다.
- 같은 요청의 후보 선택/가격계산/live food grade는 catalog를 공유한다. 기존 hub 읽기는 신규/후속 각각 필요한 지점 1회 그대로 유지했다.
- cold/raw catalog와 warm catalog에서 유닛 선택·전체 견적 dict 동일, cold/warm 정책에서 견적 동일, mtime만 변경/size만 변경한 각각의 무효화를 테스트했다. 기존 floor/C우선/조건/food/협상 전체 회귀도 통과했다.
- mtime_ns와 size를 모두 의도적으로 동일하게 보존한 파일 변경까지 탐지하는 content hash 캐시는 범위 밖이다.

## 실제 실행과 결과

저장소 루트에서 기존 `.venv/Scripts/python.exe -B`를 사용했다. runner는 매번 새 label 디렉터리만 만들며 기존 evidence를 덮어쓰지 않는다.

```text
.venv/Scripts/python.exe -B submissions/25512085/week-08/history/20261112-seller-locks-cache-01/run_isolated.py <unique-label> <mode>
```

| 단계 / 파일 | 결과 |
|---|---|
| `baseline-all.log` | 최초 runner가 traceback 자신의 소스 읽기까지 차단해 중단; 원본 그대로 보존 |
| `baseline-v2-all.log` | 기존 696개: runner 격리 호환 문제 1 failure/14 errors/2 skips. 자식 프로세스와 temp-parent fixture 문제 |
| `baseline-confirmed.log` | 수정 전 코드 **696개, 실패 0/오류 0/skip 3**. 당시 baseline-v3 snapshot으로 재확인; 최종 runner의 baseline mode는 같은 코드의 `3f65cdd`를 로컬 git show로 복원 가능 |
| `baseline-replay.log` | 최종 runner로 `baseline-replay baseline` 실제 재실행: **696개, 실패 0/오류 0/skip 3**. `3f65cdd` 복원 및 SHA256 manifest 보존 |
| `baseline-v3-all.log` | 기존 suite+신규 red 13개: 709개, 실패 11/오류 5/skip 3(subtest 실패 포함) |
| `red-focused.log` | **13개, 실패 11/오류 5**; 예약 복구/rollback/cleanup, fail-open, 반복 parse 재현 |
| `green-01-focused.log` | 13개 통과 |
| `green-01-all.log` | 709개, 실패 154/오류 18/skip 3. 기존 독립 subtest가 재시작=빈 예약을 가정한 문제를 발견; assertion 완화 없이 예약 파일 격리로 수정 |
| `green-02-focused.log` / `green-02-all.log` | 17개 통과 / 713개 실패 0/오류 0/skip 3 |
| `final-focused.log` | 확장 집중 23개 통과 |
| `final-focused-v2.log` | **집중 25개, 실패 0/오류 0/skip 0** |
| `final-all.log` | **전체 721개, 실패 0/오류 0/skip 3** (718 실행 통과) |
| `final-compile.log` | mode=compile에서 `py_compile.compile(..., doraise=True)`: **65개 통과**, pyc는 격리 scratch에만 생성 |
| `diff-check.log` | 최초 전체 diff-check에서 raw unittest 로그 3줄의 trailing space 검출; 로그 수정 금지에 따라 그대로 보존 |
| `diff-check-final.log` | **`git diff 3f65cdd --check` exit 0**. 이 history의 `.gitattributes`만 raw `*.log -whitespace` 지정; 코드 whitespace 검사는 유지, Git config 변경 없음 |

최종 명령의 label/mode: `final-focused-v2 focused`, `final-all all`, `final-compile compile`. 각 복사본의 소스 SHA256은 동명 `*-sha256.json`에 보존했다.

전체 suite의 3 skip은 기존 launcher bash/interpreter 테스트 2개와 cross-process journal-lock 테스트 1개다. 요청의 엄격한 no-child-process 격리를 위해 명시적으로 skip했다. 신규 hub lock은 OS의 실제 독점 잠금을 같은 프로세스의 별도 descriptor로 중첩하여 fail-closed/원본 보존/migration 무대기를 검증했다. 별도 프로세스 경쟁, 실제 VM, 운영 파일, 실제 ACP 결제는 검증하지 않았다.

## 단위별 커밋

- `5235168`: 최초 격리 runner 및 실패 로그 보존
- `3f65cdd`: 결함 재현 red 테스트와 기준선 로그
- `7f0fc89`: shared store fail-closed/정적 JSON 캐시, 첫 회귀 실패 보존
- `ee8ee06`: buyer/restock/review 호출처별 안전한 timeout 처리
- `cffa4d9`: durable seller 예약/발행 경계/요청 catalog 공유
- `227443a`: 독립 시뮬레이션 예약 파일 격리 및 회귀 green
- `7c33b58`: checkout 예산 내 bounded mutation 대기/config 캐시 복사
- `d473361`: 25개 집중 테스트 및 최종 전체/compile/diff 증거 보존

보고서 자체는 별도 documentation 커밋으로 추가한다. push/VM 배포는 하지 않았다.

## 후속 리뷰 2건 — 2026-11-13

### P2: 실패 fallback을 캐시하지 않도록 수정

- `store.py:380–395`: `load_json_safe(path, {})` 대신 이 함수 안에서 read/parse 성공을 구별한다. OSError(첫 PermissionError 포함), JSON/Unicode 파싱 실패, dict/list가 아닌 최상위 값이면 호출 결과만 `{}`로 반환하고 signature를 캐시에 넣지 않는다. 유효한 빈 `{}`와 `[]`는 성공 결과로 캐시한다. 공개 API와 정상 catalog 변환 규칙은 유지했다.
- `seller_cash_policy.py:205–220`: 기존 read/parse 예외는 이미 캐시하지 않았다. 다만 JSON 최상위가 dict가 아닐 때 `{'enabled': False}`를 캐시하던 유사 문제가 있어, 이 경우도 캐시 없이 fallback만 반환하도록 수정했다. 정상 빈 dict는 캐시한다.
- `test_seller_locks_cache_offline.py:164–221`: 기존 집중 25개에 테스트 5개 추가. 정상 임시 파일에서 첫 read_text만 PermissionError, 실제 파일 수정 없이 다음 호출 성공 및 mtime_ns/size 불변을 확인한다. 파싱 실패/잘못된 타입 재시도, 정상 빈 catalog/config 캐시를 별도로 확인한다.
- baseline `review2-baseline.log`: **25개 통과**.
- red `review2-red.log`: **30개, 실패 4/오류 0**. catalog PermissionError 1건, catalog parse/type 2 subtest, policy type 1 subtest가 실패했다. policy의 PermissionError/parse 실패는 수정 전부터 통과했다.
- green `review2-green.log`: **30개 통과**.
- 전체 `review2-all.log`: **726개, 실패 0/오류 0/skip 3** (기존과 같은 자식 프로세스 skip).
- compile `review2-compile.log`: 격리 복사본 **65개 py_compile 통과**.
- 명령은 기존 runner에 각각 `review2-baseline focused`, `review2-red focused`, `review2-green focused`, `review2-all all`, `review2-compile compile`을 전달했다. 모든 label은 신규이며 원래 로그/evidence를 덮어쓰지 않았다. 소스 SHA256도 동명 manifest로 보존했다.
- 커밋: red **`f068618`**, 구현+green **`7509364`**. 전체 검증/조사 문서는 후속 documentation 커밋에 보존한다.

### P1 조사 결론: 신뢰 가능한 전체 세션 신호 없음 — seller 변경하지 않음

`scripts/market_kit.py`만 읽기 전용으로 조사했다. 실제 로컬 HTTP GET, 운영 state/.market/env/PEM/운영 로그 읽기, 네트워크/VM/배포/거래는 하지 않았다. **15분 예약 해제의 재발급 창은 미해결 P1로 남는다.** 아래 근거는 현재 소스의 동작이지 시장 backend나 실거래 관측의 보장이 아니다.

#### (a) seller가 읽을 수 있는 상태 노출 여부

| 소스 근거 | 노출과 신뢰 한계 |
|---|---|
| `market_kit.py:324–327` | sessions/terms는 키트 프로세스의 OrderedDict이며 파일 저장/복구가 없다. seller는 별도 프로세스다(1006–1012행). |
| `330–355` | `GET /acp/checkout_sessions/{id}`는 **이미 아는 session ID**에 대해 상태를 반환한다. collection GET은 목록을 주지 않고 404이며 task/unit 검색 API도 없다. |
| `420,431–440` | SID는 `cs_`+무작위 8바이트(64비트)이고 create 응답으로 구매자에게 전달된다. seller로 돌려주거나 task↔SID를 파일에 쓰는 경로가 없다. seller가 가진 unit/tid만으로 해당 GET을 구성할 수 없다. |
| `270–271,1010–1012,1026` | 로컬 Proxy도 ACP 경로를 제공한다. 하지만 pport를 알아도 SID 발견 수단이 없으므로 endpoint가 있다는 사실만으로 seller 구현에 사용할 수 없다. |
| `356–359,360–378` | 조회된 canceled/completed는 그 세션 상태만 말한다. **404는 잘못된 ID/eviction/키트 재시작을 구별하지 못하며 영수증 폐기를 뜻하지 않는다.** 조회 후 다른 session 생성과 원자적으로 묶인 신호도 아니다. |
| `94–110,367–376,417–419` | shop 로그는 정산 성공/정산 거절/checkout 거절을 기록하지만 create 시 SID/task를 남기는 로그나 취소 로그는 없다. 성공 로그도 SID/task 없이 이름·가격·ledger와 선택적으로 unit만 있다. 로그 쓰기 실패를 무시하고 20MB rotation하며 시각은 날짜 없는 HH:MM:SS다. 모든 unit의 pending/cancel 상태를 완전하게 재구성하는 journal이 아니다. |
| `831–845,868–874` | inventory는 10초 sync 및 성공 정산 후 refresh로 갱신한다(`SYNC_EVERY=10`, 84행). 재고 소멸은 기존 구현에 이미 반영된 신호다. 재고에 남았다는 사실로 unpaid 여부/유효한 영수증 존재 여부는 구별할 수 없다. |

따라서 **재고가 아직 존재하는 예약을 안전하게 해제하도록 900초 만료를 대체/보강할 새로운 신뢰 신호를 확인하지 못했다.** 로그를 추정 파싱하거나 SID를 추측하는 구현은 추가하지 않았다. 개별 세션 GET을 이용하려면 먼저 task/unit↔SID lifecycle을 완전하고 원자적으로 노출하는 키트 측 계약이 필요하며, 키트 수정은 현재 쓰기 허용 범위 밖이다.

#### (b) 500개 eviction과 unpaid 수명

- `438–439행`: **성공적으로 생성된 모든 상태의 세션을 합쳐 500개**를 유지한다. 501번째 삽입부터 가장 먼저 삽입된 항목을 FIFO로 제거한다. completed/canceled도 저장된 채 한도를 소비하며 GET/complete/cancel은 순서를 갱신하지 않는다.
- 어떤 새 세션도 이후 **500개 성공 생성**이 발생하면 FIFO에서 사라진다. 시간 조건은 없으므로 생성 유량 0이면 pending 수명은 무기한(키트 종료 전까지)이다. 다른 생성이 λ개/분으로 일정하다는 가정 아래 eviction 시간은 약 **500/λ분**이다: λ=1이면 500분(8시간20분), λ=10이면 50분, λ=100이면 5분. 이는 관측치가 아닌 소스에서 계산한 예시다.
- complete 실패는 402를 반환할 뿐 pending을 취소하지 않는다(367–369행). 성공은 completed, 명시적 cancel은 canceled가 된다. 키트 전체 재시작 시 메모리 세션이 사라지지만 이미 발급한 seller 영수증이 취소되는 것은 아니다.
- `422,437행`의 JWT에는 iat가 있지만 exp는 없고, `124–136행`의 서명/검증에도 만료 판단이 없다. 시장 backend의 iat 제한 여부는 이 소스로 확인할 수 없다. `BUYER_TIMEOUT=300`이나 buyer HTTP timeout=60(491행)은 세션 TTL이 아니다.
- `303–310행`에서 영수증 terms도 FIFO 2,000개만 남는다. 이는 취소 장치가 아니다. terms가 사라지면 create의 `own={}`가 되어 unit 조건 없이 SKU 기반 fallback pick이 가능해진다(412–416,390–397행). 따라서 eviction/재시작/terms 소멸을 영수증 무효화로 간주하면 안 된다.

#### (c) 같은 unit의 두 번째 checkout 거절 조건과 이중 정산 가능성

1. **같은 task**: 저장된 500개 세션 중 어느 상태든 같은 task가 있으면 409(`409–410행`). canceled도 task 중복은 막지만 eviction 이후에는 이 검사가 더 이상 해당 영수증을 기억하지 못한다.
2. **다른 task, 같은 unit**: 직렬 생성이고 terms에 unit이 남아 있으며, 기존 세션이 현재 sessions 안에 ready_for_payment로 남아 있으면 `held`에서 제외되어 `unit ... is not in stock for this order`로 409(`389–395,416–419행`). expired/없는 unit, SKU/acceptable 불일치도 같은 경로로 거절한다.
3. completed는 held 대상은 아니지만 정상 fresh 시장 재고에서 판매된 unit이 사라지므로 다음 pick이 거절한다. canceled/evicted는 held 대상이 아니어서 재고에 unit이 있으면 새로운 task의 checkout은 허용될 수 있다.
4. **구조적 안전 보장으로 확대할 수 없음**: `create`는 `await asyncio.to_thread(self.pick_unit, ...)`에서 양보한 뒤 session을 넣는다(416→431행). 두 동시 create가 insertion 전 같은 held snapshot을 보면 같은 unit을 각각 선택할 경쟁 창이 있다. complete도 상태 검사 후 await 동안 processing으로 바꾸지 않으므로 같은 세션의 동시 complete 요청 자체를 키트가 직렬화하지 않는다(360–370행). 이번 작업은 이 경로를 실행/재현하지 않았고, 소스상 경쟁 가능성을 식별한 것이다.
5. 최종 정산은 외부 `/api/settle`의 mandate/replay/재고 원자성 검증에 달려 있다. 해당 서버 구현/응답을 조사하지 않았으므로 **키트만으로 이중 정산이 구조적으로 불가능하다고 결론 낼 수 없다.** 직렬·세션 보존 조건에서의 2차 checkout 거절만 확인했다. 실제 중복 대금 청구가 발생했다고 주장하는 것도 아니다.

#### (d) unpaid 영수증의 ledger/inventory 흔적

- 영수증만 받고 checkout을 아직 생성하지 않았으면 키트에는 terms만 있고 market에 예약 요청을 보내지 않는다. create도 시장에는 **GET inventory만** 호출한다(384행). 이 단계에서 해당 unit의 market hold나 settle ledger를 생성하는 코드가 없다.
- unpaid 세션의 JWT/ready_for_payment는 키트 메모리에만 있다. inventory는 일반 미판매 unit과 동일하게 남으므로 seller가 inventory만으로 둘을 구별할 수 없다. canceled도 시장에 별도 취소 API를 호출하지 않고 로컬 status만 바꾼다(356–359행).
- buyer `acp_pay`가 반환하면 **그 구매자 키트의** `payments.jsonl`에 paid:false/이유가 기록될 수 있다(757–761행). SID/unit/task를 포함하지 않고 다른 구매자의 로컬 파일이므로 seller의 incoming 예약에 대한 완전한 신호가 아니다. 결제 성공 시에만 해당 구매자의 deal.json을 쓴다(762–766행).
- 이후 buyer가 `/api/results`로 실패 결과/리뷰를 제출할 수 있다(667–673,769–778행). 이 호출의 응답에는 ledger/checks/verified가 있으므로 **미결제이면 시장 ledger에 어떤 종류의 항목도 절대로 없다고 단정하지 않는다.** 세션 생성 자체에는 settle 흔적이 없고, 실패 결과 기록이 생길 가능성과 유효한 정산 ledger는 구별해야 한다. backend가 어떤 실패 항목을 보존하는지는 미확인이다.

#### 기존 history 관측 검색

tracked `history/**/*.md`에서 ACP/정산 소요시간·분포·latency/duration/p95/p99를 검색하고, tracked `history/**/*.log`의 `[acp] settled`, `checkout ships unit`, `paid`, `payment not completed`, `not paying`를 표적 검색했다. 파일명에서도 acp/latency/settle/timing/distribution을 확인했다. **예약 발행→세션 생성→정산 완료를 연결할 실제 소요시간 표본/분포는 발견하지 못했다.** `20261104-seller-cash/{before,after}/REPORT.md:19`는 settled 건수 업데이트 **예정**이라는 문구일 뿐 관측 수치가 아니다. `20261107-goods-terms-01/duration-*.log` 같은 테스트 로그는 파일명만으로 실제 정산시간 관측이라고 간주하지 않았다. 운영 로그/금지된 운영 덤프와 untracked 원본은 검색하지 않았다. 따라서 경험적 평균/p95/p99 또는 최대 정산시간을 새로 제시할 근거는 없다.

#### 세 옵션 비교 — 수치는 소스 기반 모델, 관측치 아님

기호: pending/아직 사용 가능한 영수증의 실제 잔존 수명 L, seller 상한 T. finite T만으로 생기는 잠재적 재발급 구간은 **max(0,L−T)**이며 L에 상한이 없으면 어떤 finite T도 완전 차단하지 못한다. 여기의 1시간/24시간은 비교 예시이지 확인된 ACP 수명이 아니다.

| 옵션 | 가용성/비용 수치 | 재발급 위험/장단점 |
|---|---|---|
| **(i) 15분 유지: T=900초 (현재 코드, 이번에 변경 안 함)** | 미결제 방치 unit을 약 15분 후 다음 execute에서 다시 제안 가능. L=1시간이면 잠재 구간 45분, L=24시간이면 23시간45분 | 직렬·동일 unit terms 보존·기존 pending 세션 보존이면 키트의 2차 checkout 409로 실제 결제 대신 구매자 불만/리뷰 위험이 주된 경로. 하지만 이 전제가 eviction/재시작/경쟁에서 깨질 수 있어 **실손실 위험이 이것뿐이라고 보장하지 않는다**. |
| **(ii) 더 긴 상한: 예를 들어 T=3,600초 또는 86,400초** | 1시간은 기존 대비 blocked 시간이 45분 증가, 24시간은 23시간45분 증가. L=24시간에서 1시간 상한의 잠재 구간은 여전히 23시간 | 지연 receipt에 대한 충돌 가능성을 늦추지만 finite TTL의 결함은 남는다. 500/λ eviction과 독립된 임의 상한이며 실측 근거 없이 안전하다고 부를 수 없다. 장기 방치 unit의 판매 기회/운전자금 회수가 늦어진다. |
| **(iii) 재고 소멸 전까지 무기한 보류 + 명시적 수동 해제** | automatic 시간 재발급 구간은 0. 단, 미결제 1건당 unit 1개가 무기한 잠겨 취소/이탈 고객도 판매를 영구 차단할 수 있음 | 자동 경로 안전성이 가장 강하지만 악의적 reservation 소진 및 수동 대응 부담. 수동 해제는 안전 예외이며, 단순 canceled/404만으로는 오래된 영수증의 미래 재사용이 무효화되지 않음. CLI 추가/운영 절차·영수증 무효화 계약에 대한 별도 승인이 필요하다. **현재 수동 해제 명령은 없고 이번에 구현하지 않았다.** |

보관비 모델: 기존 코드의 일 **원가×1%**(`_cash_pricing.py:30`의 `storage=cost*.01*age/86400`)를 단순 시간 비례 적용하면 원가 100,000 unit의 보관비는 15분 약 **10.42**, 1시간 약 **41.67**, 24시간 **1,000**이다. 15분 대비 추가분은 각각 1시간 **31.25**, 24시간 **989.58**이며, 무기한은 하루마다 **1,000**씩 증가한다(실제 판매/보관 종료 시점과 자금 기회비용은 별도). 이 수치는 예약 파일이 별도의 수수료를 발생시킨다는 뜻이 아니라, 미판매 보류 시간을 늘릴 때의 비용 비교다.

**판단:** 승인된 조사 조건에 따라 신뢰 가능한 새 ACP 해제 신호가 없으므로 P1 관련 seller/예약 상한/CLI는 구현하지 않았다. 현재 (i)의 알려진 위험을 남겼으며, (iii)로 바꿀지와 키트 lifecycle/영수증 무효화 계약을 추가할지는 사용자 결정이 필요하다. 어느 옵션도 이번 조사만으로 안전성이 실증됐다고 주장하지 않는다.

## 사용자 결정 반영 — committed 600초 (2026-11-13)

- `seller.py:76`의 **COMMITTED_RESERVATION_S만 900→600초(10분)**로 변경했다. `NEGOTIATION_MAX_S=900`(78행)은 원래부터 독립 상수여서 분리 작업 없이 그대로 유지했다. 협상 idle TTL 90초와 예약 해제/발행/복구 로직도 불변이다.
- `test_seller_locks_cache_offline.py:57,96`: committed **599초 유지**, 추가 2초 후 **601초 해제** 및 completion 실패 시 예약 유지 시나리오 사이의 정리 대기를 **901→601초**로 바꿨다. 협상 세션 cap 테스트의 시간은 변경하지 않았다.
- 근거는 **사용자 제공 VM 실측**: 58분 표본에서 발급→정산 최대 **9초**, **2분 초과 0건**, 미결제 **4/24 전부 이탈**. 600초는 제공된 표본 최대의 약 66.7배다. 이번 작업은 VM/운영 데이터에 접근하거나 표본을 독립 검증하지 않았다. 이전 조사에서 실측 자료를 찾지 못했다는 기록과 구별한다.
- `POLICY.md`에 같은 수치와 협상 900초 유지를 명시했다. ACP의 세션 만료 없음/eviction/경쟁과 finite 상한 뒤 재발급 위험은 이 변경으로 해소되지 않는다. 10분은 시장/키트의 보장 시간이 아니라 사용자 결정이다.
- baseline `cap600-baseline.log`: 집중 **30개 통과**. red `cap600-red.log`: 기존 900초 코드에 새 시간 테스트 적용 시 **30개 중 실패 2/오류 0**. 실패 로그/manifest는 `bcb7ea6`에 보존했다.
- green `cap600-green.log`: 집중 **30개 통과**. `cap600-all.log`: 전체 **726개, 실패 0/오류 0/skip 3**(기존 자식 프로세스 skip). `cap600-compile.log`: 격리 복사본 **65개 py_compile 통과**. runner 인자는 각각 `cap600-green focused`, `cap600-all all`, `cap600-compile compile`이며 신규 고유 evidence/로그/SHA256 manifest를 사용했다.
- `git diff a058e45 --check` 통과. source diff는 상수 한 줄 및 테스트 숫자 두 곳뿐이며 협상 상수/로직은 변경하지 않았다. VM 접속/배포/push는 하지 않았다.
- 구현·정책·green 검증 커밋: **`92ce2e0`**. 이 보고서 갱신은 별도 documentation 커밋이다.

## 사용자 재결정 반영 — committed 300초 (2026-11-13)

- `seller.py:76`의 **COMMITTED_RESERVATION_S만 600→300초(5분)**로 변경했다. 별도 상수 `NEGOTIATION_MAX_S=900`(78행)과 idle TTL 90초 및 나머지 예약 로직은 그대로다.
- `test_seller_locks_cache_offline.py:57,96`: **299초 유지·301초 해제** 및 시나리오 사이 정리 대기를 301초로 갱신했다. 협상 cap 테스트는 변경하지 않았다. `POLICY.md:739,741`도 300초로 갱신했다.
- 근거는 동일한 **사용자 제공 실측**(58분 표본, 정산 최대 9초, 2분 초과 0건, 미결제 4/24 전부 이탈)이다. 300초는 표본 최대의 약 33.3배이며 ACP 만료 보장은 아니다. 이번 작업에서 VM 접속/원본 재측정/배포/push는 하지 않았다. 상한 이후 재발급 위험은 남는다.
- baseline `cap300-baseline.log`: 집중 **30개 통과**. red `cap300-red.log`: 600초 코드에 새 테스트를 적용하면 **30개 중 실패 2/오류 0**. red 커밋 **`5cccf66`**에 로그/manifest를 보존했다.
- green `cap300-green.log`: 집중 **30개 통과**. 전체 `cap300-all.log`: **726개, 실패 0/오류 0/skip 3**(기존 자식 프로세스 skip). `cap300-compile.log`: 격리 복사본 **65개 py_compile 통과**. 기존 runner에 각각 `cap300-green focused`, `cap300-all all`, `cap300-compile compile`을 전달했고 새 evidence/로그/SHA256 manifest만 생성했다.
- `git diff fdeeea2 --check` **exit 0**(`cap300-diff-check.log`). 구현·정책·green 증거 커밋은 **`fea6951`**이며 보고서는 별도 documentation 커밋이다. 기존 로그/원본과 협상 cap은 변경하지 않았다.
