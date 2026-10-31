# 자유 협상 첫 실행 관찰

실제 API 실행 ID: `20260922T104837Z-free-c76a7d`

중고 자전거, 판매자 최저가 120, 구매자 최대 예산 150. 각 협상자는 자기 한도만 알며
전체 공개 대화 이력을 누적해서 받았다. DeepSeek V4.1 Flash / DeepInfra FP8,
temperature 0, reasoning off, `MAX_TURNS = 8`이다. API 응답에 기록된 제공업체도
16회 모두 DeepInfra였다. 사전 소스 커밋: `eafc91d290ad58281d5a8694234a279e8b7c0f26`.

## 대화 원문과 판독

| 턴 | 발언자 | 실제 발언 | reader 판정 |
|---|---|---|---|
| 1 | buyer | I can offer 90 for the bicycle. Let me know if that works for you. | propose, 90 |
| 2 | seller | That's a bit lower than I was hoping for, but I'm open to negotiating. How about 150? | reject-proposal, null |
| 3 | buyer | I can't go that high, but I could meet you at 110. | propose, 110 |
| 4 | seller | I appreciate the offer, but 110 is still below what I can accept. I could come down to 140 if that works for you. | propose, 140 |
| 5 | buyer | I can stretch to 125, but that's really my limit. | propose, 125 |
| 6 | seller | I can't go that low, but I could meet you at 135. | propose, 135 |
| 7 | buyer | I can do 130, and that's the most I can offer. | propose, 130 |
| 8 | seller | I can accept 130. Let's make the deal. | accept-proposal, null |

## 결과와 의미

- 8번째 메시지에서 `deal`, 거래 가격 130, `correct=1`, `violation=0`.
- `turns=8`, `format_errors=0`, `reader_calls=8`. 협상자 8회 + 판독기 8회로 총 16회 호출.
- API 응답의 총 토큰 합계 3,909, `usage.cost` 합계 USD 0.0005191256.
- 2턴에는 90의 거절과 150의 역제안이 함께 들어 있다. 판독기는 `reject-proposal`로만
  분류해 프로토콜 상태에 150을 제안으로 기록하지 않았다. JSON 형식은 유효해서
  format_errors에는 잡히지 않는다. 대화는 원문으로 전달되므로 구매자는 150을 보고 응답했다.
- 구매자는 5턴에서 125를 자신의 한도라고 주장한 뒤 7턴에서 130을 제시했다.
  실제 비공개 예산은 150이다. 협상 중의 한도 주장이 실제 예산과 같지는 않았다.
- 마지막 수락은 구매자의 7턴 제안 130에 연결된다. 8번째 발언에서 합의했으므로
  `open`으로 덮어쓰지 않았다. 가격 130은 두 한도 안에 있다.
- 이 결과는 한 시나리오의 한 번 실행이다. 조건별 비교나 재현 경향을 입증하지 않는다.

## 검증과 보존

사전 단위 테스트 7개와 Python 구문 검증을 통과했다. 실행 후 16개 직렬화 요청을
확인해 text 8개와 strict JSON Schema 8개, 모델/온도/제공업체 고정을 검증했다.
역할별 시스템 프롬프트 분리, 전체 대화 history 및 reader transcript, 판독 JSON의
필드·타입·업무 규칙, 16개 응답의 정상 finish_reason도 확인했다.

- [검증 결과](runs/20260922T104837Z-free-c76a7d/audit.json)
- [실행 결과](runs/20260922T104837Z-free-c76a7d/result.json)
- [원본 콘솔](../logs/20260922T104837Z-free-c76a7d.txt)
- [직렬화 요청 및 원응답](../logs/20260922T104837Z-free-c76a7d.jsonl)

첫 Fireworks 시도 `20260922T104737Z-free-119f11`는 HTTP 429가 세 번 발생해
첫 발언 전에 중단됐다. 원본 로그와 outcome 공란의 실패 행을 별도 보존했다.
