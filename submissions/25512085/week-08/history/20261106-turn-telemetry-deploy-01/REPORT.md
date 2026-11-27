# 협상 계측 한정 VM 배포

## 배포 결과

- 사용자 승인: “VM에 배포 진행해줘”. 다른 에이전트 호출 없음.
- 대상: `market-agent`, project `studious-plate-458511-a1`, zone `us-central1-a`.
- 배포 확인 시각: **2026-11-06 08:53:04 UTC / 17:53:04 KST** (`remote-evidence/atomic-applied.json`에 파일 교체 시각 별도 보존).
- 원격 시작 HEAD 실측: `981c63401db534a30575fa4449d725bee04d4cdd`, tracked/index clean.
- 원격 배포 커밋: **`be6a77c835fbcabf7edb75097be318ba4f84eecb`**. 위 HEAD의 직계 후속이며 buyer와 신규 테스트 두 파일만 커밋했다. push 없음.
- 원본: `85b39b5`, `3f92323`, `35deeb6` 및 최종 로컬 `94085b0314717b34cec242cea988da1b4687404c`. 원격 커밋에 Backport-of를 기록했다.

| 원격 파일 | 최종 SHA-256 |
|---|---|
| `market_buyer.py` | `1e71f1e53ebfb1f651f79335c0250db1b93416e56a0638b1f26c0cc588c72889` |
| `test_buyer_turn_telemetry_offline.py` | `8b76204046833e9197500d04c9b63e122ba5020b82aab3b87bb54a0d9b3374d9` |

## 범위 / 보존

- 기존 AGENTS, 로컬 status, 설계/시험 REPORT, TURN_TELEMETRY 및 기존 배포 helper 소스를 먼저 읽었다. 기존 mutation helper를 실행하지 않았다.
- 원격 buyer는 로컬 `c819c25` buyer와 **바이트 및 AST 동일**: SHA-256 `e03cef48b0760c695edca399a6692fee4b614d5cf16162c802d8781d446ac527`.
- `c819c25..94085b0` 중 두 파일만의 patch를 생성해 원격 격리 tree에 `git apply --check` 후 적용했다. 후보 파일은 최종 로컬 바이트 해시와 일치한다. 로컬 전체 sync/cherry-pick 없음.
- SDK 호환 JSON-string stderr envelope, 시도당 13행/행당 1KiB 상한, safe metadata만의 기존 최종 구현을 그대로 배포했다. RPC/parser, counter35/third/finalaccept, savings ranking, procurement guard, forum cohort, monolithic seller는 변경하지 않았다. 분리 cash/profile/hub/refactor/평판 관측 도구를 전송하지 않았다.
- 원격 allowlisted source 52개 중 기존 buyer 외 **51개 불변**. 신규 테스트 1개 추가. 적용 직전 HEAD/dirty/hash/collision 재검사, 원본 buyer backup/fsync, 임시파일 fsync와 atomic replace, 디렉터리 fsync를 수행했다. 테스트 파일을 먼저 설치했다.
- 적용 후 byte/AST/compile, 명시 두 파일 커밋, parent/커밋 파일 목록/clean 확인. Git identity는 원본 작성자로 해당 커밋 명령에만 `-c` 적용했다. 영구 Git 설정 변경 없음.
- kit/seller/tunnel의 PID/starttime 불변. **재시작 0, 시그널 0**. 원격 kit 소스의 `handle_order -> run_buyer -> start/Popen`을 확인하여 주문별 새 프로세스가 소스를 읽는 구조임을 확인했다. 이미 실행 중인 buyer 프로세스가 새 코드를 적재했다고 주장하지 않는다.
- 기존 로그/history/evidence/rotation/원본 데이터/사용자 dirty와 index를 보존했다. PEM/env/token/cookie/운영 state를 읽거나 복사하지 않았다. 운영 로그는 다운로드하지 않았다. 기존 gcloud 인증만 사용했다. 설치/업데이트/권한 변경/POST/거래 시작 없음.

## 실제 명령 / 테스트

공통 명령: 저장소 기존 `.venv/Scripts/python.exe -B history/.../control.py <mode>` (실제 경로는 이 폴더).

| mode | 결과 |
|---|---|
| `inspect`, `prepare` | 원격 HEAD/clean/source 52개/hash/byte+AST 기준 일치, 최종 후보 해시 검증 |
| `local` | 원격 monolithic source 기반 계측 **10**, baseline **426**, 후보 전체 **436**, 모두 failure/error **0** |
| `stage` | 고유 원격 history 디렉터리, 허용 소스 두 파일/patch/전용 실행 helper만 전송 |
| `remote validate` | VM 기존 Python **3.10.12**, baseline **426**, 계측 **10**, 후보 전체 **436**, 모두 failure/error **0** |
| `remote apply` | 원본 backup 및 원자적 교체/한정 커밋/51개 소스 불변/compile/clean, exit 0 |
| `remote observe` | 로컬 카드/board/공개 카드 GET **200/200/200**, seller/buyer alive **true/true** |
| `export` | 허용된 합성 테스트 로그 및 민감정보 없는 배포 증거 14개만 수집 |
| 로컬 `git diff --check` / 절차 파일 `git diff --cached --check` | 코드 대상 exit 0; 원본 합성 로그는 편집하지 않음 |

격리 runner는 tracked source만 사용하며 합성 HOME/TEMP, 최소 환경, socket 감사 차단, 운영 저장소 읽기 차단, evidence 외 쓰기 차단을 적용했다. 원격 실행도 실제 key/state/.market runtime을 복사하지 않았다. 로컬 495개 회귀와 다른 이유는 **원격 monolithic 소스 426개가 배포 기준**이기 때문이다. 로컬 미배포 refactor를 함께 이식하지 않았다.

이 배포 작업에서 실패한 실행 명령은 없었다. 로컬 tar extract의 Python 3.14 기본값 변경 DeprecationWarning은 보존된 출력과 동일하며 테스트 실패가 아니다. 과제 `weeks/week-08/README.md`, `scripts/check_week08.py`는 존재하지 않아 checker 미실행.

## 실제 운영 이벤트 관측 / 제한

- 단 한 번의 bounded 조회: **08:53:12 UTC / 17:53:12 KST**, 현재 `market-kit.log` 마지막 **262,144 bytes**만 원격 메모리에서 파싱했다. sleep/poll/반복 조회, 전체 로그 다운로드 없음.
- 배포 이후 최종 schema `buyer_turn` 이벤트 **0개**; marker 파싱 실패 **0개**, 제한 tail 내 `market_buyer printed no result line` 경고 **0개**. 계정/대화/예산/URL/서명/원본 로그 행은 출력·저장하지 않았다.
- **활성 경로의 파일 해시 확인과 실제 이벤트 생성 검증은 별개**다. 관측 시점의 두 파일 해시는 위 표와 같지만, 새 정상 주문에서의 실제 계측 출력/턴별 체결 원인/누락률/실시간 stdout single-JSON 계약은 아직 검증하지 못했다. 경고 0개를 온라인 전체 정상 증거로 확대하지 않는다.
- stdout single-JSON, stderr string envelope, 안전 필드/행수/크기/오류 무시/전략 불변은 **합성 오프라인 테스트**에서 확인됐다. 향후 정상 주문 뒤 별도 승인 범위에서 짧은 tail 관측으로 실이벤트를 확인할 수 있다. 이를 위해 이번 작업에서 별도 거래를 시작하지 않았다.

## 로컬 산출물

이 고유 폴더의 helper, patch, manifest, 민감정보 없는 metadata, 합성 테스트 로그/보고서만 별도 커밋한다. 새 로컬 snapshot/archive는 재현을 위해 이 폴더 안에 보존하되 중복 대량 소스이므로 stage하지 않는다. 기존 사용자 dirty/untracked는 stage하지 않는다. push하지 않는다.

절차/patch/manifest 커밋: `50f5860`. 실행 결과/보고서는 별도 후속 증거 커밋으로 분리했다. 로컬 source checkout/사용자 dirty 파일의 내용은 변경하지 않았다.
