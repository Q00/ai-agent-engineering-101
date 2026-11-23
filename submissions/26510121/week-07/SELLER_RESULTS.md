# 판매자 구현 · 3단계 실행 결과

## 결과

2026-10-17, 최초 실제 판매자 검증 실행에서 **12/12 통과**했다.
이 결과는 구매자까지 포함한 공식 전체 trade test 통과를 의미하지 않는다.

- 원본 실행: `logs/seller/stage-20261017T041758.156194Z/`
- 결과 파일: 같은 디렉터리의 `seller-summary.json`
- 입력·응답·오류: `client-console.log`, `server-console.log`
- 모델 프롬프트·응답·검증·사용량: `model/model-*.jsonl`
- task별 수신·반영·영수증·취소: `model/server-events.jsonl`

## 실제 거래

| 확인 | 실제 관찰 | 결과 |
|---|---|---|
| 카드 | Seochanit Shop, JSONRPC, 1.0, negotiate skill | 통과 |
| 첫 호가 | lamp: asking 120, INPUT_REQUIRED | 통과 |
| lamp 거래 ×3 | 각각 120 제안을 모델이 수락, COMPLETED+receipt | 3회 통과 |
| 종료 후 메시지 | JSON-RPC -32004, UNSUPPORTED_OPERATION | 통과 |
| bookshelf 최저가 ×3 | 첫 호가 300 → 제안 140 → 역제안 260 → 제안 170 → REJECTED | 3회 통과 |
| 없는 상품 | sofa 요청은 REJECTED | 통과 |
| 취소 | chair task에 CancelTask → CANCELED | 통과 |
| 버전 헤더 누락 | -32009, VERSION_NOT_SUPPORTED | 통과 |

모델이 내놓은 120, 300, 260은 이 실행에서 관찰한 값이다. 코드에 해당 호가나 양보 공식을 넣지 않았다.
최저가 100/180/260은 과제의 고정 설정이고 코드에서도 검사한다.

영수증 원문에는 `{"item": "lamp", "price": 120.0, "seller": "Seochanit Shop"}`가 있다.
내부 판단 가격은 정수지만 SDK의 JSON data Part를 거치면 숫자가 120.0처럼 표현될 수 있다.
구매자 구현에서는 유한한 숫자·정수 값·품목·예산을 확인해야 하며, 문자열 가격이나 bool은 허용하지 않아야 한다.

## 하네스와 실행 구조

- 요청 모델 `gpt-4o-mini`, 실제 응답 모델 `gpt-4o-mini-2024-07-18`.
- OpenAI Responses API, strict JSON Schema, temperature 0, store false.
- 16회 API 호출, 입력 7,287토큰, 출력 707토큰. 이 실행에서는 검증 재질문이 없었다.
- 모델은 역할 프롬프트, 고정 최저가, 관찰된 제안, 이전 호가·요약·최근 4턴을 받았다. 고정 구매자의 비공개 예산은 제공하지 않았다.
- 각 결정 worker는 `.runtime/negotiation-empty/`를 작업 디렉터리로 사용했다.
- 동일 task의 상태 변경은 lock으로 직렬화하고 종료된 거래는 재사용하지 않는다.

실제 실행 명령:

```powershell
.\.venv\Scripts\python.exe lab\check_seller.py
```

검증기는 현재 `scripts/trade_test_week06.py`의 `negotiate`, `send`, `rpc`, 시나리오를 읽어 고정 구매자를 재사용했다. 공식 seller 조건 9개에 대응하는 확인과 추가 프로토콜 확인 3개를 수행했다. 원본 kit는 수정하지 않았고, 서버는 Windows에서 직접 시작·종료했다.

## 오프라인 검증과 과정 기록

`logs/seller/offline-20261017-041551-860.log`에는 **27개 테스트 통과**가 기록돼 있다.
기존 계약 검사 19개와 하네스 검사 8개다. 후자는 모의 API 응답으로 다음을 확인한다.

- 잘못된 최저가 제안을 모델에 다시 질문하고, 두 번째도 잘못되면 실패 처리.
- 유효한 모델 가격을 다른 가격으로 바꾸지 않음.
- HTTP 인증 오류 본문·네트워크 세부 오류·키를 로그에 유출하지 않음.
- 출력 잘림을 정상 결정으로 사용하지 않음.
- task 기억의 최근 4턴 제한과 task 간 분리.

이 모의 실패는 실제 모델의 실패 사례가 아니다. 이번 판매자 실제 실행은 첫 시도에 통과했고 실패를 꾸며내지 않았다. 앞선 실습 단계의 실제 실패는 `lab/RESULTS.md`와 원본 로그에 보존돼 있다.

## 학습 포인트와 남은 작업

같은 A2A 상태 규격을 사용해도 가격 정책은 별개다. 이 모델은 최저가 260인 책장에 300을 부르고 260까지 낮춘 뒤 반복된 낮은 제안을 거절했다. A2A가 이 가격이나 거절 시점을 정해 준 것은 아니다.

다음 단계는 구매자다. 카드·skill 확인, 버전 헤더, taskId 유지, 모델의 제안·중단 판단, 영수증 검사, 240초 자체 기한을 구현한다. 이후 공식 전체 테스트와 필수 도전 1·2를 진행한다.
