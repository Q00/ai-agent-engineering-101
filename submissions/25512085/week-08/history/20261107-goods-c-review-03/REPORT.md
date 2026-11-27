# P1 명시 조건 부분 파싱 수정 — 최종 revision05, 운영 미적용

## 변경 파일 / 최종 준비 자료

- `seller.py`: `goods_request_terms`만 변경. 가격·선택·세션 처리 함수는 이전
  승인 후보(72afb71)와 AST 동일하다.
- `test_goods_terms_pilot_offline.py`: 최초/후속·가격 유무 및 지원/미지원 문법 회귀.
- `POLICY.md`, `GRADE_WARRANTY_POLICY.md`: 조건 전체 검사/중복 거절 정책 명시.
- 과정은 `history/20261107-goods-c-review-01/`, `-02/`, 이 `-03/` 및 신규
  `logs/goods-c-review-20261107-01.log`에 보존한다. 기존 로그를 정리하지 않았다.

**최종 인계 자료는 이 디렉터리의 `manifest.json`, `backport.patch`,
`remote-evidence/remote-tests.json`, `verify-validated-artifacts.json`이다.**
revision02/03/04는 중간 후보이며 현재 활성화 대상으로 쓰면 안 된다.

- 최종 source commit: `beab1f7b73ad06764ae886c59a871f4444e32a94`.
- 원격 격리 후보:
  `/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08/history/20261107-goods-c-only-runtime-05`.
- seller SHA256 (git LF / 원격 후보 바이트):
  `a095ee56a9a8e606a1a5ba126caaf14c0d60cd171278203335ad30f7cec2d225`.
- test SHA256:
  `04a6d267a2648b6d22a77f8c008155235870505b3188d9369707ca72691b0d48`.

## 변경 요지 / P1 해결 근거와 검토 라인

문제는 한 조건이 파싱되면 다른 미지원 조건을 묵살하던 것이다. 이제 **각 field의
출현 위치와 소비 범위**를 대조하며, 지원 값 뒤 미소비 연산자/등급 집합도 검사한다.

1. `seller.py:209-238`: match 위치를 보존하고 모든 grade field/한국어 등급 표지를
   검사한다. `grade=C grade!=C`의 두 번째 `grade`는 지원 match로 소비되지 않으므로
   `invalid=True`다. 이미 인식한 C가 이 검사를 건너뛰게 하지 않는다.
2. `seller.py:215,245-255,276-289`: 모든 `warranty_hours`와 자연어 보증 표지를
   검사한다. `warranty_hours>=72`는 지원 `:`/`=` assignment가 아니므로 거절한다.
   이 문법을 0시간 요구나 모호한 ‘보증 있음’으로 낮춰 B6로 체결하지 않는다.
3. `seller.py:218-238,278-289`: 값 뒤 연산자/집합/identifier suffix를 검사한다.
   `grade_min`, `warranty_hours_min`, `grade=C/D`, `grade=C or D`, Unicode 비교기호,
   숫자 없는 미지원 보증 연산자도 조용히 버리지 않는다. 마지막 보강은 지원 match가
   없는 보증 표지에도 동일한 잔여 연산자 검사를 적용한 것이다.
4. `seller.py:216,276-277`: 동일 값도 포함한 중복 grade/warranty 조건은 하나의
   명확한 조건으로 다시 요청한다. 전역 minimum 플래그가 다른 exact 조건을
   약화시키지 않도록 보수적으로 거절한다. 서로 다른/모순 값도 거절한다.
5. `seller.py:447` 및 `1167`: 기존 최초/후속 거절 경로가 invalid를 가격 소비 전에
   처리한다. 이 함수들은 이번 수정에서 변경하지 않았다.
6. `test_goods_terms_pilot_offline.py:350-434`: 두 핵심 반례의 최초/후속 × 가격
   유무, 중복·모순·미지원 연산자·field suffix·잔여 집합과 지원 단일 조건 회귀.

`grade>=B cheap`, `grade B or above cheap`, `warranty of at least 72h`,
`보증 최소 72시간`은 계속 정확한 최소 조건으로 처리한다. `warranty_hours>=72`를
새 문법으로 지원한 것은 아니며, 지원 자연어 최소 보증 문법으로 다시 요청해야 한다.
이는 제한된 규칙 문법이지 임의 자연어 전체를 이해하거나 숨겨진 accept를 보장한다는
뜻은 아니다. A0 제외 / 대상 3SKU C 우선 / A72-B6-C0 / 기존 약속 / 다른 SKU /
가격·자동 할인·매입·forum·buyer 불변 범위는 유지한다.

## 실제 검증 결과 / 실패 기록

공통 명령 prefix:
`.venv/Scripts/python.exe -B submissions/25512085/week-08/history/`

| history / check.py 인자 | 발견 | 실패 | 오류 | skip |
|---|---:|---:|---:|---:|
| goods-c-review-01 / baseline-focused focused | 111 | 0 | 0 | 0 |
| goods-c-review-01 / red-focused focused | 117 | 49 | 0 | 0 |
| goods-c-review-01 / green-focused focused | 117 | 0 | 0 | 0 |
| goods-c-review-01 / boundary-red focused | 119 | 16 | 0 | 0 |
| goods-c-review-01 / final-focused focused | 119 | 0 | 0 | 0 |
| goods-c-review-01 / final-all | 546 | 0 | 0 | 2 |
| goods-c-review-01 / symbolic-red focused | 119 | 6 | 0 | 0 |
| goods-c-review-02 / final-focused focused | 119 | 0 | 0 | 0 |
| goods-c-review-02 / final-all | 546 | 0 | 0 | 2 |
| goods-c-review-02 / uncovered-symbol-red focused | 119 | 4 | 0 | 0 |
| goods-c-review-03 / final-focused focused | 119 | 0 | 0 | 0 |
| goods-c-review-03 / final-all | 546 | 0 | 0 | 2 |

실패는 subtest 포함이며 원시 로그/manifest/실패 commit 모두 보존했다.
**최종 로컬 집중 119 / 전체 544 실행 통과 + 기존 launcher skip 2개.**
초기 untracked `test_market_monitor_offline.py`는 source-only snapshot에 포함하지 않는다.

각 신규 history의 `control.py inspect/prepare/dependencies/stage/validate/export`로
revision03/04/05를 별도 격리 후보로 만들고 전부 검증했다. 기존 mutation CLI를
재실행하지 않았다. 최신 revision05의 VM Python 3.10.12 결과:

| 세트 | 발견 | 실패 | 오류 | skip |
|---|---:|---:|---:|---:|
| 운영 source baseline 전체 | 440 | 0 | 0 | 2 |
| baseline + 최종 fixture 집중 red | 119 | 148 | 0 | 0 |
| 최종 후보 집중 | 119 | 0 | 0 | 0 |
| 최종 후보 전체 | 487 | 0 | 0 | 2 |

**최종 VM 집중 119 / 전체 485 실행 통과 + 2 skip.** 47개 pilot fixture를 포함한다.
이전 revision03/04도 집중 119 및 전체 485 실행 통과했으나 이후 경계 subcase가
늘었으므로 그것을 최종 revision05 결과로 대체하지 않았다. 두 skip은 기존 bash/
자식 Python launcher 테스트이며 엄격한 subprocess 금지 때문에 명시 skip했다.

fake HOME/TEMP·합성 재고/임시 키·외부 socket/원본 repo/하위 프로세스 차단·고유
evidence 밖 쓰기 금지 유지. VM 모든 snapshot Python 메모리 compile, 격리 tree의
git apply --check/apply 및 후보 해시/기존 55개 파일 불변 검증 통과. monolithic
의존 함수와 split facade의 AST/wrapper/default/PRIOR_BREAK/config 동등성 재검증.
후보는 seller와 신규 fixture 두 파일만이며 전체 sync/cherry-pick하지 않았다.

`goods-c-review-03/verify.py validated`: 소스/후보/helper 10개 py_compile(고유 cfile),
72afb71 대비 변경 정의가 parser 하나뿐임, 소스/정책/helper 한정 diff --check exit 0.
`verify_validated.py`: commit/후보/manifest 일치 및 완료된 원격 테스트 증거 검증 통과.
주차 README/checker는 부재로 미실행. 원시 로그 포함 전체 역사 diff의 whitespace
통과나 실제 거래/결제 검증은 주장하지 않는다.

### 과정 commit

- `89b7ecb`: 두 반례 및 중복/미지원 조건 red.
- `ce57ba3`: 전체 field 소비 검사 / 집중 green.
- `71bba76`: identifier/잔여 집합 추가 red.
- `78db4fd`: 경계 수정 / revision03 소스.
- `62151e7`: revision03 증거와 기호 연산자 red.
- `aa94004`: 일반 기호 잔여 검사 / revision04 소스.
- `58c880a`: revision04 증거와 미파싱 보증 기호 red.
- `beab1f7`: 미파싱 보증 표지까지 동일 검사 / **최종 revision05 소스**.
- 후속 인계 commit은 증거/보고서만 추가한다. squash/history rewrite 없음.

## 미해결 / 활성화 금지 유지

**운영 교체 0 / 재시작 0 / 시그널 0 / 원격 commit 0 / 실제 배포 없음.**
최종 원격 검증 종료(후단 guard 포함): **2026-11-07 02:26:53.985863 UTC / 11:26:53 KST**.
HEAD `4a5deb685368343abfd1310c572288a924351b01`, tracked/staged clean,
allowlist 56개 hash가 최초 및 기존 보류 기준선과 동일함을 post-test guard가 확인했다.

- 운영 seller: `708845c79e7d9fc33ec0218f93312e452df72f0544beaff9484831ada5517227`.
- market_buyer: `7e7a8a5905b29b9c583840e4a16ead2ed5a6d5f6698b5cda22c1709c2b60fc23`.
- 마지막 성공한 프로세스 관측은 **02:23:38.400624 UTC**의 inspect다:
  kit 43679/1037901, tunnel 43696/1038186, seller 299169/7735542, port 52507.
- 이후 read-only `observe-final`은 foreground shell 300000ms timeout으로 **미확인**.
  당시 stderr는 비어 있었고 stdout 파일이 없었다. 연결 뒤의 `verify.py final`도
  실행된 것으로 취급하지 않는다. 별도 `verify.py validated`/`verify_validated.py`만
  성공으로 기록했다. 실패 관측을 덮어쓰거나 mutation을 재실행하지 않았다.

pending 세션은 unknown이다. 메인 독립 검토 후에도 실제 배포는 별도 지시와
drain/pending 안전성 결정 또는 명시 위험 수용 유지보수 창이 필요하다. 재개 시
HEAD/dirty/hash/PID/starttime을 다시 읽어야 하며, 시간 초과한 프로세스 관측이나
현재 테스트 통과를 미래 안전성 근거로 재사용하면 안 된다. 최종 revision05 두 파일
patch만 검토하고, 운영 파일 선교체/임의 kill/자동 후속 job은 만들지 않는다.

기존 dirty logs/test-report 및 untracked 원본 보존. 운영 state/.market/키/env/pem
읽기, 새 RPC/POST/HTTP 거래/매입/forum 게시, 설치, push, 다른 에이전트 호출 없음.
