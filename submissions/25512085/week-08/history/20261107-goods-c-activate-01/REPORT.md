# revision06 승인 배포 완료 — seller-only

## 변경 파일

- 원격 운영: `submissions/25512085/week-08/seller.py`, 신규 `test_goods_terms_pilot_offline.py` 두 파일만.
- 로컬: 이 고유 history의 배포/검증 helper, 정제된 결과 및 보고서만 추가.
- 기존 dirty 로그/test-report, 다른 untracked 파일, 로컬 refactor와 기존 history는 수정/스테이징하지 않음.
- 원격 backup: `history/20261107-goods-c-activate-runtime-01/seller.py.before`.

## 변경 요지 / 실제 활성화

- 사용자 승인 및 메모리 협상 일부 유실 위험 수용에 따라 적용. pending unknown을 이유로 재보류하지 않음.
- 승인 소스 `2fbd842b2bf5b02716e479fc81099ee4bbe1e7a0`, revision06 manifest/patch/검증 증거만 사용.
- 원격 HEAD `4a5deb685368343abfd1310c572288a924351b01`, tracked/staged clean,
  56개 운영 소스, 후보 해시 및 세 프로세스 identity를 read-only inspect와 실제 적용 직전에 재확인.
- `git apply --check` 통과 후 격리 검증 후보의 정확한 바이트만 backup/fsync/atomic replace.
- 원격 배포 commit: **`93b93b8bbca52c8b00afda0480d53ee8937aa574`**.
  전역 Git 설정 변경 없이 per-command identity 사용, 정확히 두 파일만 commit.
- **활성화 확인: 2026-11-07 14:49:22 KST** (05:49:22 UTC, 새 PID 카드 200 시각).
- seller SHA256: `17c653f836436975268651436e9f7cf0e1bfda9b1fdfeb6615de4b67df4a13fd`.
- fixture SHA256: `ad9568062c09b6689925078b284b1a5ab356620ab5dc10fc40ef2ceb5a4432cf`.
- A0 제외/A72 유지, 3 SKU 신규 무제약 C0 우선, 복합 조건 부정 fail-closed 후보 활성화.
  가격/매입/buyer/forum/kit/config는 기존대로. 전체 sync/cherry-pick 없음.
- seller **299169/start 7735542에만 SIGTERM 1회**. 기존 kit watchdog가
  **621240/start 16932912**를 자동 기동. 수동 spawn 0, 중복 seller 없음.
  명령 `python seller.py --port 52507`, cwd `/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08`.
- kit **43679/1037901**, tunnel **43696/1038186** PID/starttime 불변. buyer/VM 재시작 없음.
- 기존 운영 56개 중 seller 외 **55개 소스 불변**, 신규 fixture만 추가.
  market_buyer SHA256 `7e7a8a5905b29b9c583840e4a16ead2ed5a6d5f6698b5cda22c1709c2b60fc23` 유지.
- 배포 실패/rollback 없음. 실패 시 원본 보존/본 작업 바이트만 롤백하는 경로는 준비했으나 실행하지 않음.

## 테스트 / 관측

공통 명령 prefix:
`.venv/Scripts/python.exe -B submissions/25512085/week-08/history/20261107-goods-c-activate-01/`

- `transport.py inspect`: 원격 HEAD/dirty/56개 hash/후보/기존 PID identity 일치.
- `check_local.py`: 배포 helper 4개 메모리 compile 통과, read-only preflight 생성.
- `transport.py preflight`: 정확한 seller shell 부모 명령/cwd, patch 적용 가능성, 후보 compile,
  기존 seller 카드 GET 200 통과. 환경변수/키/agent 설정 내용 읽지 않음.
- `transport.py activate`: 두 파일 적용/commit, seller TERM 1회/자동 재기동/카드 200,
  무관 소스 불변 및 tracked/staged clean 확인 성공.
- `transport.py observe`: **2026-11-07 14:50:36 KST**, 같은 원격 commit·새 PID/start/hash 유지.
  **로컬 카드 200 / board 200 / 공개 카드 200**, board seller_alive=true / buyer_alive=true.
- 정상 유입은 `logs/market-kit.log` 마지막 **65,536 bytes를 VM 내에서 딱 1회** 제한 읽기 후
  집계만 출력. 새 PID 시작 marker가 tail에 없어 재시작 이후 유입을 귀속할 수 없음.
  집계 0은 실제 유입/견적 0이라는 뜻이 아님. 원본 로그/대화/계정/예산/서명 출력·다운로드 없음.
- 회귀 근거: 기존 revision06 VM 격리 `remote-tests.json`의 집중 **124 통과**,
  전체 **490 실행 통과 + 2 skip**; 이번 배포에서 전체 테스트 재실행하지 않음.
- `verify.py`: 신규 helper 메모리 compile, 저장 증거/manifest/기존 격리 테스트 성공 및 제한 diff-check 검증.
- week-08 README/checker는 저장소에 없어 해당 검증 미실행. 실거래 테스트 미실행.
- GET 외 새 거래/RPC/POST/매입/forum 게시, 설치, push, 다른 agent 호출 없음.

## 미해결

- 재시작으로 유실된 메모리 협상 수: **unknown**. 승인된 위험 수용 범위에서 유지보수 재시작 수행.
- **새 C0의 실제 유입·성사 효과 미확인**. 공개 health 성공을 정책의 실거래 효과로 해석하지 않음.
- 공개 card/board GET은 읽기 전용 검증이며 새 거래를 만들지 않았음.
