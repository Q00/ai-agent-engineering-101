# Week 07 — 상시 시장 참여 보고서

- 학번: 26512076
- 시장 등록 이름: `26512076`
- 하네스: Groq API를 호출하는 Python 에이전트 루프
- 모델: `openai/gpt-oss-20b` (`GROQ_MODEL` 환경변수로 변경할 수 있습니다.)
- 실행 환경: Windows, Anaconda Prompt, 로컬 PC와 ngrok 터널
- 기록 기준: 2026년 10월 24일에 수집한 제출 폴더의 코드와 로그입니다. JSONL의 시간은 UTC이며, 시장 키트 콘솔의 당일 16시대 기록은 한국 시각에 해당합니다.

## 1. Buyer가 가게를 고르는 방법과 기억의 형식

6주차의 A2A 판매자·구매자 구현을 확장하여 서명된 영수증과 시장용 구매자 프로그램을 추가하였습니다. 협상에서 얼마를 제안할지, 수락할지, 철수할지는 모델이 결정합니다. 코드는 A2A 통신, 상태 보관, 하한가·예산 검사, 영수증 처리와 가게 선택을 담당합니다. 따라서 가게 선택까지 모두 모델이 결정하는 구조는 아닙니다.

`market_buyer.py`는 키트가 전달한 가게 목록을 읽고, `market_support.py`의 `selection()`으로 가게를 선택합니다. 먼저 과거에 영수증 불일치가 있었던 가게 이름을 후보에서 제외합니다. 나머지 가게에는 다음 점수를 계산하고 가장 높은 가게를 선택합니다.

```text
점수 = (유효한 별점 합계 + 6) / (유효한 별점 개수 + 2)
       + 0.2 × 해당 가게의 최근 기억 10건 중 구매 성공 건수
```

별점은 유한한 1~5의 수치만 사용합니다. 후기 없는 가게는 사전 평균 3점으로 시작하므로, 후기 한 개만으로 평가가 크게 흔들리는 것을 줄였습니다. 모든 가게가 영수증 불일치로 제외되면 요청을 보내지 않습니다. 후보별 점수, 차단 여부, 선택한 이름은 `logs/challenge-2/avoidance.jsonl`의 `selection` 이벤트로 남깁니다. 콘솔의 `choose` 이벤트에도 선택 이유를 출력합니다.

기억은 `buyer_memory.jsonl`에 거래당 한 줄의 JSON으로 저장합니다. 주요 필드는 `time`, `shop_name`, `item`, `budget`, `price`, `result`, `stars`, `review`입니다. 결과는 `purchased`, `no_deal`, `receipt_mismatch`, `technical_failure`로 구분합니다. 다시 읽을 때 일반 기록은 최근 200건을 사용하고, 영수증 불일치는 오래되어도 가게 이름별로 유지합니다. URL이 변경되어도 같은 이름의 가게를 피할 수 있지만, 상대가 이름까지 변경하는 경우를 식별하는 기능은 구현하지 않았습니다.

구매자는 카드에서 A2A v1.0 JSON-RPC 주소를 찾고, 모든 HTTP 요청에 `A2A-Version: 1.0`을 붙입니다. 후속 협상에는 받은 `taskId`와 `contextId`를 사용합니다. 모델의 제안 가격이 양의 정수이면서 예산 이내인지 전송 전에 검사하며, 협상은 최대 12번의 가격 제안과 270초의 제한을 둡니다. 마지막 표준 출력 줄에는 항상 `shop`, `receipt`, `stars`, `review`를 담습니다. 거래가 없거나 검증에 실패하면 `receipt`는 `null`입니다.

## 2. 후기를 하네스에 보여 준 방법

구매자에게는 선택한 가게의 최근 후기 2개를 각 160자까지 잘라 보여 주고, 해당 가게와의 최근 경험 2건을 `item`, `price`, `result`로 요약하여 전달하였습니다. 이 내용은 `market_context_untrusted`에 넣었습니다. 예산과 요청 품목은 별도의 `trusted_state`로 전달하며, 상대 메시지·후기·카드가 예산이나 시스템 지시를 바꿀 수 없도록 프롬프트에 명시하였습니다.

판매자에게는 자신의 최근 거래 결과와 실제 시장 후기를 보여 주었습니다. 키트가 구매자에게 주는 목록에서는 자신의 가게가 제외되므로, 운영자가 시장의 가게 목록에서 자기 가게의 후기만 추출한 `own_reviews.json`을 사용하였습니다. 다음 명령으로 이를 입력 자료와 증거 로그로 보관하였습니다.

```bat
python market_challenges.py feedback --input own_reviews.json --name 26512076
```

입력은 `shop_feedback.json`과 `logs/challenge-1/reviews-1792825952340759400.json`에 남아 있습니다. 해당 자료에는 Round 2 후기 10개가 있으며, 판매자 모델에는 그중 앞의 8개를 각 300자까지 전달합니다. 예를 들어 다음 후기가 포함되어 있습니다.

```text
"Receipt verified locally; fair price 295.0 within budget."
"Paid 100.0 of 110."
```

판매자는 최근 로그 240개를 읽고 현재 `MARKET_RUN_ID`에 해당하는 기록에서 최근 판매 12건과 종료 결과별 건수를 구성합니다. 이 정보와 후기를 `market_feedback`에 넣고, 한 협상이 시작될 때의 내용을 그 협상 동안 유지합니다. 후기 입력은 운영자가 가져온 시점의 스냅샷이며 자동으로 갱신되지는 않습니다. 또한 최근 거래는 여러 품목을 포함하므로, 특정 가격이 판매 실패의 원인이었다고 단정하지 않도록 프롬프트에 명시하였습니다.

## 3. Round 0, 1, 2에서 관찰한 결과

`logs/market-kit.log`에는 이전 실행과 10월 24일 실행이 함께 들어 있습니다. 각 `order` 줄의 round를 뒤따르는 ledger 결과와 연결하여 집계하면 다음과 같습니다. 이 표는 이 키트 로그에 남은 구매자 결과만 집계한 것이며, 시장 전체 또는 내 판매자의 매출 통계는 아닙니다.

| 구간 | ledger 결과 줄 | 가격이 있는 결과 | `verified True` 결과 |
|---|---:|---:|---:|
| Round 0 | 15 | 6 | 3 |
| Round 1 | 76 | 27 | 21 |
| Round 2 | 74 | 21 | 21 |

Round 0에서는 상대가 서명을 제공하지 않은 영수증도 관찰하였습니다. 다음 기록은 가격이 기록되어도 시장에서 인정되는 거래와는 다를 수 있음을 보여 줍니다.

```text
20:20:41 [sign] receipt has no sig_seller
20:20:42 [market] ledger #3529: lamp at 140, stars None, verified False (failed: sig_seller_ok, sig_buyer_ok), review not shown
```

Round 1에서도 성공과 실패가 함께 있었습니다. 보존된 과거 로그만으로 당시 판매자의 단계별 시장 점유율을 복원할 수는 없어, 구매자 ledger 결과와 판매자 지표를 분리하였습니다.

Round 2에서는 이전 실행의 API 한도 초과로 거래하지 못한 기록 이후, 10월 24일에 가게와 구매자를 다시 등록하여 실제로 인정된 거래를 확인하였습니다.

```text
16:01:00 [market] shop registered as 26512076, round 2
16:01:00 [market] buyer registered as 26512076, round 2
16:01:09 [market] ledger #527140: chair at 212, stars 4, verified True, review accepted
16:02:13 [market] ledger #527242: lamp at 105, stars 4, verified True, review accepted
16:02:59 [market] ledger #527314: lamp at 102, stars 4, verified True, review accepted
```

판매자 쪽은 `seller-events.jsonl`의 판매 기록과 실제로 수집한 자기 가게 후기, 대시보드 행으로 관찰하였습니다. 대시보드의 앞선 관찰에서는 `DEALS`, `RECEIPTS`, `WITH ORDER`, `REVIEWS`가 각각 12이고 `STARS`가 4.83이었습니다. 이후 관찰에서는 네 건수가 각각 50이고 `STARS`가 4.76이었습니다. 두 화면 모두 `SHARE`는 0%로 표시되었습니다. 이는 화면에 표시된 값이며, 실제 점유율의 정밀한 수치나 동일한 기간의 점유율 변화를 계산할 자료는 확보하지 못하였습니다. 이 화면 수치는 운영 중 수동으로 확인한 값이며 ZIP의 JSON 집계와는 출처가 다릅니다.

## 4. 서명·실행 과정에서 실패한 것과 수정한 것

### 영수증 서명과 검증

판매자는 수락한 구매자 제안과 가격이 일치하는지 확인한 후 `{shop, item, price, task}`를 구성합니다. `price`는 정수로 만들고, `task`에는 실제 A2A task id를 넣습니다. 해당 네 필드를 키 정렬·공백 없는 UTF-8 JSON으로 직렬화하고, 키트가 생성한 `shop_key.pem`의 Ed25519 개인키로 서명하여 Base64 형식의 `sig_seller`를 추가합니다. 완성된 영수증을 data Part의 artifact로 보냅니다.

구매자는 상대 영수증의 품목·예산·최종 제안 가격·task를 검사하되, 받은 영수증을 임의로 고치지 않고 그대로 키트에 반환합니다. 시장 공개키를 이용한 서명 검증과 구매자 서명 추가는 키트가 담당합니다. 따라서 구매자의 내용 검사와 키트의 암호학적 검증을 구분하였습니다.

보존된 로그에는 상대의 서명 없는 영수증 때문에 검증에 실패한 기록이 있습니다. 다만 이것을 제 판매자의 서명 버그였다고 볼 근거는 없습니다. 제 판매자 구현은 최종 market test에서 다음 검증을 세 번 모두 통과하였습니다.

```text
seller_signed_receipt: sig_seller verifies with shop_key.pem
```

서명 추가 전 제 코드가 어떤 오류로 실패했는지를 입증하는 별도 로그는 확인하지 못하였으므로, 관찰하지 않은 실패를 작성하지 않았습니다.

### API 실패와 남아 있는 제한

과거 시장 실행에서는 Groq의 일일 토큰 한도 때문에 협상이 실패하였습니다. 이를 줄이기 위해 모델 출력 토큰 상한을 2,000으로 설정하고, gpt-oss 모델에 `reasoning_effort=low`를 사용하였습니다. 판매자 모델 호출은 직렬화하고 호출 사이에 간격을 두었습니다. 재시도는 제한된 횟수만 수행하며, 429 응답의 대기 시간이 길면 공통 cooldown 파일에 기록하고 실패를 반환합니다. 모델 호출이 실패했다고 코드가 임의의 협상 가격을 만들어 내지는 않습니다.

이 조치가 API 한도를 없애는 것은 아닙니다. 실제로 최신 실행에도 다음 제한이 다시 발생하였습니다.

```text
16:23:31 [buyer] ... "status": 429, ... "wait_seconds": 285.688, "error_type": "RateLimitError"
```

그 다음 오류에는 `tokens per day (TPD): Limit 200000, Used 198659, Requested 2000`이 기록되어 있습니다. 기술적 실패는 상대의 부정행위로 분류하지 않았습니다. 종료 시점의 판매자 테스트 로그에는 SDK의 context 정리 관련 오류도 남아 있으므로, 25개 검사 통과를 실행 중 오류가 전혀 없었다는 의미로 해석하지 않았습니다.

## 5. 필수 도전의 구현과 결과

### 도전 1 — 거래 기록과 후기를 반영한 호가 변경

`PRICE_PHASE=before`에서는 피드백을 끄고 기본 협상을 실행하였습니다. 이후 자기 가게 후기를 가져오고 `PRICE_PHASE=after`로 재시작하여, 모델이 최근 거래·종료 결과·후기를 바탕으로 호가를 조정하도록 하였습니다. 하한가는 두 조건 모두 lamp 100, chair 180, bookshelf 260으로 동일합니다. 모델이 하한가 미만의 역제안을 만들면 전송 전에 하한가로 올리고, 하한가 미만 수락은 거부합니다.

`logs/challenge-1/seller-events.jsonl`의 2026-10-24 07:13:18 UTC `task_started`에는 다음 입력이 기록되어 있습니다.

```json
{"phase":"after","feedback_enabled":true,"recent_terminal_counts":{"sale":23,"reject":5,"failed":3}}
```

같은 이벤트에 최근 판매 12건과 실제 후기 8개가 포함되어 있어, 피드백을 모델 입력에 전달하였음을 확인할 수 있습니다. 이후 lamp 첫 호가가 120인 기록(07:13:41 UTC), bookshelf 첫 호가가 270인 기록(07:15:05 UTC) 등이 관찰되었습니다. before 조건의 첫 호가는 lamp 130, chair 200, bookshelf 300으로 기록되어 있습니다. 모델의 선택적 `reason` 필드는 빈 경우가 있어, 가격별 상세 판단 이유까지 로그에서 복원할 수는 없습니다.

다음 표는 `before-summary.json`, `after-summary.json`, `comparison.json`에 고정하여 저장한 결과입니다. 테스트용 `validation` 실행은 제외하고 `run_id=live-market`만 사용하였습니다.

| 지표 | 변경 전 | 변경 후 |
|---|---:|---:|
| 집계 저장 시각(UTC) | 07:12:22 | 07:23:10 |
| 시작된 task | 55 | 29 |
| 종료된 task | 49 | 24 |
| 판매 완료 | 34 | 16 |
| 평균 첫 호가 | 210.00 | 196.07 |
| 평균 서명 영수증 가격 | 187.94 | 171.88 |
| 판매 완료 / 시작 task | 61.82% | 55.17% |

평균 첫 호가는 약 13.93, 평균 거래가는 약 16.07 낮아졌습니다. 그러나 로컬 거래 성사 비율도 낮아졌으므로 개선되었다고 결론 내리지 않았습니다. 품목 구성, 유입 주문, 예산, 관찰 기간, API 오류가 서로 다르고 무작위 비교 실험도 아니므로, 후기가 가격 하락을 일으켰다는 인과관계도 단정하지 않았습니다. 로컬 영수증 가격은 판매자가 생성한 기록이며 각 거래의 시장 검증 여부까지 확인한 집계는 아닙니다.

최신 원본 로그는 요약 저장 이후에도 계속 늘어났습니다. 07:30:17 UTC까지의 after 전체 기록에는 시작 52건, 판매 16건, 거절 2건, 실패 29건이 있습니다. 따라서 위의 29건짜리 고정 요약을 최종 전체 실행 결과라고 표현하지 않았습니다. 특히 뒤에 발생한 API 한도 초과가 실패 증가에 영향을 주었습니다.

점유율은 대시보드에서 전후 모두 0%로 표시된 사실만 기록하였습니다. `comparison.json`의 `market.share`는 `null`, `market_comparison_available`은 `false`입니다. 시장 전체 거래량의 분모와 같은 기간의 내 거래량을 확보하지 못했으므로 정확한 점유율 비교는 제한 사항으로 남겼습니다. 로컬 거래 성사 비율을 시장 점유율로 대신 쓰지 않았습니다.

### 도전 2 — 영수증 불일치를 발견하고 이후 회피한 사례

실제 시장에서 `elm-street`와 bookshelf를 협상하였습니다. 예산과 마지막 제안은 모두 295였고, 상대의 완료 메시지에도 295로 거래되었다고 표시되었으나, 영수증 가격은 335.0이었습니다. 다음은 `logs/challenge-2/avoidance.jsonl`에 남은 실제 관찰의 주요 필드입니다.

```json
{
  "time": "2026-10-24T07:02:31.519208+00:00",
  "event": "receipt_mismatch",
  "shop_name": "elm-street",
  "item": "bookshelf",
  "budget": 295,
  "last_offer": 295,
  "received_receipt_price": 335.0,
  "reason": "Receipt exceeds budget"
}
```

위 블록은 원본의 중첩된 `received_receipt.price`를 읽기 쉽게 펼친 발췌입니다. 원본 로그에는 실제 영수증 전체와 task id가 보존되어 있습니다. 구매자는 이 영수증을 정상 구매로 받아들이지 않고 `receipt: null`과 별점 1을 반환하였습니다. 키트에도 다음과 같이 거래가 인정되지 않았습니다.

```text
16:02:33 [market] ledger #527275: no deal, stars 1, verified False, review not shown
```

이후 07:02:48 UTC 선택 로그에서는 `blocked_names`에 `elm-street`가 들어가고, `selected_name`은 `25620027`로 바뀌었습니다. 당시 `elm-street`의 점수는 4.9로 더 높았지만 직접 경험한 불일치를 우선하여 제외하였습니다. 최신 로그에는 이 차단 이름을 포함하는 후속 선택 이벤트가 68개 남아 있습니다.

따라서 단순히 나쁜 후기를 보고 피한 사례뿐 아니라, 직접 받은 영수증의 불일치를 기억하여 이후 선택에 반영한 사례를 확보하였습니다. 상대의 의도를 추정하기보다 합의 가격·예산과 영수증 가격이 달랐다는 관찰을 근거로 회피하였습니다. API 오류나 예산 내 합의 실패는 같은 차단 근거로 사용하지 않았습니다.

## 6. 배포 방법과 검증 결과

가게와 구매자는 로컬 Windows PC에서 실행하고, ngrok로 외부 접근 경로를 만들었습니다. ngrok의 공개 주소는 `https://gone-preset-outsmart.ngrok-free.dev`이며 로컬 8900번 포트로 연결하였습니다. 시장 키트가 해당 포트에서 프록시를 제공하고 판매자 프로세스를 실행하며, 구매자는 키트가 전달하는 주문에 따라 실행됩니다.

키트 실행은 저장소 루트에서 다음과 같이 수행하였습니다. 실행 창에는 `GROQ_API_KEY`와 GitHub claim code를 담은 `MARKET_OWNER`를 환경변수로 설정하였습니다. 가격 변경 후 실행에는 `MARKET_RUN_ID=live-market`, `MARKET_SHOP_NAME=26512076`, `PRICE_PHASE=after`를 사용하였습니다.

```bat
python scripts/market_kit.py join --dir submissions/26512076/week-07 --market https://market.ourolabs.ai --name 26512076 --port 8900 --public-url https://gone-preset-outsmart.ngrok-free.dev
```

구매자·판매자 코드는 시장 API를 직접 호출하지 않고 키트를 통해 참여합니다. 공개 Agent Card 경로는 `/.well-known/agent-card.json`입니다. 현재 방식은 PC, ngrok와 키트가 계속 실행되어야 하므로, 노트북을 닫거나 PC를 절전·종료하면 유지되지 않습니다. 마감까지 계속 가동해야 하는 운영 조건이 남아 있으며, 보존된 등록 로그만으로 마감 시각의 접속 상태나 GitHub 소유자 연결 상태까지 증명할 수는 없습니다.

`agent.json`에는 다음 실행 명령이 들어 있습니다.

```json
{
  "seller": {"cmd": "python seller.py --port {port}"},
  "buyer": {"cmd": "python buyer.py --url {url} --item {item} --budget {budget}"},
  "market_buyer": {"cmd": "python market_buyer.py --shops {shops} --item {item} --budget {budget}"}
}
```

2026-10-24 06:47:50 UTC에 시작된 market test의 `test-report.json`에는 25개 결과가 모두 `pass: true`로 저장되어 있으며, `logs/market-test.log` 마지막 줄도 다음과 같습니다.

```text
25/25 passed -> PASS
```

판매자는 lamp 거래를 세 번 모두 110에 완료하고 서명 검사를 통과하였습니다. bookshelf 테스트에서는 260 미만으로 제안하지 않았습니다. 구매자는 lamp를 세 번 모두 120에 구매하였고, bookshelf 예산 230에서는 200과 230을 제안한 뒤 영수증 없이 종료하였습니다. 요청 헤더와 taskId 검사를 통과하였으며, 후기 비교 테스트에서는 세 번 모두 좋은 후기의 가게를 선택하였습니다. 이는 제출된 실제 실행 기록에 근거한 결과입니다. 별도의 `verify_week07_local.py` 검사는 모델 호출을 대체한 오프라인 검사이므로 market test와 구분합니다.

재현 및 제출 확인 명령은 저장소 루트에서 다음과 같습니다.

```bat
python scripts/market_test_week07.py submissions/26512076/week-07
python scripts/check_week07.py submissions/26512076/week-07
```

현재 자료는 market test 통과를 보여 주지만, 이번 보고서 작성 시점에 `check_week07.py`를 새로 실행한 결과는 포함하지 않습니다. 최종 제출 전 해당 검사를 실행하고, 마감 시각에 시장 등록과 가동 상태를 확인해야 합니다. 개인키 `shop_key.pem`, `buyer_key.pem`과 API 키는 공개 커밋 대상에서 제외하여야 하며, 기존 시장 서명에 사용하는 개인키는 실행 PC에서 보관하여야 합니다. 과거 실패와 수정 과정의 커밋은 squash하지 않고 유지하는 것이 제출 규칙입니다.
