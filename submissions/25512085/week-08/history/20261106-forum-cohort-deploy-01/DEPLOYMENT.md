# 게시글 cohort 근거 정합성 한정 배포

- 승인: 사용자 “일단 얼른 배포해줘”. VM `market-agent`, project `studious-plate-458511-a1`, zone `us-central1-a`. 기존 gcloud 인증만 사용.
- 원격 before HEAD `594e1f9bd4de1662c46e4816fabe5a096ab1233d`, tracked/staged clean 실측. 로컬 기존 dirty 두 파일과 untracked 보존.
- 원본 `417786f44d7315160a278f6de9229bdc0b05ca5c`, 원격 **`981c63401db534a30575fa4449d725bee04d4cdd`**, Backport-of 명시. 원본 작성자 identity를 commit 명령에만 지정, 영구 config·push 없음.

## 변경 파일과 범위

1. `seller_cash_policy.py`: monolithic 원격 소스의 `compact_break_orders`, `observe_break_stats` byte-context만 이식. 신규 ledger 판매의 sale/break ID와 실제 counter 성숙 proof 기록, 최대 20,000개 제한. 가격·매입·불량률 추정 함수 AST 불변.
2. `forum_writer.py`: `analysis_candidates`만 변경. 성숙 cohort의 고장-판매 쌍 refs, 대표 근거 명시, 가격의 미통제 혼합조건 고지.
3. `test_forum_intel_offline.py`: fixture/기대 보강.
4. `test_forum_quality_evidence_offline.py`: 원본 신규 회귀 10개.

변경 함수 baseline AST가 원본 직전 커밋과 같고, 적용 후 AST가 원본 구현과 같음을 확인했다. 다른 함수 및 다른 source 48개 SHA 불변. buyer 35% gap·savings ranking·5SKU procurement guard 유지. 전체 파일 복사/cherry-pick/sync 없음. 독립관측·평판 도구 및 로컬 cash refactor는 미배포 유지.

## 실제 검증과 적용

- `.venv/Scripts/python.exe -B`로 `bootstrap.py`, `control.py inspect`, `prepare.py`, `build_remote.py`, `local_validate.py`, `stage.py`, `control.py remote validate`, `load_flow.py`, `control.py remote apply`, `control.py remote export final`, `audit.py` 실행.
- 로컬 격리 focused **193**, 전체 offline **426**, failure/error 0.
- VM 기존 `/home/comalswjd/venv/bin/python -B` 3.10.12, 운영 반영 전 별도 격리 tree focused **193**, 전체 offline **426**, failure/error 0. forum/cash/compaction/break/guard/buyer 포함.
- 기존 audit-hook runner 재사용: synthetic HOME/TEMP/최소 환경, socket 연결/운영 repo 접근/evidence 밖 쓰기 차단. 운영 state·env·키·로그를 테스트 입력으로 읽거나 복사하지 않음. 설치/권한 변경 없음.
- 적용 직전 HEAD/dirty/source SHA 재검증, 기존 세 파일 backup+fsync 보존, 신규 테스트 충돌 검사. fsync/atomic replace/디렉터리 fsync.
- **2026-11-06 07:02:54.340390 UTC (16:02:54 KST)** cash 먼저, **07:02:54.348059 UTC** strict writer 적용. `atomic-applied.json`에 각 파일 시각/hash 보존.
- 적용 후 구문/hash/정확히 4개 commit 경로/parent/clean 검사. **07:03:47 UTC** 독립 audit 재확인: 다른 source **48**개 불변, backup SHA 일치. 기존 procurement evidence 불변.
- **07:02:56 UTC (16:02:56 KST)** local card/public card/board GET 모두 **200**, seller/buyer **alive=true**. URL 유지.

## 재시작·한계·실패 보존

- restart/signal **0회**. kit 43679/starttime 1037901, tunnel 43696/1038186, seller 299169/7735542 전후 동일.
- `market_kit.py:183-185,887-892`의 restock별 subprocess, `restock.py:188` → `store.py:412` observer 로드 확인. tracked agent 명령에서 restock.py/review.py 사용 및 shell loop 부재를 boolean으로만 검사. `review.py:29,42-50`이 새 review마다 writer를 import하고 별도 writer process 생성. seller는 cash를 import하나 변경은 observer 메타이며 가격 함수 불변이므로 재시작 불필요.
- 운영 state를 읽어 proof 존재 여부를 확인하지 않았다. 기존 state에 proof가 없으면 quality 글이 skip될 수 있고, 새 관측은 **6시간 성숙**이 필요할 수 있다. 미래 정상 tick에만 적용되며 실제 새 게시글/거래 효과는 미검증.
- 기존 게시글 수정/삭제, 게시/투표/매입/거래 POST는 실행하지 않았다. GET 상태 확인만 수행.
- 첫 로컬 prepare의 fixture 인접 문맥 중첩 실패를 `preparation-failure.json`에 보존. baseline 재사용/고유 byte anchor 수정 뒤 모든 검사 통과. 운영 반영 전 실패이며 과거 evidence 수정 없음.
- 새 원격 evidence `history/20261106-forum-cohort-runtime-01/`, 로컬은 이 고유 폴더만 작성. 대형 archive/sandbox/backup은 보존하되 commit 제외. 과제 `weeks/week-08/README.md`, `scripts/check_week08.py` 부재로 과제 checker 미실행.
