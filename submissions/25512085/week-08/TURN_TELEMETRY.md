# Round 3 turn telemetry v1 — 로컬 구현, 미배포

기존 `diagnostic`과 동일하게 stderr 한 행을 JSON **문자열**로 인코딩한다.
문자열을 JSON decode한 뒤 `[buyer] `를 제거하고 내부 JSON을 decode한다.
JSON 객체를 stderr에 직접 출력하지 않아 기존 diagnostic 소비자와 kit의
최종 stdout 계약을 유지한다. 마지막 stdout JSON의 필드는 바꾸지 않는다.

## 필드

| 필드 | 의미 / 제한 |
|---|---|
| `event`, `schema_version` | `buyer_turn`, `1` |
| `timestamp_utc` | 이벤트 생성 UTC ISO8601, milliseconds; 서버 거래시각 아님 |
| `attempt_id`, `seq` | 무작위 32hex 시도 ID, 시도 내 1부터 증가. task/order/account와 매핑 저장 안 함 |
| `sku` | `[gGfF][0-9a-fA-F]{7,10}`만 허용, 나머지 null |
| `phase`, `boundary` | `card/opening/offer/attempt`, `request/response`; attempt는 요약 response |
| `turn_index` | card/opening/summary=0, 제안=1..3, finalaccept=4 |
| `policy_branch` | `first/counter35/third/finalaccept`; card/opening/summary는 first |
| `ask_before`, `offer_sent`, `ask_after` | 기존 parser/계산의 정수 가격. 0..10^12 외 null. 미관측 null. request의 ask_after는 null |
| `response_state` | `completed/inputrequired/rejected/canceled/other/null` 허용 목록. request는 null |
| `outcome` | `request/completed/inputrequired/rejected/canceled/other/error/timeout` |
| `error_type` | null 또는 `TimeoutError/HTTPError/ParseError/RPCError/Error`. 클래스명·repr·서버 오류 원문을 전사하지 않음 |
| `receipt_selected`, `payment_confirmed` | attempt 요약에만 존재. 선택 여부 및 **항상 false**인 결제 확인 |

card response의 completed는 card 처리 성공, 협상 response의 completed는
서버 task 완료, attempt의 completed는 buyer 영수증 선택을 뜻한다.
어느 것도 실제 결제 증거가 아니다. 정책 거절로 task completed여도 요약은 rejected일 수 있다.
`offer_sent`는 기존 RPC로 **시도한** 값이며 네트워크 실패 때 실제 전송/수신은 알 수 없다.
request가 기록되어도 시한 검사에서 중단될 수 있다. request/result 로그와 시도 요약은
최선 노력이다. stderr/직렬화/시계 실패, 프로세스 강제 종료 시 누락될 수 있다.
latency는 이번 버전에 추가하지 않았다.

## 합성 예시 (내부 객체, 실제 고객/운영 데이터 아님)

```json
{"event":"buyer_turn","schema_version":1,"timestamp_utc":"2026-11-06T00:00:00.000+00:00","attempt_id":"00000000000000000000000000000001","seq":8,"sku":"f00000001","phase":"offer","boundary":"response","turn_index":2,"policy_branch":"counter35","ask_before":100000,"offer_sent":74000,"ask_after":100000,"response_state":"inputrequired","outcome":"inputrequired","error_type":null}
```

최종 선택 요약의 `receipt_selected:true,payment_confirmed:false`를
actual-paid/settled로 집계하지 않는다. 순서는 시각이 아니라 `(attempt_id, seq)`로 본다.
오류 원문·예산·계정명·대화·receipt signature·URL·자격증명·실제 task/order ID는
새 이벤트에서 제외한다. 기존 diagnostic은 이번 변경 범위 밖이며 원래 동작 그대로다.

## 용량과 보관

- 시도당 card 2 + opening 2 + 세 제안 6 + finalaccept 2 + summary 1 = **13행**.
- UTF-8 개행 포함 **1,024 bytes/행** 하드 캡. 3시도 hook에서 최대 **39,936 bytes**.
- kit의 시간 및 `[buyer] ` prefix 17 bytes/행을 합친 추가분 상한은 **40,599 bytes/hook**.
- 합성 finalaccept fixture 실측: 13행, 최장 **480 bytes**, 합계 **5,562 bytes**;
  kit prefix 가정 포함 5,783 bytes. 이는 합성 fixture 실측이지 운영 빈도 실측이 아니다.
- 예를 들어 **60 hooks/hour, 매번 3시도, 모두 최대 행 크기**를 가정하면
  추가 출력 상한은 약 **2.44 MB/hour (58.46 MB/day)**. 실제 hook 빈도는 읽지 않았다.
- 파일을 직접 만들지 않고 kit의 기존 `logs/market-kit.log` 경로를 사용한다.
  VM 소스 읽기에서 `LOG_MAX=20_000_000`, 초과 시 `.log.1`로 replace 후 append를 확인했다.
  현재+이전 파일 약 40MB 수준이나 검사 전 마지막 append 때문에 엄밀한 총량 하드 캡은 아니다.
  빈도가 높으면 디스크 증가보다 과거 관측의 보존 기간 단축이 먼저 문제다.
  kit stdout을 외부에서 중복 저장하는 경로의 보관 정책은 이번에 확인하지 않았다.

## 검증 / 한계

새 합성 테스트는 모든 turn·RPC 오류·card/각 제안 timeout·parse 오류·경계 deadline·
기록 실패 시 동일 result/RPC/hub·정수/문자열 크기 제한·민감 필드 배제·legacy 무출력·
stdout 한 줄·기존 parser 호출 횟수를 확인한다. 최종 명령·결과는 작업 REPORT에 기록한다.
운영 거래/checkout 성공, 실시간 이벤트 수집/지연 및 VM 배포 검증은 수행하지 않았다.
