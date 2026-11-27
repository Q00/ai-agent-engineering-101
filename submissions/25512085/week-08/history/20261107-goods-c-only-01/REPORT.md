# C 우선 전용 후보 — A 무보증 제외, 실배포 미수행

## 1. 변경 파일 / 인계할 후보

- 실제 소스: `seller.py`, `test_goods_terms_pilot_offline.py`.
- 정책: `POLICY.md`, `GRADE_WARRANTY_POLICY.md`.
- 과정·격리 helper·원시 실패/테스트 로그·manifest: 이 history와
  `logs/goods-c-only-local-20261107-01.log`, `logs/goods-c-only-vm-20261107-01.log`.
- **최종 후보는 `revision02/manifest.json` / `revision02/backport.patch`이다.**
  상위 manifest 및 runtime-01은 이전 중간 후보이므로 활성화 대상으로 쓰지 않는다.
- 최종 소스 commit: `f06f9d7b8fa852cb74ad00e6e92288ff7b4de98a`.
- 최종 VM 격리 경로:
  `/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08/history/20261107-goods-c-only-runtime-02`.
- 최종 seller SHA256 (git LF / 원격 후보 바이트):
  `0eb5113c27d92075235d0ea3944eee60954e7da3e0235191dd1d22cc16d717fd`.
- 최종 test SHA256:
  `5caaf1c3d77daf28296305e870eab5e3baf9f1eed856e41d7ae6039b80e5d6a0`.

기존 dirty `logs/market-test.log`, `test-report.json` 및 초기 untracked 원본은
수정/스테이징하지 않았다. 새 snapshot은 추적된 allowlist 소스만 사용하므로
초기 untracked `test_market_monitor_offline.py`는 전체 테스트 집계에 포함하지 않는다.
주차 README와 `scripts/check_week08.py`는 없으며 확인 실패도 숨기지 않는다.

## 2. 변경 요지 / 학습용 동작 설명

| 상황 | 이번 후보의 동작 |
|---|---|
| 대상 3SKU 신규 무제약 문의 | 재고 C0을 우선 선택; C가 없으면 기존 선택 |
| A 명시, 보증 언급 없음 | **A72 유지**, A0 제안/선택 안내 없음 |
| A + `warranty_hours=0` / no-care | 기존처럼 A72 가능; 보증을 새로 없애지 않음 |
| B 명시 / B 이상 / 6h·72h 최소 조건 | 실제 등급/보증을 만족하는 재고만; 불가하면 영수증 없이 거절 |
| C 부정문 / 지원하지 않는 명시 문법 | 최초·후속 fail-closed; 여집합을 추측하지 않음 |
| 이미 제시한 A72/B6/C0 | 숫자 답변으로 약속을 바꾸지 않음 |
| 다른 SKU·식품·매입·forum·buyer | 기존 경로 유지 |

핵심은 **재고 선택 변경과 보증 상품 추가를 분리**하는 것이다. C를 먼저 보여주는
선택 규칙만 도입하고, A 무보증 상품/옵션 전환/재견적/가격·환불위험 override는
제거했다. 가격 비교·할인·보증부 기본 하한 계산은 원래 함수 AST와 완전히 같다.
기존 C 전용 무보증 설명도 원문으로 복원하여 A0 전용 일반화 문구까지 남기지 않았다.
이는 제한된 공개 문법 처리이며 숨겨진 구매 조건이나 결제 성공을 보장하지 않는다.

### 메인 독립 검토 지점

1. `seller.py:goods_request_terms`: 기존 검토의 C 부정/최소보증/미지원 기간 보강 유지.
2. `_select_preferred_unit`: 3SKU 내부 필터와 무제약 C 우선, 나머지 기존 우선순위.
   명시 0은 A/B 보증을 벗겨내지 않지만 양의 exact 보증은 여전히 필터링한다.
3. `Seller.execute` 후속 조건 검사: 불일치면 가격을 소비하기 전에 거절; 옵션 분기 없음.
4. `revision02/backport.patch`: seller와 신규 fixture **두 파일만** VM 후보에 적용.
   monolithic cash policy는 통째 sync/cherry-pick하지 않았다.
5. `dependency-equivalence.json`, `revision02/dependency-equivalence.json`,
   `artifact-review.json`: facade 인자 제거 후 실제 사용 policy 함수·wrapper·기본값·
   PRIOR_BREAK·config 동등성, 최종 참조 집합 불변, 가격 AST 동일.
   원격 기준선 대비 기존 top-level 정의 중 변경은 `_select_preferred_unit`와
   `Seller.execute`뿐이며 parser 한 개를 추가했다. 다른 Seller 메서드는 동일하다.

## 3. 실제 명령 / 테스트 결과

공통 prefix (저장소 루트):
`.venv/Scripts/python.exe -B submissions/25512085/week-08/history/20261107-goods-c-only-01/`

| 명령 인자 | 발견 | 실패 | 오류 | skip | 결과 |
|---|---:|---:|---:|---:|---|
| `check.py baseline-focused focused` | 107 | 0 | 0 | 0 | 기존 집중 기준선 |
| `check.py baseline-all` | 534 | 0 | 0 | 2 | 기존 전체 기준선 |
| `check.py red-focused focused` | 108 | 27 | 0 | 0 | 의도한 새 계약 red |
| `check.py green-focused focused` | 108 | 1 | 0 | 0 | 보증 안내 문자열 fixture 차이, 원시 로그 보존 |
| `check.py final-focused focused` | 111 | 0 | 0 | 0 | 첫 후보 집중 |
| `check.py final-all` | 538 | 0 | 0 | 2 | 첫 후보 전체 |
| `check.py revision02-focused focused` | 111 | 0 | 0 | 0 | **최종 집중 통과** |
| `check.py revision02-all` | 538 | 0 | 0 | 2 | **최종 536 실행 통과** |

최종 원격 실행:
`control_v2.py prepare`, `stage`, `validate`, `export`, `observe-final`.
기존 mutation helper CLI는 재실행하지 않았다. 이전에 검토된 읽기 전용 inspect,
순수 로컬 AST 검사 함수, 원격 격리 validator만 제한적으로 재사용했다.

| 최종 VM Python 3.10.12 세트 | 발견 | 실패 | 오류 | skip |
|---|---:|---:|---:|---:|
| baseline 전체 | 440 | 0 | 0 | 2 |
| baseline + 새 fixture 집중 red | 111 | 50 | 0 | 0 |
| 최종 후보 집중 | 111 | 0 | 0 | 0 |
| 최종 후보 전체 | 479 | 0 | 0 | 2 |

**VM 최종 477 실행 통과 + 2 skip**. 이전 보류 후보 107/473 결과를 새 후보
통과로 대신하지 않고, 새 39개 fixture를 포함하여 전부 다시 실행했다.
중간 runtime-01도 111 집중/477 전체 통과했고 자료를 덮어쓰지 않았다.
실패 수는 subtest 포함이다. 두 skip은 기존 bash/자식 Python 실행 launcher
테스트이며, subprocess 금지 격리 정책 때문에 명시적으로 제외했다.

fake HOME/TEMP, 합성 재고/임시 서명 키, live repo 접근/외부 socket/하위 프로세스
차단, 새 evidence 밖 쓰기 금지로 실행했다. VM snapshot 모든 Python을 메모리
compile했고 git apply --check/apply는 격리 tree에서만 실행했다.
`verify_artifacts.py`: 최종 commit/후보/관측 해시 일치, 이전 validator 소스 해시
일치, 운영 56개 allowlist 및 프로세스 메타데이터 불변, A0 심볼 제거 검증 통과.
`verify_local.py final03`: seller/test/helper 10개 py_compile(고유 cfile), 가격 AST
완전 동일, 소스·정책·helper 한정 `git diff 01f4be7 --check` exit 0.
원시 로그를 포함한 전체 역사 diff의 whitespace 통과는 주장하지 않는다.

### 과정 commit (실패 보존, squash 없음)

- `72f5741`: 기준선 및 A0 제외 red 회귀.
- `0ef1a4e`: A0 helper/override/옵션 제거; 문자열 fixture 실패 1 보존.
- `ffd166d`: fixture 교정/회귀 추가, 정책 갱신, 로컬 전체 통과/VM 재점검.
- `f06f9d7`: C 전용 원문 설명 복원, 중간 VM 후보 증거 보존; 최종 source attribution.
- 후속 evidence/report commit은 소스 변경 없이 최종 VM 증거와 인계만 기록한다.

## 4. 미해결 / 실제 활성화는 별도 지시 필요

**운영 교체 0 / 재시작 0 / 시그널 0 / 원격 commit 0 / 실배포 미수행.**
마지막 VM 읽기: **2026-11-07 01:26:05.784257 UTC / 10:26:05 KST**.
HEAD `4a5deb685368343abfd1310c572288a924351b01`, tracked/staged dirty 없음.
운영 allowlist 56개 전부 이전 보류 및 이번 기준선과 동일하다. 격리 후보에서
seller 이외 기존 55개는 불변이며, 신규 fixture 한 개만 더했다.

- 운영 seller `708845c79e7d9fc33ec0218f93312e452df72f0544beaff9484831ada5517227`.
- market_buyer `7e7a8a5905b29b9c583840e4a16ead2ed5a6d5f6698b5cda22c1709c2b60fc23`.
- PID/starttime: kit 43679/1037901, tunnel 43696/1038186,
  seller 299169/7735542 (기존 port 52507). 시작 메타데이터 동일.

운영 pending 협상 수는 **unknown / 미조회**다. 앞선 보류 사유인 seller.open 및
InMemoryTaskStore 메모리 상태는 여전히 유효하다. 검증 성공은 안전한 메모리
협상 이전이나 pending=0의 증거가 아니다. 이번에는 HTTP 관측도 하지 않았다.

메인 검토 후 실제 배포 지시가 오면 다음 순서가 필요하다(이번에는 미실행):
1. 사용자/메인이 drain·pending 계측 별도 범위 또는 협상 메모리 손실 위험을
   명시적으로 수용한 유지보수 창을 결정한다. 임의 kill이나 숨은 디스크 선교체 금지.
2. HEAD/dirty/allowlist hash/PID/starttime을 **다시** 읽는다. 현재 관측을 미래
   precondition으로 재사용하지 않는다. 불일치 시 보존하고 중지한다.
3. revision02 manifest와 최종 두 파일 patch를 독립 확인한다. 운영 monolithic
   구조·buyer/매입/forum/guard/kit/config는 유지한다. 전체 sync/cherry-pick 금지.
4. 승인된 유지보수 절차에서 원본 보관·원자적 교체/fsync·정확 파일 한정 commit,
   seller만 승인된 supervisor 경로로 재시작/검증한다. 새 협상·거래·POST는 따로 승인.

실거래/체결/결제 효과, 운영 pending, 새 동작의 운영 반영은 검증하지 않았다.
주차 checker 부재, 위 두 launcher skip 및 제한된 자연어 문법도 남은 한계다.
운영 state/.market/키/env/pem 읽기, 설치, 새 RPC/POST/매입/게시, push, Git history
재작성, 다른 에이전트 호출은 하지 않았다. 원본·실패 로그는 보존했다.
