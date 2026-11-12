# Seller 예약·hub 잠금·JSON 캐시 로컬 수정

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
