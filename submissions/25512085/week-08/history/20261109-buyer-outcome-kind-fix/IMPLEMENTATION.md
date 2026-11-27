# Buyer outcome kind v2 — 로컬 검증 완료, VM 미배포

기준 HEAD: `d8e540e3efc08727ce634521e269c61c5a169c35` (2026-11-09).
AGENTS 및 기존 outcome 구현 문서를 확인했다. `weeks/week-08/README.md`는
현재 checkout에 없어 읽지 못했다. 정책·가격·구매·판매·store는 수정하지 않았다.
추가 위임, 설치, 외부 네트워크, VM, 실거래, commit/push는 수행하지 않았다.

## 원인과 정정 범위

v1의 normalize/validator는 G-prefix SKU를 goods로 간주했고, summary는 goods가
아니면 food로 취급했다. SKU 접두어는 상품 종류의 증거가 아니다. 이제 종류는
관측 당시 catalog의 정확한 `kind:goods|food`만 근거로 한다. category 및 SKU 모양은
분류에 사용하지 않는다. catalog 누락/null/invalid는 unknown이며 정가가 없어도
종류의 unknown 근거를 저장한다. 상품명, 원문 객체, 비밀정보는 저장하지 않는다.

사용자가 전달한 과거 집계 수치 및 원본 journal은 이번 검증에서 읽거나 재계산하지
않았다. 과거 v1 오분류를 이 변경으로 자동 복원했다고 주장하지 않는다.
goods 20% / food 10% buyer 정책은 그대로다. 15% food synthetic fixture가 goods로
오인되지 않는지만 확인하며, 수집기에 정책 경고나 새로운 가격 기준을 추가하지 않았다.
food의 warranty_hours:null은 정상적인 무보증 관측이며 누락 오류/정책 위반으로
간주하지 않는다. food served/value가 없으므로 goods 고장 기여 수식을 적용하지 않는다.

## schema v2

기존 store 경로 `runtime/buyer-outcomes-v1.jsonl`을 변경하지 않는다. 파일 이름의
v1은 기존 경로일 뿐이며 각 행의 version으로 schema를 판정한다. 향후 append는
settle/broken/coverage 모두 version:2이며 v1/v2 혼합을 읽는다. 원본 v1 파일의
rewrite/migration/delete는 없다. 관측 캡처와 hub lock 이후 append 흐름도 그대로다.

기존 allowlist와 숫자/ID 제약을 유지하고 v2 settle에 다음 필드를 요구한다.

| 필드 | 의미 / 허용값 |
|---|---|
| item_kind | goods / food / unknown |
| item_kind_source | catalog_at_observation / catalog_kind_unknown / catalog_ledger_conflict |
| catalog_kind | catalog에서 허용된 goods/food, 나머지는 unknown |
| catalog_kind_state | valid / missing / null / invalid |
| catalog_kind_observed_at | 이 snapshot의 observed_at과 같은 유한 비음수 숫자, bool 금지 |
| ledger_item_kind | 명시적 data.item_kind 또는 data.kind의 goods/food, 아니면 unknown |
| ledger_kind_state | valid / missing / event_kind / invalid / conflict |

row.kind는 settle/broken이라는 **event kind**다. data.kind가 해당 event kind이면
event_kind로 구별하며 상품 종류로 사용하지 않는다. 명시적 ledger 종류가 catalog와
다르거나 두 ledger 종류 필드가 상충하거나 잘못된 종류 값이 있으면 unknown 및
catalog_ledger_conflict로 보수적으로 남긴다. ledger 종류만으로 catalog unknown을
goods/food로 승격하지 않는다. 미허용 원문은 저장하지 않고 상태 enum만 저장한다.

catalog_kind 및 list_price는 **관측 당시 메타데이터이지 원주문의 kind/정가 증거가
아니다**. 따라서 snapshot만으로 당시 주문에 적용된 가격 정책 위반을 단정할 수 없다.

validator는 v2 메타데이터 누락, 잘못된 enum/타입/시각 및 필드 간 모순을 거절한다.
v2의 legacy goods 필드는 거절한다. read_events와 직접 iterable replay는 같은
allowlist 검증을 거치고 JSONL roundtrip 후에도 결과가 같다. broken/coverage에는
해당하지 않는 상품 메타데이터를 요구하지 않는다.

## legacy / dedup / summary

- v1의 bool goods는 schema 하위 호환 목적으로만 읽고 종류 추론에 쓰지 않는다.
  v1-only settle은 item_kind:unknown, source/reason:legacy_kind_unknown이다.
  현재 catalog를 읽어 과거 v1을 자동 정정하지 않는다. goods/food 분모 또는
  특정 종류 공식 Saved를 v1 bool이나 SKU로 복원하지 않는다.
- 동일 event kind+seq는 dedup한다. 순서와 무관하게 v1+v2가 있으면 저장된 v2
  종류 관측 메타데이터만 사용하고 kind_versions_seen:[1,2]를 표시한다. 이는
  명시적인 v2 재관측이지 원주문 종류의 증명이나 v1 원본의 수정이 아니다.
- 최초 정가 snapshot은 유지한다. v2끼리는 최초 종류 snapshot도 유지하며 후속
  관측 종류 차이는 observation_kind_changed로 남긴다. 서로 다른 관측 종류/정가는
  원장 사실 충돌로 보지 않는다. 관측 순서가 다르면 최초 snapshot은 다를 수 있다.
- 같은 seq의 buyer/order/ts/unit/SKU/결제액/grade/보증 또는 환불액 차이는
  duplicate_integrity_conflict 및 conflicting_events로 검출한다. 서로 다른 seq의
  동일 주문 settle/refund도 기존대로 conflicting_events이며 환불을 합산하지 않는다.
- item_kind_orders는 남아 있는 고유 settle 주문별 최초 snapshot 수,
  item_kind_settle_events는 dedup된 고유 settle event별 종류 수다. 각각
  goods+food+unknown이 자신의 분모이며 broken/coverage/반복 관측은 포함하지 않는다.
  충돌 cohort도 관측 종류 수에는 포함된다. 이 수는 정책 준수/성과 분모가 아니다.
  eviction시 주문 수는 보존된 cohort만, event 수는 읽은 고유 event만 나타내고
  truncated:true이므로 전체 이력의 분모라고 주장할 수 없다.
- exact order/refund/maturity/unknown coverage 의미는 유지한다. 모든 cohort의
  observation_complete:false, complete:false, official_saved:null은 그대로다.
  알려진 goods broken 기여만 제한적으로 계산하며 v1-only/food/unknown에는 적용하지 않는다.

## 격리 검증과 원본 보존

새 check.py는 tracked 소스와 신규 kind 테스트를 명시적으로 고유 evidence tree에
복사한다. 기존 history/20261109-buyer-outcome-link/check.py는 수정하지 않았다.
운영 journal/key/state/log와 사용자 untracked 테스트는 복사하거나 테스트 입력으로
읽지 않는다. 정책 테스트의 기존 seller_cash_policy.json은 tracked 정책 소스 사본이며
운영 raw state가 아니다. 네트워크 차단 및 원본 repository read/write 방지 audit,
별도 home/temp, 환경 whitelist를 기존 runner와 동일하게 사용했다.

실행 명령 (각 명령은 `.venv/Scripts/python.exe -B`로 새 check.py 실행):

| 인자 | 결과 / 원문 로그 |
|---|---|
| baseline-repro test_buyer_outcomes_kind_offline.py | HEAD의 수집기 **코드만** 고유 트리에 사용; 14 tests, failures=7/errors=18; baseline-repro-f25c27cd10b8.log |
| focused test_buyer_outcomes*offline.py | 중간 검증 62 tests OK; focused-6a031103eb4a.log |
| final-focused test_buyer_outcomes*offline.py | **65 tests OK**, 0.443s; final-focused-60367cbe2a86.log |
| final-all | **639 tests OK**, 24.743s; final-all-923f864225d0.log |

기존 622개에 kind 회귀 17개를 추가했다. 기존 테스트는 catalog fixture의 실제 kind를
명시하고, 허용된 schema version 및 새 metadata 검증에 맞춰 invalid fixture만
조정했다. v1 하위 호환 테스트는 실제 v1 shape를 명시적으로 구성한다. 기존
exact-order/refund/maturity/잠금/fsync/동시성/용량/손상/NaN/bool 테스트를 유지했다.
최종 git diff --check는 추적 변경 파일에서 통과했다 (LF→CRLF 안내만 있음).

실패 로그와 당시 source tree도 원문 보존한다. 기존 dirty 로그/test-report 및
user untracked 파일, 원본 v1 journal/key/state는 수정하지 않았다. VM 배포/실제
운영 파일 replay/장기 수집/실거래/외부 API/전원 장애 검증은 **실행하지 않았다**.
이번 단계는 로컬 수정·검증뿐이며 메인 리뷰 후 별도 배포 승인이 필요하다.

## 리뷰 P2 보완 — 명시적 원장 종류 모순 / 지연 도착 주문 충돌

위 최종 검증 이후 리뷰에서 발견된 두 문제를 로컬에서 보완했다. 변경은
buyer_outcomes.py, kind 회귀 테스트 및 이 문서에 한정한다. 기존 실패/성공 로그와
당시 evidence tree는 수정하지 않았으며 기존 runner도 그대로 사용했다.

1. 같은 seq의 catalog snapshot 변화와 **명시적 ledger_item_kind 모순**을 구별한다.
   고유 settle event마다 읽은 유효 ledger 종류 집합을 최대 goods/food 두 값으로
   유지한다. goods와 food가 모두 관측되면 원장 무결성 충돌로 표식하고 종류는
   unknown, reason은 conflicting_events, 고장 기여는 null이다. 따라서 첫 goods
   snapshot 뒤 food 원장 근거가 도착해도 무환불 broken에 goods 손실식을 적용하지
   않는다. catalog만 변경된 경우는 기존처럼 최초 snapshot을 유지한다.
2. ledger 종류의 부재(v1 포함)는 반대 종류를 뜻하지 않는다. absent→known 또는
   known→absent 자체는 무결성 충돌이 아니다. 다만 뒤늦게 확인된 명시적 ledger
   종류가 보존된 catalog snapshot과 상충하면 item_kind_unknown으로 보수적으로
   처리한다. invalid/conflict ledger 근거도 계속 unknown이다. 원본 snapshot 행은
   바꾸지 않고 replay에서만 저장된 모든 명시 근거를 반영한다. 위 문서의 최초
   종류 snapshot 유지 규칙은 이제 이러한 원장 근거의 제약을 함께 받는다.
3. 같은 seq가 서로 다른 order를 가리키면 두 order를 영속적인 **replay 내**
   conflict_orders 집합에 남긴다. 아직 cohort가 없거나 eviction된 order도 이후
   삽입 시 충돌을 적용하고 최종 집계에서도 확인한다. journal/hub에 새 상태를
   쓰는 것은 아니다. 집합은 event cap으로 최대 2×MAX_EVENTS ID까지 제한된다.
   기존 결제액/SKU/ts/unit/grade/보증/환불 등 원장 사실 비교는 그대로다.

선행 red 검증: 새 회귀 6개 추가 후 코드 수정 **전에** 실행했다.

```powershell
.\.venv\Scripts\python.exe -B submissions/25512085/week-08/history/20261109-buyer-outcome-kind-fix/check.py review-p2-red test_buyer_outcomes_kind_offline.py
.\.venv\Scripts\python.exe -B submissions/25512085/week-08/history/20261109-buyer-outcome-kind-fix/check.py review-p2-focused test_buyer_outcomes*offline.py
.\.venv\Scripts\python.exe -B submissions/25512085/week-08/history/20261109-buyer-outcome-kind-fix/check.py review-p2-all
```

- `review-p2-red-9ac74e942c49.log`: **23 tests, failures=5**, 수정 전 두 문제 재현.
- `review-p2-focused-e19bbf8e4289.log`: **71 tests OK**, 0.681s.
- `review-p2-all-a06b3228dc08.log`: **645 tests OK**, 27.050s.
- 원장 종류 goods→food 및 역순/부재 경유 순열, absent↔matching known 비충돌,
  같은 seq의 A→B 후 B 별도 seq와 broken 도착, A/B 모두 나중에 삽입되는 순열을
  검증했다. 모든 도착 순열에서 충돌 이유 및 기여 null 의미가 유지된다.
  기존대로 최초 관측 snapshot/대표 ledger 번호/출력 순서는 도착 순서에 의존할 수
  있으며 전체 JSON이 순열마다 동일하다는 의미는 아니다.
- 추적 변경 소스·기존 테스트의 git diff --check 통과 (LF→CRLF 안내만 있음).

가격/구매/판매 정책, store, 운영 journal/key/state 및 기존 사용자 변경은
수정하지 않았다. 설치/외부 네트워크/VM/실거래/commit/push는 수행하지 않았다.
이번에도 **VM 미배포**이며 메인의 리뷰·커밋 및 후속 배포 단계와 분리한다.
