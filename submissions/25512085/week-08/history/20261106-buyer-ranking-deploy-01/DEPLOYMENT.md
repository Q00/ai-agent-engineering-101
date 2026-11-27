# Buyer savings ranking: deployed

- 승인 범위: 원본 `62b862c8c190801fea74d79410daf1c063df4344`의 후보 정렬/정가 안전처리와 신규 회귀 테스트만 monolithic VM에 이식했다. 전체 동기화/cherry-pick/리팩터링 배포 없음.
- VM: `market-agent`, project `studious-plate-458511-a1`, zone `us-central1-a`. 기존 gcloud 인증만 사용했다.
- 원격 작업 경로: `/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08`.
- 원격 evidence: 위 경로의 `history/20261106-buyer-ranking-runtime-01`.
- Before HEAD: `d45dfac64c094de791f7c51e72d537399789df16`, tracked/staged dirty 없음. 49개 allowlisted tracked source SHA 확인.
- 실제 배포 commit: **`edfb50e5a36815ff131d3ea30d67389758b1d4c8`**, parent는 위 Before HEAD. commit에 `Backport-of: 62b862c8c190801fea74d79410daf1c063df4344` 기록.

## 실제 변경 파일 / SHA-256

| 파일 | 적용 SHA-256 |
|---|---|
| `market_buyer.py` | `01b49b84ef6017c724567a4ab2caef836f51f2d8eb5f0c64798725a20c5dd2fc` |
| `test_buyer_savings_ranking_offline.py` | `185cf2feb9b2d47166a192f033934dd48b6f7130fc3b55931fdfa8991454efbf` |

원본 변경 전 정렬 함수 AST와 VM 함수가 동일함을 확인했다. 원본 delta 5개 구간만 byte-context로 이식하고 변경 후 함수는 원본 새 AST와 동일, 나머지 함수 AST는 VM before와 동일함을 확인했다. 기존 줄바꿈은 변경 구간 밖에서 보존했다. cash/restock/seller/config/kit 및 5SKU 매입 guard는 변경하지 않았으며 나머지 allowlisted source SHA도 불변이다.

`market_buyer.py`의 before SHA는 `ba4ce6d190dba4b23e9ebcfecef9ff2630799dd403a49b469a8292359ca8c9cc`. 원격 `backup/market_buyer.py`와 로컬 `remote-export-final/backup/market_buyer.py`로 보존했다. 적용 직전 HEAD/dirty/전체 소스 SHA를 재검증한 후 파일을 fsync하고 **04:49:47.586202 UTC (13:49:47 KST)**에 `os.replace`로 원자 교체했다. 권한 변경 없음.

## 실제 실행한 검증

- `.venv/Scripts/python.exe -B .../prepare.py`: 원본 attribution, 문맥 유일성, 전후 함수 AST 및 메모리 compile 통과.
- `.venv/Scripts/python.exe -B .../local_validate.py`: 로컬 격리 **특화 126 / 전체 405** 통과.
- `.venv/Scripts/python.exe -B .../control.py remote validate`: 기존 VM venv `/home/comalswjd/venv/bin/python -B` **Python 3.10.12**, 별도 격리 tree에서 **특화 126 / 전체 405** 통과. 신규 savings 회귀 8개와 기존 procurement guard 13개 포함. **실제 적용 전** 완료했다.
- `git apply --check` 후 격리 tree에만 patch 적용, 전후 SHA 및 모든 격리 Python 메모리 compile 통과.
- 테스트는 synthetic HOME/환경/TEMP와 외부 socket·운영 repo 접근·evidence 밖 쓰기 차단 runner를 사용했다. 기존 key/env/state/inventory를 복사하거나 읽지 않았고 신규 회귀는 네트워크 호출을 mock했다. 패키지 설치 없음.
- 적용 후 두 파일 SHA/메모리 구문 검사, commit parent/변경 경로 정확히 두 개, tracked/staged clean, 나머지 소스 SHA 불변과 이전 procurement evidence 두 파일 SHA 불변 확인.
- **04:51:21.763840 UTC (13:51:21 KST)** GET: local card **200**, public card **200**, board **200**, `mjforge` seller **alive=true**, buyer **alive=true**. 공개 URL 유지: `https://combines-spelling-sam-parish.trycloudflare.com`.

## 프로세스 / 실패 이력

- `scripts/market_kit.py:183-185,642-651,710-711`은 주문별 `run_buyer` → `start` → `subprocess.Popen`을 실행한다. 다음 호출부터 새 파일을 읽으므로 **재시작·signal 0회**. 진행 중 buyer는 건드리지 않았다.
- kit **43679/starttime 1037901**, tunnel **43696/starttime 1038186**, seller **299169/starttime 7735542** 전후 동일. 최종 snapshot에서 정상 주문의 market_buyer **307969**가 관측됐으며 배포가 별도 거래를 시작한 것이 아니다.
- 최초 staging은 Windows command length 제한으로 SSH 실행 전에 실패했다. 실패 stderr 보존 후 zlib 압축 인라인 명령으로 바꿨다. 원격 인증/권한 변경 없음.
- 적용 후 첫 Git commit은 VM 작성자 설정 부재로 실패했다. 코드/테스트는 이미 올바른 SHA로 적용·stage된 상태였고 재적용하지 않았다. `finish_commit.py`가 HEAD/정확한 두 staged 파일/나머지 source 및 프로세스를 재검증하고 원본 작성자 정보를 **해당 명령의 `git -c user.name/user.email`에만** 지정해 commit을 완료했다. global/local Git config 변경 없음. 최초 `commit.log`, `failure-apply.json`과 후속 `finish-commit.log` 모두 보존했다.
- 원격 evidence 중 이 작업의 명시된 15개 파일만 export했다. synthetic test fixture tree와 기존 운영 로그/state/key는 export하지 않았다.

## 제한 / 보존

- 주문·게시·매입·결제 POST 및 실거래 테스트는 하지 않았다. 실제 수익률·거래 효과는 미확인이다.
- `weeks/week-08/README.md`와 `scripts/check_week08.py`가 없어 해당 과제 검사 미실행. 실제 검증은 원본 commit/VM 소스와 offline suite 기준이다.
- 로컬 기존 dirty `logs/market-test.log`, `test-report.json` 및 기존 untracked/evidence는 수정하지 않았다. 신규 산출물은 이 고유 history 경로 안에만 보존했다. 격리 synthetic fixture/대형 archive는 로컬 보존하되 commit 대상에서 제외한다.
- 배포 차단 문제 없음. Push/reset/clean/history rewrite/별도 에이전트/재시작/설치 없음.
