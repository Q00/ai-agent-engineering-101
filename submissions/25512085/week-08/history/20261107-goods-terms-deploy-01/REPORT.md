# 3SKU seller VM 후보 검증 — 활성 배포 보류

## 변경 파일 / 결과

- 로컬 변경은 이 고유 `history/20261107-goods-terms-deploy-01/`의 helper,
  두 파일 patch, manifest, AST 증거, 원시 합성 테스트 로그 및 보고서와
  `logs/goods-terms-vm-hold-20261107-01.log`뿐이다. 운영 소스는 수정하지 않았다.
- VM에는 `submissions/25512085/week-08/history/20261107-goods-terms-runtime-01/`
  격리 후보/검증 자료만 생성했다. **운영 파일 교체 0, 재시작 0, 시그널 0,
  원격 commit 0. 실배포 시각 없음. 배포 완료가 아니다.**
- 마지막 읽기: **2026-11-06 16:16:49.545380 UTC / 2026-11-07 01:16:49 KST**.
  원격 HEAD는 계속 `4a5deb685368343abfd1310c572288a924351b01`.
- 운영 seller SHA256:
  `708845c79e7d9fc33ec0218f93312e452df72f0544beaff9484831ada5517227`.
- 미적용 후보 seller SHA256:
  `2292cba1f710db57bc446a13079674553d31f4aa8639eeb20c6ca38571678d17`.
- buyer SHA256 불변:
  `7e7a8a5905b29b9c583840e4a16ead2ed5a6d5f6698b5cda22c1709c2b60fc23`.

## 변경 요지 / 범위 검증

AGENTS/current status/history, 로컬 REPORT와 6c17b35/1ada6cf/372bc6b,
기존 integer-wire control/remote/inspect/runner를 먼저 읽었다. 이전 mutation
helper는 실행하지 않았다. 신규 helper만 기존 gcloud 인증으로 실행했다.

후보 attribution은 최종 로컬 `0a674221f020c98a0ce8c553ca0f093128692d82`.
원격 seller는 로컬 파일 `95bd83e73c2a6eb3a54f66de5b34587768f26df7` 기준과
바이트/AST가 동일하여 seller 한정 delta를 정확히 적용할 수 있었다. 후보는
그 delta와 신규 `test_goods_terms_pilot_offline.py`만 포함한다. 명시한 3SKU
C0 우선/A0 명시 기본/긍정 명령 A72 재견적/기존 약속 보존을 검증했다.

원격 cash policy는 monolithic 그대로 유지했다. 로컬 facade의 config/number,
내부 warranty_hours/minimum_contribution/profit_floor/reference_price/quote 및
refund_probability가 주입된 policy 매개변수를 제거하면 원격 함수와 AST가
동일함을 확인했다. live facade 전달 wrapper, 인자/default, PRIOR_BREAK 및
config 바이트도 검사했다(`dependency-equivalence.json`). refactor 전체 sync나
cherry-pick은 하지 않았다. 후보 격리 tree의 기존 56개 파일 중 seller 외
**55개 불변**, 운영은 검사 대상 **56개 전부 불변**이다. buyer/guard/forum/
restock/kit/config는 미변경이다.

## 실제 실행 명령 / 테스트 결과

공통 prefix:
`.venv/Scripts/python.exe -B submissions/25512085/week-08/history/20261107-goods-terms-deploy-01/control.py`

| 인자 | 실제 결과 |
|---|---|
| `inspect`, `read-runtime` | 원격 HEAD/clean/소스 해시/프로세스 확인, allowlist 코드만 수집 |
| 첫 `prepare` | Windows CRLF와 git LF 바이트 비교 assertion 실패, 원본 실패 기록 보존 |
| 수정 후 `prepare`, `dependencies` | 커밋 LF 사용/정규화 worktree 대조, seller 기준선 및 의존 AST 동등성 통과 |
| `stage` | 새 고유 history만 생성, source-only tar 전송, 운영 미변경 |
| `validate` | 아래 네 세트 VM Python **3.10.12**로 실행, 예상 결과 충족 |
| `observe-hold` | 로컬 카드/board/공개 카드 GET **200/200/200**, seller/buyer alive **true/true** |
| `export` | 합성 로그/결과 및 비민감 관측 **12개**만 반출, 운영 원시 로그 반출 없음 |

| 세트 | 발견 | 실패 | 오류 | skip | 결과 |
|---|---:|---:|---:|---:|---|
| 원격 baseline 전체 | 440 | 0 | 0 | 2 | **438 실행 통과** |
| baseline + 신규 fixture 집중 | 107 | 67 | 7 | 0 | 의도한 red; 미구현 warranty_override TypeError 7개 포함 |
| 후보 집중 | 107 | 0 | 0 | 0 | **107 통과** |
| 후보 전체 | 475 | 0 | 0 | 2 | **473 실행 통과** |

실패 수는 subtest 포함이다. 후보 집중에는 부정 등급 최초/후속 거절, 한국어/
영어 최소 보증 및 미지원 시간 거절, A72 부정·불명확 답변의 전체 세션 불변과
명시 긍정 선택의 무서명 재견적 회귀가 전부 포함된다. 기준선과 후보의 차이는
35개 신규 파일 테스트다. 로컬 split 구조의 534개 발견 결과를 원격에 재사용해
통과했다고 주장하지 않는다. 원격 monolithic 가능 세트만 실행했다.

두 skip은 기존 LauncherTests의 bash/하위 Python 실행 테스트다. runner가
하위 프로세스를 차단하므로 명시 skip했다. fake HOME/TEMP, 최소 환경,
외부 socket/원본 저장소 접근 차단, 고유 evidence 밖 쓰기 차단 하에서 실행했다.
격리 tree의 모든 Python 소스를 메모리 compile했고 patch check/apply와 후보
해시/바이트도 검증했다. 주차 README/checker 부재로 주차 checker 미실행.
로컬 helper 4개도 `python -B -c ... compile(...)`로 메모리 compile 통과했다.
helper/REPORT 한정 `git diff --cached --check`는 exit 0이다. 원시 로그를
포함하는 전체 역사 diff의 whitespace 통과는 주장하지 않는다.

## 보류 사유 / 운영 안전성

원격 seller `Seller.__init__`의 `self.open`과 build_app의
`InMemoryTaskStore()`는 메모리 상태다. 등록 라우트는 카드와 JSON-RPC이며
읽기 전용 pending-count, 신규 협상 차단, drain/export/import 경로가 없다.
kit watchdog는 종료된 seller를 같은 명령/cwd/port로 다시 시작하지만 기존
협상 메모리를 옮기거나 신규 유입을 막아 배출하지 않는다. HTTP graceful
종료만으로 여러 턴 협상 사이에 대기 중인 조건 약속까지 안전하다고 볼 수 없다.

따라서 pending 세션 안전 여부는 **unknown**이다. 운영 state/메모리/원문 대화에
접근하거나 임의 SIGTERM을 보내지 않았다. 사용자의 "판별 못하면 임의 kill
말고 보류" 지시를 적용했다. 자동 후속 적용/재시작 job도 만들지 않았다.
backup/atomic replace/fsync/한정 원격 commit은 **미실행**이며 실행됐다고
주장하지 않는다. 디스크만 교체해 나중의 crash 재시작에 몰래 적용되는 상태도
만들지 않았다.

PID/starttime은 최초와 마지막 관측에서 동일하다: kit **43679/1037901**,
tunnel **43696/1038186**, seller **299169/7735542**, seller port **52507**.
카드 버전은 기존 **3.2.0**. 단 한 번 65536-byte bounded tail을 메모리에서
집계했다: seller marker 3, quote marker 0, stock-miss marker 0, restart marker 0.
이것은 pending=0의 증거나 새 동작 관측이 아니다. **신규 동작 및 실거래 효과
미확인**이며, 새 협상 RPC/테스트 거래/POST/게시/매입/ledger 스캔은 하지 않았다.

다음 결정은 메인/사용자에게 넘긴다: 기존 협상 배출을 확인할 수 있는 최소한의
drain/pending 계측을 별도 범위로 승인하거나, 협상 메모리 손실 위험을 명시적으로
수용한 유지보수 창을 정해야 한다. 어느 경우든 재개 시 HEAD/dirty/hash/process를
다시 읽고 재검증해야 하며, 현재 결과를 미래 precondition으로 재사용하면 안 된다.

기존 사용자 dirty/untracked/실험 로그는 제외했다. env/pem/key/token/cookie,
운영 state/.market 내용은 읽거나 복사하지 않았다. 비밀 출력/설치/cleanup/
push/Git history rewrite/다른 에이전트 호출 없음. 원시 실패/테스트 로그는
정리하지 않고 보존했다. 중복 tar/소스 snapshot/transport archive는 로컬 보존만
하고 커밋하지 않는다.
