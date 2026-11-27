# Round 3 숫자-only 한정 VM 배포

## 결과 / 변경 파일

- 사용자 승인 “VM배포해줘”, 대상 `market-agent`, project `studious-plate-458511-a1`, zone `us-central1-a`. 다른 에이전트 호출 없음.
- 실제 원자 교체: **2026-11-06 10:19:56.072695 UTC / 19:19:56 KST**.
- 원격 커밋 **`4a5deb685368343abfd1310c572288a924351b01`**, parent **`be6a77c835fbcabf7edb75097be318ba4f84eecb`**. 명시한 아래 다섯 파일만 커밋, tracked/index clean, push 없음.
- 운영 코드 `market_buyer.py`: Round 3 일반 제안 `str(offer)`, 최종 호가 수락 `str(seller_ask)` 두 표현식만 변경.
- 테스트: 신규 `test_buyer_integer_wire_offline.py`, 기존 price/retry/turn_telemetry 세 파일의 wire fixture 기대만 변경. 정책/수치 assertions 유지.
- 최종 buyer SHA-256: **`7e7a8a5905b29b9c583840e4a16ead2ed5a6d5f6698b5cda22c1709c2b60fc23`**. 다섯 파일 해시는 `manifest.json`, `remote-evidence/applied.json`에 보존.

## 범위 / attribution / 보존

- AGENTS, 초기 status/HEAD, 최신 로컬 REPORT, 이전 배포 REPORT/control/remote/inspect/runner를 먼저 읽었다. 기존 mutation helper는 재실행하지 않고 새 고유 폴더에서 새 절차를 작성했다.
- 원격 사전 HEAD와 buyer `1e71f1e53ebfb1f651f79335c0250db1b93416e56a0638b1f26c0cc588c72889` 실측 일치. 로컬 `cc62b97` buyer와 byte/AST 동일. 기존 관련 세 테스트도 해당 기준선과 바이트 동일했다.
- 원본 attribution: `cc62b97` 최초 strict 테스트/예상 실패; `0baab04` **실제 운영 두 식**과 관련 세 fixture/strict 확장; `bfa4e52` legacy fixture 인자 수정. 최종 후보 source `bfa4e52a507bfd338336ac8e6fb210cf40f99bea`. 원격 커밋에도 Backport-of 기록.
- 원격 원본 buyer에 두 byte 치환만 적용한 결과가 후보 바이트와 정확히 동일함을 검증했다. 최종 로컬 AST/바이트와도 동일. 전체 sync/cherry-pick 없음. remote monolithic seller 유지.
- initial SKU/인증/보증 문의, legacy 문장, 35% gap/상한/횟수/cooldown/telemetry/매입 guard/forum cohort 불변. stage2 중복 재전송 방지/cooldown은 미구현·미배포.
- allowlisted 기존 source 53개 중 관련 기존 네 파일 외 **49개 불변**. 적용 직전 HEAD/dirty/모든 소스 해시/신규 파일 충돌 재검사. 변경할 기존 네 파일 backup/fsync, 임시파일 fsync, 테스트 먼저 설치 후 buyer atomic replace 및 디렉터리 fsync.
- 적용 후 해시/AST/메모리 compile/parent/정확 커밋 파일 목록/clean/49개 소스 해시 확인. identity는 원본 작성자를 해당 commit의 `-c`로만 사용, 영구 config 변경 없음.
- **재시작 0, 시그널 0**. kit/seller/tunnel PID/starttime 불변. 원격 allowlisted `scripts/market_kit.py`의 `handle_order -> run_buyer -> start/Popen` 구조 확인. 주문별 새 프로세스가 소스를 읽으며 이미 실행 중인 buyer가 새 코드를 적재했다고 주장하지 않는다.
- 기존 사용자 dirty/untracked/log/history/evidence 보존. PEM/env/token/key/cookie 및 운영 state/.market 파일을 읽거나 전송하지 않았다. 기존 gcloud 인증만 사용. 설치/업데이트/cleanup/push/POST/별도 대화 RPC/주문·매입·게시·투표 없음.

## 실제 명령 / 테스트

공통: `.venv/Scripts/python.exe -B submissions/25512085/week-08/history/20261106-integer-wire-deploy-01/control.py <mode>`.

| 명령 | 결과 |
|---|---|
| `build.py`, `inspect`, `prepare` | 새 helper 작성, 원격 기준선/해시/AST 및 두 식 delta 검증 |
| 첫 `local` | 집중 207개 중 실패 34, error 0. 검증 도구가 격리 내부의 합성 `.market` fixture까지 차단한 문제. 실패 log/json 및 초기 helper 커밋 `d72fb61` 보존 |
| 수정 후 `local` | 집중 **207**, baseline **436**, 후보 전체 **440**, 모두 failure/error **0** |
| `stage`, `remote validate` | VM 기존 Python **3.10.12**, baseline **436**, 집중 **207**, 후보 전체 **440**, 모두 failure/error **0** |
| `remote apply` | 백업/원자적 교체/한정 commit/해시/AST/compile/clean 확인, exit 0 |
| `remote observe` | 카드/board/공개 카드 GET **200/200/200**, seller/buyer alive **true/true** |
| `export` | 합성 테스트 및 민감정보 없는 배포 증거 **14개**만 수집 |
| `git diff --check` (수정 helper 코드) | exit 0; 원본 로그는 편집하지 않음 |

runner는 source-only 격리 tree, 합성 HOME/TEMP, 최소 환경, 외부 socket 차단, 원본 저장소 읽기와 evidence 밖 쓰기 차단. 검증 도구 수정은 격리 OUT 내부 합성 fixture만 허용한 것이며 운영 `.market` 읽기 허용이 아니다. 로컬 499개가 아닌 **원격 monolithic 기준 436+4=440개**를 검증했다. 기존 미배포 refactor를 반입하지 않았다. checker `weeks/week-08/README.md`, `scripts/check_week08.py` 부재로 주차 checker 미실행.

## 실이벤트와 실효과 구분

- 단 한 번 관측: **10:20:04.607461 UTC / 19:20:04 KST**. 로그 마지막 **262144 bytes**만 원격 메모리에서 파싱, 원자 교체 시각 이후 이벤트만 집계. sleep/poll/재조회/운영 로그 다운로드 없음.
- `buyer_turn` **16개**, JSON-string envelope **16개**, marker 파싱 실패 **0개**. 카드/초기 문의/first/counter35/third 및 rejected 경계가 관측됐다. receipt-selected **0개**.
- 제한 tail 내 stdout-contract 경고 **0개**지만 경고는 시간 필터를 적용하지 않은 값이며 전체 운영 정상 증거가 아니다. 원문/가격/예산/계정/URL/서명/대화는 저장·출력하지 않고 안전한 이벤트 경계별 집계만 보존했다.
- **배포와 실제 계측 이벤트 발생은 확인**, 그러나 각 이벤트가 새 소스를 적재한 buyer인지, 실제 상대가 숫자-only 제안을 수락했는지, 실제 영수증·결제·Saved·체결률이 개선됐는지는 **미확인**. strict integer 수락과 synthetic receipt 선택은 오프라인 테스트에서만 확인했다. 16개 이벤트만으로 wire payload나 거래 성과를 추론하지 않는다.

## 로컬 산출물

로컬 운영 source 변경 없음. 이 새 history 폴더의 절차/patch/manifest/비민감 metadata/합성 실패·성공 로그/배포 보고서만 명시 stage/commit. `evidence/`, 중복 source tar/후보 사본/archive 포함 inspect stdout/export transport는 stage하지 않는다. 기존 사용자 dirty 파일은 commit하지 않았다. 검증 도구 수정과 성공 기록 커밋 `ea3ceca`; 최종 배포 증거는 후속 별도 commit.
