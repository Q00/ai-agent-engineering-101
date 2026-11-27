# Buyer Saved / surplus 분리 — 1단계 로컬 구현·검증, 미배포

기준 HEAD `a539bdb`. AGENTS.md, 본인 roster 및 기존 kind-fix 구현 문서와
격리 runner를 먼저 확인했다. `weeks/week-08/README.md`는 checkout에 없다.
추가 위임, VM, 네트워크, 설치, 거래, commit/push는 하지 않았다.
새 risk 정책(2단계), 가격·랭킹·구매·판매 정책은 변경하지 않았다.

## 근거와 변경

메인에서 직접 확인하여 전달한 공식 agent.md 문구:
“Surplus=value-price+refund ... Saved ... surplus/list (a deal that served
nothing counts -1, or 0 if refunded), divided by deals+100”. 이번 세션은
공식 문서에 네트워크 접근하지 않았으며 전달된 문구 이상의 부분 환불 해석을
추가하지 않았다.

`buyer_outcomes.py`의 **summarize replay만** 수정했다. collector의 normalize,
prepare, append, validator, read_events 및 schema v1/v2는 그대로다.
운영 journal/key/state는 읽거나 변경하지 않았으며 원본 migration도 없다.

정확한 주문 ID로 연결된, 종류·원장·unit·시각·금액 검증을 통과한 goods broken:

| 환불 | normalized_surplus_contribution | saved_contribution | unknown_reason |
|---|---|---|---|
| 0 | -paid/list | -1 | null |
| 전액, paid > 0 | 0 | 0 | null |
| 0 < refund < paid | (refund-paid)/list | null | partial_refund_semantics_unknown |
| 누락 또는 refund > paid | null | null | refund_unknown |

`normalized_surplus_unknown_reason`은 surplus가 계산되면 null이며 그렇지 않으면
해당 unknown 이유다. 기존 `unknown_reason`은 이제 명시적으로 Saved의 unknown
이유를 나타낸다. 부분 환불은 normalized surplus가 알려져도 Saved는 unknown이다.
paid=refund=0은 기존 분기처럼 **무환불 우선**으로 취급한다: surplus 0, Saved -1.
양의 결제액에 대한 전액 환불만 Saved 0으로 판정한다.

### 호환성 — 기존 필드는 Saved로 재정의하지 않음

`known_broken_contribution`은 **deprecated legacy normalized surplus**다.
기존 소비자 값과 unknown 범위를 보존했다: 무환불 -paid/list, 전액 환불 0,
부분 환불 null. 새 normalized 필드와 달리 부분 환불은 계속 null이다.
이를 Saved로 읽으면 잘못된 해석이다. Saved 소비자는 `saved_contribution`으로,
surplus 소비자는 `normalized_surplus_contribution`으로 명시적으로 이동해야 한다.
단위가 다른 값을 같은 이름으로 몰래 바꾸지 않았다. 기존 테스트의 expected는
바꾸지 않았으며, 새 테스트에서 legacy 값과 Saved가 다른 사실을 검증했다.

소비자 검색은 week-08 최상위 Python 파일에 한정했다. 테스트 외에는 기존 기여/
official_saved/종류 통계 필드를 읽는 소비자가 없었다. history 사본의 소비자는
과거 증거이므로 수정하지 않았다. 정책 입력으로 연결한 곳도 없다.

## unknown과 분모

- food served/value 부재, kind 누락/충돌, legacy kind unknown을 유지한다.
- 정확한 order join 실패, 원장 충돌, unit/시각 불일치, truncated replay는
  두 새 기여 모두 unknown이다. 환불 합산이나 이름·시간 기반 join은 없다.
- 무고장 이벤트 부재는 성공 증명이 아니다. 6시간 경과에도 coverage unknown이다.
- `official_saved=null`, `complete=false`, 모든 `observation_complete=false` 유지.
  알려진 일부 기여를 평균하거나 공식 Saved 또는 성과로 보고하지 않는다.
- 새 `contribution_counts.retained_order_denominator`는 반환된 cohort 수다.
  saved_known_orders + saved_unknown_orders 및
  normalized_surplus_known_orders + normalized_surplus_unknown_orders는 각각
  이 분모와 같다. partial은 surplus known / Saved unknown으로 따로 센다.
  중복 event나 coverage 행은 주문 분모에 더하지 않는다. eviction/truncation시
  보존된 주문 수일 뿐이며 공식 deals 또는 deals+100 분모가 아니다.
- 기존 item_kind_orders와 item_kind_settle_events 분모/의미는 바꾸지 않았다.

## 실행한 검증

새 `check.py`는 기존 kind-fix runner를 새 경로에 복사하고 새 테스트 파일만
include했다. tracked Python 소스/필수 정책 소스만 고유 evidence tree에 복사하며
실제 runtime, journal, keys, 기존 dirty 로그와 사용자 untracked 테스트는 제외한다.
child는 환경 whitelist, 격리 home/temp, 원본 repository read 및 격리 밖 write
차단, 외부 socket connect/sendto/getaddrinfo 차단을 유지한다. 기존 runner,
기존 실패/성공 로그와 결과 파일은 변경하지 않았다.

공통 명령 prefix:
`.\.venv\Scripts\python.exe -B submissions/25512085/week-08/history/20261109-buyer-saved-strategy-step1/check.py`

| 인자 | 실제 결과 | 원문 로그 |
|---|---|---|
| first-red test_buyer_outcomes_saved_offline.py | 수정 전 14 tests, errors=14 (새 필드 부재), exit 1 | first-red-26f93af4da30.log |
| focused test_buyer_outcomes*offline.py | 85 tests OK, 0.902s, exit 0 | focused-2614d6b63d33.log |
| all | 659 tests OK, 32.412s, exit 0 | all-10ecee77ea3e.log |
| final-focused test_buyer_outcomes*offline.py | 85 tests OK, 0.953s, exit 0 | final-focused-b1bb4cd3a7a1.log |
| final-all | 659 tests OK, 25.472s, exit 0 | final-all-9dd8f17620be.log |

기존 645개 + 신규 14개 = 659개다. 무환불/전액/부분/과환불/환불 누락,
0원 무환불, 6h coverage, food/kind missing·clash/legacy unknown, exact order/unit,
원장 충돌, truncation, 분리된 통계 분모, 입력 불변을 새로 검증했다.
기존 kind/ledger 충돌 회귀 및 append/lock/fsync 검증도 전체 suite에서 통과했다.
추적 소스의 `git diff --check -- .../buyer_outcomes.py`는 통과했다
(LF→CRLF 안내만 있음). 새 파일은 전체 suite에서 import/실행되었다.

## 미실행·인계

운영 원본 replay, API/실거래, VM 배포, 장기 운영 검증은 하지 않았다. 실제
Saved 전체 분모/coverage와 food served value는 여전히 알 수 없다. 부분 환불의
공식 Saved 해석도 미해결이다. 기존 dirty 로그/test-report 및 사용자 파일을
보존했다. 본 단계는 로컬 구현·검증까지만이며 메인 리뷰·커밋 후 다음 단계를
별도 진행해야 한다. 이 작업에서 커밋·푸시는 하지 않았다.
