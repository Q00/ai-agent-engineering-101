# Round3 35% gap counter VM deployment

- 승인: 사용자의 “VM에 배포해줘”. VM `market-agent`, project `studious-plate-458511-a1`, zone `us-central1-a`. 기존 gcloud 인증만 사용.
- 원격 repo `/home/comalswjd/ai-agent-engineering-101`, 작업 `submissions/25512085/week-08/`.
- 실측 before HEAD `edfb50e5a36815ff131d3ea30d67389758b1d4c8`, tracked/staged clean. buyer SHA `01b49b84ef6017c724567a4ab2caef836f51f2d8eb5f0c64798725a20c5dd2fc`. allowlisted tracked source 50개만 확인/격리 복사.
- 실제 원격 commit **`594e1f9bd4de1662c46e4816fabe5a096ab1233d`**, parent는 위 before HEAD.
- 원본 구현 `abc7e0a97685f1290a3a6c74fb3cf0907deb78a5`, 최종 테스트 `1854fc8e953dce5274b3765d2f2bef605192fe48`. commit에 `Backport-of`와 `Tests-from` 기록.

## 변경 파일

| 파일 | 적용 SHA-256 |
|---|---|
| `market_buyer.py` | `e03cef48b0760c695edca399a6692fee4b614d5cf16162c802d8781d446ac527` |
| `test_buyer_gap_counter_offline.py` | `f1559e053a9ae25f2daaf086cc8b3c1ebec185e4de9255100b6940062cd8ffe3` |
| `test_buyer_retry_offline.py` | `f5c7c96c10a912d54b8111a01e181dc52318a708417433a130f0813c35756fd2` |

원격 `_run_attempt` baseline AST가 원본 구현 직전과 동일함을 확인한 뒤, 구현의 두 byte-context 구간만 이식했다. `step_inc=5`는 legacy 전용이며 Round3 두 번째 제안은 실제 `seller_ask-last_offer`의 35%를 정수 half-up/최소 1로 더한다. gap이 0 이하면 seller ask를 사용한다. 기존 offer_limit clamp는 그대로다. 변경 후 함수 AST는 원본 구현 및 최종 커밋과 동일하고, 다른 함수 AST는 VM before와 동일하다. 신규 gap 회귀 11개 및 기존 retry 기대값 한 줄만 추가 변경했다. 최초/셋째/최종 수락·레거시·절약률 정렬·매입 guard는 유지했다. policy/docs/전체 로컬 소스 동기화 또는 cherry-pick은 하지 않았다.

## 실제 명령 및 검증

로컬 `.venv/Scripts/python.exe -B`로 이 폴더의 `bootstrap.py`, `control.py inspect`, `prepare.py`, `local_validate.py`, `stage.py`, `control.py remote validate`, `control.py remote apply`, `control.py remote export final`, `audit.py`를 실행했다.

- 로컬 격리: 특화 **137 / 전체 416**, failure/error 0.
- VM 기존 `/home/comalswjd/venv/bin/python -B` (3.10.12), 운영 반영 전 별도 격리 tree: 특화 **137 / 전체 416**, failure/error 0. gap 11개, savings ranking 8개, procurement guard 13개 포함.
- 격리 tree에만 `git apply --check` 및 patch 적용. synthetic HOME/TEMP/최소 환경, 외부 socket 접근·운영 repo 접근·evidence 밖 파일 쓰기 차단 runner 사용. 운영 state/key/env/runtime log를 복사하거나 읽지 않았다. 설치 없음.
- 적용 직전 HEAD/dirty/allowlisted SHA 재검증. 원본 buyer/retry backup 보존. fsync 후 **2026-11-06 05:52:54.937461 UTC (14:52:54.937461 KST)** buyer `os.replace` 원자 교체. 권한 변경 없음.
- 적용 후 및 독립 `audit.py`: 세 파일 SHA/메모리 compile, 원격 commit parent·변경 경로 정확히 세 개·Backport trailer·tracked/staged clean, 다른 **48개 source SHA 불변**, 두 backup SHA 검증. 기존 procurement evidence SHA 불변.
- **05:52:55.662358 UTC (14:52:55 KST)** local card/public card/board GET 모두 **200**, `mjforge` seller **alive=true**, buyer **alive=true**. URL `https://combines-spelling-sam-parish.trycloudflare.com` 유지.

## 재시작 / 증거 / 제한

- VM 실측 `scripts/market_kit.py:183-185,642-651,705-711`에서 주문별 `run_buyer` → `start` → `subprocess.Popen` 확인. 다음 호출부터 새 buyer 파일을 읽는다. **restart/signal 0회**. kit 43679/starttime 1037901, tunnel 43696/1038186, seller 299169/7735542 전후 동일.
- 원격 새 evidence: `submissions/25512085/week-08/history/20261106-gap35-runtime-01/`. `backup/market_buyer.py`, `backup/test_buyer_retry_offline.py` 보존. 로컬 `remote-export-final/`에 이 작업의 allowlisted 증거 14개만 export했다. synthetic fixture tree/대형 archive/backup은 로컬 보존하되 commit에서 제외한다.
- 원격 Git identity는 원본 작성자를 해당 commit 명령의 `-c user.name/user.email`로만 사용. 영구 config 변경 없음. 새 작업에 실패 명령 없음. 모든 새 stdout/stderr 및 기존 로그/history 보존.
- 로컬 기존 dirty `logs/market-test.log`, `test-report.json` 및 기존 untracked는 수정/stage하지 않았다. 로컬 helper/evidence만 이 고유 history 안에 작성했다. 준비 검증 commit `a012ced`.
- 거래/매입/게시/결제 POST 또는 실거래 테스트 없음. 실제 거래·절약률 개선 효과는 **미확인**. `weeks/week-08/README.md` 및 `scripts/check_week08.py`가 없어 과제 검사 미실행. 배포 차단 문제 없음.
- 별도 에이전트 호출, push, reset/clean, history rewrite, VM/kit/seller/tunnel 재시작, 의존성 설치, 인증/권한 변경 없음.
