# Compound minimum-grade exclusion P1 — revision06 준비 완료, 운영 미적용

## 변경 파일 / 최신 artifact

- `seller.py`: `goods_request_terms` 안의 최소 등급 span/부정/길이 검사만 변경.
- `test_goods_terms_pilot_offline.py`: 최초·후속 × 가격 유무, 지원 단일 조건,
  무해한 상품명 및 조건 span 길이 회귀 5개 메서드 추가.
- 정책 문서 2개, 이 신규 history 및 `logs/goods-c-review06-20261107-01.log`.
- **최신 준비 자료는 이 디렉터리의 `manifest.json`, `backport.patch`,
  `remote-evidence/remote-tests.json`, `observe-final-stdout.log`, `verify-final.json`.**
  이전 revision05 이하를 최종 후보로 사용하면 안 된다.
- 최종 source commit: `2fbd842b2bf5b02716e479fc81099ee4bbe1e7a0`.
- 원격 격리 경로:
  `/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08/history/20261107-goods-c-only-runtime-06`.
- seller SHA256 (git LF / 원격 후보 바이트):
  `17c653f836436975268651436e9f7cf0e1bfda9b1fdfeb6615de4b67df4a13fd`.
- test SHA256:
  `ad9568062c09b6689925078b284b1a5ab356620ab5dc10fc40ef2ceb5a4432cf`.

## 해결 요지 / 독립 리뷰 라인

1. `seller.py:209-212`: 지원하는 `or above`/`이상`을 grade match 전체에 포함한다.
   따라서 `grade B or above except A`가 `or above`까지만 건너뛰어 뒤 조건을
   버리는 대신, 접미사 뒤의 잔여 문법을 검사한다(`237`). 반복 `or`도 거절한다.
2. `seller.py:243-249`: 등급이 명시된 메시지 전체에서 미지원 복합 제외·부정을
   검사한다. `except`, `excluding`, `but not`(기존 not 검사), `제외`, `빼고`,
   `말고`는 최소 접미사 앞/뒤나 중간 상품명·문장 구분자와 무관하게 fail-closed다.
   제외의 여집합을 추측해서 A72 또는 다른 등급으로 체결하지 않는다.
3. `seller.py:216-218`: 지원 문법으로 인식된 명시 등급 조건 span만 128자로
   제한한다. 상품명/전체 메시지를 자르거나 길이 제한하지 않는다. 전체 prose를
   whitelist하면 정상 SKU/상품명을 거절하므로, 조건 문법의 긍정 allowlist와
   등급 동반 복합 부정 거절을 조합했다. 텍스트를 잘라 검사하지 않는다.
4. 새 영어 토큰은 단어 경계, 새 `빼고`는 끝 경계를 확인한다. `Exceptional`,
   `Excludington`, `빼고테이블` 및 긴 무해한 이름은 오인하지 않는 회귀를 실행했다.
   기존 부정 표현의 보수적 동작은 유지한다. 임의 자연어 이해를 보장하지 않는다.
5. 기존 최초 거절 `seller.py:452`, 후속 거절 `1172`는 그대로 사용하며 가격 소비
   전에 invalid를 거절한다. `test_goods_terms_pilot_offline.py:432-478`에 핵심
   반례 및 같은 부류 12개 표현의 최초·후속 × 가격 유무를 포함했다.

단일 `grade B or above`는 기존 A72 선택, `cheap`이 있으면 B6 선택을 유지한다.
한국어 `B급`은 원래 저가 힌트이므로 `B급 이상`도 B6를 선택한다. 이 기존 선택을
바꾸지 않고 새 fixture의 기대값만 바로잡았다. A0 완전 제외/A72 유지/3SKU C 우선/
기존 약속/다른 SKU/가격·할인·매입·buyer·forum 불변 범위는 유지한다.
AST 검사에서도 parser 외 기존 함수·가격·선택·세션 정의는 동일하다.

## 실제 실행 / 테스트 결과

공통 prefix:
`.venv/Scripts/python.exe -B submissions/25512085/week-08/history/20261107-goods-c-review-04/`

| 명령 | 발견 | 실패 | 오류 | skip | 비고 |
|---|---:|---:|---:|---:|---|
| `check.py baseline-focused focused` | 119 | 0 | 0 | 0 | 기존 기준선 |
| `check.py red-focused focused` | 124 | 38 | 0 | 0 | 수정 전 재현; subtest 포함 |
| 중단 전 `check.py final-focused focused` | 124 | 1 | 0 | 0 | 위 B급 fixture 기대값 오류 |
| 중단 전 `check.py final-all` | — | — | — | — | 완료 marker 없음, 미완료로 보존 |
| `check.py resumed-focused focused` | 124 | 0 | 0 | 0 | **최종 집중 통과** |
| `check.py resumed-all` | 551 | 0 | 0 | 2 | **549 실행 통과** |

재개 시 HEAD 5024fd7/기존 dirty seller와 원시 로그를 먼저 확인했다. 원본을
되돌리거나 기준선/red를 중복 실행하지 않았다. 중단 당시 전체 로그는 unrelated
restock 테스트 도중 멈췄고 성공/실패 집계가 없어 통과로 취급하지 않는다.

원격 `control.py prepare/dependencies/stage/validate/export/observe-final` 실행 완료.
VM Python 3.10.12, 52개 pilot fixture 포함 결과:

| 원격 세트 | 발견 | 실패 | 오류 | skip |
|---|---:|---:|---:|---:|
| baseline 전체 | 440 | 0 | 0 | 2 |
| baseline + 최종 fixture 집중 red | 124 | 199 | 5 | 0 |
| revision06 집중 | 124 | 0 | 0 | 0 |
| revision06 전체 | 492 | 0 | 0 | 2 |

**VM 집중 124 / 전체 490 실행 통과 + 2 skip.** baseline red의 오류 5개는 운영
기준선에 아직 없는 `goods_request_terms` 직접 호출의 AttributeError로 확인했다.
후보 오류는 0이다. 두 skip은 기존 bash/자식 Python 실행 launcher 테스트다.

fake HOME/TEMP·합성 재고/임시 서명 키·원본 repo/외부 socket/하위 프로세스 차단·
고유 evidence 밖 쓰기 금지를 유지했다. VM snapshot 모든 Python 메모리 compile,
격리 tree git apply --check/apply, 후보 hash/기존 55개 파일 불변 검증 통과.
monolithic 의존 함수/로컬 facade의 AST/wrapper/default/PRIOR_BREAK/config 동등성
재검증 통과. 전체 sync/cherry-pick 없이 seller와 신규 fixture 두 파일만 후보에 적용.

`verify.py resumed`: 7개 py_compile, parser-only AST, 제한 diff-check exit 0.
`verify.py final`: source/snapshot/helper 9개 py_compile(고유 cfile), commit/후보/
manifest/관측 hash 일치, 프로세스 메타데이터 및 운영 allowlist 56개 불변 통과.
주차 README/checker 부재로 미실행. 원시 로그 포함 전체 역사 diff의 whitespace
통과 및 실거래/결제 효과는 주장하지 않는다. 초기 untracked 테스트는 snapshot 제외다.

### Commit

- `5024fd7`: 수정 전 기준선/red와 회귀 추가.
- `363a734`: 중단된 seller 수정 이어서 보존, 최소 접미사/전역 부정/길이 검사;
  fixture 실패 및 미완료 전체 로그 보존.
- `2fbd842`: 기존 B급 선택을 존중한 fixture 교정, 로컬 집중/전체 통과, 정책 기록.
- 후속 evidence/report commit은 소스 변경 없이 최종 원격 증거와 인계만 추가한다.

## 미해결 / 운영 적용은 별도 단계

**운영 교체 0 / 재시작 0 / 시그널 0 / 원격 commit 0 / 실배포 미수행.**
마지막 성공한 읽기: **2026-11-07 05:41:18.239731 UTC / 14:41:18 KST**.
HEAD `4a5deb685368343abfd1310c572288a924351b01`, tracked/staged clean,
운영 allowlist 56개 모두 기존 보류 기준선 및 이번 baseline과 동일하다.
seller 운영 hash는 `708845c79e7d9fc33ec0218f93312e452df72f0544beaff9484831ada5517227`,
market_buyer는 `7e7a8a5905b29b9c583840e4a16ead2ed5a6d5f6698b5cda22c1709c2b60fc23`.
kit 43679/1037901, tunnel 43696/1038186, seller 299169/7735542의 PID/starttime
불변을 확인했다. 이전 revision05 관측 timeout 기록은 그대로 보존했으며 이번
revision06 새 read-only 관측은 실제로 성공했다.

pending 협상 수/메모리는 미조회·unknown이다. **메인 독립 리뷰용 준비 완료**이지
운영 활성화 완료가 아니다. 다음 적용 지시 때 HEAD/dirty/hash/process를 다시 읽고,
drain/pending 안전성 또는 명시 위험 수용 유지보수 창을 결정해야 한다. 기존 메모리
협상을 임의 kill하거나 디스크만 미리 교체하지 않는다. 최신 revision06 두 파일
patch만 검토하고 monolithic 구조·buyer/매입/forum/kit/config는 그대로 유지한다.

기존 dirty logs/test-report 및 untracked 원본을 수정/스테이징하지 않았다. 운영
state/.market/키/env/pem 읽기, 새 거래/RPC/POST/HTTP, 설치, push, history rewrite,
다른 에이전트 호출 및 자동 후속 배포 job 생성 없음.
