# 필수 도전 1: 대규모 카탈로그 판매자

현재 구현으로 **실제 모델·HTTP/A2A 검사 15/15**, 오프라인 51개, 기본 공식 trade test 21/21을 통과했다.
모델은 기존 기본값 `gpt-4o-mini`, 실제 응답 모델은 `gpt-4o-mini-2024-07-18`이다.

## 구조와 직접 확인할 지점

1. `catalog/generate.py`가 원본 합성 상품 3,000개를 생성한다. 데이터·출처 설명을 함께 커밋했다.
2. `prompts/need.md`로 모델이 설명을 용도·재질·색상·책 수·크기 조건으로 해석한다.
3. `Catalog.retrieve()`가 모든 명시 조건으로 필터하고 최대 8개를 전달한다. 후보 순위에 최저가를 사용하지 않는다.
4. `prompts/select.md`로 모델이 후보 중 상품을 선택한다. 코드가 후보 소속과 조건을 검사한다.
5. 원본 상품에서 최저가를 다시 읽고 기존 판매자 하네스로 협상한다. task의 상품 ID와 최저가는 요약으로 덮어쓰지 않는다.

모델이 **무엇을 찾을지·무엇을 고를지·어떤 가격으로 협상할지** 결정한다.
코드는 검색 범위와 거래 제한을 검사한다. 고정 구매자는 판매자의 호가를 제안해 실제 수락과 영수증을 확인한다.
이 검사는 도전 2의 모델 구매자·여러 가게 선택을 대신하지 않는다.

## 재실행

이 제출 폴더에서, 이미 연결한 로컬 키를 사용한다. 키가 없으면 실행을 건너뛰고 설정 상태를 확인한다.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -u lab/check_catalog.py
```

각 실행은 새 `logs/challenge-1/run-<UTC>/` 폴더를 만든다. 서버는 검사 프로세스 안에서 시작·종료한다.
수동 서버는 `python seller.py --catalog catalog/products.jsonl --port 9999`로 실행한다.
Agent Card의 skill에 설명 입력과 추가 질문, 선택 이후 정수 가격 교환 방식을 적었다.
선택된 상품은 첫 호가 메시지의 data Part `selected_item`으로 공개하며, 최저가는 공개하지 않는다.

## 실제 결과

최종 실행: `logs/challenge-1/run-20261017T060327.556765Z/`.
`summary.json`은 시나리오 결과, `client-events.jsonl`은 A2A 요청·응답,
`server/`는 원문 프롬프트·관찰·모델 JSON·검증·선택·가격·영수증이다.
`audit.json`에서 실행 소스의 SHA-256 일치와 후보 제한을 다시 확인했다.

| 확인 항목 | 결과 |
|---|---|
| 다섯 용도의 설명 기반 구매를 각 2회 | 10/10, 실제 영수증 10개 |
| 모호한 요청 → 추가 설명, 같은 task에서 구매 | 통과, 영수증 1개 |
| 책상 높이를 모르는 요청 → 80cm 설명 후 구매 | 통과, 영수증 1개 |
| 책 40권·최대 높이 20cm (일치 상품 없음) | REJECTED, 거래 없음 |
| 티타늄 좌석 (미지원 재질) | REJECTED, 대체 상품 판매 없음 |
| 선택된 상품 최저가 370에 제안 369 반복 | 호가 [400, 370, 370], 거래 없이 취소 |

정답은 모델이 추출한 조건을 그대로 믿지 않고 검사 도구에 별도로 적었다.
각 구매에서 실제 카탈로그의 용도·재질·색상·용량·치수가 그 정답에 맞는지 확인했다.
정확히 하나의 영수증, 선택된 ID, 실제 합의 가격, 해당 상품의 최저가도 검사했다.
동등하게 조건을 만족하는 상품은 여러 개일 수 있어 특정 SKU 하나만을 정답으로 강제하지 않는다.

한국어 요청의 실제 선택 (`server/server-events.jsonl`):

```json
{"item_id":"cabinet-0362","material":"metal","color":"blue","height_cm":60,"width_cm":40,"capacity_books":60}
```

모델의 실제 선택 사유:

> This blue metal cabinet holds 60 books and meets the height and width limits specified.

상대적 높이를 바로 추정하지 않고 실제로 질문한 사유:

> What is the height of your desk to determine the maximum height for the bookshelf?

전체 데이터 파일은 891,294바이트다. 실제 전달 후보는 최대 8개, 후보 JSON 문자열만 최대 2,258자였다.
이 문자 수는 전체 요청의 토큰 수가 아니다. 역할 지침·요청 원문·구조화 조건은 별도로 전달한다.
실제 API 요청은 need 17회, select 13회, seller 27회였고 검증 실패·재질문은 0회였다.
협상 턴에는 전체 카탈로그나 후보 8개를 다시 넣지 않고 고정된 ID·최저가·최근 대화·요약을 사용했다.

## 실패와 수정 이력

1. 첫 검사 도구는 서버 로깅 설정 중 `AttributeError: 'Tee' object has no attribute 'isatty'`로 중단됐다.
   `STARTUP_FAILURE.md`와 당시 생성 파일을 커밋했고, 기록 객체의 터미널 확인 메서드를 추가했다. 모델 호출은 없었다.
2. 실제 첫 전체 도전 실행은 **9/15**였다. 원본은 `run-20261017T060018.636561Z/`와 커밋 `b2b47eb`에 보존했다.
   `console.log`에는 `FAIL open-books-1`의 `ReadTimeout`이 있다. 세 번의 모델 호출이 필요할 수 있는 첫 응답에
   기본 10초 대기가 짧았다. 카탈로그 검사 클라이언트에 125초 대기를 명시했고 기본 구매자의 값은 유지했다.
3. 선택 모델은 다음처럼 올바른 후보 ID와 **빈 사유**를 내보냈다. 재질문에서도 반복돼 FAILED가 됐다.

```json
{"action":"select","item_id":"chair-0213","reason":""}
```

실제 `server/model-7904fd710a7e49bab5bdc61c73bb0e7c.jsonl`:

```text
"event": "validation_failed", "attempt": 2, "code": "invalid_reason_length"
"event": "harness_failed", "code": "decision_invalid_after_repair"
```

카탈로그 역할의 JSON Schema에 reason의 minLength=1·maxLength=240을 추가하고 비어 있지 않은 한 문장을 명시했다.
지원 범위는 [OpenAI Structured Outputs 문서](https://developers.openai.com/api/docs/guides/structured-outputs)에서 확인했다.
생성 단계의 길이 제한과 별개로 로컬 검증도 유지한다.

4. 상대적 높이 요청에는 모델이 올바르게 clarify를 선택하면서 이미 안 조건을 남겼다.
   그런데 초기 계약이 clarify의 모든 조건을 null로 요구해 `non_search_requires_null_constraints`로 두 번 거절했다.
   알려진 조건을 보존하는 것은 안전하므로 이를 허용하고, **action=search만 검색을 실행**하도록 유지했다.
   실제 추가 설명 전에는 선택·가격 제안·거래가 일어나지 않는다. 이 경로도 오프라인 검사에 추가했다.

이 세 수정을 함께 적용한 뒤 15/15를 얻었다. 각 수정의 효과를 분리한 인과 실험은 아니다.
실패 로그를 고치거나 삭제하지 않았고, 성공 시 새 실행 폴더를 만들었다.

## 범위와 다음 단계

합성 가구의 제한된 속성을 다룬다. 모델이 임의의 자연어 조건을 항상 정확히 해석한다는 보장은 없다.
이 실행은 영어·한국어의 명시 조건과 추가 질문 경로를 확인한 것이며 자유로운 모든 상품 설명의 정확도 측정은 아니다.
서버 재시작 복원, 긴 숫자 문장의 제안 해석, 실제 결제는 다루지 않았다.
다음 필수 도전 2에서는 여러 가게의 카드 탐색·동시 협상·총예산·중복 구매 방지를 구현한다.
