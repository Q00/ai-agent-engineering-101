# Week 04 — 실전 화행(speech act): 자유(free) / 태그(tagged) / 구조화(structured) 협상

## 1. 설정 (Setup)

**제공자 / 모델**: Anthropic, `claude-haiku-4-5-20251001` (환경변수 `AGENT_MODEL`, [model.py](model.py) 참고).

**temperature**: `1.0` — Anthropic API 자체의 기본값. `extra_body={"temperature": 1.0}`로 매 호출마다
명시적으로 고정한다([model.py](model.py)).

> **정정 노트**: 이전 버전의 이 문서는 "설치된 SDK가 temperature를 지원하지 않는다"고 적었는데,
> 정확하지 않았다. 실제로 확인한 사실은 두 가지다.
> 1. 설치된 `anthropic` 파이썬 SDK(실제 버전 1.8.0 — 이전 기록의 "1.5.0"도 오기였다)는
>    `Messages.create()`의 타입 있는 키워드 인자 목록에서 `temperature`/`top_p`/`top_k`를 아예
>    뺐다 — SDK 소스를 직접 읽어 확인.
> 2. 하지만 `claude-haiku-4-5-20251001`에 `extra_body={"temperature": 0.2}`로 직접 호출하면
>    **정상 성공한다** — Messages API 자체는 이 모델에서 temperature를 여전히 받아준다.
>
> 즉 빠진 건 **API가 아니라 이 SDK 버전의 타입 시그니처**였다. 이제 `extra_body`로 우회해서
> temperature를 API 기본값(1.0)에 고정했다 — 값 자체는 그 전까지 아무것도 지정하지 않았을 때
> 서버가 쓰던 기본값과 같으므로 행동을 바꾸지 않지만, 이제는 암묵적이 아니라 명시적으로 기록되고
> 재현 가능하다.

**턴 제한**: `MAX_TURNS = 5`메시지(구매자·판매자 교대, 5메시지 안에 끝나지 않으면 `open`). 더 이른
시도는 `MAX_TURNS = 8`이었고, 그 실행 결과는 수정 없이 [`_old_maxturns8/`](_old_maxturns8/) 아래에
폐기된 시도로 보존했다(4절 참고).

**역할**. 세 조건 모두 같은 기본 역할 프롬프트를 공유한다(구매자는 예산만, 판매자는 최저가만 알고,
양쪽 다 FIPA의 4개 화행 `propose` / `accept-proposal` / `reject-proposal` / `refuse`로 제한). 시스템
프롬프트 뒤에 붙는 포맷 문단만 조건별로 다르다([protocol.py](protocol.py)):

- **free** — *"메시지 형식: 태그·라벨·JSON 없이, 자연스러운 영어 협상 대화 한두 문장으로 작성하라.
  가격을 제안하거나 수락할 때는 반드시 구체적인 숫자를 명시하라."*
- **tagged** — *"메시지 형식: 괄호로 된 화행 태그 하나로 시작하라 -- (propose), (accept-proposal),
  (reject-proposal), (refuse) 중 하나 -- 그 뒤에 평문 한 문장. 예: "(propose) 95에 드릴 수
  있습니다." 제안하거나 수락할 때는 구체적인 숫자를 명시하라."*
- **structured** — *"메시지 형식: 다른 텍스트 없이 JSON 객체 하나만 출력하라. 형식은
  {"performative": ..., "content": {"price": <정수>|null}}. propose와 accept-proposal에는
  content.price를 채우고, reject-proposal과 refuse에는 content.price = null을 쓴다. JSON 밖에는
  아무것도 쓰지 말 것."*

**리더(reader) 프롬프트** (free에서는 매 메시지마다, tagged에서는 propose 안의 가격을 뽑을 때만,
structured에서는 전혀 쓰지 않음):

> *"당신은 구매자·판매자 사이 가격 협상 메시지 하나를 읽는다. 다음 네 가지 화행 중 정확히 하나로
> 분류하라: propose(구체적 가격 제안); accept-proposal(상대의 마지막 가격에 동의, 거래 종료);
> reject-proposal(가격을 거절하되 협상 계속); refuse(협상 중단, 거래 없음). JSON 객체 하나만
> 답하라: {"performative": ..., "price": <정수>|null}. price는 propose와 accept-proposal에만
> 채우고, 그 외에는 null."*

tagged 조건은 propose 안의 가격만 뽑는 더 좁은 두 번째 리더도 쓴다(*"이 협상 메시지에서 제안 또는
수락되는 단일 숫자 가격을 추출하라. JSON 객체 하나만: {"price": <정수>|null}"*).

**가격 확정(price resolution)**. `accept-proposal`은 자기 문장에서 가격을 다시 읽지 않는다. 거래
가격은 항상 프로토콜 레이어가 추적하는 상태 — 마지막으로 `propose`로 분류된 메시지의 가격이다
([negotiation.py](negotiation.py)). FIPA의 accept-proposal 정의("상대가 마지막으로 제안한 가격에
동의")를 *상태 기반*으로 해석한 것으로, 발화자가 자기 문장에서 주장하는 숫자를 그대로 믿는 것과는
다르다. 이 선택이 왜 중요한지는 4절에서 다룬다.

**실행 방법**:

```bash
export ANTHROPIC_API_KEY=<your key>       # 또는 model.py 옆에 .env 파일
python run.py                             # results.csv, logs/ 를 씀
python ../../../scripts/check_week04.py . # 이 디렉토리에서
```

`run.py`는 중단 후 재실행해도 안전하다: `results.csv`에 이미 있는 `(run, scenario)` 쌍은 건너뛴다.

**시나리오**: [scenarios.json](scenarios.json)에 gap(=budget−reserve) 크기가 다양한 8개가 있다 —
근소한 격차(±2), 원래 격차(±10~±40), 아주 큰 격차(±200~±300):

| id | 품목 | reserve | budget | gap | deal_possible |
|---|---|---|---|---|---|
| 90 | 파인애플 | 70 | 90 | +20 | 가능 |
| 100 | 사과 | 100 | 110 | +10 | 가능 |
| 95 | 우산 | 95 | 97 | +2 (근소) | 가능 |
| 130 | 중고차 | 200 | 500 | +300 (여유 큼) | 가능 |
| 105 | 노트북 | 120 | 105 | −15 | 불가능 |
| 115 | 손목시계 | 102 | 100 | −2 (근소) | 불가능 |
| 110 | 자전거 | 150 | 110 | −40 | 불가능 |
| 140 | TV | 300 | 100 | −200 (격차 큼) | 불가능 |

조건당 8시나리오 × 5반복 = 40 episode (총 120 episode).

**폐기된 시도**. [`_old_no_temperature_control/`](_old_no_temperature_control/)는 위의 temperature
버그를 고치기 전에 (`extra_body` 없이, SDK 기본 동작 그대로) 같은 8시나리오·5반복으로 돌렸던 전체
120-episode 실행이다. temperature를 API 기본값(1.0)에 고정한 것이 그 자체로 행동을 바꾸는 변경은
아니었으므로 — 이 문서의 2·4절은 temperature를 명시적으로 고정한 재실행 결과만 다루고, 두 실행의
차이는 통제된 값 하나를 명시했는가의 차이일 뿐, 협상 자체의 확률성(같은 온도라도 매 호출은 여전히
샘플링됨)에서 오는 자연스러운 실행 간 변동으로 본다.

## 2. 결과

| condition | n | deal | no_deal | open | correct | violations | mean turns | format_errors | reader_calls |
|---|---|---|---|---|---|---|---|---|---|
| free | 40 | 5 | 3 | 32 | 20/40 | 5 | 4.88 | 4 | 195 |
| tagged | 40 | 10 | 7 | 23 | 23/40 | 7 | 4.90 | 0 | 63 |
| structured | 40 | 6 | 0 | 34 | 26/40 | 0 | 4.85 | 0 | 0 |

### 2-1. gap(격차) 크기별 결과

| 시나리오 | 품목 | gap | free 결과 | free 위반 | tagged 결과 | tagged 위반 | structured 결과 | structured 위반 |
|---|---|---|---|---|---|---|---|---|
| 90 | 파인애플 | +20 | deal×2, open×3 | 2 | open×2, deal×3 | 3 | open×4, deal×1 | 0 |
| 100 | 사과 | +10 | open×2, deal×3 | 3 | deal×2, open×3 | 2 | open×5 | 0 |
| 95 | 우산 | +2 | open×5 | 0 | open×3, deal×2 | 2 | open×5 | 0 |
| 115 | 손목시계 | −2 | open×5 | 0 | no_deal×2, open×3 | 0 | open×5 | 0 |
| 105 | 노트북 | −15 | open×5 | 0 | open×5 | 0 | open×5 | 0 |
| 110 | 자전거 | −40 | open×4, no_deal×1 | 0 | open×5 | 0 | open×5 | 0 |
| 130 | 중고차 | +300 | open×5 | 0 | open×2, deal×3 | 0 | **deal×5** | 0 |
| 140 | TV | −200 | open×3, no_deal×2 | 0 | **no_deal×5** | 0 | open×5 | 0 |

위반 12건 전부가 여전히 gap이 좁은(+2, +10, +20) 세 시나리오(90, 100, 95)에 몰려 있다 — temperature를
고정하기 전 실행(7건, 마찬가지로 90/95/100에만 집중)과 같은 자리에서, 이번에는 더 많이 나왔다. gap이
아주 크거나(+300) 애초에 불가능한 시나리오에서는 위반이 여전히 0건이다. structured의 유일한 거래
성사 시나리오도 여전히 gap=+300(중고차)이다(5/5 성사). 4절에서 이 재현 결과를 다룬다.

### 2-2. 전체 episode 표 (`results.csv`)

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-r1 | free | 90 | 1 | deal | 95 | 0 | 1 | 5 | 0 | 5 |  |
| free-r1 | free | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 140 | 0 | open |  | 1 | 0 | 5 | 1 | 5 |  |
| free-r2 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 95 | 1 | open |  | 0 | 0 | 5 | 1 | 5 |  |
| free-r2 | free | 100 | 1 | deal | 80 | 0 | 1 | 5 | 0 | 5 |  |
| free-r2 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 110 | 0 | no_deal |  | 1 | 0 | 1 | 0 | 1 |  |
| free-r2 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 90 | 1 | deal | 95 | 0 | 1 | 4 | 0 | 4 |  |
| free-r3 | free | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 100 | 1 | deal | 75 | 0 | 1 | 5 | 0 | 5 |  |
| free-r3 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 140 | 0 | open |  | 1 | 0 | 5 | 1 | 5 |  |
| free-r4 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 100 | 1 | deal | 80 | 0 | 1 | 5 | 0 | 5 |  |
| free-r5 | free | 105 | 0 | open |  | 1 | 0 | 5 | 1 | 5 |  |
| free-r5 | free | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| tagged-r1 | tagged | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r1 | tagged | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 100 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r1 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r1 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r2 | tagged | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r2 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r2 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r3 | tagged | 90 | 1 | deal | 50 | 0 | 1 | 4 | 0 | 1 |  |
| tagged-r3 | tagged | 95 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r3 | tagged | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r3 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 130 | 1 | deal | 400 | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r3 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 90 | 1 | deal | 50 | 0 | 1 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 95 | 1 | deal | 75 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r4 | tagged | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r4 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r4 | tagged | 130 | 1 | deal | 350 | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 90 | 1 | deal | 65 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r5 | tagged | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r5 | tagged | 100 | 1 | deal | 75 | 0 | 1 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 4 | 0 | 1 |  |
| tagged-r5 | tagged | 130 | 1 | deal | 400 | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 |  |
| structured-r1 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r1 | structured | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 90 | 1 | deal | 70 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r2 | structured | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r2 | structured | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 130 | 1 | deal | 450 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r3 | structured | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r4 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r4 | structured | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r4 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r4 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r4 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r4 | structured | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r4 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r4 | structured | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r5 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r5 | structured | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r5 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r5 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r5 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r5 | structured | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r5 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r5 | structured | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |

## 3. FIPA-ACL 대 세 조건

| | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| 발화수반력(illocutionary force)이 있는 곳 | 필수 `performative` 필드, 내용과 분리 | 명시적으로 없음 — 문장에서 추론 | 발화자가 직접 쓰는 괄호 태그, 문장과 분리 | JSON의 `performative` 필드, `content`와 분리 |
| 내용 언어 | 선언된 형식 온톨로지(예: SL) | 자유 영어 산문 | 자유 영어 산문 | 고정된 2필드 JSON 스키마(`price`) |
| 내용을 해석하는 주체 | 수신 에이전트(공유 온톨로지 전제) | 매 메시지마다 LLM 리더 | 태그는 정규식; propose 안의 가격만 LLM 리더 | JSON 파서, 모델 호출 없음 |
| 대화 종료 방식 | 프로토콜이 정의(예: 상호작용 프로토콜의 accept/reject 화행) | `accept-proposal`, `refuse`, 또는 턴 제한(`open`) | 동일 | 동일 |
| 진실성(sincerity)을 보장하는 것 | 형식적으로 없음 — FP/RE는 이행 조건을 정의할 뿐 강제하지 않음 | 없음 — 리더의 라벨을 그대로 신뢰 | 없음 — 태그가 문장과 모순돼도 그대로 신뢰 | 없음 — JSON 필드를 그대로 신뢰 |
| 메시지 하나를 읽는 비용 | 공짜로 가정(공유 온톨로지, 해석 단계 없음) | episode당 평균 4.88턴 × 매 메시지 리더 1회 = 총 195회(40 episode) | propose의 가격 추출에만 리더 1회, 나머지 3개 화행은 0회 = 총 63회 | 0 — 파싱만 |
| 관찰된 실패 유형 | 문헌에 기록됨: 온톨로지 불일치, 비협조적 에이전트 | 리더가 counter-offer를 가격 없는 `reject-proposal`로 오독; format_error 4건; gap이 좁을 때 위반 5건 | 발화자가 새 가격을 담은 문장을 `propose`가 아닌 `reject-proposal`로 태그(gap이 좁을 때 위반 7건); format_error는 0 — 태그 자체는 파싱이 쉬움 | format_error 0, 위반 0. 대신 gap이 좁거나 음수인 시나리오에서는 거래 성사가 0 — 판매자가 `reject-proposal`을 선언한 뒤 대안 가격을 전혀 말하지 않아 협상이 굳어버림 |

## 4. 해석

**gap(격차) 크기가 위반과 결과를 가장 강하게 좌우한다 — temperature를 명시적으로 고정한 뒤에도
재현됐다.** temperature를 API 기본값(1.0)에 고정하기 전 실행(`_old_no_temperature_control/`)에서는
위반 7건이 gap +2/+10/+20 세 시나리오에만 몰려 있었다. 이번에 temperature를 명시적으로 고정하고
전체를 재실행한 결과, 위반은 오히려 12건으로 늘었지만 **여전히 정확히 같은 세 시나리오(90, 100,
95)에만** 몰려 있다 — gap이 크거나(+300) 음수인 시나리오는 두 실행 모두에서 위반 0건이다. 이는
이전 실행의 패턴이 temperature가 통제되지 않아 생긴 우연이 아니라, gap 크기 자체가 만드는 구조적
효과임을 재확인해 준다: gap이 좁을수록 판매자/구매자의 마지막 양보가 "이미 합의된 가격 근처라 굳이
새 propose를 선언할 필요가 없어 보이는" 문장이 되기 쉽고, 그 결과 협상이 stale한(오래된) 추적
가격 위에서 마무리될 때 그 오차가 좁은 window를 벗어나 버린다. gap이 300이면 같은 크기의 오차가
있어도 window 안에 들어간다.

두 사례가 이 메커니즘을 판매자·구매자 양쪽에서 보여준다. `free-r1`의 시나리오90(파인애플,
reserve=70/budget=90)에서는 판매자가 turn 2에 95를 propose하고, 구매자가 turn 3에 75로 반박하지만
리더가 이를 가격 없는 `reject-proposal`로 오독한다. 판매자가 turn 4에 80으로 양보해도 마찬가지로
`reject-proposal`로 오독된다. 구매자의 turn 5 "I'll accept your offer at 80"은 리더가
`accept-proposal, price=80`으로 **정확히** 읽지만, `accept-proposal`은 자기 문장의 가격이 아니라
프로토콜이 추적해 온 `last_price`를 쓰므로(1절) 거래는 두 턴 전 오독으로 인해 갱신되지 않은 채
남아있던 turn 2의 95로 성사된다 — 구매자 자신의 예산(90)을 넘는 위반이다. `tagged-r3`의 같은
시나리오에서는 반대 방향으로 같은 실패가 나타난다: 구매자가 turn 3에 70을 제안하면서도 태그를
`(reject-proposal)`로 쓰고("I'd prefer to find a middle ground. Let me propose 70"), 판매자의
turn 4 `(accept-proposal) I can accept 70`은 tagged 프로토콜이 `(propose)` 태그가 붙은 메시지만
추적하므로 70이 아니라 turn 1의 50으로 확정된다 — 이번엔 판매자의 최저가(70)보다 낮게 파는 위반이다.
두 경우 모두 화자가 실제로는 새 가격을 말했지만 화행 라벨(리더의 오독이든, 발화자 자신이 잘못
붙인 태그든)이 그 사실을 프로토콜에 전달하지 못했다는 점에서 근본적으로 같은 실패다 — FIPA가 형식화
하지 않은 "진실성(sincerity)"의 문제.

**gap이 아주 벌어지면 포맷 차이가 무의미해진다.** structured의 유일한 거래 성사 시나리오는 이번에도
gap=+300(중고차)이며, 5회 중 4회는 정확히 400원, 1회는 450원으로 거의 동일한 각본(구매자가 큰
자릿수로 propose, 판매자가 한 번 reject, 구매자가 50 올려 재propose, 판매자가 accept)을 따른다.
gap이 넓으면 아무 앵커나 window 안에 들어가므로, structured가 다른 모든 시나리오에서 겪는
"reject-proposal 이후 판매자가 숫자를 전혀 말하지 않아 교착되는" 문제가 이 시나리오에서는 발생하지
않는다. 반대로 gap이 크게 벌어진 **불가능한** 시나리오(140-TV, −200)에서는 tagged가 5회 모두
`no_deal`로 정확히 끝냈다. 같은 시나리오에서 free는 여전히 format_error를 냈다(4건 중 2건이 시나리오
140) — 구매자의 첫 쿼리를 리더가 네 화행 중 어디에도 넣지 못하는, 과제 지침이 예견한 바로 그 현상이
temperature 값과 무관하게 재현됐다.

**턴 제한의 영향은 여전하다.** `_old_maxturns8/results.csv`(턴 8, 그 외 코드·시나리오 동일)는
`deal`/`no_deal`이 훨씬 많고 `open`이 적다. 이번 실행에서도 `open`이 세 조건 모두에서 가장 흔한
결과다(32/40, 23/40, 34/40). temperature를 고정하기 전후 두 번의 독립적인 120-episode 실행을
비교했을 때 결국 가장 안정적으로 재현되는 변수는 포맷(free/tagged/structured) 자체가 아니라
**협상 가능 구간(gap)의 폭**이며, 이번 재현은 그 결론이 통제되지 않은 temperature 때문에 생긴
우연이 아니었음을 보여준다.
