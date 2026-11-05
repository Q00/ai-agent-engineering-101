# Week 07 — Agent Market Report

## 프로젝트 개요

Week 06에서 구현한 판매자와 구매자를 Round 2 시장에 연결했다. 판매자는 A2A JSON-RPC를 통해 `lamp`, `chair`, `bookshelf` 등을 협상하고 서명된 영수증을 발급한다. 시장 구매자는 상점의 리뷰와 자신의 거래 기억을 참고해 상점을 선택하고, 예산을 넘지 않는 범위에서 협상한다. `agent.json`에는 seller, Week 06 buyer, market buyer의 실행 명령과 Vercel 배포 주소를 기록했다. 확인한 설정의 harness는 `Codex CLI (gpt-6.1-terra)`이다.

## 1. 구매자의 상점 선택과 기억

구매자는 시장이 제공한 상점 목록에서 각 상점의 유효한 별점 평균을 계산한다. 리뷰가 없는 경우 기본 점수 3을 사용한다. 이전 거래에서 **합의 가격과 영수증 가격이 불일치했다고 확인된 횟수**마다 점수에서 2점을 차감한다. 점수가 같으면 리뷰 수가 많은 상점, 그다음으로 내 거래가 적은 상점, 마지막으로 상점명 순으로 선택한다. 선택 이유는 평균 별점, 리뷰 수, 자기 거래 수, 불일치 횟수와 점수를 로그에 출력한다.

`buyer_memory.json`은 상점 이름을 기준으로 거래를 기록하는 JSON 배열이다. 항목은 `shopName`, `item`, `price`, `result`, `receiptMismatch`와 선택적인 `occurredAt`으로 구성된다. `price` 또는 `receiptMismatch`가 `null`이면 해당 값이 없거나 판정되지 않았다는 뜻이며, 가격이 일치했다는 뜻으로 간주하지 않는다. 상점 URL이 바뀌어도 이름으로 기억을 조회한다.

구매자는 상점의 최근 두 거래가 `remote_error`이고 마지막 오류가 5분 이내라면 일시적으로 그 상점을 제외한다. 다른 상점이 모두 제외된 경우에는 점수순 fallback을 사용한다. 일반적인 협상 거절과 상대 응답 오류는 구분한다. `remote_error`의 분류 범위는 구현에 따르며 모든 HTTP·네트워크 오류를 포괄한다고 할 수는 없다.

**기록의 한계:** market test를 실행한 뒤 구매자 기억 파일에서 `Kit Shop`과 `Kit Shop South`의 테스트 기록이 실제 시장 기록과 함께 확인되었다. 테스트 기록을 실제 시장 거래 건수로 집계하지 않는다. 원본을 보존하고 테스트와 시장 기록을 구분한다.

## 2. 리뷰를 어떻게 사용했는가

market kit가 제공한 상점 목록의 리뷰 별점은 TypeScript `marketSelection.ts`에서 직접 읽어 상점 선택 점수를 계산하는 데 사용했다. 확인한 `market_buyer.ts`에서 협상 harness에 전달하는 내용은 상품, 예산, 현재 호가, 판매자의 메시지와 협상 대화 등이다. **리뷰 별점과 리뷰 본문을 harness에 별도로 전달하지는 않았다.** 따라서 리뷰가 모델 프롬프트에 제시되었다고 주장하지 않는다. 리뷰를 harness에 직접 보여 주는 구현은 추가 검토 사항으로 남는다.

market kit가 제공한 상점 목록의 리뷰 별점은 TypeScript `marketSelection.ts`에서 읽어 상점 선택 점수를 계산하는 데 사용한다. 이후 선택된 상점의 리뷰 최대 5개를 협상 harness 입력에 포함하도록 수정했다. 각 리뷰 본문은 최대 500자로 제한하며, 외부 리뷰는 신뢰할 수 없는 데이터로 다루도록 프롬프트에 명시했다. `market_kit3.txt` 실행 기록에서 Elm Street와 YAHO1212에 대해 `[review-debug] ... reviews_to_harness=5`가 확인됐다. 이 로그는 전달 대상으로 준비된 리뷰 수를 보여 준다.

## 3. Round별 관찰

### Round 0과 Round 1

현재 확인한 제출 자료에는 Round 0과 Round 1의 상점·구매자 실적을 신뢰성 있게 비교할 수 있는 수치가 없다. **미확인:** 해당 시기의 대시보드 또는 원장 자료를 확인한 뒤 추가해야 한다. 다른 예시 로그의 거래 수치를 실적으로 사용하지 않았다.

### Round 2: 구매자 거래

운영 로그에서 판매자와 구매자가 `helenjeon101`로 등록된 것을 확인했다. 예를 들어 chair 주문의 예산이 185일 때, 구매자는 Afford Shop과 185에 합의했고 영수증 가격도 185였다. 시장 원장 `#1958435`에는 `verified True`와 `review accepted`가 기록됐다. 이는 **내 구매자가 다른 상점에서 산 거래**이며 내 상점의 판매 실적으로 합산하지 않는다.

이후 원장에는 `bookshelf` 거래 357 (`#1958470`), 324 (`#1958525`), 295 (`#1958563`)도 각각 `verified True`로 기록됐다. 이 금액 역시 구매자 측 거래다. 근거: `logs/market-kit.log`의 해당 원장 항목 및 `logs/challenge-2/`의 오류 회피 발췌본.

### Round 2: 판매자 대시보드

101대시보드 스크린샷의 한 시점에서 `Catalog Shop`은 거래 1014건, 매출 25,666, 표시 profit 208,106, 평균 별점 4.89, 리뷰 1014개로 표시됐다. 화면에는 share도 `0%`로 표시됐다. 이것은 **해당 시점의 대시보드 표시값**이며 가격 변경 전후 비교 결과가 아니다. 근거: `Screenshot2026-11-03103500.png`.

## 4. 영수증 서명과 검증

Week 06 영수증 형식에서 Week 07에 필요한 `{shop, item, price, task}` 데이터와 `sig_seller` 형식으로 변경했다. 서명 대상은 네 필드로 구성한 canonical JSON이며, 키 정렬, 공백 없는 구분자와 UTF-8 인코딩을 사용한다. 가격은 서명 전에 정수로 확인하고 Ed25519 개인키로 서명한 뒤 base64 문자열로 담는다. market kit가 판매자 서명과 주문 조건을 확인한 후 구매자 서명을 추가한다.

확장 상품의 영수증 상품군 선택에서 `categoty` 오타를 `category`로 수정했다. 그 오타 때문에 실제 거래가 실패했는지는 확인하지 못했다. 로컬 market test에서는 `seller_signed_receipt` 세 회가 모두 통과했다. 실제 Round 2 구매 로그에서도 `sig_seller ok, sig_buyer added`에 이어 원장 `verified True`가 기록된 사례가 있다. 개인키 파일과 키 값은 저장소에 포함하지 않는다.

**미확인:** 서명 추가 과정에서 발생한 구체적인 서명 검증 실패의 원본 로그.

## 5. Challenges

### Challenge 1 — 판매 가격 조정

판매자의 초기 호가와 재제안에는 기존에 harness 제안 가격을 바탕으로 `+15`를 적용하고 `reserve`를 하한으로 유지하는 정책이 있었다. 추가로 첫 호가에서 실제 판매 기록과 리뷰를 읽어 조정하는 코드를 준비했다. `pricing_history.json`에는 `Catalog Shop`과 판매·리뷰 배열이 있고, Vercel pricing 로그에서는 `reason=insufficient_data`, `sales_count=0`, `review_count=0`, `ask=base_ask`가 확인됐다. **확인한 구간에서는 실제 거래·리뷰에 따른 추가 조정은 발생하지 않았다.**

추가 제공된 11월 3일 Vercel 판매자 로그(`Copied log3.txt`)에서 `[pricing]` 41줄은 모두 `sales_count=0`, `review_count=0`, `reason=insufficient_data`, `ask=base_ask`였다. 예를 들어 chair 기본 호가 235는 235로 유지됐다. 원본과 가격 산정 발췌본은 `logs/challenge-1/`에 보존한다. seller의 `completed`는 협상 완료이며 각각이 시장 원장에서 검증된 판매인지는 별도 대조가 필요하다.

**미완료:** 우리 상점의 실제 거래·리뷰를 확보해 가격 변경에 사용한 근거, 적용 시점, 변경 전후의 동일 기준 시장 점유율과 평균 판매 가격. 현재 자료만으로 전후 효과를 계산하거나 성능 향상을 주장하지 않는다.


### Challenge 2 — 신뢰할 수 없는 상대와 회피

Round 2 로그에서 `q00` 관련 요청이 19:07:00과 19:07:28에 `harness exit 1`로 실패했다. 구매자는 19:07:46에 `[cooldown] temporarily avoiding shop=q00`을 출력하고 Afford Shop을 선택해 예산 185 내에서 chair 거래를 진행했으며, 원장 `#1958435`에서 검증됐다. 19:12:59에 `q00`이 다시 선택된 기록과 이후 오류·재회피 기록도 있다. 원문 발췌는 `logs/challenge-2/`에 정리한다.

이것은 **반복 응답 오류에 대한 안정성 사례**다. traceback 일부만으로 근본 원인이나 상대의 고의를 판단할 수 없으며, 부정직한 거래의 증거로 제시하지 않는다. 다른 두 Vercel 파일은 판매자 로그이므로 구매자의 부정직 상점 회피를 증명하지 않는다.


11월 3일 구매 로그 `market_kit3.txt`에서는 별도의 영수증 불일치가 확인됐다. Elm Street에서 구매자는 `bookshelf`를 270에 합의했지만 판매자 영수증 가격은 310이었다. 구매자는 `mismatch=true`를 기록했고, 시장 측 서명 단계는 예산 270을 초과한 영수증에 서명하지 않았다. 원장 `#2065076`은 `verified False`와 `price_within_budget`, `sig_buyer_ok` 검증 실패를 기록했으며 1점 리뷰는 게시되지 않았다. 판매자의 의도는 이 기록만으로 알 수 없다.

제공된 `buyer_memory.txt` 발췌에도 `shopName=elm-street`, `item=bookshelf`, `price=310`, `result=completed`, `receiptMismatch=true`가 남아 있다. 여기서 `completed`는 협상 task 상태이며 시장의 최종 검증 성공을 뜻하지 않는다. 이 기억은 구현상 상점 선택 점수를 2점 낮추는 근거가 된다. 발췌본은 배열 앞부분이 없어 전체 거래 건수 집계에는 사용하지 않는다.

이후 11:10:30 구매자가 다음 주문에서 YAHO1212를 선택했고 11:12:46에는 이 상점에서 bookshelf 357 거래의 합의 가격과 영수증 가격이 일치했으며 원장 `#2065371`은 `verified True`였다. 다만 다음 주문은 다른 상품이었고 Elm Street가 당시 선택 후보였는지와 점수 비교가 보이지 않으므로, **기억된 불일치 때문에 Elm Street를 회피했다고까지는 입증할 수 없다.** 선택 당시 각 후보의 점수 또는 해당 상점 제외 이유가 남은 추가 로그가 필요하다.

## 6. 배포 및 테스트

판매자 공개 주소는 `https://ai-agent-engineering-a2a.vercel.app`이고, market kit의 `--deployed` 방식으로 연결했다. seller의 Ed25519 개인키는 배포 환경의 비밀 설정으로 제공하고, kit가 등록하는 공개키와 대응해야 한다. 구매자는 로컬 machine에서 실행되는 market kit가 주문마다 실행한다. seller와 buyer의 마감 시점 동작 여부는 제출 시 다시 확인한다.

제공된 market test 실행 기록은 **25/25 PASS**다. 공개 `week-07` 브랜치에서 확인한 `test-report.json`도 25개 항목이 통과했으며, 시작 시각은 `2026-11-02T11:15:29+00:00`이고 테스트 스크립트 해시는 확인 시점의 공식 스크립트와 일치한다. 서명, 판매자 최저가, 구매자 예산, A2A 1.0 헤더와 task 이어가기, 리뷰 기반 선택 테스트가 포함된다. 테스트 중 한 차례는 `PRICING_HISTORY_PATH`를 `/tmp/week07-empty-pricing-history.json`으로 지정해 실행했다.