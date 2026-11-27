# Step3 — 식품 lifetime 사전검사, 로컬 전용

기준 HEAD `4fa63d5`(step2 리뷰 통과), step1 `3773978`.
AGENTS, roster/25512085.md, POLICY 및 기존 step2 구현/runner를 확인했다.
`weeks/week-08/README.md`는 이 checkout에 없어 읽지 못했다.
VM·네트워크·설치·실거래·다른 에이전트·commit·push 없음. 배포 승인이 아니다.
기존 dirty 로그/test-report, userdata/runtime/key 및 이전 실험 결과는 보존했다.

## 실제 포맷과 읽기 전용 계약

- 기존 buyer `food_price_policy`의 힌트는 숫자 절대 Unix timestamp `expires_at`이다.
  상대 `hours_left`/자유 텍스트/ISO를 해석하는 기존 buyer parser는 없다. 새 parser도 없다.
- seller의 `compute_live_unit_grade`는 numeric expiry로 `hours_left`를 계산하지만,
  일반 signed receipt에는 unit/claim/cert가 있고 expiry가 생략된다. 그러므로 모든 receipt에서
  사전검사가 가능한 것은 아니다. 누락을 전면 차단하지 않는다.
- 읽기 전용 `scripts/market_kit.py`의 `RECEIPT_KEYS`는 shop/item/price/task뿐이다.
  seller signature 존재는 freshness/cert 인증이 아니다. kit는 seller 자신의 terms에서
  실제 checkout을 구성하고, market 서명 인증서의 unit/SKU를 검증한다.
- 실제 `check_accept`는 인증서의 `(expires_at-time.time())/3600`을 구해
  **hours < accept.min_hours_left**이면 거절한다. buyer 사전검사도 같은 `<`를 쓴다.
  별도 epsilon/추가 버퍼/collection-to-checkout 시간 정책은 도입하지 않았다.

## 최소 변경

- 식품 opening text는 기존 `build_accept_policy`가 계산한
  `round(use_within_hours(default 1)+0.25, 2)`h를 요구한다. cert=true 요청은 유지한다.
  개인 예산/90% cap/comparable 정책은 문의에 노출하지 않는다. goods 문구는 불변이다.
  기존 use_within_hours 변환/기본값/검증 동작도 수정하지 않았다.
- receipt SKU로 기존 `food_price_policy`를 재계산한 뒤 base accept와 merge한다.
  **그 동일한 최종 policy 객체**로 힌트를 검사하고 kit에 전달한다. 따라서 comparable
  가격/신선도 조건이 기본1.25h보다 큰 min_hours_left를 요구해도 같은 기준을 쓴다.
  가격 cap 함수의 `(cap, accept)`/`None` 반환 호환, 90% 정가·budget min·비교표본 정책은 불변이다.
- 새 helper `food_lifetime_precheck`는 `cert is False` 또는 양수·유한·비boolean 숫자
  expiry의 실제 잔여시간 부족만 이유 문자열로 반환한다. expired timestamp도 부족이다.
  누락/NaN/무한/bool/음수/0/문자열/상대 hours_left는 unknown으로 `None`을 반환한다.
  `None`은 **인증 성공이 아니다**. cert 누락도 kit의 기존 최종 cert gate를 유지한다.
- 신규 failure enum 없이 기존 outcome `declined-price-policy`, failure `price`, telemetry
  `receipt_policy`를 재사용한다. 상세 review에는 `food-lifetime-precheck`와 실제 이유가 남는다.
  거절한 영수증/서명은 선택·pending 저장하지 않고 기존 다른 상점 retry를 탄다.
  invalid/unsigned/over-budget/가격정책 우선순위와 dishonesty 판정은 기존대로다.
- 실제 receipt SKU의 catalog 정가/이름 기반 처리 및 substitute SKU 가격정책을 유지했다.
  SKU 접두사를 식품/상품 판정으로 새로 사용하지 않는다. 신규 네트워크 호출은 없다.
- step2 goods risk/20% cap/ranking/store, seller/restock, shared kit는 수정하지 않았다.
  기존 integer-wire 테스트의 opening text 기대값만 12h→12.25h로 갱신했다.

## 검증 — 실패 포함 실제 기록

공통 명령 prefix:
`python -B submissions/25512085/week-08/history/20261109-buyer-saved-strategy-step3/check.py`

runner는 step2의 짧은 UUID 경로·source-only 복제·네트워크 및 원본 runtime 읽기/쓰기
차단을 재사용한다. untracked 신규 `test_buyer_food_precheck_offline.py`를 **명시 include**한다.
각 실행은 새 evidence tree와 exclusive log를 사용한다. 과거 출력은 덮어쓰지 않았다.

| 인자 | 로그 | 실제 결과 |
| --- | --- | --- |
| `first-red test_buyer_food_precheck_offline.py` | first-red-e42c9a2c73e5.log | 9개, 실패7, exit1 (구현 전) |
| `focused-v1 test_buyer_food_precheck_offline.py` | focused-v1-9e9aa3f9cb10.log | 11개 통과, exit0 |
| `all-v1` | all-v1-e1a1aac92d2b.log | 693개, 실패1, exit1 (기존 opening text 기대값12h) |
| `focused-v2 test_buyer_food_precheck_offline.py` | focused-v2-776c1a4c0616.log | 12개 통과, exit0 |
| `all-v2` | all-v2-1f4c56b39a43.log | **694개 전부 통과**, exit0 (기존682 + 신규12) |

신규 검증은 use1h/hint1.1h 거절·1.25h 정확 경계, 강화 accept3h 경계,
unknown/expired/cert=false, receipt SKU 교체 정가/이름·정수 가격, 초기~4번째 finalaccept
완료 경로, 기존 schema 실패기록 및 다음 상점 retry, 문의의 cert/시간·비공개 예산,
goods 무변경과 4번째 finalaccept의 음수 ExpectedSaved 거절을 포함한다.
읽기 전용 실제 kit `check_accept`/`canon` AST와 임시 합성 market 인증서로 기본1.25h,
강화3h/38.4h의 경계 및 누락 인증서 거절을 검증했다. 실키를 읽지 않는다.

`git diff --check -- submissions/25512085/week-08`는 whitespace 오류 없이 통과했다
(LF→CRLF 안내 경고만 존재). 보호 대상 store/seller/restock/buyer_outcomes/shared kit의
`git diff --quiet HEAD -- ...`는 exit0이며 HEAD는4fa63d5 그대로다.
기존 dirty `logs/market-test.log`, `test-report.json`은 이번 변경 파일이 아니다.

## 남는 한계 / 실행하지 않은 검증

- 실거래 N=0, paid=0. retry fixture의 두 번째 receipt는 **결제 대기 선택**일 뿐이다.
  실제 checkout/정산/Saved 개선·paid 수량 효과는 검증하지 않았다.
- seller hint는 거짓일 수 있다. 힌트 통과/누락은 cert 검증을 대체하지 않는다.
  선택 후 시간 경과로 kit가 거절할 수 있으며 buyer exit 이후 retry는 여전히 불가능하다.
- 기존 kit가 다른 acceptable SKU를 checkout에서 선택할 수 있다는 한계는 그대로다.
  미지원 accept SKU 키나 새로운 가격/신선도 버퍼를 발명하지 않았다.
- 주차 checker/과제 README가 없어 주차 검사 실행은 하지 않았다. 로컬 구현과 회귀검증만
  완료했으며 배포/실시장 평가를 승인받았다고 주장하지 않는다.
# 메인 최종 검증

- 독립 읽기 전용 리뷰에서 신규 차단 사항 없음. 메인이 diff 확인 및 격리 runner 재실행으로 전체 **694개 통과**를 확인했다 (`main-verification-b7d60d8337af.log`).
- 단계별 변경은 별도 로컬 커밋으로 보존한다. 기존 사용자 로그·test-report 및 운영 자료는 포함하지 않는다.
- 세 단계 모두 VM 미배포·실거래 효과 미검증이다. checkout의 다른 acceptable SKU 교체 및 사전검사 이후 시간 경과 한계는 남는다.
