# 로컬 협상 계측 / VM 자원 읽기 전용 점검

## 범위와 변경

- 승인 범위: 협상 전략 변경 없이 Round 3 per-turn 관측 추가, 로컬 테스트 및 VM 자원 조회. **미배포**.
- 시작 HEAD `c819c25`. 원래 dirty `logs/market-test.log`, `test-report.json` 및 모든 사용자 untracked/evidence는 수정·stage하지 않았다. 키/환경/쿠키/원본 운영 state·운영 로그 본문은 읽지 않았다.
- `market_buyer.py`: 기존 RPC 경계에서 card/opening/first/counter35/third/finalaccept와 시도 요약. 기존 파싱 결과만 사용. 13행/시도, UTF-8 1,024 bytes/행. stderr JSON-string envelope, stdout 계약 불변. 기록 예외 무시, 오류 타입 허용 목록, opaque 시도 ID. 영수증 선택 ≠ 결제 성공.
- `test_buyer_turn_telemetry_offline.py`: 신규 합성 10개. 모든 제안/RPC 오류/timeout/parse/deadline, stderr 실패 시 결과·RPC·hub 동일, 개인정보 배제·행수/크기·legacy·단일 stdout·parser 추가 호출 없음.
- `POLICY.md`, `TURN_TELEMETRY.md`: 미배포 상태, schema 및 합성 예시/용량 한계.
- 구현 커밋 `85b39b5`, 호환성·시한 경계/runner 보강 `3f92323`. 실패 시도 로그를 보존했다. 추가 문서·자원 증거는 별도 후속 커밋.
- 자원 증거 커밋 `2c32bbd`, 최종 schema/검증/보고 `35deeb6`.

## 실제 명령 및 결과

공통 interpreter: 저장소 기존 `.venv/Scripts/python.exe -B`. 설치/업데이트 없음.

| 명령 (`history/20261106-turn-telemetry-01/` 아래) | 결과 |
|---|---|
| `check.py baseline-v4` | **485**, failure/error **0** |
| `check.py telemetry-final telemetry` | **10**, failure/error **0** |
| `check.py after-final` | **495**, failure/error **0** |
| `audit.py` | 기존 snapshot 파일 중 buyer만 변경, 신규 테스트 1개, **54개 불변** |
| `resources.py` (읽기 전용 SSH, 최종 v3) | exit **0**, 시각/자원/stat/rotation 소스 한정 |
| `git diff --check` | exit **0** |

최종 evidence stage의 `git diff --cached --check`는 원본 unittest 출력
`after-final.log:1395`의 trailing whitespace 한 곳을 보고했다(exit 2).
**로그는 정리하지 않고 그대로 보존**했다. 이는 실행 테스트 실패가 아니며,
코드/문서만 대상으로 한 별도 whitespace 검사는 후속 확인했다.

`check.py`는 root의 **직접 속한 tracked Python source만** 복사하며 source 설정
`seller_cash_policy.json`, launcher, `scripts/market_kit.py`만 추가한다. 원본 데이터는 복사하지 않는다.
baseline의 buyer는 최초 HEAD에서 `git show 85b39b5^:.../market_buyer.py`로 복원한다.
합성 HOME/TEMP, `-B`, socket 차단, 운영 저장소 읽기 및 evidence 외 쓰기 차단 runner 사용.
buyer/price/gap/ranking/stockout/retry, forum/cohort/평판, procurement/visibility guard,
분리 cash 모듈 및 전체 tracked offline 회귀를 실행했다. 기존 untracked monitor 테스트는 복사하지 않았다.

초기 `baseline`(485, 33 failures/13 errors)은 설정/kit/launcher 누락, `baseline-v2`(485, 2 failures)는
launcher 누락으로 실패했다. `baseline-v3`는 통과했으나 git pathspec이 history 하위 Python까지 매칭하는
flatten-copy 위험을 발견해 최종 판정에서 제외했다. 직접 부모 경로 필터와 source SHA를 추가한
**baseline-v4/after-final만 최종 비교 기준**이다. 초기 `after-01`(15 failures)은 직접 객체 출력이
기존 stderr 문자열 계약을 깨뜨린 문제 및 합성 receipt fixture 문제였고, envelope/fixture를 수정했다.
`after-02`(2 failures)은 receipt 가격이 앞선 제안보다 높았던 합성 fixture를 수정했다.
`telemetry-04`(1 failure)는 finalaccept에서 잡힌 timeout의 요약 error_type 전달을 보강했다.
모든 실패 로그는 새 파일 그대로 보존했고 기존 기대값을 낮추지 않았다.

`weeks/week-08/README.md`, `scripts/check_week08.py` 부재로 과제 checker는 미실행.
실제 네트워크 협상/checkout/결제·운영 이벤트 수집은 미실행. 테스트는 외부 요청 없이 합성 mock만 사용했다.

## VM 실측 — 2026-11-06 07:41:28 UTC / 16:41:28 KST

대상 `market-agent`, project `studious-plate-458511-a1`, zone `us-central1-a`.
기존 배포 `control.py`의 gcloud SSH 인자·base64 quoting만 참고해 별도 읽기 전용 명령을 작성했다.
기존 배포 helper 실행은 하지 않았다. `/home/comalswjd/venv/bin/python -B`로 자원 명령만 실행했다.

| 항목 | 실측 |
|---|---|
| RAM | total **3,913 MiB**, available **3,137 MiB**, used **534 MiB** |
| `/proc/meminfo` | MemTotal **4,007,000 kB**, MemAvailable **3,213,028 kB** |
| Swap | total **2,047 MiB**, used **45 MiB**, free **2,002 MiB** |
| Swap 정확값 | SwapTotal **2,097,148 kB**, SwapFree **2,050,280 kB** |
| root disk | size **29G**, used **7.5G**, available **22G**, **26%** |
| inode | total **3,870,720**, used **110,104**, free **3,760,616**, **3%** |
| uptime / load | **1 day 54 min**, load **0.33 / 0.42 / 0.42** |
| project / week-08 | **88M / 66M** |
| week logs / .market / history | **24M / 4.8M / 33M** |
| project .git | **9.9M** |
| `market-kit.log` | **1,585,298 bytes**, mtime **07:41:27 UTC** |
| `market-kit.log.1` | **20,100,390 bytes**, mtime **07:20:13 UTC** |

`du`는 명시한 project/week/logs/.market/history/.git 경로 크기만 확인했다.
첫 조회는 `du` 한 명령의 중복 하위경로 억제로 project 크기만 나와, 후속 조회에서 경로별 명령으로 보완했다.
home 전체 검색/du, 프로세스 cmdline, 운영 로그 본문/개별 고객 데이터는 읽지 않았다.

메모리 상위 8개 (`ps pid,comm,rss,etime`만; RSS 단위 KiB):

| PID | comm | RSS | elapsed |
|---|---|---:|---|
| 299169 | python | 138144 | 03:25:02 |
| 43679 | python | 113132 | 22:01:18 |
| 131 | systemd-journal | 96724 | 1-00:54:12 |
| 805 | otelopscol | 88820 | 1-00:53:42 |
| 43696 | cloudflared | 43428 | 22:01:16 |
| 171 | multipathd | 27676 | 1-00:54:11 |
| 432 | google_osconfig | 27132 | 1-00:54:00 |
| 452 | snapd | 25708 | 1-00:54:00 |

## 위험과 권고

- 현재 RAM/disk/inode 자원 부족의 심각 경고 없음. swap 45MiB 사용만으로 현재 압박이라고 판단하지 않는다.
- 원격 kit 소스의 rotation 관련 행만 확인: **20,000,000 bytes 초과 시 `.log.1` replace**,
  append 전 검사. 따라서 현재+이전 약 40MB 수준이나 기존 큰 행의 append overshoot는 가능하다.
- 합성 fixture 실측 **13행/5,562 bytes**, 최장 **480 bytes**. kit prefix 17 bytes/행 가정 시
  5,783 bytes/시도. hook당 최대 3시도·39행·각 1,024 bytes 하드 캡에 kit prefix를 더하면
  **40,599 bytes/hook**. 운영 event/hook 빈도는 조회하지 않았다.
- **60 hooks/hour를 가정**하면 최악 추가량 **2.44 MB/hour, 58.46 MB/day**. 운영 실측이 아니다.
  rotation 때문에 디스크 부족보다는 관측 보존 기간이 짧아질 위험을 우선 고려한다.
  외부 kit stdout 중복 저장 경로의 보관 설정은 미확인이다.
- 권고: 별도 승인을 받은 뒤 이 buyer 변경만 배포하고, 작은 기간의 이벤트 스키마/누락·보존시간을 검증한다.
  이 작업에서는 **배포/재시작/파일삭제/cleanup/환경설치/거래/서비스변경을 전혀 실행하지 않았다**.
  따라서 운영상 2·3턴 체결 원인 분석은 아직 이 계측으로 확정할 수 없다.
