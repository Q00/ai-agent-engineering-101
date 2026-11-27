# Buyer savings guard — 로컬 수정, 미배포

## 범위와 근거

- 기준 HEAD `9b632c1`. 루트 AGENTS.md, POLICY.md, 최근 buyer ranking/gap/integer-wire 이력을 먼저 확인했다. 추가 AGENTS.md는 tracked 목록에 없었다. `weeks/week-08/README.md`는 현재 경로에 없다.
- 전달받은 읽기 전용 분석 수치: 11/8 07~12 paid 618건, 평균 할인 25.476%; 20% 미만 236건(38.2%)은 모두 3차 ask 수락, 3차 paid 508건 평균 22.252%. **이번 작업에서 운영 로그를 다시 읽거나 수치를 재검증하지 않았다.** 과거 결과를 수정하거나 그 결과에 맞춰 가격을 학습하지 않았다.
- 실행 코드 수정은 `market_buyer.py`의 goods 상한과 시장가격 추정뿐이다. `store.py`, `adaptive_strategy.py`, seller revision06, 실패 분류/피드백, stockout, 재시도 횟수/기한/취소, bestseller 1.35·신뢰·다양성·등급/보증 정책은 변경하지 않았다. 전체 과제를 새로 구현하지 않았다.

## 가격 정책과 우회 방지

- 기존 모듈 상수 설정 형태를 따라 `GOODS_MIN_DISCOUNT_PERCENT = 20`을 추가했다. 별도 설정 파일은 없다. **20%는 새 전략 가정이며 시장 규칙이나 실측 최적값이 아니다.**
- `goods_price_cap(catalog, hub, sku, budget)` 하나를 선택 SKU의 협상 한도와 실제 receipt SKU의 검증에 공통 사용한다. 예산, 정가-1, 기존 92% offer cap, 기존 comparable cap, 새 정가 80% 중 최솟값이다. 정수 나눗셈으로 내림하며 유효한 양의 정수 정가가 없으면 원 SKU 정가/예산으로 대체하지 않고 보류한다.
- 첫 제안/둘째 35% counter/셋째 ask 수락·counter/최종 수락은 기존 공통 clamp를 따른다. 3차 ask가 cap을 넘으면 기존 cap 내 counter까지만 보내며, 이후 기존 최대 3개 상점 retry를 이용한다. 판매자가 이 가격으로 체결한다는 보장은 없다. 무한 재시도나 협상 횟수 증가는 없다.
- 최초 응답부터 COMPLETED인 경우(제안 0회), 1~3차 응답에서 바로 완료된 경우, 마지막 수락 응답을 모두 기존 단일 receipt 검사로 통과시킨다. receipt 가격은 실제 SKU cap과 마지막 합의·예산·기존 구조 검증을 함께 만족해야 한다. 대체 SKU도 실제 catalog 정가로 재계산하며, accept의 품질 계산 정가도 실제 SKU를 사용한다. 허용 SKU 검증은 유지한다.
- 무보증 C 기본 가정과 양의 기대잉여 검사, cert/등급/보증 accept, 서명 존재 검사, task/shop/가격 검증은 유지했다. 서명 검증과 실제 checkout은 종전대로 kit의 별도 단계이며 receipt 선택을 결제 성공으로 기록하지 않는다. kit에서 다른 acceptable SKU로 바뀌는 경우를 buyer가 확정 제어하지 못하는 기존 통합 한계는 남는다.
- 식품의 90% 가격 한도, 비교가·expiry·use_within·checkout margin과 legacy 협상은 그대로다. 식품에 20% 최소 할인을 적용하지 않는다. integer-only 협상 wire와 예산 비노출도 유지한다.

## 시장가격 근거의 정의와 한계

- 현재 `store.apply_ledger_rows`는 다른 buyer의 공개 `settle.data.amount`를 SKU별 `sku_settles`에 정수로 저장한다(최대 30개). 자신의 buyer 구매는 제외한다. 가격 원소에는 timestamp/shop/grade/warranty/qty가 없으며 **신선도를 검증할 수 없다**. 3시간 창을 보장한다고 주장하지 않는다.
- 새 공통 reader는 scalar 목록에서 양수·finite·non-bool·최대 10^12인 수치만 뽑아 마지막 15개를 사용한다. 유효 관측 3개 이상이면 median을 **그대로** 평가한다. 정가80%, 예산, 추정 원가하한으로 evidence를 덮어쓰지 않는다. 정가 이상 중앙값의 음수 원시 savings rate는 유지하되, 독립 리뷰 보완 후 utility만 0으로 제한해 페널티 역전을 방지하고 후보를 유지한다. 85% 실제 관측을 80%로 낮춰 구매 가능 가격처럼 만들지 않는다.
- 3개는 단위 수량이 아니라 유효 scalar 관측 수다. qty 누락/0/NaN/bool 등의 structured 행을 임의 단위가격/가중 표본으로 해석하지 않는다. 기존 schema에 없는 수량을 만들거나 amount/qty 정규화·shop×SKU 집계를 추가하지 않았다. malformed/부족한 체결 근거는 기존 정가80% prior(기존 예산·원가 fallback 포함)로 돌아가며, 이를 실제 견적이라고 설명하지 않는다. goods 자체 정가가 없어 cap을 계산할 수 없는 경우는 독립 리뷰 보완 후 후보 단계에서 제외한다.
- 별도 `sku_comparables`에는 시각 등 일부 구조가 있지만 압축 과정에서 shop도 사라질 수 있다. 이번 변경은 이를 새로운 상점별 집계로 확장하지 않는다. 현재 추정은 **SKU 시장 전체의 조건 혼합 estimate**이며 상점별 예측/동일 품질·수량 비교가 아니다. 오래된 표본, 품질/보증/거래 구성 편향이 남는다. 새 aggregation 파이프라인은 범위 밖이다.

## 거래량·점수 위험

20% 하한을 과거 거래에 그대로 적용하고 다른 상점 대체 체결이 전혀 없다면 전달받은 **236건의 성공 거래 전부를 거절할 위험**이 있다. 할인율이 높아져도 체결량 감소로 Saved(거래수+100 분모 포함)나 총점이 떨어질 수 있다. 다른 상점 retry의 성공, 평균 할인·Saved·총점 상승을 보장하지 않는다. 실측 성과/VM 반영 여부는 별도 검토 대상이다.

## 실행 기록 (원문 로그 모두 신규 보존)

저장소 루트에서 `.venv/Scripts/python.exe -B submissions/25512085/week-08/history/20261108-buyer-savings-guard/check.py <label> [pattern]` 실행.

| label / pattern | 결과 | logs/ 파일 |
| --- | --- | --- |
| red / test_buyer_savings_guard_offline.py | 수정 전 11 tests, 12 subtest failures; cap 우회·낙관 clip 재현 | buyer-savings-guard-red-149d5c6adc3b.log |
| focused / 같은 pattern | 수정 후 신규 15 tests 통과 | buyer-savings-guard-focused-f55619a1be16.log |
| all / 기본 test_*_offline.py | 566 tests, 5 failures/15 errors 보존 | buyer-savings-guard-all-0b82c66f176f.log |
| all-v2 / 기본 | 566 tests 통과 | buyer-savings-guard-all-v2-2a26775d7898.log |
| final / 기본 | **568 tests 통과 (신규 17개)** | buyer-savings-guard-final-4e9f0c55fde7.log |

- 전체 첫 실패의 6개는 새 가격 정책과 기존 fixtures의 의도적 충돌이었다: goods 92%→80% 한도, 그 영향으로 첫 anchor/35% counter 값 변화, 보증부 16000이 새 cap 13360을 초과하는 성공 fixture, 최종 ask 15000이 새 cap을 넘는 품질 fixture. 가격을 13000으로 바꿔 여전히 C 무보증은 실패하고 보증/A는 성공하도록 분리했다. 품질 조건을 느슨하게 하거나 실패 assertions를 삭제하지 않았다. comparable .88보다 새 .80 cap이 더 엄격하다는 기대를 명시했다.
- 나머지 14 errors는 runner의 scratch/HOME이 복제 repo 안이라 기존 select_temp_parent의 외부 경로 검사를 위반한 harness 문제였다. scratch/HOME을 **같은 새 evidence 아래, 복제 repo의 형제 경로**로 옮겼다. 기존 테스트·제품 코드를 바꿔 우회하지 않았다.
- runner는 git tracked 직속 Python 소스, 명시적 신규 테스트, 정책 JSON/launcher/kit만 새 고유 tree로 복사한다. 기존 untracked test_market_monitor_offline.py는 읽거나 포함하지 않았다. 환경은 최소 allowlist로 정리하고 HOME/TEMP를 새 경로로 지정했다. `runpy`로 표준 `unittest discover -p test_*_offline.py -v`를 실행한 것이며 원본 cwd에서 무가드 `-m unittest`를 실행한 것은 아니다.
- audit hook은 외부 socket/DNS, 원본 저장소·운영 파일 읽기 및 새 sandbox 밖 쓰기를 차단한다. 기존 운영 키는 읽거나 복사하지 않았다. 테스트의 presence-only receipt fixture와 전체 suite가 만드는 임시 합성 키만 사용했다. evidence tree는 .gitignore로 제외한다. 로그는 exclusive create로 저장했으며 실패도 삭제·편집하지 않았다.
- 수정 6개 Python 파일을 `compile(..., 'exec')`로 메모리 구문 검사: 전부 COMPILE_OK. `git diff --check` 통과(LF→CRLF 안내만 있음).

## 미실행 / 보존

- VM/배포·네트워크·실거래·모델 호출·설치·추가 위임·commit/push 없음. 메인 리뷰 후 커밋할 로컬 후보다.
- 기존 dirty 로그/test-report, 다른 untracked, 운영 keys/state는 읽거나 수정하지 않았다. 기존 실험 결과/이력도 덮어쓰지 않았다. 모든 새 파일은 `submissions/25512085/week-08/` 안이다.
- 공식 `scripts/check_week08.py` 및 주차 README 부재로 주차 checker 미실행. 실상점 체결률/결제/성과는 검증하지 않았다.

## 독립 리뷰 P2 보완 (같은 HEAD, 로컬만)

### 재현과 최소 수정

1. **음수 utility의 페널티 역전:** 정가 10000·median 11000의 원시 savings rate는 -10%다. 여기에 작은 신뢰/다양성/실패/feasibility 배수를 곱하면 나쁜 후보의 점수가 덜 음수가 되어 우선되는 문제가 재현됐다. median과 원시 rate는 변경하지 않고, **가중치 적용 전 rate를 utility 계산에서만 `max(0, rate)`로 제한**했다. 0 이하 후보는 삭제하지 않고 기존 입력 순서의 동률로 남긴다. 이 동률은 높은 신뢰를 엄격히 우선한다는 뜻은 아니지만, 낮은 신뢰/실현 가능성 때문에 가점을 받지는 않는다. 양수 utility의 기존 배수/순위는 그대로다. 앞 절의 초기 구현에서 음수 utility를 그대로 보존했던 설명은 이 리뷰 수정으로 대체된다.
2. **동일 상점의 유효 SKU 가림:** goods 정가가 없는 A의 예산 fallback prior(20%)가 정가 10000·median 8500인 B(15%)보다 높아 A를 선택한 뒤, attempt가 cap 계산 실패로 끝나면서 URL 전체가 retry 후보에서 빠졌다. 선택 루프에서 기존 `goods_price_cap(...) is None`인 goods SKU를 제외하도록 했다. cap이 0 이하인 경우도 helper가 None으로 처리한다. 제외는 URL probe/주문 협상 전에 이루어지며 같은 URL의 유효 B는 계속 평가한다. 식품 정가 미상 처리, 유효 SKU의 다른 URL retry는 유지한다. 기존 ranking 테스트의 goods 정가 누락/0에 대한 budget fallback 성공 기대만 후보 보류 기대로 바꿨다.
3. **retry 테스트의 원인 분리:** 신규 savings guard와 기존 retry 모듈 양쪽에 first price 24500/list 30000의 **A·6h 보증** fixture를 사용한다. 새 최소 할인만 0으로 끈 대조군은 같은 영수증을 24500에 선택해야 하고, 20% 설정에서는 `declined-price-policy`, `cap 24000`, 비사기 기록을 확인한 뒤 다른 상점 18000 영수증을 선택해야 한다. `break rate` 거절이 아님도 assert한다. C 무보증 거절로도 통과할 수 있던 테스트 공백을 없앴다. 제품의 품질 정책은 바꾸지 않았다.

### 새 테스트와 실행 기록

같은 격리 runner/명령으로 실행했으며 기존 실패·성공 로그는 편집하거나 덮어쓰지 않았다.

| label / pattern | 결과 | logs/ 파일 |
| --- | --- | --- |
| review-red / test_buyer_savings_guard_offline.py | 수정 전 21 tests, **13 subtest failures**: 음수 순위 역전 5개, 불가능 goods 후보 선택 8개 | buyer-savings-guard-review-red-948b084fca89.log |
| review-focused / 같은 pattern | 수정 후 **22 tests 통과** | buyer-savings-guard-review-focused-280003db93ef.log |
| review-all / 기본 test_*_offline.py | **574 tests 통과** (이전 568 + 6) | buyer-savings-guard-review-all-834197c16a8b.log |

추가 6개: 동일 SKU 두 상점의 신뢰/방문 다양성/최근 실패/결합 페널티(양수·음수 비교), 음수 feasibility, 동일 상점 A 제외 후 B 선택, 불가능 후보의 probe 0회, 실제 ranking→다른 URL retry 연결, 기존 retry 모듈의 보증부 cap-only 대조군 테스트. 기존 guard retry 1개도 동일하게 강화했다. 이 중 retry 연결 테스트는 ranking과 run_round3를 실제 호출하되 run_attempt는 합성 결과로 대체하며, 실상점 체결 검증이 아니다.

### guard 경계 — receipt까지이며 checkout 전체 보장이 아님

`scripts/market_kit.py`는 **읽기 전용**으로 확인했다. `ACP.pick_unit` 380~399행은 `acceptable`이 있으면 receipt SKU가 아닌 다른 acceptable SKU도 고를 수 있다. create 421~422행은 실제 선택 unit SKU를 checkout item에 넣지만 가격은 receipt 가격을 유지한다. `acp_pay` 501~518행은 acceptable 목록을 넘기고 checkout item이 다른 acceptable SKU이면 허용하며, receipt와 가격/task 일치·주문 예산을 검사한다. `check_accept` 443~480행의 cert/grade/use_within/warranty 검증에는 실제 checkout SKU 정가 대비 최소 할인 재검사가 없다.

따라서 이번 buyer guard의 보장 범위는 **협상 제안 및 buyer가 출력 대상으로 고른 receipt의 실제 SKU/가격 검증까지**다. 예를 들어 정가 100000 receipt의 80000이 buyer cap을 통과한 뒤, checkout이 정가 50000인 다른 acceptable SKU를 같은 80000에 선택한다면 buyer의 20% cap이 checkout 실제 SKU에 다시 적용된다고 보장할 수 없다. 이 수치는 소스 경로를 설명하는 합성 예시이며 운영 사고를 확인한 결과가 아니다. checkout 최종 SKU 기준 cap 강화 또는 SKU 고정/별도 검증은 **후속 승인·통합 검토 대상**이다. scripts/kit/서명/accept 조건은 수정하거나 약화하지 않았다. 모든 checkout/결제에서 cap 우회가 완전히 차단됐다고 주장하지 않는다.

VM/네트워크/배포/실거래/설치/추가 위임/commit/push는 이번 보완에서도 수행하지 않았다.

리뷰 수정 Python 4개(`market_buyer`, savings guard/ranking/retry tests) 메모리 compile 전부 COMPILE_OK. 수정 tracked 경로를 지정한 `git diff --check`도 통과했다(LF→CRLF 안내만 있음). `git status --short -- scripts/market_kit.py` 출력은 없어 kit tracked 수정 없음도 확인했다.
# 메인 검증 기록

- 메인이 격리 runner를 별도로 실행해 전체 574개 통과를 확인했다. 로그: `logs/buyer-savings-guard-main-verification-811d163f98ca.log`.
- 독립 리뷰에서 발견한 음수 효용 페널티 역전, 정가 불명 후보가 유효 후보를 가리는 문제를 보완하고 재현 실패 기록을 보존했다.
- 이번 변경과 신규 검증 기록만 로컬 커밋한다. VM 배포·푸시·실거래 개선 검증은 미실행이다.
