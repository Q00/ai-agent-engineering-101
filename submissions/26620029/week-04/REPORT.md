# Week 04 — 실전 화행(speech act): 자유(free) / 태그(tagged) / 구조화(structured) 협상

## 1. 설정 (Setup)

**제공자 / 모델**: Anthropic, `claude-haiku-4-5-20251001` (환경변수 `AGENT_MODEL`, [model.py](model.py) 참고).

**temperature**: 설정 불가. 설치된 `anthropic` 파이썬 SDK(1.5.0)는 `Messages.create`에 `temperature`
파라미터를 더 이상 노출하지 않는다 — 이 SDK/모델 조합의 Messages API에서 빠졌다. 지어내지 않고 있는
그대로 기록한다. week-03 노트가 temperature를 노출하지 않는 CLI에 대해 쓴 것과 같은 관례다.

**턴 제한**: `MAX_TURNS = 5`메시지(구매자·판매자 교대, 5메시지 안에 끝나지 않으면 `open`). 이전 시도는
`MAX_TURNS = 8`이었고, 그 실행 결과는 수정 없이 [`_old_maxturns8/`](_old_maxturns8/) 아래에 폐기된
시도로 보존했다 — `deal`/`no_deal`이 훨씬 많고 `open`은 적게 나왔다(4절 참고).

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
([negotiation.py](negotiation.py)). 이는 FIPA의 accept-proposal 정의("상대가 마지막으로 제안한
가격에 동의")를 *상태 기반*으로 해석한 것으로, 발화자가 자기 문장에서 주장하는 숫자를 그대로 믿는
것과는 다르다. 이 선택이 왜 중요한지는 4절에서 다룬다.

**실행 방법**:

```bash
export ANTHROPIC_API_KEY=<your key>       # 또는 model.py 옆에 .env 파일
python run.py                             # results.csv, logs/ 를 씀
python ../../../scripts/check_week04.py . # 이 디렉토리에서
```

`run.py`는 중단 후 재실행해도 안전하다: `results.csv`에 이미 있는 `(run, scenario)` 쌍은 건너뛴다.

**구현 노트 (이번 라운드에서 고친 버그)**. `.env` 로더가 `KEY="value"`처럼 따옴표로 감싼 값을 그대로
`os.environ`에 넣고 있었다 — 따옴표 문자까지 키 값에 포함되어 API가 매번 401(`invalid x-api-key`)을
반환했다. `model.py`에서 값 양끝의 짝이 맞는 따옴표 한 겹을 벗기도록 수정했다. 재현성과 직결되는
부분이라 여기 기록한다.

**추가 실험 (반복 3→5, 시나리오 4→8)**. 처음 제출한 3회 반복·4개 시나리오만으로는 위반(violation)이
2건뿐이라 우연인지 패턴인지 판단하기 어려웠다. 그래서
- 반복 횟수를 조건당 **3 → 5**로 늘리고,
- gap(=budget−reserve) 크기가 다양하도록 시나리오를 **4개 → 8개**로 늘렸다(근소한 격차 ±2, 기존
  ±10~±40, 아주 큰 격차 ±200~±300).

`run.py`가 이미 완료된 `(run, scenario)`를 건너뛰므로 기존 3회·4시나리오 데이터는 그대로 두고 나머지
84개 episode만 새로 실행했다. 새 시나리오는 [scenarios.json](scenarios.json)에 있다:

| id | 품목 | reserve | budget | gap | deal_possible |
|---|---|---|---|---|---|
| 95 | 우산 | 95 | 97 | +2 (근소) | 가능 |
| 115 | 손목시계 | 102 | 100 | −2 (근소) | 불가능 |
| 130 | 중고차 | 200 | 500 | +300 (여유 큼) | 가능 |
| 140 | TV | 300 | 100 | −200 (격차 큼) | 불가능 |

## 2. 결과

조건당 8시나리오 × 5반복 = 40 episode (총 120 episode).

| condition | n | deal | no_deal | open | correct | violations | mean turns | format_errors | reader_calls |
|---|---|---|---|---|---|---|---|---|---|
| free | 40 | 6 | 5 | 29 | 23/40 | 3 | 4.70 | 2 | 188 |
| tagged | 40 | 9 | 8 | 23 | 25/40 | 4 | 4.78 | 0 | 64 |
| structured | 40 | 5 | 0 | 35 | 25/40 | 0 | 4.88 | 0 | 0 |

### 2-1. gap(격차) 크기별 결과 — 새로 추가한 분석

| 시나리오 | 품목 | gap | free 결과 | free 위반 | tagged 결과 | tagged 위반 | structured 결과 |
|---|---|---|---|---|---|---|---|
| 90 | 파인애플 | +20 | open×4, deal×1 | 0 | open×2, deal×3 | 1 | open×5 |
| 100 | 사과 | +10 | open×3, deal×2 | 2 | open×2, deal×3 | 3 | open×5 |
| 95 | 우산 | +2 | open×4, deal×1 | 1 | open×5 | 0 | open×5 |
| 115 | 손목시계 | −2 | open×4, no_deal×1 | 0 | open×3, no_deal×2 | 0 | open×5 |
| 105 | 노트북 | −15 | open×4, no_deal×1 | 0 | open×5 | 0 | open×5 |
| 110 | 자전거 | −40 | open×3, no_deal×2 | 0 | open×4, no_deal×1 | 0 | open×5 |
| 130 | 중고차 | +300 | open×3, deal×2 | 0 | deal×3, open×2 | 0 | **deal×5** |
| 140 | TV | −200 | open×4, no_deal×1 | 0 | **no_deal×5** | 0 | open×5 |

위반 7건 전부가 gap이 좁은(+2 ~ +20) 두 시나리오(90, 95, 100)에 몰려 있다. gap이 아주 크거나(+300)
음수인 시나리오에서는 free/tagged를 통틀어 위반이 **0건**이다. structured의 유일한 거래 성사도
gap=+300 시나리오에서만 나왔다(5/5 전부 성사, 5회 모두 정확히 400원). 3절·4절에서 이 패턴을 다룬다.

### 2-2. 전체 episode 표 (`results.csv`)

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-r1 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 110 | 0 | open |  | 1 | 0 | 5 | 1 | 5 |  |
| free-r1 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 140 | 0 | open |  | 1 | 0 | 5 | 1 | 5 |  |
| free-r2 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 95 | 1 | deal | 75 | 0 | 1 | 5 | 0 | 5 |  |
| free-r2 | free | 100 | 1 | deal | 120 | 0 | 1 | 5 | 0 | 5 |  |
| free-r2 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 130 | 1 | deal | 420 | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 90 | 1 | deal | 72 | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 105 | 0 | no_deal |  | 1 | 0 | 1 | 0 | 1 |  |
| free-r3 | free | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 110 | 0 | no_deal |  | 1 | 0 | 1 | 0 | 1 |  |
| free-r4 | free | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 130 | 1 | deal | 425 | 1 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 100 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 5 |  |
| free-r5 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 110 | 0 | no_deal |  | 1 | 0 | 1 | 0 | 1 |  |
| free-r5 | free | 115 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| tagged-r1 | tagged | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r1 | tagged | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r1 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r1 | tagged | 130 | 1 | deal | 350 | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 4 | 0 | 1 |  |
| tagged-r2 | tagged | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r2 | tagged | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 100 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r2 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 130 | 1 | deal | 385 | 1 | 0 | 5 | 0 | 3 |  |
| tagged-r2 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 |  |
| tagged-r3 | tagged | 90 | 1 | deal | 75 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-r3 | tagged | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r3 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 |  |
| tagged-r4 | tagged | 90 | 1 | deal | 75 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-r4 | tagged | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 100 | 1 | deal | 95 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r4 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 110 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 130 | 1 | open |  | 0 | 0 | 5 | 0 | 4 |  |
| tagged-r4 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 |  |
| tagged-r5 | tagged | 90 | 1 | deal | 65 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r5 | tagged | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r5 | tagged | 100 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r5 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r5 | tagged | 130 | 1 | deal | 425 | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r5 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| structured-r1 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 95 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 115 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r1 | structured | 140 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
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
| structured-r3 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
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
| 메시지 하나를 읽는 비용 | 공짜로 가정(공유 온톨로지, 해석 단계 없음) | episode당 평균 4.70턴 × 매 메시지 리더 1회 = 총 188회(40 episode) | propose의 가격 추출에만 리더 1회, 나머지 3개 화행은 0회 = 총 64회 | 0 — 파싱만 |
| 관찰된 실패 유형 | 문헌에 기록됨: 온톨로지 불일치, 비협조적 에이전트 | 리더가 질문을 가격 없는 `propose`로 오독(1턴째, 대부분의 episode); 파싱 불가 응답 2건(format_error); gap이 좁을 때 위반 3건 | 발화자가 새 가격을 담은 반박을 `propose`가 아닌 `reject-proposal`로 태그(gap이 좁을 때 위반 4건); format_error는 0 — 태그 자체는 파싱이 쉬움 | format_error 0, 위반 0. 대신 gap이 좁거나 음수인 시나리오에서는 거래 성사가 0 — 판매자가 `reject-proposal`을 선언한 뒤 대안 가격을 전혀 말하지 않아 협상이 굳어버림 |

## 4. 해석

**gap(격차) 크기가 위반과 결과를 가장 강하게 좌우한다.** 7건의 위반 전부가 gap이 좁은
(+2, +10, +20) 세 시나리오(95-우산, 100-사과, 90-파인애플)에 몰려 있다. gap이 크게 벌어진
시나리오(130-중고차, +300)나 애초에 거래가 불가능한 시나리오(105, 110, 115, 140)에서는 free·tagged
를 통틀어 위반이 단 한 건도 없다. 메커니즘은 명확하다: gap이 좁을수록 판매자의 최종 양보가 (a) 이미
합의된 가격 근처에 있어 "굳이 새 propose를 선언할 필요가 없어 보이는" 문장이 되기 쉽고, (b) 그 결과
협상이 stale한(오래된) 추적 가격 위에서 마무리될 때 그 오차가 좁은 window를 훌쩍 벗어나 버린다. gap
이 300이면 같은 크기의 오차가 있어도 여전히 window 안에 들어간다.

두 건의 새 위반 사례가 이 메커니즘을 더 정밀하게 보여준다. `tagged-r4`/`tagged-r5`의 시나리오 100·90
에서는 판매자가 마지막 turn에서 "제 최소 가격은 70/100입니다, 그 가격에 맞춰주시겠어요?"처럼 새
가격을 분명히 말하면서도 태그는 `(reject-proposal)`을 쓴다. tagged의 프로토콜은 `(propose)` 태그가
붙은 메시지만 `last_price`를 갱신하므로 이 숫자는 등록되지 않고, 구매자의 `(accept-proposal)`은 그보다
앞선 stale한 가격으로 확정된다(예: tagged-r5/시나리오90 — 판매자가 "70"을 두 번 언급하지만 매번
`(reject-proposal)`, 구매자가 "70에 동의합니다"라고 수락해도 거래는 이전 propose인 65로 성사).
`free-r2`/시나리오95는 한 겹 더 흥미롭다: 구매자의 마지막 메시지 "당신의 최소가 95에 맞출 수
있어요"는 리더가 `accept-proposal, price=95`로 **정확하게** 읽는다. 그런데도 거래는 95가 아니라
직전 turn의 stale한 propose였던 75로 성사된다 — `negotiation.py`가 accept-proposal에서는 리더가 그
문장 자체에서 읽은 가격을 쓰지 않고 프로토콜이 추적해 온 상태만 신뢰하도록 설계했기 때문이다(1절).
이 상태 기반 설계는 "진실성 없는 자기 보고"라는 FIPA의 근본 문제(발화자가 태그만 바꿔 달면 그만인
문제)에는 강하지만, 이번 사례처럼 리더가 accept 문장 안의 가격을 옳게 읽어낸 경우조차 무시해버리는
트레이드오프가 있다는 것을 보여준다.

**gap이 아주 벌어지면 포맷 차이가 무의미해진다.** structured의 유일한 거래 성사는 gap=+300 시나리오
(중고차, reserve 200/budget 500)에서만 나왔고, 놀랍게도 5회 반복 전부 정확히 동일한 각본을 따랐다
— 구매자가 350을 propose, 판매자가 reject, 구매자가 400을 propose, 판매자가 accept, 정확히
turn 4에서 400원에 성사. window가 넓으면 아무 숫자나 "합리적인 앵커"로 던져도 window 안에 들어가기
때문에, structured가 다른 모든 시나리오에서 겪는 "reject-proposal 이후 판매자가 숫자를 전혀 말하지
않아 교착되는" 문제가 이 시나리오에서는 발생하지 않는다. 반대로 gap이 크게 벌어진 **불가능한**
시나리오(140-TV, −200)에서는 tagged가 5회 모두 정확히 `no_deal`로 끝냈다(`tagged-r1`: 판매자가
"제 최소가는 300이고 협상 불가입니다"라며 `(refuse)`로 명확히 종료). 반면 같은 시나리오에서 free는
1건의 format_error를 냈다 — 구매자의 첫 메시지 "최저가가 얼마인가요?"를 리더가 네 화행 중 어디에도
넣지 못했다. 과제 지침이 예견한 "쿼리를 강제로 네 화행 중 하나로 분류해야 하는 리더는 종종 잘못
답한다"는 현상이 이번에도 재현된 것이다.

**턴 제한의 영향은 여전하다.** `_old_maxturns8/results.csv`(턴 8, 그 외 코드·시나리오 동일)는
`deal`/`no_deal`이 훨씬 많고 `open`이 적다. 이번 5턴 확장 실행에서도 `open`이 세 조건 모두에서
가장 흔한 결과다(29/40, 23/40, 35/40) — 짧은 턴 제한이 "틀린 거래에 도달하는 데는 2~5턴이면 충분한
반면, 맞는 거래에 도달하려면 그보다 훨씬 많은 턴이 필요하다"는 최초 관찰을 그대로 뒷받침한다. 반복
횟수를 3→5로, 시나리오를 4→8로 늘리면서 드러난 것은 결국 하나의 변수 — **협상 가능 구간(gap)의
폭** — 가 포맷(free/tagged/structured) 자체보다 위반 여부와 거래 성사 여부에 더 결정적이라는
점이다.
