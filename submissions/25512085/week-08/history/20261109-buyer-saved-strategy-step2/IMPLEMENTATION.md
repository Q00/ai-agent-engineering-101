# Step2 — 로컬 buyer Saved 위험 가드 (메인 리뷰 대기)

> 아래 초기 기록의 size/visibility 미해결 상태는 마지막 **후속 수정** 절에서 해결했다.
> kit acceptable SKU 교체 한계 및 실거래 미검증은 그대로 남는다. 과거 실패 로그는 보존한다.

기준 HEAD `3773978`. VM·네트워크·거래·설치·commit·push 없음.
`buyer_outcomes.py`의 step1 및 food step3, seller/restock, 가격 20% 상한은 수정하지 않았다.
week-08 과제 README는 이 checkout에 없다. 루트 AGENTS 및 POLICY,
GRADE_WARRANTY_POLICY, 읽기 전용 scripts/market_kit.py 계약을 확인했다.

## 구현

- goods에만 A=.03/B=.15/C=.35 **정책 prior**를 사용한다. 실측 고장률/신뢰도라고 주장하지 않는다.
- `discount=(catalog_lp-actual_amount)/catalog_lp`. full window(>=6h)면
  `ExpectedSaved=(1-q)*discount`, 그 외 `(1-q)*discount-q`. 0보다 클 때만 수락한다.
  0~6h의 고장시각 분포를 모르므로 부분 보증의 환불 혜택은 0으로 둔다.
  보증 누락도 혜택 0. boolean/문자열/NaN/무한/음수 보증은 거절한다.
  등급 누락·알 수 없는 문자열은 명시적으로 C prior, 비문자열은 거절한다.
  C0 할인40%는 -.09로 거절, 할인60%는 .04로 수락 가능하다. C 블랙리스트가 아니다.
- 모든 COMPLETED 영수증 경로는 공통 후처리에서 **실제 영수증 SKU의 catalog 정가**로
  재검사한다. 정수·예산·허용 SKU·서명존재·합의가·20% cap 보호는 유지한다.
- checkout `accept`는 기존 지원 필드 `cert`, `grades`, `warranty_hours`만 쓴다.
  receipt claim보다 나쁘지 않고 실제 amount/lp에서 위험 점수가 양수인 등급만 whitelist.
  full-window 계산이면 최소6h, short이면 기존 약속 duration을 유지한다.
  seller receipt는 시장검증이 아니므로 실제 인증서는 kit에서 검증해야 한다.
- goods ranking은 공개 offers의 grade/warranty를 추정하지 않는다. 기존 bounded
  sku_comparables 중 같은 shop×SKU×(grade, full-window 여부), 미래시각 제외,
  3h 이내(경계 포함), 조건당 최소3표본만 사용한다. 명시 보증 필드 누락은 unknown.
  충분한 조건 중 가장 최근 관측된 조건을 사용하고, 조건 중앙가를
  `n/(n+3)`으로 SKU 가격 prior 쪽으로 축소한다. sample 수는 가격 축소 가중치일 뿐
  실증 신뢰도/성숙 고장 위험 추정이 아니다.
- 부족하면 기존 SKU scalar 중앙가(3건 이상, 최근15개 유효값) 또는 기존 80% 가격 prior에
  **C/no-refund** 위험을 적용한다. scalar 자료의 신선도·등급·수량은 발명하지 않는다.
  음수 ranking utility는0으로 floor하며 기존 trust/diversity/feasibility/bestseller는 유지한다.
  순위0 후보도 협상 가능하나 영수증에서는 반드시 양수 gate를 재검사한다.
- store compaction은 기존 필드 whitelist에 public shop/order/seq만 추가한다.
  원래50행/3h 보존·자가 buyer 제외·수요 카운터·수집 pipeline은 그대로다.
  journal full scan, 새 실거래 evidence 수집 및 signature/nonce/budget 저장은 추가하지 않는다.

## 중요 미해결 — 배포/완전 settle 보장 금지

1. **SKU 교체**: kit `acp_pay`는 order의 acceptable 전체를 checkout에 전달하며 다른
   acceptable SKU도 허용한다. accept에는 SKU restriction/max_price 지원이 없다.
   따라서 영수증과 다른 낮은 정가 SKU로 checkout되면 risk/20% cap 우회 가능성이 남는다.
   먹히지 않는 새 accept key는 추가하지 않았다. 최소 acceptable 정가로 선제 제한하는
   대안은 거래량 감소가 크고, 모든 정가 누락 처리도 필요하므로 메인 설계 리뷰 전 미구현이다.
2. **기존 3.2MB 대형 fixture 한도와 provenance 보존 충돌**: 모두 보존하면 합성 허브가
   3,101,682 → 4,860,776 bytes가 된다. 기존3.2MB assertion을 늘리거나 삭제하지 않았다.
   50표본/3h를 줄이면 기존 consumer 중앙가를 바꾸므로 하지 않았다. 메인이 byte 한도의
   의미와 bounded metadata 별도 표현 여부를 결정해야 한다. 이 실패를 숨기지 않는다.
3. 과거 seller의 다음 unit 등급은 검증하지 못한다. ranking 조건은 predicted condition일 뿐이며
   receipt 실제 조건 + kit 인증서 gate가 필수다. 시장 executable quote나 순위품질 실증이 아니다.
4. kit warranty predicate 자체의 악성 nonfinite checkout 값을 이 buyer가 완전히 보장하지 못한다.
   scripts는 read-only이며 수정하지 않았다. 테스트는 정상 숫자의 보증 약화와 인증 등급을 검사한다.

## 검증과 평가 범위

- `check.py`는 step1의 source-only 격리 runner를 재사용하고 새 fixture를 명시 추가한다.
  원본 state/journal/key/log 읽기와 네트워크는 차단한다. 과거 output을 덮어쓰지 않고
  고유 evidence 및 로그를 만든다. 기존 dirty 파일은 보존한다.
- 첫 red: `python -B .../step2/check.py first-red test_buyer_saved_risk_offline.py`, exit1.
- focused-v2: 같은 runner `focused-v2 test_buyer_saved_risk_offline.py`, **15 tests 통과**.
  실제 kit `check_accept`/`canon` AST를 읽어 임시 생성 인증서로 등급/보증 약화를 검증한다.
- all-v2: 같은 runner `all-v2`, **674 tests**, 실패2: 예상 문구 변경1건과 위 byte-size 한도1건.
  문구 회귀를 새 ExpectedSaved 진단으로 갱신했다. 최종 실행 결과는 별도 final 로그 참조.
- final-focused: **16 tests 통과**, `final-focused-ffae400a3d2f.log`.
- final-all: **675 tests 중674 통과, 실패1** (`final-all-cbe4c63d958f.log`).
  유일한 실패는 기존 `test_large_synthetic_state_size`의3.2MB 한도다.
  `git diff --check`는 whitespace 오류 없이 통과(CRLF 변환 경고만 존재).
- food의 기존 float 변환 경로까지 명시 보존한 최신 source 재실행:
  `python -B submissions/25512085/week-08/history/20261109-buyer-saved-strategy-step2/check.py final-food-preserved`,
  **675개 중672 통과/실패3**, `final-food-preserved-d89b7fc24c2c.log`.
  위 size 실패 외에 수정하지 않은 visibility/supply 회귀2개가 추가 실패했다:
  `test_pause_restores_one_only_once_per_sku_window_even_401`(409 !=401),
  `test_timeout_reservation_survives_wrapper_restart`(ReadTimeout 미발생).
  원인 미확정이며 범위 밖 코드를 수정하지 않았다.
- 같은 최신 source에서 `check.py visibility-repro test_visibility_guard_offline.py`는
  **43 tests 통과** (`visibility-repro-1c08b6515a05.log`). 단독 재현은 안 됐으며
  전체 suite 상호작용/비결정성 여부는 메인 확인 필요하다. 전체 green이라고 주장하지 않는다.
- step1 신규 outcome_saved 테스트도 전체 suite에 포함한다. 기존 ranking 회귀의 기대값은
  새 unknown 위험 prior에 맞게 갱신했고, 양수60% 할인 fixture로 기존 배수를 계속 검증한다.

실거래 평가 N=0: paid=0, 정책 효과/순위품질/실현 Saved 개선은 **미검증**.
합성 테스트 개수는 거래 표본 N이 아니다. 향후 승인된 실험에서 주문수, unknown 비중,
eligible/selected/paid, 결제 실패사유, 보증만료 후 per-order Saved 및 aggregate
`sum(scored_savings)/(deals+100)`를 구분해야 한다. gate 강화는 음의 기대 거래를 줄이지만
paid 수량도 줄일 수 있고, per-order proxy 양수만으로 aggregate 최적화는 보장하지 않는다.
unknown fallback의 보수성과 실제 paid volume 사이 tradeoff는 실험 전 결론낼 수 없다.

## 후속 수정 — 기존 한도 유지 및 실패 원인 해결

사용자 후속 승인 범위에서만 수정했다. 기존 state/journal/key/log, seller/restock,
buyer_outcomes step1 및 food를 수정하지 않았다. 기존 실패 로그도 삭제/수정하지 않았다.

### 크기 원인과 수정

- 기존 scalar 가격 이력은30개, structured comparable은50개/SKU·3시간이다.
  core 증거를 줄이거나 기존3.2MB assertion을 바꾸지 않았다.
- 전체 행에 shop/order/seq를 붙였던 것이 크기 증가 원인이었다. prediction에 필요한
  shop 태그만 허브 전체 최대120행, shop UTF-8 최대128bytes로 제한한다.
  앞뒤 공백·제어문자·잘못된 타입·surrogate·초과 길이는 tag만 unknown 처리하며 자르지 않는다.
- (SKU, shop, grade, full-window 여부)별 최신3개를 선택하고, 완전3표본 조건을 먼저
  최근 관측순으로 보존한다. 최대40개의 완전 조건을 보존할 수 있고, 남는 슬롯에만
  sparse 조건을 축적한다. 예산이 차면 sparse 진입이 늦어질 수 있다. 기존 행의 unknown
  shop은 복원하지 않는다. 이는 조건 coverage 제한이며 신뢰도/순위 개선 실증이 아니다.
- 기존 ledger cursor/seen_seqs가 ingestion을 dedup하므로 per-row order/seq 복제는 제거한다.
  기존 수요 카운터와 자가 buyer 제외는 유지한다. helper는 core 증거를 건드리지 않고,
  ingestion 반환과 minute compaction에 동일하게 적용한다. 신규 수집 경로는 없다.
- 다른 consumer의 가격 중앙가는 원래 core50행을 계속 사용한다. buyer 조건 중앙가만
  retained 최신3표본 기준이다. 이전 n/(n+3) shrink 공식은 유지하되 compact 후 보통n=3이며,
  실제 전체 표본수·성숙 위험·실행 가능한 현재 가격으로 해석하지 않는다.
- 최종 대형 fixture: `15,950,758 -> 3,106,002 bytes`, comparables1,620,541,
  sales66,686, breaks618,208. 3.2MB 및 기존45% 이하 assertion 모두 유지·통과.

### visibility 원인: suite 누수가 아니라 경로 길이

- `diagnose-supply-exception-04fabe0a1b7f.log`에서 supply_allowed line99의
  atomic_json 예약 파일 생성이 **FileNotFoundError/ENOENT**로 실패함을 관측했다.
  clock=1790000000, inventory mtime=1790000000으로 정상, active started patchers=0이었다.
  공용 time module patch/복원과 fixture thread-start patch를 읽고 확인했으며,
  해당 실패를 설명하는 module/config leak 수정은 필요하지 않았다.
- 이전 runner의 scratch 이름에 label이 들어갔다. short `final-all` 예약 .tmp 경로는251자,
  `final-food-preserved`는262자였다. 단독 visibility44개를 동일 긴 라벨로 실행해
  **동일 2개 실패+새 길이 assertion 실패**를 결정적으로 재현했다:
  `final-food-preserved-0d3532aa250a.log`.
- 더 긴 진단 라벨은 초기 fixture 파일까지 Windows 경로 한도를 넘겨33 errors를 냈다
  (`diagnose-path-length-red-long-label-d4b619f54d93.log`). 첫 scratch-only 수정 후에는
  source-copy 경로도 너무 길어 콘솔에서 FileNotFoundError가 났다
  (`diagnose-path-length-after-long-label` 명령의 첫 실행, 로그 생성 전 실패).
  이 실패를 성공으로 기록하지 않았다.
- 최종 runner는 source tree(`r-UUID`, 진단은`diagnose-UUID`), scratch(`s-UUID`),
  home(`h-UUID`)을 짧고 고유하게 만든다. 라벨은 exclusive log에만 남기며 로그 안에
  command와 source evidence 경로를 기록한다. 현재 reservation 경로는235자다.
  실제 임시 원본·기존 운영 파일을 건드리지 않고, production adapter 및 timeout은 불변이다.
- 새 `test_isolated_atomic_reservation_path_stays_below_windows_limit`을 추가했다.
  skip/임의 timeout 증가/실패 무시는 없다.

### 후속 실제 명령·결과

모든 명령은 아래 공통 prefix를 사용한다:
`python -B submissions/25512085/week-08/history/20261109-buyer-saved-strategy-step2/check.py`

| 인자 | 로그 | 결과 |
| --- | --- | --- |
| `metadata-red test_buyer_saved_risk_offline.py` | metadata-red-5800dba17a17.log | red, exit1 |
| `final-food-preserved test_visibility_guard_offline.py` (수정 전) | final-food-preserved-0d3532aa250a.log | 44개, 실패3 |
| `compact-after test_hub_compaction_offline.py` | compact-after-2593bcf3adb7.log | 통과, 기존 size assertions 유지 |
| `metadata-after test_buyer_saved_risk_offline.py` | metadata-after-885696945797.log | 22개 통과 |
| `diagnose-path-length-after-long-label test_visibility_guard_offline.py` (최종 수정 후) | diagnose-path-length-after-long-label-e73fc910bbaf.log | 44개 통과 |
| `corrected-all-v1` | corrected-all-v1-906918681209.log | **682개 전부 통과**, exit0 |
| `corrected-full-suite-long-label-repeat` | corrected-full-suite-long-label-repeat-2d6d895f73eb.log | **682개 전부 재통과**, exit0 |

`git diff --check -- submissions/25512085/week-08`도 whitespace 오류 없이 통과했다
(기존 LF→CRLF 안내 경고만 발생). HEAD는3773978 그대로이며 commit/push하지 않았다.

이제 로컬 구현·회귀 검증은 메인 리뷰에 전달 가능하다. 실거래 N=0/순위품질 미검증,
kit acceptable SKU 교체에 따른 완전 settle/risk/cap 보장 불가는 해소됐다고 주장하지 않는다.
